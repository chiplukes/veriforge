"""Declaration-oriented helpers for model transforms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lark import Token, Tree

from ..model.assignments import ContinuousAssign
from ..model.base import SourceLocation
from ..model.expressions import BinaryOp, Expression, Identifier, Literal, Range
from ..model.nets import Net, NetKind
from ..model.package import ImportDecl
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.sv_types import EnumMember, EnumType, StructField, StructType, TypedefDecl, UnionType
from ..model.variables import Variable, VariableKind
from ._tree_utils import _collect_text, _loc_from_tree

_DIMENSION_RANGE_EXPR_COUNT = 2
_MIN_NAMED_IMPORT_IDENTIFIERS = 2
_PARAMETER_TYPE_TOKEN_KINDS = frozenset(
    (
        "KW_BIT",
        "KW_BYTE",
        "KW_INT",
        "KW_INTEGER",
        "KW_LOGIC",
        "KW_LONGINT",
        "KW_REG",
        "KW_SHORTINT",
        "KW_TIME",
    )
)

BuildConstantExpressionFn = Callable[[Tree, str | None], Expression]
BuildExpressionFn = Callable[[Tree, str | None], Expression]


@dataclass(frozen=True)
class _RegVariableContext:
    source_file: str | None
    kind: VariableKind
    width: Range | None
    signed: bool
    loc: SourceLocation | None
    build_constant_expression: BuildConstantExpressionFn


@dataclass(frozen=True)
class _SvTypeContext:
    source_file: str | None
    type_name: str
    declared_dims: list[Range]
    loc: SourceLocation | None
    build_constant_expression: BuildConstantExpressionFn


def _direction_from_rule(rule_name: str) -> PortDirection:
    """Map rule name to PortDirection."""
    if "input" in rule_name:
        return PortDirection.INPUT
    elif "output" in rule_name:
        return PortDirection.OUTPUT
    elif "inout" in rule_name:
        return PortDirection.INOUT
    return PortDirection.INPUT


def _net_kind_from_tree(tree: Tree) -> NetKind:
    """Extract NetKind from a net_type tree."""
    text = _collect_text(tree).strip().lower()
    try:
        return NetKind(text)
    except ValueError:
        return NetKind.WIRE


def _extract_identifiers(tree: Tree) -> list[str]:
    """Extract identifier names from a declaration tree."""
    names: list[str] = []
    for child in tree.children:
        if isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            names.append(str(child))
        elif isinstance(child, Tree) and child.data in (
            "variable_type",
            "real_type",
            "net_decl_assignment",
            "port_id_with_dimensions",
        ):
            name = _first_identifier(child)
            if name:
                names.append(name)
    return names


def _first_identifier(tree: Tree) -> str | None:
    """Find the first identifier-like token in a tree."""
    for child in tree.children:
        if isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            return str(child)
        elif isinstance(child, Token) and child.type not in (
            "KW_REG",
            "KW_INTEGER",
            "KW_REAL",
            "KW_TIME",
            "KW_EVENT",
            "KW_REALTIME",
        ):
            text = str(child)
            if text and text[0].isalpha():
                return text

    for child in tree.children:
        if isinstance(child, Tree):
            result = _first_identifier(child)
            if result:
                return result
    return None


def _build_range(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> Range | None:
    """Build a Range from a range tree node containing msb/lsb expressions."""
    msb: Expression | None = None
    lsb: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "msb_constant_expression":
                msb = build_constant_expression(child, source_file)
            elif child.data == "lsb_constant_expression":
                lsb = build_constant_expression(child, source_file)

    if msb is not None and lsb is not None:
        loc = _loc_from_tree(tree, source_file)
        return Range(msb=msb, lsb=lsb, loc=loc)
    return None


def _build_dimension(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> Range | None:
    """Build a Range from a dimension tree node."""
    exprs: list[Expression] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "dimension_constant_expression":
            exprs.append(build_constant_expression(child, source_file))
    loc = _loc_from_tree(tree, source_file)
    if len(exprs) == _DIMENSION_RANGE_EXPR_COUNT:
        return Range(msb=exprs[0], lsb=exprs[1], loc=loc)
    if len(exprs) == 1:
        upper = BinaryOp(
            op="-",
            left=exprs[0],
            right=Literal(value="1", loc=loc),
            loc=loc,
        )
        return Range(msb=Literal(value="0", loc=loc), lsb=upper, loc=loc)
    return None


def _extract_dimension_range(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> Range | None:
    """Extract a Range from a dimension-like tree node."""
    if tree.data == "dimension":
        return _build_dimension(tree, source_file, build_constant_expression)

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "range":
            return _build_range(child, source_file, build_constant_expression)
        elif isinstance(child, Tree) and child.data == "constant_expression":
            expr = build_constant_expression(child, source_file)
            if expr:
                return Range(msb=expr, lsb=Literal(value=0, loc=_loc_from_tree(child, source_file)))
    return None


def _extract_dimensions(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Range]:
    """Extract array dimensions from a variable_type node."""
    dims: list[Range] = []
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "range":
                range_ = _build_range(child, source_file, build_constant_expression)
                if range_:
                    dims.append(range_)
            elif child.data == "dimension":
                range_ = _build_dimension(child, source_file, build_constant_expression)
                if range_:
                    dims.append(range_)
    return dims


def _extract_port_identifiers_with_dimensions(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[tuple[str, list[Range]]]:
    """Extract port identifiers and unpacked dimensions from a port identifier list."""
    items: list[tuple[str, list[Range]]] = []

    for child in tree.children:
        if isinstance(child, Token) and child.type in ("PORT_IDENTIFIER", "NET_IDENTIFIER"):
            items.append((str(child), []))
        elif isinstance(child, Tree) and child.data == "port_id_with_dimensions":
            name = ""
            dims: list[Range] = []
            for item in child.children:
                if isinstance(item, Token) and item.type == "PORT_IDENTIFIER":
                    name = str(item)
                elif isinstance(item, Tree) and item.data == "dimension":
                    dim = _extract_dimension_range(item, source_file, build_constant_expression)
                    if dim is not None:
                        dims.append(dim)
            if name:
                items.append((name, dims))

    return items


def _extract_port_names(tree: Tree, source_file: str | None) -> list[Port]:
    """Extract port names from a list_of_ports (old-style, non-ANSI)."""
    ports: list[Port] = []
    for child in tree.iter_subtrees():
        if child.data == "port":
            for item in child.children:
                if isinstance(item, Token):
                    name = str(item)
                    loc = _loc_from_tree(child, source_file)
                    ports.append(Port(name=name, direction=PortDirection.INPUT, loc=loc))
    return ports


def _extract_net_ids_with_dims(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[tuple[str, list[Range]]]:
    """Extract (name, dimensions) pairs from a list_of_net_identifiers node."""
    result: list[tuple[str, list[Range]]] = []
    current_name: str | None = None
    current_dims: list[Range] = []
    for child in tree.children:
        if isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            if current_name is not None:
                result.append((current_name, current_dims))
            current_name = str(child)
            current_dims = []
        elif isinstance(child, Tree) and child.data == "dimension":
            range_ = _build_dimension(child, source_file, build_constant_expression)
            if range_:
                current_dims.append(range_)
    if current_name is not None:
        result.append((current_name, current_dims))
    return result


def _extract_parameters(
    tree: Tree,
    source_file: str | None,
    is_local: bool,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Parameter]:
    """Extract parameters from a parameter port list or declaration."""
    if tree.data == "module_parameter_port_list":
        params: list[Parameter] = []
        for child in tree.children:
            if isinstance(child, Tree) and child.data in (
                "local_parameter_declaration",
                "parameter_declaration",
                "parameter_port_declaration",
            ):
                params.extend(
                    _extract_parameters(
                        child,
                        source_file,
                        is_local=child.data == "local_parameter_declaration",
                        build_constant_expression=build_constant_expression,
                    )
                )
        return params

    params = []
    param_type: str | None = None
    width: Range | None = None
    signed = False

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "parameter_type":
            param_type, width, signed = _extract_parameter_type(child, source_file, build_constant_expression)
            break

    for node in tree.iter_subtrees():
        if node.data == "param_assignment":
            name = ""
            default_value: Expression | None = None
            for child in node.children:
                if isinstance(child, Token) and child.type == "PARAMETER_IDENTIFIER":
                    name = str(child)
                elif isinstance(child, Tree) and child.data == "constant_mintypmax_expression":
                    default_value = build_constant_expression(child, source_file)

            if name:
                loc = _loc_from_tree(node, source_file)
                params.append(
                    Parameter(
                        name=name,
                        param_type=param_type,
                        width=width,
                        signed=signed,
                        default_value=default_value,
                        is_local=is_local,
                        loc=loc,
                    )
                )

    return params


def _extract_ports_from_declarations(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Port]:
    """Extract ports from list_of_port_declarations (ANSI-style)."""
    ports: list[Port] = []

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "port_declaration":
            ports.extend(_extract_port_declaration(child, source_file, build_constant_expression))

    return ports


def _extract_net_declaration(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
    build_expression: BuildExpressionFn,
) -> tuple[list[Net], list[ContinuousAssign]]:
    """Extract nets and implicit continuous assigns from a net_declaration subtree."""
    nets: list[Net] = []
    implicit_assigns: list[ContinuousAssign] = []
    kind = NetKind.WIRE
    packed_ranges: list[Range] = []
    signed = False
    net_entries: list[tuple[str, list[Range]]] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_SIGNED":
                signed = True
            elif child.type == "NET_IDENTIFIER":
                net_entries.append((str(child), []))
        elif isinstance(child, Tree):
            if child.data == "net_type":
                kind = _net_kind_from_tree(child)
            elif child.data == "range":
                range_ = _build_range(child, source_file, build_constant_expression)
                if range_ is not None:
                    packed_ranges.append(range_)
            elif child.data == "list_of_net_identifiers":
                net_entries.extend(_extract_net_ids_with_dims(child, source_file, build_constant_expression))
            elif child.data == "list_of_net_decl_assignments":
                entries, assigns = _extract_net_decl_assignments(child, source_file, loc, build_expression)
                net_entries.extend(entries)
                implicit_assigns.extend(assigns)

    width = packed_ranges[-1] if packed_ranges else None
    extra_dims = packed_ranges[:-1] if len(packed_ranges) > 1 else []

    for name, dims in net_entries:
        all_dims = extra_dims + dims
        nets.append(Net(name=name, kind=kind, width=width, signed=signed, dimensions=all_dims or None, loc=loc))

    return nets, implicit_assigns


def _extract_reg_declaration(
    tree: Tree,
    source_file: str | None,
    kind: VariableKind,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Variable]:
    """Extract variables from a reg_declaration or logic/bit declaration subtree."""
    variables: list[Variable] = []
    width: Range | None = None
    signed = False
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_SIGNED":
                signed = True
        elif isinstance(child, Tree):
            if child.data == "range":
                width = _build_range(child, source_file, build_constant_expression)
            elif child.data == "list_of_variable_identifiers":
                ctx = _RegVariableContext(
                    source_file=source_file,
                    kind=kind,
                    width=width,
                    signed=signed,
                    loc=loc,
                    build_constant_expression=build_constant_expression,
                )
                variables.extend(_extract_reg_variable_items(child, ctx))

    return variables


def _extract_reg_variable_items(
    tree: Tree,
    ctx: _RegVariableContext,
) -> list[Variable]:
    variables: list[Variable] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "variable_type":
            variable = _extract_reg_variable_item(child, ctx)
            if variable is not None:
                variables.append(variable)
        elif isinstance(child, Token):
            variables.append(Variable(name=str(child), kind=ctx.kind, width=ctx.width, signed=ctx.signed, loc=ctx.loc))
    return variables


def _extract_reg_variable_item(
    tree: Tree,
    ctx: _RegVariableContext,
) -> Variable | None:
    name = _first_identifier(tree)
    if not name:
        return None
    dims = _extract_dimensions(tree, ctx.source_file, ctx.build_constant_expression)
    initial_value = _extract_variable_initial_value(tree, ctx.source_file, ctx.build_constant_expression)
    return Variable(
        name=name,
        kind=ctx.kind,
        width=ctx.width,
        signed=ctx.signed,
        dimensions=dims,
        initial_value=initial_value,
        loc=ctx.loc,
    )


def _extract_typed_variable(
    tree: Tree,
    source_file: str | None,
    kind: VariableKind,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Variable]:
    """Extract variables from integer/real/realtime/time/event declarations."""
    variables: list[Variable] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.iter_subtrees():
        if child.data in ("variable_type", "real_type"):
            name = _first_identifier(child)
            if name:
                dims = _extract_dimensions(child, source_file, build_constant_expression)
                initial_value = _extract_variable_initial_value(child, source_file, build_constant_expression)
                variables.append(Variable(name=name, kind=kind, dimensions=dims, initial_value=initial_value, loc=loc))
        elif child.data == "event_identifier":
            name = _first_identifier(child)
            if name:
                variables.append(Variable(name=name, kind=kind, loc=loc))

    if not variables:
        names = _extract_identifiers(tree)
        for name in names:
            variables.append(Variable(name=name, kind=kind, loc=loc))

    return variables


def _extract_sv_type_declaration(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Variable]:
    """Extract variables from sv_type_declaration nodes."""
    variables: list[Variable] = []
    type_name = ""
    loc = _loc_from_tree(tree, source_file)
    declared_dims: list[Range] = []

    for child in tree.children:
        if isinstance(child, Token) and child.type == "IDENTIFIER" and not type_name:
            type_name = str(child)
        elif isinstance(child, Tree):
            if child.data == "scoped_identifier":
                parts = [str(token) for token in child.children if isinstance(token, Token)]
                type_name = "::".join(parts)
            elif child.data == "dimension":
                range_ = _extract_dimension_range(child, source_file, build_constant_expression)
                if range_:
                    declared_dims.append(range_)
            elif child.data == "sv_type_var_id":
                ctx = _SvTypeContext(
                    source_file=source_file,
                    type_name=type_name,
                    declared_dims=declared_dims,
                    loc=loc,
                    build_constant_expression=build_constant_expression,
                )
                variable = _extract_sv_type_variable(child, ctx)
                if variable is not None:
                    variables.append(variable)

    return variables


def _extract_sv_type_variable(
    tree: Tree,
    ctx: _SvTypeContext,
) -> Variable | None:
    name = ""
    dims: list[Range] = []
    for child in tree.children:
        if isinstance(child, Token) and child.type == "VARIABLE_IDENTIFIER":
            name = str(child)
        elif isinstance(child, Tree) and child.data == "dimension":
            range_ = _extract_dimension_range(child, ctx.source_file, ctx.build_constant_expression)
            if range_:
                dims.append(range_)
    if not name:
        return None
    return Variable(
        name=name,
        kind=VariableKind.LOGIC,
        dimensions=[*ctx.declared_dims, *dims],
        loc=ctx.loc,
        type_name=ctx.type_name,
    )


def _extract_typedef_declaration(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> TypedefDecl | None:
    """Extract a TypedefDecl from a typedef_declaration tree node."""
    name = ""
    enum_type: EnumType | None = None
    struct_type: StructType | None = None
    union_type: UnionType | None = None
    type_ref: str | None = None
    typedef_width: Range | None = None

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "VARIABLE_IDENTIFIER":
                name = str(child)
        elif isinstance(child, Tree):
            if child.data == "enum_declaration":
                enum_type = _extract_enum_type(child, source_file, build_constant_expression)
            elif child.data == "struct_declaration":
                struct_type = _extract_struct_type(child, source_file, build_constant_expression)
            elif child.data == "union_declaration":
                union_type = _extract_union_type(child, source_file, build_constant_expression)
            elif child.data == "enum_base_type":
                type_ref = _extract_base_type_string(child)
                typedef_width = _extract_typedef_width(child, source_file, build_constant_expression)

    if name:
        loc = _loc_from_tree(tree, source_file)
        return TypedefDecl(
            name=name,
            enum_type=enum_type,
            struct_type=struct_type,
            union_type=union_type,
            type_ref=type_ref,
            width=typedef_width,
            loc=loc,
        )
    return None


def _extract_typedef_width(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> Range | None:
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "range":
            return _build_range(child, source_file, build_constant_expression)
    return None


def _extract_struct_type(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> StructType:
    """Extract a StructType from a struct_declaration tree node."""
    fields: list[StructField] = []
    packed = False
    signed = False

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_PACKED":
                packed = True
            elif child.type == "KW_SIGNED":
                signed = True
        elif isinstance(child, Tree) and child.data == "struct_member":
            field = _extract_struct_field(child, source_file, build_constant_expression)
            if field:
                fields.append(field)

    struct_type = StructType(fields=fields, packed=packed, signed=signed)
    for field in struct_type.fields:
        field.parent = struct_type
    return struct_type


def _extract_union_type(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> UnionType:
    """Extract a UnionType from a union_declaration tree node."""
    fields: list[StructField] = []
    packed = False
    signed = False

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_PACKED":
                packed = True
            elif child.type == "KW_SIGNED":
                signed = True
        elif isinstance(child, Tree) and child.data == "struct_member":
            field = _extract_struct_field(child, source_file, build_constant_expression)
            if field:
                fields.append(field)

    union_type = UnionType(fields=fields, packed=packed, signed=signed)
    for field in union_type.fields:
        field.parent = union_type
    return union_type


def _extract_struct_field(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> StructField | None:
    """Extract a StructField from a struct_member tree node."""
    name = ""
    data_type: str | None = None
    width: Range | None = None
    signed = False

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "VARIABLE_IDENTIFIER":
                name = str(child)
            elif child.type == "IDENTIFIER" and data_type is None:
                data_type = str(child)
        elif isinstance(child, Tree) and child.data == "enum_base_type":
            data_type, width, signed = _parse_enum_base_type(child, source_file, build_constant_expression)

    if name and data_type:
        return StructField(name=name, data_type=data_type, width=width, signed=signed)
    return None


def _extract_enum_type(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> EnumType:
    """Extract an EnumType from an enum_declaration tree node."""
    members: list[EnumMember] = []
    base_type: str | None = None
    width: Range | None = None
    signed = False

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "enum_name_declaration":
                member = _extract_enum_member(child, source_file, build_constant_expression)
                if member:
                    members.append(member)
            elif child.data == "enum_base_type":
                base_type, width, signed = _parse_enum_base_type(child, source_file, build_constant_expression)

    return EnumType(members=members, base_type=base_type, width=width, signed=signed)


def _extract_enum_member(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> EnumMember | None:
    """Extract an EnumMember from an enum_name_declaration tree node."""
    name = ""
    value: Expression | None = None

    for child in tree.children:
        if isinstance(child, Token) and child.type == "VARIABLE_IDENTIFIER":
            name = str(child)
        elif isinstance(child, Tree) and child.data == "constant_expression":
            value = build_constant_expression(child, source_file)

    if name:
        return EnumMember(name=name, value=value)
    return None


def _extract_variable_initial_value(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> Expression | None:
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "constant_expression":
            return build_constant_expression(child, source_file)
    return None


def _extract_net_decl_assignments(
    tree: Tree,
    source_file: str | None,
    loc: SourceLocation | None,
    build_expression: BuildExpressionFn,
) -> tuple[list[tuple[str, list[Range]]], list[ContinuousAssign]]:
    entries: list[tuple[str, list[Range]]] = []
    assigns: list[ContinuousAssign] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "net_decl_assignment":
            name, expr = _extract_net_decl_assignment(child, source_file, build_expression)
            if name is not None:
                entries.append((name, []))
                if expr is not None:
                    assigns.append(ContinuousAssign(lhs=Identifier(name), rhs=expr, loc=loc))
    return entries, assigns


def _extract_net_decl_assignment(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
) -> tuple[str | None, Expression | None]:
    name = None
    expr = None
    for child in tree.children:
        if isinstance(child, Token) and child.type == "NET_IDENTIFIER":
            name = str(child)
        elif isinstance(child, Tree) and child.data == "expression":
            expr = build_expression(child, source_file)
    return name, expr


def _extract_port_declaration(  # noqa: PLR0912
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> list[Port]:
    """Extract one or more ports from a port_declaration subtree."""
    ports: list[Port] = []

    for child in tree.children:
        if not isinstance(child, Tree):
            continue

        if child.data in ("input_declaration", "output_declaration", "inout_declaration"):
            direction = _direction_from_rule(child.data)
            width: Range | None = None
            signed = False
            data_type: str | None = None
            declared_dims: list[Range] = []
            port_items: list[tuple[str, list[Range]]] = []
            loc = _loc_from_tree(child, source_file)

            for item in child.children:
                if isinstance(item, Token):
                    if item.type == "KW_REG":
                        data_type = "reg"
                    elif item.type == "KW_SIGNED":
                        signed = True
                    elif item.type in ("PORT_IDENTIFIER", "NET_IDENTIFIER"):
                        port_items.append((str(item), []))
                    elif item.type == "IDENTIFIER":
                        data_type = str(item)
                elif isinstance(item, Tree):
                    if item.data == "range":
                        width = _build_range(item, source_file, build_constant_expression)
                    elif item.data == "dimension":
                        dim = _extract_dimension_range(item, source_file, build_constant_expression)
                        if dim is not None:
                            declared_dims.append(dim)
                    elif item.data == "scoped_identifier":
                        tokens = [str(c) for c in item.children if isinstance(c, Token)]
                        data_type = "::".join(tokens)
                    elif item.data in ("list_of_port_identifiers", "list_of_variable_port_identifiers"):
                        port_items.extend(
                            _extract_port_identifiers_with_dimensions(item, source_file, build_constant_expression)
                        )

            for name, dims in port_items:
                ports.append(
                    Port(
                        name=name,
                        direction=direction,
                        data_type=data_type,
                        width=width,
                        dimensions=[*declared_dims, *dims],
                        signed=signed,
                        loc=loc,
                    )
                )

    return ports


def _extract_parameter_type(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> tuple[str | None, Range | None, bool]:
    """Extract shared type metadata from a parameter_type node."""
    param_type: str | None = None
    width: Range | None = None
    signed = False

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_SIGNED":
                signed = True
            elif child.type in _PARAMETER_TYPE_TOKEN_KINDS:
                param_type = str(child)
        elif isinstance(child, Tree):
            if child.data == "range":
                width = _build_range(child, source_file, build_constant_expression)
            elif child.data == "scoped_identifier":
                parts = [str(token) for token in child.children if isinstance(token, Token)]
                param_type = "::".join(parts)

    return param_type, width, signed


def _parse_enum_base_type(
    tree: Tree,
    source_file: str | None,
    build_constant_expression: BuildConstantExpressionFn,
) -> tuple[str | None, Range | None, bool]:
    """Parse an enum_base_type node and return (base_type, width, signed)."""
    base_type: str | None = None
    width: Range | None = None
    signed = False

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_SIGNED":
                signed = True
            elif child.type.startswith("KW_"):
                base_type = str(child)
        elif isinstance(child, Tree) and child.data == "range":
            width = _build_range(child, source_file, build_constant_expression)

    return base_type, width, signed


def _extract_base_type_string(tree: Tree) -> str:
    """Build a type string from an enum_base_type node for type alias typedefs."""
    parts: list[str] = []
    for child in tree.children:
        if isinstance(child, Token):
            parts.append(str(child))
        elif isinstance(child, Tree) and child.data == "range":
            tokens = [str(token) for token in child.scan_values(lambda token: True)]
            parts.append("[" + ":".join(tokens) + "]")
    return " ".join(parts)


def _extract_import_declaration(tree: Tree, source_file: str | None) -> list[ImportDecl]:
    """Extract ImportDecl items from an import_declaration tree node."""
    result: list[ImportDecl] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "import_item":
            import_decl = _extract_import_item(child, source_file)
            if import_decl:
                result.append(import_decl)
    return result


def _extract_package_import_declaration(tree: Tree, source_file: str | None) -> list[ImportDecl]:
    """Extract ImportDecl items from a package_import_declaration tree node."""
    result: list[ImportDecl] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "package_import_item":
            identifiers = [
                str(token) for token in child.children if isinstance(token, Token) and token.type == "IDENTIFIER"
            ]
            loc = _loc_from_tree(child, source_file)
            if len(identifiers) >= _MIN_NAMED_IMPORT_IDENTIFIERS:
                result.append(ImportDecl(package_name=identifiers[0], item_name=identifiers[1], loc=loc))
            elif len(identifiers) == 1:
                result.append(ImportDecl(package_name=identifiers[0], item_name="*", loc=loc))
    return result


def _extract_import_item(tree: Tree, source_file: str | None) -> ImportDecl | None:
    """Extract a single ImportDecl from an import_item tree node."""
    identifiers: list[str] = []
    is_wildcard = False
    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "MODULE_IDENTIFIER":
                identifiers.append(str(child))
            elif child.type == "IMPORT_WILDCARD":
                is_wildcard = True
    if len(identifiers) >= _MIN_NAMED_IMPORT_IDENTIFIERS:
        return ImportDecl(
            package_name=identifiers[0],
            item_name=identifiers[1],
            loc=_loc_from_tree(tree, source_file),
        )
    elif len(identifiers) == 1 and is_wildcard:
        return ImportDecl(
            package_name=identifiers[0],
            item_name="*",
            loc=_loc_from_tree(tree, source_file),
        )
    return None
