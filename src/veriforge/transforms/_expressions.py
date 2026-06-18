"""Shared expression-building helpers for model transforms."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lark import Token, Tree

from ..model.base import SourceLocation
from ..model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from ._tree_utils import _SOURCE_TEXT_CACHE, _collect_real_number_text, _collect_text, _loc_from_tree

_BINARY_EXPR_CHILD_COUNT = 3
_DEFAULT_PRECEDENCE = 6
_POWER_OPERATOR = "**"
_SCOPED_IDENTIFIER_TOKEN_COUNT = 2
_TERNARY_EXPR_CHILD_COUNT = 3
_GENVAR_TERNARY_EXPR_CHILD_COUNT = 5
_TERNARY_CONDITION_INDEX = 0
_TERNARY_TRUE_INDEX = 2
_TERNARY_FALSE_INDEX = 4

# Lower number = higher precedence = binds tighter.
_VERILOG_PRECEDENCE: dict[str, int] = {
    _POWER_OPERATOR: 1,
    "*": 2,
    "/": 2,
    "%": 2,
    "+": 3,
    "-": 3,
    "<<": 4,
    ">>": 4,
    "<<<": 4,
    ">>>": 4,
    "<": 5,
    "<=": 5,
    ">": 5,
    ">=": 5,
    "==": 6,
    "!=": 6,
    "===": 6,
    "!==": 6,
    "&": 7,
    "^": 8,
    "^~": 8,
    "~^": 8,
    "|": 9,
    "&&": 10,
    "||": 11,
}

_VERILOG_OPERATORS = frozenset(
    (
        "+",
        "-",
        "*",
        "/",
        "%",
        _POWER_OPERATOR,
        "==",
        "!=",
        "===",
        "!==",
        "<",
        ">",
        "<=",
        ">=",
        "&&",
        "||",
        "&",
        "|",
        "^",
        "~^",
        "^~",
        "<<",
        ">>",
        "<<<",
        ">>>",
        "~",
        "!",
        "~&",
        "~|",
    )
)

BuildChildFn = Callable[[Tree | Token, str | None], Expression]
BuildExpressionFn = Callable[[Tree, str | None], Expression]


@dataclass(frozen=True)
class _ExpressionCallbacks:
    build_constant_expression: BuildExpressionFn
    build_expression: BuildExpressionFn
    build_expr_inner: BuildExpressionFn


@dataclass
class _RangeSelectParts:
    msb: Expression | None = None
    lsb: Expression | None = None
    base: Expression | None = None
    width: Expression | None = None


def _extract_operator(node: Tree | Token) -> str | None:
    """Extract operator string from an operator tree or token.

    Only returns a value for explicit operator tokens/trees.  Non-operator
    Tree nodes (e.g. constant_expression in a ternary) intentionally return
    None to prevent mis-identification of operands as operators.
    """
    if isinstance(node, Token):
        text = str(node)
        if text in _VERILOG_OPERATORS:
            return text
    elif isinstance(node, Tree):
        if node.data in ("binary_operator", "unary_operator"):
            return _collect_text(node).strip()
    return None


def _flatten_binary_chain(  # noqa: PLR0913
    tree: Tree,
    operands: list[Tree | Token],
    operators: list[str],
    locs: list[SourceLocation],
    source_file: str | None,
    rule_name: str,
) -> None:
    """Flatten a chain of binary expression nodes sharing *rule_name*.

    Only descends into children whose ``data`` matches *rule_name* and that have
    the binary-op shape. Everything else is treated as a leaf operand.
    """
    children = tree.children
    if tree.data != rule_name or len(children) != _BINARY_EXPR_CHILD_COUNT:
        operands.append(tree)
        return

    left_node, op_node, right_node = children
    op_str = _extract_operator(op_node)
    if not op_str:
        operands.append(tree)
        return

    if (
        isinstance(left_node, Tree)
        and left_node.data == rule_name
        and len(left_node.children) == _BINARY_EXPR_CHILD_COUNT
    ):
        _flatten_binary_chain(left_node, operands, operators, locs, source_file, rule_name)
    else:
        operands.append(left_node)

    operators.append(op_str)
    locs.append(_loc_from_tree(tree, source_file))

    if (
        isinstance(right_node, Tree)
        and right_node.data == rule_name
        and len(right_node.children) == _BINARY_EXPR_CHILD_COUNT
    ):
        _flatten_binary_chain(right_node, operands, operators, locs, source_file, rule_name)
    else:
        operands.append(right_node)


def _rebuild_with_precedence(
    operands: list[Tree | Token],
    operators: list[str],
    locs: list[SourceLocation],
    build_child_fn: BuildChildFn,
    source_file: str | None,
) -> Expression:
    """Rebuild a flat operand/operator list into a correctly-nested BinaryOp tree."""
    output: list[Expression] = [build_child_fn(operands[0], source_file)]
    op_stack: list[tuple[str, SourceLocation]] = []

    for i, op in enumerate(operators):
        prec = _VERILOG_PRECEDENCE.get(op, _DEFAULT_PRECEDENCE)
        while op_stack:
            top_op, top_loc = op_stack[-1]
            top_prec = _VERILOG_PRECEDENCE.get(top_op, _DEFAULT_PRECEDENCE)
            if top_op == _POWER_OPERATOR:
                should_pop = top_prec < prec
            else:
                should_pop = top_prec <= prec
            if not should_pop:
                break
            op_stack.pop()
            right = output.pop()
            left = output.pop()
            output.append(BinaryOp(op=top_op, left=left, right=right, loc=top_loc))

        op_stack.append((op, locs[i]))
        output.append(build_child_fn(operands[i + 1], source_file))

    while op_stack:
        op, loc = op_stack.pop()
        right = output.pop()
        left = output.pop()
        output.append(BinaryOp(op=op, left=left, right=right, loc=loc))

    return output[0]


def _build_binary_chain(
    tree: Tree,
    source_file: str | None,
    rule_name: str,
    build_child_fn: BuildChildFn,
) -> Expression | None:
    """Build a binary-op chain for *rule_name* with correct Verilog precedence."""
    children = tree.children
    if tree.data != rule_name or len(children) != _BINARY_EXPR_CHILD_COUNT:
        return None
    if not _extract_operator(children[1]):
        return None

    operands: list[Tree | Token] = []
    operators: list[str] = []
    locs: list[SourceLocation] = []
    _flatten_binary_chain(tree, operands, operators, locs, source_file, rule_name)

    if len(operators) == 1:
        left = build_child_fn(operands[0], source_file)
        right = build_child_fn(operands[1], source_file)
        return BinaryOp(op=operators[0], left=left, right=right, loc=locs[0])

    return _rebuild_with_precedence(operands, operators, locs, build_child_fn, source_file)


def _build_sv_fill_literal(tree: Tree, source_file: str | None, loc: SourceLocation) -> Literal:
    """Build a Literal from a SV fill literal ('0, '1, 'x, 'z)."""
    text = _collect_text(tree).strip()
    fill_char = text[-1].lower() if text else "0"
    if fill_char == "0":
        return Literal(value=0, original_text=text, loc=loc)
    elif fill_char == "1":
        return Literal(value=-1, original_text=text, loc=loc)
    elif fill_char == "x":
        return Literal(value=0, original_text=text, is_x=True, loc=loc)
    elif fill_char == "z":
        return Literal(value=0, original_text=text, is_z=True, loc=loc)
    return Literal(value=0, original_text=text, loc=loc)


def _build_sized_number(
    tree: Tree,
    base: str,
    source_file: str | None,
    loc: SourceLocation,
) -> Literal:
    """Build a Literal from a hex/binary/octal number subtree."""
    size_text = ""
    digits_text = ""
    base_text = ""
    signed = False

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "size":
                size_text = _collect_text(child)
            elif child.data.endswith("_base"):
                base_text = _collect_text(child).strip()
                if "s" in base_text.lower():
                    signed = True
            elif child.data.endswith("_value") or child.data == "unsigned_number":
                digits_text = _collect_text(child)
            elif child.data in ("x_digit", "z_digit"):
                digits_text = _collect_text(child)

    width = int(size_text) if size_text else None

    if not base_text:
        sign_char = "s" if signed else ""
        base_text = f"'{sign_char}{base}"
    original = f"{size_text}{base_text}{digits_text}" if size_text else f"{base_text}{digits_text}"

    is_x = "x" in digits_text.lower()
    is_z = "z" in digits_text.lower() or "?" in digits_text

    if is_x or is_z:
        return Literal(
            value=digits_text,
            width=width,
            base=base,
            signed=signed,
            is_x=is_x,
            is_z=is_z,
            original_text=original,
            loc=loc,
        )

    value: int | str
    try:
        base_map = {"h": 16, "b": 2, "o": 8}
        value = int(digits_text, base_map[base])
    except (ValueError, KeyError):
        value = digits_text

    return Literal(
        value=value, width=width, base=base, signed=signed, is_x=is_x, is_z=is_z, original_text=original, loc=loc
    )


def _build_decimal_number(tree: Tree, source_file: str | None, loc: SourceLocation) -> Literal:
    """Build a Literal from a decimal_number subtree."""
    has_base = any(isinstance(c, Tree) and c.data == "decimal_base" for c in tree.children)

    if has_base:
        return _build_sized_number(tree, "d", source_file, loc)

    text = _collect_text(tree)
    try:
        return Literal(value=int(text), original_text=text, loc=loc)
    except ValueError:
        return Literal(value=text, original_text=text, loc=loc)


def _parse_verilog_number(  # noqa: PLR0911, PLR0912
    text: str,
) -> tuple[int | float | str, int | None, str | None, bool, bool, bool]:
    """Parse a Verilog number string into components."""
    text = text.strip().replace("_", "")
    width: int | None = None
    base: str | None = None
    signed = False
    is_x = False
    is_z = False

    if not text:
        return (0, None, None, False, False, False)

    if "'" in text:
        parts = text.split("'", 1)
        if parts[0]:
            try:
                width = int(parts[0])
            except ValueError:
                pass

        base_and_digits = parts[1] if len(parts) > 1 else ""
        if base_and_digits:
            if base_and_digits[0].lower() == "s" and len(base_and_digits) > 1:
                signed = True
                base_and_digits = base_and_digits[1:]

            base = base_and_digits[0].lower() if base_and_digits else None
            digits = base_and_digits[1:] if len(base_and_digits) > 1 else ""

            is_x = "x" in digits.lower() or "X" in digits
            is_z = "z" in digits.lower() or "Z" in digits or "?" in digits

            if is_x or is_z:
                return (digits, width, base, signed, is_x, is_z)

            try:
                if base == "h":
                    value = int(digits, 16)
                elif base == "b":
                    value = int(digits, 2)
                elif base == "o":
                    value = int(digits, 8)
                elif base == "d":
                    value = int(digits, 10)
                else:
                    value = int(digits) if digits else 0
                return (value, width, base, signed, is_x, is_z)
            except ValueError:
                return (digits, width, base, signed, is_x, is_z)

    num_val: int | float | str
    try:
        num_val = int(text)
        return (num_val, None, None, False, False, False)
    except ValueError:
        try:
            num_val = float(text)
            return (num_val, None, None, False, False, False)
        except ValueError:
            return (text, None, None, False, False, False)


def _build_number(tree: Tree, source_file: str | None) -> Literal:  # noqa: PLR0911
    """Build a Literal from a number subtree."""
    loc = _loc_from_tree(tree, source_file)

    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "hex_number":
                return _build_sized_number(child, "h", source_file, loc)
            elif child.data == "binary_number":
                return _build_sized_number(child, "b", source_file, loc)
            elif child.data == "octal_number":
                return _build_sized_number(child, "o", source_file, loc)
            elif child.data == "decimal_number":
                return _build_decimal_number(child, source_file, loc)
            elif child.data == "real_number":
                text = _collect_real_number_text(child)
                try:
                    return Literal(value=float(text), original_text=text, loc=loc)
                except ValueError:
                    return Literal(value=text, original_text=text, loc=loc)
            elif child.data == "sv_fill_literal":
                return _build_sv_fill_literal(child, source_file, loc)

    text = _collect_text(tree)
    value, width, base, signed, is_x, is_z = _parse_verilog_number(text)
    return Literal(
        value=value, width=width, base=base, signed=signed, is_x=is_x, is_z=is_z, original_text=text, loc=loc
    )


def _token_to_expression(token: Token, source_file: str | None) -> Expression:
    """Convert a Token to an Expression."""
    text = str(token)
    kind = str(token.type) if hasattr(token, "type") else ""

    if "IDENTIFIER" in kind or kind in ("GENVAR_IDENTIFIER",):
        loc = SourceLocation(
            file=source_file,
            line=getattr(token, "line", 0),
            column=getattr(token, "column", 0),
            end_line=getattr(token, "end_line", 0),
            end_column=getattr(token, "end_column", 0),
        )
        return Identifier(name=text, loc=loc)

    if kind == "REAL_NUMBER":
        try:
            return Literal(value=float(text), original_text=text)
        except ValueError:
            pass

    try:
        return Literal(value=int(text), original_text=text)
    except ValueError:
        pass

    if text.startswith('"') and text.endswith('"'):
        return StringLiteral(value=text[1:-1])

    return Identifier(name=text)


def _tree_source_text(node: Tree) -> str:
    tokens: list[str] = []

    def _walk_text(child: Tree | Token) -> None:
        if isinstance(child, Token):
            tokens.append(str(child))
            return
        for grandchild in child.children:
            _walk_text(grandchild)

    _walk_text(node)
    return "".join(tokens)


def _build_hierarchical_identifier(tree: Tree, source_file: str | None) -> Identifier:
    """Build an Identifier from a hierarchical_identifier or hierarchical_net_identifier."""
    parts: list[str] = []
    pending_part: str | None = None

    for child in tree.children:
        if isinstance(child, Tree) and child.data == "hierarchical_identifier":
            return _build_hierarchical_identifier(child, source_file)
        elif isinstance(child, Token) and "IDENTIFIER" in str(child.type):
            if pending_part is not None:
                parts.append(pending_part)
            pending_part = str(child)
        elif isinstance(child, Tree) and child.data == "constant_expression" and pending_part is not None:
            pending_part = f"{pending_part}[{_tree_source_text(child)}]"

    if pending_part is not None:
        parts.append(pending_part)

    loc = _loc_from_tree(tree, source_file)
    if len(parts) == 1:
        return Identifier(name=parts[0], loc=loc)
    elif len(parts) > 1:
        return Identifier(name=parts[-1], hierarchy=parts[:-1], loc=loc)
    return Identifier(name="?", loc=loc)


def _build_scoped_identifier(tree: Tree, source_file: str | None) -> Identifier:
    """Build an Identifier from a scoped_identifier (pkg::member) node."""
    tokens = [str(c) for c in tree.children if isinstance(c, Token)]
    loc = _loc_from_tree(tree, source_file)
    if len(tokens) == _SCOPED_IDENTIFIER_TOKEN_COUNT:
        return Identifier(name=tokens[1], hierarchy=[tokens[0]], loc=loc)
    return Identifier(name="::".join(tokens) if tokens else "?", loc=loc)


def _build_string_literal(tree: Tree, source_file: str | None) -> StringLiteral:
    """Build a StringLiteral from a string tree node."""
    for child in tree.children:
        text = str(child)
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        loc = _loc_from_tree(tree, source_file)
        return StringLiteral(value=text, loc=loc)
    return StringLiteral(value="", loc=_loc_from_tree(tree, source_file))


def _build_constant_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build an Expression from a constant_expression or constant_mintypmax_expression tree."""
    if tree.data == "constant_expression":
        return _build_const_expr_inner(tree, source_file, callbacks)

    for child in tree.children:
        expr = _build_constant_expression_child(child, source_file, callbacks)
        if expr is not None:
            return expr

    return _walk_for_expression(tree, source_file, callbacks)


