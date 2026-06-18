"""Generate construct helpers for model transforms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from lark import Token, Tree

from ..model.assignments import ContinuousAssign
from ..model.base import VerilogNode
from ..model.behavioral import AlwaysBlock, InitialBlock
from ..model.expressions import Expression, Identifier
from ..model.functions import FunctionDecl, TaskDecl
from ..model.generate import GenerateBlock, GenerateCase, GenerateCaseItem, GenerateFor, GenerateIf, GenvarDecl
from ..model.instances import Instance
from ..model.nets import Net
from ..model.package import ImportDecl
from ..model.parameters import Parameter
from ..model.ports import Port
from ..model.specify import SpecifyBlock
from ..model.sv_types import TypedefDecl
from ..model.variables import Variable
from ._tree_utils import _loc_from_tree

BuildExpressionFn = Callable[[Tree, str | None], Expression]

_ASSIGN_OP_TYPES = frozenset({"OP_ADD_ASSIGN", "OP_SUB_ASSIGN", "OP_MUL_ASSIGN", "OP_DIV_ASSIGN", "OP_MOD_ASSIGN"})
_INC_DEC_TYPES = frozenset({"OP_INC", "OP_DEC"})
_MIN_THEN_ELSE_BLOCKS = 2

_OP_TYPE_TO_STR = {
    "OP_ADD_ASSIGN": "+=",
    "OP_SUB_ASSIGN": "-=",
    "OP_MUL_ASSIGN": "*=",
    "OP_DIV_ASSIGN": "/=",
    "OP_MOD_ASSIGN": "%=",
    "OP_INC": "++",
    "OP_DEC": "--",
}


@dataclass
class _GenerateModuleItems:
    parameters: list[Parameter] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
    continuous_assigns: list[ContinuousAssign] = field(default_factory=list)
    always_blocks: list[AlwaysBlock] = field(default_factory=list)
    initial_blocks: list[InitialBlock] = field(default_factory=list)
    functions: list[FunctionDecl] = field(default_factory=list)
    tasks: list[TaskDecl] = field(default_factory=list)
    generate_blocks: list[VerilogNode] = field(default_factory=list)
    specify_blocks: list[SpecifyBlock] = field(default_factory=list)
    typedefs: list[TypedefDecl] = field(default_factory=list)
    imports: list[ImportDecl] = field(default_factory=list)


ExtractModuleItemsFn = Callable[[Tree, str | None, _GenerateModuleItems], None]


@dataclass(frozen=True)
class _GenerateCallbacks:
    build_constant_expression: BuildExpressionFn
    build_genvar_expression: BuildExpressionFn
    extract_module_items: ExtractModuleItemsFn


def _extract_genvar_declaration(tree: Tree, source_file: str | None) -> GenvarDecl:
    """Extract a GenvarDecl from a genvar_declaration parse tree."""
    names: list[str] = []
    loc = _loc_from_tree(tree, source_file)

    for node in tree.iter_subtrees():
        if node.data == "list_of_genvar_identifiers":
            for child in node.children:
                if isinstance(child, Token) and child.type == "GENVAR_IDENTIFIER":
                    names.append(str(child))

    return GenvarDecl(names=names, loc=loc)


def _extract_loop_generate(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> GenerateFor | None:
    """Extract a GenerateFor from a loop_generate_construct parse tree."""
    genvar: str = ""
    genvar_local = False
    init_value: Expression | None = None
    condition: Expression | None = None
    update: Expression | None = None
    update_op = "="
    body: GenerateBlock | None = None
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "genvar_initialization":
                genvar, init_value, genvar_local = _extract_genvar_init(child, source_file, callbacks)
            elif child.data == "genvar_expression":
                condition = callbacks.build_genvar_expression(child, source_file)
            elif child.data == "genvar_iteration":
                update_op, update = _extract_genvar_iteration(child, source_file, callbacks)
            elif child.data == "generate_block":
                body = _extract_generate_block(child, source_file, callbacks)

    if not genvar or init_value is None or condition is None or body is None:
        return None

    gen = GenerateFor(
        genvar=genvar,
        init_value=init_value,
        condition=condition,
        update=update,
        body=body,
        update_op=update_op,
        genvar_local=genvar_local,
        loc=loc,
    )
    body.parent = gen
    return gen


def _extract_genvar_init(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> tuple[str, Expression | None, bool]:
    """Extract genvar name, initial value, and local flag from genvar_initialization."""
    genvar = ""
    init_value: Expression | None = None
    genvar_local = False

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "GENVAR_IDENTIFIER":
                genvar = str(child)
            elif child.type == "KW_GENVAR":
                genvar_local = True
        elif isinstance(child, Tree) and child.data == "constant_expression":
            init_value = callbacks.build_constant_expression(child, source_file)

    return genvar, init_value, genvar_local


def _extract_genvar_iteration(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> tuple[str, Expression | None]:
    """Extract iteration operator and expression from genvar_iteration."""
    children = tree.children
    tokens = [(i, c) for i, c in enumerate(children) if isinstance(c, Token)]
    trees = [c for c in children if isinstance(c, Tree)]

    genvar_idx = -1
    for i, tok in tokens:
        if tok.type == "GENVAR_IDENTIFIER":
            genvar_idx = i
            break

    for i, tok in tokens:
        if tok.type in _INC_DEC_TYPES:
            op_str = _OP_TYPE_TO_STR[tok.type]
            if genvar_idx > i:
                return f"pre{op_str}", None
            return f"post{op_str}", None

    for _i, tok in tokens:
        if tok.type in _ASSIGN_OP_TYPES:
            return _OP_TYPE_TO_STR[tok.type], _find_genvar_iteration_expression(trees, source_file, callbacks)

    return "=", _find_genvar_iteration_expression(trees, source_file, callbacks)


def _find_genvar_iteration_expression(
    trees: list[Tree],
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> Expression | None:
    for tree in trees:
        if tree.data == "genvar_expression":
            return callbacks.build_genvar_expression(tree, source_file)
    return None


def _extract_generate_block(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> GenerateBlock:
    """Extract a GenerateBlock from generate_block or generate_block_or_null."""
    if tree.data == "generate_block_or_null":
        for child in tree.children:
            if isinstance(child, Tree) and child.data == "generate_block":
                return _extract_generate_block(child, source_file, callbacks)
        return GenerateBlock(loc=_loc_from_tree(tree, source_file))

    name = _extract_generate_block_name(tree)
    items = _extract_generate_block_items(tree, source_file, callbacks)
    return GenerateBlock(name=name, items=items, loc=_loc_from_tree(tree, source_file))


def _extract_generate_block_name(tree: Tree) -> str | None:
    has_begin = any(isinstance(child, Token) and child.type == "KW_BEGIN" for child in tree.children)
    if not has_begin:
        return None

    for child in tree.children:
        if isinstance(child, Token) and child.type == "GENERATE_BLOCK_IDENTIFIER":
            return str(child)
    return None


def _extract_generate_block_items(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> list[VerilogNode]:
    """Extract module items from inside a generate block."""
    items = _GenerateModuleItems()

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "module_or_generate_item":
            callbacks.extract_module_items(child, source_file, items)

    return [
        *items.typedefs,
        *items.nets,
        *items.variables,
        *items.parameters,
        *items.continuous_assigns,
        *items.instances,
        *items.always_blocks,
        *items.initial_blocks,
        *items.functions,
        *items.tasks,
        *items.generate_blocks,
    ]


def _extract_if_generate(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> GenerateIf | None:
    """Extract a GenerateIf from an if_generate_construct parse tree."""
    condition: Expression | None = None
    then_body: GenerateBlock | None = None
    else_body: GenerateBlock | None = None
    loc = _loc_from_tree(tree, source_file)

    blocks_found: list[GenerateBlock] = []

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "constant_expression":
                condition = callbacks.build_constant_expression(child, source_file)
            elif child.data == "generate_block_or_null":
                blocks_found.append(_extract_generate_block(child, source_file, callbacks))

    if condition is None:
        return None

    if blocks_found:
        then_body = blocks_found[0]
    if len(blocks_found) >= _MIN_THEN_ELSE_BLOCKS:
        else_body = blocks_found[1]

    gen = GenerateIf(condition=condition, then_body=then_body, else_body=else_body, loc=loc)
    if then_body:
        then_body.parent = gen
    if else_body:
        else_body.parent = gen
    return gen


def _extract_case_generate(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> GenerateCase | None:
    """Extract a GenerateCase from a case_generate_construct parse tree."""
    expression: Expression | None = None
    items: list[GenerateCaseItem] = []
    qualifier: str | None = None
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token) and child.type in ("KW_UNIQUE", "KW_UNIQUE0", "KW_PRIORITY"):
            qualifier = str(child)
        elif isinstance(child, Tree):
            if child.data == "case_qualifier":
                qualifier = _extract_case_qualifier(child) or qualifier
            elif child.data == "constant_expression" and expression is None:
                expression = callbacks.build_constant_expression(child, source_file)
            elif child.data == "case_generate_item":
                item = _extract_case_generate_item(child, source_file, callbacks)
                if item:
                    items.append(item)

    if expression is None:
        return None

    gen = GenerateCase(expression=expression, items=items, qualifier=qualifier, loc=loc)
    for item in items:
        item.parent = gen
    return gen


def _extract_case_qualifier(tree: Tree) -> str | None:
    for child in tree.children:
        if isinstance(child, Token):
            return str(child)
    return None


def _extract_case_generate_item(
    tree: Tree,
    source_file: str | None,
    callbacks: _GenerateCallbacks,
) -> GenerateCaseItem | None:
    """Extract a GenerateCaseItem from a case_generate_item parse tree."""
    is_default = False
    values: list[Expression] = []
    body: GenerateBlock | None = None
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "KW_DEFAULT":
                is_default = True
        elif isinstance(child, Tree):
            if child.data == "constant_expression":
                values.append(callbacks.build_constant_expression(child, source_file))
            elif child.data == "generate_block_or_null":
                body = _extract_generate_block(child, source_file, callbacks)

    is_default, values = _normalize_default_case_item(is_default, values)
    return GenerateCaseItem(
        values=values if not is_default else [],
        is_default=is_default,
        body=body,
        loc=loc,
    )


def _normalize_default_case_item(
    is_default: bool,
    values: list[Expression],
) -> tuple[bool, list[Expression]]:
    if is_default or len(values) != 1:
        return is_default, values

    value = values[0]
    if isinstance(value, Identifier) and value.name == "default":
        return True, []
    return is_default, values
