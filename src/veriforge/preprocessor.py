"""Verilog preprocessor — text-level directive processing.

Handles compiler directives before the Lark parser sees the source:
  `define, `undef, `ifdef, `ifndef, `else, `elsif, `endif,
  `include, `timescale, `resetall, `default_nettype, `pragma,
  `line, `celldefine, `endcelldefine, `nounconnected_drive,
  `unconnected_drive

Usage::

    from veriforge.preprocessor import preprocess, preprocess_file

    # Preprocess a string
    output = preprocess(source_text, defines={"SIMULATION": ""})

    # Preprocess a file (resolves `include relative to its directory)
    output = preprocess_file("rtl/top.v", defines={"__ICARUS__": ""})

    # Preprocess a file, get defines back for chaining
    output, final_defines = preprocess_file(
        "rtl/top.v",
        defines={"__ICARUS__": ""},
        return_defines=True,
    )
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Matches a preprocessor directive at the start of a (possibly indented) line.
# Group 1: directive name (e.g. "define", "ifdef")
# Group 2: rest of the line after the directive keyword
# re.DOTALL so (.*) captures across newlines from joined continuation lines.
_DIRECTIVE_RE = re.compile(r"^\s*`(\w+)\b(.*)", re.DOTALL)

# Matches a `define with optional value: `define NAME value...
# Group 1: macro name
# Group 2: optional parameter list "(arg1, arg2)" (no whitespace before paren)
# Group 3: macro body (may be empty, may have line continuations)
_DEFINE_RE = re.compile(r"^\s*(\w+)(\([^)]*\))?(?:\s+(.*))?$", re.DOTALL)

# Matches `include argument: "filename" or <filename>
_INCLUDE_RE = re.compile(r'^\s*"([^"]+)"\s*$|^\s*<([^>]+)>\s*$')

# Matches a macro invocation in source text: `NAME
# Avoids matching directives we handle specially.
_HANDLED_DIRECTIVES = frozenset(
    {
        "define",
        "undef",
        "ifdef",
        "ifndef",
        "else",
        "elsif",
        "endif",
        "include",
        "timescale",
        "resetall",
        "default_nettype",
        "pragma",
        "line",
        "celldefine",
        "endcelldefine",
        "unconnected_drive",
        "nounconnected_drive",
    }
)

# Directives that do not contribute syntax the grammar needs to see and can be
# blanked safely before parsing even when full preprocessing is not enabled.
_PARSER_BLOCKING_DIRECTIVES = frozenset(
    {
        "timescale",
        "resetall",
        "default_nettype",
        "pragma",
        "line",
        "celldefine",
        "endcelldefine",
        "unconnected_drive",
        "nounconnected_drive",
    }
)

# Maximum include depth to prevent infinite recursion
_MAX_INCLUDE_DEPTH = 64


class PreprocessorError(Exception):
    """Raised on unrecoverable preprocessor errors."""

    def __init__(self, message: str, file: str | None = None, line: int | None = None):
        self.file = file
        self.line = line
        loc = ""
        if file:
            loc += f"{file}"
        if line is not None:
            loc += f":{line}"
        if loc:
            message = f"{loc}: {message}"
        super().__init__(message)


def preprocess(  # noqa: PLR0913  # cm:8f2d5b
    source: str,
    *,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
    source_file: str | None = None,
    strip_comments: bool = False,
    return_defines: bool = False,
    _depth: int = 0,
    _parent_files: frozenset[str] | None = None,
) -> str | tuple[str, dict[str, str]]:
    """Preprocess Verilog source text.

    Args:
        source: Raw Verilog source text.
        defines: Initial macro definitions (name → value).
        include_paths: Directories to search for `include files.
        source_file: Path of the source file (for include resolution and errors).
        strip_comments: If True, strip // and /* */ comments before processing.
        return_defines: If True, return (output, defines_dict) tuple.
        _depth: Internal recursion depth counter (do not set).
        _parent_files: Internal include guard set (do not set).

    Returns:
        Preprocessed source text, or (text, defines) if return_defines=True.
    """
    ctx = _PreprocessContext(
        defines=dict(defines) if defines else {},
        include_paths=[Path(p) for p in include_paths] if include_paths else [],
        source_file=source_file,
        depth=_depth,
        parent_files=_parent_files or frozenset(),
    )

    if source_file:
        src_dir = Path(source_file).parent
        if src_dir not in ctx.include_paths:
            ctx.include_paths.insert(0, src_dir)

    if strip_comments:
        source = _strip_comments(source)

    output = _process_source(source, ctx)

    if return_defines:
        return output, ctx.defines
    return output


def preprocess_file(  # noqa: PLR0913
    path: str | Path,
    *,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
    strip_comments: bool = False,
    return_defines: bool = False,
    encoding: str = "utf-8",
) -> str | tuple[str, dict[str, str]]:
    """Preprocess a Verilog file.

    Like ``preprocess()`` but reads from a file path and automatically
    includes the file's directory in the include search path.

    Args:
        path: Path to the Verilog source file.
        defines: Initial macro definitions.
        include_paths: Additional directories for `include resolution.
        strip_comments: If True, strip comments before processing.
        return_defines: If True, return (output, defines_dict) tuple.
        encoding: File encoding.

    Returns:
        Preprocessed source text, or (text, defines) if return_defines=True.
    """
    path = Path(path).resolve()
    source = path.read_text(encoding=encoding)
    return preprocess(
        source,
        defines=defines,
        include_paths=include_paths,
        source_file=str(path),
        strip_comments=strip_comments,
        return_defines=return_defines,
    )


def strip_parser_blocking_directives(source: str) -> str:
    """Blank parser-blocking directive lines while preserving line numbers.

    This is intentionally narrower than full preprocessing: it only strips
    non-syntactic directives like `` `timescale `` so the raw grammar parser can
    accept common real-world files without implicitly enabling macro/include
    expansion.
    """

    output_lines: list[str] = []
    for line in source.split("\n"):
        directive, directive_name, _directive_args = _parse_directive(line)
        if directive and directive_name in _PARSER_BLOCKING_DIRECTIVES:
            output_lines.append("")
        else:
            output_lines.append(line)
    return "\n".join(output_lines)


class _PreprocessContext:
    """Mutable state carried through preprocessing."""

    __slots__ = ("defines", "depth", "include_paths", "parent_files", "source_file")

    def __init__(
        self,
        defines: dict[str, str],
        include_paths: list[Path],
        source_file: str | None,
        depth: int,
        parent_files: frozenset[str],
    ):
        self.defines = defines
        self.include_paths = include_paths
        self.source_file = source_file
        self.depth = depth
        self.parent_files = parent_files


def _process_source(source: str, ctx: _PreprocessContext) -> str:  # noqa: PLR0912, PLR0915
    """Core preprocessing loop — processes source line by line."""
    lines = source.split("\n")
    output_lines: list[str] = []

    # ifdef/ifndef stack: list of (active, seen_true_branch, in_else)
    # active: whether current branch is emitting output
    # seen_true_branch: whether any branch in this if/elif chain was true
    # in_else: whether we've seen `else for this level
    cond_stack: list[tuple[bool, bool, bool]] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Handle line continuations in `define
        # (backslash at end of line joins with next)
        full_line = line
        while full_line.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            full_line = full_line.rstrip()[:-1] + "\n" + lines[i]

        lineno = i + 1  # 1-based for error messages

        # Check if this line has a directive
        directive, directive_name, directive_args = _parse_directive(full_line)

        if directive:
            if directive_name == "endif":
                if not cond_stack:
                    raise PreprocessorError("`endif without matching `ifdef/`ifndef", ctx.source_file, lineno)
                cond_stack.pop()
                output_lines.append("")  # blank line preserves line numbers
                i += 1
                continue

            if directive_name == "else":
                if not cond_stack:
                    raise PreprocessorError("`else without matching `ifdef/`ifndef", ctx.source_file, lineno)
                active, seen_true, in_else = cond_stack[-1]
                if in_else:
                    raise PreprocessorError("duplicate `else", ctx.source_file, lineno)
                # `else is active only if parent is active AND no branch was true yet
                parent_active = _parent_active(cond_stack)
                new_active = parent_active and not seen_true
                cond_stack[-1] = (new_active, seen_true or new_active, True)
                output_lines.append("")
                i += 1
                continue

            if directive_name == "elsif":
                if not cond_stack:
                    raise PreprocessorError("`elsif without matching `ifdef/`ifndef", ctx.source_file, lineno)
                active, seen_true, in_else = cond_stack[-1]
                if in_else:
                    raise PreprocessorError("`elsif after `else", ctx.source_file, lineno)
                parent_active = _parent_active(cond_stack)
                macro_name = directive_args.strip()
                if not macro_name:
                    raise PreprocessorError("`elsif requires a macro name", ctx.source_file, lineno)
                condition = macro_name in ctx.defines
                new_active = parent_active and not seen_true and condition
                cond_stack[-1] = (new_active, seen_true or new_active, False)
                output_lines.append("")
                i += 1
                continue

            if directive_name in ("ifdef", "ifndef"):
                macro_name = directive_args.strip()
                if not macro_name:
                    raise PreprocessorError(f"`{directive_name} requires a macro name", ctx.source_file, lineno)
                parent_active = _all_active(cond_stack)
                if directive_name == "ifdef":
                    condition = macro_name in ctx.defines
                else:
                    condition = macro_name not in ctx.defines
                active = parent_active and condition
                cond_stack.append((active, active, False))
                output_lines.append("")
                i += 1
                continue

            # For all other directives, check if we're in an active branch
            if not _all_active(cond_stack):
                output_lines.append("")
                i += 1
                continue

            # Active branch — process directive
            if directive_name == "define":
                _handle_define(directive_args, ctx, lineno)
                output_lines.append("")
                i += 1
                continue

            if directive_name == "undef":
                macro_name = directive_args.strip()
                if not macro_name:
                    raise PreprocessorError("`undef requires a macro name", ctx.source_file, lineno)
                ctx.defines.pop(macro_name, None)
                output_lines.append("")
                i += 1
                continue

            if directive_name == "include":
                included = _handle_include(directive_args, ctx, lineno)
                # Included content may contain multiple lines
                inc_lines = included.split("\n")
                # First included line replaces the `include line
                output_lines.extend(inc_lines)
                i += 1
                continue

            if directive_name in (
                "timescale",
                "resetall",
                "default_nettype",
                "pragma",
                "line",
                "celldefine",
                "endcelldefine",
                "unconnected_drive",
                "nounconnected_drive",
            ):
                # Strip these directives — emit blank to preserve line numbers
                output_lines.append("")
                i += 1
                continue

            # Unknown directive — leave as-is (may be a macro invocation
            # that starts a line, like `MACRO_NAME)
            if not _all_active(cond_stack):
                output_lines.append("")
            else:
                expanded_line = full_line
                if "`" in expanded_line:
                    expanded_line, extra_lines = _join_multiline_macro(expanded_line, lines, i)
                    for _ in range(extra_lines):
                        i += 1
                        output_lines.append("")
                expanded = _expand_macros(expanded_line, ctx)
                output_lines.append(expanded)
            i += 1
            continue

        # Not a directive line
        if not _all_active(cond_stack):
            output_lines.append("")  # blank line preserves line count
        else:
            # Handle multi-line macro invocations: if line has an unbalanced
            # macro call (backtick + name + open paren without close), join
            # subsequent lines until parens balance.
            expanded_line = full_line
            if "`" in expanded_line:
                expanded_line, extra_lines = _join_multiline_macro(expanded_line, lines, i)
                for _ in range(extra_lines):
                    i += 1
                    output_lines.append("")  # blank to preserve line numbers
            expanded = _expand_macros(expanded_line, ctx)
            output_lines.append(expanded)

        i += 1

    if cond_stack:
        raise PreprocessorError(
            f"unterminated `ifdef/`ifndef ({len(cond_stack)} level(s) open)",
            ctx.source_file,
        )

    return "\n".join(output_lines)


