"""SystemVerilog query / bit-vector system functions, lowered before any
engine sees the module (``Simulator.__init__``), so every engine supports
them the same way:

- ``$size``/``$high``/``$low``/``$left``/``$right`` (IEEE 1800 20.7) fold to
  integer literals from the declarations (optional dimension argument; the
  unpacked dimensions come first, then the packed ones).
- ``$countones``/``$onehot``/``$onehot0``/``$isunknown``/``$countbits``
  (20.9) become ordinary expressions with Icarus's x semantics: x/z bits are
  not counted as ones (``x === 1'b1`` per bit), and ``$isunknown`` is
  ``(^e) === 1'bx``. ``$countbits(e, c, ...)`` counts the bits equal (``===``)
  to any of its constant control bits; x and z share one representation in
  this simulator, so a ``'x`` or ``'z`` control counts both.

Before this, no engine implemented them: reference returned x and compiled
silently 0.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from veriforge.model.base import VerilogNode
from veriforge.model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Range,
    PartSelect,
    RangeSelect,
    StreamingConcatenation,
    UnaryOp,
    WidthCast,
)
from veriforge.model.functions import FunctionDecl, TaskDecl
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import BlockingAssign
from veriforge.model.variables import INTEGER_ATOM_WIDTHS
from veriforge.semantics import const_int, expr_signed, expr_width

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.nets import Net
    from veriforge.model.variables import Variable

_RANGE_QUERIES = {"$size", "$high", "$low", "$left", "$right"}
_BIT_FUNCS = {"$countones", "$onehot", "$onehot0", "$isunknown", "$countbits"}
_SKIP_SLOTS = {"_parse_tree", "attributes", "comments", "loc", "parent", "drivers", "loads"}


# cm:201aad
def lower_sv_functions(module: Module) -> Module:
    """Rewrite the functions above everywhere in *module*, in place."""
    from .elaborate import _build_param_env  # noqa: PLC0415

    if not _mentions(module):
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    decls = _declarations(module, env)

    def width_of(name: str) -> int:
        d = decls.get(name)
        return d[2] if d is not None else 1

    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, FunctionCall):
            return None
        name = expr.name.lower()
        if name in _RANGE_QUERIES:
            return _fold_range_query(name, expr, decls, env)
        if name == "$bits" and len(expr.arguments) == 1:
            # `$bits(type_t)` of a typedef: its width, as a constant on every
            # engine. Reference evaluated the type name as an (unknown)
            # signal and returned 1, so ibex's
            # `cpu_ctrl_sts_part_t'(csr_wdata_int[$bits(cpu_ctrl_sts_part_t)-1:0])`
            # sliced one bit (vm/compiled read the elaboration env).
            arg = expr.arguments[0]
            if isinstance(arg, Identifier) and not arg.hierarchy and arg.name not in decls:
                bits = env.get(f"$bits:{arg.name}")
                if isinstance(bits, int):
                    return _integer_literal(bits)
            return None
        if name == "$countbits" and len(expr.arguments) >= 2:
            controls = [_control_bit(c, env) for c in expr.arguments[1:]]
            if None in controls:
                raise ValueError("$countbits: each control bit must be a constant ('0, '1, 'x or 'z)")
            return _as_int(_count_bits(expr.arguments[0], decls, env, width_of, sorted(set(controls))))  # type: ignore[arg-type]
        if name in _BIT_FUNCS and len(expr.arguments) == 1:
            arg = expr.arguments[0]
            if name == "$isunknown":
                return BinaryOp("===", UnaryOp("^", arg), Literal(0, width=1, is_x=True, original_text="1'bx"))
            count = _count_bits(arg, decls, env, width_of, ["1"])
            if name == "$countones":
                return _as_int(count)
            op = "==" if name == "$onehot" else "<="
            return BinaryOp(op, count, Literal(1, width=32, original_text="32'd1"))
        return None

    _rewrite(module, lower, set())
    return module


def lower_struct_param_fields(module: Module) -> Module:
    """Fold every field read of a struct-valued parameter (``Nm.lower_cause``
    of ``localparam c_t Nm = '{irq_int: 1'b1, lower_cause: 5'd31}``) to a
    constant, in place, for every engine.

    The parameter itself evaluates to its packed value, but a parameter is not
    a struct-typed signal, so a field select read x (reference, vm, vm-fast) or
    0 (compiled): ibex's `irq_vec = ExcCauseIrqNm.lower_cause`, found by
    tools/corpus_check.py. The layout comes from the assignment pattern's field
    names, as for the value, or else from the parameter's declared struct type:
    a `parameter cfg_t Cfg = '0` (pulp's axi_lite_xbar) also read its fields as
    x on reference/vm/vm-fast.
    """
    from .elaborate import _build_param_env, _build_struct_env, match_assignment_pattern_layout  # noqa: PLC0415

    def declared_type(param) -> str | None:
        name = param.param_type if isinstance(param.param_type, str) else None
        return name.rsplit("::", 1)[-1] if name else None

    patterned = [
        p for p in module.parameters if isinstance(p.default_value, AssignmentPattern) and p.default_value.named_pairs
    ]
    if not patterned and not any(declared_type(p) for p in module.parameters):
        return module
    type_map, _signal_map = _build_struct_env(module)
    typed = [p for p in module.parameters if p not in patterned and declared_type(p) in type_map]
    if not patterned and not typed:
        return module
    env = _build_param_env(module)
    layouts = {}
    for param in patterned:
        value = env.get(param.name)
        pattern = param.default_value
        assert isinstance(pattern, AssignmentPattern)  # (filtered above)
        layout = match_assignment_pattern_layout(pattern, type_map)
        if isinstance(value, int) and layout is not None:
            layouts[param.name] = (layout, value)
    for param in typed:
        layout = type_map[declared_type(param)]  # type: ignore[index]
        value = env.get(param.name)
        fill = getattr(param.default_value, "original_text", None) if isinstance(param.default_value, Literal) else None
        if fill in ("'0", "'1"):
            value = 0 if fill == "'0" else (1 << layout.total_width) - 1
        if isinstance(value, int):
            layouts[param.name] = (layout, value & ((1 << layout.total_width) - 1))
    if not layouts:
        return module

    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, Identifier):
            return None
        parts = [*(expr.hierarchy or ()), expr.name]
        if len(parts) == 1:
            parts = expr.name.split(".")
        for k in range(1, len(parts)):
            hit = layouts.get(".".join(parts[:k]))
            if hit is None:
                continue
            layout, value = hit
            spec = layout.fields.get(".".join(parts[k:]))
            if spec is None:
                return None
            offset, width = spec
            field = (value >> offset) & ((1 << width) - 1)
            return Literal(field, width=width, original_text=f"{width}'d{field}")
        return None

    _rewrite(module, lower, set())
    return module


# cm:a83c5f
def lower_struct_patterns(module: Module) -> Module:
    """Rewrite an assignment pattern assigned to a packed-struct target
    (``s = '{b: 1, default: '1}``, ``m[k] = '{a, b}``, ``n_t s = '{...}``) as a
    concatenation of its fields, each cast to the field's width, in place, for
    every engine; a nested pattern takes the field's own type.

    Each engine picked the struct by the pattern's field names alone, so two
    typedefs sharing field names packed with the wrong layout, and compiled
    packed only the ``default:`` of a mixed pattern (``'{b: 1, default: 0}``,
    which the parser used to keep as a field named "default", so every engine
    raised). A cast inside a pattern (``'{a: 4'(k)}``) also reached the
    engines unlowered. Patterns elsewhere (operands, arguments) keep the
    engines' field-name match.
    """
    from .elaborate import _build_param_env, _build_struct_env  # noqa: PLC0415

    targets = [
        node
        for node in iter_nodes(module, set())
        if isinstance(getattr(node, "rhs", None), AssignmentPattern)
        or isinstance(getattr(node, "initial_value", None), AssignmentPattern)
    ]
    if not targets:
        return module
    _type_map, signal_map = _build_struct_env(module)
    if not signal_map:
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    decls = _declarations(module, env)

    def width_of(name: str) -> int:
        d = decls.get(name)
        return d[2] if d is not None else 1

    def target_type(lhs: Expression) -> str | None:
        if isinstance(lhs, Identifier):
            name = ".".join([*lhs.hierarchy, lhs.name]) if lhs.hierarchy else lhs.name
            d = decls.get(name)
            layout = signal_map.get(name)
            # (A packed array of structs, `n_t [1:0] m`, is not one struct.)
            if layout is not None and d is not None and (d[1] or d[2] != layout.total_width):
                return None
            return name
        if isinstance(lhs, BitSelect) and isinstance(lhs.target, Identifier) and not lhs.target.hierarchy:
            # One element of an array of structs (`m[k] = '{...}`).
            d = decls.get(lhs.target.name)
            layout = signal_map.get(lhs.target.name)
            if layout is not None and d is not None and d[1] + (d[2] != layout.total_width) == 1:
                return lhs.target.name
        return None

    def build(pattern: AssignmentPattern, path: str) -> Expression | None:
        return _pack_struct_pattern(pattern, path, signal_map.get, lambda v, w: _fit_width(v, w, width_of, env))

    for node in targets:
        slot = "rhs" if isinstance(getattr(node, "rhs", None), AssignmentPattern) else "initial_value"
        if slot == "rhs":
            path = target_type(node.lhs)  # type: ignore[attr-defined]
        else:
            path = node.name if not getattr(node, "dimensions", None) else None  # type: ignore[attr-defined]
        if path is None:
            continue
        new = build(getattr(node, slot), path)
        if new is not None:
            setattr(node, slot, new)
    return module


def _fit_width(value: Expression, width: int, width_of, env) -> Expression:
    """*value* at *width* bits: a fill literal (``'0``, ``'1``, ``'x``) sized,
    a value of another width cast, else *value* itself."""
    text = getattr(value, "original_text", None) if isinstance(value, Literal) else None
    if text in ("'0", "'1", "'x", "'X", "'z", "'Z"):
        bit = text[1].lower()
        if bit in "01":
            fill = 0 if bit == "0" else (1 << width) - 1
            return Literal(fill, width=width, original_text=f"{width}'h{fill:x}")
        return Literal(0, width=width, is_x=bit == "x", is_z=bit == "z", original_text=f"{width}'b{bit}")
    try:
        if expr_width(value, width_of, env) == width:
            return value
    except (ValueError, TypeError, KeyError):
        pass
    return WidthCast(Literal(width, width=32, original_text=f"32'd{width}"), value)


def _pack_struct_pattern(pattern: AssignmentPattern, path: str, layout_of, fit) -> Expression | None:
    """*pattern* as a concatenation of the fields of the struct
    ``layout_of(path)``, MSB first, each ``fit(value, width)``; a nested
    pattern or a ``default:`` reaching a nested struct takes
    ``layout_of(f"{path}.{field}")``. None when the pattern does not fit."""
    layout = layout_of(path)
    if layout is None:
        return None
    fields = sorted(layout.fields.items(), key=lambda item: item[1][0], reverse=True)
    if pattern.positional:
        if pattern.named_pairs or len(pattern.positional) != len(fields):
            return None
        values = dict(zip((name for name, _ in fields), pattern.positional, strict=True))
    else:
        values = dict(pattern.named_pairs)
        if not set(values) <= set(layout.fields):
            return None
    parts: list[Expression] = []
    for name, (_offset, width) in fields:
        value = values.get(name, pattern.default_value)
        sub = f"{path}.{name}"
        if value is None:
            parts.append(Literal(0, width=width, original_text=f"{width}'d0"))
        elif isinstance(value, AssignmentPattern):
            nested = _pack_struct_pattern(value, sub, layout_of, fit)
            parts.append(nested if nested is not None else fit(value, width))
        elif name not in values and layout_of(sub) is not None:
            # (`default:` reaches each member of a nested struct.)
            nested = _pack_struct_pattern(AssignmentPattern(default_value=value), sub, layout_of, fit)
            parts.append(nested if nested is not None else fit(value, width))
        else:
            parts.append(fit(value, width))
    return Concatenation(parts=parts)


# cm:9e4b17
def lower_parameter_arrays(module: Module) -> Module:
    """Rewrite each parameter with an unpacked dimension (``parameter t_t
    A [N] = '{e0, e1, ...}``) as a packed parameter of N elements, element
    ``i`` at bits ``(i - low) * W``, and each read of an element (``A[i]``,
    ``A[i][3:0]``, ``A[i].f``) as a select of it, in place, for every engine.

    The parser dropped the dimension, so every engine took the pattern as one
    value and read every element as 0 (ibex_pkg's ``PmpCfgRst`` and
    ``PmpAddrRst``, passed down as ``ibex_cs_registers``' ``PMPRstCfg[i]``).
    The Simulator runs this on every module before flattening, so a whole
    array passed as a parameter override is the packed value and an index
    that is a genvar is renamed like any other. A package's parameter is
    lowered as the module's own copy (the package keeps its dimension).
    """
    from .elaborate import _build_param_env, _build_struct_env  # noqa: PLC0415

    arrays = [prm for prm in module.parameters if len(getattr(prm, "dimensions", None) or ()) == 1]
    if not arrays:
        return module
    raw_env = _build_param_env(module)
    env = {k: v for k, v in raw_env.items() if isinstance(v, int)}
    type_map, _signal_map = _build_struct_env(module)
    field_types = {
        (td.name, fld.name): (fld.data_type or "").rsplit("::", 1)[-1]
        for td in getattr(module, "typedefs", None) or ()
        if getattr(td, "struct_type", None) is not None
        for fld in td.struct_type.fields
    }

    def element_type(prm) -> str | None:
        name = prm.param_type.rsplit("::", 1)[-1] if isinstance(prm.param_type, str) else None
        return name if name in type_map else None

    def element_width(prm) -> int | None:
        if prm.width is not None:
            msb, lsb = const_int(prm.width.msb, env), const_int(prm.width.lsb, env)
            return abs(msb - lsb) + 1 if msb is not None and lsb is not None else None
        kind = prm.param_type.rsplit("::", 1)[-1] if isinstance(prm.param_type, str) else None
        if kind is None or kind in ("bit", "logic", "reg"):
            return 1 if kind else None
        if kind in INTEGER_ATOM_WIDTHS:
            return INTEGER_ATOM_WIDTHS[kind]
        if kind in type_map:
            return type_map[kind].total_width
        width = raw_env.get(f"$bits:{kind}")
        return width if isinstance(width, int) else None

    def layout_path(type_name: str):
        def layout_of(path: str):
            current = type_name
            for part in path.split(".")[1:]:
                current = field_types.get((current, part), "")
            return type_map.get(current)

        return layout_of

    def element_value(item: Expression, prm, width: int) -> Expression:
        type_name = element_type(prm)
        if isinstance(item, AssignmentPattern) and type_name is not None:
            fit = lambda v, w: _fit_width(v, w, lambda _n: 1, env)  # noqa: E731
            packed = _pack_struct_pattern(item, type_name, layout_path(type_name), fit)
            if packed is not None:
                return packed
        return _fit_width(item, width, lambda _n: 1, env)

    # name -> (low index, count, element width, element type, element's declared lsb)
    shapes: dict[str, tuple[int, int, int, str | None, int | None]] = {}
    replaced: dict[int, Parameter] = {}
    for prm in arrays:
        dim = prm.dimensions[0]
        left, right = const_int(dim.msb, env), const_int(dim.lsb, env)
        width = element_width(prm)
        if left is None or right is None or width is None:
            continue
        low, count = min(left, right), abs(left - right) + 1
        step = 1 if left <= right else -1
        value = prm.default_value
        if isinstance(value, AssignmentPattern):
            by_index: dict[int, Expression] = {}
            if value.positional:
                if len(value.positional) != count:
                    continue
                by_index = {left + k * step: item for k, item in enumerate(value.positional)}
            for key, item in value.named_pairs:
                index = int(key) if str(key).lstrip("-").isdigit() else None
                if index is None:
                    break
                by_index[index] = item
            else:
                for index in range(low, low + count):
                    if index not in by_index:
                        if value.default_value is None:
                            break
                        by_index[index] = value.default_value
                else:
                    parts = [element_value(by_index[i], prm, width) for i in range(low + count - 1, low - 1, -1)]
                    value = Concatenation(parts=parts)
            if isinstance(value, AssignmentPattern):
                continue  # (a pattern this does not read: left as it was)
        new = copy.copy(prm)
        new.dimensions = []
        new.width = Range(Literal(count * width - 1, width=32), Literal(0, width=32))
        new.param_type = None
        new.signed = False
        new.default_value = value
        replaced[id(prm)] = new
        elem_lsb: int | None = 0
        if prm.width is not None:
            msb, lsb = const_int(prm.width.msb, env), const_int(prm.width.lsb, env)
            elem_lsb = lsb if msb is not None and lsb is not None and msb >= lsb else None
        shapes[prm.name] = (low, count, width, element_type(prm), elem_lsb)
    if not shapes:
        return module
    module.parameters = [replaced.get(id(prm), prm) for prm in module.parameters]

    def element_select(name: str, index: Expression, offset: int, width: int) -> Expression:
        low, _count, elem_width, _type, _lsb = shapes[name]
        position = const_int(index, env)
        if position is not None:
            lsb = (position - low) * elem_width + offset
            return RangeSelect(Identifier(name), Literal(lsb + width - 1, width=32), Literal(lsb, width=32))
        base: Expression = BinaryOp("*", BinaryOp("-", index, Literal(low, width=32)), Literal(elem_width, width=32))
        if offset:
            base = BinaryOp("+", base, Literal(offset, width=32))
        return PartSelect(Identifier(name), base, Literal(width, width=32), "+:")

    field_layouts = {name: type_map[shape[3]] for name, shape in shapes.items() if shape[3] is not None}
    for name, (_lo, _n, _w, t, _l) in shapes.items():
        if t is not None:
            stack = [(name, t)]
            while stack:
                path, tname = stack.pop()
                for (owner, fld), ftype in field_types.items():
                    if owner == tname and ftype in type_map:
                        field_layouts[f"{path}.{fld}"] = type_map[ftype]
                        stack.append((f"{path}.{fld}", ftype))
    field_lsbs = _struct_field_lsbs(module, env)
    lowered: dict[int, tuple[int, int, int | None]] = {}  # id(select) -> (element offset, width, lsb)

    def lower(expr: Expression) -> Expression | None:
        if (
            isinstance(expr, BitSelect)
            and isinstance(expr.target, Identifier)
            and not expr.target.hierarchy
            and expr.target.name in shapes
        ):
            width, elem_lsb = shapes[expr.target.name][2], shapes[expr.target.name][4]
            new = element_select(expr.target.name, expr.index, 0, width)
            lowered[id(new)] = (0, width, elem_lsb)
            return new
        if isinstance(expr, Identifier) and field_layouts:
            hit = _struct_array_field(expr, field_layouts, field_lsbs)
            if hit is None:
                return None
            element, offset, width, lsb = hit
            if not (isinstance(element, BitSelect) and isinstance(element.target, Identifier)):
                return None
            if element.target.name not in shapes:
                return None
            new = element_select(element.target.name, element.index, offset, width)
            lowered[id(new)] = (offset, width, lsb)
            return new
        if isinstance(expr, (BitSelect, RangeSelect, PartSelect)) and id(expr.target) in lowered:
            target = expr.target
            offset, width, lsb = lowered[id(target)]
            if not isinstance(target, RangeSelect):
                return None  # (a variable index: the select stays a select of a select)
            start = const_int(target.lsb, env)
            if start is None or not isinstance(target.target, Identifier):
                return None
            return _fold_field_select(expr, Identifier(target.target.name), start, width, lsb, env)
        return None

    _rewrite(module, lower, set())
    return module


# cm:6d2e91
def lower_struct_array_fields(module: Module) -> Module:
    """Rewrite a field of an indexed struct array element whose index is an
    expression (``m[k ^ 1].f``, ``m[j].f.g[3:0]``) as a part-select of the
    element, ``m[k ^ 1][hi:lo]``, in place, for every engine.

    The parser keeps such an access as an Identifier whose hierarchy holds
    the index as text (``hierarchy=["m[k[0]]"]``); each engine re-read that
    text as a signal name, which only worked for a plain name in the top
    module: reference read and wrote nothing (silently),
    vm/vm-fast raised, and compiled raised or used the wrong element. In a
    submodule even a plain name failed -- flattening prefixed the array
    (``u.m[j]``) but not the text's ``j``. The Simulator runs this on every
    module before flattening, so the index is an expression that flattening
    and generate elaboration rename like any other. A numeric index goes
    the same way (compiled could not resolve one in a submodule:
    ``i_tb.addr_map[0].idx``, the corpus's pulp xbar test benches). A
    constant select of the field is folded into the part-select.
    """
    from .elaborate import _build_param_env, _build_struct_env  # noqa: PLC0415

    if not any(
        isinstance(n, Identifier) and "[" in n.name + "".join(n.hierarchy or ()) for n in iter_nodes(module, set())
    ):
        return module
    _type_map, layouts = _build_struct_env(module)
    if not layouts:
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    field_lsbs = _struct_field_lsbs(module, env)
    # id(select) -> (element, offset, width, lsb, the access as parsed)
    lowered: dict[int, tuple[Expression, int, int, int | None, Identifier]] = {}

    def lower(expr: Expression) -> Expression | None:
        if isinstance(expr, Identifier):
            hit = _struct_array_field(expr, layouts, field_lsbs)
            if hit is None:
                return None
            element, offset, width, lsb = hit
            new = RangeSelect(element, Literal(offset + width - 1, width=32), Literal(offset, width=32))
            lowered[id(new)] = (element, offset, width, lsb, expr)
            return new
        if isinstance(expr, (BitSelect, RangeSelect, PartSelect)) and id(expr.target) in lowered:
            element, offset, width, lsb, original = lowered[id(expr.target)]
            folded = _fold_field_select(expr, element, offset, width, lsb, env)
            if folded is None and isinstance(element, BitSelect) and isinstance(element.index, (Literal, Identifier)):
                # (A dynamic select of a field indexed by a number or a plain
                # name: keep the access as parsed, which the engines resolve
                # in the top module. Lowered, it was a select of a select,
                # which compiled could not write and reference/vm read wrong.)
                expr.target = original
            return folded
        return None

    _rewrite(module, lower, set())
    return module


def _fold_field_select(expr, element, offset: int, width: int, lsb: int | None, env) -> Expression | None:
    """*expr*, a constant bit, range or part select of a lowered struct
    field, as a range select of the array element; else None."""
    if lsb is None:
        return None
    msb_bit: int | None
    lsb_bit: int | None
    if isinstance(expr, PartSelect):
        base, span = const_int(expr.base, env), const_int(expr.width, env)
        if base is None or span is None or span <= 0:
            return None
        msb_bit, lsb_bit = (base + span - 1, base) if expr.direction == "+:" else (base, base - span + 1)
    elif isinstance(expr, BitSelect):
        msb_bit = lsb_bit = const_int(expr.index, env)
    else:
        msb_bit, lsb_bit = const_int(expr.msb, env), const_int(expr.lsb, env)
    if msb_bit is None or lsb_bit is None:
        return None
    hi, lo = msb_bit - lsb, lsb_bit - lsb
    if not 0 <= lo <= hi < width:
        return None  # (out of range or descending: the engines' select rules)
    return RangeSelect(element, Literal(offset + hi, width=32), Literal(offset + lo, width=32))


def _split_path(path: str) -> list[str]:
    """*path* split at the dots outside brackets."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(path):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "." and depth == 0:
            parts.append(path[start:i])
            start = i + 1
    parts.append(path[start:])
    return parts


def _struct_array_field(expr: Identifier, layouts, field_lsbs) -> tuple[Expression, int, int, int | None] | None:
    """(element select, field offset, width, field's declared lsb) of a
    ``base[index].field...`` access with an expression index, else None."""
    from veriforge.transforms.tree_to_model import _build_expression  # noqa: PLC0415
    from veriforge.verilog_parser import verilog_parser  # noqa: PLC0415

    parts = _split_path(".".join([*(expr.hierarchy or ()), expr.name]))
    for k, part in enumerate(parts[:-1]):
        bracket = part.find("[")
        if bracket <= 0 or not part.endswith("]"):
            continue
        index_text = part[bracket + 1 : -1].strip()
        base = ".".join([*parts[:k], part[:bracket]])
        current = base
        offset, width, lsb = 0, 0, None
        for field_name in parts[k + 1 :]:
            layout = layouts.get(current)
            info = layout.fields.get(field_name) if layout is not None else None
            if info is None:
                return None
            field_offset, width = info
            offset += field_offset
            lsb = field_lsbs.get((layout.name, field_name))
            current = f"{current}.{field_name}"
        try:
            index = _build_expression(verilog_parser(start="expression").build_tree(index_text), None)
        except Exception:  # noqa: BLE001  (not an expression: leave the access as it was)
            return None
        return BitSelect(Identifier(base), index), offset, width, lsb
    return None


def _struct_field_lsbs(module: Module, env) -> dict[tuple[str, str], int]:
    """(struct typedef, field) -> the field's declared constant lsb, for
    fields declared descending (``logic [7:4] f`` -> 4); the others are left
    out (a select of them is not folded)."""
    out: dict[tuple[str, str], int] = {}
    for td in getattr(module, "typedefs", None) or ():
        st = getattr(td, "struct_type", None)
        if st is None:
            continue
        for fld in st.fields:
            rng = getattr(fld, "width", None)
            if rng is None:
                out[(td.name, fld.name)] = 0
                continue
            msb, lsb = const_int(rng.msb, env), const_int(rng.lsb, env)
            if msb is not None and lsb is not None and msb >= lsb:
                out[(td.name, fld.name)] = lsb
    return out


def lower_named_arguments(module: Module) -> Module:
    """Rewrite every named-argument user-function call (``f(.rs(a), .b(1))``)
    in *module* to positional order, from the function's declared ports, in
    place -- so no engine sees argument names. A call naming an unknown port,
    or leaving a port out, is an error (default argument values aren't
    modeled)."""
    functions = {f.name: f for f in module.functions}

    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, FunctionCall) or not expr.arg_names:
            return None
        func = functions.get(expr.name)
        if func is None:
            raise ValueError(f"named arguments in a call of unknown function {expr.name!r}")
        by_name = dict(zip(expr.arg_names, expr.arguments, strict=True))
        order = [p.name for p in func.ports]
        unknown = sorted(set(by_name) - set(order))
        missing = [n for n in order if n not in by_name]
        if unknown or missing:
            raise ValueError(
                f"call of {expr.name!r}: "
                + (f"no port {', '.join(unknown)}" if unknown else f"no argument for {', '.join(missing)}")
            )
        expr.arguments = [by_name[n] for n in order]
        expr.arg_names = None
        return None  # (rewritten in place)

    _rewrite(module, lower, set())
    return module


def lower_event_expressions(module: Module) -> Module:
    """Give each event-control entry on an expression other than a plain
    signal (`@(posedge w[0])`, `@(negedge (a & b))`, `@(m[2] or b)`) a
    generated net, in place: `assign __vf_ev{k} = expr;`, the entry then
    naming the net. 1 bit for an edge (IEEE 1364-2005 9.7.2: an edge is on
    the expression's LSB), the expression's width for a level entry. Every
    engine used to ignore such an entry -- the block never ran.
    """
    from veriforge.model.assignments import ContinuousAssign  # noqa: PLC0415
    from veriforge.model.nets import Net, NetKind  # noqa: PLC0415
    from veriforge.model.statements import SensitivityEdge  # noqa: PLC0415

    from .elaborate import _build_param_env  # noqa: PLC0415

    entries = [
        node
        for node in iter_nodes(module, set())
        if isinstance(node, SensitivityEdge)
        and isinstance(node.signal, Expression)
        and not isinstance(node.signal, Identifier)
    ]
    if not entries:
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    decls = _declarations(module, env)

    def width_of(name: str) -> int:
        d = decls.get(name)
        return d[2] if d is not None else 1

    def operand_width(expr: Expression) -> int:
        # (A memory element `m[i]` is a word, not a bit select.)
        base, depth = expr, 0
        while isinstance(base, BitSelect):
            base, depth = base.target, depth + 1
        if isinstance(base, Identifier):
            d = decls.get(base.name)
            if d is not None and d[1] and depth == d[1]:
                return d[2]
        return expr_width(expr, width_of, env)

    for k, entry in enumerate(entries):
        name = f"__vf_ev{k}"
        width = 1 if entry.edge in ("posedge", "negedge") else max(1, operand_width(entry.signal))
        rng = Range(Literal(width - 1, width=32), Literal(0, width=32)) if width > 1 else None
        module.nets.append(Net(name, NetKind.WIRE, width=rng))
        module.continuous_assigns.append(ContinuousAssign(Identifier(name), entry.signal))
        entry.signal = Identifier(name)
    return module


def lower_streaming_targets(module: Module) -> Module:
    """Rewrite each assignment to a streaming concatenation (unpacking,
    IEEE 1800-2017 11.4.14.3), in place, as an assignment to the plain
    concatenation of its targets:

    - ``{>>{a, b}} = s``: ``{a, b} = s``.
    - ``{<<n{a, b}} = s``: ``begin tmp = s; {a, b} = {tmp[n-1:0],
      tmp[2n-1:n], ...}; end`` -- *s* cut into n-bit slices from its LSB
      end, in reverse order: the inverse of ``{<<n{...}}`` as an expression,
      which slices from the MSB end. (Nesting that expression instead made
      compiled's generated C exponentially large.) A continuous assign gets
      a temporary net and two assigns.

    A source wider than the targets is consumed from its left (most
    significant) end, ``s >> (W - T)`` (the LRM's ``{>>{a, b, c}} =
    100'b11111`` gives ``c = 1``); a narrower one is an error. (Verilator
    instead streams all of a wider source for ``<<`` and keeps its left end
    -- known_issues.md.) This used to be a parse error.
    """
    from veriforge.model.assignments import ContinuousAssign  # noqa: PLC0415
    from veriforge.model.nets import Net, NetKind  # noqa: PLC0415
    from veriforge.model.statements import NonblockingAssign, SeqBlock, Statement  # noqa: PLC0415
    from veriforge.model.variables import Variable, VariableKind  # noqa: PLC0415

    from .elaborate import _build_param_env  # noqa: PLC0415

    def is_target(node: object) -> bool:
        return isinstance(node, (BlockingAssign, NonblockingAssign, ContinuousAssign)) and (
            isinstance(node.lhs, StreamingConcatenation)
            or (isinstance(node.lhs, Concatenation) and node.lhs.from_streaming)
        )

    all_targets = [
        node
        for node in iter_nodes(module, set())
        if isinstance(node, (BlockingAssign, NonblockingAssign, ContinuousAssign)) and is_target(node)
    ]
    if not all_targets:
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    module_decls = _declarations(module, env)
    # (A task/function body: its own ports, locals and return variable.)
    scoped: dict[int, dict] = {}
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        tf_decls = [*tf.ports, *tf.locals]
        decls = {**module_decls, **_declarations(module, env, tf_decls)}
        if isinstance(tf, FunctionDecl):
            ret = tf.return_range
            msb = const_int(ret.msb, env) if ret is not None else 0
            lsb = const_int(ret.lsb, env) if ret is not None else 0
            decls[tf.name] = ([], 0, abs(msb - lsb) + 1 if msb is not None and lsb is not None else 1)
        for node in iter_nodes(tf.body, set()):
            if is_target(node):
                scoped[id(node)] = decls

    def lit(value: int) -> Literal:
        return Literal(value, width=32, original_text=f"32'd{value}")

    replacements: dict[int, object] = {}
    extra_assigns: list[ContinuousAssign] = []
    for k, node in enumerate(all_targets):
        decls = scoped.get(id(node), module_decls)

        def width_of(name: str, decls: dict = decls) -> int:
            d = decls.get(name)
            return d[2] if d is not None else 1

        def operand_width(expr: Expression, decls: dict = decls, width_of=width_of) -> int:
            # (A memory element `m[i]` is a word, not a bit select.)
            base, depth = expr, 0
            while isinstance(base, BitSelect):
                base, depth = base.target, depth + 1
            if isinstance(base, Identifier):
                d = decls.get(base.name)
                if d is not None and d[1] and depth == d[1]:
                    return d[2]
            return expr_width(expr, width_of, env)

        lhs = node.lhs
        assert isinstance(lhs, (StreamingConcatenation, Concatenation))  # (is_target)  # noqa: S101
        parts = list(lhs.parts)
        t = sum(operand_width(part) for part in parts)
        w = operand_width(node.rhs)
        if w < t:
            raise ValueError(
                f"Streaming concatenation target needs {t} bits but the source has only {w} (IEEE 1800-2017 11.4.14.3)"
            )
        src: Expression = node.rhs if w == t else BinaryOp(">>", node.rhs, lit(w - t))
        target = Concatenation(parts=parts, loc=lhs.loc)
        if not isinstance(lhs, StreamingConcatenation):
            node.lhs, node.rhs = target, src  # (`>>`)
            continue
        n = const_int(lhs.slice_size, env) if lhs.slice_size is not None else 1
        if n is None or n <= 0:
            raise ValueError(f"Streaming concatenation slice size must be a positive constant: {lhs.slice_size!r}")
        if isinstance(src, Identifier) and w == t and not src.hierarchy:
            tmp = src.name
        else:
            tmp = f"__vf_stream{k}"
        slices: list[Expression] = []
        lo = 0
        while lo < t:
            hi = min(lo + n, t) - 1
            slices.append(RangeSelect(Identifier(tmp), lit(hi), lit(lo)))
            lo = hi + 1
        unpacked = Concatenation(parts=slices)
        if tmp == getattr(src, "name", None):
            node.lhs, node.rhs = target, unpacked
            continue
        tmp_range = Range(lit(t - 1), lit(0))
        if isinstance(node, ContinuousAssign):
            module.nets.append(Net(tmp, NetKind.WIRE, width=tmp_range))
            node.lhs, node.rhs = Identifier(tmp), src
            extra_assigns.append(ContinuousAssign(target, unpacked, loc=node.loc))
        else:
            module.variables.append(Variable(tmp, VariableKind.REG, width=tmp_range))
            second: Statement = (
                NonblockingAssign(target, unpacked)
                if isinstance(node, NonblockingAssign)
                else BlockingAssign(target, unpacked)
            )
            replacements[id(node)] = SeqBlock(statements=[BlockingAssign(Identifier(tmp), src), second])
    module.continuous_assigns.extend(extra_assigns)
    if replacements:
        _replace_statements(module, replacements, set())
    return module


def _replace_statements(obj: object, replacements: dict[int, object], seen: set[int]) -> None:
    """In place: replace every node below *obj* whose id is a key of
    *replacements* with its value."""
    if id(obj) in seen:
        return
    seen.add(id(obj))
    slots: list[str] = []
    for klass in type(obj).__mro__:
        slots.extend(s for s in getattr(klass, "__slots__", ()) if s not in _SKIP_SLOTS)
    for slot in slots:
        value = getattr(obj, slot, None)
        if isinstance(value, VerilogNode):
            if id(value) in replacements:
                setattr(obj, slot, replacements[id(value)])
            else:
                _replace_statements(value, replacements, seen)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, VerilogNode):
                    if id(item) in replacements:
                        value[i] = replacements[id(item)]
                    else:
                        _replace_statements(item, replacements, seen)


def _mentions(module: Module) -> bool:
    return any(
        isinstance(call, FunctionCall) and call.name.lower() in _RANGE_QUERIES | _BIT_FUNCS | {"$bits"}
        for call in iter_nodes(module, set())
    )


def iter_nodes(obj: object, seen: set[int]) -> Iterator[object]:
    """Every node below *obj* (``__slots__`` walk, like ``_rewrite``).
    Unlike ``VerilogNode.find`` it skips non-node list items (a hand-built
    model may hold plain strings there)."""
    if id(obj) in seen:
        return
    seen.add(id(obj))
    yield obj
    for klass in type(obj).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if slot in _SKIP_SLOTS:
                continue
            value = getattr(obj, slot, None)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, (VerilogNode, Range)):
                    yield from iter_nodes(item, seen)
                elif isinstance(item, tuple):  # (an assignment pattern's `(field, value)` pairs)
                    for part in item:
                        if isinstance(part, (VerilogNode, Range)):
                            yield from iter_nodes(part, seen)


def _rewrite(obj: object, fn, seen: set[int]) -> None:
    """Post-order, in place: replace every expression *e* below *obj* for
    which ``fn(e)`` returns a node."""
    if id(obj) in seen:
        return
    seen.add(id(obj))
    slots: list[str] = []
    for klass in type(obj).__mro__:
        slots.extend(s for s in getattr(klass, "__slots__", ()) if s not in _SKIP_SLOTS)
    for slot in slots:
        value = getattr(obj, slot, None)
        if isinstance(value, (VerilogNode, Range)):
            _rewrite(value, fn, seen)
            if isinstance(value, Expression):
                new = fn(value)
                if new is not None:
                    setattr(obj, slot, new)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, (VerilogNode, Range)):
                    _rewrite(item, fn, seen)
                    if isinstance(item, Expression):
                        new = fn(item)
                        if new is not None:
                            value[i] = new
                elif isinstance(item, tuple) and any(isinstance(x, Expression) for x in item):
                    # (An assignment pattern's `(field, value)` pairs: a cast
                    # or $bits in `'{a: 4'(k)}` was left for the engines,
                    # which raised.)
                    parts = list(item)
                    for j, part in enumerate(parts):
                        if isinstance(part, Expression):
                            _rewrite(part, fn, seen)
                            new = fn(part)
                            if new is not None:
                                parts[j] = new
                    value[i] = tuple(parts)