def _build_constant_expression_child(
    child: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    result: Expression | None = None
    if isinstance(child, Token):
        result = _token_to_expression(child, source_file)
    elif isinstance(child, Tree):
        if child.data == "constant_expression":
            result = _build_const_expr_inner(child, source_file, callbacks)
        elif child.data == "constant_mintypmax_expression":
            result = _build_constant_expression(child, source_file, callbacks)
        elif child.data == "constant_primary":
            result = _build_constant_primary(child, source_file, callbacks)
        elif child.data == "number":
            result = _build_number(child, source_file)
    return result


def _build_const_expr_inner(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build expression from a constant_expression node."""
    children = tree.children

    if len(children) == 1:
        return _build_const_single_child(children[0], source_file, callbacks)

    if len(children) == _TERNARY_EXPR_CHILD_COUNT and all(
        isinstance(child, Tree) and child.data == "constant_expression" for child in children
    ):
        cond = _build_const_expr_child(children[0], source_file, callbacks)
        true_expr = _build_const_expr_child(children[1], source_file, callbacks)
        false_expr = _build_const_expr_child(children[2], source_file, callbacks)
        loc = _loc_from_tree(tree, source_file)
        return TernaryOp(condition=cond, true_expr=true_expr, false_expr=false_expr, loc=loc)

    # Handle Earley-parser ambiguity: "A op (B ? C : D)" where the parser chose
    # the binary-right interpretation instead of the ternary.  In Verilog, ternary
    # always has lower precedence than all binary operators, so the correct reading
    # is always "(A op B) ? C : D".  Detect the pattern and restructure.
    if (
        len(children) == _BINARY_EXPR_CHILD_COUNT
        and isinstance(children[1], Tree)
        and children[1].data == "binary_operator"
        and isinstance(children[2], Tree)
        and children[2].data == "constant_expression"
        and len(children[2].children) == _TERNARY_EXPR_CHILD_COUNT
        and all(isinstance(c, Tree) and c.data == "constant_expression" for c in children[2].children)
    ):
        ternary_ce = children[2]
        ternary_cond_ce, ternary_true_ce, ternary_false_ce = ternary_ce.children
        op_str = _collect_text(children[1]).strip()
        left_expr = _build_const_expr_child(children[0], source_file, callbacks)
        cond_expr = _build_const_expr_child(ternary_cond_ce, source_file, callbacks)
        true_expr = _build_const_expr_child(ternary_true_ce, source_file, callbacks)
        false_expr = _build_const_expr_child(ternary_false_ce, source_file, callbacks)
        loc = _loc_from_tree(tree, source_file)
        new_condition = BinaryOp(op=op_str, left=left_expr, right=cond_expr, loc=loc)
        return TernaryOp(condition=new_condition, true_expr=true_expr, false_expr=false_expr, loc=loc)

    result = _build_binary_chain(
        tree,
        source_file,
        "constant_expression",
        lambda node, sf: _build_const_expr_child(node, sf, callbacks),
    )
    if result is not None:
        return result

    if len(children) == 2:  # noqa: PLR2004
        first, second = children
        unary_op = _extract_operator(first)
        if unary_op:
            operand = _build_const_expr_child(second, source_file, callbacks)
            loc = _loc_from_tree(tree, source_file)
            return UnaryOp(op=unary_op, operand=operand, loc=loc)

    return _walk_for_expression(tree, source_file, callbacks)


def _build_const_single_child(
    child: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    if isinstance(child, Token):
        return _token_to_expression(child, source_file)
    if isinstance(child, Tree):
        if child.data == "constant_primary":
            return _build_constant_primary(child, source_file, callbacks)
        if child.data == "constant_expression":
            return _build_const_expr_inner(child, source_file, callbacks)
        if child.data == "number":
            return _build_number(child, source_file)
        return _walk_for_expression(child, source_file, callbacks)
    return Identifier("?")


def _build_const_expr_child(
    node: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build expression from a constant_expression child node."""
    if isinstance(node, Token):
        return _token_to_expression(node, source_file)
    if isinstance(node, Tree):
        if node.data == "constant_expression":
            return _build_const_expr_inner(node, source_file, callbacks)
        if node.data == "constant_primary":
            return _build_constant_primary(node, source_file, callbacks)
        if node.data == "number":
            return _build_number(node, source_file)
        return _walk_for_expression(node, source_file, callbacks)
    return Identifier("?")


def _build_const_function_call(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build a FunctionCall from constant_system_function_call or constant_function_call."""
    name = ""
    args: list[Expression] = []
    for child in tree.children:
        if isinstance(child, Token):
            if not name:
                name = str(child)
        elif isinstance(child, Tree):
            if child.data == "constant_expression":
                args.append(_build_const_expr_inner(child, source_file, callbacks))
            elif child.data in ("hierarchical_identifier", "hierarchical_function_identifier"):
                name = _build_hierarchical_identifier(child, source_file).name
    loc = _loc_from_tree(tree, source_file)
    return FunctionCall(name=name, arguments=args, loc=loc)


def _build_constant_primary(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build expression from a constant_primary node."""
    for child in tree.children:
        if isinstance(child, Tree):
            result = _build_constant_primary_child(child, source_file, callbacks)
            if result is not None:
                return result
        elif isinstance(child, Token):
            return _token_to_expression(child, source_file)

    return Identifier("?")


def _build_constant_primary_child(
    child: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    result: Expression | None = None
    if child.data == "number":
        result = _build_number(child, source_file)
    elif child.data == "scoped_identifier":
        result = _build_scoped_identifier(child, source_file)
    elif child.data == "constant_expression":
        result = _build_const_expr_inner(child, source_file, callbacks)
    elif child.data == "constant_mintypmax_expression":
        result = _build_constant_expression(child, source_file, callbacks)
    elif child.data == "string":
        result = _build_string_literal(child, source_file)
    elif child.data in ("concatenation", "constant_concatenation"):
        result = _build_concatenation(child, source_file, callbacks)
    elif child.data == "assignment_pattern":
        result = _build_assignment_pattern(child, source_file, callbacks)
    elif child.data in ("multiple_concatenation", "constant_multiple_concatenation"):
        result = _build_multiple_concatenation(child, source_file, callbacks)
    elif child.data in ("constant_system_function_call", "constant_function_call"):
        result = _build_const_function_call(child, source_file, callbacks)
    elif child.data == "sv_width_cast":
        result = _build_constant_width_cast(child, source_file, callbacks)
    else:
        result = _walk_for_expression(child, source_file, callbacks)
    return result


def _build_constant_width_cast(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    for child in tree.children:
        if isinstance(child, Tree) and child.data in (
            "expression",
            "constant_expression",
            "constant_mintypmax_expression",
        ):
            return _build_constant_expression(child, source_file, callbacks)

    tree_children = [child for child in tree.children if isinstance(child, Tree)]
    if len(tree_children) >= 2:  # noqa: PLR2004
        return _build_constant_expression(tree_children[-1], source_file, callbacks)
    return Identifier("?")


def _walk_for_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Fallback: walk a tree and return the first expression-like thing found."""
    for child in tree.children:
        if isinstance(child, Token):
            return _token_to_expression(child, source_file)
        if isinstance(child, Tree):
            if child.data in ("number", "decimal_number", "unsigned_number"):
                return _build_number(child, source_file)
            if child.data == "constant_primary":
                return _build_constant_primary(child, source_file, callbacks)
            if child.data == "constant_expression":
                return _build_const_expr_inner(child, source_file, callbacks)
            result = _walk_for_expression(child, source_file, callbacks)
            if result:
                return result

    return Identifier("?")


def _build_expression(tree: Tree, source_file: str | None, callbacks: _ExpressionCallbacks) -> Expression:
    """Build an Expression from a general expression tree."""
    result: Expression | None = None
    if tree.data in ("constant_mintypmax_expression", "constant_expression"):
        result = _build_constant_expression(tree, source_file, callbacks)
    elif tree.data == "mintypmax_expression":
        result = _build_mintypmax_expression(tree, source_file, callbacks)
    elif tree.data in ("expression", "binary_expression"):
        result = _build_expr_inner(tree, source_file, callbacks)
    else:
        result = _build_expression_special(tree, source_file, callbacks)

    if result is not None:
        return result

    for child in tree.children:
        if isinstance(child, Tree):
            return _build_expression(child, source_file, callbacks)
        if isinstance(child, Token):
            return _token_to_expression(child, source_file)

    return Identifier("?")


def _build_mintypmax_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data == "expression":
                return _build_expr_inner(child, source_file, callbacks)
            return _build_expression(child, source_file, callbacks)
    return _walk_for_expression(tree, source_file, callbacks)


def _build_expression_special(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    result: Expression | None = None
    if tree.data == "primary":
        result = _build_primary(tree, source_file, callbacks)
    elif tree.data == "number":
        result = _build_number(tree, source_file)
    elif tree.data in ("hierarchical_identifier", "hierarchical_net_identifier"):
        result = _build_hierarchical_identifier(tree, source_file)
    elif tree.data in ("concatenation", "constant_concatenation"):
        result = _build_concatenation(tree, source_file, callbacks)
    elif tree.data == "assignment_pattern":
        result = _build_assignment_pattern(tree, source_file, callbacks)
    elif tree.data in ("multiple_concatenation", "constant_multiple_concatenation"):
        result = _build_multiple_concatenation(tree, source_file, callbacks)
    elif tree.data == "conditional_expression":
        result = _build_conditional_expression(tree, source_file, callbacks)
    elif tree.data == "constant_primary":
        result = _build_constant_expression(tree, source_file, callbacks)
    elif tree.data == "string":
        result = _build_string_literal(tree, source_file)
    elif tree.data == "sv_width_cast":
        result = _build_width_cast(tree, source_file, callbacks)
    return result


def _build_width_cast(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    for child in tree.children:
        if isinstance(child, Tree) and child.data in (
            "expression",
            "constant_expression",
            "constant_mintypmax_expression",
        ):
            return _build_expression(child, source_file, callbacks)

    tree_children = [child for child in tree.children if isinstance(child, Tree)]
    if len(tree_children) >= 2:  # noqa: PLR2004
        return _build_expression(tree_children[-1], source_file, callbacks)
    return Identifier("?")


def _build_expr_inner(tree: Tree, source_file: str | None, callbacks: _ExpressionCallbacks) -> Expression:
    """Build expression from an expression node."""
    children = tree.children

    if len(children) == 1:
        return _build_expr_single_child(children[0], source_file, callbacks)

    result = _build_binary_chain(
        tree,
        source_file,
        "binary_expression",
        lambda node, sf: _build_expr_child(node, sf, callbacks),
    )
    if result is not None:
        return result

    inside_expr = _build_inside_expression(tree, source_file, callbacks)
    if inside_expr is not None:
        return inside_expr

    if len(children) == 2:  # noqa: PLR2004
        first, second = children
        op_str = _extract_operator(first)
        if op_str:
            operand = _build_expr_child(second, source_file, callbacks)
            loc = _loc_from_tree(tree, source_file)
            return UnaryOp(op=op_str, operand=operand, loc=loc)

    return _walk_for_expression(tree, source_file, callbacks)


def _build_expr_single_child(
    child: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    if isinstance(child, Tree):
        return _build_expression(child, source_file, callbacks)
    if isinstance(child, Token):
        return _token_to_expression(child, source_file)
    return Identifier("?")


def _build_inside_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    children = tree.children
    if len(children) != _TERNARY_EXPR_CHILD_COUNT:
        return None

    mid = children[1]
    if not isinstance(mid, Token) or mid.type != "KW_INSIDE":
        return None

    lhs = _build_expr_child(children[0], source_file, callbacks)
    range_list = children[2]
    comparisons = []
    if isinstance(range_list, Tree):
        for range_child in range_list.children:
            if isinstance(range_child, Tree):
                comparison = _build_inside_open_value_range(lhs, range_child, source_file, callbacks)
                if comparison is not None:
                    comparisons.append(comparison)

    if not comparisons:
        return lhs

    result = comparisons[0]
    for comparison in comparisons[1:]:
        loc = _loc_from_tree(tree, source_file)
        result = BinaryOp(op="||", left=result, right=comparison, loc=loc)
    return result


def _build_inside_open_value_range(
    lhs: Expression,
    range_child: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    """Lower one ``inside`` open-value-range item into comparisons."""
    expr_children = [child for child in range_child.children if isinstance(child, Tree) and child.data == "expression"]
    loc = _loc_from_tree(range_child, source_file)

    if len(expr_children) == 1:
        value = _build_expression(expr_children[0], source_file, callbacks)
        return BinaryOp(op="==", left=copy.deepcopy(lhs), right=value, loc=loc)

    if len(expr_children) == 2:  # noqa: PLR2004
        lo = _build_expression(expr_children[0], source_file, callbacks)
        hi = _build_expression(expr_children[1], source_file, callbacks)
        ge = BinaryOp(op=">=", left=copy.deepcopy(lhs), right=lo, loc=loc)
        le = BinaryOp(op="<=", left=copy.deepcopy(lhs), right=hi, loc=loc)
        return BinaryOp(op="&&", left=ge, right=le, loc=loc)

    return None


def _build_expr_child(
    node: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build expression from a child node."""
    if isinstance(node, Token):
        return _token_to_expression(node, source_file)
    if isinstance(node, Tree):
        if node.data in ("expression", "binary_expression"):
            return _build_expr_inner(node, source_file, callbacks)
        return _build_expression(node, source_file, callbacks)
    return Identifier("?")


def _build_primary(tree: Tree, source_file: str | None, callbacks: _ExpressionCallbacks) -> Expression:
    """Build expression from a primary node."""
    children = [child for child in tree.children if isinstance(child, Tree)]
    tokens = [child for child in tree.children if isinstance(child, Token)]

    if not children and tokens:
        return _token_to_expression(tokens[0], source_file)

    if not children:
        return Identifier("?")

    first = children[0]

    if first.data == "hierarchical_identifier":
        return _build_hierarchical_primary(tree, first, children[1:], source_file, callbacks)

    special = _build_primary_special(first, source_file, callbacks)
    if special is not None:
        return special

    return _build_expression(first, source_file, callbacks)


def _build_hierarchical_primary(
    tree: Tree,
    first: Tree,
    siblings: list[Tree],
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    base: Expression = _build_hierarchical_identifier(first, source_file)
    for sibling in siblings:
        if sibling.data == "range_expression":
            return _build_range_select(base, sibling, source_file, callbacks)
        if sibling.data == "expression":
            index = _build_expr_inner(sibling, source_file, callbacks)
            loc = _loc_from_tree(tree, source_file)
            base = BitSelect(target=base, index=index, loc=loc)
    return base


def _build_primary_special(
    first: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    result: Expression | None = None
    if first.data == "scoped_identifier":
        result = _build_scoped_identifier(first, source_file)
    elif first.data == "number":
        result = _build_number(first, source_file)
    elif first.data in ("concatenation", "constant_concatenation"):
        result = _build_concatenation(first, source_file, callbacks)
    elif first.data == "assignment_pattern":
        result = _build_assignment_pattern(first, source_file, callbacks)
    elif first.data in ("multiple_concatenation", "constant_multiple_concatenation"):
        result = _build_multiple_concatenation(first, source_file, callbacks)
    elif first.data == "expression":
        result = _build_expr_inner(first, source_file, callbacks)
    elif first.data == "mintypmax_expression":
        result = _build_expression(first, source_file, callbacks)
    elif first.data in ("system_function_call", "function_call"):
        result = _build_function_call(first, source_file, callbacks)
    elif first.data == "string":
        result = _build_string_literal(first, source_file)
    elif first.data == "sv_width_cast":
        result = _build_width_cast(first, source_file, callbacks)
    return result


def _build_genvar_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build an Expression from a genvar_expression node."""
    children = tree.children

    if len(children) == 1:
        return _build_genvar_single_child(children[0], source_file, callbacks)

    result = _build_binary_chain(
        tree,
        source_file,
        "genvar_expression",
        lambda node, sf: _build_genvar_expr_child(node, sf, callbacks),
    )
    if result is not None:
        return result

    if len(children) == 2:  # noqa: PLR2004
        first, second = children
        op_str = _extract_operator(first)
        if op_str:
            operand = _build_genvar_expr_child(second, source_file, callbacks)
            loc = _loc_from_tree(tree, source_file)
            return UnaryOp(op=op_str, operand=operand, loc=loc)

    if len(children) == _GENVAR_TERNARY_EXPR_CHILD_COUNT:
        cond = _build_genvar_expr_child(children[_TERNARY_CONDITION_INDEX], source_file, callbacks)
        true_expr = _build_genvar_expr_child(children[_TERNARY_TRUE_INDEX], source_file, callbacks)
        false_expr = _build_genvar_expr_child(children[_TERNARY_FALSE_INDEX], source_file, callbacks)
        loc = _loc_from_tree(tree, source_file)
        return TernaryOp(condition=cond, true_expr=true_expr, false_expr=false_expr, loc=loc)

    return _walk_for_expression(tree, source_file, callbacks)


def _build_genvar_single_child(
    child: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    if isinstance(child, Tree):
        if child.data == "genvar_primary":
            return _build_genvar_primary(child, source_file, callbacks)
        if child.data == "genvar_expression":
            return _build_genvar_expression(child, source_file, callbacks)
        return _build_expression(child, source_file, callbacks)
    if isinstance(child, Token):
        return _token_to_expression(child, source_file)
    return Identifier("?")


def _build_genvar_expr_child(
    node: Tree | Token,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build expression from a child of genvar_expression."""
    if isinstance(node, Tree):
        if node.data == "genvar_expression":
            return _build_genvar_expression(node, source_file, callbacks)
        if node.data == "genvar_primary":
            return _build_genvar_primary(node, source_file, callbacks)
        return _build_expression(node, source_file, callbacks)
    if isinstance(node, Token):
        return _token_to_expression(node, source_file)
    return Identifier("?")


def _build_genvar_primary(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build expression from genvar_primary."""
    for child in tree.children:
        if isinstance(child, Token) and child.type == "GENVAR_IDENTIFIER":
            loc = _loc_from_tree(tree, source_file)
            return Identifier(name=str(child), loc=loc)
        if isinstance(child, Tree) and child.data == "constant_primary":
            return _build_constant_primary(child, source_file, callbacks)
    return _walk_for_expression(tree, source_file, callbacks)


def _build_concatenation(tree: Tree, source_file: str | None, callbacks: _ExpressionCallbacks) -> Concatenation:
    """Build a Concatenation from a concatenation-like tree node."""
    parts: list[Expression] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data == "expression":
            parts.append(callbacks.build_expr_inner(child, source_file))
        elif isinstance(child, Tree):
            parts.append(callbacks.build_expression(child, source_file))
    loc = _loc_from_tree(tree, source_file)
    return Concatenation(parts=parts, loc=loc)


def _build_assignment_pattern(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> AssignmentPattern:
    """Build an AssignmentPattern from an assignment_pattern tree node."""
    loc = _loc_from_tree(tree, source_file)
    named_pairs: list[tuple[str, Expression]] = []
    positional: list[Expression] = []
    default_value: Expression | None = None

    children = list(tree.children)
    index = 0
    while index < len(children):
        child = children[index]
        if isinstance(child, Token) and child.type == "KW_DEFAULT":
            index, default_value = _consume_pattern_value(children, index + 1, source_file, callbacks)
        elif isinstance(child, Token) and child.type == "IDENTIFIER":
            field_name = str(child)
            index, expr = _consume_pattern_value(children, index + 1, source_file, callbacks)
            if expr is not None:
                named_pairs.append((field_name, expr))
        elif isinstance(child, Tree):
            positional.append(callbacks.build_expression(child, source_file))
        index += 1

    return AssignmentPattern(
        named_pairs=named_pairs if named_pairs else None,
        positional=positional if positional else None,
        default_value=default_value,
        loc=loc,
    )


def _consume_pattern_value(
    children: list[Tree | Token],
    start_index: int,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> tuple[int, Expression | None]:
    index = start_index
    while index < len(children):
        child = children[index]
        if isinstance(child, Tree):
            return index, callbacks.build_expression(child, source_file)
        index += 1
    return index, None


def _build_multiple_concatenation(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Replication:
    """Build a Replication from a multiple_concatenation tree node."""
    count_expr: Expression = Literal("1")
    value_expr: Expression = Identifier("?")
    for child in tree.children:
        if isinstance(child, Tree):
            if child.data in ("constant_expression", "constant_mintypmax_expression"):
                count_expr = callbacks.build_constant_expression(child, source_file)
            elif child.data in ("concatenation", "constant_concatenation"):
                value_expr = _build_concatenation(child, source_file, callbacks)
    loc = _loc_from_tree(tree, source_file)
    return Replication(count=count_expr, value=value_expr, loc=loc)


def _build_conditional_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> TernaryOp:
    """Build a TernaryOp from a conditional_expression tree node."""
    exprs: list[Expression] = []
    for child in tree.children:
        if isinstance(child, Tree) and child.data in ("expression", "binary_expression"):
            exprs.append(callbacks.build_expr_inner(child, source_file))
        elif isinstance(child, Tree):
            exprs.append(callbacks.build_expression(child, source_file))
    loc = _loc_from_tree(tree, source_file)
    if len(exprs) >= 3:  # noqa: PLR2004
        return TernaryOp(condition=exprs[0], true_expr=exprs[1], false_expr=exprs[2], loc=loc)
    cond = exprs[0] if exprs else Identifier("?")
    true_expr = exprs[1] if len(exprs) > 1 else Identifier("?")
    false_expr = exprs[2] if len(exprs) > 2 else Identifier("?")  # noqa: PLR2004
    return TernaryOp(condition=cond, true_expr=true_expr, false_expr=false_expr, loc=loc)


def _build_range_select(
    base: Expression,
    range_tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression:
    """Build a RangeSelect or PartSelect from a range_expression tree node."""
    parts = _collect_range_select_parts(range_tree, source_file, callbacks)
    loc = _loc_from_tree(range_tree, source_file)

    if parts.base is not None and parts.width is not None:
        direction = _extract_part_select_direction(range_tree, source_file)
        return PartSelect(target=base, base=parts.base, width=parts.width, direction=direction, loc=loc)
    if parts.msb is not None and parts.lsb is not None:
        return RangeSelect(target=base, msb=parts.msb, lsb=parts.lsb, loc=loc)
    if parts.msb is not None:
        return BitSelect(target=base, index=parts.msb, loc=loc)
    return base


def _collect_range_select_parts(
    range_tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> _RangeSelectParts:
    parts = _RangeSelectParts()

    for child in range_tree.children:
        if isinstance(child, Tree):
            _collect_range_select_child(parts, child, source_file, callbacks)

    return parts


def _collect_range_select_child(
    parts: _RangeSelectParts,
    child: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> None:
    if child.data == "msb_constant_expression":
        parts.msb = _build_wrapped_constant_expression(child, source_file, callbacks)
    elif child.data == "lsb_constant_expression":
        parts.lsb = _build_wrapped_constant_expression(child, source_file, callbacks)
    elif child.data in ("constant_expression", "constant_mintypmax_expression"):
        _assign_next_range_bound(parts, callbacks.build_constant_expression(child, source_file))
    elif child.data in ("expression", "binary_expression"):
        _assign_next_range_bound(parts, callbacks.build_expr_inner(child, source_file))
    elif child.data == "base_expression":
        parts.base = _build_wrapped_expression(child, source_file, callbacks)
    elif child.data == "constant_base_expression":
        parts.base = _build_wrapped_constant_expression(child, source_file, callbacks)
    elif child.data == "width_constant_expression":
        parts.width = _build_wrapped_constant_expression(child, source_file, callbacks)


def _assign_next_range_bound(parts: _RangeSelectParts, expr: Expression) -> None:
    if parts.msb is None:
        parts.msb = expr
    else:
        parts.lsb = expr


def _build_wrapped_constant_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    for child in tree.children:
        if isinstance(child, Tree):
            return callbacks.build_constant_expression(child, source_file)
    return None


def _build_wrapped_expression(
    tree: Tree,
    source_file: str | None,
    callbacks: _ExpressionCallbacks,
) -> Expression | None:
    for child in tree.children:
        if isinstance(child, Tree):
            return callbacks.build_expression(child, source_file)
    return None


def _extract_part_select_direction(range_tree: Tree, source_file: str | None) -> str:
    if not source_file or not hasattr(range_tree.meta, "start_pos") or not hasattr(range_tree.meta, "end_pos"):
        return "+:"

    source_text = _SOURCE_TEXT_CACHE.get(source_file)
    if source_text is None:
        source_text = Path(source_file).read_text()
        _SOURCE_TEXT_CACHE[source_file] = source_text

    segment = source_text[range_tree.meta.start_pos : range_tree.meta.end_pos]
    return "-:" if "-:" in segment else "+:"


def _build_function_call(tree: Tree, source_file: str | None, callbacks: _ExpressionCallbacks) -> Expression:
    """Build a FunctionCall from a function_call or system_function_call tree."""
    name = ""
    args: list[Expression] = []
    for child in tree.children:
        if isinstance(child, Token):
            if child.type in ("SYSTEM_TF_IDENTIFIER", "SIMPLE_IDENTIFIER"):
                name = str(child)
            elif not name:
                name = str(child)
        elif isinstance(child, Tree):
            if child.data in ("hierarchical_identifier", "hierarchical_function_identifier"):
                name = _build_hierarchical_identifier(child, source_file).name
            elif child.data == "expression":
                args.append(callbacks.build_expr_inner(child, source_file))
            elif child.data in ("list_of_arguments",):
                for arg_child in child.children:
                    if isinstance(arg_child, Tree) and arg_child.data == "expression":
                        args.append(callbacks.build_expr_inner(arg_child, source_file))
    loc = _loc_from_tree(tree, source_file)
    return FunctionCall(name=name, arguments=args, loc=loc)