def _parse_directive(line: str) -> tuple[bool, str, str]:
    """Check if a line contains a preprocessor directive.

    Returns (is_directive, directive_name, rest_of_line).
    """
    m = _DIRECTIVE_RE.match(line)
    if not m:
        return False, "", ""
    name = m.group(1)
    rest = m.group(2) or ""
    # Only treat as directive if it's a known directive keyword
    if name in _HANDLED_DIRECTIVES:
        return True, name, rest
    # Could be a macro invocation that starts the line — not a directive
    return False, "", ""


def _all_active(cond_stack: list[tuple[bool, bool, bool]]) -> bool:
    """True if all levels of the conditional stack are active."""
    return all(active for active, _, _ in cond_stack)


def _parent_active(cond_stack: list[tuple[bool, bool, bool]]) -> bool:
    """True if all levels EXCEPT the top are active."""
    return all(active for active, _, _ in cond_stack[:-1])


def _strip_define_comment(value: str) -> str:
    """Strip // line comment from a `define value, respecting string literals."""
    in_string = False
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '"' and (i == 0 or value[i - 1] != "\\"):
            in_string = not in_string
        elif not in_string and ch == "/" and i + 1 < len(value) and value[i + 1] == "/":
            return value[:i].rstrip()
        i += 1
    return value