def _dims(width: Range | None, dimensions: list[Range], packed_count: int) -> list[Range]:
    """Dimensions in SystemVerilog query order: unpacked, then packed."""
    element = width if width is not None else Range(Literal(0, width=32), Literal(0, width=32))
    return [*dimensions[packed_count:], *dimensions[:packed_count], element]


def _declarations(
    module: Module, env: dict[str, int], decls: list | None = None
) -> dict[str, tuple[list[tuple[int, int]], int, int]]:
    """name -> (dims as (left, right) pairs, number of unpacked dims,
    packed width), for *module*'s declarations or *decls*."""
    out: dict[str, tuple[list[tuple[int, int]], int, int]] = {}
    all_decls: list[Port | Net | Variable] = (
        [*module.ports, *module.nets, *module.variables] if decls is None else decls
    )
    for decl in all_decls:
        width = decl.width
        if width is None:
            # (`byte b;`: the atom type's own width -- it read as 1 bit.)
            kind = getattr(getattr(decl, "kind", None), "value", None) or getattr(decl, "data_type", None)
            atom = {**INTEGER_ATOM_WIDTHS, "time": 64}.get(kind or "")
            if atom is not None:
                width = Range(Literal(atom - 1, width=32), Literal(0, width=32))
        packed_count = len(decl.dimensions) if decl.packed_only else decl.packed_dim_count
        dims = _dims(width, list(decl.dimensions), packed_count)
        pairs = []
        for rng in dims:
            left, right = const_int(rng.msb, env), const_int(rng.lsb, env)
            if left is None or right is None:
                break
            pairs.append((left, right))
        else:
            n_unpacked = len(decl.dimensions) - packed_count
            packed = 1
            for left, right in pairs[n_unpacked:]:
                packed *= abs(left - right) + 1
            out.setdefault(decl.name, (pairs, n_unpacked, packed))
    return out


