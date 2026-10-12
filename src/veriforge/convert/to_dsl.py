"""Verilog → DSL translator.

Converts parsed Verilog model objects into equivalent Python DSL source code.
The generated code uses the ``veriforge.dsl`` API and can be executed to
reconstruct the same hardware design.

Usage::

    from veriforge.verilog_parser import verilog_parser
    from veriforge.transforms import tree_to_design
    from veriforge.convert.to_dsl import module_to_dsl, design_to_dsl

    vp = verilog_parser(start="module_declaration")
    tree = vp.build_tree(open("counter.v").read())
    design = tree_to_design(tree)

    # Convert a single module
    python_code = module_to_dsl(design.modules[0])

    # Convert all modules in a design
    python_code = design_to_dsl(design)
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

from ..model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from ..model.design import Design, Module
from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    Range,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
    WidthCast,
)
from ..model.instances import Instance
from ..model.interface import Interface
from ..model.nets import Net, NetKind
from ..model.package import ImportDecl, Package
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventControl,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from ..model.sv_types import StructType, TypedefDecl, UnionType
from ..model.variables import Variable, VariableKind

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_INDENT = "    "  # 4 spaces

# Binary operators that map directly to Python operators
_BINOP_PYTHON = {
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "//",
    "%": "%",
    "**": "**",
    "&": "&",
    "|": "|",
    "^": "^",
    "<<": "<<",
    ">>": ">>",
    "==": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}

# Binary operators that need helper functions
_BINOP_FUNC = {
    "&&": "land",
    "||": "lor",
    "~^": None,
    "^~": None,  # lowered to bitwise-not XOR
    "<<<": "ashl",
    ">>>": "ashr",
    "===": "case_eq",
    "!==": "case_ne",
}

# Unary operators mapping
_UNOP_PYTHON = {"~": "~", "-": "-", "+": "+"}
_UNOP_FUNC = {
    "!": "lnot",
    "&": "reduce_and",
    "|": "reduce_or",
    "^": "reduce_xor",
    # (These were `~reduce_and(x)` etc. -- a context-sized bitwise NOT of
    # the 1-bit reduction, which set every upper bit in a wider context.)
    "~&": "reduce_nand",
    "~|": "reduce_nor",
    "~^": "reduce_xnor",
    "^~": "reduce_xnor",
}

# System tasks with direct DSL methods
_SYSTASK_METHOD = {
    "$display": "display",
    "$write": "write",
    "$monitor": "monitor",
    "$finish": "finish",
    "$stop": "stop",
    "$readmemh": "readmemh",
    "$readmemb": "readmemb",
}


# ---------------------------------------------------------------------------
# Width helpers
# ---------------------------------------------------------------------------


def _width_arg(rng: Range | None) -> str | None:
    """Convert a model Range to a DSL ``width=`` argument string.

    Returns ``None`` for scalar (width 1), or the Python expression string
    for the width value.
    """
    if rng is None:
        return None
    msb = _expr_to_python(rng.msb)
    lsb = _expr_to_python(rng.lsb)
    # Common case: [N-1:0] → width=N
    if lsb == "0":
        if isinstance(rng.msb, Literal) and isinstance(rng.msb.value, int):
            return str(rng.msb.value + 1)
        if isinstance(rng.msb, BinaryOp) and rng.msb.op == "-":
            if isinstance(rng.msb.right, Literal) and rng.msb.right.value == 1:
                return _expr_to_python(rng.msb.left)
        return f"{msb} + 1"
    return f"{msb} - {lsb} + 1"


def _system_function_helper_name(expr: FunctionCall) -> str | None:
    """Return the DSL helper name for a supported common system function."""
    name = expr.name.lower()
    if name == "$time" and len(expr.arguments) == 0:
        return "sim_time"
    if name == "$clog2":
        if len(expr.arguments) != 1:
            return None
        return "clog2"
    if name == "$signed":
        if len(expr.arguments) != 1:
            return None
        return "signed"
    if name == "$unsigned":
        if len(expr.arguments) != 1:
            return None
        return "unsigned"
    return None


def _depth_arg(dimensions: list[Range]) -> str | None:
    """Convert array dimensions to a DSL ``depth=`` argument string."""
    if not dimensions:
        return None
    dim = dimensions[0]
    msb = _expr_to_python(dim.msb)
    lsb = _expr_to_python(dim.lsb)
    if lsb == "0":
        if isinstance(dim.msb, Literal) and isinstance(dim.msb.value, int):
            return str(dim.msb.value + 1)
        return f"{msb} + 1"
    if msb == "0":
        if isinstance(dim.lsb, Literal) and isinstance(dim.lsb.value, int):
            return str(dim.lsb.value + 1)
        return f"{lsb} + 1"
    return f"{msb} - {lsb} + 1"


# ---------------------------------------------------------------------------
# Expression → Python string
# ---------------------------------------------------------------------------


def _needs_parens(expr: Expression, parent_op: str | None = None) -> bool:
    """Check if an expression needs parentheses in the DSL context."""
    if isinstance(expr, BinaryOp):
        return True  # conservative: always wrap nested binops
    return False


def _literal_to_python(expr: Literal) -> str:
    """A literal as DSL code: a Python int only for a plain unsized decimal
    (which is what a DSL int means), else ``lit("<Verilog text>")``, which
    keeps the size, base, signedness and x/z bits. (Sized literals used to
    lose their width -- `2'b11` became `3` -- and the parser's string-valued
    ones came out as Python strings, `i['7':'2']`.)"""
    text = expr.original_text.strip()
    if isinstance(expr.value, float):
        return text or str(expr.value)
    if text and "'" not in text and text.isdigit() and int(text) < (1 << 31):
        return str(int(text))
    if text:
        return f'lit("{text}")'
    if isinstance(expr.value, int) and expr.width is None and not expr.is_x and not expr.is_z:
        return str(expr.value)
    # Built without source text (e.g. by a transform): spell it out.
    base = expr.base or ("h" if expr.width is not None and expr.width > 8 else "d")
    if isinstance(expr.value, int):
        digits: dict[str, Callable[[int], str]] = {
            "d": str,
            "h": lambda v: format(v, "x"),
            "b": lambda v: format(v, "b"),
            "o": lambda v: format(v, "o"),
        }
        number = expr.value
        if number < 0 and expr.width is not None:
            number &= (1 << expr.width) - 1  # two's complement in the literal's width
        if number < 0:
            # An unsized negative value (a sized one was masked above).
            return f"-{_literal_to_python(Literal(-number, width=expr.width, base=expr.base, signed=expr.signed))}"
        value = digits.get(base.lower(), str)(number)
    else:
        value = str(expr.value)
    size = str(expr.width) if expr.width is not None else ""
    sign = "s" if expr.signed else ""
    return f'lit("{size}\'{sign}{base}{value}")'


def _expr_to_python(expr: Expression) -> str:  # noqa: PLR0911, PLR0912, PLR0915
    """Convert a model Expression to a Python DSL expression string."""

    # --- Literal ---
    if isinstance(expr, Literal):
        return _literal_to_python(expr)

    # --- StringLiteral ---
    if isinstance(expr, StringLiteral):
        return repr(expr.value)

    # --- Identifier ---
    if isinstance(expr, Identifier):
        if expr.hierarchy:
            return ".".join(expr.hierarchy)
        return expr.name

    # --- UnaryOp ---
    if isinstance(expr, UnaryOp):
        operand = _expr_to_python(expr.operand)
        if expr.op in _UNOP_PYTHON:
            op = _UNOP_PYTHON[expr.op]
            if isinstance(expr.operand, BinaryOp):
                return f"{op}({operand})"
            return f"{op}{operand}"
        if expr.op in _UNOP_FUNC:
            func = _UNOP_FUNC[expr.op]
            if func is not None:
                return f"{func}({operand})"
            return f'unsupported("unary operator {expr.op}")'
        return f'unsupported("unary operator {expr.op}")'

    # --- BinaryOp ---
    if isinstance(expr, BinaryOp):
        left = _expr_to_python(expr.left)
        right = _expr_to_python(expr.right)
        if isinstance(expr.left, BinaryOp):
            left = f"({left})"
        if isinstance(expr.right, BinaryOp):
            right = f"({right})"

        if expr.op in {"~^", "^~"}:
            return f"~({left} ^ {right})"
        if expr.op in _BINOP_PYTHON:
            return f"{left} {_BINOP_PYTHON[expr.op]} {right}"
        if expr.op in _BINOP_FUNC:
            helper = _BINOP_FUNC[expr.op]
            if helper is not None:
                return f"{helper}({left}, {right})"
            # No DSL helper
            return f'unsupported("operator {expr.op}")'
        return f'unsupported("operator {expr.op}")'

    # --- TernaryOp ---
    if isinstance(expr, TernaryOp):
        cond = _expr_to_python(expr.condition)
        true_e = _expr_to_python(expr.true_expr)
        false_e = _expr_to_python(expr.false_expr)
        return f"mux({cond}, {true_e}, {false_e})"

    # --- Concatenation ---
    if isinstance(expr, Concatenation):
        parts = ", ".join(_expr_to_python(p) for p in expr.parts)
        return f"cat({parts})"

    # --- Replication ---
    if isinstance(expr, Replication):
        count = _expr_to_python(expr.count)
        value = _expr_to_python(expr.value)
        return f"rep({count}, {value})"

    # --- BitSelect ---
    if isinstance(expr, BitSelect):
        target = _expr_to_python(expr.target)
        index = _expr_to_python(expr.index)
        return f"{target}[{index}]"

    # --- RangeSelect ---
    if isinstance(expr, RangeSelect):
        target = _expr_to_python(expr.target)
        msb = _expr_to_python(expr.msb)
        lsb = _expr_to_python(expr.lsb)
        return f"{target}[{msb}:{lsb}]"

    # --- PartSelect ---
    if isinstance(expr, PartSelect):
        target = _expr_to_python(expr.target)
        base = _expr_to_python(expr.base)
        width = _expr_to_python(expr.width)
        if expr.direction == "+:":
            return f"{target}.part_select({base}, {width})"
        return f"{target}.part_select_down({base}, {width})"

    # --- FunctionCall ---
    if isinstance(expr, FunctionCall):
        args = ", ".join(_expr_to_python(a) for a in expr.arguments)
        helper = _system_function_helper_name(expr)
        if helper is not None:
            return f"{helper}({args})"
        # (These used to be followed by a `# ...` comment, which cut off the
        # rest of the enclosing expression.)
        if expr.is_system or expr.name.startswith("$"):
            return f'unsupported("system function {expr.name}")'
        return f'unsupported("call of function {expr.name}")'

    # --- Mintypmax ---
    if isinstance(expr, Mintypmax):
        return _expr_to_python(expr.typ_val)  # use typ value

    if isinstance(expr, WidthCast):
        width = _expr_to_python(expr.width)
        return f"cast({width}, {_expr_to_python(expr.operand)})"

    return f'unsupported("{type(expr).__name__} expression")'


# ---------------------------------------------------------------------------
# Statement → Python string (lines)
# ---------------------------------------------------------------------------


def _unsupported_stmt(pfx: str, description: str) -> list[str]:
    """A statement the DSL can't express: an `unsupported(...)` call, so
    building the converted module fails there (see `dsl.unsupported`)."""
    return [f'{pfx}unsupported("{description}")']


def _body_lines(stmt: Statement | None, depth: int, mv: str) -> list[str]:
    """A `with` block's body; ``pass`` when empty (`if (c) ;` used to leave
    the `with` line with no body -- a syntax error)."""
    lines = _stmt_to_lines(stmt, depth, mv) if stmt is not None else []
    return lines or [f"{_INDENT * depth}pass"]


def _stmt_to_lines(stmt: Statement, depth: int = 0, mv: str = "m") -> list[str]:  # noqa: PLR0911, PLR0912, PLR0915
    """Convert a model Statement to lines of Python DSL code.

    Returns a list of strings (no trailing newlines).  ``depth`` tracks
    the indentation level within the behavioral block; ``mv`` is the
    module variable (it used to be a hardcoded ``m``).
    """
    pfx = _INDENT * depth

    # --- SeqBlock (begin...end) → flatten statements ---
    if isinstance(stmt, SeqBlock):
        lines: list[str] = []
        for s in stmt.statements:
            lines.extend(_stmt_to_lines(s, depth, mv))
        return lines

    # --- NonblockingAssign (NBA): lhs <= rhs → lhs <<= rhs ---
    if isinstance(stmt, NonblockingAssign):
        lhs = _expr_to_python(stmt.lhs)
        rhs = _expr_to_python(stmt.rhs)
        return [f"{pfx}{lhs} <<= {rhs}"]

    # --- BlockingAssign: lhs = rhs → lhs @= rhs ---
    if isinstance(stmt, BlockingAssign):
        lhs = _expr_to_python(stmt.lhs)
        rhs = _expr_to_python(stmt.rhs)
        return [f"{pfx}{lhs} @= {rhs}"]

    # --- IfStatement ---
    if isinstance(stmt, IfStatement):
        cond = _expr_to_python(stmt.condition)
        lines = [f"{pfx}with {mv}.if_({cond}):"]
        lines.extend(_body_lines(stmt.then_body, depth + 1, mv))
        # Handle else / else-if chain
        if stmt.else_body:
            if isinstance(stmt.else_body, IfStatement):
                # else if → elif_
                elif_stmt = stmt.else_body
                econd = _expr_to_python(elif_stmt.condition)
                lines.append(f"{pfx}with {mv}.elif_({econd}):")
                lines.extend(_body_lines(elif_stmt.then_body, depth + 1, mv))
                # Recurse for the rest of the elif/else chain
                rest = elif_stmt.else_body
                while rest is not None:
                    if isinstance(rest, IfStatement):
                        rcond = _expr_to_python(rest.condition)
                        lines.append(f"{pfx}with {mv}.elif_({rcond}):")
                        lines.extend(_body_lines(rest.then_body, depth + 1, mv))
                        rest = rest.else_body
                    else:
                        lines.append(f"{pfx}with {mv}.else_():")
                        body_lines = _stmt_to_lines(rest, depth + 1, mv)
                        lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
                        rest = None
            else:
                lines.append(f"{pfx}with {mv}.else_():")
                body_lines = _stmt_to_lines(stmt.else_body, depth + 1, mv)
                lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
        return lines

    # --- CaseStatement ---
    if isinstance(stmt, CaseStatement):
        expr = _expr_to_python(stmt.expression)
        case_method = "case"
        if stmt.case_type == "casex":
            case_method = "casex"
        elif stmt.case_type == "casez":
            case_method = "casez"
        # One name per nesting depth: with a single `_c`, an inner case
        # rebound it and the outer case's later items joined the inner one.
        cv = f"_c{depth}"
        lines = [f"{pfx}with {mv}.{case_method}({expr}) as {cv}:"]
        for item in stmt.items:
            if item.is_default or item.values is None:
                lines.append(f"{pfx}{_INDENT}with {cv}.default():")
            else:
                vals = ", ".join(_expr_to_python(v) for v in item.values)
                lines.append(f"{pfx}{_INDENT}with {cv}.when({vals}):")
            lines.extend(_body_lines(item.body, depth + 2, mv))
        return lines

    # --- SystemTaskCall ---
    if isinstance(stmt, SystemTaskCall):
        name = stmt.task_name
        args = ", ".join(_expr_to_python(a) for a in stmt.arguments)
        if name in _SYSTASK_METHOD:
            method = _SYSTASK_METHOD[name]
            if method in ("finish", "stop") and not args:
                return [f"{pfx}{mv}.{method}()"]
            return [f"{pfx}{mv}.{method}({args})"]
        return _unsupported_stmt(pfx, f"system task {name}")

    # --- DelayControl ---
    if isinstance(stmt, DelayControl):
        delay = _expr_to_python(stmt.delay)
        if stmt.body is None:
            return [f"{pfx}{mv}.delay({delay})"]
        lines = [f"{pfx}with {mv}.delay({delay}):"]
        body_lines = _stmt_to_lines(stmt.body, depth + 1, mv)
        lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
        return lines

    # --- EventControl ---
    if isinstance(stmt, EventControl):
        sens_args = _sensitivity_args(stmt.events)
        if stmt.body is None:
            return [f"{pfx}{mv}.wait_event({sens_args})"]
        lines = [f"{pfx}with {mv}.wait_event({sens_args}):"]
        body_lines = _stmt_to_lines(stmt.body, depth + 1, mv)
        lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
        return lines

    # --- ForLoop ---
    # (It used to be "translated" by writing the body out once, so the
    # converted design ran it once instead of N times.)
    if isinstance(stmt, ForLoop):
        return _unsupported_stmt(pfx, "for loop")

    # --- Unsupported loops ---
    if isinstance(stmt, WhileLoop):
        return _unsupported_stmt(pfx, "while loop")
    if isinstance(stmt, ForeverLoop):
        return _unsupported_stmt(pfx, "forever loop")
    if isinstance(stmt, RepeatLoop):
        return _unsupported_stmt(pfx, "repeat loop")

    # --- ParBlock (fork/join) ---
    # (Its statements used to run in sequence instead.)
    if isinstance(stmt, ParBlock):
        return _unsupported_stmt(pfx, "fork/join block")

    # --- Misc unsupported ---
    # (Each of these used to become a `# UNSUPPORTED` comment, so the
    # converted design silently left the statement out.)
    if isinstance(stmt, WaitStatement):
        return _unsupported_stmt(pfx, "wait statement")
    if isinstance(stmt, DisableStatement):
        return _unsupported_stmt(pfx, f"disable {stmt.target}")
    if isinstance(stmt, EventTrigger):
        return _unsupported_stmt(pfx, f"event trigger -> {stmt.event}")
    if isinstance(stmt, TaskEnable):
        return _unsupported_stmt(pfx, f"call of task {stmt.task_name}")

    return _unsupported_stmt(pfx, f"{type(stmt).__name__} statement")


# ---------------------------------------------------------------------------
# Sensitivity list
# ---------------------------------------------------------------------------


def _sensitivity_args(sens_list: list) -> str:
    """Convert a sensitivity list to DSL function arguments."""
    parts = []
    for edge in sens_list:
        sig = _expr_to_python(edge.signal)
        if edge.edge == "posedge":
            parts.append(f"posedge({sig})")
        elif edge.edge == "negedge":
            parts.append(f"negedge({sig})")
        else:
            parts.append(sig)
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Module → DSL
# ---------------------------------------------------------------------------


def _zero_based_ranges(module: Module) -> Module:
    """A copy of *module* with every constant packed range rewritten as
    ``[w-1:0]`` and its selects remapped, since a DSL signal of width *w* is
    always ``[w-1:0]``. (``[15:8]`` used to become ``[7:0]`` with its selects
    unchanged -- ``a[15:12]`` then read x -- and ``[0:7]`` a negative width.)"""
    from veriforge.sim.ascending import (  # noqa: PLC0415
        flatten_unpacked_arrays,
        lower_ascending_ranges,
        lower_memory_bases,
    )

    # (Memories too: `reg m [1:4]` used to become a negative depth.)
    module = lower_memory_bases(lower_ascending_ranges(copy.deepcopy(module), offsets=True))
    return flatten_unpacked_arrays(module)


def module_to_dsl(module: Module, *, module_var: str = "m") -> str:  # noqa: PLR0912  # cm:3e2a4c
    """Convert a parsed Verilog Module to Python DSL code.

    Parameters
    ----------
    module : Module
        The parsed model ``Module`` to convert.
    module_var : str
        Variable name for the ``Module`` builder (default ``"m"``).

    Returns
    -------
    str
        Complete Python source code that uses the DSL to construct
        the same module.
    """
    module = _zero_based_ranges(module)
    lines: list[str] = []
    v = module_var

    # --- Imports ---
    imports = _collect_imports(module)
    lines.append(imports)
    lines.append("")

    # --- Module declaration ---
    lines.append(f'{v} = Module("{module.name}")')

    # Track which signals are declared as ports (avoid duplicate declarations)
    port_names: set[str] = {p.name for p in module.ports}

    # --- Imports (SV) ---
    for imp in module.imports:
        _emit_import(lines, imp, v)

    # --- Typedefs (SV) ---
    for td in module.typedefs:
        _emit_typedef(lines, td, v)

    # --- Parameters ---
    for param in module.parameters:
        if param.is_local:
            _emit_localparam(lines, param, v)
        else:
            _emit_parameter(lines, param, v)

    # --- Ports ---
    for port in module.ports:
        _emit_port(lines, port, v)

    # --- Internal nets (skip port-declared nets) ---
    for net in module.nets:
        if net.name not in port_names:
            _emit_net(lines, net, v)

    # --- Internal variables (skip port-declared regs) ---
    for var in module.variables:
        if var.name not in port_names:
            _emit_variable(lines, var, v)

    # --- Continuous assigns ---
    for assign in module.continuous_assigns:
        lhs = _expr_to_python(assign.lhs)
        rhs = _expr_to_python(assign.rhs)
        lines.append(f"{v}.assign({lhs}, {rhs})")

    # --- Instances ---
    for inst in module.instances:
        _emit_instance(lines, inst, v)

    # --- Always blocks ---
    for blk in module.always_blocks:
        _emit_always(lines, blk, v)

    # --- Initial blocks ---
    for init_blk in module.initial_blocks:
        _emit_initial(lines, init_blk, v)

    # --- Functions & Tasks (unsupported) ---
    for func in module.functions:
        lines.append(f"# UNSUPPORTED: function {func.name}")
    for task in module.tasks:
        lines.append(f"# UNSUPPORTED: task {task.name}")

    # --- Generate blocks (unsupported; they hold logic, so building fails) ---
    for gen in module.generate_blocks:
        lines.append(f'unsupported("generate block ({type(gen).__name__})")')

    # --- Specify blocks (unsupported) ---
    for _spec in module.specify_blocks:
        lines.append("# UNSUPPORTED: specify block")

    # --- Build ---
    lines.append(f"module = {v}.build()")
    lines.append("")

    lines[0] = _add_used_helpers(lines[0], lines[1:])
    return "\n".join(lines)


def _add_used_helpers(import_line: str, body: list[str]) -> str:
    """Add to *import_line* every DSL helper *body* calls (`lit`, `cast`,
    `unsupported`, ... -- `_collect_imports` only knows the ones it derives
    from the model)."""
    from .. import dsl  # noqa: PLC0415

    prefix = "from veriforge.dsl import "
    if not import_line.startswith(prefix):
        return import_line
    names = {n.strip() for n in import_line[len(prefix) :].split(",")}
    text = "\n".join(body)
    for name in dsl.__all__:
        if name[0].islower() and name not in names and re.search(rf"(?<![\w.]){name}\(", text):
            names.add(name)
    ordered = sorted(names, key=lambda n: (n != "Module", n.lower()))
    return prefix + ", ".join(ordered)


def design_to_dsl(design: Design, *, module_var: str = "m") -> str:
    """Convert all modules, packages, and interfaces in a Design to Python DSL code.

    Each top-level item is separated by a blank line and a comment header.
    """
    parts = []
    for pkg in design.packages:
        parts.append(f"# {'=' * 60}")
        parts.append(f"# Package: {pkg.name}")
        parts.append(f"# {'=' * 60}")
        parts.append("")
        parts.append(package_to_dsl(pkg, module_var=module_var))
    for intf in design.interfaces:
        parts.append(f"# {'=' * 60}")
        parts.append(f"# Interface: {intf.name}")
        parts.append(f"# {'=' * 60}")
        parts.append("")
        parts.append(interface_to_dsl(intf, module_var=module_var))
    for module in design.modules:
        parts.append(f"# {'=' * 60}")
        parts.append(f"# Module: {module.name}")
        parts.append(f"# {'=' * 60}")
        parts.append("")
        parts.append(module_to_dsl(module, module_var=module_var))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Import collector
# ---------------------------------------------------------------------------


def _collect_imports(module: Module) -> str:  # noqa: PLR0912
    """Generate the import statement based on what DSL features are used."""
    imports = ["Module"]
    helpers = set()

    # Check if we need posedge/negedge
    for blk in module.always_blocks:
        for edge in blk.sensitivity_list:
            if edge.edge == "posedge":
                helpers.add("posedge")
            elif edge.edge == "negedge":
                helpers.add("negedge")

    # Walk expressions for helper functions
    for node in module.walk():
        if isinstance(node, TernaryOp):
            helpers.add("mux")
        elif isinstance(node, Concatenation):
            helpers.add("cat")
        elif isinstance(node, Replication):
            helpers.add("rep")
        elif isinstance(node, BinaryOp):
            if node.op == "&&":
                helpers.add("land")
            elif node.op == "||":
                helpers.add("lor")
            elif node.op == "<<<":
                helpers.add("ashl")
            elif node.op == ">>>":
                helpers.add("ashr")
            elif node.op == "===":
                helpers.add("case_eq")
            elif node.op == "!==":
                helpers.add("case_ne")
        elif isinstance(node, FunctionCall):
            helper = _system_function_helper_name(node)
            if helper is not None:
                helpers.add(helper)
        elif isinstance(node, UnaryOp):
            if node.op == "!":
                helpers.add("lnot")
            elif node.op in {"&", "~&"}:
                helpers.add("reduce_and")
            elif node.op in {"|", "~|"}:
                helpers.add("reduce_or")
            elif node.op in {"^", "~^"}:
                helpers.add("reduce_xor")

    all_names = sorted(imports) + sorted(helpers)
    return f"from veriforge.dsl import {', '.join(all_names)}"


# ---------------------------------------------------------------------------
# Declaration emitters
# ---------------------------------------------------------------------------


def _emit_parameter(lines: list[str], param: Parameter, v: str) -> None:
    """Emit a parameter declaration."""
    name = param.name
    default = "0"
    if param.default_value is not None:
        default = _expr_to_python(param.default_value)
    extras = _signed_kwarg(param.signed)
    width = _width_arg(param.width)
    if width is not None:
        extras += f", width={width}"
    lines.append(f'{name} = {v}.parameter("{name}", default={default}{extras})')


def _emit_localparam(lines: list[str], param: Parameter, v: str) -> None:
    """Emit a localparam declaration."""
    name = param.name
    value = "0"
    if param.default_value is not None:
        value = _expr_to_python(param.default_value)
    extras = _signed_kwarg(param.signed)
    width = _width_arg(param.width)
    if width is not None:
        extras += f", width={width}"
    lines.append(f'{name} = {v}.localparam("{name}", value={value}{extras})')


def _emit_port(lines: list[str], port: Port, v: str) -> None:
    """Emit a port declaration."""
    name = port.name
    width = _width_arg(port.width)
    extras = _signed_kwarg(port.signed)

    if port.default_value is not None:
        init_val = _expr_to_python(port.default_value)
        extras += f", init={init_val}"

    width_str = f", width={width}" if width is not None else ""
    # Omit width=1 (default)
    if width == "1":
        width_str = ""

    # Preserve an explicit net_type (`wire`, `logic`, `tri`, ...) so a port
    # declared e.g. `output wire [7:0] y` doesn't silently round-trip back
    # as a bare, typeless `output [7:0] y` -- `output_reg` has no net_type
    # kwarg since a `reg`/`logic`-data_type port can't also carry one.
    net_type_str = f', net_type="{port.net_type}"' if port.net_type else ""

    if port.direction == PortDirection.INPUT:
        lines.append(f'{name} = {v}.input("{name}"{width_str}{extras}{net_type_str})')
    elif port.direction == PortDirection.OUTPUT:
        if port.data_type == "reg":
            lines.append(f'{name} = {v}.output_reg("{name}"{width_str}{extras})')
        else:
            lines.append(f'{name} = {v}.output("{name}"{width_str}{extras}{net_type_str})')
    elif port.direction == PortDirection.INOUT:
        lines.append(f'{name} = {v}.inout("{name}"{width_str}{extras}{net_type_str})')


def _emit_net(lines: list[str], net: Net, v: str) -> None:
    """Emit an internal net declaration."""
    name = net.name
    if len(net.dimensions) > 1 or net.packed_dim_count:
        # (Only the first dimension used to be kept.)
        lines.append(f'{net.name} = unsupported("multi-dimensional array {net.name}")')
        return
    width = _width_arg(net.width)
    extras = _signed_kwarg(net.signed)

    if net.initial_value is not None:
        init_val = _expr_to_python(net.initial_value)
        extras += f", init={init_val}"

    depth = _depth_arg(net.dimensions)
    if depth is not None:
        extras += f", depth={depth}"

    width_str = f", width={width}" if width is not None else ""
    if width == "1":
        width_str = ""

    if net.kind == NetKind.LOGIC:
        # A `logic` declaration is a variable (it may be assigned in an
        # always block); it used to become a `wire`.
        lines.append(f'{name} = {v}.reg("{name}"{width_str}{extras}, kind="logic")')
        return
    lines.append(f'{name} = {v}.wire("{name}"{width_str}{extras})')


def _emit_variable(lines: list[str], var: Variable, v: str) -> None:
    """Emit an internal variable (reg, integer) declaration."""
    name = var.name
    if var.kind in (VariableKind.REAL, VariableKind.REALTIME, VariableKind.TIME, VariableKind.EVENT):
        lines.append(f'{name} = unsupported("{var.kind.value} variable {name}")')
        return

    # reg, or a SystemVerilog variable kind (`logic`, `bit`, `int`, ...;
    # these used to become a `wire` or a 1-bit `reg`).
    if len(var.dimensions) > 1 or var.packed_dim_count:
        # (Only the first dimension used to be kept.)
        lines.append(f'{var.name} = unsupported("multi-dimensional array {var.name}")')
        return
    width = _width_arg(var.width)
    extras = _signed_kwarg(var.signed)
    atom = var.kind in _ATOM_KINDS
    if var.kind != VariableKind.REG:
        extras += f', kind="{var.kind.value}"'
    if atom:
        width = None
        default_signed = True
        extras = extras.replace(", signed=True", "") if var.signed == default_signed else extras + ", signed=False"

    if var.initial_value is not None:
        init_val = _expr_to_python(var.initial_value)
        extras += f", init={init_val}"

    depth = _depth_arg(var.dimensions)
    if depth is not None:
        extras += f", depth={depth}"

    width_str = f", width={width}" if width is not None else ""
    if width == "1":
        width_str = ""

    lines.append(f'{name} = {v}.reg("{name}"{width_str}{extras})')


_ATOM_KINDS = frozenset(
    {VariableKind.INT, VariableKind.INTEGER, VariableKind.BYTE, VariableKind.SHORTINT, VariableKind.LONGINT}
)


def _signed_kwarg(signed: bool) -> str:
    """Return ``', signed=True'`` if signed, else empty string."""
    return ", signed=True" if signed else ""


# ---------------------------------------------------------------------------
# Instance emitter
# ---------------------------------------------------------------------------


def _emit_instance(lines: list[str], inst: Instance, v: str) -> None:
    """Emit a module instantiation."""
    mod_name = inst.module_name
    inst_name = inst.instance_name

    # Port connections → dict
    ports_parts = []
    for pc in inst.port_connections:
        if pc.is_named and pc.port_name:
            if pc.expression is not None:
                val = _expr_to_python(pc.expression)
                ports_parts.append(f'"{pc.port_name}": {val}')
            else:
                ports_parts.append(f'"{pc.port_name}": None  # unconnected')
        elif pc.expression is not None:
            # Positional connection — emit with index comment
            val = _expr_to_python(pc.expression)
            ports_parts.append(f"{val}  # positional")

    # Parameter bindings → dict
    params_parts = []
    for pb in inst.parameter_bindings:
        if pb.name and pb.value is not None:
            val = _expr_to_python(pb.value)
            params_parts.append(f'"{pb.name}": {val}')
        elif pb.value is not None:
            val = _expr_to_python(pb.value)
            params_parts.append(f"{val}  # positional parameter")

    # Format the call
    ports_str = "{" + ", ".join(ports_parts) + "}" if ports_parts else "None"
    params_str = ""
    if params_parts:
        params_str = f", parameters={{{', '.join(params_parts)}}}"

    lines.append(f'{v}.instance("{mod_name}", "{inst_name}", ports={ports_str}{params_str})')


# ---------------------------------------------------------------------------
# Always / Initial block emitters
# ---------------------------------------------------------------------------


def _emit_always(lines: list[str], blk: AlwaysBlock, v: str) -> None:
    """Emit an always block."""
    if blk.sensitivity_type == SensitivityType.COMBINATIONAL or not blk.sensitivity_list:
        lines.append(f"with {v}.always():")
    else:
        args = _sensitivity_args(blk.sensitivity_list)
        lines.append(f"with {v}.always({args}):")

    body_lines = _stmt_to_lines(blk.body, depth=1, mv=v)
    if body_lines:
        lines.extend(body_lines)
    else:
        lines.append(f"{_INDENT}pass")


def _emit_initial(lines: list[str], blk: InitialBlock, v: str) -> None:
    """Emit an initial block."""
    lines.append(f"with {v}.initial():")
    body_lines = _stmt_to_lines(blk.body, depth=1, mv=v)
    if body_lines:
        lines.extend(body_lines)
    else:
        lines.append(f"{_INDENT}pass")


# ---------------------------------------------------------------------------
# SV typedef / import emitters
# ---------------------------------------------------------------------------


def _emit_typedef(lines: list[str], td: TypedefDecl, v: str) -> None:
    """Emit a typedef declaration."""
    if td.enum_type is not None:
        _emit_typedef_enum(lines, td, v)
    elif td.struct_type is not None:
        _emit_typedef_struct(lines, td, td.struct_type, v, "typedef_struct")
    elif td.union_type is not None:
        _emit_typedef_union(lines, td, td.union_type, v)
    elif td.type_ref is not None:
        lines.append(f'{v}.typedef_alias("{td.name}", "{td.type_ref}")')


def _emit_typedef_enum(lines: list[str], td: TypedefDecl, v: str) -> None:
    """Emit a typedef enum declaration."""
    et = td.enum_type
    if et is None:
        return
    # Build members list
    member_parts: list[str] = []
    for m in et.members:
        if m.value is not None:
            member_parts.append(f'("{m.name}", {_expr_to_python(m.value)})')
        else:
            member_parts.append(f'"{m.name}"')
    members_str = f"[{', '.join(member_parts)}]"
    extras = ""
    width = _width_arg(et.width)
    if width is not None:
        extras += f", width={width}"
    if et.base_type is not None:
        extras += f', base_type="{et.base_type}"'
    if et.signed:
        extras += ", signed=True"
    lines.append(f'{v}.typedef_enum("{td.name}", {members_str}{extras})')


def _emit_typedef_struct(lines: list[str], td: TypedefDecl, st: StructType, v: str, method: str) -> None:
    """Emit a typedef struct declaration."""
    field_parts: list[str] = []
    for f in st.fields:
        width = _width_arg(f.width)
        if width is not None:
            field_parts.append(f'("{f.name}", "{f.data_type}", {width})')
        else:
            field_parts.append(f'("{f.name}", "{f.data_type}")')
    fields_str = f"[{', '.join(field_parts)}]"
    extras = ""
    if st.packed:
        extras += ", packed=True"
    if st.signed:
        extras += ", signed=True"
    lines.append(f'{v}.{method}("{td.name}", {fields_str}{extras})')


def _emit_typedef_union(lines: list[str], td: TypedefDecl, ut: UnionType, v: str) -> None:
    """Emit a typedef union declaration."""
    field_parts: list[str] = []
    for f in ut.fields:
        width = _width_arg(f.width)
        if width is not None:
            field_parts.append(f'("{f.name}", "{f.data_type}", {width})')
        else:
            field_parts.append(f'("{f.name}", "{f.data_type}")')
    fields_str = f"[{', '.join(field_parts)}]"
    extras = ""
    if ut.packed:
        extras += ", packed=True"
    if ut.signed:
        extras += ", signed=True"
    lines.append(f'{v}.typedef_union("{td.name}", {fields_str}{extras})')


def _emit_import(lines: list[str], imp: ImportDecl, v: str) -> None:
    """Emit a package import declaration."""
    if imp.item_name == "*":
        lines.append(f'{v}.import_pkg("{imp.package_name}")')
    else:
        lines.append(f'{v}.import_pkg("{imp.package_name}", "{imp.item_name}")')


# ---------------------------------------------------------------------------
# Package → DSL
# ---------------------------------------------------------------------------


def package_to_dsl(pkg: Package, *, module_var: str = "m") -> str:  # noqa: PLR0912
    """Convert a parsed Package to Python DSL code (informational comment block).

    Packages are emitted as commented DSL-style code since full package
    builder support is outside the current scope.
    """
    lines: list[str] = []
    lines.append(f"# Package: {pkg.name}")

    for imp in pkg.imports:
        if imp.item_name == "*":
            lines.append(f"# import {imp.package_name}::*")
        else:
            lines.append(f"# import {imp.package_name}::{imp.item_name}")

    for param in pkg.parameters:
        if param.is_local:
            value = _expr_to_python(param.default_value) if param.default_value is not None else "0"
            lines.append(f"# localparam {param.name} = {value}")
        else:
            default = _expr_to_python(param.default_value) if param.default_value is not None else "0"
            lines.append(f"# parameter {param.name} = {default}")

    for td in pkg.typedefs:
        if td.enum_type is not None:
            members = ", ".join(m.name for m in td.enum_type.members)
            lines.append(f"# typedef enum {{ {members} }} {td.name}")
        elif td.struct_type is not None:
            fields = ", ".join(f.name for f in td.struct_type.fields)
            lines.append(f"# typedef struct {{ {fields} }} {td.name}")
        elif td.union_type is not None:
            fields = ", ".join(f.name for f in td.union_type.fields)
            lines.append(f"# typedef union {{ {fields} }} {td.name}")
        elif td.type_ref is not None:
            lines.append(f"# typedef {td.type_ref} {td.name}")

    for func in pkg.functions:
        lines.append(f"# function {func.name}")
    for task in pkg.tasks:
        lines.append(f"# task {task.name}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interface → DSL
# ---------------------------------------------------------------------------


def interface_to_dsl(intf: Interface, *, module_var: str = "m") -> str:
    """Convert a parsed Interface to Python DSL code (informational comment block).

    Interfaces are emitted as commented DSL-style code since full interface
    builder support is outside the current scope.
    """
    lines: list[str] = []
    lines.append(f"# Interface: {intf.name}")

    for imp in intf.imports:
        if imp.item_name == "*":
            lines.append(f"# import {imp.package_name}::*")
        else:
            lines.append(f"# import {imp.package_name}::{imp.item_name}")

    for param in intf.parameters:
        if param.is_local:
            value = _expr_to_python(param.default_value) if param.default_value is not None else "0"
            lines.append(f"# localparam {param.name} = {value}")
        else:
            default = _expr_to_python(param.default_value) if param.default_value is not None else "0"
            lines.append(f"# parameter {param.name} = {default}")

    for td in intf.typedefs:
        if td.enum_type is not None:
            members = ", ".join(m.name for m in td.enum_type.members)
            lines.append(f"# typedef enum {{ {members} }} {td.name}")
        elif td.struct_type is not None:
            fields = ", ".join(f.name for f in td.struct_type.fields)
            lines.append(f"# typedef struct {{ {fields} }} {td.name}")

    for net in intf.nets:
        width = _width_arg(net.width)
        w = f" [{width}]" if width is not None else ""
        lines.append(f"# {net.kind.value}{w} {net.name}")

    for var in intf.variables:
        width = _width_arg(var.width)
        w = f" [{width}]" if width is not None else ""
        lines.append(f"# {var.kind.value}{w} {var.name}")

    for mp in intf.modports:
        port_strs = [f"{p.direction.value} {p.name}" for p in mp.ports]
        lines.append(f"# modport {mp.name}({', '.join(port_strs)})")

    lines.append("")
    return "\n".join(lines)
