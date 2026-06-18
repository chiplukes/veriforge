"""Function and task helpers for model transforms."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from lark import Token, Tree

from ..model.expressions import Expression, Identifier, Range
from ..model.functions import FunctionDecl, TaskDecl
from ..model.ports import Port, PortDirection
from ..model.statements import BlockingAssign, SeqBlock, Statement
from ..model.variables import Variable, VariableKind
from ._tree_utils import _loc_from_tree

BuildExpressionFn = Callable[[Tree, str | None], Expression]
BuildRangeFn = Callable[[Tree, str | None], Range | None]
ExtractIdentifiersFn = Callable[[Tree], list[str]]
ExtractStatementFn = Callable[[Tree, str | None], Statement | None]
UnwrapStatementFn = Callable[[Tree], Tree | None]

_DIRECTION_MAP = {
    "tf_input_declaration": PortDirection.INPUT,
    "tf_output_declaration": PortDirection.OUTPUT,
    "tf_inout_declaration": PortDirection.INOUT,
}

_BLOCK_KIND_TOKENS = {
    "KW_REG": VariableKind.REG,
    "KW_LOGIC": VariableKind.LOGIC,
    "KW_BIT": VariableKind.BIT,
    "KW_INTEGER": VariableKind.INTEGER,
    "KW_TIME": VariableKind.TIME,
    "KW_REAL": VariableKind.REAL,
    "KW_REALTIME": VariableKind.REALTIME,
    "KW_EVENT": VariableKind.EVENT,
    "KW_INT": VariableKind.INT,
    "KW_BYTE": VariableKind.BYTE,
    "KW_SHORTINT": VariableKind.SHORTINT,
    "KW_LONGINT": VariableKind.LONGINT,
}

_TASK_PORT_TYPE_TOKENS = {
    "KW_INTEGER": "integer",
    "KW_REAL": "real",
    "KW_REALTIME": "realtime",
    "KW_TIME": "time",
}


@dataclass(frozen=True)
class _FunctionTaskCallbacks:
    build_range: BuildRangeFn
    build_dimension: BuildRangeFn
    build_expression: BuildExpressionFn
    build_scoped_identifier: Callable[[Tree, str | None], Identifier]
    extract_identifiers: ExtractIdentifiersFn
    extract_statement: ExtractStatementFn
    unwrap_statement: UnwrapStatementFn


@dataclass
class _FunctionContext:
    name: str = ""
    is_automatic: bool = False
    return_range: Range | None = None
    return_kind: str | None = None
    ports: list[Port] = field(default_factory=list)
    local_vars: list[Variable] = field(default_factory=list)
    body_statements: list[Statement] = field(default_factory=list)


@dataclass
class _BlockVarContext:
    decl_kind: VariableKind = VariableKind.REG
    width: Range | None = None
    signed: bool = False
    type_name: str | None = None
    decl_list: Tree | None = None


@dataclass
class _TfPortSpec:
    direction: PortDirection
    width: Range | None = None
    signed: bool = False
    data_type: str | None = None
    names: list[str] = field(default_factory=list)


def _extract_function_declaration(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> FunctionDecl | None:
    """Extract a FunctionDecl from a function_declaration parse tree."""
    ctx = _FunctionContext()
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "FUNCTION_IDENTIFIER":
                ctx.name = str(child)
            elif child.type == "KW_AUTOMATIC":
                ctx.is_automatic = True
        elif isinstance(child, Tree):
            _update_function_context(ctx, child, source_file, callbacks)

    if not ctx.name:
        return None

    body = _combine_function_body(ctx.body_statements, loc)
    func = FunctionDecl(
        name=ctx.name,
        return_range=ctx.return_range,
        return_kind=ctx.return_kind,
        is_automatic=ctx.is_automatic,
        ports=ctx.ports,
        local_vars=ctx.local_vars,
        body=body,
        loc=loc,
    )
    _attach_function_children(func, ctx.ports, ctx.local_vars, body)
    return func


def _update_function_context(
    ctx: _FunctionContext,
    child: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> None:
    if child.data == "function_range_or_type":
        ctx.return_range, ctx.return_kind = _extract_function_range_or_type(child, source_file, callbacks)
    elif child.data == "function_port_list":
        ctx.ports.extend(_extract_tf_ports(child, source_file, callbacks))
    elif child.data == "function_item_declaration":
        ctx.ports.extend(_extract_tf_ports(child, source_file, callbacks))
        _extend_block_items(ctx, child, source_file, callbacks)
    elif child.data == "block_item_declaration":
        _extend_block_items(ctx, child, source_file, callbacks)
    elif child.data == "function_statement":
        stmt = _extract_function_body(child, source_file, ctx.name, callbacks)
        if stmt is not None:
            ctx.body_statements.append(stmt)


def _extend_block_items(
    ctx: _FunctionContext,
    child: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> None:
    decl_vars, init_stmts = _extract_function_block_items(child, source_file, callbacks)
    ctx.local_vars.extend(decl_vars)
    ctx.body_statements.extend(init_stmts)


def _combine_function_body(body_statements: list[Statement], loc) -> Statement | None:
    if len(body_statements) == 1:
        return body_statements[0]
    if body_statements:
        return SeqBlock(statements=body_statements, loc=loc)
    return None


def _attach_function_children(
    func: FunctionDecl,
    ports: list[Port],
    local_vars: list[Variable],
    body: Statement | None,
) -> None:
    for port in ports:
        port.parent = func
    for variable in local_vars:
        variable.parent = func
    if body:
        body.parent = func


def _extract_function_range_or_type(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> tuple[Range | None, str | None]:
    """Extract return range and/or kind from function_range_or_type."""
    return_range: Range | None = None
    return_kind: str | None = None

    for child in tree.children:
        if isinstance(child, Token) and child.type in _TASK_PORT_TYPE_TOKENS:
            return_kind = _TASK_PORT_TYPE_TOKENS[child.type]
        elif isinstance(child, Tree) and child.data == "range":
            return_range = callbacks.build_range(child, source_file)

    return return_range, return_kind


def _extract_block_item_variables(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> list[Variable]:
    """Extract variable declarations from a block_item_declaration-like node."""
    loc = _loc_from_tree(tree, source_file)
    ctx = _collect_block_var_context(tree, source_file, callbacks)
    if ctx.decl_list is None:
        return []

    variables: list[Variable] = []
    for item in ctx.decl_list.children:
        if isinstance(item, Tree) and item.data in {"block_variable_type", "block_real_type"}:
            variable = _extract_block_variable(item, loc, source_file, ctx, callbacks)
            if variable is not None:
                variables.append(variable)
    return variables


def _collect_block_var_context(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> _BlockVarContext:
    ctx = _BlockVarContext()
    for child in tree.children:
        if isinstance(child, Token):
            _update_block_var_token(ctx, child)
        elif isinstance(child, Tree):
            if child.data == "range":
                ctx.width = callbacks.build_range(child, source_file)
            elif child.data == "scoped_identifier":
                ident = callbacks.build_scoped_identifier(child, source_file)
                ctx.type_name = "::".join([*ident.hierarchy, ident.name]) if ident.hierarchy else ident.name
            elif child.data in {"list_of_block_variable_identifiers", "list_of_block_real_identifiers"}:
                ctx.decl_list = child
    return ctx


def _update_block_var_token(ctx: _BlockVarContext, token: Token) -> None:
    if token.type in _BLOCK_KIND_TOKENS:
        ctx.decl_kind = _BLOCK_KIND_TOKENS[token.type]
    elif token.type == "KW_SIGNED":
        ctx.signed = True
    elif token.type == "KW_UNSIGNED":
        ctx.signed = False
    elif token.type == "IDENTIFIER" and ctx.type_name is None:
        ctx.type_name = str(token)


def _extract_block_variable(
    item: Tree,
    loc,
    source_file: str | None,
    ctx: _BlockVarContext,
    callbacks: _FunctionTaskCallbacks,
) -> Variable | None:
    var_name = None
    init_expr = None
    dims: list[Range] = []

    for entry in item.children:
        if isinstance(entry, Token) and entry.type in {"VARIABLE_IDENTIFIER", "REAL_IDENTIFIER"}:
            var_name = str(entry)
        elif isinstance(entry, Tree) and entry.data == "dimension":
            dim = callbacks.build_dimension(entry, source_file)
            if dim is not None:
                dims.append(dim)
        elif isinstance(entry, Tree) and entry.data == "expression":
            init_expr = callbacks.build_expression(entry, source_file)

    if var_name is None:
        return None

    return Variable(
        name=var_name,
        kind=VariableKind.LOGIC if ctx.type_name is not None else ctx.decl_kind,
        width=ctx.width,
        signed=ctx.signed,
        dimensions=dims,
        initial_value=init_expr,
        type_name=ctx.type_name,
        loc=loc,
    )


def _extract_function_block_items(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> tuple[list[Variable], list[BlockingAssign]]:
    """Extract function-local variables plus any initializer statements."""
    variables = _extract_block_item_variables(tree, source_file, callbacks)
    init_statements: list[BlockingAssign] = []

    for variable in variables:
        if variable.initial_value is not None:
            init_statements.append(
                BlockingAssign(
                    lhs=Identifier(variable.name, loc=variable.loc),
                    rhs=copy.deepcopy(variable.initial_value),
                    loc=variable.loc,
                )
            )

    return variables, init_statements


def _extract_function_body(
    tree: Tree,
    source_file: str | None,
    function_name: str,
    callbacks: _FunctionTaskCallbacks,
) -> Statement | None:
    """Extract the body statement from function_statement."""
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "statement":
            return _extract_function_statement(child, source_file, function_name, callbacks)
    return None


def _extract_function_statement(
    tree: Tree,
    source_file: str | None,
    function_name: str,
    callbacks: _FunctionTaskCallbacks,
) -> Statement | None:
    inner = callbacks.unwrap_statement(tree)
    if inner is None:
        return None
    if inner.data == "return_statement":
        ret_expr = _extract_return_expression(inner, source_file, callbacks)
        loc = _loc_from_tree(inner, source_file)
        return BlockingAssign(
            lhs=Identifier(function_name, loc=loc),
            rhs=ret_expr or Identifier("?", loc=loc),
            loc=loc,
        )
    return callbacks.extract_statement(inner, source_file)


def _extract_return_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> Expression | None:
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "expression":
            return callbacks.build_expression(child, source_file)
    return None


def _extract_tf_ports(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> list[Port]:
    """Extract ports from task/function declaration nodes."""
    ports: list[Port] = []
    for node in tree.iter_subtrees():
        if node.data in _DIRECTION_MAP:
            ports.extend(_extract_tf_direction_ports(node, source_file, callbacks))
        elif node.data == "sv_function_port":
            ports.extend(_extract_sv_function_ports(node, source_file, callbacks))
    return ports


def _extract_tf_direction_ports(
    node: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> list[Port]:
    spec = _TfPortSpec(direction=_DIRECTION_MAP[node.data])
    loc = _loc_from_tree(node, source_file)

    for child in node.children:
        if isinstance(child, Token):
            if child.type == "KW_SIGNED":
                spec.signed = True
        elif isinstance(child, Tree):
            if child.data == "range":
                spec.width = callbacks.build_range(child, source_file)
            elif child.data == "list_of_port_identifiers":
                spec.names.extend(callbacks.extract_identifiers(child))
            elif child.data == "task_port_type":
                spec.data_type = _extract_task_port_type(child)

    return _ports_from_spec(spec, loc)


def _extract_sv_function_ports(
    node: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> list[Port]:
    spec = _TfPortSpec(direction=PortDirection.INPUT)
    loc = _loc_from_tree(node, source_file)

    for child in node.children:
        if isinstance(child, Token):
            if child.type == "KW_SIGNED":
                spec.signed = True
            elif child.type in {"KW_LOGIC", "KW_BIT"}:
                spec.data_type = str(child).lower().replace("kw_", "")
        elif isinstance(child, Tree):
            if child.data == "range":
                spec.width = callbacks.build_range(child, source_file)
            elif child.data == "scoped_identifier":
                ident = callbacks.build_scoped_identifier(child, source_file)
                spec.data_type = "::".join([*ident.hierarchy, ident.name]) if ident.hierarchy else ident.name
            elif child.data == "list_of_port_identifiers":
                spec.names.extend(callbacks.extract_identifiers(child))

    return _ports_from_spec(spec, loc)


def _ports_from_spec(spec: _TfPortSpec, loc) -> list[Port]:
    return [
        Port(
            name=name,
            direction=spec.direction,
            data_type=spec.data_type,
            width=spec.width,
            signed=spec.signed,
            loc=loc,
        )
        for name in spec.names
    ]


def _extract_task_port_type(tree: Tree) -> str | None:
    """Extract type from task_port_type."""
    for child in tree.children:
        if isinstance(child, Token) and child.type in _TASK_PORT_TYPE_TOKENS:
            return _TASK_PORT_TYPE_TOKENS[child.type]
    return None


def _extract_task_declaration(
    tree: Tree,
    source_file: str | None,
    callbacks: _FunctionTaskCallbacks,
) -> TaskDecl | None:
    """Extract a TaskDecl from a task_declaration parse tree."""
    name = ""
    is_automatic = False
    ports: list[Port] = []
    body: Statement | None = None
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "TASK_IDENTIFIER":
                name = str(child)
            elif child.type == "KW_AUTOMATIC":
                is_automatic = True
        elif isinstance(child, Tree):
            if child.data == "task_port_list":
                ports.extend(_extract_tf_ports(child, source_file, callbacks))
            elif child.data == "task_item_declaration":
                ports.extend(_extract_tf_ports(child, source_file, callbacks))
            elif child.data == "statement_or_null":
                body = callbacks.extract_statement(child, source_file)

    if not name:
        return None

    task = TaskDecl(name=name, is_automatic=is_automatic, ports=ports, body=body, loc=loc)
    for port in ports:
        port.parent = task
    if body:
        body.parent = task
    return task
