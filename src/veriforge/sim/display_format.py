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

import math
from collections.abc import Iterator
from typing import TYPE_CHECKING

from veriforge.model.expressions import Expression, StringLiteral
from veriforge.model.functions import FunctionDecl, TaskDecl
from veriforge.model.base import VerilogNode
from veriforge.model.statements import ParBlock, SeqBlock, SystemTaskCall
from veriforge.model.variables import Variable
from veriforge.semantics import expr_signed, signed_memory_names

from .sv_functions import iter_nodes

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.nets import Net
    from veriforge.model.parameters import Parameter
    from veriforge.model.ports import Port

    from .value import Value

SIGNED_FLAG = "\x1d"
# Specifiers that consume an argument (the engines' formatters agree).
CONSUMING = frozenset("dhxobcstefg")
_DISPLAY_TASKS = {"$display", "$write", "$monitor", "$strobe", "$info", "$warning", "$error", "$fatal"}
_FILE_TASKS = {"$fdisplay", "$fwrite"}
_RADIX_TASKS = {
    f"{base}{radix}"
    for base in ("$display", "$write", "$strobe", "$monitor", "$fdisplay", "$fwrite")
    for radix in "hbo"
}
_RADIX_BITS = {"h": 4, "x": 4, "o": 3, "b": 1}
_DIGITS = "0123456789abcdef"
_SCOPE_TASKS = {
    "$display", "$write", "$strobe", "$monitor", "$fdisplay", "$fwrite",
    "$info", "$warning", "$error", "$fatal",
}  # fmt: skip


def decimal_width(width: int, signed: bool) -> int:  # noqa: FBT001
    """Columns ``%d`` pads a *width*-bit value to."""
    if width <= 1:
        return 1  # a 1-bit signed value too (Icarus: `[0]`, not `[ 0]`)
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


def format_real(  # noqa: PLR0913
    v: Value,
    spec: str,
    field_width: int,
    precision: int | None,
    zero_pad: bool,
    signed: bool,  # noqa: FBT001
) -> str:
    """``%e``/``%f``/``%g`` of an integer argument, as C/Icarus print it (x/z
    bits read as 0). There is no ``real`` type in this simulator yet, so
    the argument is always the integer value."""
    width = max(v.width, 1)
    # x/z bits read as 0 (as in Icarus: `8'bx0000001` prints 1.0)
    val = v.val & ~v.mask & ((1 << width) - 1)
    if signed and val >> (width - 1):
        val -= 1 << width
    pad = "0" if zero_pad and field_width else ""
    try:
        real = float(val)
    except OverflowError:  # beyond double range (> ~1024 bits): +/-inf
        real = -math.inf if val < 0 else math.inf
    return format(real, f"{pad}{field_width or ''}.{6 if precision is None else precision}{spec}")


_SCOPE_ATTR = "__vf_scope"


def mark_instance_scope(node: object, prefix: str) -> None:
    """Flattening: record that the display tasks below *node* now live in
    instance *prefix* (relative to the enclosing module; an outer level
    prepends its own prefix when it inlines this one in turn)."""
    for task in _iter_scope_tasks(node, set()):
        inner = task.attributes.get(_SCOPE_ATTR)
        task.attributes[_SCOPE_ATTR] = f"{prefix}.{inner}" if inner else prefix


def substitute_scope_names(module: Module) -> None:
    """Replace ``%m`` in every display-family format string in the flattened
    *module*, in place, with the task's hierarchical scope name as Icarus
    prints it: the top module, the instance path recorded by
    ``mark_instance_scope``, then any enclosing named block or function/
    task (``top.u_sub.blk``). It used to print a ``<module>`` placeholder."""
    _substitute_scope(module, "", module.name, set())


def _iter_scope_tasks(node: object, seen: set[int]) -> Iterator[SystemTaskCall]:
    for n in iter_nodes(node, seen):
        if isinstance(n, SystemTaskCall) and n.task_name.lower().rstrip("hbo") in _SCOPE_TASKS:
            yield n


def _substitute_scope(node: object, named: str, top: str, seen: set[int]) -> None:
    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, (FunctionDecl, TaskDecl)) or (isinstance(node, (SeqBlock, ParBlock)) and node.name):
        named = f"{named}.{node.name}"
    if isinstance(node, SystemTaskCall):
        if node.task_name.lower().rstrip("hbo") in _SCOPE_TASKS:
            inst = node.attributes.get(_SCOPE_ATTR)
            scope = f"{top}{'.' + inst if inst else ''}{named}".replace("%", "%%")
            for arg in node.arguments:
                if isinstance(arg, StringLiteral) and "%" in arg.value:
                    arg.value = _replace_scope_spec(arg.value, scope)
        return
    for klass in type(node).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if slot in ("parent", "loc", "_parse_tree", "attributes", "comments", "drivers", "loads"):
                continue
            value = getattr(node, slot, None)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, VerilogNode):
                    _substitute_scope(item, named, top, seen)


