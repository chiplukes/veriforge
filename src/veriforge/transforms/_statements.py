"""Behavioral and procedural statement helpers for model transforms."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

from lark import Token, Tree

from ..model.assignments import ContinuousAssign
from ..model.base import SourceLocation
from ..model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from ..model.expressions import BinaryOp, Expression, Identifier, Literal
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
from ..model.variables import Variable
from ._tree_utils import _collect_real_number_text, _collect_text, _loc_from_tree

BuildExpressionFn = Callable[[Tree, str | None], Expression]
BuildHierarchicalIdentifierFn = Callable[[Tree, str | None], Identifier]
BuildLvalueFn = Callable[[Tree, str | None], Expression]
ExtractBlockItemVariablesFn = Callable[[Tree, str | None], list[Variable]]
ExtractNetAssignmentFn = Callable[[Tree, str | None], ContinuousAssign | None]
TokenToExpressionFn = Callable[[Token, str | None], Expression]

_ELSE = "else"
_IF = "if"
_CASE_TYPES = frozenset({"case", "casex", "casez"})
_LOOP_TYPES = frozenset({"for", "while", "forever", "repeat"})
_POSEDGE = "posedge"
_NEGEDGE = "negedge"
_LEVEL = "level"
_DEFAULT = "default"
_FOR_MIN_ASSIGNMENTS = 2


@dataclass(frozen=True)
class _StatementCallbacks:
    build_expression: BuildExpressionFn
    build_hierarchical_identifier: BuildHierarchicalIdentifierFn
    build_net_lvalue: BuildLvalueFn
    build_variable_lvalue: BuildLvalueFn
    extract_block_item_variables: ExtractBlockItemVariablesFn
    extract_net_assignment: ExtractNetAssignmentFn
    token_to_expression: TokenToExpressionFn


def _extract_always_construct(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_construct parse tree."""
    loc = _loc_from_tree(tree, source_file)
    stmt_tree = _find_child_tree(tree, "statement")
    if stmt_tree is None:
        return None

    sensitivity_list: list[SensitivityEdge] = []
    sensitivity_type: SensitivityType | None = None
    body_tree: Tree | None = None

    body: Statement | None = None
    inner = _unwrap_statement(stmt_tree)
    if inner is not None and inner.data == "procedural_timing_control_statement":
        if _has_delay_control(inner):
            body = _extract_procedural_timing_control_statement(inner, source_file, callbacks)
            sensitivity_type = SensitivityType.UNKNOWN
        else:
            sensitivity_list, body_tree = _extract_event_controlled_always_parts(inner, source_file, callbacks)
            body = _extract_statement_from_tree(body_tree, source_file, callbacks) if body_tree else None
    else:
        body_tree = stmt_tree
        body = _extract_statement_from_tree(body_tree, source_file, callbacks)

    if body is None:
        return None

    if sensitivity_type is None:
        sensitivity_type = _classify_sensitivity(sensitivity_list)

    return AlwaysBlock(
        body=body,
        sensitivity_list=sensitivity_list,
        sensitivity_type=sensitivity_type,
        loc=loc,
    )


def _find_child_tree(tree: Tree, data: str) -> Tree | None:
    for child in tree.children:
        if isinstance(child, Tree) and child.data == data:
            return child
    return None


def _has_delay_control(tree: Tree) -> bool:
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "procedural_timing_control":
            for tc_child in child.children:
                if isinstance(tc_child, Tree) and tc_child.data == "delay_control":
                    return True
    return False


