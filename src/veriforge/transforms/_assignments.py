"""Assignment and lvalue helpers for model transforms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from lark import Token, Tree

from ..model.assignments import ContinuousAssign
from ..model.expressions import BitSelect, Concatenation, Expression, Identifier, Range, RangeSelect
from ._tree_utils import _loc_from_tree

BuildConstantExpressionFn = Callable[[Tree, str | None], Expression]
BuildExpressionFn = Callable[[Tree, str | None], Expression]
BuildHierarchicalIdentifierFn = Callable[[Tree, str | None], Identifier]
BuildRangeFn = Callable[[Tree, str | None], Range | None]
BuildRangeSelectFn = Callable[[Expression, Tree, str | None], Expression]
TokenToExpressionFn = Callable[[Token, str | None], Expression]


@dataclass(frozen=True)
class _LvalueCallbacks:
    build_constant_expression: BuildConstantExpressionFn
    build_expression: BuildExpressionFn
    build_hierarchical_identifier: BuildHierarchicalIdentifierFn
    build_range: BuildRangeFn
    build_range_select: BuildRangeSelectFn
    token_to_expression: TokenToExpressionFn


def _extract_continuous_assign(
    tree: Tree,
    source_file: str | None,
    callbacks: _LvalueCallbacks,
) -> list[ContinuousAssign]:
    """Extract ContinuousAssign objects from a continuous_assign subtree."""
    assigns: list[ContinuousAssign] = []

    for child in tree.iter_subtrees():
        if child.data == "net_assignment":
            ca = _extract_net_assignment(child, source_file, callbacks)
            if ca:
                assigns.append(ca)

    return assigns


def _extract_net_assignment(
    tree: Tree,
    source_file: str | None,
    callbacks: _LvalueCallbacks,
) -> ContinuousAssign | None:
    """Extract a single ContinuousAssign from net_assignment."""
    lhs: Expression | None = None
    rhs: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "net_lvalue":
                lhs = _build_net_lvalue(child, source_file, callbacks)
            elif child.data == "expression":
                rhs = callbacks.build_expression(child, source_file)

    if lhs is not None and rhs is not None:
        loc = _loc_from_tree(tree, source_file)
        return ContinuousAssign(lhs=lhs, rhs=rhs, loc=loc)
    return None


def _build_net_lvalue(tree: Tree, source_file: str | None, callbacks: _LvalueCallbacks) -> Expression:
    """Build an Expression from a net_lvalue subtree."""
    net_lvalue_children = [child for child in tree.children if isinstance(child, Tree) and child.data == "net_lvalue"]
    if net_lvalue_children:
        parts = [_build_net_lvalue(child, source_file, callbacks) for child in net_lvalue_children]
        loc = _loc_from_tree(tree, source_file)
        return Concatenation(parts=parts, loc=loc)

    ident: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "hierarchical_net_identifier":
                ident = callbacks.build_hierarchical_identifier(child, source_file)
            elif child.data == "constant_expression":
                if ident is None:
                    ident = Identifier("?")
                idx = callbacks.build_constant_expression(child, source_file)
                loc = _loc_from_tree(child, source_file)
                ident = BitSelect(target=ident, index=idx, loc=loc)
            elif child.data == "constant_range_expression":
                if ident is None:
                    ident = Identifier("?")
                return callbacks.build_range_select(ident, child, source_file)
            elif ident is None:
                return callbacks.build_expression(child, source_file)
        elif isinstance(child, Token) and "IDENTIFIER" in str(child.type) and ident is None:
            ident = callbacks.token_to_expression(child, source_file)

    if ident is None:
        return Identifier("?")

    return ident


def _build_variable_lvalue(tree: Tree, source_file: str | None, callbacks: _LvalueCallbacks) -> Expression:
    """Build an Expression from a variable_lvalue subtree."""
    lvalue_children = [child for child in tree.children if isinstance(child, Tree) and child.data == "variable_lvalue"]
    if lvalue_children:
        parts = [_build_variable_lvalue(child, source_file, callbacks) for child in lvalue_children]
        loc = _loc_from_tree(tree, source_file)
        return Concatenation(parts=parts, loc=loc)

    base_expr: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "hierarchical_variable_identifier":
                base_expr = _build_hierarchical_variable_identifier(child, source_file, callbacks)
            elif child.data == "expression":
                if base_expr is None:
                    base_expr = Identifier("?")
                idx = callbacks.build_expression(child, source_file)
                loc = _loc_from_tree(child, source_file)
                base_expr = BitSelect(target=base_expr, index=idx, loc=loc)
            elif child.data == "range_expression":
                if base_expr is None:
                    base_expr = Identifier("?")
                return _apply_range_or_select(base_expr, child, source_file, callbacks)

    if base_expr is None:
        base_expr = Identifier("?")

    return base_expr


def _build_hierarchical_variable_identifier(
    tree: Tree,
    source_file: str | None,
    callbacks: _LvalueCallbacks,
) -> Identifier:
    """Build Identifier from hierarchical_variable_identifier."""
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "hierarchical_identifier":
            return callbacks.build_hierarchical_identifier(child, source_file)
        if isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            loc = _loc_from_tree(tree, source_file)
            return Identifier(name=str(child), loc=loc)
    return Identifier("?")


def _apply_range_or_select(
    base: Expression,
    range_tree: Tree,
    source_file: str | None,
    callbacks: _LvalueCallbacks,
) -> Expression:
    """Apply a range_expression or expression to a base to create BitSelect or RangeSelect."""
    loc = _loc_from_tree(range_tree, source_file)

    if range_tree.data == "range_expression":
        for child in range_tree.children:
            if isinstance(child, Tree):
                if child.data == "expression":
                    index = callbacks.build_expression(child, source_file)
                    return BitSelect(target=base, index=index, loc=loc)
                if child.data == "range":
                    range_value = callbacks.build_range(child, source_file)
                    if range_value:
                        return RangeSelect(target=base, msb=range_value.msb, lsb=range_value.lsb, loc=loc)
                return callbacks.build_range_select(base, range_tree, source_file)
    elif range_tree.data == "expression":
        index = callbacks.build_expression(range_tree, source_file)
        return BitSelect(target=base, index=index, loc=loc)

    return base
