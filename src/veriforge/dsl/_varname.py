"""Infer a DSL declaration's name from the variable its result is stored in.

``x = m.wire(width=8)`` names the wire ``x``. Python has no supported way for a
function to see its assignment target, so this reads the caller's bytecode
(as Amaranth's ``tracer.get_var_name`` does): the call that is running must be
followed by a store to a plain name, directly or through a forward jump::

    CALL            (CALL_FUNCTION / CALL_METHOD / CALL_KW / ... on older Pythons)
    [JUMP_FORWARD]  (from the first branch of ``x = m.wire() if c else ...``)
    STORE_FAST x    (or STORE_NAME / STORE_GLOBAL / STORE_DEREF, or 3.13+'s
                     STORE_FAST_LOAD_FAST for ``x = m.wire(); return x``)

Anything else returns None, so the caller can ask for an explicit name rather
than guess: attribute and subscript targets, ``a = b = ...``, unpacking,
walrus, a call used as an argument or returned, and any instruction a future
Python adds. Non-CPython interpreters (PyPy) always return None.

A call inside a loop body returns None too, even on the loop's first pass:
every pass would store into the same variable, so the name would repeat, and
refusing up front makes the result independent of the iteration count.

Each code object is disassembled once (cached by identity).
"""

from __future__ import annotations

import bisect
import dis
import itertools
import sys
from types import CodeType, FrameType

_CALLS = frozenset({"CALL", "CALL_KW", "CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_METHOD", "CALL_FUNCTION_EX"})
_STORES = frozenset({"STORE_FAST", "STORE_NAME", "STORE_GLOBAL", "STORE_DEREF"})
_SKIP = frozenset({"CACHE", "EXTENDED_ARG"})
_JUMPS = frozenset({"JUMP_FORWARD", "JUMP_ABSOLUTE"})  # forward only; JUMP_ABSOLUTE is 3.10

SUPPORTED = sys.implementation.name == "cpython"


def _stores_after_calls(code: CodeType) -> dict[int, str]:
    """{bytecode offset inside a call: the plain name the call's result is stored in}."""
    out: dict[int, str] = {}
    ins = [i for i in dis.get_instructions(code) if i.opname not in _SKIP]
    for call, after in itertools.pairwise(ins):
        if call.opname not in _CALLS:
            continue
        nxt = after
        # `x = m.wire() if c else ...`: the first branch jumps forward to the store.
        while nxt.opname in _JUMPS and nxt.argval > nxt.offset:
            nxt = ins[bisect.bisect_left(ins, nxt.argval, key=lambda i: i.offset)]
        if nxt.opname in _STORES:
            name = nxt.argval
        elif nxt.opname == "STORE_FAST_LOAD_FAST":
            name = nxt.argval[0]
        else:
            continue
        # A running caller's f_lasti is the call itself (3.10, 3.13+) or the call's
        # last inline CACHE slot (3.11, 3.12): map every offset up to the next instruction.
        for off in range(call.offset, after.offset, 2):
            out[off] = name
    return out


_ANY_JUMP = frozenset(dis.hasjrel) | frozenset(dis.hasjabs) | frozenset(getattr(dis, "hasjump", ()))
_FLOW_ENDS = frozenset(
    {
        "JUMP_FORWARD",
        "JUMP_BACKWARD",
        "JUMP_ABSOLUTE",
        "JUMP_BACKWARD_NO_INTERRUPT",
        "RETURN_VALUE",
        "RETURN_CONST",
        "RAISE_VARARGS",
        "RERAISE",
    }
)
# 3.10's exception-handler setup: the target is a handler, not normal flow.
_HANDLER_SETUP = frozenset({"SETUP_FINALLY", "SETUP_WITH", "SETUP_ASYNC_WITH"})


def _loops(code: CodeType) -> list[tuple[int, int]]:
    """(header offset, back-jump offset) of every loop: a backward jump reached by
    normal control flow from the function's entry.

    Exception handlers (reached only through the exception table, and placed
    out of line on 3.11+) jump back to the code after a ``with``/``try``/
    ``await``; walking normal flow only keeps those from looking like loops.
    ``JUMP_BACKWARD_NO_INTERRUPT`` is ``await``/``yield from``'s own resend loop.
    """
    ins = [i for i in dis.get_instructions(code) if i.opname != "CACHE"]
    index = {i.offset: n for n, i in enumerate(ins)}
    reached: set[int] = set()
    todo = [0]
    while todo:
        n = todo.pop()
        while n < len(ins) and n not in reached:
            reached.add(n)
            i = ins[n]
            if i.opcode in _ANY_JUMP and i.opname not in _HANDLER_SETUP and i.argval in index:
                todo.append(index[i.argval])
            if i.opname in _FLOW_ENDS:
                break
            n += 1
    return [
        (i.argval, i.offset)
        for n, i in enumerate(ins)
        if n in reached
        and i.opcode in _ANY_JUMP
        and i.opname not in _HANDLER_SETUP
        and i.opname != "JUMP_BACKWARD_NO_INTERRUPT"
        and isinstance(i.argval, int)
        and i.argval < i.offset
    ]


def _calls_in_loops(code: CodeType) -> dict[int, tuple[int, int]]:
    """{bytecode offset inside a call within a loop body: the innermost loop's
    (header offset, back-jump offset)}."""
    loops = _loops(code)
    out: dict[int, tuple[int, int]] = {}
    if not loops:
        return out
    ins = [i for i in dis.get_instructions(code) if i.opname not in _SKIP]
    for call, after in itertools.pairwise(ins):
        if call.opname not in _CALLS:
            continue
        around = [(head, back) for head, back in loops if head <= call.offset < back]
        if around:
            for off in range(call.offset, after.offset, 2):
                out[off] = max(around)
    return out


