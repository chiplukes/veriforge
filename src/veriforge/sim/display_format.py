"""``$display``-family argument formatting (IEEE 1800 21.2.1), with Icarus's
conventions, shared by every engine:

- ``%h``/``%x``/``%o``/``%b`` print every digit of the value's width
  (``8'h5`` -> ``05``); ``%0h`` strips leading zeros. A digit whose bits are
  all x prints ``x``, one with only some x bits ``X`` (z shares x's encoding
  in this 4-state model, so it prints as x).
- ``%d`` is right-justified to the widest decimal of the value's width
  (one column more when signed: ``-128``); ``%0d`` doesn't pad. An all-x
  value prints ``x``, a partly-x one ``X``. Signed arguments print negative.
- An explicit field width only pads (spaces, or zeros with ``%0<w>``).
- An argument not consumed by a format string prints as ``%d``; every
  string-literal argument is itself a format string.

``lower_display_args`` (run before any engine sees the module) rewrites
each display-family call to one format string with exactly one argument
per data-consuming specifier, and flags each signed ``%d`` argument with
``SIGNED_FLAG`` right after its ``%`` -- so the engines need neither
signedness analysis nor the leftover-argument rule.

Before this, every engine printed any value containing x as a lone ``x``,
never padded, printed signed values as unsigned and dropped leftover
arguments; compiled ignored x bits entirely and truncated wide values to
64 bits; vm-fast printed a wide ``%h`` as 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import Expression, StringLiteral
from veriforge.model.statements import SystemTaskCall
from veriforge.semantics import expr_signed

from .sv_functions import iter_nodes

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.nets import Net
    from veriforge.model.ports import Port
    from veriforge.model.variables import Variable

    from .value import Value

SIGNED_FLAG = "\x1d"
# Specifiers that consume an argument (the engines' formatters agree).
CONSUMING = frozenset("dhxobcst")
_DISPLAY_TASKS = {"$display", "$write", "$monitor", "$strobe", "$info", "$warning", "$error", "$fatal"}
_FILE_TASKS = {"$fdisplay", "$fwrite"}
_RADIX_BITS = {"h": 4, "x": 4, "o": 3, "b": 1}
_DIGITS = "0123456789abcdef"
_SIGNED_KINDS = {"integer", "int", "shortint", "longint", "byte"}


def decimal_width(width: int, signed: bool) -> int:  # noqa: FBT001
    """Columns ``%d`` pads a *width*-bit value to."""
    if width <= 0:
        return 1
    return len(str(-(1 << (width - 1)))) if signed else len(str((1 << width) - 1))


def format_value(v: Value, spec: str, field_width: int, zero_pad: bool, signed: bool) -> str:  # noqa: FBT001
    """Format one argument for ``%d``/``%h``/``%x``/``%o``/``%b``."""
    width = max(v.width, 1)
    full = (1 << width) - 1
    mask, val = v.mask & full, v.val & full
    if spec == "d":
        if mask:
            s = "x" if mask == full else "X"
        else:
            s = str(val - (1 << width) if signed and val >> (width - 1) else val)
        if field_width:
            return s.rjust(field_width, "0" if zero_pad else " ")
        return s if zero_pad else s.rjust(decimal_width(width, signed))
    bits = _RADIX_BITS[spec]
    chars = []
    for d in range((width + bits - 1) // bits - 1, -1, -1):
        shift = d * bits
        group = (1 << min(bits, width - shift)) - 1
        m = (mask >> shift) & group
        if not m:
            chars.append(_DIGITS[(val >> shift) & group])
        else:
            chars.append("x" if m == group else "X")
    s = "".join(chars)
    if field_width:
        return s.rjust(field_width, "0" if zero_pad else " ")
    if zero_pad:
        s = s.lstrip("0") or "0"
    return s


def lower_display_args(module: Module) -> Module:
    """Canonicalize every display-family call in *module*, in place (see
    the module docstring)."""
    signed_names = _signed_names(module)

    def signed_of(name: str) -> bool:
        return name in signed_names

    for task in iter_nodes(module, set()):
        if not isinstance(task, SystemTaskCall):
            continue
        name = task.task_name.lower()
        args = list(task.arguments)
        if name in _FILE_TASKS:
            keep = 1
        elif name == "$fatal" and args and not isinstance(args[0], StringLiteral):
            keep = 1  # finish_number
        elif name in _DISPLAY_TASKS:
            keep = 0
        else:
            continue
        if len(args) <= keep:
            continue
        fmt, data = _canonical(args[keep:], signed_of)
        task.arguments = [*args[:keep], fmt, *data]
    return module


def _signed_names(module: Module) -> set[str]:
    names: set[str] = set()
    all_decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    for decl in all_decls:
        kind = getattr(decl, "kind", None)
        if getattr(decl, "signed", False) or getattr(kind, "value", None) in _SIGNED_KINDS:
            names.add(decl.name)
    return names


def _canonical(args: list[Expression], signed_of) -> tuple[StringLiteral, list[Expression]]:
    out: list[str] = []
    data: list[Expression] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if not isinstance(arg, StringLiteral):
            data.append(arg)
            out.append(f"%{SIGNED_FLAG if _signed(arg, signed_of) else ''}d")
            continue
        fmt = arg.value
        j = 0
        while j < len(fmt):
            ch = fmt[j]
            if ch == "\\" and j + 1 < len(fmt):
                out.append(fmt[j : j + 2])
                j += 2
                continue
            if ch != "%":
                out.append(ch)
                j += 1
                continue
            k = j + 1
            if k < len(fmt) and fmt[k] == SIGNED_FLAG:
                k += 1
            while k < len(fmt) and fmt[k].isdigit():
                k += 1
            if k >= len(fmt):
                out.append(fmt[j:])
                break
            spec = fmt[k].lower()
            piece = fmt[j : k + 1]
            if spec in CONSUMING and i < len(args):
                consumed = args[i]
                i += 1
                data.append(consumed)
                if spec == "d" and SIGNED_FLAG not in piece and _signed(consumed, signed_of):
                    piece = f"%{SIGNED_FLAG}{piece[1:]}"
            out.append(piece)
            j = k + 1
    return StringLiteral(value="".join(out)), data


def _signed(expr: Expression, signed_of) -> bool:
    try:
        return expr_signed(expr, signed_of)
    except Exception:  # noqa: BLE001 -- unusual expression: treat as unsigned
        return False