def _handle_define(args: str, ctx: _PreprocessContext, lineno: int) -> None:
    """Process a `define directive.

    Supports both simple macros (`define NAME value) and parameterized
    macros (`define NAME(arg1, arg2) body).  Parameterized macros are
    stored as (params_list, body) tuples in ctx.defines.
    """
    m = _DEFINE_RE.match(args)
    if not m:
        raise PreprocessorError(f"invalid `define syntax: `define{args}", ctx.source_file, lineno)
    name = m.group(1)
    params_str = m.group(2)  # e.g. "(arg1, arg2)" or None
    value = m.group(3) or ""
    # Collapse line continuations and trim
    value = value.replace("\\\n", " ").strip()
    # Strip trailing // line comments (but not inside string literals)
    value = _strip_define_comment(value)
    if params_str is not None:
        # Parameterized macro: store as (param_names, body)
        param_names = [p.strip() for p in params_str[1:-1].split(",") if p.strip()]
        ctx.defines[name] = (param_names, value)  # type: ignore[assignment]
    else:
        ctx.defines[name] = value


def _handle_include(args: str, ctx: _PreprocessContext, lineno: int) -> str:
    """Process an `include directive — returns the preprocessed content."""
    m = _INCLUDE_RE.match(args)
    if not m:
        raise PreprocessorError(f"invalid `include syntax: `include{args}", ctx.source_file, lineno)
    filename = m.group(1) or m.group(2)

    if ctx.depth >= _MAX_INCLUDE_DEPTH:
        raise PreprocessorError(
            f"`include depth exceeds {_MAX_INCLUDE_DEPTH} (possible recursion)",
            ctx.source_file,
            lineno,
        )

    # Search for the file
    resolved = _resolve_include(filename, ctx)
    if resolved is None:
        raise PreprocessorError(
            f"cannot find include file: {filename!r} (searched {[str(p) for p in ctx.include_paths]})",
            ctx.source_file,
            lineno,
        )

    resolved_str = str(resolved)
    if resolved_str in ctx.parent_files:
        log.warning("Skipping recursive include of %s", resolved_str)
        return ""

    log.debug("Including %s (depth=%d)", resolved_str, ctx.depth + 1)
    inc_source = resolved.read_text(encoding="utf-8")

    # Build a child context that SHARES the defines dict so that
    # defines from the included file propagate back to the parent.
    inc_ctx = _PreprocessContext(
        defines=ctx.defines,
        include_paths=list(ctx.include_paths),
        source_file=resolved_str,
        depth=ctx.depth + 1,
        parent_files=ctx.parent_files | {resolved_str},
    )
    # Add the included file's directory to the front of search paths
    inc_dir = resolved.parent
    if inc_dir not in inc_ctx.include_paths:
        inc_ctx.include_paths.insert(0, inc_dir)

    return _process_source(inc_source, inc_ctx)