def _extract_event_controlled_always_parts(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> tuple[list[SensitivityEdge], Tree | None]:
    sensitivity_list: list[SensitivityEdge] = []
    body_tree: Tree | None = None
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "procedural_timing_control":
                sensitivity_list = _extract_sensitivity_from_timing_control(child, source_file, callbacks)
            elif child.data == "statement_or_null":
                body_tree = child
    return sensitivity_list, body_tree


def _extract_always_comb_construct(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_comb_construct parse tree."""
    loc = _loc_from_tree(tree, source_file)
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "statement":
            body = _extract_statement_from_tree(child, source_file, callbacks)
            if body:
                return AlwaysBlock(
                    body=body,
                    sensitivity_list=[],
                    sensitivity_type=SensitivityType.COMBINATIONAL,
                    loc=loc,
                )
    return None


def _extract_always_ff_construct(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_ff_construct parse tree."""
    loc = _loc_from_tree(tree, source_file)
    sensitivity_list: list[SensitivityEdge] = []
    body = None
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "event_expression":
                _collect_sensitivity_edges(child, source_file, sensitivity_list, callbacks)
            elif child.data == "statement":
                body = _extract_statement_from_tree(child, source_file, callbacks)
    if body is None:
        return None
    return AlwaysBlock(
        body=body,
        sensitivity_list=sensitivity_list,
        sensitivity_type=SensitivityType.SEQUENTIAL,
        loc=loc,
    )


def _extract_always_latch_construct(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> AlwaysBlock | None:
    """Extract an AlwaysBlock from an always_latch_construct parse tree."""
    loc = _loc_from_tree(tree, source_file)
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "statement":
            body = _extract_statement_from_tree(child, source_file, callbacks)
            if body:
                return AlwaysBlock(body=body, sensitivity_list=[], sensitivity_type=SensitivityType.LATCH, loc=loc)
    return None


def _extract_initial_construct(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> InitialBlock | None:
    """Extract an InitialBlock from an initial_construct parse tree."""
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "statement":
            body = _extract_statement_from_tree(child, source_file, callbacks)
            if body:
                return InitialBlock(body=body, loc=loc)

    return None


def _extract_sensitivity_from_timing_control(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> list[SensitivityEdge]:
    """Extract sensitivity edges from a procedural_timing_control node."""
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "event_control":
            return _extract_event_control(child, source_file, callbacks)
    return []


def _extract_event_control(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> list[SensitivityEdge]:
    """Extract sensitivity edges from event_control."""
    event_expr_children = [c for c in tree.children if isinstance(c, Tree) and c.data == "event_expression"]
    if not event_expr_children:
        return []
    edges: list[SensitivityEdge] = []
    for expr_tree in event_expr_children:
        _collect_sensitivity_edges(expr_tree, source_file, edges, callbacks)
    return edges


def _collect_sensitivity_edges(
    tree: Tree,
    source_file: str | None,
    edges: list[SensitivityEdge],
    callbacks: _StatementCallbacks,
) -> None:
    """Recursively collect SensitivityEdge from an event_expression tree."""
    sub_events = [child for child in tree.children if isinstance(child, Tree) and child.data == "event_expression"]
    if sub_events:
        for sub in sub_events:
            _collect_sensitivity_edges(sub, source_file, edges, callbacks)
        return

    edge_type = _LEVEL
    signal_tree: Tree | None = None

    for child in tree.children:
        if isinstance(child, Token):
            token_str = str(child).lower()
            if token_str == _POSEDGE:
                edge_type = _POSEDGE
            elif token_str == _NEGEDGE:
                edge_type = _NEGEDGE
        elif isinstance(child, Tree) and child.data == "expression":
            signal_tree = child

    if signal_tree is not None:
        signal = callbacks.build_expression(signal_tree, source_file)
        loc = _loc_from_tree(tree, source_file)
        edges.append(SensitivityEdge(edge=edge_type, signal=signal, loc=loc))


def _classify_sensitivity(edges: list[SensitivityEdge]) -> SensitivityType:
    """Classify sensitivity type from edge list."""
    if not edges:
        return SensitivityType.COMBINATIONAL

    has_edge = any(e.edge in (_POSEDGE, _NEGEDGE) for e in edges)
    has_level = any(e.edge == _LEVEL for e in edges)

    if has_edge and not has_level:
        return SensitivityType.SEQUENTIAL
    if has_level and not has_edge:
        return SensitivityType.COMBINATIONAL
    if has_edge and has_level:
        return SensitivityType.SEQUENTIAL
    return SensitivityType.UNKNOWN


def _unwrap_statement(tree: Tree) -> Tree | None:
    """Unwrap a statement tree to find the inner semantic node."""
    if tree.data == "statement":
        for child in tree.children:
            if isinstance(child, Tree) and child.data != "attribute_instance":
                return child
    elif tree.data == "statement_or_null":
        for child in tree.children:
            if isinstance(child, Tree) and child.data == "statement":
                return _unwrap_statement(child)
            if isinstance(child, Tree):
                return child
    return tree if tree.data not in ("statement", "statement_or_null") else None


def _extract_statement_from_tree(  # noqa: PLR0911, PLR0912
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> Statement | None:
    """Extract a Statement from a parse tree node."""
    if tree.data in ("statement", "statement_or_null"):
        inner = _unwrap_statement(tree)
        if inner is None:
            return None
        return _extract_statement_from_tree(inner, source_file, callbacks)

    if tree.data == "blocking_assignment":
        return _extract_blocking_assignment(tree, source_file, callbacks)
    if tree.data == "nonblocking_assignment":
        return _extract_nonblocking_assignment(tree, source_file, callbacks)
    if tree.data == "seq_block":
        return _extract_seq_block(tree, source_file, callbacks)
    if tree.data == "par_block":
        return _extract_par_block(tree, source_file, callbacks)
    if tree.data == "conditional_statement":
        return _extract_conditional_statement(tree, source_file, callbacks)
    if tree.data == "if_else_if_statement":
        return _extract_if_else_if_statement(tree, source_file, callbacks)
    if tree.data == "case_statement":
        return _extract_case_statement(tree, source_file, callbacks)
    if tree.data == "loop_statement":
        return _extract_loop_statement(tree, source_file, callbacks)
    if tree.data == "system_task_enable":
        return _extract_system_task_enable(tree, source_file, callbacks)
    if tree.data == "task_enable":
        return _extract_task_enable(tree, source_file, callbacks)
    if tree.data == "disable_statement":
        return _extract_disable_statement(tree, source_file, callbacks)
    if tree.data == "event_trigger":
        return _extract_event_trigger(tree, source_file, callbacks)
    if tree.data == "wait_statement":
        return _extract_wait_statement(tree, source_file, callbacks)
    if tree.data == "procedural_timing_control_statement":
        return _extract_procedural_timing_control_statement(tree, source_file, callbacks)
    if tree.data == "procedural_continuous_assignments":
        return _extract_procedural_continuous_assignment(tree, source_file, callbacks)

    return None


def _extract_blocking_assignment(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> BlockingAssign:
    """Extract BlockingAssign from blocking_assignment tree."""
    lhs: Expression | None = None
    rhs: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "variable_lvalue":
                lhs = callbacks.build_variable_lvalue(child, source_file)
            elif child.data == "expression":
                rhs = callbacks.build_expression(child, source_file)

    loc = _loc_from_tree(tree, source_file)
    return BlockingAssign(
        lhs=lhs or Identifier("?"),
        rhs=rhs or Identifier("?"),
        loc=loc,
    )


def _extract_nonblocking_assignment(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> NonblockingAssign:
    """Extract NonblockingAssign from nonblocking_assignment tree."""
    lhs: Expression | None = None
    rhs: Expression | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "variable_lvalue":
                lhs = callbacks.build_variable_lvalue(child, source_file)
            elif child.data == "expression":
                rhs = callbacks.build_expression(child, source_file)

    loc = _loc_from_tree(tree, source_file)
    return NonblockingAssign(
        lhs=lhs or Identifier("?"),
        rhs=rhs or Identifier("?"),
        loc=loc,
    )


def _extract_seq_block(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> SeqBlock:
    """Extract SeqBlock from seq_block tree."""
    name: str | None = None
    local_vars: list[Variable] = []
    statements: list[Statement] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "BLOCK_IDENTIFIER":
                name = str(child)
        elif isinstance(child, Tree):
            if child.data == "block_item_declaration":
                local_vars.extend(callbacks.extract_block_item_variables(child, source_file))
            elif child.data == "statement":
                stmt = _extract_statement_from_tree(child, source_file, callbacks)
                if stmt:
                    statements.append(stmt)

    return SeqBlock(statements=statements, local_vars=local_vars, name=name, loc=loc)


def _extract_par_block(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> ParBlock:
    """Extract ParBlock from par_block tree."""
    name: str | None = None
    local_vars: list[Variable] = []
    statements: list[Statement] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if child.type == "BLOCK_IDENTIFIER":
                name = str(child)
        elif isinstance(child, Tree):
            if child.data == "block_item_declaration":
                local_vars.extend(callbacks.extract_block_item_variables(child, source_file))
            elif child.data == "statement":
                stmt = _extract_statement_from_tree(child, source_file, callbacks)
                if stmt:
                    statements.append(stmt)

    return ParBlock(statements=statements, local_vars=local_vars, name=name, loc=loc)


def _extract_conditional_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> IfStatement:
    """Extract IfStatement from conditional_statement tree."""
    condition: Expression | None = None
    then_body: Statement | None = None
    else_body: Statement | None = None
    loc = _loc_from_tree(tree, source_file)

    seen_else = False
    for child in tree.children:
        if isinstance(child, Token) and str(child).lower() == _ELSE:
            seen_else = True
        elif isinstance(child, Tree):
            if child.data == "expression" and condition is None:
                condition = callbacks.build_expression(child, source_file)
            elif child.data == "statement_or_null":
                stmt = _extract_statement_from_tree(child, source_file, callbacks)
                if not seen_else:
                    then_body = stmt
                else:
                    else_body = stmt

    return IfStatement(
        condition=condition or Identifier("?"),
        then_body=then_body,
        else_body=else_body,
        loc=loc,
    )


def _extract_if_else_if_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> IfStatement:
    """Extract IfStatement from if_else_if_statement."""
    pairs: list[tuple[Expression, Statement | None]] = []
    final_else: Statement | None = None
    loc = _loc_from_tree(tree, source_file)

    i = 0
    children = tree.children
    while i < len(children):
        child = children[i]
        if isinstance(child, Token) and str(child).lower() == _IF:
            cond, body, i = _extract_if_else_if_pair(children, i, source_file, callbacks)
            if cond:
                pairs.append((cond, body))
        elif isinstance(child, Token) and str(child).lower() == _ELSE:
            final_else, i = _extract_if_else_final_else(children, i, source_file, callbacks)
        i += 1

    if not pairs:
        return IfStatement(condition=Identifier("?"), then_body=None, loc=loc)

    result_else = final_else
    for cond, body in reversed(pairs[1:]):
        result_else = IfStatement(condition=cond, then_body=body, else_body=result_else, loc=loc)

    return IfStatement(
        condition=pairs[0][0],
        then_body=pairs[0][1],
        else_body=result_else,
        loc=loc,
    )


def _extract_if_else_if_pair(
    children,
    index: int,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> tuple[Expression | None, Statement | None, int]:
    cond = None
    body = None
    if index + 1 < len(children) and isinstance(children[index + 1], Tree):
        cond = callbacks.build_expression(children[index + 1], source_file)
        index += 1
    if index + 1 < len(children) and isinstance(children[index + 1], Tree):
        body = _extract_statement_from_tree(children[index + 1], source_file, callbacks)
        index += 1
    return cond, body, index


def _extract_if_else_final_else(
    children,
    index: int,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> tuple[Statement | None, int]:
    if index + 1 >= len(children):
        return None, index

    next_child = children[index + 1]
    if isinstance(next_child, Token) and str(next_child).lower() == _IF:
        return None, index
    if isinstance(next_child, Tree):
        return _extract_statement_from_tree(next_child, source_file, callbacks), index + 1
    return None, index


def _extract_case_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> CaseStatement:
    """Extract CaseStatement from case_statement tree."""
    case_type = "case"
    expression: Expression | None = None
    items: list[CaseItem] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            token_str = str(child).lower()
            if token_str in _CASE_TYPES:
                case_type = token_str
        elif isinstance(child, Tree):
            if child.data == "expression" and expression is None:
                expression = callbacks.build_expression(child, source_file)
            elif child.data == "case_item":
                items.append(_extract_case_item(child, source_file, callbacks))

    return CaseStatement(
        case_type=case_type,
        expression=expression or Identifier("?"),
        items=items,
        loc=loc,
    )


def _extract_case_item(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> CaseItem:
    """Extract CaseItem from case_item tree."""
    expressions: list[Expression] = []
    body: Statement | None = None
    is_default = False
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            if str(child).lower() == _DEFAULT:
                is_default = True
        elif isinstance(child, Tree):
            if child.data == "expression":
                expressions.append(callbacks.build_expression(child, source_file))
            elif child.data == "statement_or_null":
                body = _extract_statement_from_tree(child, source_file, callbacks)

    is_default, expressions = _normalize_default_case_item(is_default, expressions)
    return CaseItem(values=expressions if not is_default else None, body=body, is_default=is_default, loc=loc)


def _normalize_default_case_item(
    is_default: bool,
    expressions: list[Expression],
) -> tuple[bool, list[Expression]]:
    if is_default or len(expressions) != 1:
        return is_default, expressions

    expr = expressions[0]
    if isinstance(expr, Identifier) and expr.name == _DEFAULT:
        return True, []
    return is_default, expressions


def _extract_loop_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> Statement:
    """Extract loop statement (for/while/forever/repeat) from loop_statement tree."""
    loc = _loc_from_tree(tree, source_file)
    loop_type = ""

    for child in tree.children:
        if isinstance(child, Token):
            token_str = str(child).lower()
            if token_str in _LOOP_TYPES:
                loop_type = token_str

    if loop_type == "for":
        return _extract_for_loop(tree, source_file, loc, callbacks)
    if loop_type == "while":
        return _extract_while_loop(tree, source_file, loc, callbacks)
    if loop_type == "forever":
        return _extract_forever_loop(tree, source_file, loc, callbacks)
    if loop_type == "repeat":
        return _extract_repeat_loop(tree, source_file, loc, callbacks)

    return SeqBlock(loc=loc)


def _extract_for_loop(
    tree: Tree,
    source_file: str | None,
    loc: SourceLocation,
    callbacks: _StatementCallbacks,
) -> ForLoop:
    """Extract ForLoop from loop_statement with 'for' keyword."""
    var_assignments: list[BlockingAssign] = []
    condition: Expression | None = None
    body: Statement | None = None
    declares_var = False
    signed_var = False

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "variable_assignment":
                var_assignments.append(_extract_variable_assignment(child, source_file, callbacks))
            elif child.data == "for_variable_declaration":
                var_assignments.append(_extract_for_variable_declaration(child, source_file, callbacks))
                declares_var = True
                signed_var = _for_variable_declaration_is_signed(child)
            elif child.data == "for_step_assignment":
                var_assignments.extend(_extract_for_step_assignments(child, source_file, callbacks))
            elif child.data == "expression" and condition is None:
                condition = callbacks.build_expression(child, source_file)
            elif child.data == "statement":
                body = _extract_statement_from_tree(child, source_file, callbacks)

    init = var_assignments[0] if var_assignments else BlockingAssign(Identifier("?"), Identifier("?"))
    update = (
        var_assignments[1]
        if len(var_assignments) >= _FOR_MIN_ASSIGNMENTS
        else BlockingAssign(Identifier("?"), Identifier("?"))
    )

    return ForLoop(
        init=init,
        condition=condition or Identifier("?"),
        update=update,
        body=body,
        declares_var=declares_var,
        signed_var=signed_var,
        loc=loc,
    )


def _for_variable_declaration_is_signed(tree: Tree) -> bool:
    words = [str(child) for child in tree.children if isinstance(child, Token)]
    has_int = any(word == "int" for word in words)
    has_unsigned = any(word == "unsigned" for word in words)
    has_integer = any(word == "integer" for word in words)
    return (has_int and not has_unsigned) or has_integer


def _extract_for_step_assignments(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> list[BlockingAssign]:
    assignments: list[BlockingAssign] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "variable_assignment":
            assignments.append(_extract_variable_assignment(child, source_file, callbacks))
    return assignments


def _extract_variable_assignment(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> BlockingAssign:
    """Extract a BlockingAssign from a variable_assignment tree."""
    lhs: Expression | None = None
    rhs: Expression | None = None
    has_inc = False
    has_dec = False
    compound_op: str | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "variable_lvalue":
                lhs = callbacks.build_variable_lvalue(child, source_file)
            elif child.data == "expression":
                rhs = callbacks.build_expression(child, source_file)
        elif isinstance(child, Token):
            has_inc, has_dec, compound_op = _update_assignment_operator_flags(child, has_inc, has_dec, compound_op)

    loc = _loc_from_tree(tree, source_file)
    if lhs is None:
        lhs = Identifier("?")

    rhs = _apply_assignment_operator(lhs, rhs, has_inc, has_dec, compound_op)
    return BlockingAssign(lhs=lhs, rhs=rhs or Identifier("?"), loc=loc)


def _update_assignment_operator_flags(
    token: Token,
    has_inc: bool,
    has_dec: bool,
    compound_op: str | None,
) -> tuple[bool, bool, str | None]:
    op_text = str(token)
    if token.type == "OP_INC" or op_text == "++":
        return True, has_dec, compound_op
    if token.type == "OP_DEC" or op_text == "--":
        return has_inc, True, compound_op
    if token.type == "COMPOUND_ASSIGN":
        return has_inc, has_dec, op_text
    return has_inc, has_dec, compound_op


def _apply_assignment_operator(
    lhs: Expression,
    rhs: Expression | None,
    has_inc: bool,
    has_dec: bool,
    compound_op: str | None,
) -> Expression | None:
    if has_inc:
        return BinaryOp(op="+", left=copy.deepcopy(lhs), right=Literal(1))
    if has_dec:
        return BinaryOp(op="-", left=copy.deepcopy(lhs), right=Literal(1))
    if compound_op and rhs is not None:
        base_op = compound_op[:-1]
        return BinaryOp(op=base_op, left=copy.deepcopy(lhs), right=rhs)
    return rhs


def _extract_for_variable_declaration(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> BlockingAssign:
    """Extract a BlockingAssign from a for_variable_declaration tree."""
    var_name: str | None = None
    init_val: Expression | None = None

    for child in tree.children:
        if isinstance(child, Token) and child.type == "VARIABLE_IDENTIFIER":
            var_name = str(child)
        elif isinstance(child, Tree) and child.data == "expression":
            init_val = callbacks.build_expression(child, source_file)

    loc = _loc_from_tree(tree, source_file)
    return BlockingAssign(
        lhs=Identifier(var_name or "?"),
        rhs=init_val or Literal(0),
        loc=loc,
    )


def _extract_while_loop(
    tree: Tree,
    source_file: str | None,
    loc: SourceLocation,
    callbacks: _StatementCallbacks,
) -> WhileLoop:
    """Extract WhileLoop from loop_statement with 'while'."""
    condition: Expression | None = None
    body: Statement | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "expression" and condition is None:
                condition = callbacks.build_expression(child, source_file)
            elif child.data == "statement":
                body = _extract_statement_from_tree(child, source_file, callbacks)

    return WhileLoop(condition=condition or Identifier("?"), body=body, loc=loc)


def _extract_forever_loop(
    tree: Tree,
    source_file: str | None,
    loc: SourceLocation,
    callbacks: _StatementCallbacks,
) -> ForeverLoop:
    """Extract ForeverLoop from loop_statement with 'forever'."""
    body: Statement | None = None

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "statement":
            body = _extract_statement_from_tree(child, source_file, callbacks)

    return ForeverLoop(body=body, loc=loc)


def _extract_repeat_loop(
    tree: Tree,
    source_file: str | None,
    loc: SourceLocation,
    callbacks: _StatementCallbacks,
) -> RepeatLoop:
    """Extract RepeatLoop from loop_statement with 'repeat'."""
    count: Expression | None = None
    body: Statement | None = None

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "expression" and count is None:
                count = callbacks.build_expression(child, source_file)
            elif child.data == "statement":
                body = _extract_statement_from_tree(child, source_file, callbacks)

    return RepeatLoop(count=count or Identifier("?"), body=body, loc=loc)


def _extract_system_task_enable(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> SystemTaskCall:
    """Extract SystemTaskCall from system_task_enable tree."""
    task_name = ""
    arguments: list[Expression] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Token):
            token_type = str(child.type) if hasattr(child, "type") else ""
            if "SYSTEM_TASK_IDENTIFIER" in token_type or str(child).startswith("$"):
                task_name = str(child)
        elif isinstance(child, Tree) and child.data == "expression":
            arguments.append(callbacks.build_expression(child, source_file))

    return SystemTaskCall(task_name=task_name, arguments=arguments, loc=loc)


def _extract_task_enable(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> TaskEnable:
    """Extract TaskEnable from task_enable tree."""
    task_name = ""
    arguments: list[Expression] = []
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data in ("hierarchical_task_identifier", "hierarchical_identifier"):
                ident = callbacks.build_hierarchical_identifier(child, source_file)
                task_name = ident.name
            elif child.data == "expression":
                arguments.append(callbacks.build_expression(child, source_file))
        elif isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            task_name = str(child)

    return TaskEnable(task_name=task_name, arguments=arguments, loc=loc)


def _extract_disable_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> DisableStatement:
    """Extract DisableStatement from disable_statement tree."""
    target = ""
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree) and child.data in ("hierarchical_task_identifier", "hierarchical_identifier"):
            ident = callbacks.build_hierarchical_identifier(child, source_file)
            target = ident.name
        elif isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            target = str(child)

    return DisableStatement(target=target, loc=loc)


def _extract_event_trigger(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> EventTrigger:
    """Extract EventTrigger from event_trigger tree."""
    event = ""
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "hierarchical_event_identifier":
            ident = callbacks.build_hierarchical_identifier(child, source_file)
            event = ident.name
        elif isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            event = str(child)

    return EventTrigger(event=event, loc=loc)


def _extract_wait_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> WaitStatement:
    """Extract WaitStatement from wait_statement tree."""
    condition: Expression | None = None
    body: Statement | None = None
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "expression" and condition is None:
                condition = callbacks.build_expression(child, source_file)
            elif child.data == "statement_or_null":
                body = _extract_statement_from_tree(child, source_file, callbacks)

    return WaitStatement(condition=condition or Identifier("?"), body=body, loc=loc)


def _extract_procedural_timing_control_statement(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> Statement:
    """Extract statement with timing control."""
    loc = _loc_from_tree(tree, source_file)
    body: Statement | None = None

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "statement_or_null":
            body = _extract_statement_from_tree(child, source_file, callbacks)

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "procedural_timing_control":
            result = _extract_timing_control_statement_body(child, source_file, body, loc, callbacks)
            if result is not None:
                return result

    return body or SeqBlock(loc=loc)


def _extract_timing_control_statement_body(
    tree: Tree,
    source_file: str | None,
    body: Statement | None,
    loc: SourceLocation,
    callbacks: _StatementCallbacks,
) -> Statement | None:
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "delay_control":
                delay_expr = _extract_delay_control_value(child, source_file, callbacks)
                return DelayControl(delay=delay_expr, body=body, loc=loc)
            if child.data == "event_control":
                edges = _extract_event_control(child, source_file, callbacks)
                return EventControl(events=edges, body=body, loc=loc)
    return None


def _extract_delay_control_value(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> Expression:
    """Extract the delay expression from a delay_control tree."""
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "delay_value":
                return _build_delay_value(child, source_file, callbacks)
            if child.data == "mintypmax_expression":
                return callbacks.build_expression(child, source_file)

    text = _collect_text(tree)
    return Literal(value=text, original_text=text)


def _build_delay_value(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> Expression:
    """Build expression from a delay_value node."""
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "unsigned_number":
                return _build_unsigned_delay_literal(child, source_file)
            if child.data == "real_number":
                return _build_real_delay_literal(child, source_file)
            return callbacks.build_expression(child, source_file)
        if isinstance(child, Token):
            if child.type == "REAL_NUMBER":
                text = str(child)
                loc = _loc_from_tree(tree, source_file)
                return Literal(value=float(text), original_text=text, loc=loc)
            return callbacks.token_to_expression(child, source_file)

    text = _collect_text(tree)
    return Literal(value=text, original_text=text)


def _build_unsigned_delay_literal(tree: Tree, source_file: str | None) -> Literal:
    text = _collect_text(tree)
    loc = _loc_from_tree(tree, source_file)
    try:
        return Literal(value=int(text), original_text=text, loc=loc)
    except ValueError:
        return Literal(value=text, original_text=text, loc=loc)


def _build_real_delay_literal(tree: Tree, source_file: str | None) -> Literal:
    text = _collect_real_number_text(tree)
    loc = _loc_from_tree(tree, source_file)
    try:
        return Literal(value=float(text), original_text=text, loc=loc)
    except ValueError:
        return Literal(value=text, original_text=text, loc=loc)


def _extract_procedural_continuous_assignment(
    tree: Tree,
    source_file: str | None,
    callbacks: _StatementCallbacks,
) -> BlockingAssign:
    """Extract from procedural_continuous_assignments as a blocking assignment."""
    lhs: Expression | None = None
    rhs: Expression | None = None
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "variable_assignment":
                return _extract_variable_assignment(child, source_file, callbacks)
            if child.data == "variable_lvalue":
                lhs = callbacks.build_variable_lvalue(child, source_file)
            elif child.data == "net_lvalue":
                lhs = callbacks.build_net_lvalue(child, source_file)
            elif child.data == "expression":
                rhs = callbacks.build_expression(child, source_file)
            elif child.data == "net_assignment":
                ca = callbacks.extract_net_assignment(child, source_file)
                if ca:
                    return BlockingAssign(lhs=ca.lhs, rhs=ca.rhs, loc=loc)

    return BlockingAssign(lhs=lhs or Identifier("?"), rhs=rhs or Identifier("?"), loc=loc)
