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

Each code object is disassembled once (cached by code object).
"""

from __future__ import annotations

import bisect
import dis
import functools
import itertools
import sys
from types import CodeType, FrameType

_CALLS = frozenset({"CALL", "CALL_KW", "CALL_FUNCTION", "CALL_FUNCTION_KW", "CALL_METHOD", "CALL_FUNCTION_EX"})
_STORES = frozenset({"STORE_FAST", "STORE_NAME", "STORE_GLOBAL", "STORE_DEREF"})
_SKIP = frozenset({"CACHE", "EXTENDED_ARG"})
_JUMPS = frozenset({"JUMP_FORWARD", "JUMP_ABSOLUTE"})  # forward only; JUMP_ABSOLUTE is 3.10

SUPPORTED = sys.implementation.name == "cpython"


@functools.lru_cache(maxsize=2048)
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


def assigned_name(frame: FrameType) -> str | None:
    """The plain variable *frame*'s current call result is stored in, or None."""
    if not SUPPORTED:
        return None
    return _stores_after_calls(frame.f_code).get(frame.f_lasti)


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