def _replace_scope_spec(fmt: str, scope: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(fmt):
        if fmt[i] == "%" and i + 1 < len(fmt):
            if fmt[i + 1] in "mM":
                out.append(scope)
            else:
                out.append(fmt[i : i + 2])  # incl. "%%"
            i += 2
            continue
        out.append(fmt[i])
        i += 1
    return "".join(out)


def lower_display_args(module: Module) -> Module:
    """Canonicalize every display-family call in *module*, in place (see
    the module docstring)."""
    substitute_scope_names(module)
    module_signed = _signed_names(module)
    func_signed = {f.name: bool(f.signed) for f in module.functions}
    memories = signed_memory_names(module)  # (`%d` of a signed memory's word is signed)
    done: set[int] = set()
    # Function/task bodies first, so their own ports and locals count too.
    subs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for sub in subs:
        local_signed = module_signed | _signed_names(sub)
        for node in iter_nodes(sub, set()):
            if isinstance(node, SystemTaskCall) and id(node) not in done:
                done.add(id(node))
                _lower_task(node, local_signed, func_signed, memories)
    for node in iter_nodes(module, set()):
        if isinstance(node, SystemTaskCall) and id(node) not in done:
            _lower_task(node, module_signed, func_signed, memories)
    return module


def _lower_task(
    task: SystemTaskCall, signed_names: set[str], func_signed: dict[str, bool], memories: frozenset[str] = frozenset()
) -> None:
    name = task.task_name.lower()
    default_spec = "d"
    if name in _RADIX_TASKS:
        # $displayh/$writeb/$fdisplayo/...: the plain task, with leftover
        # arguments in that radix (they used to be ignored entirely).
        name, default_spec = name[:-1], name[-1]
        task.task_name = name
    args = list(task.arguments)
    if name in _FILE_TASKS:
        keep = 1
    elif name == "$fatal" and args and not isinstance(args[0], StringLiteral):
        keep = 1  # finish_number
    elif name in _DISPLAY_TASKS:
        keep = 0
    else:
        return
    if len(args) <= keep:
        return
    fmt, data = _canonical(
        args[keep:], (lambda n: n in signed_names, lambda f: func_signed.get(f, False), memories), default_spec
    )
    task.arguments = [*args[:keep], fmt, *data]


def _signed_names(scope: Module | FunctionDecl | TaskDecl) -> set[str]:
    names: set[str] = set()
    if isinstance(scope, (FunctionDecl, TaskDecl)):
        # Ports, plus every variable declared anywhere inside (function
        # locals, and a task's block-level declarations).
        all_decls: list[Port | Net | Variable | Parameter] = list(scope.ports)
        all_decls += [n for n in iter_nodes(scope, set()) if isinstance(n, Variable)]
    else:
        all_decls = [*scope.ports, *scope.nets, *scope.variables, *scope.parameters]
    for decl in all_decls:
        # `Variable.signed` already defaults by kind (`integer` signed,
        # `int unsigned` not).
        if getattr(decl, "signed", False):
            names.add(decl.name)
    return names


def _canonical(args: list[Expression], signed_of, default_spec: str = "d") -> tuple[StringLiteral, list[Expression]]:
    out: list[str] = []
    data: list[Expression] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if not isinstance(arg, StringLiteral):
            data.append(arg)
            flag = SIGNED_FLAG if default_spec == "d" and _signed(arg, signed_of) else ""
            out.append(f"%{flag}{default_spec}")
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
            while k < len(fmt) and (fmt[k].isdigit() or fmt[k] == "."):  # width[.precision]
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
                if spec in "defg" and SIGNED_FLAG not in piece and _signed(consumed, signed_of):
                    piece = f"%{SIGNED_FLAG}{piece[1:]}"
            out.append(piece)
            j = k + 1
    return StringLiteral(value="".join(out)), data


def _signed(expr: Expression, signed_of) -> bool:
    """*signed_of*: (signal signedness, function return signedness, signed
    memory names)."""
    try:
        return expr_signed(expr, *signed_of)
    except Exception:  # noqa: BLE001 -- unusual expression: treat as unsigned
        return False