def _resolve_include(filename: str, ctx: _PreprocessContext) -> Path | None:
    """Search include paths for a file, return resolved Path or None."""
    # Try each include path
    for base in ctx.include_paths:
        candidate = base / filename
        if candidate.is_file():
            return candidate.resolve()

    # Also try as an absolute or CWD-relative path
    p = Path(filename)
    if p.is_file():
        return p.resolve()

    return None


def _has_unbalanced_macro_parens(line: str) -> bool:
    """Check if a line has a macro invocation with unbalanced parentheses."""
    i = 0
    while i < len(line):
        if line[i] == "`" and i + 1 < len(line) and (line[i + 1].isalpha() or line[i + 1] == "_"):
            # Found macro start — skip name
            j = i + 1
            while j < len(line) and (line[j].isalnum() or line[j] == "_"):
                j += 1
            # Check if followed by '('
            if j < len(line) and line[j] == "(":
                # Count paren depth from here
                depth = 0
                k = j
                in_string = False
                while k < len(line):
                    ch = line[k]
                    if ch == '"' and (k == 0 or line[k - 1] != "\\"):
                        in_string = not in_string
                    elif not in_string:
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                            if depth == 0:
                                break
                    k += 1
                if depth > 0:
                    return True
            i = j
        else:
            i += 1
    return False


