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
)
from ..model.instances import Instance
from ..model.interface import Interface
from ..model.nets import Net
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
    "~&": None,
    "~|": None,
    "~^": None,  # lowered to bitwise-not reduction
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


def _expr_to_python(expr: Expression) -> str:  # noqa: PLR0911, PLR0912, PLR0915
    """Convert a model Expression to a Python DSL expression string."""

    # --- Literal ---
    if isinstance(expr, Literal):
        if isinstance(expr.value, int):
            # Prefer hex for values >= 256 with explicit width
            if expr.width is not None and expr.value >= 256:  # noqa: PLR2004
                return hex(expr.value)
            return str(expr.value)
        if isinstance(expr.value, float):
            return str(expr.value)
        return repr(expr.value)

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
        if expr.op == "~&":
            return f"~reduce_and({operand})"
        if expr.op == "~|":
            return f"~reduce_or({operand})"
        if expr.op == "~^":
            return f"~reduce_xor({operand})"
        if expr.op in _UNOP_PYTHON:
            op = _UNOP_PYTHON[expr.op]
            if isinstance(expr.operand, BinaryOp):
                return f"{op}({operand})"
            return f"{op}{operand}"
        if expr.op in _UNOP_FUNC:
            func = _UNOP_FUNC[expr.op]
            if func is not None:
                return f"{func}({operand})"
            return f"({operand})  # UNSUPPORTED: unary {expr.op}"
        return f"({operand})  # UNSUPPORTED: unary {expr.op}"

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
            func = _BINOP_FUNC[expr.op]
            if func is not None:
                return f"{func}({left}, {right})"
            # No DSL helper
            return f"{left}  # UNSUPPORTED: operator {expr.op}"
        return f"{left}  # UNSUPPORTED: operator {expr.op}"

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
        if expr.is_system or expr.name.startswith("$"):
            return f'FunctionCall("{expr.name}", [{args}])  # system function'
        return f"{expr.name}({args})  # user function"

    # --- Mintypmax ---
    if isinstance(expr, Mintypmax):
        return _expr_to_python(expr.typ_val)  # use typ value

    return f"None  # UNSUPPORTED expression: {type(expr).__name__}"


# ---------------------------------------------------------------------------
# Statement → Python string (lines)
# ---------------------------------------------------------------------------


