import argparse
import importlib.metadata
import json
import logging
import sys
from pathlib import Path
from typing import NoReturn

import rich
from rich.logging import RichHandler

from .analysis import analyze_design, infer_widths, lint_design
from .codegen.format_style import FormatStyle
from .codegen.verilog_formatter import format_design as _format_design
from .project import DEFAULT_EXTENSIONS, parse_directory, parse_file
from .scaffold import build_testbench_plan, export_dsl_project, generate_python_testbench_skeleton
from .refactor import (
    BoundaryMoveSelection,
    ExtractSelection,
    apply_collapse_preview,
    apply_extract_preview,
    apply_hierarchy_boundary_move_preview,
    build_hierarchy_graph,
    hierarchy_graph_to_dot,
    hierarchy_graph_to_mermaid,
    hierarchy_graph_to_text,
    preview_collapse_hierarchy,
    preview_extract_submodule,
    preview_hierarchy_push_down,
    preview_hierarchy_pull_up,
)
from .verilog_parser import verilog_parser
from .sim.bench import PlannerOverrides

log = logging.getLogger("rich")
DEFAULT_FILE = Path(__file__).absolute().parents[2] / "tests" / "test_verilog_parser" / "verilog" / "verilog_all.v"
SUBCOMMANDS = {
    "tree",
    "reconstruct",
    "generate-python-testbench",
    "parse-file",
    "parse-directory",
    "export-dsl",
    "hierarchy",
    "lint",
    "format",
}
JSON_CAPABLE_COMMANDS = {
    "generate-python-testbench",
    "parse-file",
    "parse-directory",
    "export-dsl",
    "hierarchy",
    "lint",
}
HELP_EPILOG = "Legacy flag mode remains supported for compatibility, for example: veriforge -f rtl/top.v -t"
_ACTIVE_ARGV: list[str] = []


def _command_from_argv(argv: list[str]) -> str | None:
    for token in argv:
        if token.startswith("-"):
            continue
        if token in SUBCOMMANDS:
            return token
        return None
    return None


def _should_emit_json_parse_error(argv: list[str]) -> bool:
    command = _command_from_argv(argv)
    return command in JSON_CAPABLE_COMMANDS and "--json" in argv


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        if _should_emit_json_parse_error(_ACTIVE_ARGV):
            command = _command_from_argv(_ACTIVE_ARGV) or "cli"
            _print_json_error(command, "ArgumentError", message)
            raise SystemExit(2)
        super().error(message)


def _add_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help="Path to the top level Verilog HDL file",
    )


def _add_parse_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-d", "--debug", action="store_true", default=False)
    parser.add_argument(
        "-parser",
        "--parser",
        default="earley",
        help="Provide Parser. Example --parser earley,lalr",
    )


def _add_logging_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-log",
        "--log",
        default="error",
        help="Provide logging level. Example --log debug,info,warning,error,critical', default='error'",
    )


def _add_generation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--module",
        default=None,
        help="Module name to use when generating a Python testbench skeleton.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Optional output path for generated Python testbench text.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print generation results as JSON.",
    )
    parser.add_argument(
        "--enhanced",
        action="store_true",
        default=False,
        help="Generate the enhanced multi-domain testbench skeleton (driven by TestbenchPlan).",
    )
    parser.add_argument(
        "--style",
        choices=("legacy", "bench"),
        default="legacy",
        help=(
            "Skeleton style. 'legacy' (default) emits raw Simulator + step_drive code; "
            "'bench' emits a Testbench(...) framework scaffold (requires --enhanced). "
            "The 'bench' style produces high-level bench.iface(...).put(...) stubs, "
            "inferred iface_layouts, an argparse --vcd flag, and a with bench.run(): block."
        ),
    )
    parser.add_argument(
        "--dut-source-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "When emitting --style=bench, embed PATH as the DUT_PATH literal in the "
            "generated scaffold so it can re-parse the source file at runtime. "
            "Defaults to the value of -f/--file when omitted."
        ),
    )
    parser.add_argument(
        "--auto-deps",
        action="store_true",
        default=False,
        help=(
            "When emitting --style=bench, scan sibling .sv/.v files for child modules "
            "instantiated by the DUT and embed them as a DEPS list in the scaffold."
        ),
    )
    parser.add_argument(
        "--include-dir",
        action="append",
        default=[],
        type=Path,
        metavar="DIR",
        help=(
            "Additional directory to search for child-module sources during --auto-deps "
            "(repeatable). When omitted, only the DUT file's parent directory is scanned."
        ),
    )
    parser.add_argument(
        "--clock-override",
        action="append",
        default=[],
        metavar="NAME=PERIOD",
        help="Override clock period for a clock signal (repeatable, e.g. --clock-override aclk=8).",
    )
    parser.add_argument(
        "--reset-override",
        action="append",
        default=[],
        metavar="NAME=POLARITY",
        help="Override reset polarity for a reset signal (active_high|active_low). Repeatable.",
    )
    parser.add_argument(
        "--iface-domain",
        action="append",
        default=[],
        metavar="PREFIX=DOMAIN",
        help="Force an interface prefix to a specific clock domain (repeatable).",
    )
    parser.add_argument(
        "--domain-alias",
        action="append",
        default=[],
        metavar="CLOCK=ALIAS",
        help=(
            "Rename a clock signal's domain (repeatable, e.g. --domain-alias aclk=axis_domain). "
            "By default the domain name matches the clock signal name."
        ),
    )
    parser.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="Fail on ambiguous clock-domain inference (default).",
    )
    parser.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="Pick the first candidate domain when inference is ambiguous (instead of failing).",
    )
    parser.add_argument(
        "--engine",
        choices=("reference", "vm", "vm-fast", "compiled"),
        default="reference",
        help=(
            "Simulation engine for the generated scaffold. When 'vm', 'vm-fast', or 'compiled' is "
            "chosen and all detected interfaces are natively lowerable, emits a compile_native() "
            "scaffold that runs at engine speed. Otherwise falls back to the Python Testbench "
            "framework. 'vm-fast' uses the Cython-accelerated bytecode VM."
        ),
    )
    parser.add_argument(
        "--cosim",
        action="store_true",
        default=False,
        help=(
            "Append a validate_with_icarus() helper to the generated skeleton. "
            "Requires iverilog and vvp on PATH. "
            "Use --dut-source-path to override the DUT file path embedded in the helper."
        ),
    )
    parser.add_argument(
        "--explain-plan",
        action="store_true",
        default=False,
        help="Print the inferred TestbenchPlan summary and exit without generating code.",
    )


