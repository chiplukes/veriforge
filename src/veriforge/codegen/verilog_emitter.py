"""Emit Verilog source code from semantic model objects.

This module provides `emit_design()` and `emit_module()` which convert
model objects back into syntactically valid Verilog source text.

Phase 1: structural elements (module, ports, parameters, nets, variables, comments).
Phase 2: instances, continuous assigns.
Phase 3: behavioral (always/initial blocks, statements).
Phase 5: functions, tasks, generate constructs, specify blocks.
"""

from __future__ import annotations

import copy
import itertools
import random
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from ..model.assignments import ContinuousAssign
from ..model.base import Comment, VerilogNode
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
    WidthCast,
    StreamingConcatenation,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from ..model.functions import FunctionDecl, TaskDecl
from ..model.generate import (
    GenerateBlock,
    GenerateCase,
    GenerateCaseItem,
    GenerateFor,
    GenerateIf,
    GenvarDecl,
)
from ..model.instances import Instance, ParameterBinding, PortConnection
from ..model.specify import SpecifyBlock
from ..model.sv_types import EnumType, StructType, TypedefDecl, UnionType
from ..model.interface import Interface, Modport
from ..model.package import ImportDecl, Package
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.statements import (
    BlockingAssign,
    CaseItem,
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
    SensitivityEdge,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from ..model.variables import INTEGER_ATOM_WIDTHS, SIGNED_KINDS, Variable, VariableKind


@dataclass
class EmitStyle:
    """Alternative spellings of the same model, for fuzzing the parser
    (`veriforge.fuzz`: emit both ways, the parsed results must agree). The
    canonical emitter only ever prints one spelling of each construct, so a
    form like a function's old-style scalar `input j;` -- which the parser
    took for a local variable -- was never exercised."""

    old_style_ports: bool = False  # module/function/task ports declared in the body
    star_paren: bool = True  # `@(*)` (else `@*`)
    sensitivity_comma: bool = False  # `@(a, b)` (else `@(a or b)`)
    extra_parens: bool = False  # parenthesize every binary/ternary operand
    literal_rng: random.Random | None = None  # re-spell literals (base, `_`, case)
    positional_connections: bool = False  # instance ports/parameters by position (needs linked instances)
    else_begin_if: bool = False  # `else begin if ... end` (else `else if`)
    named_blocks: bool = False  # label every unnamed `begin` (`begin : vfbN`)
    generate_region: bool = False  # wrap a module's generate constructs in `generate`/`endgenerate`
    net_decl_assign: bool = False  # `wire w = e;` for a net's only `assign w = e;`
    for_var_outside: bool = False  # `begin int i; for (i = 0; ...) ... end` (else `for (int i = 0; ...)`)
    case_rng: random.Random | None = None  # merge equal-bodied case items, add unreachable values
    split_case_items: bool = False  # `a: s; b: s;` for `a, b: s;` (before any case_rng merging)
    genvar_outside: bool = False  # `genvar g; for (g = 0; ...)` (else `for (genvar g = 0; ...)`)
    _block_ids: Iterator[int] = field(default_factory=itertools.count, repr=False)


_STYLE: ContextVar[EmitStyle | None] = ContextVar("verilog_emit_style", default=None)
_CANONICAL = EmitStyle()


def _style() -> EmitStyle:
    return _STYLE.get() or _CANONICAL


@contextmanager
def emit_style(style: EmitStyle) -> Iterator[None]:
    """Emit with *style* inside the ``with`` block."""
    token = _STYLE.set(style)
    try:
        yield
    finally:
        _STYLE.reset(token)


def emit_design(design: Design, *, indent: str = "    ", emit_comments: bool = True) -> str:
    """Emit a full Design as Verilog source text.

    Args:
        design: The Design model to emit.
        indent: Indentation string (default 4 spaces).
        emit_comments: Whether to include comments in output.

    Returns:
        Complete Verilog source text.
    """
    parts: list[str] = []
    for i, module in enumerate(design.modules):
        if i > 0:
            parts.append("")  # blank line between modules
        parts.append(emit_module(module, indent=indent, emit_comments=emit_comments))
    for iface in design.interfaces:
        if parts:
            parts.append("")
        parts.append(emit_interface(iface, indent=indent))
    for pkg in design.packages:
        if parts:
            parts.append("")
        parts.append(emit_package(pkg, indent=indent))
    return "\n".join(parts) + "\n"


def emit_module(module: Module, *, indent: str = "    ", emit_comments: bool = True) -> str:  # noqa: PLR0912, PLR0915  # cm:b7a6d5
    """Emit a Module as Verilog source text.

    Args:
        module: The Module model to emit.
        indent: Indentation string (default 4 spaces).
        emit_comments: Whether to include comments in output.

    Returns:
        Verilog module declaration text.
    """
    lines: list[str] = []

    # Leading comments on the module itself
    if emit_comments:
        lines.extend(_emit_leading_comments(module))

    # Module header
    header = f"module {module.name}"

    # Parameter port list
    param_ports = [p for p in module.parameters if not p.is_local]
    if param_ports:
        header += " #("
        param_strs = [_emit_parameter_port(p) for p in param_ports]
        if len(param_strs) == 1:
            header += param_strs[0] + ")"
        else:
            header += "\n"
            for i, ps in enumerate(param_strs):
                sep = "," if i < len(param_strs) - 1 else ""
                header += f"{indent}{ps}{sep}\n"
            header += ")"

    # Port list
    old_style = _style().old_style_ports and module.ports and not any(p.default_value for p in module.ports)
    if old_style:
        header += " (" + ", ".join(p.name for p in module.ports) + ");"
    elif module.ports:
        header += " ("
        port_strs = [_emit_port(p) for p in module.ports]
        has_port_extras = emit_comments and any(p.comments for p in module.ports)
        has_port_attrs = any(p.attributes for p in module.ports)
        if _ports_fit_one_line(port_strs) and not has_port_extras and not has_port_attrs:
            header += ", ".join(port_strs) + ");"
        else:
            header += "\n"
            for i, port in enumerate(module.ports):
                if emit_comments:
                    for lc in _emit_leading_comments(port, indent):
                        header += lc + "\n"
                for attr_line in _emit_attributes(port, indent):
                    header += attr_line + "\n"
                ps = _emit_port(port)
                sep = "," if i < len(module.ports) - 1 else ""
                line = f"{indent}{ps}{sep}"
                if emit_comments:
                    trailing = _get_trailing_comment_text(port)
                    if trailing:
                        line += f"  {trailing}"
                header += line + "\n"
            header += ");"
    else:
        header += ";"

    lines.append(header)

    # Body declarations
    body_items: list[str] = []
    if old_style:
        body_items.extend(f"{indent}{_emit_port(p)};" for p in module.ports)

    # Localparams (from parameter list with is_local=True)
    for p in module.parameters:
        if p.is_local:
            if emit_comments:
                body_items.extend(_emit_leading_comments(p, indent))
            item = _emit_localparam(p, indent)
            if emit_comments:
                trailing = _get_trailing_comment_text(p)
                if trailing:
                    item += f"  {trailing}"
            body_items.append(item)

    # Imports
    for imp in module.imports:
        body_items.append(_emit_import(imp, indent))

    # Typedefs
    for td in module.typedefs:
        body_items.append(_emit_typedef(td, indent))

    # Nets
    folded = _net_decl_assigns(module) if _style().net_decl_assign else {}
    for n in module.nets:
        if n.name in folded:
            continue  # (declared where its assign was)
        if emit_comments:
            body_items.extend(_emit_leading_comments(n, indent))
        body_items.extend(_emit_attributes(n, indent))
        item = _emit_net(n, indent)
        if emit_comments:
            trailing = _get_trailing_comment_text(n)
            if trailing:
                item += f"  {trailing}"
        body_items.append(item)

    # Variables — skip those already declared as ``output reg`` ports
    _port_reg_names = {p.name for p in module.ports if p.data_type == "reg" and p.direction == PortDirection.OUTPUT}
    for v in module.variables:
        if v.name in _port_reg_names:
            continue
        if emit_comments:
            body_items.extend(_emit_leading_comments(v, indent))
        body_items.extend(_emit_attributes(v, indent))
        item = _emit_variable(v, indent)
        if emit_comments:
            trailing = _get_trailing_comment_text(v)
            if trailing:
                item += f"  {trailing}"
        body_items.append(item)

    # Continuous assigns
    folded_ids = {id(ca): net for net, ca in folded.values()}
    for ca in module.continuous_assigns:
        if id(ca) in folded_ids:
            body_items.append(_emit_net(folded_ids[id(ca)], indent, ca.rhs))
            continue
        if emit_comments:
            body_items.extend(_emit_leading_comments(ca, indent))
        item = _emit_continuous_assign(ca, indent)
        if emit_comments:
            trailing = _get_trailing_comment_text(ca)
            if trailing:
                item += f"  {trailing}"
        body_items.append(item)

    # Instances
    for inst in module.instances:
        if emit_comments:
            body_items.extend(_emit_leading_comments(inst, indent))
        item = _emit_instance(inst, indent)
        if emit_comments:
            trailing = _get_trailing_comment_text(inst)
            if trailing:
                item += f"  {trailing}"
        body_items.append(item)

    # Always blocks
    for ab in module.always_blocks:
        if emit_comments:
            body_items.extend(_emit_leading_comments(ab, indent))
        body_items.append("")  # blank line before always
        body_items.append(_emit_always_block(ab, indent, indent))

    # Initial blocks
    for ib in module.initial_blocks:
        if emit_comments:
            body_items.extend(_emit_leading_comments(ib, indent))
        body_items.append("")  # blank line before initial
        body_items.append(_emit_initial_block(ib, indent, indent))

    # Functions
    for fn in module.functions:
        if emit_comments:
            body_items.extend(_emit_leading_comments(fn, indent))
        body_items.append("")  # blank line before function
        body_items.append(_emit_function_decl(fn, indent, indent))

    # Tasks
    for tk in module.tasks:
        if emit_comments:
            body_items.extend(_emit_leading_comments(tk, indent))
        body_items.append("")  # blank line before task
        body_items.append(_emit_task_decl(tk, indent, indent))

    # Generate constructs
    region = _style().generate_region and module.generate_blocks
    if region:
        body_items.append("")
        body_items.append(f"{indent}generate")
    module_names = {p.name for p in module.ports} | {p.name for p in module.parameters}
    module_names |= {n.name for n in module.nets} | {v.name for v in module.variables}
    genvars = _GenvarScope(module.generate_blocks, module_names)
    for gb in module.generate_blocks:
        if emit_comments:
            body_items.extend(_emit_leading_comments(gb, indent))
        body_items.append("")  # blank line before generate
        body_items.extend(genvars.declare(gb, indent))
        body_items.append(_emit_generate_construct(gb, indent, indent, hoisted=genvars.hoisted))
    if region:
        body_items.append(f"{indent}endgenerate")

    # Specify blocks
    for sb in module.specify_blocks:
        if emit_comments:
            body_items.extend(_emit_leading_comments(sb, indent))
        body_items.append("")  # blank line before specify
        body_items.append(_emit_specify_block(sb, indent))

    if body_items:
        lines.append("")
        lines.extend(body_items)

    # Endmodule
    lines.append("")
    lines.append("endmodule")

    return "\n".join(lines)


_MAX_INLINE_PORTS = 3
_MAX_PORT_STR_LEN = 30


def _ports_fit_one_line(port_strs: list[str]) -> bool:
    """Check if ports can be emitted on a single line."""
    return len(port_strs) <= _MAX_INLINE_PORTS and all(len(s) < _MAX_PORT_STR_LEN for s in port_strs)


# ---- Comment helpers ----


def _emit_comment(comment: Comment) -> str:
    """Emit a single comment as Verilog text."""
    if comment.kind == "block":
        return f"/* {comment.text} */"
    return f"// {comment.text}"


def _emit_leading_comments(node: VerilogNode, prefix: str = "") -> list[str]:
    """Emit leading comments for a node as separate lines."""
    result: list[str] = []
    for c in node.comments:
        if c.position == "leading":
            result.append(f"{prefix}{_emit_comment(c)}")
    return result


def _get_trailing_comment_text(node: VerilogNode) -> str | None:
    """Get the first trailing comment text for a node, or None."""
    for c in node.comments:
        if c.position == "trailing":
            return _emit_comment(c)
    return None


def _emit_attributes(node: VerilogNode, prefix: str = "") -> list[str]:
    """Emit Verilog attributes ``(* key = "val" *)`` for a node."""
    if not node.attributes:
        return []
    parts: list[str] = []
    for k, v in node.attributes.items():
        if v is not None:
            parts.append(f'{k} = "{v}"')
        else:
            parts.append(k)
    return [f"{prefix}(* {', '.join(parts)} *)"]


def emit_interface(iface: Interface, *, indent: str = "    ") -> str:  # noqa: PLR0912
    """Emit an Interface as Verilog source text."""
    lines: list[str] = []

    # Header
    header = f"interface {iface.name}"

    # Parameters
    param_ports = [p for p in iface.parameters if not p.is_local]
    if param_ports:
        header += " #("
        param_strs = [_emit_parameter_port(p) for p in param_ports]
        if len(param_strs) == 1:
            header += param_strs[0] + ")"
        else:
            header += "\n"
            for i, ps in enumerate(param_strs):
                sep = "," if i < len(param_strs) - 1 else ""
                header += f"{indent}{ps}{sep}\n"
            header += ")"

    header += ";"
    lines.append(header)

    body: list[str] = []

    # Localparams
    for p in iface.parameters:
        if p.is_local:
            body.append(_emit_localparam(p, indent))

    # Typedefs
    for td in iface.typedefs:
        body.append(_emit_typedef(td, indent))

    # Imports
    for imp in iface.imports:
        body.append(_emit_import(imp, indent))

    # Nets
    for n in iface.nets:
        body.append(_emit_net(n, indent))

    # Variables
    for v in iface.variables:
        body.append(_emit_variable(v, indent))

    # Continuous assigns
    for ca in iface.continuous_assigns:
        body.append(_emit_continuous_assign(ca, indent))

    # Modports
    for mp in iface.modports:
        body.append(_emit_modport(mp, indent))

    if body:
        lines.append("")
        lines.extend(body)

    lines.append("")
    lines.append("endinterface")
    return "\n".join(lines)


def _emit_modport(mp: Modport, indent: str) -> str:
    """Emit a modport declaration."""
    port_strs = [f"{p.direction.value} {p.name}" for p in mp.ports]
    return f"{indent}modport {mp.name}({', '.join(port_strs)});"


def emit_package(pkg: Package, *, indent: str = "    ") -> str:
    """Emit a Package as Verilog source text."""
    lines: list[str] = []
    lines.append(f"package {pkg.name};")

    body: list[str] = []

    # Parameters (non-local first, then localparams)
    for p in pkg.parameters:
        if not p.is_local:
            body.append(_emit_parameter_body(p, indent))

    for p in pkg.parameters:
        if p.is_local:
            body.append(_emit_localparam(p, indent))

    # Imports within the package
    for imp in pkg.imports:
        body.append(_emit_import(imp, indent))

    # Typedefs
    for td in pkg.typedefs:
        body.append(_emit_typedef(td, indent))

    # Functions
    for fn in pkg.functions:
        body.append("")
        body.append(_emit_function_decl(fn, indent, indent))

    # Tasks
    for tk in pkg.tasks:
        body.append("")
        body.append(_emit_task_decl(tk, indent, indent))

    if body:
        lines.append("")
        lines.extend(body)

    lines.append("")
    lines.append("endpackage")
    return "\n".join(lines)


def _emit_import(imp: ImportDecl, indent: str) -> str:
    """Emit an import declaration."""
    return f"{indent}import {imp.package_name}::{imp.item_name};"


def _emit_parameter_body(param: Parameter, indent: str) -> str:
    """Emit a parameter declaration in a body (not port list)."""
    parts = [f"{indent}parameter"]
    if param.param_type:
        parts.append(param.param_type)
    if param.signed:
        parts.append("signed")
    if param.width:
        parts.append(_emit_range(param.width))
    parts.append(param.name)
    if param.default_value:
        parts.append("=")
        parts.append(emit_expression(param.default_value))
    return " ".join(parts) + ";"


def _emit_parameter_port(param: Parameter) -> str:
    """Emit a parameter in a parameter port list."""
    parts = ["parameter"]
    if param.param_type:
        parts.append(param.param_type)
    if param.signed:
        parts.append("signed")
    if param.width:
        parts.append(_emit_range(param.width))
    parts.append(param.name)
    if param.default_value:
        parts.append("=")
        parts.append(emit_expression(param.default_value))
    return " ".join(parts)


def _emit_localparam(param: Parameter, indent: str) -> str:
    """Emit a localparam declaration as a body statement."""
    parts = [f"{indent}localparam"]
    if param.param_type:
        parts.append(param.param_type)
    if param.signed:
        parts.append("signed")
    if param.width:
        parts.append(_emit_range(param.width))
    parts.append(param.name)
    if param.default_value:
        parts.append("=")
        parts.append(emit_expression(param.default_value))
    return " ".join(parts) + ";"


def _emit_typedef(td: TypedefDecl, indent: str) -> str:
    """Emit a typedef declaration."""
    if td.enum_type is not None:
        return f"{indent}typedef {_emit_enum_type(td.enum_type)} {td.name};"
    if td.struct_type is not None:
        return f"{indent}typedef {_emit_struct_or_union_type(td.struct_type, 'struct', indent)} {td.name};"
    if td.union_type is not None:
        return f"{indent}typedef {_emit_struct_or_union_type(td.union_type, 'union', indent)} {td.name};"
    if td.type_ref is not None:
        return f"{indent}typedef {td.type_ref} {td.name};"
    return f"{indent}typedef {td.name};"


def _emit_struct_or_union_type(st: StructType | UnionType, keyword: str, indent: str) -> str:
    """Emit a struct or union type specifier."""
    parts = [keyword]
    if st.packed:
        parts.append("packed")
    if st.signed:
        parts.append("signed")

    inner_indent = indent + "    "
    field_lines: list[str] = []
    for f in st.fields:
        field_parts = [f.data_type]
        if f.signed:
            field_parts.append("signed")
        if f.width:
            field_parts.append(_emit_range(f.width))
        field_parts.append(f.name)
        field_lines.append(f"{inner_indent}{' '.join(field_parts)};")

    header = " ".join(parts) + " {"
    return header + "\n" + "\n".join(field_lines) + "\n" + indent + "}"


def _emit_enum_type(et: EnumType) -> str:
    """Emit an enum type specifier."""
    parts = ["enum"]

    # Base type and optional width
    if et.base_type:
        bt = et.base_type
        if et.signed:
            bt += " signed"
        if et.width:
            bt += " " + _emit_range(et.width)
        parts.append(bt)
    elif et.signed:
        parts.append("signed")

    # Members
    member_strs: list[str] = []
    for m in et.members:
        if m.value is not None:
            member_strs.append(f"{m.name} = {emit_expression(m.value)}")
        else:
            member_strs.append(m.name)
    parts.append("{" + ", ".join(member_strs) + "}")

    return " ".join(parts)


def _emit_port(port: Port) -> str:
    """Emit a port declaration."""
    parts = [port.direction.value]
    if port.net_type:
        parts.append(port.net_type)
    if port.data_type:
        parts.append(port.data_type)
    if port.data_type in INTEGER_ATOM_WIDTHS:
        # `byte`/`int`/...: fixed width, signed unless `unsigned`.
        if not port.signed:
            parts.append("unsigned")
    elif port.data_type == "time":
        if port.signed:
            parts.append("signed")  # (`time`: 64 bits, unsigned by default; no range)
    else:
        if port.signed:
            parts.append("signed")
        if port.width:
            parts.append(_emit_range(port.width))
    parts.append(port.name)
    if port.default_value:
        parts.append("=")
        parts.append(emit_expression(port.default_value))
    return " ".join(parts)


def _net_decl_assigns(module: Module) -> dict[str, tuple[Net, ContinuousAssign]]:
    """The nets declared with a declaration assignment instead of their
    `assign w = e;` (`EmitStyle.net_decl_assign`): the net's only continuous
    assign, whose RHS reads only names declared before it -- the declaration
    goes where the assign was, after the other declarations (Icarus rejects
    a declaration assignment reading a later-declared reg)."""
    nets = {n.name: n for n in module.nets if not n.dimensions and n.initial_value is None}
    counts = Counter(ca.lhs.name for ca in module.continuous_assigns if isinstance(ca.lhs, Identifier))
    candidates = {
        ca.lhs.name
        for ca in module.continuous_assigns
        if isinstance(ca.lhs, Identifier) and not ca.lhs.hierarchy and ca.lhs.name in nets and counts[ca.lhs.name] == 1
    }
    declared = {p.name for p in module.ports} | {p.name for p in module.parameters}
    declared |= {v.name for v in module.variables} | {n.name for n in module.nets if n.name not in candidates}
    folded: dict[str, tuple[Net, ContinuousAssign]] = {}
    for ca in module.continuous_assigns:
        if not isinstance(ca.lhs, Identifier) or ca.lhs.name not in candidates:
            continue
        ids = [node for node in ca.rhs.walk() if isinstance(node, Identifier)]
        if all(not i.hierarchy and i.name in declared for i in ids):
            folded[ca.lhs.name] = (nets[ca.lhs.name], ca)
            declared.add(ca.lhs.name)
    return folded


def _emit_net(net: Net, indent: str, assign_rhs: Expression | None = None) -> str:
    """Emit a net declaration (with *assign_rhs* as a declaration assignment)."""
    parts = [f"{indent}{net.kind.value}"]
    if net.signed:
        parts.append("signed")
    if net.width:
        parts.append(_emit_range(net.width))
    parts.append(net.name)
    if net.dimensions:
        for dim in net.dimensions:
            parts.append(_emit_range(dim))
    if assign_rhs is not None:
        parts.append("=")
        parts.append(emit_expression(assign_rhs))
    elif net.initial_value and not net.dimensions:
        # Array/memory nets can't take an inline initializer in Verilog --
        # only scalar/vector nets can. Any runtime default for an array is
        # established elsewhere (e.g. synchronous reset logic); silently
        # drop it here rather than emit illegal `wire ... [0:N-1] = 0;`.
        parts.append("=")
        parts.append(emit_expression(net.initial_value))
    return " ".join(parts) + ";"


def _emit_variable(var: Variable, indent: str) -> str:
    """Emit a variable declaration."""
    if var.type_name:
        parts = [f"{indent}{var.type_name}"]
    else:
        parts = [f"{indent}{var.kind.value}"]
        # Only where it differs from the kind's default (`integer` is signed).
        if var.signed and var.kind not in SIGNED_KINDS:
            parts.append("signed")
        elif not var.signed and var.kind in SIGNED_KINDS:
            parts.append("unsigned")
        if var.width:
            parts.append(_emit_range(var.width))
    parts.append(var.name)
    if var.dimensions:
        for dim in var.dimensions:
            parts.append(_emit_range(dim))
    if var.initial_value and not var.dimensions:
        # Array/memory regs can't take an inline initializer in Verilog --
        # only scalar/vector regs can. Any runtime default for an array is
        # established elsewhere (e.g. synchronous reset logic); silently
        # drop it here rather than emit illegal `reg ... [0:N-1] = 0;`.
        parts.append("=")
        parts.append(emit_expression(var.initial_value))
    return " ".join(parts) + ";"


def _emit_range(r: Range) -> str:
    """Emit a range expression [msb:lsb]."""
    return f"[{emit_expression(r.msb)}:{emit_expression(r.lsb)}]"


def _emit_continuous_assign(ca: ContinuousAssign, indent: str) -> str:
    """Emit a continuous assignment: assign lhs = rhs;"""
    return f"{indent}assign {emit_expression(ca.lhs)} = {emit_expression(ca.rhs)};"


def _emit_instance(inst: Instance, indent: str) -> str:
    """Emit a module instantiation statement.

    Examples:
        counter u1 (.clk(clk), .rst(rst));
        counter #(.WIDTH(8)) u1 (.clk(clk));
        counter #(8) u1 (clk, rst);
    """
    parts = [f"{indent}{inst.module_name}"]

    positional = _style().positional_connections and _positional_order(inst) is not None
    child = inst.resolved_module
    # Parameter overrides: #(...)
    if inst.has_parameter_override:
        parts.append(" #(")
        param_order = [p.name for p in child.parameters if not p.is_local] if positional and child is not None else None
        bindings = {b.name: b for b in inst.parameter_bindings}
        if param_order is not None and all(b.name in param_order for b in inst.parameter_bindings):
            # Positional: every parameter up to the last overridden one, in
            # declaration order (a gap can't be skipped).
            last = max(param_order.index(b.name) for b in inst.parameter_bindings if b.name is not None)
            values = []
            for name in param_order[: last + 1]:
                binding = bindings.get(name)
                if binding is not None and binding.value is not None:
                    values.append(emit_expression(binding.value))
                    continue
                assert child is not None  # (param_order came from it)
                default = next(p for p in child.parameters if p.name == name).default_value
                values.append(emit_expression(default) if default is not None else "")
            parts.append(", ".join(values))
        else:
            parts.append(_emit_parameter_bindings(inst.parameter_bindings))
        parts.append(")")

    # Instance name and optional array range
    parts.append(f" {inst.instance_name}")
    if inst.instance_array:
        parts.append(f" {_emit_range(inst.instance_array)}")

    # Port connections: (...)
    parts.append(" (")
    order = _positional_order(inst) if positional else None
    if order is not None:
        by_name = {c.port_name: c.expression for c in inst.port_connections}
        parts.append(", ".join("" if (e := by_name.get(name)) is None else emit_expression(e) for name in order))
    else:
        parts.append(_emit_port_connections(inst.port_connections))
    parts.append(");")

    return "".join(parts)


def _positional_order(inst: Instance) -> list[str] | None:
    """The child's port order, when every connection is named and resolvable."""
    child = inst.resolved_module
    if child is None or not inst.port_connections or not all(c.is_named for c in inst.port_connections):
        return None
    order = [p.name for p in child.ports]
    if not all(c.port_name in order for c in inst.port_connections):
        return None
    return order


def _emit_parameter_bindings(bindings: list[ParameterBinding]) -> str:
    """Emit parameter binding list contents."""
    strs: list[str] = []
    for b in bindings:
        if b.name is not None:
            val = emit_expression(b.value) if b.value is not None else ""
            strs.append(f".{b.name}({val})")
        else:
            strs.append(emit_expression(b.value) if b.value is not None else "")
    return ", ".join(strs)


def _emit_port_connections(connections: list[PortConnection]) -> str:
    """Emit port connection list contents."""
    strs: list[str] = []
    for c in connections:
        if c.is_named:
            expr = emit_expression(c.expression) if c.expression is not None else ""
            strs.append(f".{c.port_name}({expr})")
        else:
            strs.append(emit_expression(c.expression) if c.expression is not None else "")
    return ", ".join(strs)


# Verilog operator precedence (IEEE 1800-2012 §11.3.2). Higher value = tighter binding.
# Unary operators have the highest precedence (not listed; they never need parens as parents).
_BINARY_PREC: dict[str, int] = {
    "**": 12,
    "*": 10,
    "/": 10,
    "%": 10,
    "+": 9,
    "-": 9,
    "<<": 8,
    ">>": 8,
    "<<<": 8,
    ">>>": 8,
    "<": 7,
    "<=": 7,
    ">": 7,
    ">=": 7,
    "==": 6,
    "!=": 6,
    "===": 6,
    "!==": 6,
    "&": 5,
    "^": 4,
    "~^": 4,
    "^~": 4,
    "|": 3,
    "&&": 2,
    "||": 1,
}


def _prec(op: str) -> int:
    """Return Verilog precedence for a binary operator (higher = tighter)."""
    return _BINARY_PREC.get(op, 0)


def emit_expression(expr: Expression) -> str:  # noqa: PLR0911, PLR0912
    """Emit an Expression as Verilog text."""
    if isinstance(expr, Literal):
        return _emit_literal(expr)
    if isinstance(expr, Identifier):
        if expr.hierarchy:
            return ".".join(expr.hierarchy) + "." + expr.name
        return expr.name
    if isinstance(expr, UnaryOp):
        operand_str = emit_expression(expr.operand)
        # BinaryOp/TernaryOp operands need parens: ~(a & b) not ~a & b.
        # Nested UnaryOp operands need parens because Verilog grammar
        # requires unary_operator → primary; a UnaryOp expression tree
        # like ~~x is not a primary, so we emit ~(~x). Concatenation/
        # Replication/FunctionCall/etc. operands are already valid
        # primaries regardless of which unary operator precedes them, so
        # they never need extra parens (confirmed against both this
        # project's grammar and Icarus Verilog: `~&{a,b}`, `~|{2{a}}`,
        # and `^foo(a)` all parse fine unparenthesized).
        # A negative literal (the DSL's `lnot(-255)`) is no primary either:
        # `!-255` didn't parse.
        if isinstance(expr.operand, (BinaryOp, TernaryOp, UnaryOp)) or operand_str.startswith("-"):
            operand_str = f"({operand_str})"
        return f"{expr.op}{operand_str}"
    if isinstance(expr, BinaryOp):
        this_prec = _prec(expr.op)
        left_str = emit_expression(expr.left)
        right_str = emit_expression(expr.right)
        # Left operand: parenthesize only if it has strictly lower precedence.
        # Equal-precedence on the left is fine for left-associative operators.
        extra = _style().extra_parens
        if isinstance(expr.left, TernaryOp) or (
            isinstance(expr.left, BinaryOp) and (extra or _prec(expr.left.op) < this_prec)
        ):
            left_str = f"({left_str})"
        # Right operand: parenthesize if equal or lower precedence to handle
        # non-associativity (a - (b - c) differs from a - b - c).
        if isinstance(expr.right, TernaryOp) or (
            isinstance(expr.right, BinaryOp) and (extra or _prec(expr.right.op) <= this_prec)
        ):
            right_str = f"({right_str})"
        return f"{left_str} {expr.op} {right_str}"
    if isinstance(expr, TernaryOp):
        cond_str = emit_expression(expr.condition)
        # Nested ternary as condition is ambiguous (right-associative in Verilog).
        if isinstance(expr.condition, TernaryOp) or (_style().extra_parens and isinstance(expr.condition, BinaryOp)):
            cond_str = f"({cond_str})"
        true_str, false_str = emit_expression(expr.true_expr), emit_expression(expr.false_expr)
        if _style().extra_parens:
            true_str, false_str = f"({true_str})", f"({false_str})"
        return f"{cond_str} ? {true_str} : {false_str}"
    if isinstance(expr, Concatenation):
        parts = ", ".join(emit_expression(p) for p in expr.parts)
        if expr.from_streaming:
            # (`{>>{a, b}}` as written: as an assignment target it isn't
            # `{a, b}`, which aligns a wider source differently.)
            return "{>>{" + parts + "}}"
        return "{" + parts + "}"
    if isinstance(expr, WidthCast):
        return f"{emit_expression(expr.width)}'({emit_expression(expr.operand)})"
    if isinstance(expr, Replication):
        # When the value is a Concatenation, emit its parts directly inside the
        # replication braces to avoid double-wrapping: {4{a, b}} not {4{{a, b}}}
        if isinstance(expr.value, Concatenation):
            inner = ", ".join(emit_expression(p) for p in expr.value.parts)
        else:
            inner = emit_expression(expr.value)
        return "{" + emit_expression(expr.count) + "{" + inner + "}}"
    if isinstance(expr, StreamingConcatenation):
        # This node only ever represents `<<` (left-stream) -- the `>>`
        # form is desugared to plain `Concatenation` at AST-build time (see
        # the node's own docstring in model/expressions.py), so `<<` is the
        # only direction this emitter ever needs to produce.
        slice_str = emit_expression(expr.slice_size) if expr.slice_size is not None else ""
        parts = ", ".join(emit_expression(p) for p in expr.parts)
        return "{<<" + slice_str + "{" + parts + "}}"
    if isinstance(expr, BitSelect):
        return f"{emit_expression(expr.target)}[{emit_expression(expr.index)}]"
    if isinstance(expr, RangeSelect):
        return f"{emit_expression(expr.target)}[{emit_expression(expr.msb)}:{emit_expression(expr.lsb)}]"
    if isinstance(expr, PartSelect):
        base = emit_expression(expr.base)
        width = emit_expression(expr.width)
        return f"{emit_expression(expr.target)}[{base} {expr.direction} {width}]"
    if isinstance(expr, FunctionCall):
        args = ", ".join(emit_expression(a) for a in expr.arguments)
        return f"{expr.name}({args})"
    if isinstance(expr, StringLiteral):
        return f'"{expr.value}"'
    if isinstance(expr, Mintypmax):
        return f"{emit_expression(expr.min_val)}:{emit_expression(expr.typ_val)}:{emit_expression(expr.max_val)}"

    return "/* unknown expression */"


_SIZED_LITERAL = re.compile(r"^(\d+)'([sS]?)([bBoOdDhH])([0-9a-fA-FxXzZ?_]+)$")
_BASE_DIGITS = {"b": 2, "o": 8, "d": 10, "h": 16}


def _respell_literal(text: str, rng: random.Random) -> str:
    """The same literal value, spelled differently: another base for a
    known sized value, `_` separators, upper/lower case."""
    match = _SIZED_LITERAL.match(text)
    if match is None:
        return text  # (an unsized decimal may be a cast's width: `12'(x)`)
    size, sign, base, digits = match.groups()
    clean = digits.replace("_", "")
    if not any(c in "xXzZ?" for c in clean) and rng.random() < 0.6:
        value = int(clean, _BASE_DIGITS[base.lower()])
        if value < 1 << int(size):
            base = rng.choice("bodh")
            clean = {"b": f"{value:b}", "o": f"{value:o}", "d": f"{value}", "h": f"{value:x}"}[base]
    if rng.random() < 0.5:
        base = base.upper()
        clean = clean.upper()
    if len(clean) > 2 and rng.random() < 0.5:
        cut = rng.randrange(1, len(clean))
        clean = f"{clean[:cut]}_{clean[cut:]}"
    return f"{size}'{sign}{base}{clean}"


def _emit_literal(lit: Literal) -> str:  # noqa: PLR0911
    """Emit a Literal as Verilog text."""
    # Prefer original text if available (preserves formatting)
    if lit.original_text:
        rng = _style().literal_rng
        return _respell_literal(lit.original_text, rng) if rng is not None else lit.original_text

    # Reconstruct from components
    if lit.width is not None and lit.base:
        prefix = f"{lit.width}'"
        if lit.signed:
            prefix += "s"
        prefix += lit.base
        if isinstance(lit.value, int):
            if lit.base == "h":
                return f"{prefix}{lit.value:x}"
            elif lit.base == "b":
                return f"{prefix}{lit.value:b}"
            elif lit.base == "o":
                return f"{prefix}{lit.value:o}"
            else:
                return f"{prefix}{lit.value}"
        return f"{prefix}{lit.value}"

    return str(lit.value)


# ---- Behavioral emission (Phase 3) ----


def _emit_always_block(ab: AlwaysBlock, indent: str, current_indent: str) -> str:
    """Emit an always block with sensitivity list."""
    keyword = getattr(ab, "keyword", "always")
    result = f"{current_indent}{keyword} "

    # Sensitivity list
    if ab.sensitivity_list:
        result += "@(" + _emit_sensitivity_list(ab.sensitivity_list) + ") "
    elif ab.sensitivity_type == SensitivityType.COMBINATIONAL and keyword == "always":
        result += "@(*) " if _style().star_paren else "@* "
    # else: no sensitivity (`always_comb`/`always_latch`, or a bare
    # `always begin ... end`). (An `always_latch` used to come out as a bare
    # `always`, which never waits.)

    # Body
    result += _emit_statement_inline(ab.body, indent, current_indent)
    return result


def _emit_initial_block(ib: InitialBlock, indent: str, current_indent: str) -> str:
    """Emit an initial block."""
    result = f"{current_indent}initial "
    result += _emit_statement_inline(ib.body, indent, current_indent)
    return result


def _emit_sensitivity_list(edges: list[SensitivityEdge]) -> str:
    """Emit a sensitivity list: posedge clk or negedge rst or a or b"""
    parts: list[str] = []
    for edge in edges:
        if edge.edge == "level":
            parts.append(emit_expression(edge.signal))
        else:
            parts.append(f"{edge.edge} {emit_expression(edge.signal)}")
    return (", " if _style().sensitivity_comma else " or ").join(parts)


def _emit_statement_inline(stmt: Statement, indent: str, current_indent: str) -> str:
    """Emit a statement inline (without leading indentation)."""
    return _emit_statement(stmt, indent, current_indent, inline=True)


def _emit_statement(  # noqa: PLR0911, PLR0912
    stmt: Statement,
    indent: str,
    current_indent: str,
    *,
    inline: bool = False,
) -> str:
    """Emit a statement with proper indentation.

    Args:
        stmt: The statement to emit.
        indent: The base indent string (e.g., "    ").
        current_indent: The current indentation level.
        inline: If True, don't prepend current_indent.
    """
    prefix = "" if inline else current_indent

    if isinstance(stmt, SeqBlock):
        return _emit_seq_block(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, ParBlock):
        return _emit_par_block(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, BlockingAssign):
        return f"{prefix}{emit_expression(stmt.lhs)} = {emit_expression(stmt.rhs)};"
    elif isinstance(stmt, NonblockingAssign):
        return f"{prefix}{emit_expression(stmt.lhs)} <= {emit_expression(stmt.rhs)};"
    elif isinstance(stmt, IfStatement):
        return _emit_if_statement(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, CaseStatement):
        return _emit_case_statement(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, ForLoop):
        return _emit_for_loop(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, WhileLoop):
        return _emit_while_loop(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, ForeverLoop):
        return _emit_forever_loop(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, RepeatLoop):
        return _emit_repeat_loop(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, SystemTaskCall):
        return _emit_system_task_call(stmt, prefix)
    elif isinstance(stmt, TaskEnable):
        return _emit_task_enable(stmt, prefix)
    elif isinstance(stmt, DelayControl):
        return _emit_delay_control(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, EventControl):
        return _emit_event_control(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, WaitStatement):
        return _emit_wait_statement(stmt, indent, current_indent, prefix)
    elif isinstance(stmt, DisableStatement):
        return f"{prefix}disable {stmt.target};"
    elif isinstance(stmt, EventTrigger):
        return f"{prefix}-> {stmt.event};"

    return f"{prefix}/* unknown statement */;"


def _emit_seq_block(stmt: SeqBlock, indent: str, current_indent: str, prefix: str) -> str:
    """Emit begin...end block."""
    inner_indent = current_indent + indent
    lines: list[str] = []

    header = f"{prefix}begin"
    name = stmt.name or (f"vfb{next(_style()._block_ids)}" if _style().named_blocks else None)
    if name:
        header += f" : {name}"
    lines.append(header)

    for local_var in stmt.local_vars:
        lines.append(_emit_variable(local_var, inner_indent))
    for s in stmt.statements:
        lines.append(_emit_statement(s, indent, inner_indent))

    lines.append(f"{current_indent}end")
    return "\n".join(lines)


def _emit_par_block(stmt: ParBlock, indent: str, current_indent: str, prefix: str) -> str:
    """Emit fork...join block."""
    inner_indent = current_indent + indent
    lines: list[str] = []

    header = f"{prefix}fork"
    if stmt.name:
        header += f" : {stmt.name}"
    lines.append(header)

    for local_var in stmt.local_vars:
        lines.append(_emit_variable(local_var, inner_indent))
    for s in stmt.statements:
        lines.append(_emit_statement(s, indent, inner_indent))

    lines.append(f"{current_indent}join")
    return "\n".join(lines)


def _ends_with_open_if(stmt: Statement | None) -> bool:
    """True if `stmt`, emitted without begin/end, ends in an `if` that has no `else`.

    Such a statement must not be the bare then-body of an if/else: Verilog binds
    an `else` to the nearest unmatched `if`, so the outer `else` would attach to
    the inner `if` (the classic dangling else).
    """
    if isinstance(stmt, IfStatement):
        return stmt.else_body is None or _ends_with_open_if(stmt.else_body)
    if isinstance(stmt, (ForLoop, WhileLoop, RepeatLoop, ForeverLoop)):
        return _ends_with_open_if(stmt.body)
    return False


def _emit_if_statement(stmt: IfStatement, indent: str, current_indent: str, prefix: str) -> str:
    """Emit if/else statement."""
    lines: list[str] = []
    inner_indent = current_indent + indent

    lines.append(f"{prefix}if ({emit_expression(stmt.condition)})")
    if stmt.then_body:
        if isinstance(stmt.then_body, SeqBlock):
            lines.append(_emit_statement(stmt.then_body, indent, current_indent))
        elif stmt.else_body and _ends_with_open_if(stmt.then_body):
            # Wrap so the else below binds to this if, not to an if inside the body.
            lines.append(f"{current_indent}begin")
            lines.append(_emit_statement(stmt.then_body, indent, inner_indent))
            lines.append(f"{current_indent}end")
        else:
            lines.append(_emit_statement(stmt.then_body, indent, inner_indent))

    if stmt.else_body:
        if isinstance(stmt.else_body, IfStatement) and _style().else_begin_if:
            lines.append(f"{current_indent}else")
            lines.append(_emit_statement(SeqBlock([stmt.else_body]), indent, current_indent))
        elif isinstance(stmt.else_body, IfStatement):
            # else if chain
            lines.append(f"{current_indent}else " + _emit_statement_inline(stmt.else_body, indent, current_indent))
        elif isinstance(stmt.else_body, SeqBlock):
            lines.append(f"{current_indent}else")
            lines.append(_emit_statement(stmt.else_body, indent, current_indent))
        else:
            lines.append(f"{current_indent}else")
            lines.append(_emit_statement(stmt.else_body, indent, inner_indent))

    return "\n".join(lines)


def _emit_case_statement(stmt: CaseStatement, indent: str, current_indent: str, prefix: str) -> str:
    """Emit case/casex/casez statement."""
    lines: list[str] = []
    inner_indent = current_indent + indent

    lines.append(f"{prefix}{stmt.case_type} ({emit_expression(stmt.expression)})")
    case_rng = _style().case_rng
    items = stmt.items
    if _style().split_case_items:
        # (Order kept: an earlier value still matches first, and `default`
        # stays where it was.)
        items = [
            split
            for item in items
            for split in ([item] if item.is_default else [CaseItem([value], item.body) for value in item.values])
        ]
    if case_rng is not None:
        items = _respell_case_items(items, case_rng)
    for item in items:
        lines.append(_emit_case_item(item, indent, inner_indent))
    lines.append(f"{current_indent}endcase")

    return "\n".join(lines)


def _respell_case_items(items: list[CaseItem], rng: random.Random) -> list[CaseItem]:
    """Equivalent case items (`EmitStyle.case_rng`): merge adjacent items
    with the same body, and add an earlier item's value to a later item
    (where it can never match: the earlier item matches first)."""
    out: list[CaseItem] = []
    earlier: list[Expression] = []
    with emit_style(EmitStyle()):
        bodies = [_emit_statement(item.body, "  ", "") if item.body else "" for item in items]
    prev_body = None
    for item, body in zip(items, bodies):
        if item.is_default:
            out.append(item)
            prev_body = None
            continue
        values = list(item.values)
        if earlier and rng.random() < 0.4:
            values.insert(rng.randint(0, len(values)), rng.choice(earlier))
        if prev_body == body and rng.random() < 0.8:
            out[-1] = CaseItem(out[-1].values + values, item.body)
        else:
            out.append(CaseItem(values, item.body))
        prev_body = body
        earlier.extend(item.values)
    return out


def _emit_case_item(item: CaseItem, indent: str, current_indent: str) -> str:
    """Emit a single case item."""
    inner_indent = current_indent + indent

    if item.is_default:
        label = f"{current_indent}default:"
    else:
        values = ", ".join(emit_expression(v) for v in item.values)
        label = f"{current_indent}{values}:"

    if item.body:
        if isinstance(item.body, SeqBlock):
            return label + " " + _emit_statement_inline(item.body, indent, current_indent)
        return label + " " + _emit_statement_inline(item.body, indent, inner_indent)

    return label + " ;"


def _emit_for_loop(stmt: ForLoop, indent: str, current_indent: str, prefix: str) -> str:
    """Emit for loop."""
    if stmt.declares_var and _style().for_var_outside and isinstance(stmt.init.lhs, Identifier):
        # (A block-local variable, not the enclosing scope's: the loop's
        # own variable may shadow one there.)
        loop = copy.copy(stmt)
        loop.declares_var = False
        var = Variable(stmt.init.lhs.name, VariableKind.INT, signed=stmt.signed_var)
        return _emit_seq_block(SeqBlock([loop], local_vars=[var]), indent, current_indent, prefix)
    init = f"{emit_expression(stmt.init.lhs)} = {emit_expression(stmt.init.rhs)}"
    if stmt.declares_var:
        # `for (int i = ...)`: the loop's own variable. It used to be dropped,
        # so the loop used an outer `i` (or an undeclared one).
        init = f"{'int' if stmt.signed_var else 'int unsigned'} {init}"
    cond = emit_expression(stmt.condition)
    update = f"{emit_expression(stmt.update.lhs)} = {emit_expression(stmt.update.rhs)}"
    inner_indent = current_indent + indent

    lines = [f"{prefix}for ({init}; {cond}; {update})"]
    if stmt.body:
        if isinstance(stmt.body, SeqBlock):
            lines.append(_emit_statement(stmt.body, indent, current_indent))
        else:
            lines.append(_emit_statement(stmt.body, indent, inner_indent))
    return "\n".join(lines)


def _emit_while_loop(stmt: WhileLoop, indent: str, current_indent: str, prefix: str) -> str:
    """Emit while loop."""
    inner_indent = current_indent + indent
    lines = [f"{prefix}while ({emit_expression(stmt.condition)})"]
    if stmt.body:
        if isinstance(stmt.body, SeqBlock):
            lines.append(_emit_statement(stmt.body, indent, current_indent))
        else:
            lines.append(_emit_statement(stmt.body, indent, inner_indent))
    return "\n".join(lines)


def _emit_forever_loop(stmt: ForeverLoop, indent: str, current_indent: str, prefix: str) -> str:
    """Emit forever loop."""
    inner_indent = current_indent + indent
    lines = [f"{prefix}forever"]
    if stmt.body:
        if isinstance(stmt.body, SeqBlock):
            lines.append(_emit_statement(stmt.body, indent, current_indent))
        else:
            lines.append(_emit_statement(stmt.body, indent, inner_indent))
    return "\n".join(lines)


def _emit_repeat_loop(stmt: RepeatLoop, indent: str, current_indent: str, prefix: str) -> str:
    """Emit repeat loop."""
    inner_indent = current_indent + indent
    lines = [f"{prefix}repeat ({emit_expression(stmt.count)})"]
    if stmt.body:
        if isinstance(stmt.body, SeqBlock):
            lines.append(_emit_statement(stmt.body, indent, current_indent))
        else:
            lines.append(_emit_statement(stmt.body, indent, inner_indent))
    return "\n".join(lines)


def _emit_system_task_call(stmt: SystemTaskCall, prefix: str) -> str:
    """Emit system task call: $display("hello");"""
    if stmt.arguments:
        args = ", ".join(emit_expression(a) for a in stmt.arguments)
        return f"{prefix}{stmt.task_name}({args});"
    return f"{prefix}{stmt.task_name};"


def _emit_task_enable(stmt: TaskEnable, prefix: str) -> str:
    """Emit task enable: my_task(arg1, arg2);"""
    if stmt.arguments:
        args = ", ".join(emit_expression(a) for a in stmt.arguments)
        return f"{prefix}{stmt.task_name}({args});"
    return f"{prefix}{stmt.task_name};"


def _emit_delay_control(stmt: DelayControl, indent: str, current_indent: str, prefix: str) -> str:
    """Emit delay control: #5 statement;"""
    result = f"{prefix}#{emit_expression(stmt.delay)} "
    if stmt.body:
        result += _emit_statement_inline(stmt.body, indent, current_indent)
    return result


def _emit_event_control(stmt: EventControl, indent: str, current_indent: str, prefix: str) -> str:
    """Emit event control: @(posedge clk) statement;"""
    if stmt.events:
        result = f"{prefix}@({_emit_sensitivity_list(stmt.events)}) "
    else:
        result = f"{prefix}@(*) " if _style().star_paren else f"{prefix}@* "

    if stmt.body:
        result += _emit_statement_inline(stmt.body, indent, current_indent)
    return result


def _emit_wait_statement(stmt: WaitStatement, indent: str, current_indent: str, prefix: str) -> str:
    """Emit wait statement: wait (condition) statement;"""
    result = f"{prefix}wait ({emit_expression(stmt.condition)}) "
    if stmt.body:
        result += _emit_statement_inline(stmt.body, indent, current_indent)
    else:
        result += ";"
    return result


# ---- Function / Task emission ----


def _emit_function_decl(fn: FunctionDecl, indent: str, current_indent: str) -> str:
    """Emit a function declaration.

    Example:
        function [7:0] add;
            input [7:0] a;
            input [7:0] b;
            begin
                add = a + b;
            end
        endfunction
    """
    inner_indent = current_indent + indent
    parts = [f"{current_indent}function"]
    if fn.is_automatic:
        parts.append("automatic")
    if fn.return_kind:
        parts.append(fn.return_kind)
        if fn.return_kind in INTEGER_ATOM_WIDTHS and not fn.signed:
            parts.append("unsigned")
    elif fn.signed:
        parts.append("signed")  # used to be dropped: a signed function round-tripped unsigned
    if not fn.return_kind and fn.return_range:
        parts.append(_emit_range(fn.return_range))
    parts.append(fn.name)
    lines: list[str] = []

    if fn.ports and _style().old_style_ports:
        # Old-style: function name; then one `input ...;` per port
        lines.append(" ".join(parts) + ";")
        lines.extend(f"{inner_indent}{_emit_port(p)};" for p in fn.ports)
    elif fn.ports:
        # ANSI-style: function name(port_list);
        port_strs = [_emit_port(p) for p in fn.ports]
        header = " ".join(parts)
        lines.append(f"{header} ({', '.join(port_strs)});")
    else:
        # Old-style: function name;
        lines.append(" ".join(parts) + ";")

    lines.extend(_emit_tf_locals(fn.locals, inner_indent))
    if fn.body:
        lines.append(_emit_statement(fn.body, indent, inner_indent))
    lines.append(f"{current_indent}endfunction")
    return "\n".join(lines)


def _emit_task_decl(tk: TaskDecl, indent: str, current_indent: str) -> str:
    """Emit a task declaration.

    Example:
        task my_task;
            input [7:0] data;
            begin
                ...
            end
        endtask
    """
    inner_indent = current_indent + indent
    parts = [f"{current_indent}task"]
    if tk.is_automatic:
        parts.append("automatic")
    parts.append(tk.name)
    lines: list[str] = []

    if tk.ports and _style().old_style_ports:
        # Old-style: task name; then one `input ...;` per port
        lines.append(" ".join(parts) + ";")
        lines.extend(f"{inner_indent}{_emit_port(p)};" for p in tk.ports)
    elif tk.ports:
        # ANSI-style: task name(port_list);
        port_strs = [_emit_port(p) for p in tk.ports]
        header = " ".join(parts)
        lines.append(f"{header} ({', '.join(port_strs)});")
    else:
        # Old-style: task name;
        lines.append(" ".join(parts) + ";")

    lines.extend(_emit_tf_locals(tk.locals, inner_indent))
    if tk.body:
        lines.append(_emit_statement(tk.body, indent, inner_indent))
    lines.append(f"{current_indent}endtask")
    return "\n".join(lines)


def _emit_tf_locals(local_vars: list[Variable], indent: str) -> list[str]:
    """Function/task-local declarations (they used to be left out, so the
    emitted Verilog referenced undeclared names). Initializers are already
    statements at the top of the body (see the parser), so none here."""
    out = []
    for var in local_vars:
        init, var.initial_value = var.initial_value, None
        try:
            out.append(_emit_variable(var, indent))
        finally:
            var.initial_value = init
    return out


# ---- Generate construct emission ----


class _GenvarScope:
    """`EmitStyle.genvar_outside` for one scope's generate items: which
    loops' `genvar` is declared before the loop (once per name per scope;
    not where the name is already a signal of the scope)."""

    def __init__(self, items: list, names: set[str]) -> None:
        self.hoisted: set[int] = set()
        self._declared = {name for item in items if isinstance(item, GenvarDecl) for name in item.names}
        self._taken = names
        self._on = _style().genvar_outside

    def declare(self, item: object, current_indent: str) -> list[str]:
        if not (self._on and isinstance(item, GenerateFor) and item.genvar_local):
            return []
        if item.genvar in self._taken:
            return []
        self.hoisted.add(id(item))
        if item.genvar in self._declared:
            return []
        self._declared.add(item.genvar)
        return [f"{current_indent}genvar {item.genvar};"]


def _emit_generate_construct(
    gen: GenerateFor | GenerateIf | GenerateCase | GenvarDecl,
    indent: str,
    current_indent: str,
    hoisted: set[int] | None = None,
) -> str:
    """Dispatch to specific generate emitter (*hoisted*: ids of loops whose
    genvar was declared before them, `EmitStyle.genvar_outside`)."""
    if isinstance(gen, GenvarDecl):
        return _emit_genvar_decl(gen, current_indent)
    elif isinstance(gen, GenerateFor):
        return _emit_generate_for(
            gen, indent, current_indent, local=gen.genvar_local and id(gen) not in (hoisted or ())
        )
    elif isinstance(gen, GenerateIf):
        return _emit_generate_if(gen, indent, current_indent)
    elif isinstance(gen, GenerateCase):
        return _emit_generate_case(gen, indent, current_indent)
    return f"{current_indent}/* unknown generate construct */;"


def _emit_genvar_decl(gv: GenvarDecl, current_indent: str) -> str:
    """Emit genvar declaration: genvar i, j;"""
    return f"{current_indent}genvar {', '.join(gv.names)};"


def _emit_generate_for(gen: GenerateFor, indent: str, current_indent: str, *, local: bool | None = None) -> str:
    """Emit a generate-for loop (*local*: declare the genvar in the loop
    header; default `gen.genvar_local`).

    Examples:
        for (i = 0; i < N; i = i + 1) begin : gen_block
        for (genvar i = 0; i < N; i++) begin : gen_block
    """
    genvar_prefix = "genvar " if (gen.genvar_local if local is None else local) else ""
    init = f"{genvar_prefix}{gen.genvar} = {emit_expression(gen.init_value)}"
    cond = emit_expression(gen.condition)

    # Format iteration based on update_op
    op = gen.update_op
    if op in ("post++", "post--"):
        update = f"{gen.genvar}{op[4:]}"
    elif op in ("pre++", "pre--"):
        update = f"{op[3:]}{gen.genvar}"
    elif op == "=" and gen.update is not None:
        update = f"{gen.genvar} = {emit_expression(gen.update)}"
    elif op in ("+=", "-=", "*=", "/=", "%=") and gen.update is not None:
        update = f"{gen.genvar} {op} {emit_expression(gen.update)}"
    else:
        update = f"{gen.genvar} = {gen.genvar} + 1"  # safety fallback

    lines = [f"{current_indent}for ({init}; {cond}; {update})"]
    lines.append(_emit_generate_block(gen.body, indent, current_indent))
    return "\n".join(lines)


def _emit_generate_if(gen: GenerateIf, indent: str, current_indent: str) -> str:
    """Emit a generate-if construct.

    Example:
        if (PARAM == 1) begin : gen_true
            ...
        end else begin : gen_false
            ...
        end
    """
    lines = [f"{current_indent}if ({emit_expression(gen.condition)})"]
    if gen.then_body:
        lines.append(_emit_generate_block(gen.then_body, indent, current_indent))
    else:
        lines.append(f"{current_indent};")

    if gen.else_body:
        lines.append(f"{current_indent}else")
        lines.append(_emit_generate_block(gen.else_body, indent, current_indent))

    return "\n".join(lines)


def _emit_generate_case(gen: GenerateCase, indent: str, current_indent: str) -> str:
    """Emit a generate-case construct.

    Example:
        [unique|unique0|priority] case (MODE)
            0: begin : gen_mode0
                ...
            end
            default: begin : gen_default
                ...
            end
        endcase
    """
    inner_indent = current_indent + indent
    qual = f"{gen.qualifier} " if gen.qualifier else ""
    lines = [f"{current_indent}{qual}case ({emit_expression(gen.expression)})"]
    for item in gen.items:
        lines.append(_emit_generate_case_item(item, indent, inner_indent))
    lines.append(f"{current_indent}endcase")
    return "\n".join(lines)


def _emit_generate_case_item(item: GenerateCaseItem, indent: str, current_indent: str) -> str:
    """Emit a single generate case item."""
    if item.is_default:
        label = f"{current_indent}default:"
    else:
        values = ", ".join(emit_expression(v) for v in item.values)
        label = f"{current_indent}{values}:"

    if item.body:
        return label + " " + _emit_generate_block(item.body, indent, current_indent).lstrip()
    return label + " ;"


def _emit_generate_block(block: GenerateBlock, indent: str, current_indent: str) -> str:
    """Emit a generate block (begin/end or single item).

    For named blocks: begin : name ... end
    For unnamed with multiple items: begin ... end
    For single item unnamed: just the item
    """
    inner_indent = current_indent + indent

    if block.name or len(block.items) != 1:
        # Use begin/end form
        header = f"{current_indent}begin"
        if block.name:
            header += f" : {block.name}"
        lines = [header]
        names = {item.name for item in block.items if isinstance(item, (Net, Variable, Parameter))}
        genvars = _GenvarScope(block.items, names)
        folded = _block_net_decl_assigns(block) if _style().net_decl_assign else {}
        folded_assigns = {id(ca) for ca in folded.values()}
        for item in block.items:
            if id(item) in folded_assigns:
                continue  # (declared with the net)
            if id(item) in folded:
                lines.append(_emit_net(item, inner_indent, folded[id(item)].rhs))  # type: ignore[arg-type]
                continue
            lines.extend(genvars.declare(item, inner_indent))
            if isinstance(item, GenerateFor):
                lines.append(_emit_generate_construct(item, indent, inner_indent, hoisted=genvars.hoisted))
            else:
                lines.append(_emit_generate_block_item(item, indent, inner_indent))
        lines.append(f"{current_indent}end")
        return "\n".join(lines)
    elif block.items:
        # Single item, no begin/end needed
        return _emit_generate_block_item(block.items[0], indent, inner_indent)
    else:
        return f"{current_indent};"


def _block_net_decl_assigns(block: GenerateBlock) -> dict[int, ContinuousAssign]:
    """`EmitStyle.net_decl_assign` inside a generate block: id of a net ->
    its only continuous assign (none in nested constructs either), folded
    into the net's declaration where the assign reads no block-local name
    declared after the net."""
    items = block.items
    writes: Counter[str] = Counter()
    for node in block.walk():
        if isinstance(node, ContinuousAssign):
            for ident in node.lhs.walk():
                if isinstance(ident, Identifier):
                    writes[ident.name] += 1
    local = {item.name for item in items if isinstance(item, (Net, Variable, Parameter))}
    assigns = {
        ca.lhs.name: ca
        for ca in items
        if isinstance(ca, ContinuousAssign) and isinstance(ca.lhs, Identifier) and not ca.lhs.hierarchy
    }
    folded: dict[int, ContinuousAssign] = {}
    declared: set[str] = set()
    for item in items:
        if not isinstance(item, (Net, Variable, Parameter)):
            continue
        ca = assigns.get(item.name)
        if (
            isinstance(item, Net)
            and ca is not None
            and not item.dimensions
            and item.initial_value is None
            and writes[item.name] == 1
        ):
            reads = {node.name for node in ca.rhs.walk() if isinstance(node, Identifier)}
            if all(name in declared or name not in local for name in reads):
                folded[id(item)] = ca
        declared.add(item.name)
    return folded


def _emit_generate_block_item(item: VerilogNode, indent: str, current_indent: str) -> str:  # noqa: PLR0911
    """Emit a single item inside a generate block.

    Items can be nets, variables, instances, assigns, always/initial blocks,
    or nested generate constructs.
    """
    if isinstance(item, Net):
        return _emit_net(item, current_indent)
    elif isinstance(item, Variable):
        return _emit_variable(item, current_indent)
    elif isinstance(item, ContinuousAssign):
        return _emit_continuous_assign(item, current_indent)
    elif isinstance(item, Instance):
        return _emit_instance(item, current_indent)
    elif isinstance(item, Parameter):
        return _emit_localparam(item, current_indent)
    elif isinstance(item, AlwaysBlock):
        return _emit_always_block(item, indent, current_indent)
    elif isinstance(item, InitialBlock):
        return _emit_initial_block(item, indent, current_indent)
    elif isinstance(item, (GenerateFor, GenerateIf, GenerateCase, GenvarDecl)):
        return _emit_generate_construct(item, indent, current_indent)
    return f"{current_indent}/* unknown generate item */;"


# ---- Specify block emission ----


def _emit_specify_block(sb: SpecifyBlock, indent: str) -> str:
    """Emit a specify block.

    When ``source_text`` was captured from the original source (via tree
    position metadata), it is re-indented and emitted verbatim for
    faithful round-tripping.  Otherwise a best-effort reconstruction
    from the raw Lark tree tokens is produced.
    """
    if sb.source_text:
        # Re-indent the captured source text
        raw_lines = sb.source_text.splitlines()
        if not raw_lines:
            return f"{indent}specify\n{indent}endspecify"
        # First line is "specify", last is "endspecify"
        result_lines: list[str] = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("specify") or stripped.startswith("endspecify"):
                result_lines.append(f"{indent}{stripped}")
            else:
                result_lines.append(f"{indent}  {stripped}")
        return "\n".join(result_lines)

    # Fallback: reconstruct from tree tokens (loses structural punctuation
    # like parentheses and operators, but preserves keywords & identifiers)
    from lark import Token as LarkToken
    from lark import Tree as LarkTree

    def _gather_tokens(tree: LarkTree) -> list[str]:
        """Collect all leaf tokens from a Lark tree."""
        tokens: list[str] = []
        for child in tree.children:
            if isinstance(child, LarkToken):
                tokens.append(str(child))
            elif isinstance(child, LarkTree):
                tokens.extend(_gather_tokens(child))
        return tokens

    inner_parts: list[str] = []
    inner_indent = indent + "  "

    for child in sb.raw_tree.children:
        if isinstance(child, LarkToken):
            continue  # skip KW_SPECIFY / KW_ENDSPECIFY
        elif isinstance(child, LarkTree):
            tokens = _gather_tokens(child)
            if tokens:
                inner_parts.append(f"{inner_indent}{' '.join(tokens)} ;")

    lines = [f"{indent}specify"]
    lines.extend(inner_parts)
    lines.append(f"{indent}endspecify")
    return "\n".join(lines)
