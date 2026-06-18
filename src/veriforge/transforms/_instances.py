"""Instance-oriented helpers for model transforms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lark import Token, Tree

from ..model.expressions import Expression, Range
from ..model.instances import Instance, ParameterBinding, PortConnection
from ._tree_utils import _loc_from_tree

BuildExpressionFn = Callable[[Tree, str | None], Expression]
BuildNetLvalueFn = Callable[[Tree, str | None], Expression]
BuildRangeFn = Callable[[Tree, str | None], Range | None]
TokenToExpressionFn = Callable[[Token, str | None], Expression]

_GATE_TYPE_RULES = frozenset(
    {
        "n_input_gatetype",
        "n_output_gatetype",
        "enable_gatetype",
        "cmos_switchtype",
        "mos_switchtype",
        "pass_switchtype",
        "pass_en_switchtype",
    }
)

_GATE_INSTANCE_RULES = frozenset(
    {
        "n_input_gate_instance",
        "n_output_gate_instance",
        "enable_gate_instance",
        "cmos_switch_instance",
        "mos_switch_instance",
        "pass_switch_instance",
        "pass_enable_switch_instance",
        "pull_gate_instance",
    }
)

_GATE_TERMINAL_RULES = frozenset(
    {
        "output_terminal",
        "input_terminal",
        "enable_terminal",
        "inout_terminal",
        "ncontrol_terminal",
        "pcontrol_terminal",
    }
)


@dataclass(frozen=True)
class _InstanceContext:
    module_name: str
    has_parameter_override: bool
    param_bindings: list[ParameterBinding]
    source_file: str | None
    build_range: BuildRangeFn
    build_expression: BuildExpressionFn


@dataclass(frozen=True)
class _PrimitiveCallbacks:
    build_range: BuildRangeFn
    build_expression: BuildExpressionFn
    build_net_lvalue: BuildNetLvalueFn
    token_to_expression: TokenToExpressionFn


@dataclass(frozen=True)
class _PrimitiveContext:
    source_file: str | None
    callbacks: _PrimitiveCallbacks


def _extract_module_instantiation(
    tree: Tree,
    source_file: str | None,
    build_range: BuildRangeFn,
    build_expression: BuildExpressionFn,
    token_to_expression: TokenToExpressionFn,
) -> list[Instance]:
    """Extract Instance objects from a module_instantiation subtree."""
    instances: list[Instance] = []
    module_name = ""
    has_parameter_override = False
    param_bindings: list[ParameterBinding] = []

    for child in tree.children:
        if isinstance(child, Token) and child.type == "MODULE_IDENTIFIER":
            module_name = str(child)
        elif isinstance(child, Tree):
            if child.data == "parameter_value_assignment":
                has_parameter_override = True
                param_bindings = _extract_parameter_value_assignment(
                    child,
                    source_file,
                    build_expression,
                    token_to_expression,
                )
            elif child.data == "module_instance":
                ctx = _InstanceContext(
                    module_name=module_name,
                    has_parameter_override=has_parameter_override,
                    param_bindings=param_bindings,
                    source_file=source_file,
                    build_range=build_range,
                    build_expression=build_expression,
                )
                inst = _extract_single_instance(child, ctx)
                if inst:
                    instances.append(inst)

    return instances


def _extract_single_instance(tree: Tree, ctx: _InstanceContext) -> Instance | None:
    """Extract a single Instance from a module_instance subtree."""
    instance_name = ""
    instance_array: Range | None = None
    port_connections: list[PortConnection] = []

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "name_of_module_instance":
                instance_name, instance_array = _extract_instance_name_and_array(child, ctx)
            elif child.data == "list_of_port_connections":
                port_connections = _extract_port_connections(child, ctx.source_file, ctx.build_expression)

    if not instance_name:
        return None

    loc = _loc_from_tree(tree, ctx.source_file)
    return Instance(
        module_name=ctx.module_name,
        instance_name=instance_name,
        instance_array=instance_array,
        has_parameter_override=ctx.has_parameter_override,
        parameter_bindings=list(ctx.param_bindings),
        port_connections=port_connections,
        loc=loc,
    )


def _extract_instance_name_and_array(tree: Tree, ctx: _InstanceContext) -> tuple[str, Range | None]:
    instance_name = ""
    instance_array = None
    for child in tree.children:
        if isinstance(child, Token) and child.type == "MODULE_INSTANCE_IDENTIFIER":
            instance_name = str(child)
        elif isinstance(child, Tree) and child.data == "range":
            instance_array = ctx.build_range(child, ctx.source_file)
    return instance_name, instance_array


def _extract_parameter_value_assignment(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
    token_to_expression: TokenToExpressionFn,
) -> list[ParameterBinding]:
    """Extract parameter bindings from parameter_value_assignment."""
    bindings: list[ParameterBinding] = []

    for child in tree.iter_subtrees():
        if child.data == "named_parameter_assignment":
            bindings.append(_extract_named_parameter_assignment(child, source_file, build_expression))
        elif child.data == "ordered_parameter_assignment":
            bindings.append(
                _extract_ordered_parameter_assignment(child, source_file, build_expression, token_to_expression)
            )

    return bindings


def _extract_named_parameter_assignment(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
) -> ParameterBinding:
    name: str | None = None
    value: Expression | None = None
    for child in tree.children:
        if isinstance(child, Token) and child.type == "PARAMETER_IDENTIFIER":
            name = str(child)
        elif isinstance(child, Tree) and child.data == "mintypmax_expression":
            value = build_expression(child, source_file)
    loc = _loc_from_tree(tree, source_file)
    return ParameterBinding(name=name, value=value, loc=loc)


def _extract_ordered_parameter_assignment(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
    token_to_expression: TokenToExpressionFn,
) -> ParameterBinding:
    value = None
    for child in tree.children:
        if isinstance(child, Tree):
            value = build_expression(child, source_file)
        elif isinstance(child, Token):
            value = token_to_expression(child, source_file)
    loc = _loc_from_tree(tree, source_file)
    return ParameterBinding(name=None, value=value, loc=loc)


def _extract_port_connections(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
) -> list[PortConnection]:
    """Extract port connections from list_of_port_connections."""
    connections: list[PortConnection] = []

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "named_port_connection":
                connections.append(_extract_named_port_connection(child, source_file, build_expression))
            elif child.data == "ordered_port_connection":
                connections.append(_extract_ordered_port_connection(child, source_file, build_expression))

    return connections


def _extract_named_port_connection(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
) -> PortConnection:
    """Extract a named port connection."""
    port_name: str | None = None
    expression: Expression | None = None

    for child in tree.children:
        if isinstance(child, Token) and child.type == "PORT_IDENTIFIER":
            port_name = str(child)
        elif isinstance(child, Tree) and child.data == "expression":
            expression = build_expression(child, source_file)

    loc = _loc_from_tree(tree, source_file)
    return PortConnection(port_name=port_name, expression=expression, is_named=True, loc=loc)


def _extract_ordered_port_connection(
    tree: Tree,
    source_file: str | None,
    build_expression: BuildExpressionFn,
) -> PortConnection:
    """Extract an ordered positional port connection."""
    expression: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "expression":
                expression = build_expression(child, source_file)
            elif child.data != "attribute_instance":
                expression = build_expression(child, source_file)

    loc = _loc_from_tree(tree, source_file)
    return PortConnection(port_name=None, expression=expression, is_named=False, loc=loc)


def _extract_udp_as_instance(
    tree: Tree,
    source_file: str | None,
    callbacks: _PrimitiveCallbacks,
) -> list[Instance]:
    """Extract Instance objects from udp_instantiation fallback parses."""
    instances: list[Instance] = []
    module_name = ""
    ctx = _PrimitiveContext(source_file=source_file, callbacks=callbacks)

    for child in tree.children:
        if isinstance(child, Token) and child.type in ("UDP_IDENTIFIER", "MODULE_IDENTIFIER"):
            module_name = str(child)
        elif isinstance(child, Tree) and child.data == "udp_instance":
            inst = _extract_udp_instance_node(child, module_name, ctx)
            if inst:
                instances.append(inst)

    return instances


def _extract_udp_instance_node(tree: Tree, module_name: str, ctx: _PrimitiveContext) -> Instance | None:
    """Extract a single Instance from a udp_instance node."""
    instance_name = ""
    instance_array: Range | None = None
    port_connections: list[PortConnection] = []

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "name_of_udp_instance":
                instance_name, instance_array = _extract_primitive_instance_name(child, ctx)
            elif child.data in ("output_terminal", "input_terminal"):
                port_connections.append(_extract_terminal_port_connection(child, ctx))
        elif isinstance(child, Token) and "IDENTIFIER" in str(child.type) and not instance_name:
            instance_name = str(child)

    if not instance_name:
        return None

    loc = _loc_from_tree(tree, ctx.source_file)
    return Instance(
        module_name=module_name,
        instance_name=instance_name,
        instance_array=instance_array,
        parameter_bindings=[],
        port_connections=port_connections,
        loc=loc,
    )


def _extract_gate_instantiation(
    tree: Tree,
    source_file: str | None,
    callbacks: _PrimitiveCallbacks,
) -> list[Instance]:
    """Extract Instance objects from gate_instantiation nodes."""
    instances: list[Instance] = []
    gate_type = ""
    ctx = _PrimitiveContext(source_file=source_file, callbacks=callbacks)

    for child in tree.children:
        if isinstance(child, Token) and child.type in ("KW_PULLUP", "KW_PULLDOWN"):
            gate_type = str(child).lower()
        elif isinstance(child, Tree):
            if child.data in _GATE_TYPE_RULES:
                gate_type = _extract_gate_type(child)
            elif child.data in _GATE_INSTANCE_RULES:
                inst = _extract_gate_instance_node(child, gate_type, ctx)
                if inst:
                    instances.append(inst)

    return instances


def _extract_gate_type(tree: Tree) -> str:
    for child in tree.children:
        if isinstance(child, Token):
            return str(child).lower()
    return ""


def _extract_gate_instance_node(tree: Tree, gate_type: str, ctx: _PrimitiveContext) -> Instance | None:
    """Extract a single Instance from a gate instance node."""
    instance_name = ""
    instance_array: Range | None = None
    port_connections: list[PortConnection] = []

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "name_of_gate_instance":
                instance_name, instance_array = _extract_primitive_instance_name(child, ctx)
            elif child.data in _GATE_TERMINAL_RULES:
                port_connections.append(_extract_terminal_port_connection(child, ctx))

    loc = _loc_from_tree(tree, ctx.source_file)
    return Instance(
        module_name=gate_type,
        instance_name=instance_name,
        instance_array=instance_array,
        parameter_bindings=[],
        port_connections=port_connections,
        loc=loc,
    )


def _extract_primitive_instance_name(tree: Tree, ctx: _PrimitiveContext) -> tuple[str, Range | None]:
    instance_name = ""
    instance_array = None
    for child in tree.children:
        if isinstance(child, Token):
            instance_name = str(child)
        elif isinstance(child, Tree) and child.data == "range":
            instance_array = ctx.callbacks.build_range(child, ctx.source_file)
    return instance_name, instance_array


def _extract_terminal_port_connection(tree: Tree, ctx: _PrimitiveContext) -> PortConnection:
    expr = _extract_terminal_expression(tree, ctx)
    loc = _loc_from_tree(tree, ctx.source_file)
    return PortConnection(port_name=None, expression=expr, is_named=False, loc=loc)


def _extract_terminal_expression(tree: Tree, ctx: _PrimitiveContext) -> Expression | None:
    """Extract expression from a primitive gate or UDP terminal."""
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "expression":
                return ctx.callbacks.build_expression(child, ctx.source_file)
            if child.data == "net_lvalue":
                return ctx.callbacks.build_net_lvalue(child, ctx.source_file)
            return ctx.callbacks.build_expression(child, ctx.source_file)
        if isinstance(child, Token):
            return ctx.callbacks.token_to_expression(child, ctx.source_file)
    return None