def _add_summary_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print the parsed design summary as JSON.",
    )


def _add_generate_directory_option(parser: argparse.ArgumentParser) -> None:
    """Add ``--directory`` and related project-parse options to a ``generate-python-testbench`` parser."""
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Root directory containing Verilog files. "
            "Use instead of --file for multi-file projects. "
            "Requires --module when the directory has more than one top module."
        ),
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        default=None,
        help="File extension to include when scanning a directory. Repeat to add multiple.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob pattern to exclude when scanning a directory. Repeat to add multiple.",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=None,
        help="Include search path used when preprocessing. Repeat to add multiple paths.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional parse cache directory.",
    )
    parser.add_argument(
        "--preprocess",
        action="store_true",
        default=False,
        help="Run the Verilog preprocessor before parsing each file.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        default=False,
        help="Only scan the top-level directory (not recursive).",
    )


def _add_project_parse_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "directory",
        type=Path,
        help="Root directory containing Verilog files to parse.",
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        default=None,
        help="File extension to include. Repeat to add multiple extensions.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob pattern to exclude. Repeat to add multiple patterns.",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=None,
        help="Include search path used when preprocessing. Repeat to add multiple paths.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional parse cache directory.",
    )
    parser.add_argument(
        "--preprocess",
        action="store_true",
        default=False,
        help="Run the Verilog preprocessor before parsing each file.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        default=False,
        help="Only scan the top-level directory.",
    )
    parser.add_argument(
        "--no-analyze",
        action="store_true",
        default=False,
        help="Skip post-parse instance linking.",
    )
    _add_summary_output_option(parser)


def _add_hierarchy_project_options(parser: argparse.ArgumentParser, *, include_format: bool = False) -> None:
    parser.add_argument(
        "directory",
        type=Path,
        help="Root directory containing Verilog files to parse.",
    )
    parser.add_argument(
        "--top",
        default=None,
        help="Top module to use as the hierarchy root. Defaults to all inferred top modules.",
    )
    parser.add_argument(
        "--extension",
        action="append",
        dest="extensions",
        default=None,
        help="File extension to include. Repeat to add multiple extensions.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob pattern to exclude. Repeat to add multiple patterns.",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=None,
        help="Include search path used when preprocessing. Repeat to add multiple paths.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional parse cache directory.",
    )
    parser.add_argument(
        "--preprocess",
        action="store_true",
        default=False,
        help="Run the Verilog preprocessor before parsing each file.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        default=False,
        help="Only scan the top-level directory.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="Maximum hierarchy depth to serialize. Use a negative value for unlimited depth.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print hierarchy results as JSON.",
    )
    if include_format:
        parser.add_argument(
            "--format",
            choices=("text", "json", "dot", "mermaid"),
            default="text",
            help="Output format for hierarchy graph.",
        )


def _add_hierarchy_collapse_options(parser: argparse.ArgumentParser) -> None:
    _add_hierarchy_project_options(parser)
    parser.add_argument(
        "--instance",
        required=True,
        help="Slash-separated instance path to collapse, for example top/u_wrap.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Preview the collapse edit plan without modifying source files.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Apply a safe collapse edit plan to source files.",
    )