def _arg_name(arg: Expression) -> str | None:
    if isinstance(arg, Identifier):
        return ".".join([*arg.hierarchy, arg.name]) if arg.hierarchy else arg.name
    return None


def _fold_range_query(name: str, call: FunctionCall, decls, env) -> Expression | None:
    if not call.arguments:
        return None
    decl = decls.get(_arg_name(call.arguments[0]) or "")
    if decl is None:
        return None
    pairs = decl[0]
    dim = 1
    if len(call.arguments) > 1:
        d = const_int(call.arguments[1], env)
        if d is None:
            return None
        dim = d
    if not 1 <= dim <= len(pairs):
        return None
    left, right = pairs[dim - 1]
    value = {
        "$left": left,
        "$right": right,
        "$high": max(left, right),
        "$low": min(left, right),
        "$size": abs(left - right) + 1,
    }[name]
    return _integer_literal(value)


def _integer_literal(value: int) -> Literal:
    """A folded `$bits`/`$size`/`$left`/... value: these return `integer`, a
    signed 32-bit type (IEEE 1800 20.6, 20.7), so `$size(m) - 5 < 0` is true for
    a 4-element `m` (it was unsigned on every engine)."""
    return Literal(value, width=32, signed=True, original_text=str(value))


def _as_int(count: Expression) -> Expression:
    """`$countones`/`$countbits` return `int`, signed 32-bit (IEEE 1800 20.9)."""
    return FunctionCall("$signed", [count])


