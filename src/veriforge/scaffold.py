"""Testbench scaffold and DSL export helpers.

High-level entry points for building :class:`~veriforge.sim.bench.runtime.Testbench`
objects and exporting a :class:`~veriforge.model.design.Design` to Python DSL files.
These functions sit on top of the core parsing layer in :mod:`veriforge.project` and
the simulation engine in :mod:`veriforge.sim`.

Usage::

    from veriforge.scaffold import build_testbench

    bench = build_testbench("rtl/", top="my_dut")

    # Or from a parsed Design:
    from veriforge.project import parse_directory
    from veriforge.scaffold import build_testbench

    design = parse_directory("rtl/")
    bench = build_testbench(design, top="my_dut")

    # Generate a Python testbench skeleton:
    from veriforge.scaffold import generate_python_testbench_skeleton

    text = generate_python_testbench_skeleton(design, module_name="my_dut")

    # Export to DSL files:
    from veriforge.scaffold import export_dsl_project

    export_dsl_project(design, "output_dir/")
"""

from __future__ import annotations

import logging
from pathlib import Path

from .model.design import Design
from .project import DEFAULT_EXTENSIONS, parse_directory, parse_files

log = logging.getLogger(__name__)


def export_dsl_project(  # cm:6e4b9a
    design: Design,
    output_dir: str | Path,
    *,
    one_file_per_module: bool = True,
    module_var: str = "m",
) -> list[Path]:
    """Export a Design to Python DSL files.

    Args:
        design: Design to export.
        output_dir: Directory to write output files.
        one_file_per_module: If True, writes one .py file per module/package/
            interface.  If False, writes a single ``design.py`` file.
        module_var: Variable name for the Module builder in emitted code.

    Returns:
        List of paths to the written files.
    """
    from .convert.to_dsl import (
        design_to_dsl,
        interface_to_dsl,
        module_to_dsl,
        package_to_dsl,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if not one_file_per_module:
        out = output_dir / "design.py"
        out.write_text(design_to_dsl(design, module_var=module_var), encoding="utf-8")
        written.append(out)
        return written

    # Packages first (dependencies)
    for pkg in design.packages:
        out = output_dir / f"pkg_{pkg.name}.py"
        out.write_text(package_to_dsl(pkg, module_var=module_var), encoding="utf-8")
        written.append(out)

    # Interfaces
    for intf in design.interfaces:
        out = output_dir / f"intf_{intf.name}.py"
        out.write_text(interface_to_dsl(intf, module_var=module_var), encoding="utf-8")
        written.append(out)

    # Modules
    for module in design.modules:
        out = output_dir / f"{module.name}.py"
        out.write_text(module_to_dsl(module, module_var=module_var), encoding="utf-8")
        written.append(out)

    log.info("Exported %d DSL files to %s", len(written), output_dir)
    return written


def generate_python_testbench_skeleton(  # noqa: PLR0913  # cm:f3d2c6
    design: Design,
    module_name: str | None = None,
    *,
    output_path: str | Path | None = None,
    function_name: str = "run_smoke_test",
    clock_period: int = 10,
    clock_max_time: int = 1000,
    reset_release_time: int = 22,
    axis_timeout_steps: int = 40,
    enhanced: bool = False,
    style: str = "legacy",
    dut_source_path: str | None = None,
    dut_dependency_paths: list[str] | None = None,
    overrides: object = None,
    strict: bool = True,
    engine: str = "reference",
    cosim: bool = False,
) -> str | Path:
    """Generate a Python testbench skeleton for a module in a parsed Design.

    Args:
        design: Parsed design containing one or more modules.
        module_name: Optional module name. If omitted, uses the sole top module.
        output_path: Optional file path to write the generated Python text.
        function_name: Generated smoke-test function name.
        clock_period: Generated clock period in time units.
        clock_max_time: Duration used when scheduling the generated clock.
        reset_release_time: Time at which the generated scaffold releases reset.
        axis_timeout_steps: Default AXI-Stream wait bound in the generated example.
        enhanced: When ``True``, render a multi-domain skeleton derived from a
            :class:`TestbenchPlan` (per-domain clock scheduling, per-domain
            reset, interfaces grouped by domain, plan summary docstring).
        overrides: Optional :class:`PlannerOverrides` (or dict) — only used
            when ``enhanced=True``.
        strict: Strict-domain inference flag — only used when ``enhanced=True``.
        cosim: When ``True`` and ``dut_source_path`` is provided, append a
            ``validate_with_icarus()`` helper that compares all simulator
            engines against Icarus Verilog.  Requires ``iverilog`` and
            ``vvp`` on ``PATH``.  Default: ``False``.

    Returns:
        Generated Python source text, or the written output path if ``output_path`` is provided.
    """
    from .dsl.testbench import generate_python_testbench

    if module_name is None:
        tops = design.get_top_modules()
        if len(tops) != 1:
            msg = "module_name is required when the design does not have exactly one top module"
            raise ValueError(msg)
        module = tops[0]
    else:
        _m = design.get_module(module_name)
        if _m is None:
            msg = f"Module not found in design: {module_name}"
            raise ValueError(msg)
        module = _m

    text = generate_python_testbench(
        module,
        function_name=function_name,
        clock_period=clock_period,
        clock_max_time=clock_max_time,
        reset_release_time=reset_release_time,
        axis_timeout_steps=axis_timeout_steps,
        enhanced=enhanced,
        style=style,
        dut_source_path=dut_source_path,
        dut_dependency_paths=dut_dependency_paths,
        overrides=overrides,
        strict=strict,
        design=design,
        engine=engine,
        cosim=cosim,
    )

    if output_path is None:
        return text

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return output


def build_testbench_plan(
    design: Design,
    top: str | None = None,
    *,
    overrides: object = None,
    strict: bool = True,
) -> object:
    """Build a :class:`TestbenchPlan` for a module in a parsed design.

    Args:
        design: Parsed design containing one or more modules.
        top: Optional name of the top module. If omitted, the design must
            contain exactly one top module.
        overrides: Optional :class:`PlannerOverrides` (or dict) to force
            clock periods, reset polarities, or interface domain bindings.
        strict: If ``True`` (default), ambiguous interface-to-domain
            mappings raise :class:`AmbiguousDomainError`. If ``False``,
            ambiguous interfaces are bound to the first candidate domain
            (sorted alphabetically) with confidence ``"sole-domain"``
            downgraded to whatever the planner inferred.

    Returns:
        A :class:`TestbenchPlan` describing clock domains, resets, and
        interface bindings for the selected module.
    """
    from .sim.bench.planner import build_plan

    if top is None:
        tops = design.get_top_modules()
        if len(tops) != 1:
            msg = "top is required when the design does not have exactly one top module"
            raise ValueError(msg)
        module = tops[0]
    else:
        _m = design.get_module(top)
        if _m is None:
            msg = f"Module not found in design: {top}"
            raise ValueError(msg)
        module = _m

    return build_plan(module, overrides=overrides, strict=strict, design=design)  # type: ignore[arg-type]


def build_testbench(  # noqa: PLR0913  # cm:4c8a1d
    source: str | Path | list[str | Path] | Design,
    top: str | None = None,
    *,
    recursive: bool = True,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    exclude: list[str] | None = None,
    preprocess: bool = False,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
    cache_dir: str | Path | None = None,
    plan: object = None,
    overrides: object = None,
    strict: bool = True,
    engine: str = "reference",
) -> object:
    """Build a :class:`Testbench` from a file, directory, list of files, or existing :class:`Design`.

    This is the primary convenience entry-point for multi-file projects.  It chains
    project parsing → top-module selection → :class:`Testbench` construction into a
    single call.

    Args:
        source: One of:

            * a :class:`~pathlib.Path` or ``str`` pointing to a **directory** —
              all matching Verilog/SV files under the directory are parsed;
            * a :class:`~pathlib.Path` or ``str`` pointing to a **single file**;
            * a ``list`` of file paths;
            * an already-parsed :class:`~veriforge.model.design.Design` object
              (no parsing is performed in this case).

        top: Name of the top-level module to use as the DUT.  If ``None`` and the
            design contains exactly one top-level module, that module is selected
            automatically.  Otherwise this parameter is required.
        recursive: When *source* is a directory, scan sub-directories recursively
            (default: ``True``).
        extensions: File extensions to include when scanning a directory.
        exclude: Glob patterns to exclude when scanning a directory.
        preprocess: Run the Verilog preprocessor before parsing.
        defines: Pre-processor macro definitions (dict of name→value).
        include_paths: Include search paths for the preprocessor.
        cache_dir: Directory used to cache parse results.
        plan: Pre-built :class:`TestbenchPlan` to use instead of re-inferring.
            When ``None`` (default), the planner is invoked automatically.
        overrides: Optional :class:`~veriforge.sim.bench.planner.PlannerOverrides`
            (or a plain ``dict`` with the same keys) to override clock periods,
            reset polarities, or interface-to-domain bindings.
        strict: If ``True`` (default), ambiguous interface-to-domain mappings raise
            :class:`~veriforge.sim.bench.planner.AmbiguousDomainError`.
        engine: Simulator engine — ``"reference"`` (default), ``"vm"``, or
            ``"compiled"``.

    Returns:
        A fully-constructed :class:`~veriforge.sim.bench.runtime.Testbench`
        ready for use in a Python testbench script.

    Raises:
        ValueError: If *top* is required but not provided, the module is not found,
            or no Verilog files are found in the directory.

    Example::

        from veriforge.scaffold import build_testbench

        # From a directory (auto-detect single top module)
        bench = build_testbench("rtl/")

        # From a directory with explicit top module
        bench = build_testbench("rtl/", top="my_dut")

        # From a list of files
        bench = build_testbench(["top.v", "alu.v"], top="my_top")

        # From a single file
        bench = build_testbench("my_dut.v")

        # With overrides and engine selection
        from veriforge.sim.bench.planner import PlannerOverrides
        bench = build_testbench(
            "rtl/",
            top="my_dut",
            overrides=PlannerOverrides(clock_periods={"clk": 10}),
            engine="vm",
        )
    """
    from .sim.bench.runtime import Testbench

    if isinstance(source, Design):
        design = source
    elif isinstance(source, list):
        design = parse_files(source)
    else:
        path = Path(source)
        if path.is_dir():
            design = parse_directory(
                path,
                recursive=recursive,
                extensions=extensions,
                exclude=exclude,
                preprocess=preprocess,
                defines=defines,
                include_paths=include_paths,
                cache_dir=cache_dir,
            )
        else:
            design = parse_files(
                [path],
                preprocess=preprocess,
                defines=defines,
                include_paths=include_paths,
                cache_dir=cache_dir,
            )

    if top is None:
        tops = design.get_top_modules()
        if len(tops) != 1:
            all_names = [m.name for m in design.modules]
            msg = (
                f"top is required when the design does not have exactly one top module. Available modules: {all_names}"
            )
            raise ValueError(msg)
        module = tops[0]
    else:
        _m = design.get_module(top)
        if _m is None:
            all_names = [m.name for m in design.modules]
            msg = f"Module not found in design: {top!r}. Available modules: {all_names}"
            raise ValueError(msg)
        module = _m

    return Testbench(module, design=design, plan=plan, overrides=overrides, strict=strict, engine=engine)  # type: ignore[arg-type]