def _add_hierarchy_extract_options(parser: argparse.ArgumentParser) -> None:
    _add_hierarchy_project_options(parser)
    parser.add_argument(
        "--module",
        required=True,
        help="Parent module containing the selected logic.",
    )
    parser.add_argument(
        "--range",
        required=True,
        dest="source_range",
        help="Selected source range as FILE:START-END using 1-based inclusive line numbers.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name for the generated child module.",
    )
    parser.add_argument(
        "--instance",
        default=None,
        help="Optional instance name for the generated child. Defaults to u_<name>.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Preview the extract edit plan without modifying source files.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Apply an extract edit plan to source files.",
    )


def _add_hierarchy_pull_up_options(parser: argparse.ArgumentParser) -> None:
    _add_hierarchy_project_options(parser)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--instance",
        help="Slash-separated instance path to pull up, for example top/u_wrap.",
    )
    selection.add_argument(
        "--subtree",
        help="Slash-separated subtree root instance path to pull up.",
    )
    selection.add_argument(
        "--module",
        help="Module name to preview. Module selections require parent context and usually block.",
    )
    selection.add_argument(
        "--file",
        type=Path,
        help="File containing one module to preview. File selections require parent context and usually block.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Preview the pull-up operation without modifying source files.",
    )


def _add_hierarchy_push_down_options(parser: argparse.ArgumentParser) -> None:
    _add_hierarchy_project_options(parser)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--instance",
        help="Slash-separated instance path to push down, for example top/u_wrap.",
    )
    selection.add_argument(
        "--subtree",
        help="Slash-separated subtree root instance path to push down.",
    )
    selection.add_argument(
        "--module",
        help="Module name to push down.",
    )
    selection.add_argument(
        "--file",
        type=Path,
        help="File containing one module to push down.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name for the new child module that would receive the selected contents.",
    )
    parser.add_argument(
        "--instance-name",
        default="",
        help="Optional instance name for the new child. Defaults to u_<name>.",
    )
    parser.add_argument(
        "--target-parent",
        default="",
        help="Optional slash-separated target parent path. Defaults to the selected source scope.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Preview the push-down operation without modifying source files.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Apply the push-down rewrite by writing edits to disk.",
    )


def _add_parse_file_options(parser: argparse.ArgumentParser) -> None:
    _add_file_arg(parser)
    parser.add_argument(
        "--include-path",
        action="append",
        default=None,
        help="Include search path used when preprocessing. Repeat to add multiple paths.",
    )
    parser.add_argument(
        "--preprocess",
        action="store_true",
        default=False,
        help="Run the Verilog preprocessor before parsing the file.",
    )
    _add_summary_output_option(parser)


def _add_export_dsl_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory where generated DSL files will be written.",
    )
    parser.add_argument(
        "--single-file",
        action="store_true",
        default=False,
        help="Write a single design.py instead of one file per module/package/interface.",
    )
    parser.add_argument(
        "--module-var",
        default="m",
        help="Variable name to use for the Module builder in emitted DSL code.",
    )


def build_legacy_arg_parser() -> argparse.ArgumentParser:
    parser = CliArgumentParser()
    _add_file_arg(parser)
    _add_parse_options(parser)
    _add_logging_option(parser)
    _add_generation_options(parser)
    parser.add_argument("-t", "--tree", action="store_true", default=False)
    parser.add_argument("-r", "--reconstruct", action="store_true", default=False)
    parser.add_argument(
        "--generate-python-testbench",
        action="store_true",
        default=False,
        help="Generate a Python simulator testbench skeleton for the parsed design.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s (version {version})".format(version=importlib.metadata.version("veriforge")),
    )
    return parser