def _stmt_to_lines(stmt: Statement, depth: int = 0) -> list[str]:  # noqa: PLR0911, PLR0912, PLR0915
    """Convert a model Statement to lines of Python DSL code.

    Returns a list of strings (no trailing newlines).  ``depth`` tracks
    the indentation level within the behavioral block.
    """
    pfx = _INDENT * depth

    # --- SeqBlock (begin...end) → flatten statements ---
    if isinstance(stmt, SeqBlock):
        lines: list[str] = []
        for s in stmt.statements:
            lines.extend(_stmt_to_lines(s, depth))
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
        lines = [f"{pfx}with m.if_({cond}):"]
        if stmt.then_body:
            body_lines = _stmt_to_lines(stmt.then_body, depth + 1)
            lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
        # Handle else / else-if chain
        if stmt.else_body:
            if isinstance(stmt.else_body, IfStatement):
                # else if → elif_
                elif_stmt = stmt.else_body
                econd = _expr_to_python(elif_stmt.condition)
                lines.append(f"{pfx}with m.elif_({econd}):")
                if elif_stmt.then_body:
                    body_lines = _stmt_to_lines(elif_stmt.then_body, depth + 1)
                    lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
                # Recurse for the rest of the elif/else chain
                rest = elif_stmt.else_body
                while rest is not None:
                    if isinstance(rest, IfStatement):
                        rcond = _expr_to_python(rest.condition)
                        lines.append(f"{pfx}with m.elif_({rcond}):")
                        if rest.then_body:
                            body_lines = _stmt_to_lines(rest.then_body, depth + 1)
                            lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
                        rest = rest.else_body
                    else:
                        lines.append(f"{pfx}with m.else_():")
                        body_lines = _stmt_to_lines(rest, depth + 1)
                        lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
                        rest = None
            else:
                lines.append(f"{pfx}with m.else_():")
                body_lines = _stmt_to_lines(stmt.else_body, depth + 1)
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
        lines = [f"{pfx}with m.{case_method}({expr}) as _c:"]
        for item in stmt.items:
            if item.is_default or item.values is None:
                lines.append(f"{pfx}{_INDENT}with _c.default():")
            else:
                vals = ", ".join(_expr_to_python(v) for v in item.values)
                lines.append(f"{pfx}{_INDENT}with _c.when({vals}):")
            if item.body:
                body_lines = _stmt_to_lines(item.body, depth + 2)
                lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}{_INDENT}pass"])
        return lines

    # --- SystemTaskCall ---
    if isinstance(stmt, SystemTaskCall):
        name = stmt.task_name
        args = ", ".join(_expr_to_python(a) for a in stmt.arguments)
        if name in _SYSTASK_METHOD:
            method = _SYSTASK_METHOD[name]
            if method in ("finish", "stop") and not args:
                return [f"{pfx}m.{method}()"]
            return [f"{pfx}m.{method}({args})"]
        # Unsupported system task — emit as comment
        return [f"{pfx}# UNSUPPORTED: {name}({args})"]

    # --- DelayControl ---
    if isinstance(stmt, DelayControl):
        delay = _expr_to_python(stmt.delay)
        if stmt.body is None:
            return [f"{pfx}m.delay({delay})"]
        lines = [f"{pfx}with m.delay({delay}):"]
        body_lines = _stmt_to_lines(stmt.body, depth + 1)
        lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
        return lines

    # --- EventControl ---
    if isinstance(stmt, EventControl):
        sens_args = _sensitivity_args(stmt.events)
        if stmt.body is None:
            return [f"{pfx}m.wait_event({sens_args})"]
        lines = [f"{pfx}with m.wait_event({sens_args}):"]
        body_lines = _stmt_to_lines(stmt.body, depth + 1)
        lines.extend(body_lines if body_lines else [f"{pfx}{_INDENT}pass"])
        return lines

    # --- ForLoop ---
    if isinstance(stmt, ForLoop):
        # Translate to Python for loop where possible
        init_var = _expr_to_python(stmt.init.lhs)
        init_val = _expr_to_python(stmt.init.rhs)
        cond = _expr_to_python(stmt.condition)
        update = _expr_to_python(stmt.update.rhs)
        lines = [f"{pfx}# Verilog for-loop: for ({init_var}={init_val}; {cond}; {init_var}={update})"]
        lines.append(f"{pfx}# Translated as inline statements (for-loops not in DSL)")
        body_lines = _stmt_to_lines(stmt.body, depth) if stmt.body is not None else []
        lines.extend(body_lines)
        return lines

    # --- Unsupported loops ---
    if isinstance(stmt, WhileLoop):
        return [f"{pfx}# UNSUPPORTED: while loop"]
    if isinstance(stmt, ForeverLoop):
        return [f"{pfx}# UNSUPPORTED: forever loop"]
    if isinstance(stmt, RepeatLoop):
        return [f"{pfx}# UNSUPPORTED: repeat loop"]

    # --- ParBlock (fork/join) ---
    if isinstance(stmt, ParBlock):
        lines = [f"{pfx}# UNSUPPORTED: fork/join block"]
        for s in stmt.statements:
            lines.extend(_stmt_to_lines(s, depth))
        return lines

    # --- Misc unsupported ---
    if isinstance(stmt, WaitStatement):
        cond = _expr_to_python(stmt.condition)
        return [f"{pfx}# UNSUPPORTED: wait({cond})"]
    if isinstance(stmt, DisableStatement):
        return [f"{pfx}# UNSUPPORTED: disable {stmt.target}"]
    if isinstance(stmt, EventTrigger):
        return [f"{pfx}# UNSUPPORTED: -> {stmt.event}"]
    if isinstance(stmt, TaskEnable):
        args = ", ".join(_expr_to_python(a) for a in stmt.arguments)
        return [f"{pfx}# UNSUPPORTED: {stmt.task_name}({args})"]

    return [f"{pfx}# UNSUPPORTED statement: {type(stmt).__name__}"]


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

    # --- Generate blocks (unsupported) ---
    for gen in module.generate_blocks:
        lines.append(f"# UNSUPPORTED: generate block ({type(gen).__name__})")

    # --- Specify blocks (unsupported) ---
    for _spec in module.specify_blocks:
        lines.append("# UNSUPPORTED: specify block")

    # --- Build ---
    lines.append(f"module = {v}.build()")
    lines.append("")

    return "\n".join(lines)


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

    if port.direction == PortDirection.INPUT:
        lines.append(f'{name} = {v}.input("{name}"{width_str}{extras})')
    elif port.direction == PortDirection.OUTPUT:
        if port.data_type == "reg":
            lines.append(f'{name} = {v}.output_reg("{name}"{width_str}{extras})')
        else:
            lines.append(f'{name} = {v}.output("{name}"{width_str}{extras})')
    elif port.direction == PortDirection.INOUT:
        lines.append(f'{name} = {v}.inout("{name}"{width_str}{extras})')


def _emit_net(lines: list[str], net: Net, v: str) -> None:
    """Emit an internal net declaration."""
    name = net.name
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

    lines.append(f'{name} = {v}.wire("{name}"{width_str}{extras})')


def _emit_variable(lines: list[str], var: Variable, v: str) -> None:
    """Emit an internal variable (reg, integer) declaration."""
    name = var.name
    if var.kind == VariableKind.INTEGER:
        lines.append(f'{name} = {v}.integer("{name}")')
        return
    if var.kind in (VariableKind.REAL, VariableKind.REALTIME, VariableKind.TIME, VariableKind.EVENT):
        lines.append(f"# UNSUPPORTED: {var.kind.value} {name}")
        return

    # REG
    width = _width_arg(var.width)
    extras = _signed_kwarg(var.signed)

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

    body_lines = _stmt_to_lines(blk.body, depth=1)
    if body_lines:
        lines.extend(body_lines)
    else:
        lines.append(f"{_INDENT}pass")


def _emit_initial(lines: list[str], blk: InitialBlock, v: str) -> None:
    """Emit an initial block."""
    lines.append(f"with {v}.initial():")
    body_lines = _stmt_to_lines(blk.body, depth=1)
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