_CONTROL_LITERALS = {
    "0": lambda: Literal(0, width=1, original_text="1'b0"),
    "1": lambda: Literal(1, width=1, original_text="1'b1"),
    "x": lambda: Literal(0, width=1, is_x=True, original_text="1'bx"),
}


def _control_bit(arg: Expression, env) -> str | None:
    """A ``$countbits`` control argument's bit: "0", "1" or "x" (z is x
    here), or None if it isn't a constant. Its least significant bit counts
    (IEEE 1800 20.9)."""
    if isinstance(arg, Literal):
        text = (arg.original_text or "").strip().lower().replace("_", "")
        if text in ("'0", "'1", "'x", "'z"):
            return {"z": "x"}.get(text[1], text[1])
        if arg.is_x or arg.is_z:
            return "x"
        if text:
            from veriforge.sim.value import Value  # noqa: PLC0415

            try:
                lit = Value.from_verilog(text)
            except ValueError:
                lit = None
            if lit is not None:
                return "x" if lit.mask & 1 else str(lit.val & 1)
    value = const_int(arg, env)
    return None if value is None else str(value & 1)


def _count_bits(arg: Expression, decls, env, width_of, controls: list[str]) -> Expression:
    """``32'd0 + (bit === c0 || bit === c1 ...) + ...`` over every bit of *arg*."""
    bits: list[Expression] = []
    name = _arg_name(arg)
    decl = decls.get(name or "")
    if name is not None and decl is not None and decl[1] == 0 and len(decl[0]) == 1:
        # A plain vector: select each bit in its own declared coordinates.
        left, right = decl[0][0]
        bits = [BitSelect(arg, Literal(i, width=32)) for i in range(min(left, right), max(left, right) + 1)]
    elif (
        isinstance(arg, BitSelect)
        and (target := decls.get(_arg_name(arg.target) or "")) is not None
        and target[1] == 1
        and len(target[0]) == 2
    ):
        # One element of a 1-D memory with a plain packed element.
        left, right = target[0][1]
        bits = [BitSelect(arg, Literal(i, width=32)) for i in range(min(left, right), max(left, right) + 1)]
    else:
        # Any other expression: bit i is (arg >> i) & 1.
        w = expr_width(arg, width_of, env)
        bits = [BinaryOp("&", BinaryOp(">>", arg, Literal(i, width=32)), Literal(1, width=1)) for i in range(w)]
    total: Expression = Literal(0, width=32, original_text="32'd0")
    for bit in bits:
        match: Expression | None = None
        for control in controls:
            test = BinaryOp("===", bit, _CONTROL_LITERALS[control]())
            match = test if match is None else BinaryOp("||", match, test)
        assert match is not None  # (at least one control)
        total = BinaryOp("+", total, match)
    return total