def _build_subcommand_parser(*, require_command: bool) -> argparse.ArgumentParser:
    parser = CliArgumentParser(
        description="Verilog parser and testbench generation tools.",
        epilog=HELP_EPILOG,
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s (version {version})".format(version=importlib.metadata.version("veriforge")),
    )

    subparsers = parser.add_subparsers(dest="command", required=require_command, metavar="command")

    tree_parser = subparsers.add_parser("tree", help="Parse a file and print the syntax tree.")
    _add_file_arg(tree_parser)
    _add_parse_options(tree_parser)
    _add_logging_option(tree_parser)

    reconstruct_parser = subparsers.add_parser("reconstruct", help="Reconstruct Verilog text from the parsed tree.")
    _add_file_arg(reconstruct_parser)
    _add_parse_options(reconstruct_parser)
    _add_logging_option(reconstruct_parser)

    generate_parser = subparsers.add_parser(
        "generate-python-testbench",
        help="Generate a Python simulator testbench skeleton for the parsed design.",
    )
    _add_file_arg(generate_parser)
    _add_generate_directory_option(generate_parser)
    _add_logging_option(generate_parser)
    _add_generation_options(generate_parser)

    parse_file_parser = subparsers.add_parser(
        "parse-file",
        help="Parse a single Verilog file and print a summary.",
    )
    _add_logging_option(parse_file_parser)
    _add_parse_file_options(parse_file_parser)

    parse_directory_parser = subparsers.add_parser(
        "parse-directory",
        help="Parse a Verilog project directory and print a summary.",
    )
    _add_logging_option(parse_directory_parser)
    _add_project_parse_options(parse_directory_parser)

    export_dsl_parser = subparsers.add_parser(
        "export-dsl",
        help="Parse a Verilog project directory and export it to Python DSL files.",
    )
    _add_logging_option(export_dsl_parser)
    _add_project_parse_options(export_dsl_parser)
    _add_export_dsl_options(export_dsl_parser)

    hierarchy_parser = subparsers.add_parser(
        "hierarchy",
        help="Inspect resolved Verilog hierarchy and wrapper candidates.",
    )
    hierarchy_subparsers = hierarchy_parser.add_subparsers(dest="hierarchy_command", required=True, metavar="command")
    hierarchy_graph_parser = hierarchy_subparsers.add_parser(
        "graph",
        help="Print the resolved hierarchy graph.",
    )
    _add_logging_option(hierarchy_graph_parser)
    _add_hierarchy_project_options(hierarchy_graph_parser, include_format=True)
    hierarchy_wrappers_parser = hierarchy_subparsers.add_parser(
        "wrappers",
        help="Print wrapper classifications for hierarchy instances.",
    )
    _add_logging_option(hierarchy_wrappers_parser)
    _add_hierarchy_project_options(hierarchy_wrappers_parser)
    hierarchy_collapse_parser = hierarchy_subparsers.add_parser(
        "collapse",
        help="Preview collapsing a pure pass-through wrapper instance.",
    )
    _add_logging_option(hierarchy_collapse_parser)
    _add_hierarchy_collapse_options(hierarchy_collapse_parser)
    hierarchy_extract_parser = hierarchy_subparsers.add_parser(
        "extract",
        help="Preview extracting selected continuous assignments into a child module.",
    )
    _add_logging_option(hierarchy_extract_parser)
    _add_hierarchy_extract_options(hierarchy_extract_parser)
    hierarchy_pull_up_parser = hierarchy_subparsers.add_parser(
        "pull-up",
        help="Preview pulling a hierarchy selection up into its parent scope.",
    )
    _add_logging_option(hierarchy_pull_up_parser)
    _add_hierarchy_pull_up_options(hierarchy_pull_up_parser)
    hierarchy_push_down_parser = hierarchy_subparsers.add_parser(
        "push-down",
        help="Preview pushing a hierarchy selection down into a new child scope.",
    )
    _add_logging_option(hierarchy_push_down_parser)
    _add_hierarchy_push_down_options(hierarchy_push_down_parser)

    lint_parser = subparsers.add_parser(
        "lint",
        help="Run lint checks on a Verilog file or project directory.",
    )
    _add_logging_option(lint_parser)
    lint_parser.add_argument(
        "path",
        type=Path,
        help="Verilog file or directory to lint.",
    )
    lint_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit results as JSON.",
    )
    lint_parser.add_argument(
        "--skip",
        metavar="CODE",
        nargs="*",
        default=[],
        help="Lint codes to suppress (e.g. UNDRIVEN UNUSED).",
    )

    format_parser = subparsers.add_parser(
        "format",
        help="Format a Verilog file using the built-in formatter.",
    )
    _add_logging_option(format_parser)
    format_parser.add_argument(
        "file",
        type=Path,
        help="Verilog file to format.",
    )
    format_parser.add_argument(
        "--style",
        choices=("knr", "allman", "gnu"),
        default="knr",
        help="begin/end brace style (default: knr).",
    )
    format_parser.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Write formatted output back to the file instead of printing to stdout.",
    )

    return parser


def build_subcommand_arg_parser() -> argparse.ArgumentParser:
    return _build_subcommand_parser(require_command=True)


def build_top_level_help_parser() -> argparse.ArgumentParser:
    return _build_subcommand_parser(require_command=False)


def _configure_logging(level_name: str) -> None:
    levels = {
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
    }
    level = levels.get(level_name.lower())
    if level is None:
        raise ValueError(f"log level given: {level_name} -- must be one of: {' | '.join(levels.keys())}")

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def _build_tree(args: argparse.Namespace):
    vp = verilog_parser(
        transformer=None,
        parser=args.parser,
        start="verilog",
        debug=args.debug,
    )
    return vp, vp.build_tree(text=args.file)