def _join_multiline_macro(line: str, all_lines: list[str], current_idx: int) -> tuple[str, int]:
    """Join continuation lines for multi-line macro invocations.

    Returns (joined_line, number_of_extra_lines_consumed).
    """
    if not _has_unbalanced_macro_parens(line):
        return line, 0

    joined = line
    extra = 0
    for k in range(current_idx + 1, len(all_lines)):
        joined = joined + "\n" + all_lines[k]
        extra += 1
        if not _has_unbalanced_macro_parens(joined):
            break
    return joined, extra


def _expand_macros(line: str, ctx: _PreprocessContext) -> str:
    """Expand macro invocations in a line of source text.

    Handles both simple macros (`NAME → value) and parameterized
    macros (`NAME(arg1, arg2) → body with substitutions).
    Iterates until no more expansions occur (handles nested macros).
    """
    if "`" not in line:
        return line

    for _ in range(64):
        expanded = _expand_macros_once(line, ctx)
        if expanded == line:
            break
        line = expanded
    return line


def _expand_macros_once(line: str, ctx: _PreprocessContext) -> str:
    """Single pass of macro expansion."""
    result: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == "`" and i + 1 < len(line) and (line[i + 1].isalpha() or line[i + 1] == "_"):
            # Extract macro name
            j = i + 1
            while j < len(line) and (line[j].isalnum() or line[j] == "_"):
                j += 1
            name = line[i + 1 : j]

            if name in _HANDLED_DIRECTIVES:
                result.append(line[i:j])
                i = j
                continue

            if name in ctx.defines:
                defn = ctx.defines[name]
                if isinstance(defn, tuple):
                    # Parameterized macro — look for (args)
                    param_names, body = defn
                    if j < len(line) and line[j] == "(":
                        args, end = _parse_macro_args(line, j)
                        # Substitute parameters in body
                        expanded_body = body
                        for pname, aval in zip(param_names, args, strict=False):
                            expanded_body = expanded_body.replace(pname, aval)
                        result.append(expanded_body)
                        i = end
                        continue
                    else:
                        # Parameterized macro invoked without args — leave as-is
                        result.append(line[i:j])
                        i = j
                        continue
                else:
                    # Simple macro
                    result.append(defn)
                    i = j
                    continue

            result.append(line[i:j])
            i = j
        else:
            result.append(line[i])
            i += 1
    return "".join(result)


def _parse_macro_args(text: str, start: int) -> tuple[list[str], int]:  # noqa: PLR0912
    """Parse parenthesized macro arguments starting at '(' position.

    Handles nested parentheses, braces, brackets and string literals.
    Returns the list of argument strings and the index after the closing ')'.
    """
    if text[start] != "(":
        msg = f"macro argument parse must start at '(', got {text[start]!r}"
        raise ValueError(msg)
    depth = 0  # parentheses depth
    brace_depth = 0  # curly braces depth
    bracket_depth = 0  # square brackets depth
    args: list[str] = []
    current: list[str] = []
    i = start + 1
    in_string = False
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_string = not in_string
            current.append(ch)
        elif in_string:
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")" and depth > 0:
            depth -= 1
            current.append(ch)
        elif ch == ")" and depth == 0 and brace_depth == 0 and bracket_depth == 0:
            args.append("".join(current).strip())
            return args, i + 1
        elif ch == "{":
            brace_depth += 1
            current.append(ch)
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)
            current.append(ch)
        elif ch == "[":
            bracket_depth += 1
            current.append(ch)
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0 and brace_depth == 0 and bracket_depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    # Unterminated — return what we have
    args.append("".join(current).strip())
    return args, i


def _strip_comments(source: str) -> str:
    """Strip // and /* */ comments, preserving line structure."""

    def _blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group())

    return re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", _blank, source)