def lower_width_casts(module: Module) -> Module:
    """Rewrite every ``N'(e)`` (WidthCast) in *module*'s behavioral code,
    continuous assigns and declaration initializers, in place, as a call
    to a generated function::

        function automatic [signed] [N-1:0] __vf_cast_N_s(input [signed] [N-1:0] v);
            __vf_cast_N_s = v;
        endfunction

    Binding *e* to the N-bit input is an assignment, which is exactly the
    cast's rule (IEEE 1800 6.24.1): *e* evaluated at max(N, its own width),
    then truncated or extended to N with its signedness; and a call is
    self-determined --
    so every engine gets cast semantics from its existing user-function
    support. (Parameter and range expressions keep the WidthCast node,
    which semantics' constant folding understands.)

    Before, the parser dropped the cast and kept only *e*, so its operators
    took the enclosing context: ``y32 = 16'(a16 + b16)`` kept the carry
    and ``y8 = 4'(x)`` didn't truncate.
    """
    from .elaborate import _build_param_env  # noqa: PLC0415

    nodes = list(iter_nodes(module, set()))
    if not any(isinstance(node, WidthCast) or _cast_call_shape(node) for node in nodes):
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    all_decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    signed_names = {d.name for d in all_decls if d.signed}
    # Parameters too: `12'(p)` of a signed parameter is signed (it used to
    # be zero-extended into a wider context).
    signed_names |= {p.name for p in module.parameters if getattr(p, "signed", False)}
    made: dict[str, FunctionDecl] = {}
    func_signed = {f.name: bool(f.signed) for f in module.functions}

    def make_lower(scope_signed: set[str]) -> Callable[[Expression], Expression | None]:
        def lower(expr: Expression) -> Expression | None:
            if not isinstance(expr, WidthCast):
                return None
            n = const_int(expr.width, env)
            if n is None or n <= 0:
                return None
            # (A nested cast was already lowered to one of the generated
            # functions -- they count too: `1'(12'(s))` of a signed `s` is
            # signed.)
            signed = expr_signed(
                expr.operand,
                lambda name: name in scope_signed,
                lambda f: bool(made[f].signed) if f in made else func_signed.get(f, False),
            )
            name = f"__vf_cast_{n}_{'s' if signed else 'u'}"
            if name not in made:
                made[name] = _cast_function(name, n, signed)
            return FunctionCall(name=name, arguments=[expr.operand], loc=expr.loc)

        return lower

    lower = make_lower(signed_names)
    seen: set[int] = set()
    for container in (module.always_blocks, module.initial_blocks, module.continuous_assigns):
        for node in container:
            _rewrite(node, lower, seen)
    # Inside a function or task, its own ports and locals are in scope (a
    # signed port `a` made `7'(a)` signed; it used to read as unsigned).
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        local_decls = [*tf.ports, *getattr(tf, "locals", ()), *getattr(tf, "local_vars", ())]
        local_names = {d.name for d in local_decls}
        scope = (signed_names - local_names) | {d.name for d in local_decls if d.signed}
        _rewrite(tf, make_lower(scope), seen)
    inits: list[Net | Variable] = [*module.nets, *module.variables]
    for decl in inits:
        init = decl.initial_value
        if init is not None:
            _rewrite(init, lower, seen)
            new = lower(init)
            if new is not None:
                decl.initial_value = new
    # Calls already lowered by an earlier Simulator: the pass rewrites
    # expression nodes in place, and a design's submodule nodes are shared
    # by every flattening of it, so a second Simulator on the same design
    # sees the calls but needs its own declarations.
    for call in nodes:
        shape = _cast_call_shape(call)
        if shape is not None and isinstance(call, FunctionCall) and call.name not in made:
            made[call.name] = _cast_function(call.name, *shape)
    existing = {f.name for f in module.functions}
    module.functions.extend(f for name, f in made.items() if name not in existing)
    return module