def _parse_kv_list(items: list[str], *, value_kind: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            msg = f"--{value_kind} entries must be NAME=VALUE, got: {item!r}"
            raise ValueError(msg)
        key, val = item.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key or not val:
            msg = f"--{value_kind} entries must be NAME=VALUE, got: {item!r}"
            raise ValueError(msg)
        out[key] = val
    return out


def _resolve_auto_deps(args: argparse.Namespace) -> list[str] | None:
    """Resolve --auto-deps + --include-dir into a list of dependency paths."""
    if not getattr(args, "auto_deps", False):
        return None
    from .dsl.testbench_deps import discover_sv_dependencies  # noqa: PLC0415

    dut_path = getattr(args, "dut_source_path", None) or args.file
    include_dirs = list(getattr(args, "include_dir", []) or [])
    search_dirs = include_dirs if include_dirs else None
    deps, _design = discover_sv_dependencies(
        dut_path,
        top_module=args.module,
        search_dirs=search_dirs,
    )
    return [str(p) for p in deps]


def _run_generate_python_testbench(args: argparse.Namespace) -> None:  # noqa: PLR0912, PLR0915
    if getattr(args, "directory", None):
        extensions = tuple(args.extensions) if args.extensions else DEFAULT_EXTENSIONS
        design = parse_directory(
            args.directory,
            extensions=extensions,
            recursive=not getattr(args, "no_recursive", False),
            exclude=getattr(args, "exclude", None),
            preprocess=getattr(args, "preprocess", False),
            include_paths=getattr(args, "include_path", None),
            cache_dir=getattr(args, "cache_dir", None),
        )
    else:
        design = parse_file(args.file)
    module_name = args.module
    if module_name is None:
        tops = design.get_top_modules()
        if len(tops) != 1:
            msg = "module_name is required when the design does not have exactly one top module"
            raise ValueError(msg)
        module_name = tops[0].name

    overrides: object | None = None
    clock_periods: dict[str, int] = {}
    iface_domains: dict[str, str] = {}
    reset_polarities: dict[str, str] = {}
    domain_aliases: dict[str, str] = {}
    if args.clock_override:
        for k, v in _parse_kv_list(args.clock_override, value_kind="clock-override").items():
            try:
                period = int(v)
            except ValueError:
                msg = f"--clock-override value must be a positive integer, got: {k!r}={v!r}"
                raise ValueError(msg) from None
            if period <= 0:
                msg = f"--clock-override period must be a positive integer, got: {k!r}={period}"
                raise ValueError(msg)
            clock_periods[k] = period
    if args.iface_domain:
        iface_domains = _parse_kv_list(args.iface_domain, value_kind="iface-domain")
    if args.reset_override:
        raw = _parse_kv_list(args.reset_override, value_kind="reset-override")
        for name, pol in raw.items():
            if pol not in {"active_high", "active_low"}:
                msg = f"--reset-override value must be 'active_high' or 'active_low', got: {name}={pol}"
                raise ValueError(msg)
        reset_polarities = raw
    if args.domain_alias:
        domain_aliases = _parse_kv_list(args.domain_alias, value_kind="domain-alias")
    if clock_periods or iface_domains or reset_polarities or domain_aliases:
        overrides = PlannerOverrides(
            clock_periods=clock_periods,
            iface_domains=iface_domains,
            reset_polarities=reset_polarities,
            domain_aliases=domain_aliases,
        )

    if getattr(args, "explain_plan", False):
        plan = build_testbench_plan(design, top=module_name, overrides=overrides, strict=args.strict)
        if args.json:
            _print_json_result("generate-python-testbench", {"module_name": module_name, "plan": plan.to_dict()})  # type: ignore[attr-defined]
        else:
            print(plan.summary())  # type: ignore[attr-defined]
        return

    style = getattr(args, "style", "legacy")
    # bench style requires plan inference — promote enhanced automatically
    effective_enhanced = args.enhanced or (style == "bench")

    _cosim = getattr(args, "cosim", False)
    _dut_source_path = (
        str(
            getattr(args, "dut_source_path", None)
            or (args.directory if getattr(args, "directory", None) else args.file)
        )
        if (style == "bench" or _cosim)
        else None
    )
    output = generate_python_testbench_skeleton(
        design,
        module_name=module_name,
        output_path=args.output,
        enhanced=effective_enhanced,
        style=style,
        dut_source_path=_dut_source_path,
        dut_dependency_paths=_resolve_auto_deps(args) if style == "bench" else None,
        cosim=_cosim,
        overrides=overrides,
        strict=args.strict,
        engine=getattr(args, "engine", "reference"),
    )
    if args.json:
        payload: dict[str, object] = {
            "module_name": module_name,
            "output_path": str(output) if args.output is not None else None,
        }
        if args.output is None:
            payload["text"] = output
        else:
            payload["written"] = True
        _print_json_result("generate-python-testbench", payload)
        return

    if args.output is None:
        print(output)


def _project_parse_kwargs(args: argparse.Namespace) -> dict[str, object]:
    extensions = tuple(args.extensions) if args.extensions else DEFAULT_EXTENSIONS
    return {
        "directory": args.directory,
        "extensions": extensions,
        "recursive": not args.no_recursive,
        "analyze": not args.no_analyze,
        "exclude": args.exclude,
        "preprocess": args.preprocess,
        "include_paths": args.include_path,
        "cache_dir": args.cache_dir,
    }


def _hierarchy_parse_kwargs(args: argparse.Namespace) -> dict[str, object]:
    extensions = tuple(args.extensions) if args.extensions else DEFAULT_EXTENSIONS
    return {
        "directory": args.directory,
        "extensions": extensions,
        "recursive": not args.no_recursive,
        "analyze": True,
        "exclude": args.exclude,
        "preprocess": args.preprocess,
        "include_paths": args.include_path,
        "cache_dir": args.cache_dir,
    }


def _print_json_result(command: str, result: dict[str, object]) -> None:
    print(
        json.dumps(
            {
                "command": command,
                "success": True,
                "result": result,
            },
            indent=2,
        )
    )


def _print_json_error(command: str, error_type: str, message: str) -> None:
    print(
        json.dumps(
            {
                "command": command,
                "success": False,
                "error": {
                    "type": error_type,
                    "message": message,
                },
            },
            indent=2,
        )
    )


def _should_emit_json_error(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "command", None) and getattr(args, "json", False))