# id(code) -> (code, stores, calls in loops). Keyed by identity because hashing a
# code object hashes its whole bytecode; holding the code keeps its id from being reused.
_CACHE: dict[int, tuple[CodeType, dict[int, str], dict[int, tuple[int, int]]]] = {}
_CACHE_SIZE = 4096


def _cached_maps(code: CodeType) -> tuple[dict[int, str], dict[int, tuple[int, int]]]:
    entry = _CACHE.get(id(code))
    if entry is not None and entry[0] is code:
        return entry[1], entry[2]
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.clear()
    stores, in_loops = _stores_after_calls(code), _calls_in_loops(code)
    _CACHE[id(code)] = (code, stores, in_loops)
    return stores, in_loops


def assigned_name(frame: FrameType) -> str | None:
    """The plain variable *frame*'s current call result is stored in, or None
    (also None inside a loop body, where every pass would reuse the name)."""
    if not SUPPORTED:
        return None
    stores, in_loops = _cached_maps(frame.f_code)
    lasti = frame.f_lasti
    return None if lasti in in_loops else stores.get(lasti)


def stored_name(frame: FrameType) -> str | None:
    """Like :func:`assigned_name`, but also inside a loop (for error messages)."""
    if not SUPPORTED:
        return None
    return _cached_maps(frame.f_code)[0].get(frame.f_lasti)


def innermost_loop(frame: FrameType) -> tuple[int, int] | None:
    """(header offset, back-jump offset) of the innermost loop around *frame*'s
    current call, or None outside loops."""
    if not SUPPORTED:
        return None
    return _cached_maps(frame.f_code)[1].get(frame.f_lasti)


def call_site(frame: FrameType) -> tuple[CodeType, int] | None:
    """(code, offset) of *frame*'s current call, to compare with :func:`innermost_loop`."""
    return (frame.f_code, frame.f_lasti) if SUPPORTED else None


def enclosing_loop(frame: FrameType) -> tuple[bool, str | None]:
    """Whether *frame*'s current call is inside a loop body, and the innermost
    loop's variable (``i`` for ``for i in ...``; None for ``while`` or unpacking)."""
    loop = innermost_loop(frame)
    if loop is None:
        return False, None
    header = loop[0]
    ins = [i for i in dis.get_instructions(frame.f_code) if i.opname not in _SKIP]
    at = bisect.bisect_left(ins, header, key=lambda i: i.offset)
    if at + 1 < len(ins) and ins[at].opname == "FOR_ITER":
        target = ins[at + 1]
        if target.opname in _STORES:
            return True, target.argval
        if target.opname == "STORE_FAST_LOAD_FAST":
            return True, target.argval[0]
    return True, None


def attribute_after_call(frame: FrameType) -> str | None:
    """The attribute read straight off *frame*'s current call result
    (``comment`` for ``m.wire().comment(...)``), for error hints; not cached."""
    if not SUPPORTED:
        return None
    ins = [i for i in dis.get_instructions(frame.f_code) if i.opname not in _SKIP]
    for call, nxt in itertools.pairwise(ins):
        if call.offset <= frame.f_lasti < nxt.offset:
            ok = call.opname in _CALLS and nxt.opname in ("LOAD_ATTR", "LOAD_METHOD")
            return nxt.argval if ok else None
    return None


# IEEE 1800-2017 Annex B (a superset of the 1364-2005 keywords): a Python
# variable may be called ``output`` or ``begin``; a signal may not.
VERILOG_KEYWORDS = frozenset(
    """
    accept_on alias always always_comb always_ff always_latch and assert assign assume automatic
    before begin bind bins binsof bit break buf bufif0 bufif1 byte case casex casez cell chandle
    checker class clocking cmos config const constraint context continue cover covergroup coverpoint
    cross deassign default defparam design disable dist do edge else end endcase endchecker endclass
    endclocking endconfig endfunction endgenerate endgroup endinterface endmodule endpackage
    endprimitive endprogram endproperty endspecify endsequence endtable endtask enum event eventually
    expect export extends extern final first_match for force foreach forever fork forkjoin function
    generate genvar global highz0 highz1 if iff ifnone ignore_bins illegal_bins implements implies
    import incdir include initial inout input inside instance int integer interconnect interface
    intersect join join_any join_none large let liblist library local localparam logic longint
    macromodule matches medium modport module nand negedge nettype new nexttime nmos nor
    noshowcancelled not notif0 notif1 null or output package packed parameter pmos posedge primitive
    priority program property protected pull0 pull1 pulldown pullup pulsestyle_ondetect
    pulsestyle_onevent pure rand randc randcase randsequence rcmos real realtime ref reg reject_on
    release repeat restrict return rnmos rpmos rtran rtranif0 rtranif1 s_always s_eventually
    s_nexttime s_until s_until_with scalared sequence shortint shortreal showcancelled signed small
    soft solve specify specparam static string strong strong0 strong1 struct super supply0 supply1
    sync_accept_on sync_reject_on table tagged task this throughout time timeprecision timeunit tran
    tranif0 tranif1 tri tri0 tri1 triand trior trireg type typedef union unique unique0 unsigned
    until until_with untyped use uwire var vectored virtual void wait wait_order wand weak weak0 weak1
    while wildcard wire with within wor xnor xor
    """.split()
)