_CAST_CALL = re.compile(r"__vf_cast_(\d+)_([su])")


def _cast_call_shape(node: object) -> tuple[int, bool] | None:
    if isinstance(node, FunctionCall) and (m := _CAST_CALL.fullmatch(node.name)):
        return int(m.group(1)), m.group(2) == "s"
    return None


def _cast_function(name: str, n: int, signed: bool) -> FunctionDecl:  # noqa: FBT001
    def rng() -> Range:
        return Range(Literal(n - 1, width=32), Literal(0, width=32))

    return FunctionDecl(
        name,
        return_range=rng(),
        signed=signed,
        is_automatic=True,
        ports=[Port("v", PortDirection.INPUT, width=rng(), signed=signed)],
        body=BlockingAssign(Identifier(name), Identifier("v")),
    )


def materialize_task_locals(module: Module) -> Module:
    """Hoist every task's local variables to module variables named
    ``__task_<task>__<var>``, renaming the references in its body, in place.

    That is a Verilog-2001 static task variable, and lets every engine use
    a task's locals without knowing about them (none did: the parser used
    to drop them). An ``automatic`` task re-entered concurrently from two
    processes would share them -- not supported.
    """
    for task in module.tasks:
        if not task.locals:
            continue
        renames = {var.name: f"__task_{task.name}__{var.name}" for var in task.locals}
        for var in task.locals:
            hoisted = copy.copy(var)
            hoisted.name = renames[var.name]
            hoisted.initial_value = None  # per-call initializers are body statements
            module.variables.append(hoisted)
        task.locals = []
        if task.body is not None:
            for node in iter_nodes(task.body, set()):
                if isinstance(node, Identifier) and not node.hierarchy and node.name in renames:
                    node.name = renames[node.name]
    return module