def _design_summary_data(design, *, root: Path) -> dict[str, object]:
    return {
        "root": str(root),
        "files": len(design.source_files),
        "modules": len(design.modules),
        "interfaces": len(design.interfaces),
        "packages": len(design.packages),
        "top_modules": [module.name for module in design.get_top_modules()],
    }


def _print_design_summary(design, *, root: Path, command: str, as_json: bool = False) -> None:
    summary = _design_summary_data(design, root=root)
    if as_json:
        _print_json_result(command, summary)
        return

    print(f"Parsed project: {summary['root']}")
    print(f"Files: {summary['files']}")
    print(f"Modules: {summary['modules']}")
    print(f"Interfaces: {summary['interfaces']}")
    print(f"Packages: {summary['packages']}")
    if summary["top_modules"]:
        print(f"Top modules: {', '.join(summary['top_modules'])}")  # type: ignore[arg-type]
    else:
        print("Top modules: <none>")


def _run_parse_file(args: argparse.Namespace) -> None:
    design = parse_file(
        args.file,
        preprocess=args.preprocess,
        include_paths=args.include_path,
    )
    _print_design_summary(design, root=args.file, command="parse-file", as_json=args.json)


def _run_parse_directory(args: argparse.Namespace) -> None:
    design = parse_directory(**_project_parse_kwargs(args))  # type: ignore[arg-type]
    _print_design_summary(design, root=args.directory, command="parse-directory", as_json=args.json)


def _run_export_dsl(args: argparse.Namespace) -> None:
    design = parse_directory(**_project_parse_kwargs(args))  # type: ignore[arg-type]
    written = export_dsl_project(
        design,
        args.output_dir,
        one_file_per_module=not args.single_file,
        module_var=args.module_var,
    )
    if args.json:
        _print_json_result(
            "export-dsl",
            {
                "output_dir": str(args.output_dir),
                "written": [str(path) for path in written],
            },
        )
        return

    print(f"Exported {len(written)} file(s) to {args.output_dir}")
    for path in written:
        print(path)


def _run_hierarchy(args: argparse.Namespace) -> None:
    design = parse_directory(**_hierarchy_parse_kwargs(args))  # type: ignore[arg-type]
    analyze_design(design)
    max_depth = None if args.max_depth < 0 else args.max_depth
    graph = build_hierarchy_graph(design, top=args.top, max_depth=max_depth)
    payload = _hierarchy_payload(args, graph, design)
    _print_hierarchy_result(args, graph, payload)


def _hierarchy_payload(args: argparse.Namespace, graph, design) -> dict[str, object]:
    payload: dict[str, object] = {
        "root": str(args.directory),
        "top": args.top,
        **graph.to_dict(),
    }
    if args.hierarchy_command == "wrappers":
        return {
            "root": str(args.directory),
            "top": args.top,
            "wrappers": payload["wrappers"],
            "stats": payload["stats"],
        }
    if args.hierarchy_command == "collapse":
        if args.preview == args.write:
            msg = "hierarchy collapse requires exactly one of --preview or --write"
            raise ValueError(msg)
        preview = preview_collapse_hierarchy(design, args.instance)
        apply_result = None
        if args.write:
            apply_result = apply_collapse_preview(preview)
            if apply_result.applied:
                reparsed = parse_directory(**_hierarchy_parse_kwargs(args))  # type: ignore[arg-type]
                analyze_design(reparsed)
        return {
            "root": str(args.directory),
            "top": args.top,
            "preview": preview.to_dict(),
            "apply": apply_result.to_dict() if apply_result is not None else None,
        }
    if args.hierarchy_command == "extract":
        if args.preview == args.write:
            msg = "hierarchy extract requires exactly one of --preview or --write"
            raise ValueError(msg)
        selection = _parse_extract_selection(args.source_range)
        preview = preview_extract_submodule(  # type: ignore[assignment]
            design,
            module_name=args.module,
            selection=selection,
            extracted_module_name=args.name,
            instance_name=args.instance,
        )
        apply_result = None
        if args.write:
            apply_result = apply_extract_preview(preview)  # type: ignore[assignment, arg-type]
            if apply_result.applied:  # type: ignore[attr-defined]
                reparsed = parse_directory(**_hierarchy_parse_kwargs(args))  # type: ignore[arg-type]
                analyze_design(reparsed)
        return {
            "root": str(args.directory),
            "top": args.top,
            "preview": preview.to_dict(),
            "apply": apply_result.to_dict() if apply_result is not None else None,
        }
    if args.hierarchy_command == "pull-up":
        if not args.preview:
            msg = "hierarchy pull-up currently supports --preview only"
            raise ValueError(msg)
        preview = preview_hierarchy_pull_up(design, _pull_up_selection_from_args(args))  # type: ignore[assignment]
        return {
            "root": str(args.directory),
            "top": args.top,
            "preview": preview.to_dict(),
        }
    if args.hierarchy_command == "push-down":
        return _hierarchy_push_down_payload(args, design)
    return payload


def _hierarchy_push_down_payload(args: argparse.Namespace, design) -> dict[str, object]:
    if not args.preview and not args.write:
        msg = "hierarchy push-down requires --preview or --write"
        raise ValueError(msg)
    if args.preview and args.write:
        msg = "hierarchy push-down accepts only one of --preview or --write"
        raise ValueError(msg)
    preview = preview_hierarchy_push_down(
        design,
        _boundary_selection_from_args(args),
        new_module_name=args.name,
        new_instance_name=args.instance_name,
        target_parent_path=args.target_parent,
    )
    result: dict[str, object] = {
        "root": str(args.directory),
        "top": args.top,
        "preview": preview.to_dict(),
    }
    if args.write:
        apply_result = apply_hierarchy_boundary_move_preview(preview)
        result["apply"] = apply_result.to_dict()
    return result


def _print_hierarchy_result(args: argparse.Namespace, graph, payload: dict[str, object]) -> None:
    if args.json or getattr(args, "format", "text") == "json":
        _print_json_result(f"hierarchy {args.hierarchy_command}", payload)
        return

    if args.hierarchy_command in {"collapse", "extract", "pull-up", "push-down"}:
        _print_hierarchy_preview(args, payload)
        return

    if args.hierarchy_command == "wrappers":
        _print_hierarchy_wrappers(payload)
        return

    output_format = getattr(args, "format", "text")
    if output_format == "dot":
        print(hierarchy_graph_to_dot(graph))
    elif output_format == "mermaid":
        print(hierarchy_graph_to_mermaid(graph))
    else:
        print(hierarchy_graph_to_text(graph))


def _print_hierarchy_preview(args: argparse.Namespace, payload: dict[str, object]) -> None:
    preview = payload["preview"]
    apply_result = payload.get("apply")
    if not isinstance(preview, dict):
        return
    if isinstance(apply_result, dict):
        if apply_result.get("applied"):
            files = ", ".join(str(path) for path in apply_result.get("writtenFiles", []))
            print(f"Applied hierarchy {args.hierarchy_command}: {files}")
        else:
            _print_hierarchy_diagnostics(apply_result)
    elif preview.get("diff"):
        print(preview["diff"])
    elif preview.get("ok") and args.hierarchy_command in {"pull-up", "push-down"}:
        print(json.dumps(preview, indent=2))
    else:
        _print_hierarchy_diagnostics(preview)


def _print_hierarchy_wrappers(payload: dict[str, object]) -> None:
    wrappers = payload.get("wrappers", [])
    if not wrappers:
        print("No wrapper candidates found.")
        return
    for wrapper in wrappers:  # type: ignore[attr-defined]
        if not isinstance(wrapper, dict):
            continue
        print(
            f"{wrapper.get('instancePath', '?')}: "
            f"{wrapper.get('moduleName', '?')} "
            f"[{wrapper.get('wrapperClass', 'unknown')}, {wrapper.get('confidence', 'unknown')}]"
        )


def _print_hierarchy_diagnostics(payload: dict[str, object]) -> None:
    for diagnostic in payload.get("diagnostics", []):  # type: ignore[attr-defined]
        if isinstance(diagnostic, dict):
            print(f"{diagnostic.get('severity', 'warning')}: {diagnostic.get('message', '')}")


def _parse_extract_selection(value: str) -> ExtractSelection:
    file_part, sep, range_part = value.rpartition(":")
    if not sep or not file_part:
        msg = "extract range must be FILE:START-END"
        raise ValueError(msg)
    start_text, dash, end_text = range_part.partition("-")
    if not dash:
        msg = "extract range must include START-END line numbers"
        raise ValueError(msg)
    start_line = int(start_text)
    end_line = int(end_text)
    if start_line < 1 or end_line < start_line:
        msg = "extract range line numbers must be 1-based and ordered"
        raise ValueError(msg)
    return ExtractSelection(str(Path(file_part).resolve()), start_line, end_line)