# SystemVerilog 2-state types (IEEE 1800 6.11): start at 0, and x/z bits
# become 0 when assigned.
_TWO_STATE_KINDS = frozenset({"bit", "byte", "shortint", "int", "longint"})
TWO_STATE_CALL = "$__2state"


def is_two_state(decl: object) -> bool:
    """Whether a variable/port declaration has a 2-state type."""
    kind = getattr(getattr(decl, "kind", None), "value", None)
    return kind in _TWO_STATE_KINDS or getattr(decl, "data_type", None) in _TWO_STATE_KINDS


def lower_two_state(module: Module) -> Module:
    """2-state variables, in place: give each one without an initializer a
    0 initializer, and wrap the RHS of every assignment to one in the
    internal ``$__2state(e)`` -- context/width/sign-transparent, it only
    clears x/z bits (every engine implements it next to ``$signed``).

    Before 2026-10-09 no engine modeled 2-state types: `int i;` read x and
    `i = 'x` stored x (Icarus: 0). A 2-state memory's elements start at 0 in
    each engine's own memory setup (`is_two_state`).
    """
    from veriforge.model.assignments import ContinuousAssign  # noqa: PLC0415
    from veriforge.model.statements import NonblockingAssign  # noqa: PLC0415

    two_state_decls: list[Port | Variable] = [*module.ports, *module.variables]
    module_two_state = {d.name for d in two_state_decls if is_two_state(d)}
    if not module_two_state and not any(
        is_two_state(v) for sub in [*module.functions, *module.tasks] for v in getattr(sub, "locals", [])
    ):
        return module

    for var in module.variables:
        if var.name not in module_two_state or var.dimensions:
            continue
        if var.initial_value is None:
            var.initial_value = Literal(0, base="d", signed=False, original_text="0")
        elif not _is_two_state_call(var.initial_value):
            var.initial_value = FunctionCall(name=TWO_STATE_CALL, arguments=[var.initial_value], is_system=True)

    # (A 2-state output port too: `output int y` read x until written.)
    for port in module.ports:
        if port.name in module_two_state and port.direction != PortDirection.INPUT and port.default_value is None:
            port.default_value = Literal(0, base="d", signed=False, original_text="0")

    def rewrite(root: object, names: set[str]) -> None:
        for node in iter_nodes(root, set()):
            if isinstance(node, (BlockingAssign, NonblockingAssign, ContinuousAssign)):
                base = _lhs_base_name(node.lhs)
                if base in names and not _is_two_state_call(node.rhs):
                    node.rhs = FunctionCall(name=TWO_STATE_CALL, arguments=[node.rhs], is_system=True)

    for block in [*module.always_blocks, *module.initial_blocks, *module.continuous_assigns, *module.tasks]:
        rewrite(block, module_two_state)
    for func in module.functions:
        shadow = {func.name, *(p.name for p in func.ports), *(v.name for v in func.locals)}
        local_two_state = {v.name for v in func.locals if is_two_state(v)}
        if func.body is not None:
            rewrite(func.body, (module_two_state - shadow) | local_two_state)
            # An automatic function's locals start at their default each call.
            inits = [
                BlockingAssign(Identifier(v.name), Literal(0, base="d", signed=False, original_text="0"))
                for v in func.locals
                if v.name in local_two_state and v.initial_value is None and not v.dimensions
            ]
            if inits:
                from veriforge.model.statements import SeqBlock  # noqa: PLC0415

                func.body = SeqBlock(statements=[*inits, func.body])
    return module


def _lhs_base_name(lhs: Expression) -> str | None:
    while isinstance(lhs, (BitSelect,)) or type(lhs).__name__ in ("RangeSelect", "PartSelect"):
        lhs = lhs.target  # type: ignore[attr-defined]
    return _arg_name(lhs)


def _is_two_state_call(expr: Expression) -> bool:
    return isinstance(expr, FunctionCall) and expr.name == TWO_STATE_CALL