def _pull_up_selection_from_args(args: argparse.Namespace) -> BoundaryMoveSelection:
    return _boundary_selection_from_args(args)


def _boundary_selection_from_args(args: argparse.Namespace) -> BoundaryMoveSelection:
    if args.instance:
        return BoundaryMoveSelection(kind="instance", instance_path=args.instance)
    if args.subtree:
        return BoundaryMoveSelection(kind="subtree", instance_path=args.subtree)
    if args.module:
        return BoundaryMoveSelection(kind="module", module_name=args.module)
    return BoundaryMoveSelection(kind="file", file=str(args.file.resolve()))


def _looks_like_subcommand(argv: list[str]) -> bool:
    for token in argv:
        if token.startswith("-"):
            continue
        return token in SUBCOMMANDS
    return False


def _show_top_level_help(argv: list[str]) -> bool:
    return not argv or argv in (["-h"], ["--help"])


def _run_lint(args: argparse.Namespace) -> None:
    from .analysis import LintCode

    path = args.path
    skip: set[LintCode] = set()
    for code_str in args.skip or []:
        try:
            skip.add(LintCode[code_str.upper()])
        except KeyError:
            valid = ", ".join(c.name for c in LintCode)
            msg = f"Unknown lint code {code_str!r}. Valid codes: {valid}"
            raise ValueError(msg) from None
    if path.is_dir():
        design = parse_directory(str(path), analyze=True)
    else:
        design = parse_file(path, preprocess=True)
        analyze_design(design)
    infer_widths(design)
    warnings = lint_design(design, skip=skip or None)
    if args.json:
        _print_json_result(
            "lint",
            {
                "path": str(path),
                "total": len(warnings),
                "warnings": [
                    {
                        "code": w.code.name,
                        "module": w.module,
                        "signal": w.signal,
                        "instance": w.instance,
                        "message": w.message,
                    }
                    for w in warnings
                ],
            },
        )
        return
    if not warnings:
        print(f"No lint warnings in {path}")
        return
    for w in warnings:
        loc = f" [{w.signal}]" if w.signal else ""
        print(f"{w.module}{loc}: [{w.code.name}] {w.message}")
    print(f"\n{len(warnings)} warning(s)")


def _run_format(args: argparse.Namespace) -> None:
    design = parse_file(args.file, preprocess=True)
    style = getattr(FormatStyle, args.style)()
    text = _format_design(design, style)
    if args.write:
        args.file.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _run_subcommand(args: argparse.Namespace) -> int:
    handlers = {
        "tree": _run_tree,
        "reconstruct": _run_reconstruct,
        "generate-python-testbench": _run_generate_python_testbench,
        "parse-file": _run_parse_file,
        "parse-directory": _run_parse_directory,
        "export-dsl": _run_export_dsl,
        "hierarchy": _run_hierarchy,
        "lint": _run_lint,
        "format": _run_format,
    }
    handler = handlers.get(args.command)
    if handler is None:
        msg = f"Unsupported command: {args.command}"
        raise ValueError(msg)
    handler(args)
    return 0


def _run_tree(args: argparse.Namespace) -> None:
    _, parse_top = _build_tree(args)
    log.debug(parse_top)
    rich.print(parse_top)
    print(parse_top)


def _run_reconstruct(args: argparse.Namespace) -> None:
    vp, parse_top = _build_tree(args)
    print(vp.reconstruct(parse_top))


def main(argv: list[str] | None = None) -> int:  # cm:7f9a8c
    global _ACTIVE_ARGV  # noqa: PLW0603
    argv = list(sys.argv[1:] if argv is None else argv)
    _ACTIVE_ARGV = argv

    if _show_top_level_help(argv):
        parser = build_top_level_help_parser()
        if argv:
            parser.parse_args(argv)
        else:
            parser.print_help()
        return 0

    parser = build_subcommand_arg_parser() if _looks_like_subcommand(argv) else build_legacy_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if _should_emit_json_parse_error(argv) and exc.code not in (None, 0):
            return int(exc.code)  # type: ignore[arg-type]
        raise
    _configure_logging(args.log)

    if hasattr(args, "command"):
        try:
            return _run_subcommand(args)
        except Exception as exc:
            if _should_emit_json_error(args):
                _print_json_error(args.command, type(exc).__name__, str(exc))
                return 1
            raise

    parse_top = None
    if args.tree or args.reconstruct:
        vp, parse_top = _build_tree(args)

    if args.tree:
        log.debug(parse_top)
        rich.print(parse_top)
        print(parse_top)

    if args.reconstruct:
        reconstructed_verilog = vp.reconstruct(parse_top)
        print(reconstructed_verilog)

    if args.generate_python_testbench:
        _run_generate_python_testbench(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
