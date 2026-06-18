"""Engine-native lowering for the Phase 8 testbench bench primitives.

This module ships a constrained subset of the transaction-level
testbench API that can be **lowered** to the hardware DSL
(``veriforge.dsl``) so the resulting wrapper module — DUT plus
synthetic bench fragments — can be simulated by the VM or compiled
engine without the per-cycle Python step overhead of
:class:`~veriforge.sim.bench.Testbench`.

Subset rules
------------

A bench primitive is *engine-native* if and only if its behavior is

* purely data-driven (no Python callbacks fired per beat),
* synchronous to a single declared domain,
* expressible as a small finite state machine over a fixed, bounded
  data set provided at compile time.

Lowerings provided
------------------

* :class:`AXIStreamSourceLowering` ``(beats, data_width, prng_bits, pause_threshold, prng_seed)``
  Drive a fixed iterable of beats with simple ``tvalid && tready``
  handshake. ``tlast`` is asserted on the final beat.  Optional PRNG
  pause randomly de-asserts ``tvalid`` to simulate bursty sources.

* :class:`AXIStreamSinkLowering` ``(n_beats, data_width, prng_bits, pause_threshold, prng_seed)``
  Capture up to ``n_beats`` beats into per-beat output regs and raise a
  ``done`` flag.  Optional PRNG pause randomly de-asserts ``tready`` to
  simulate back-pressure.

* :class:`AXILiteMasterLowering` ``(operations, addr_width, data_width)``
  Drive a scripted sequence of :class:`AXILiteOp` write/read operations
  against a DUT AXI-Lite slave. Operations are ROM-encoded and replayed
  by an FSM; per-op response and read-data are captured as output regs.

* :class:`AXILiteSlaveLowering` ``(memory_depth, data_width, addr_width, ...)``
  Act as a memory-backed AXI-Lite slave responder for a DUT master port.
  Supports WSTRB byte-merging and optional initial memory contents.  Each
  memory cell is exposed as an output port for post-simulation inspection.

* :class:`AXI4SlaveLowering` ``(memory_depth, data_width, addr_width, ...)``
  Act as an AXI4 memory-backed slave responder for a DUT master port.
  Supports INCR bursts and WSTRB byte-merging. Each memory cell is
  exposed as an output port for post-simulation inspection.

* :class:`AXI4MasterLowering` ``(operations, addr_width, data_width, id_width)``
  Drive a scripted sequence of :class:`AXI4MasterOp` single-beat
  write/read operations against a DUT AXI4 slave port. Operations are
  ROM-encoded and replayed by an FSM; per-op response and read-data are
  captured as output regs.

Escape hatch
------------

Any interface whose stimulus does *not* fit the subset should use the
existing Python-stepped :class:`Testbench` runtime instead — do not
call :func:`compile_native` for that test, or omit the offending
interface from the ``lowerings`` mapping (this raises a clear error to
flag that partial native mode is not supported).

See also
--------

``notes/simulation/bench_native_lowering.md`` — full API reference,
examples, signal naming conventions, and performance guidance.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from veriforge.dsl import Module as DSLModule
from veriforge.dsl import negedge, posedge
from veriforge.model.design import Design

from .plan import ClockDomain, InterfaceBinding, TestbenchPlan

if TYPE_CHECKING:
    from veriforge.dsl.builder import Signal
    from veriforge.model.design import Module as ModelModule

    from .runtime import Testbench


__all__ = [
    "AXI4MasterLowering",
    "AXI4MasterOp",
    "AXI4SlaveLowering",
    "AXILiteMasterLowering",
    "AXILiteOp",
    "AXILiteSlaveLowering",
    "AXIStreamSinkLowering",
    "AXIStreamSourceLowering",
    "InterfaceLowering",
    "LoweredDesign",
    "LoweringError",
    "MemBusMasterLowering",
    "MemBusOp",
    "MemBusResponderLowering",
    "compile_native",
]


class LoweringError(RuntimeError):
    """Raised when a bench specification cannot be lowered to engine-native form."""


# ---------------------------------------------------------------------------
# Lowering protocol + concrete lowerings
# ---------------------------------------------------------------------------


class InterfaceLowering(Protocol):
    """Protocol for an engine-native lowering of one interface bundle."""

    protocol: str  # e.g. "axi_stream"
    role: str  # required DUT-side role for this lowering

    def apply(  # noqa: PLR0913
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        """Emit DSL fragments into ``wrapper`` and populate ``port_map``.

        Implementations *must* register an entry in ``port_map`` for
        every signal listed in ``binding.signals`` (mapping the DUT-side
        port name to a wrapper-internal signal/wire/reg).
        """
        ...


def _reset_condition(rst: "Signal | None", domain: ClockDomain) -> "Signal | int":
    """Return the boolean expression that means 'reset is asserted'."""
    if rst is None or domain.reset is None:
        return 0  # never-asserted
    return ~rst if domain.reset.active_low else rst


def _bit_width_for(value: int) -> int:
    return max(1, (max(value, 1)).bit_length())


# 32-bit Galois LFSR polynomial: x^32 + x^31 + x^29 + x + 1 (maximal-length).
_LFSR_POLY_32 = 0xD0000001


def _build_lfsr_data(  # noqa: PLR0913
    wrapper: DSLModule,
    name: str,
    data_width: int,
    prng_seed: int,
    rst_cond: "Signal | int",
    sens: "list[object]",
    handshake: "Signal",
) -> "Signal":
    """Emit a 32-bit Galois LFSR that advances only when *handshake* is high.

    Returns a combinational wire carrying ``lfsr[min(data_width,32)-1:0]``.
    Both source and sink call this with the same *prng_seed*; since the LFSR
    advances exactly once per accepted beat the two stay in sync regardless of
    any pause or back-pressure inserted between them.

    For ``data_width > 32`` the upper bits of the returned wire are zero-extended
    from the 32-bit LFSR state (adequate for data-integrity testing).
    """
    seed = prng_seed if prng_seed != 0 else 0xACE1
    lfsr = wrapper.reg(f"{name}_data_lfsr", width=32)
    effective_width = min(data_width, 32)
    tdata_w = wrapper.wire(f"{name}_tdata_prng", width=data_width)
    if data_width <= 32:  # noqa: PLR2004
        wrapper.assign(tdata_w, lfsr[effective_width - 1 : 0] if effective_width < 32 else lfsr)  # noqa: PLR2004
    else:
        # Upper bits zero-padded; lower 32 bits from LFSR.
        wrapper.assign(tdata_w, lfsr)

    with wrapper.always(*sens):
        with wrapper.if_(rst_cond):
            lfsr <<= seed
        with wrapper.else_():
            with wrapper.if_(lfsr == 0):
                lfsr <<= seed
            with wrapper.elif_(handshake):
                with wrapper.if_(lfsr[0]):
                    lfsr <<= (lfsr >> 1) ^ _LFSR_POLY_32
                with wrapper.else_():
                    lfsr <<= lfsr >> 1

    return tdata_w


def _build_lfsr_pause(
    wrapper: DSLModule,
    name: str,
    prng_bits: int,
    pause_threshold: int,
    prng_seed: int,
    rst_cond: "Signal | int",
    sens: "list[object]",
) -> "Signal":
    """Emit a 32-bit Galois LFSR and return a 1-bit combinational *pause* wire.

    The pause wire is high when ``lfsr[prng_bits-1:0] < pause_threshold``.
    Only call this when ``prng_bits > 0``.

    The LFSR self-reseeds from *prng_seed* when it reaches zero, which handles
    the edge case of startup without a reset signal.
    """
    seed = prng_seed if prng_seed != 0 else 0xACE1
    lfsr = wrapper.reg(f"{name}_lfsr", width=32)
    pause_w = wrapper.wire(f"{name}_pause")
    prng_mask = (1 << prng_bits) - 1
    wrapper.assign(pause_w, (lfsr & prng_mask) < pause_threshold)

    with wrapper.always(*sens):
        with wrapper.if_(rst_cond):
            lfsr <<= seed
        with wrapper.else_():
            with wrapper.if_(lfsr == 0):
                # Self-reseed: handles startup without reset (LFSR must never stay zero).
                lfsr <<= seed
            with wrapper.elif_(lfsr[0]):
                lfsr <<= (lfsr >> 1) ^ _LFSR_POLY_32
            with wrapper.else_():
                lfsr <<= lfsr >> 1

    return pause_w


@dataclass
class AXIStreamSourceLowering:  # cm:2b9f4c
    """Lower a fixed list of beats (or a PRNG stream) to an AXI-Stream source FSM.

    Two data modes — exactly one must be active:

    **ROM mode** (default): set ``beats`` to a non-empty sequence of integers.
    The lowering encodes them in a ROM keyed on the beat counter.

    **PRNG mode**: set ``n_prng_beats`` to a positive integer.  ``tdata`` is
    driven by a 32-bit Galois LFSR that advances once per accepted beat.  Pair
    with :class:`AXIStreamSinkLowering` using the same ``data_prng_seed`` value
    to check data integrity end-to-end entirely inside the simulator engine.
    For ``data_width > 32``, upper bits of ``tdata`` are zero-extended.

    Args:
        beats: Sequence of integer beat values to drive in order (ROM mode).
            Must be non-empty when ``n_prng_beats == 0``.
        n_prng_beats: Number of beats to generate in PRNG mode.  Set to a
            positive integer to enable PRNG mode; ``beats`` must be empty.
        data_prng_seed: Initial seed for the data LFSR used in PRNG mode.
            Defaults to ``0xACE1``; ``0`` is treated as ``0xACE1``.
        data_width: Width (in bits) of ``tdata``.
        prng_bits: Number of low-order LFSR bits used for the *pause* comparison.
            Set to ``0`` (default) to disable the pause generator entirely.
        pause_threshold: Pause is asserted when ``lfsr[prng_bits-1:0] < pause_threshold``.
            Must be in ``[0, 2**prng_bits]``.  A value of ``2**prng_bits`` means
            *always* pause; ``0`` means never pause.  E.g. ``prng_bits=4,
            pause_threshold=8`` gives ~50 % random pause rate.
        prng_seed: Initial seed for the 32-bit Galois LFSR used for *pause*.
            Defaults to ``0xACE1``; a value of ``0`` is treated as ``0xACE1``.

    The lowering produces a small counter-driven FSM that:

    * holds ``tvalid`` high until all beats have been transferred (gated by
      the optional pause generator so ``tvalid`` drops when the LFSR fires),
    * exposes ``tdata`` from the ROM (ROM mode) or data LFSR (PRNG mode),
    * raises ``tlast`` on the final beat (also gated by pause),
    * advances the counter only when ``tvalid && tready`` are both high.
    """

    beats: Sequence[int] = ()
    n_prng_beats: int = 0
    data_prng_seed: int = 0xACE1
    data_width: int = 8
    protocol: str = "axi_stream"
    role: str = "slave"  # DUT side; lowering is a *source*
    prng_bits: int = 0
    pause_threshold: int = 0
    prng_seed: int = 0xACE1

    def apply(  # noqa: PLR0913, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"AXIStreamSourceLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        # Validate mode: exactly one of beats or n_prng_beats must be active.
        prng_mode = self.n_prng_beats > 0
        if prng_mode and len(self.beats) > 0:
            raise LoweringError(
                f"AXIStreamSourceLowering[{binding.prefix}]: "
                "set either 'beats' (ROM mode) or 'n_prng_beats' (PRNG mode), not both"
            )
        if not prng_mode and len(self.beats) == 0:
            raise LoweringError(
                f"AXIStreamSourceLowering[{binding.prefix}]: "
                "beats must be non-empty (or set n_prng_beats > 0 for PRNG mode)"
            )
        n = self.n_prng_beats if prng_mode else len(self.beats)
        if not 0 <= self.prng_bits <= 32:
            raise LoweringError(
                f"AXIStreamSourceLowering[{binding.prefix}]: prng_bits must be 0..32, got {self.prng_bits}"
            )
        if self.prng_bits > 0 and not 0 <= self.pause_threshold <= (1 << self.prng_bits):
            raise LoweringError(
                f"AXIStreamSourceLowering[{binding.prefix}]: pause_threshold {self.pause_threshold} "
                f"out of range 0..{1 << self.prng_bits} for prng_bits={self.prng_bits}"
            )
        cnt_width = _bit_width_for(n + 1)

        prefix = binding.prefix
        cnt = wrapper.reg(f"{prefix}_src_cnt", width=cnt_width)
        tvalid = wrapper.wire(f"{prefix}_src_tvalid")
        tdata = wrapper.wire(f"{prefix}_src_tdata", width=self.data_width)
        tlast = wrapper.wire(f"{prefix}_src_tlast")
        tready = wrapper.wire(f"{prefix}_src_tready")

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))

        if self.prng_bits > 0:
            pause_w = _build_lfsr_pause(
                wrapper,
                f"{prefix}_src",
                self.prng_bits,
                self.pause_threshold,
                self.prng_seed,
                rst_cond,
                sens,
            )
            wrapper.assign(tvalid, (cnt < n) & ~pause_w)
            wrapper.assign(tlast, (cnt == (n - 1)) & ~pause_w)
        else:
            wrapper.assign(tvalid, cnt < n)
            wrapper.assign(tlast, cnt == (n - 1))

        if prng_mode:
            # PRNG data mode: tdata is driven by an LFSR that advances once per
            # accepted beat.  The sink must use the same data_prng_seed.
            handshake = tvalid & tready
            data_w = _build_lfsr_data(
                wrapper,
                f"{prefix}_src",
                self.data_width,
                self.data_prng_seed,
                rst_cond,
                sens,
                handshake,
            )
            wrapper.assign(tdata, data_w)

            with wrapper.always(*sens):
                with wrapper.if_(rst_cond):
                    cnt <<= 0
                with wrapper.else_():
                    with wrapper.if_(tvalid & tready):
                        cnt <<= cnt + 1
        else:
            # ROM mode: tdata comes from a registered case-statement ROM.
            # A chained mux expression for large n creates a deeply-nested ternary in
            # the generated C, causing Clang to hang.  A case-statement-based register
            # compiles to an O(n) switch and scales to hundreds of beats.
            mask = (1 << self.data_width) - 1
            tdata_r = wrapper.reg(f"{prefix}_src_tdata_r", width=self.data_width)
            wrapper.assign(tdata, tdata_r)

            with wrapper.always(*sens):
                with wrapper.if_(rst_cond):
                    cnt <<= 0
                    tdata_r <<= int(self.beats[0]) & mask
                with wrapper.else_():
                    with wrapper.if_(tvalid & tready):
                        # Pre-load next beat so tdata_r == beats[cnt] is maintained:
                        # when cnt advances to cnt+1, tdata_r must already hold beats[cnt+1].
                        with wrapper.if_(cnt < (n - 1)):
                            with wrapper.case(cnt) as c:
                                for i in range(n - 1):
                                    with c.when(i):
                                        tdata_r <<= int(self.beats[i + 1]) & mask
                                with c.default():
                                    pass
                        cnt <<= cnt + 1

        # Wire DUT ports.
        sigs = binding.signals
        port_map[sigs["tvalid"]] = tvalid
        port_map[sigs["tdata"]] = tdata
        if "tlast" in sigs:
            port_map[sigs["tlast"]] = tlast
        port_map[sigs["tready"]] = tready


@dataclass
class AXIStreamSinkLowering:
    """Lower an AXI-Stream sink to a per-beat capture array or PRNG checker.

    Two data modes are available:

    **Capture mode** (default): every accepted beat is written into an individual
    ``<prefix>_cap_<i>`` output reg.  Suitable for small frame counts where the
    test reads back each byte.

    **PRNG check mode**: set ``data_prng_seed`` to a non-``None`` integer (must
    match the ``data_prng_seed`` used on :class:`AXIStreamSourceLowering`).
    The lowering runs a shadow LFSR that advances once per accepted beat,
    comparing ``tdata`` against the expected LFSR value.  No per-beat capture
    regs are created — this scales to millions of beats.  After a batched run
    read the output signals:

    * ``<prefix>_snk_err_cnt`` — count of mismatched beats (32 bits),
    * ``<prefix>_snk_err_flag`` — 1 if *any* beat mismatched.

    For ``data_width > 32`` only the lower 32 bits are checked; the source also
    only uses 32 bits, so this is lossless for all common widths.

    Args:
        n_beats: Maximum number of beats to capture / check.
        data_width: Width (in bits) of ``tdata``.
        data_prng_seed: When not ``None``, enable PRNG check mode with this
            seed.  Must match the source's ``data_prng_seed``.
        prng_bits: Number of low-order LFSR bits used for the *pause* comparison.
            Set to ``0`` (default) to disable the pause generator entirely.
        pause_threshold: Pause is asserted when ``lfsr[prng_bits-1:0] < pause_threshold``.
            Must be in ``[0, 2**prng_bits]``.  E.g. ``prng_bits=4,
            pause_threshold=8`` gives ~50 % random back-pressure on ``tready``.
        prng_seed: Initial seed for the 32-bit Galois LFSR used for *pause*.
            Defaults to ``0xACE1``; a value of ``0`` is treated as ``0xACE1``.

    In capture mode the lowering produces:

    * ``tready`` high when the PRNG is not asserting back-pressure
      (permanently high when ``prng_bits=0``),
    * a counter ``snk_cnt`` of accepted beats,
    * per-beat output regs ``<prefix>_cap_<i>`` of width ``data_width``,
    * an output flag ``<prefix>_snk_done`` that latches once
      ``snk_cnt == n_beats``.

    In PRNG check mode the per-beat capture regs are replaced by
    ``<prefix>_snk_err_cnt`` and ``<prefix>_snk_err_flag``.

    All output regs are exposed as **wrapper output ports**, so test code can
    read them after a batched run via
    :meth:`veriforge.sim.Simulator.signal`.
    """

    n_beats: int
    data_width: int = 8
    data_prng_seed: "int | None" = None
    protocol: str = "axi_stream"
    role: str = "master"  # DUT side; lowering is a *sink*
    prng_bits: int = 0
    pause_threshold: int = 0
    prng_seed: int = 0xACE1

    def apply(  # noqa: PLR0913, PLR0915, PLR0912
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"AXIStreamSinkLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        if self.n_beats <= 0:
            raise LoweringError(f"AXIStreamSinkLowering[{binding.prefix}]: n_beats must be positive")
        if not 0 <= self.prng_bits <= 32:
            raise LoweringError(
                f"AXIStreamSinkLowering[{binding.prefix}]: prng_bits must be 0..32, got {self.prng_bits}"
            )
        if self.prng_bits > 0 and not 0 <= self.pause_threshold <= (1 << self.prng_bits):
            raise LoweringError(
                f"AXIStreamSinkLowering[{binding.prefix}]: pause_threshold {self.pause_threshold} "
                f"out of range 0..{1 << self.prng_bits} for prng_bits={self.prng_bits}"
            )
        cnt_width = _bit_width_for(self.n_beats + 1)
        prng_check = self.data_prng_seed is not None

        prefix = binding.prefix
        cnt = wrapper.reg(f"{prefix}_snk_cnt", width=cnt_width)
        tvalid = wrapper.wire(f"{prefix}_snk_tvalid")
        tdata = wrapper.wire(f"{prefix}_snk_tdata", width=self.data_width)
        tlast = wrapper.wire(f"{prefix}_snk_tlast")
        tready = wrapper.wire(f"{prefix}_snk_tready")

        wrapper.assign(tlast, 0)  # unused; assign to silence dangling-signal warnings
        _ = tlast  # marker that we read the wire

        done = wrapper.output_reg(f"{prefix}_snk_done")

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))

        if self.prng_bits > 0:
            pause_w = _build_lfsr_pause(
                wrapper,
                f"{prefix}_snk",
                self.prng_bits,
                self.pause_threshold,
                self.prng_seed,
                rst_cond,
                sens,
            )
            wrapper.assign(tready, ~pause_w)
        else:
            wrapper.assign(tready, 1)

        if prng_check:
            # PRNG check mode: shadow LFSR tracks expected tdata; mismatch → error regs.
            handshake = tvalid & tready
            exp_data_w = _build_lfsr_data(
                wrapper,
                f"{prefix}_snk",
                self.data_width,
                self.data_prng_seed,
                rst_cond,
                sens,
                handshake,
            )
            err_cnt = wrapper.output_reg(f"{prefix}_snk_err_cnt", width=32)
            err_flag = wrapper.output_reg(f"{prefix}_snk_err_flag")

            # Mask comparison to the bits actually generated by the LFSR.
            effective_width = min(self.data_width, 32)
            if effective_width < self.data_width:
                # Upper bits of tdata are 0 by design; ignore them in check.
                tdata_masked = tdata[effective_width - 1 : 0]
            else:
                tdata_masked = tdata

            with wrapper.always(*sens):
                with wrapper.if_(rst_cond):
                    cnt <<= 0
                    done <<= 0
                    err_cnt <<= 0
                    err_flag <<= 0
                with wrapper.else_():
                    with wrapper.if_(tvalid & tready & (cnt < self.n_beats)):
                        with wrapper.if_(tdata_masked != exp_data_w):
                            err_cnt <<= err_cnt + 1
                            err_flag <<= 1
                        cnt <<= cnt + 1
                        with wrapper.if_(cnt == (self.n_beats - 1)):
                            done <<= 1
        else:
            # Capture mode: per-beat output regs.
            cap_regs = [wrapper.output_reg(f"{prefix}_cap_{i}", width=self.data_width) for i in range(self.n_beats)]

            with wrapper.always(*sens):
                with wrapper.if_(rst_cond):
                    cnt <<= 0
                    done <<= 0
                    for r in cap_regs:
                        r <<= 0  # noqa: PLW2901  (DSL non-blocking assign, not a rebind)
                with wrapper.else_():
                    with wrapper.if_(tvalid & tready & (cnt < self.n_beats)):
                        with wrapper.case(cnt) as c:
                            for i, r in enumerate(cap_regs):
                                with c.when(i):
                                    r <<= tdata  # noqa: PLW2901  (DSL non-blocking assign)
                            with c.default():
                                pass  # unreachable due to (cnt < n_beats)
                        cnt <<= cnt + 1
                        with wrapper.if_(cnt == (self.n_beats - 1)):
                            done <<= 1

        sigs = binding.signals
        port_map[sigs["tvalid"]] = tvalid
        port_map[sigs["tdata"]] = tdata
        if "tlast" in sigs:
            # DUT drives tlast; we don't act on it but must connect it.
            port_map[sigs["tlast"]] = tlast
        port_map[sigs["tready"]] = tready


# ---------------------------------------------------------------------------
# AXI-Lite master lowering (DUT is slave; bench drives scripted writes/reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AXILiteOp:
    """One scripted AXI-Lite operation.

    Use :meth:`write` / :meth:`read` factories rather than constructing
    directly. ``kind`` is ``"write"`` or ``"read"``; for read ops
    ``data`` and ``strb`` are ignored.
    """

    kind: str  # "write" or "read"
    addr: int
    data: int = 0
    strb: int = 0xF  # default: all bytes (DW=32)

    @classmethod
    def write(cls, addr: int, data: int, *, strb: int | None = None) -> "AXILiteOp":
        return cls(kind="write", addr=addr, data=data, strb=0xF if strb is None else strb)

    @classmethod
    def read(cls, addr: int) -> "AXILiteOp":
        return cls(kind="read", addr=addr)


@dataclass
class AXILiteMasterLowering:
    """Lower a scripted AXI-Lite master driver to an engine-native FSM.

    Walks ``operations`` in order, driving AW+W in parallel for writes
    and AR for reads, capturing ``b_resp`` for writes and
    ``r_data``/``r_resp`` for reads.

    Args:
        operations: Sequence of :class:`AXILiteOp`. Use the
            :meth:`AXILiteOp.write` / :meth:`AXILiteOp.read` factories.
        addr_width: Width of AW/AR addr in bits.
        data_width: Width of W/R data in bits. WSTRB is ``data_width/8``.

    Wrapper output ports created (per operation index ``i``):

    * ``<prefix>_op_<i>_resp`` — 2-bit response (B for write, R for read)
    * ``<prefix>_op_<i>_rdata`` — captured read data (0 for write ops)

    Plus a single ``<prefix>_master_done`` flag that latches when the
    final operation completes.
    """

    operations: Sequence[AXILiteOp]
    addr_width: int = 32
    data_width: int = 32
    protocol: str = "axi_lite"
    role: str = "slave"  # DUT side; lowering is a *master*

    def apply(  # noqa: PLR0913, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"AXILiteMasterLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        n = len(self.operations)
        if n == 0:
            raise LoweringError(f"AXILiteMasterLowering[{binding.prefix}]: operations must be non-empty")
        for i, op in enumerate(self.operations):
            if op.kind not in {"write", "read"}:
                raise LoweringError(
                    f"AXILiteMasterLowering[{binding.prefix}].operations[{i}]: "
                    f"kind must be 'write' or 'read', got {op.kind!r}"
                )

        if self.data_width % 8 != 0:
            raise LoweringError(f"AXILiteMasterLowering[{binding.prefix}]: data_width must be a multiple of 8")
        strb_width = self.data_width // 8

        from veriforge.dsl import mux  # noqa: PLC0415

        prefix = binding.prefix
        sigs = binding.signals
        idx_width = _bit_width_for(n + 1)

        # Internal regs
        idx = wrapper.reg(f"{prefix}_mst_idx", width=idx_width)
        aw_done = wrapper.reg(f"{prefix}_mst_aw_done")
        w_done = wrapper.reg(f"{prefix}_mst_w_done")
        ar_done = wrapper.reg(f"{prefix}_mst_ar_done")

        # Wires for handshake / payload
        active = wrapper.wire(f"{prefix}_mst_active")
        is_write = wrapper.wire(f"{prefix}_mst_is_write")
        is_read = wrapper.wire(f"{prefix}_mst_is_read")
        awvalid = wrapper.wire(f"{prefix}_mst_awvalid")
        awaddr = wrapper.wire(f"{prefix}_mst_awaddr", width=self.addr_width)
        awready = wrapper.wire(f"{prefix}_mst_awready")
        wvalid = wrapper.wire(f"{prefix}_mst_wvalid")
        wdata = wrapper.wire(f"{prefix}_mst_wdata", width=self.data_width)
        wstrb = wrapper.wire(f"{prefix}_mst_wstrb", width=strb_width)
        wready = wrapper.wire(f"{prefix}_mst_wready")
        bvalid = wrapper.wire(f"{prefix}_mst_bvalid")
        bready = wrapper.wire(f"{prefix}_mst_bready")
        bresp = wrapper.wire(f"{prefix}_mst_bresp", width=2)
        arvalid = wrapper.wire(f"{prefix}_mst_arvalid")
        araddr = wrapper.wire(f"{prefix}_mst_araddr", width=self.addr_width)
        arready = wrapper.wire(f"{prefix}_mst_arready")
        rvalid = wrapper.wire(f"{prefix}_mst_rvalid")
        rready = wrapper.wire(f"{prefix}_mst_rready")
        rdata = wrapper.wire(f"{prefix}_mst_rdata", width=self.data_width)
        rresp = wrapper.wire(f"{prefix}_mst_rresp", width=2)

        # Build chained-mux ROMs over idx for op_type, addr, wdata, wstrb.
        addr_mask = (1 << self.addr_width) - 1
        data_mask = (1 << self.data_width) - 1
        strb_mask = (1 << strb_width) - 1

        is_write_expr: object = 1 if self.operations[-1].kind == "write" else 0
        addr_expr: object = self.operations[-1].addr & addr_mask
        wdata_expr: object = self.operations[-1].data & data_mask
        wstrb_expr: object = self.operations[-1].strb & strb_mask
        for i in range(n - 1, -1, -1):
            op = self.operations[i]
            iw = 1 if op.kind == "write" else 0
            is_write_expr = mux(idx == i, iw, is_write_expr)
            addr_expr = mux(idx == i, op.addr & addr_mask, addr_expr)
            wdata_expr = mux(idx == i, op.data & data_mask, wdata_expr)
            wstrb_expr = mux(idx == i, op.strb & strb_mask, wstrb_expr)

        wrapper.assign(active, idx < n)
        wrapper.assign(is_write, active & is_write_expr)
        wrapper.assign(is_read, active & ~is_write_expr)

        wrapper.assign(awvalid, is_write & ~aw_done)
        wrapper.assign(awaddr, addr_expr)
        wrapper.assign(wvalid, is_write & ~w_done)
        wrapper.assign(wdata, wdata_expr)
        wrapper.assign(wstrb, wstrb_expr)
        wrapper.assign(bready, is_write & aw_done & w_done)
        wrapper.assign(arvalid, is_read & ~ar_done)
        wrapper.assign(araddr, addr_expr)
        wrapper.assign(rready, is_read & ar_done)

        # Per-op capture regs (output ports)
        resp_caps = [wrapper.output_reg(f"{prefix}_op_{i}_resp", width=2) for i in range(n)]
        rdata_caps = [wrapper.output_reg(f"{prefix}_op_{i}_rdata", width=self.data_width) for i in range(n)]
        master_done = wrapper.output_reg(f"{prefix}_master_done")

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))
        with wrapper.always(*sens):
            with wrapper.if_(rst_cond):
                idx <<= 0
                aw_done <<= 0
                w_done <<= 0
                ar_done <<= 0
                master_done <<= 0
                for r in resp_caps:
                    r <<= 0  # noqa: PLW2901
                for r in rdata_caps:
                    r <<= 0  # noqa: PLW2901
            with wrapper.else_():
                with wrapper.if_(active):
                    with wrapper.if_(awvalid & awready):
                        aw_done <<= 1
                    with wrapper.if_(wvalid & wready):
                        w_done <<= 1
                    with wrapper.if_(arvalid & arready):
                        ar_done <<= 1
                    # Write completion
                    with wrapper.if_(bvalid & bready):
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(resp_caps):
                                with c.when(i):
                                    r <<= bresp  # noqa: PLW2901
                            with c.default():
                                pass
                        idx <<= idx + 1
                        aw_done <<= 0
                        w_done <<= 0
                        with wrapper.if_(idx == (n - 1)):
                            master_done <<= 1
                    # Read completion
                    with wrapper.if_(rvalid & rready):
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(resp_caps):
                                with c.when(i):
                                    r <<= rresp  # noqa: PLW2901
                            with c.default():
                                pass
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(rdata_caps):
                                with c.when(i):
                                    r <<= rdata  # noqa: PLW2901
                            with c.default():
                                pass
                        idx <<= idx + 1
                        ar_done <<= 0
                        with wrapper.if_(idx == (n - 1)):
                            master_done <<= 1

        # Wire DUT ports.
        # Bench drives (DUT inputs): awaddr, awprot, awvalid, wdata, wstrb,
        # wvalid, bready, araddr, arprot, arvalid, rready
        # DUT drives (bench reads): awready, wready, bresp, bvalid, arready,
        # rdata, rresp, rvalid
        port_map[sigs["awaddr"]] = awaddr
        port_map[sigs["awprot"]] = 0
        port_map[sigs["awvalid"]] = awvalid
        port_map[sigs["awready"]] = awready
        port_map[sigs["wdata"]] = wdata
        port_map[sigs["wstrb"]] = wstrb
        port_map[sigs["wvalid"]] = wvalid
        port_map[sigs["wready"]] = wready
        port_map[sigs["bresp"]] = bresp
        port_map[sigs["bvalid"]] = bvalid
        port_map[sigs["bready"]] = bready
        port_map[sigs["araddr"]] = araddr
        port_map[sigs["arprot"]] = 0
        port_map[sigs["arvalid"]] = arvalid
        port_map[sigs["arready"]] = arready
        port_map[sigs["rdata"]] = rdata
        port_map[sigs["rresp"]] = rresp
        port_map[sigs["rvalid"]] = rvalid
        port_map[sigs["rready"]] = rready


# ---------------------------------------------------------------------------
# AXI4 slave lowering (DUT is master; bench acts as memory-backed responder)
# ---------------------------------------------------------------------------


@dataclass
class AXI4SlaveLowering:  # cm:c9a1e6
    """Lower a memory-backed AXI4 slave responder to an engine-native FSM.

    The lowering accepts INCR bursts of arbitrary length, byte-strobed
    writes, and always responds OKAY (2'b00). The backing memory is an
    array of ``memory_depth`` words each ``data_width`` bits wide, exposed
    as wrapper output ports ``<prefix>_slv_mem_<i>`` so test code can
    inspect the contents after the simulation completes.

    Word addressing: the byte address is shifted by ``log2(data_width/8)``
    to index the memory array.

    Args:
        memory_depth: Number of words in the backing memory. Each word
            becomes a wrapper output port; keep this small (≤ 256) to
            avoid bloating the wrapper.
        data_width: Width of WDATA/RDATA in bits. WSTRB is data_width/8.
        addr_width: Width of AW/AR addr in bits.
        id_width: Width of *ID signals (set 0 to disable; use the
            DUT's actual ID width when present).
        initial_memory: Optional dict of word_index -> initial value.

    Limitations:

    * FIXED bursts degenerate to single-beat correctness (still increments
      address per beat — caller should not use FIXED).
    * WRAP bursts are not modeled.
    * Single outstanding transaction; AW handshake blocks AR until B.
    * No exclusive access modeling.
    """

    memory_depth: int = 16
    data_width: int = 32
    addr_width: int = 32
    id_width: int = 0
    initial_memory: Mapping[int, int] | None = None
    protocol: str = "axi4"
    role: str = "master"  # DUT side; lowering is a *slave*

    def apply(  # noqa: PLR0913, PLR0912, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"AXI4SlaveLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        if self.memory_depth <= 0:
            raise LoweringError(f"AXI4SlaveLowering[{binding.prefix}]: memory_depth must be positive")
        if self.data_width % 8 != 0:
            raise LoweringError(f"AXI4SlaveLowering[{binding.prefix}]: data_width must be a multiple of 8")
        if self.id_width < 0:
            raise LoweringError(f"AXI4SlaveLowering[{binding.prefix}]: id_width must be >= 0")

        from veriforge.dsl import cat, mux

        prefix = binding.prefix
        sigs = binding.signals
        depth = self.memory_depth
        n_bytes = self.data_width // 8
        word_addr_shift = (n_bytes - 1).bit_length()  # log2(n_bytes), 0 for 1 byte
        addr_index_width = _bit_width_for(depth + 1)
        len_width = 8  # AXI4 awlen/arlen are 8 bits

        # State encoding: IDLE=0, W_BURST=1, B_RESP=2, R_BURST=3
        S_IDLE = 0
        S_W_BURST = 1
        S_B_RESP = 2
        S_R_BURST = 3

        state = wrapper.reg(f"{prefix}_slv_state", width=2)
        burst_addr = wrapper.reg(f"{prefix}_slv_burst_addr", width=addr_index_width)
        burst_len = wrapper.reg(f"{prefix}_slv_burst_len", width=len_width)
        burst_id = wrapper.reg(f"{prefix}_slv_burst_id", width=self.id_width) if self.id_width > 0 else None

        # Memory cells (one output_reg per word so test code can inspect).
        mem_cells = [wrapper.output_reg(f"{prefix}_slv_mem_{i}", width=self.data_width) for i in range(depth)]

        # Counters (output ports for diagnostics).
        aw_count = wrapper.output_reg(f"{prefix}_slv_aw_count", width=16)
        w_count = wrapper.output_reg(f"{prefix}_slv_w_count", width=16)
        ar_count = wrapper.output_reg(f"{prefix}_slv_ar_count", width=16)

        # Channel signals.
        awvalid = wrapper.wire(f"{prefix}_slv_awvalid")
        awready = wrapper.wire(f"{prefix}_slv_awready")
        awaddr = wrapper.wire(f"{prefix}_slv_awaddr", width=self.addr_width)
        awlen = wrapper.wire(f"{prefix}_slv_awlen", width=len_width)
        wvalid = wrapper.wire(f"{prefix}_slv_wvalid")
        wready = wrapper.wire(f"{prefix}_slv_wready")
        wdata = wrapper.wire(f"{prefix}_slv_wdata", width=self.data_width)
        wstrb = wrapper.wire(f"{prefix}_slv_wstrb", width=n_bytes)
        wlast = wrapper.wire(f"{prefix}_slv_wlast")
        bvalid = wrapper.wire(f"{prefix}_slv_bvalid")
        bready = wrapper.wire(f"{prefix}_slv_bready")
        bresp = wrapper.wire(f"{prefix}_slv_bresp", width=2)
        arvalid = wrapper.wire(f"{prefix}_slv_arvalid")
        arready = wrapper.wire(f"{prefix}_slv_arready")
        araddr = wrapper.wire(f"{prefix}_slv_araddr", width=self.addr_width)
        arlen = wrapper.wire(f"{prefix}_slv_arlen", width=len_width)
        rvalid = wrapper.wire(f"{prefix}_slv_rvalid")
        rready = wrapper.wire(f"{prefix}_slv_rready")
        rdata = wrapper.wire(f"{prefix}_slv_rdata", width=self.data_width)
        rresp = wrapper.wire(f"{prefix}_slv_rresp", width=2)
        rlast = wrapper.wire(f"{prefix}_slv_rlast")

        # Optional ID wires (only created when id_width > 0).
        awid_w = wrapper.wire(f"{prefix}_slv_awid", width=self.id_width) if self.id_width > 0 else None
        arid_w = wrapper.wire(f"{prefix}_slv_arid", width=self.id_width) if self.id_width > 0 else None
        bid_w = wrapper.wire(f"{prefix}_slv_bid", width=self.id_width) if self.id_width > 0 else None
        rid_w = wrapper.wire(f"{prefix}_slv_rid", width=self.id_width) if self.id_width > 0 else None

        # Combinational outputs.
        wrapper.assign(awready, state == S_IDLE)
        # AR is accepted only when no AW pending (simple priority).
        wrapper.assign(arready, (state == S_IDLE) & ~awvalid)
        wrapper.assign(wready, state == S_W_BURST)
        wrapper.assign(bvalid, state == S_B_RESP)
        wrapper.assign(bresp, 0)
        wrapper.assign(rvalid, state == S_R_BURST)
        wrapper.assign(rresp, 0)
        wrapper.assign(rlast, (state == S_R_BURST) & (burst_len == 0))
        if bid_w is not None and burst_id is not None:
            wrapper.assign(bid_w, burst_id)
        if rid_w is not None and burst_id is not None:
            wrapper.assign(rid_w, burst_id)

        # Memory read mux for rdata.
        rdata_expr: object = 0
        for i in range(depth - 1, -1, -1):
            rdata_expr = mux(burst_addr == i, mem_cells[i], rdata_expr)
        wrapper.assign(rdata, rdata_expr)

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))

        # Helper: build the strobe-merged write word for a given memory cell.
        def _merged_word(old_word: object) -> object:
            """Return cat of bytes (MSB first) with WSTRB selecting old vs new."""
            byte_exprs: list[object] = []  # MSB-first for cat()
            # cat() takes MSB first; we iterate high byte to low byte.
            for byte_i in range(n_bytes - 1, -1, -1):
                lo = byte_i * 8
                old_byte = old_word[lo + 7 : lo]
                new_byte = wdata[lo + 7 : lo]
                byte_exprs.append(mux(wstrb[byte_i], new_byte, old_byte))
            return cat(*byte_exprs)

        with wrapper.always(*sens):
            with wrapper.if_(rst_cond):
                state <<= S_IDLE
                burst_addr <<= 0
                burst_len <<= 0
                if burst_id is not None:
                    burst_id <<= 0
                aw_count <<= 0
                w_count <<= 0
                ar_count <<= 0
                init = self.initial_memory or {}
                for i, r in enumerate(mem_cells):
                    val = int(init.get(i, 0)) & ((1 << self.data_width) - 1)
                    r <<= val  # noqa: PLW2901
            with wrapper.else_():
                with wrapper.case(state) as c:
                    with c.when(S_IDLE):
                        with wrapper.if_(awvalid & awready):
                            # Latch AW: byte addr -> word addr
                            if word_addr_shift > 0:
                                burst_addr <<= awaddr[word_addr_shift + addr_index_width - 1 : word_addr_shift]
                            else:
                                burst_addr <<= awaddr[addr_index_width - 1 : 0]
                            burst_len <<= awlen
                            if burst_id is not None and awid_w is not None:
                                burst_id <<= awid_w
                            aw_count <<= aw_count + 1
                            state <<= S_W_BURST
                        with wrapper.elif_(arvalid & arready):
                            if word_addr_shift > 0:
                                burst_addr <<= araddr[word_addr_shift + addr_index_width - 1 : word_addr_shift]
                            else:
                                burst_addr <<= araddr[addr_index_width - 1 : 0]
                            burst_len <<= arlen
                            if burst_id is not None and arid_w is not None:
                                burst_id <<= arid_w
                            ar_count <<= ar_count + 1
                            state <<= S_R_BURST
                    with c.when(S_W_BURST):
                        with wrapper.if_(wvalid & wready):
                            # Update memory cell at burst_addr with strobe-merged word.
                            with wrapper.case(burst_addr) as wc:
                                for i, cell in enumerate(mem_cells):
                                    with wc.when(i):
                                        cell <<= _merged_word(cell)  # noqa: PLW2901
                                with wc.default():
                                    pass
                            burst_addr <<= burst_addr + 1
                            w_count <<= w_count + 1
                            with wrapper.if_(wlast):
                                state <<= S_B_RESP
                    with c.when(S_B_RESP):
                        with wrapper.if_(bready & bvalid):
                            state <<= S_IDLE
                    with c.when(S_R_BURST):
                        with wrapper.if_(rready & rvalid):
                            with wrapper.if_(rlast):
                                state <<= S_IDLE
                            with wrapper.else_():
                                burst_addr <<= burst_addr + 1
                                burst_len <<= burst_len - 1
                    with c.default():
                        pass

        # Wire DUT ports.
        # DUT drives (we read): aw* valid/addr/len/(id), w* valid/data/strb/last,
        #                       ar* valid/addr/len/(id), bready, rready
        # We drive (DUT inputs): aw_ready, w_ready, b_valid/resp/(id),
        #                        ar_ready, r_valid/data/resp/(id)/last
        port_map[sigs["awaddr"]] = awaddr
        port_map[sigs["awlen"]] = awlen
        port_map[sigs["awvalid"]] = awvalid
        port_map[sigs["awready"]] = awready
        # Tie off other AW sideband signals if present (we accept any value).
        for sb in ("awsize", "awburst", "awlock", "awcache", "awprot", "awqos", "awregion", "awuser"):
            if sb in sigs:
                # Sideband DUT outputs we ignore — declare an unused wire so the
                # port has somewhere to connect.
                w = wrapper.wire(f"{prefix}_slv_unused_{sb}", width=32)
                port_map[sigs[sb]] = w
        if "awid" in sigs and awid_w is not None:
            port_map[sigs["awid"]] = awid_w

        port_map[sigs["wdata"]] = wdata
        port_map[sigs["wstrb"]] = wstrb
        port_map[sigs["wvalid"]] = wvalid
        port_map[sigs["wready"]] = wready
        port_map[sigs["wlast"]] = wlast
        if "wuser" in sigs:
            w = wrapper.wire(f"{prefix}_slv_unused_wuser", width=32)
            port_map[sigs["wuser"]] = w

        port_map[sigs["bvalid"]] = bvalid
        port_map[sigs["bready"]] = bready
        port_map[sigs["bresp"]] = bresp
        if "bid" in sigs and bid_w is not None:
            port_map[sigs["bid"]] = bid_w
        if "buser" in sigs:
            port_map[sigs["buser"]] = 0

        port_map[sigs["araddr"]] = araddr
        port_map[sigs["arlen"]] = arlen
        port_map[sigs["arvalid"]] = arvalid
        port_map[sigs["arready"]] = arready
        for sb in ("arsize", "arburst", "arlock", "arcache", "arprot", "arqos", "arregion", "aruser"):
            if sb in sigs:
                w = wrapper.wire(f"{prefix}_slv_unused_{sb}", width=32)
                port_map[sigs[sb]] = w
        if "arid" in sigs and arid_w is not None:
            port_map[sigs["arid"]] = arid_w

        port_map[sigs["rvalid"]] = rvalid
        port_map[sigs["rready"]] = rready
        port_map[sigs["rdata"]] = rdata
        port_map[sigs["rresp"]] = rresp
        port_map[sigs["rlast"]] = rlast
        if "rid" in sigs and rid_w is not None:
            port_map[sigs["rid"]] = rid_w
        if "ruser" in sigs:
            port_map[sigs["ruser"]] = 0


# ---------------------------------------------------------------------------
# AXI-Lite slave lowering (DUT is master; bench acts as memory-backed responder)
# ---------------------------------------------------------------------------


@dataclass
class AXILiteSlaveLowering:  # cm:7d6f3a
    """Lower a memory-backed AXI-Lite slave responder to an engine-native FSM.

    The lowering accepts single-beat write and read transactions from a DUT
    AXI-Lite **master** port.  Writes apply WSTRB byte-merging.  The backing
    memory is an array of ``memory_depth`` words each ``data_width`` bits wide,
    exposed as wrapper output ports ``<prefix>_slv_mem_<i>`` so test code can
    inspect the contents after the simulation completes.

    Unlike :class:`AXI4SlaveLowering`, there are no burst transfers or IDs —
    AXI-Lite is always single-beat.  AW and W channels are accepted
    sequentially (AW first, then W); AR is accepted only when the channel is
    idle.  Write priority over read: when AW and AR arrive simultaneously,
    AW is accepted.

    Word addressing: the byte address is right-shifted by
    ``log2(data_width / 8)`` to obtain the word index into the memory.

    Args:
        memory_depth: Number of words in the backing memory.  Each word
            becomes a wrapper ``output_reg`` port.  Keep small (≤ 256) to
            avoid excessive wrapper size.
        data_width: Width of WDATA / RDATA in bits.  Must be a multiple of 8.
        addr_width: Width of AWADDR / ARADDR in bits.
        initial_memory: Optional ``{word_index: initial_value}`` dict.
            Words not present default to 0.

    Wrapper output ports created (per interface ``prefix``):

    * ``<prefix>_slv_mem_<i>`` — ``data_width``-bit contents of word *i*
    * ``<prefix>_slv_aw_count`` — 16-bit write-transaction counter
    * ``<prefix>_slv_ar_count`` — 16-bit read-transaction counter
    """

    memory_depth: int = 16
    data_width: int = 32
    addr_width: int = 32
    initial_memory: Mapping[int, int] | None = None
    protocol: str = "axi_lite"
    role: str = "master"  # DUT side; lowering is a *slave*

    def apply(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"AXILiteSlaveLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        if self.memory_depth <= 0:
            raise LoweringError(f"AXILiteSlaveLowering[{binding.prefix}]: memory_depth must be positive")
        if self.data_width % 8 != 0:
            raise LoweringError(f"AXILiteSlaveLowering[{binding.prefix}]: data_width must be a multiple of 8")

        from veriforge.dsl import mux  # noqa: PLC0415

        prefix = binding.prefix
        sigs = binding.signals
        depth = self.memory_depth
        n_bytes = self.data_width // 8
        word_addr_shift = (n_bytes - 1).bit_length()  # log2(n_bytes), 0 for 8-bit data
        addr_index_width = _bit_width_for(depth + 1)

        # State encoding: IDLE=0, WRITE=1, BRESP=2, RRESP=3
        S_IDLE = 0
        S_WRITE = 1
        S_BRESP = 2
        S_RRESP = 3

        state = wrapper.reg(f"{prefix}_slv_state", width=2)
        cur_addr = wrapper.reg(f"{prefix}_slv_addr", width=addr_index_width)

        # Memory cells (one output_reg per word for test inspection).
        mem_cells = [wrapper.output_reg(f"{prefix}_slv_mem_{i}", width=self.data_width) for i in range(depth)]

        # Transaction counters.
        aw_count = wrapper.output_reg(f"{prefix}_slv_aw_count", width=16)
        ar_count = wrapper.output_reg(f"{prefix}_slv_ar_count", width=16)

        # Channel wires.
        awvalid = wrapper.wire(f"{prefix}_slv_awvalid")
        awready = wrapper.wire(f"{prefix}_slv_awready")
        awaddr = wrapper.wire(f"{prefix}_slv_awaddr", width=self.addr_width)
        wvalid = wrapper.wire(f"{prefix}_slv_wvalid")
        wready = wrapper.wire(f"{prefix}_slv_wready")
        wdata = wrapper.wire(f"{prefix}_slv_wdata", width=self.data_width)
        wstrb = wrapper.wire(f"{prefix}_slv_wstrb", width=n_bytes)
        bvalid = wrapper.wire(f"{prefix}_slv_bvalid")
        bready = wrapper.wire(f"{prefix}_slv_bready")
        bresp = wrapper.wire(f"{prefix}_slv_bresp", width=2)
        arvalid = wrapper.wire(f"{prefix}_slv_arvalid")
        arready = wrapper.wire(f"{prefix}_slv_arready")
        araddr = wrapper.wire(f"{prefix}_slv_araddr", width=self.addr_width)
        rvalid = wrapper.wire(f"{prefix}_slv_rvalid")
        rready = wrapper.wire(f"{prefix}_slv_rready")
        rdata = wrapper.wire(f"{prefix}_slv_rdata", width=self.data_width)
        rresp = wrapper.wire(f"{prefix}_slv_rresp", width=2)

        # Combinational outputs.
        wrapper.assign(awready, state == S_IDLE)
        # AR accepted only when idle and no AW pending (AW has priority).
        wrapper.assign(arready, (state == S_IDLE) & ~awvalid)
        wrapper.assign(wready, state == S_WRITE)
        wrapper.assign(bvalid, state == S_BRESP)
        wrapper.assign(bresp, 0)
        wrapper.assign(rvalid, state == S_RRESP)
        wrapper.assign(rresp, 0)

        # Memory read-data mux.
        rdata_expr: object = 0
        for i in range(depth - 1, -1, -1):
            rdata_expr = mux(cur_addr == i, mem_cells[i], rdata_expr)
        wrapper.assign(rdata, rdata_expr)

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))

        # Byte-strobe write helper (identical pattern to AXI4SlaveLowering).
        def _merged_word(old_word: object) -> object:
            from veriforge.dsl import cat  # noqa: PLC0415

            byte_exprs: list[object] = []  # MSB-first for cat()
            for byte_i in range(n_bytes - 1, -1, -1):
                lo = byte_i * 8
                old_byte = old_word[lo + 7 : lo]
                new_byte = wdata[lo + 7 : lo]
                byte_exprs.append(mux(wstrb[byte_i], new_byte, old_byte))
            return cat(*byte_exprs)

        with wrapper.always(*sens):
            with wrapper.if_(rst_cond):
                state <<= S_IDLE
                cur_addr <<= 0
                aw_count <<= 0
                ar_count <<= 0
                init = self.initial_memory or {}
                for i, r in enumerate(mem_cells):
                    val = int(init.get(i, 0)) & ((1 << self.data_width) - 1)
                    r <<= val  # noqa: PLW2901
            with wrapper.else_():
                with wrapper.case(state) as c:
                    with c.when(S_IDLE):
                        # AW has priority over AR.
                        with wrapper.if_(awvalid & awready):
                            if word_addr_shift > 0:
                                cur_addr <<= awaddr[word_addr_shift + addr_index_width - 1 : word_addr_shift]
                            else:
                                cur_addr <<= awaddr[addr_index_width - 1 : 0]
                            aw_count <<= aw_count + 1
                            state <<= S_WRITE
                        with wrapper.elif_(arvalid & arready):
                            if word_addr_shift > 0:
                                cur_addr <<= araddr[word_addr_shift + addr_index_width - 1 : word_addr_shift]
                            else:
                                cur_addr <<= araddr[addr_index_width - 1 : 0]
                            ar_count <<= ar_count + 1
                            state <<= S_RRESP
                    with c.when(S_WRITE):
                        with wrapper.if_(wvalid & wready):
                            with wrapper.case(cur_addr) as wc:
                                for i, cell in enumerate(mem_cells):
                                    with wc.when(i):
                                        cell <<= _merged_word(cell)  # noqa: PLW2901
                                with wc.default():
                                    pass
                            state <<= S_BRESP
                    with c.when(S_BRESP):
                        with wrapper.if_(bready & bvalid):
                            state <<= S_IDLE
                    with c.when(S_RRESP):
                        with wrapper.if_(rready & rvalid):
                            state <<= S_IDLE
                    with c.default():
                        pass

        # Wire DUT ports.
        # DUT drives (bench reads): awvalid, awaddr, (awprot); wvalid, wdata, wstrb;
        #                           bready; arvalid, araddr, (arprot); rready
        # Bench drives (DUT inputs): awready; wready; bvalid, bresp;
        #                            arready; rvalid, rdata, rresp
        port_map[sigs["awaddr"]] = awaddr
        port_map[sigs["awvalid"]] = awvalid
        port_map[sigs["awready"]] = awready
        if "awprot" in sigs:
            awprot_w = wrapper.wire(f"{prefix}_slv_awprot", width=3)
            port_map[sigs["awprot"]] = awprot_w

        port_map[sigs["wdata"]] = wdata
        port_map[sigs["wstrb"]] = wstrb
        port_map[sigs["wvalid"]] = wvalid
        port_map[sigs["wready"]] = wready

        port_map[sigs["bvalid"]] = bvalid
        port_map[sigs["bready"]] = bready
        port_map[sigs["bresp"]] = bresp

        port_map[sigs["araddr"]] = araddr
        port_map[sigs["arvalid"]] = arvalid
        port_map[sigs["arready"]] = arready
        if "arprot" in sigs:
            arprot_w = wrapper.wire(f"{prefix}_slv_arprot", width=3)
            port_map[sigs["arprot"]] = arprot_w

        port_map[sigs["rvalid"]] = rvalid
        port_map[sigs["rready"]] = rready
        port_map[sigs["rdata"]] = rdata
        port_map[sigs["rresp"]] = rresp


# ---------------------------------------------------------------------------
# AXI4 master lowering (DUT is slave; bench drives scripted single-beat writes/reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AXI4MasterOp:
    """One scripted AXI4 single-beat operation.

    Use :meth:`write` / :meth:`read` factories rather than constructing
    directly.  ``kind`` is ``"write"`` or ``"read"``; for read ops
    ``data`` and ``strb`` are ignored.  All operations are single-beat
    (``awlen`` / ``arlen`` = 0, ``wlast`` permanently asserted).
    """

    kind: str  # "write" or "read"
    addr: int
    data: int = 0
    strb: int = 0xF  # default: all bytes (DW=32)

    @classmethod
    def write(cls, addr: int, data: int, *, strb: int | None = None) -> "AXI4MasterOp":
        return cls(kind="write", addr=addr, data=data, strb=0xF if strb is None else strb)

    @classmethod
    def read(cls, addr: int) -> "AXI4MasterOp":
        return cls(kind="read", addr=addr)


@dataclass
class AXI4MasterLowering:
    """Lower a scripted single-beat AXI4 master driver to an engine-native FSM.

    Walks ``operations`` in order, driving AW+W in parallel for writes
    (``awlen=0``, ``wlast=1``) and AR for reads (``arlen=0``), then
    capturing ``bresp`` for writes and ``rdata``/``rresp`` for reads.
    ``awburst``/``arburst`` are fixed to INCR (``2'b01``).

    Args:
        operations: Sequence of :class:`AXI4MasterOp`. Use the
            :meth:`AXI4MasterOp.write` / :meth:`AXI4MasterOp.read` factories.
        addr_width: Width of AW/AR addr in bits.
        data_width: Width of W/R data in bits. WSTRB is ``data_width/8``.
        id_width: Width of ID signals (``awid``/``arid``/``bid``/``rid``).
            Set to 0 (default) when the DUT has no ID signals. When > 0
            the lowering drives ``awid``/``arid`` as zero on all operations.

    Wrapper output ports created (per operation index ``i``):

    * ``<prefix>_op_<i>_resp`` — 2-bit response (B for write, R for read)
    * ``<prefix>_op_<i>_rdata`` — captured read data (0 for write ops)

    Plus a single ``<prefix>_master_done`` flag that latches when the
    final operation completes.
    """

    operations: Sequence[AXI4MasterOp]
    addr_width: int = 32
    data_width: int = 32
    id_width: int = 0
    protocol: str = "axi4"
    role: str = "slave"  # DUT side; lowering is a *master*

    def apply(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"AXI4MasterLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        n = len(self.operations)
        if n == 0:
            raise LoweringError(f"AXI4MasterLowering[{binding.prefix}]: operations must be non-empty")
        for i, op in enumerate(self.operations):
            if op.kind not in {"write", "read"}:
                raise LoweringError(
                    f"AXI4MasterLowering[{binding.prefix}].operations[{i}]: "
                    f"kind must be 'write' or 'read', got {op.kind!r}"
                )
        if self.data_width % 8 != 0:
            raise LoweringError(f"AXI4MasterLowering[{binding.prefix}]: data_width must be a multiple of 8")

        from veriforge.dsl import mux  # noqa: PLC0415

        prefix = binding.prefix
        sigs = binding.signals
        idx_width = _bit_width_for(n + 1)
        strb_width = self.data_width // 8
        # AXI4 AWSIZE/ARSIZE: encodes log2(bytes_per_beat) — 2 for 32-bit, 3 for 64-bit, etc.
        awsize_val = (self.data_width // 8).bit_length() - 1

        # Internal regs
        idx = wrapper.reg(f"{prefix}_mst_idx", width=idx_width)
        aw_done = wrapper.reg(f"{prefix}_mst_aw_done")
        w_done = wrapper.reg(f"{prefix}_mst_w_done")
        ar_done = wrapper.reg(f"{prefix}_mst_ar_done")

        # Wires for handshake / payload
        active = wrapper.wire(f"{prefix}_mst_active")
        is_write = wrapper.wire(f"{prefix}_mst_is_write")
        is_read = wrapper.wire(f"{prefix}_mst_is_read")
        awvalid = wrapper.wire(f"{prefix}_mst_awvalid")
        awaddr = wrapper.wire(f"{prefix}_mst_awaddr", width=self.addr_width)
        awready = wrapper.wire(f"{prefix}_mst_awready")
        wvalid = wrapper.wire(f"{prefix}_mst_wvalid")
        wdata = wrapper.wire(f"{prefix}_mst_wdata", width=self.data_width)
        wstrb = wrapper.wire(f"{prefix}_mst_wstrb", width=strb_width)
        wlast = wrapper.wire(f"{prefix}_mst_wlast")
        wready = wrapper.wire(f"{prefix}_mst_wready")
        bvalid = wrapper.wire(f"{prefix}_mst_bvalid")
        bready = wrapper.wire(f"{prefix}_mst_bready")
        bresp = wrapper.wire(f"{prefix}_mst_bresp", width=2)
        arvalid = wrapper.wire(f"{prefix}_mst_arvalid")
        araddr = wrapper.wire(f"{prefix}_mst_araddr", width=self.addr_width)
        arready = wrapper.wire(f"{prefix}_mst_arready")
        rvalid = wrapper.wire(f"{prefix}_mst_rvalid")
        rready = wrapper.wire(f"{prefix}_mst_rready")
        rdata = wrapper.wire(f"{prefix}_mst_rdata", width=self.data_width)
        rresp = wrapper.wire(f"{prefix}_mst_rresp", width=2)
        rlast = wrapper.wire(f"{prefix}_mst_rlast")

        # Optional ID wires
        awid_w = wrapper.wire(f"{prefix}_mst_awid", width=self.id_width) if self.id_width > 0 else None
        arid_w = wrapper.wire(f"{prefix}_mst_arid", width=self.id_width) if self.id_width > 0 else None
        bid_w = wrapper.wire(f"{prefix}_mst_bid", width=self.id_width) if self.id_width > 0 else None
        rid_w = wrapper.wire(f"{prefix}_mst_rid", width=self.id_width) if self.id_width > 0 else None

        # Build chained-mux ROMs over idx for op_type, addr, wdata, wstrb.
        addr_mask = (1 << self.addr_width) - 1
        data_mask = (1 << self.data_width) - 1
        strb_mask = (1 << strb_width) - 1

        is_write_expr: object = 1 if self.operations[-1].kind == "write" else 0
        addr_expr: object = self.operations[-1].addr & addr_mask
        wdata_expr: object = self.operations[-1].data & data_mask
        wstrb_expr: object = self.operations[-1].strb & strb_mask
        for i in range(n - 1, -1, -1):
            op = self.operations[i]
            iw = 1 if op.kind == "write" else 0
            is_write_expr = mux(idx == i, iw, is_write_expr)
            addr_expr = mux(idx == i, op.addr & addr_mask, addr_expr)
            wdata_expr = mux(idx == i, op.data & data_mask, wdata_expr)
            wstrb_expr = mux(idx == i, op.strb & strb_mask, wstrb_expr)

        wrapper.assign(active, idx < n)
        wrapper.assign(is_write, active & is_write_expr)
        wrapper.assign(is_read, active & ~is_write_expr)

        # AW channel: valid until aw_done latched
        wrapper.assign(awvalid, is_write & ~aw_done)
        wrapper.assign(awaddr, addr_expr)
        # W channel: valid until w_done latched; wlast always 1 (single-beat)
        wrapper.assign(wvalid, is_write & ~w_done)
        wrapper.assign(wdata, wdata_expr)
        wrapper.assign(wstrb, wstrb_expr)
        wrapper.assign(wlast, 1)
        # B channel: accept when both AW and W are done
        wrapper.assign(bready, is_write & aw_done & w_done)
        # AR channel: valid until ar_done latched
        wrapper.assign(arvalid, is_read & ~ar_done)
        wrapper.assign(araddr, addr_expr)
        # R channel: ready once AR accepted
        wrapper.assign(rready, is_read & ar_done)

        # Drive IDs as zero
        if awid_w is not None:
            wrapper.assign(awid_w, 0)
        if arid_w is not None:
            wrapper.assign(arid_w, 0)

        # Per-op capture regs (output ports)
        resp_caps = [wrapper.output_reg(f"{prefix}_op_{i}_resp", width=2) for i in range(n)]
        rdata_caps = [wrapper.output_reg(f"{prefix}_op_{i}_rdata", width=self.data_width) for i in range(n)]
        master_done = wrapper.output_reg(f"{prefix}_master_done")

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))
        with wrapper.always(*sens):
            with wrapper.if_(rst_cond):
                idx <<= 0
                aw_done <<= 0
                w_done <<= 0
                ar_done <<= 0
                master_done <<= 0
                for r in resp_caps:
                    r <<= 0  # noqa: PLW2901
                for r in rdata_caps:
                    r <<= 0  # noqa: PLW2901
            with wrapper.else_():
                with wrapper.if_(active):
                    with wrapper.if_(awvalid & awready):
                        aw_done <<= 1
                    with wrapper.if_(wvalid & wready):
                        w_done <<= 1
                    with wrapper.if_(arvalid & arready):
                        ar_done <<= 1
                    # Write completion: both AW and W done, waiting for B
                    with wrapper.if_(bvalid & bready):
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(resp_caps):
                                with c.when(i):
                                    r <<= bresp  # noqa: PLW2901
                            with c.default():
                                pass
                        idx <<= idx + 1
                        aw_done <<= 0
                        w_done <<= 0
                        with wrapper.if_(idx == (n - 1)):
                            master_done <<= 1
                    # Read completion: AR done, waiting for R
                    with wrapper.if_(rvalid & rready & rlast):
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(resp_caps):
                                with c.when(i):
                                    r <<= rresp  # noqa: PLW2901
                            with c.default():
                                pass
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(rdata_caps):
                                with c.when(i):
                                    r <<= rdata  # noqa: PLW2901
                            with c.default():
                                pass
                        idx <<= idx + 1
                        ar_done <<= 0
                        with wrapper.if_(idx == (n - 1)):
                            master_done <<= 1

        # Wire DUT ports.
        # Bench drives (DUT inputs): awid(opt), awaddr, awlen=0, awsize, awburst=INCR,
        #   awvalid; wdata, wstrb, wlast=1, wvalid; bready;
        #   arid(opt), araddr, arlen=0, arsize, arburst=INCR, arvalid; rready
        # DUT drives (bench reads): awready; wready; bvalid, bresp, bid(opt);
        #   arready; rvalid, rdata, rresp, rlast, rid(opt)
        port_map[sigs["awaddr"]] = awaddr
        port_map[sigs["awvalid"]] = awvalid
        port_map[sigs["awready"]] = awready
        port_map[sigs["awlen"]] = 0
        port_map[sigs["awsize"]] = awsize_val
        port_map[sigs["awburst"]] = 1  # INCR
        if "awid" in sigs and awid_w is not None:
            port_map[sigs["awid"]] = awid_w
        for sb in ("awlock", "awcache", "awprot", "awqos", "awregion", "awuser"):
            if sb in sigs:
                port_map[sigs[sb]] = 0

        port_map[sigs["wdata"]] = wdata
        port_map[sigs["wstrb"]] = wstrb
        port_map[sigs["wlast"]] = wlast
        port_map[sigs["wvalid"]] = wvalid
        port_map[sigs["wready"]] = wready
        if "wuser" in sigs:
            port_map[sigs["wuser"]] = 0

        port_map[sigs["bvalid"]] = bvalid
        port_map[sigs["bready"]] = bready
        port_map[sigs["bresp"]] = bresp
        if "bid" in sigs and bid_w is not None:
            port_map[sigs["bid"]] = bid_w

        port_map[sigs["araddr"]] = araddr
        port_map[sigs["arvalid"]] = arvalid
        port_map[sigs["arready"]] = arready
        port_map[sigs["arlen"]] = 0
        port_map[sigs["arsize"]] = awsize_val
        port_map[sigs["arburst"]] = 1  # INCR
        if "arid" in sigs and arid_w is not None:
            port_map[sigs["arid"]] = arid_w
        for sb in ("arlock", "arcache", "arprot", "arqos", "arregion", "aruser"):
            if sb in sigs:
                port_map[sigs[sb]] = 0

        port_map[sigs["rvalid"]] = rvalid
        port_map[sigs["rready"]] = rready
        port_map[sigs["rdata"]] = rdata
        port_map[sigs["rresp"]] = rresp
        port_map[sigs["rlast"]] = rlast
        if "rid" in sigs and rid_w is not None:
            port_map[sigs["rid"]] = rid_w


# ---------------------------------------------------------------------------
# MemBus master lowering (DUT is slave; bench drives scripted writes/reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemBusOp:
    """One scripted MemBus operation.

    Use :meth:`write` / :meth:`read` factories rather than constructing
    directly.  ``kind`` is ``"write"`` or ``"read"``; for read ops
    ``data`` and ``be`` are ignored.
    """

    kind: str  # "write" or "read"
    addr: int
    data: int = 0
    be: int | None = None  # None → all bytes enabled

    @classmethod
    def write(cls, addr: int, data: int, *, be: int | None = None) -> "MemBusOp":
        return cls(kind="write", addr=addr, data=data, be=be)

    @classmethod
    def read(cls, addr: int) -> "MemBusOp":
        return cls(kind="read", addr=addr)


@dataclass
class MemBusMasterLowering:
    """Lower a scripted MemBus master driver to an engine-native FSM.

    Drives the synchronous memory bus against a DUT **slave** port.  Each
    operation asserts the appropriate signals for exactly one clock cycle
    (single-cycle transactions, consistent with :class:`MemBusMaster`).
    Write: asserts ``addr``, ``wdata``, ``wen`` (and ``be`` when present).
    Read: asserts ``addr`` (and ``ren`` when present); captures ``rdata``
    on the following posedge.

    Args:
        operations: Sequence of :class:`MemBusOp`. Use the
            :meth:`MemBusOp.write` / :meth:`MemBusOp.read` factories.
        addr_width: Width of ``addr`` in bits.
        data_width: Width of ``wdata``/``rdata`` in bits. Must be a multiple of 8.
        has_ren: Whether the DUT has a ``ren`` port. When ``True``, the
            lowering drives it alongside ``addr`` on reads.
        be_width: Width of the ``be`` byte-enable port in bits.  Set to 0
            (default) to omit byte-enable driving.  When non-zero, writes
            drive ``be`` from the :attr:`MemBusOp.be` field (default: all
            bytes enabled = ``(1 << be_width) - 1``).

    Wrapper output ports created (per operation index ``i``):

    * ``<prefix>_op_<i>_rdata`` — captured read data (0 for write ops)

    Plus a single ``<prefix>_master_done`` flag that latches when the
    final operation completes.
    """

    operations: Sequence[MemBusOp]
    addr_width: int = 32
    data_width: int = 32
    has_ren: bool = False
    be_width: int = 0
    protocol: str = "membus"
    role: str = "slave"  # DUT side; lowering is a *master*

    def apply(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"MemBusMasterLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        n = len(self.operations)
        if n == 0:
            raise LoweringError(f"MemBusMasterLowering[{binding.prefix}]: operations must be non-empty")
        for i, op in enumerate(self.operations):
            if op.kind not in {"write", "read"}:
                raise LoweringError(
                    f"MemBusMasterLowering[{binding.prefix}].operations[{i}]: "
                    f"kind must be 'write' or 'read', got {op.kind!r}"
                )
        if self.data_width % 8 != 0:
            raise LoweringError(f"MemBusMasterLowering[{binding.prefix}]: data_width must be a multiple of 8")

        from veriforge.dsl import mux

        prefix = binding.prefix
        sigs = binding.signals
        idx_width = _bit_width_for(n + 1)
        addr_mask = (1 << self.addr_width) - 1
        data_mask = (1 << self.data_width) - 1
        all_be = (1 << self.be_width) - 1 if self.be_width > 0 else 0

        # Internal counter + state
        idx = wrapper.reg(f"{prefix}_mst_idx", width=idx_width)
        read_pending = wrapper.reg(f"{prefix}_mst_read_pending")

        # Combinational wires for signals driven to DUT
        active = wrapper.wire(f"{prefix}_mst_active")
        is_write = wrapper.wire(f"{prefix}_mst_is_write")
        addr_w = wrapper.wire(f"{prefix}_mst_addr", width=self.addr_width)
        wdata_w = wrapper.wire(f"{prefix}_mst_wdata", width=self.data_width)
        wen_w = wrapper.wire(f"{prefix}_mst_wen")
        rdata_w = wrapper.wire(f"{prefix}_mst_rdata", width=self.data_width)

        # Build chained-mux ROMs over idx.
        is_write_expr: object = 1 if self.operations[-1].kind == "write" else 0
        addr_expr: object = self.operations[-1].addr & addr_mask
        wdata_expr: object = self.operations[-1].data & data_mask
        for i in range(n - 1, -1, -1):
            op = self.operations[i]
            iw = 1 if op.kind == "write" else 0
            is_write_expr = mux(idx == i, iw, is_write_expr)
            addr_expr = mux(idx == i, op.addr & addr_mask, addr_expr)
            wdata_expr = mux(idx == i, op.data & data_mask, wdata_expr)

        wrapper.assign(active, idx < n)
        wrapper.assign(is_write, active & is_write_expr)
        # Drive addr whenever active (keeps addr stable across both cycles of a read).
        # wdata and wen are only asserted during the request cycle (not read_pending).
        wrapper.assign(addr_w, mux(active, addr_expr, 0))
        wrapper.assign(wdata_w, mux(is_write & ~read_pending, wdata_expr, 0))
        wrapper.assign(wen_w, is_write & ~read_pending)

        # Per-op captured read data + done flag
        rdata_caps = [wrapper.output_reg(f"{prefix}_op_{i}_rdata", width=self.data_width) for i in range(n)]
        master_done = wrapper.output_reg(f"{prefix}_master_done")

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))

        with wrapper.always(*sens):
            with wrapper.if_(rst_cond):
                idx <<= 0
                read_pending <<= 0
                master_done <<= 0
                for r in rdata_caps:
                    r <<= 0  # noqa: PLW2901
            with wrapper.else_():
                with wrapper.if_(active):
                    with wrapper.if_(read_pending):
                        # Second cycle: capture rdata, then advance.
                        with wrapper.case(idx) as c:
                            for i, r in enumerate(rdata_caps):
                                with c.when(i):
                                    r <<= rdata_w  # noqa: PLW2901
                            with c.default():
                                pass
                        read_pending <<= 0
                        idx <<= idx + 1
                        with wrapper.if_(idx == (n - 1)):
                            master_done <<= 1
                    with wrapper.elif_(is_write):
                        # Write: single-cycle — advance immediately.
                        idx <<= idx + 1
                        with wrapper.if_(idx == (n - 1)):
                            master_done <<= 1
                    with wrapper.else_():
                        # Read: assert addr/ren this cycle, wait next cycle for rdata.
                        read_pending <<= 1

        # Wire DUT ports.
        port_map[sigs["addr"]] = addr_w
        port_map[sigs["wdata"]] = wdata_w
        port_map[sigs["wen"]] = wen_w
        port_map[sigs["rdata"]] = rdata_w
        if "ren" in sigs:
            ren_w = wrapper.wire(f"{prefix}_mst_ren")
            # ren is active during the request cycle (before read_pending)
            wrapper.assign(ren_w, active & ~is_write & ~read_pending)
            port_map[sigs["ren"]] = ren_w
        if "be" in sigs and self.be_width > 0:
            be_expr: object = all_be
            for i in range(n - 1, -1, -1):
                op = self.operations[i]
                if op.be is not None:
                    be_expr = mux(idx == i, int(op.be) & all_be, be_expr)
            be_w = wrapper.wire(f"{prefix}_mst_be", width=self.be_width)
            wrapper.assign(be_w, mux(is_write & ~read_pending, be_expr, 0))
            port_map[sigs["be"]] = be_w
        if "rvalid" in sigs:
            rvalid_w = wrapper.wire(f"{prefix}_mst_rvalid")
            port_map[sigs["rvalid"]] = rvalid_w


# ---------------------------------------------------------------------------
# MemBus responder lowering (DUT is master; bench acts as memory-backed slave)
# ---------------------------------------------------------------------------


@dataclass
class MemBusResponderLowering:
    """Lower a memory-backed MemBus slave responder to an engine-native FSM.

    The lowering responds to synchronous memory-bus transactions from a DUT
    **master** port.  On every rising edge:

    * If ``wen`` is asserted: apply WSTRB-merged write to backing memory.
    * If ``ren`` is asserted (or port absent): drive ``rdata`` from backing
      memory combinatorially; optionally assert ``rvalid`` for one cycle.

    The backing memory is an array of ``memory_depth`` words each
    ``data_width`` bits wide, exposed as wrapper output ports
    ``<prefix>_rsp_mem_<i>`` for post-simulation inspection.

    Args:
        memory_depth: Number of words in the backing memory.  Each word
            becomes a wrapper ``output_reg`` port.  Keep small (≤ 256) to
            avoid excessive wrapper size.
        data_width: Width of ``wdata``/``rdata`` in bits.  Must be a
            multiple of 8.
        addr_width: Width of ``addr`` in bits.
        has_be: Whether the DUT drives a ``be`` byte-enable port.  When
            ``True``, writes apply per-byte masking.
        has_ren: Whether the DUT drives a separate ``ren`` read-enable.
        has_rvalid: Whether the DUT has an ``rvalid`` input.  When
            ``True``, the responder pulses ``rvalid`` high for one cycle
            after each read request.
        initial_memory: Optional ``{word_index: initial_value}`` dict.

    Wrapper output ports created (per interface ``prefix``):

    * ``<prefix>_rsp_mem_<i>`` — ``data_width``-bit contents of word *i*
    * ``<prefix>_rsp_wr_count`` — 16-bit write-transaction counter
    * ``<prefix>_rsp_rd_count`` — 16-bit read-transaction counter
    """

    memory_depth: int = 16
    data_width: int = 32
    addr_width: int = 32
    has_be: bool = False
    has_ren: bool = False
    has_rvalid: bool = False
    initial_memory: Mapping[int, int] | None = None
    protocol: str = "membus"
    role: str = "master"  # DUT side; lowering is a *responder/slave*

    def apply(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        wrapper: DSLModule,
        *,
        binding: InterfaceBinding,
        domain: ClockDomain,
        clk: "Signal",
        rst: "Signal | None",
        port_map: dict[str, object],
    ) -> None:
        if binding.role != self.role:
            raise LoweringError(
                f"MemBusResponderLowering expects DUT role={self.role!r} "
                f"for interface {binding.prefix!r}, got {binding.role!r}"
            )
        if self.memory_depth <= 0:
            raise LoweringError(f"MemBusResponderLowering[{binding.prefix}]: memory_depth must be positive")
        if self.data_width % 8 != 0:
            raise LoweringError(f"MemBusResponderLowering[{binding.prefix}]: data_width must be a multiple of 8")

        from veriforge.dsl import mux

        prefix = binding.prefix
        sigs = binding.signals
        depth = self.memory_depth
        n_bytes = self.data_width // 8
        addr_index_width = _bit_width_for(depth + 1)

        # Memory cells (one output_reg per word for test inspection).
        mem_cells = [wrapper.output_reg(f"{prefix}_rsp_mem_{i}", width=self.data_width) for i in range(depth)]

        # Transaction counters.
        wr_count = wrapper.output_reg(f"{prefix}_rsp_wr_count", width=16)
        rd_count = wrapper.output_reg(f"{prefix}_rsp_rd_count", width=16)

        # Wires driven by DUT.
        addr_w = wrapper.wire(f"{prefix}_rsp_addr", width=self.addr_width)
        wdata_w = wrapper.wire(f"{prefix}_rsp_wdata", width=self.data_width)
        wen_w = wrapper.wire(f"{prefix}_rsp_wen")
        rdata_w = wrapper.wire(f"{prefix}_rsp_rdata", width=self.data_width)

        # MemBus uses word addresses directly (unlike AXI byte addresses).
        word_addr = addr_w[addr_index_width - 1 : 0]

        # Combinational read-data mux.
        rdata_expr: object = 0
        for i in range(depth - 1, -1, -1):
            rdata_expr = mux(word_addr == i, mem_cells[i], rdata_expr)
        wrapper.assign(rdata_w, rdata_expr)

        rst_cond = _reset_condition(rst, domain)
        sens: list[object] = [posedge(clk)]
        if rst is not None and domain.reset is not None and domain.reset.style == "async":
            sens.append(negedge(rst) if domain.reset.active_low else posedge(rst))

        # Byte-strobe write helper.
        be_w = None
        if self.has_be and "be" in sigs:
            be_w = wrapper.wire(f"{prefix}_rsp_be", width=n_bytes)

        def _merged_word(old_word: object) -> object:
            if be_w is None:
                return wdata_w
            from veriforge.dsl import cat

            byte_exprs: list[object] = []
            for byte_i in range(n_bytes - 1, -1, -1):
                lo = byte_i * 8
                old_byte = old_word[lo + 7 : lo]
                new_byte = wdata_w[lo + 7 : lo]
                byte_exprs.append(mux(be_w[byte_i], new_byte, old_byte))
            return cat(*byte_exprs)

        # Optional ren and rvalid
        ren_w = None
        if self.has_ren and "ren" in sigs:
            ren_w = wrapper.wire(f"{prefix}_rsp_ren")
        rvalid_reg = None
        if self.has_rvalid and "rvalid" in sigs:
            rvalid_reg = wrapper.reg(f"{prefix}_rsp_rvalid_r")

        with wrapper.always(*sens):
            with wrapper.if_(rst_cond):
                wr_count <<= 0
                rd_count <<= 0
                if rvalid_reg is not None:
                    rvalid_reg <<= 0
                init = self.initial_memory or {}
                for i, r in enumerate(mem_cells):
                    val = int(init.get(i, 0)) & ((1 << self.data_width) - 1)
                    r <<= val  # noqa: PLW2901
            with wrapper.else_():
                if rvalid_reg is not None:
                    rvalid_reg <<= 0  # pulse for one cycle
                # Write: wen asserted
                with wrapper.if_(wen_w):
                    with wrapper.case(word_addr) as c:
                        for i, cell in enumerate(mem_cells):
                            with c.when(i):
                                cell <<= _merged_word(cell)  # noqa: PLW2901
                        with c.default():
                            pass
                    wr_count <<= wr_count + 1
                # Read: ren asserted (or absent — reads happen every cycle).
                read_en_expr = ren_w if ren_w is not None else 1
                with wrapper.elif_(read_en_expr):
                    rd_count <<= rd_count + 1
                    if rvalid_reg is not None:
                        rvalid_reg <<= 1

        # Wire DUT ports.
        port_map[sigs["addr"]] = addr_w
        port_map[sigs["wdata"]] = wdata_w
        port_map[sigs["wen"]] = wen_w
        port_map[sigs["rdata"]] = rdata_w
        if ren_w is not None:
            port_map[sigs["ren"]] = ren_w
        if be_w is not None:
            port_map[sigs["be"]] = be_w
        if rvalid_reg is not None:
            port_map[sigs["rvalid"]] = rvalid_reg


@dataclass(frozen=True)
class LoweredDesign:  # cm:3e3a4c
    """Result of a successful :func:`compile_native` call.

    Attributes:
        wrapper: The synthesized DSL wrapper module (model form), ready
            to pass to :class:`veriforge.sim.Simulator` together
            with ``design`` for hierarchy resolution.
        design: A :class:`Design` containing both the wrapper and the
            original DUT module.
        capture_signals: Per-interface mapping of ``prefix -> [list of
            wrapper output port names that hold captured beats]``. Empty
            list for interfaces that don't capture.
        done_signals: Per-interface mapping of ``prefix -> done port
            name`` for sink lowerings (sources do not currently expose a
            done flag).
        plan: The :class:`TestbenchPlan` used to build this lowered
            design. Consumed by :meth:`run` to schedule clocks and
            sequence reset automatically.
    """

    wrapper: "ModelModule"
    design: Design
    capture_signals: Mapping[str, list[str]]
    done_signals: Mapping[str, str]
    plan: TestbenchPlan

    def run(
        self,
        engine: str = "reference",
        *,
        max_time: int | None = None,
        vcd: str | Path | None = None,
        vcd_timescale: str = "1ns",
        vcd_signals: Iterable[str] | None = None,
    ) -> dict[str, int]:
        """Run the lowered design end-to-end and return all capture signal values.

        Creates a :class:`~veriforge.sim.Simulator` for the wrapper module,
        schedules clocks from the plan, sequences reset (assert → a few cycles
        → release), optionally attaches a VCD trace, runs the simulation, and
        returns the captured output signal values.

        Args:
            engine: Simulator engine (``"reference"``, ``"vm"``, ``"compiled"``).
            max_time: Simulation time limit in simulator time units. Defaults to
                1000 x the minimum clock period across all domains (or 10000 if
                no period hints are set).
            vcd: Optional path to write a VCD trace file.
            vcd_timescale: VCD ``$timescale`` directive (default ``"1ns"``).
            vcd_signals: Signal names to record. ``None`` records all signals.

        Returns:
            Dict mapping capture-signal name to integer value. The names match
            those listed in :attr:`capture_signals` (AXIS sink beats, AXI-Lite
            op results, AXI slave memory cells, etc.).
        """
        from veriforge.sim.testbench import Clock, Simulator
        from veriforge.sim.trace import attach_vcd

        _DEFAULT_PERIOD = 10
        period_hints = [dom.clock.period_hint for dom in self.plan.domains if dom.clock.period_hint is not None]
        min_period = min(period_hints, default=_DEFAULT_PERIOD)
        effective_max_time = min_period * 1000 if max_time is None else max_time

        sim = Simulator(self.wrapper, design=self.design, engine=engine)

        for dom in self.plan.domains:
            period = dom.clock.period_hint if dom.clock.period_hint is not None else _DEFAULT_PERIOD
            sim.fork(Clock(sim.signal(dom.clock.name), period=period))

        # Reset sequence: assert → run a few cycles → release
        for dom in self.plan.domains:
            if dom.reset is not None:
                sim.signal(dom.reset.name).value = dom.reset.assert_level
        sim.run(max_time=min_period * 4)
        for dom in self.plan.domains:
            if dom.reset is not None:
                sim.signal(dom.reset.name).value = dom.reset.release_level

        # Optional VCD trace (attached after reset so reset transients are still visible)
        trace = None
        if vcd is not None:
            trace = attach_vcd(sim, vcd, timescale=vcd_timescale, signal_names=vcd_signals)

        try:
            sim.run(max_time=effective_max_time)
        finally:
            if trace is not None:
                trace.close()

        return {name: int(sim.signal(name).value) for sigs in self.capture_signals.values() for name in sigs} | {
            done_name: int(sim.signal(done_name).value) for done_name in self.done_signals.values()
        }

    def batch_run(
        self,
        cycles: int = 1000,
        *,
        clock_name: str | None = None,
        clock_period: int | None = None,
        reset_cycles: int = 4,
    ) -> dict[str, int]:
        """Run the lowered design using the compiled engine's C-level batch loop.

        Unlike :meth:`run`, this method drives the clock and applies reset
        entirely inside a single C-level loop with no Python per-cycle overhead.
        It is the fastest execution path for data-driven lowered designs.

        VCD tracing is **not** supported in batch mode — the C loop does not
        invoke Python callbacks.  Use :meth:`run` when a waveform trace is
        needed.

        Only single-domain lowered designs support automatic clock detection.
        For multi-domain designs supply *clock_name* explicitly.

        Args:
            cycles: Total clock cycles to run.  Must be greater than
                *reset_cycles*.  Defaults to 1000.
            clock_name: Name of the clock signal to drive.  Auto-detected from
                the plan when the design has exactly one clock domain.
            clock_period: Period in simulator time units.  Auto-detected from
                the domain's ``period_hint`` when *None* (falls back to 10).
            reset_cycles: Clock cycles to hold each domain's reset asserted
                before releasing.  Delivered as pre-scheduled batch events so
                no extra ``sim.run()`` call is needed.

        Returns:
            Dict mapping capture-signal name to integer value (same layout as
            :meth:`run`).

        Raises:
            ValueError: If *reset_cycles* >= *cycles*, or if the plan has
                multiple domains and *clock_name* is not supplied, or if the
                plan has no domains and *clock_name* is not supplied.
        """
        from veriforge.sim.testbench import Simulator

        _DEFAULT_PERIOD = 10

        if reset_cycles >= cycles:
            raise ValueError(f"reset_cycles ({reset_cycles}) must be less than cycles ({cycles})")

        # Auto-detect clock_name / clock_period from the plan.
        if clock_name is None:
            if not self.plan.domains:
                raise ValueError("batch_run: plan has no domains; provide clock_name explicitly")
            if len(self.plan.domains) > 1:
                names = [d.clock.name for d in self.plan.domains]
                raise ValueError(
                    f"batch_run: plan has {len(self.plan.domains)} domains ({names}); "
                    "provide clock_name explicitly for multi-domain lowered designs"
                )
            primary_dom = self.plan.domains[0]
            clock_name = primary_dom.clock.name
            if clock_period is None:
                clock_period = primary_dom.clock.period_hint or _DEFAULT_PERIOD
        elif clock_period is None:
            for dom in self.plan.domains:
                if dom.clock.name == clock_name:
                    clock_period = dom.clock.period_hint or _DEFAULT_PERIOD
                    break
            else:
                clock_period = _DEFAULT_PERIOD

        # Build reset events: assert at cycle 0, release at reset_cycles.
        # Explicit cycle-0 assert ensures correctness regardless of initial
        # signal state in the compiled engine.
        events: list[tuple[int, str, int]] = []
        for dom in self.plan.domains:
            if dom.reset is not None:
                events.append((0, dom.reset.name, dom.reset.assert_level))
                events.append((reset_cycles, dom.reset.name, dom.reset.release_level))
        events.sort(key=lambda e: e[0])

        sim = Simulator(self.wrapper, design=self.design, engine="compiled")
        sim.batch_run(cycles, clock_name, clock_period, events=events if events else None)

        return {name: int(sim.signal(name).value) for sigs in self.capture_signals.values() for name in sigs} | {
            done_name: int(sim.signal(done_name).value) for done_name in self.done_signals.values()
        }


def compile_native(  # noqa: PLR0912, PLR0915  # cm:b7b6d5
    bench: "Testbench",
    *,
    lowerings: Mapping[str, InterfaceLowering],
    name: str = "bench_native_top",
) -> LoweredDesign:
    """Synthesize a wrapper module that runs ``bench`` natively in the engine.

    Args:
        bench: A constructed :class:`Testbench` instance. Only its
            :attr:`Testbench.plan` and :attr:`Testbench.module` are
            consulted; no Python-stepped state is reused.
        lowerings: Mapping from interface ``prefix`` to an
            :class:`InterfaceLowering`. Every interface present in the
            plan must have a corresponding lowering — partial native
            mode is intentionally rejected so failures are loud rather
            than silently mixed.
        name: Wrapper module name (also the top in the returned design).

    Returns:
        A :class:`LoweredDesign` ready to feed to ``Simulator(wrapper,
        design=design, engine="compiled")`` (or ``"vm"``,
        ``"reference"``).

    Raises:
        LoweringError: on any subset violation — unknown prefix, missing
            lowering, role mismatch, or empty / out-of-range stimulus.
    """
    plan: TestbenchPlan = bench.plan
    dut = bench.module

    plan_prefixes = {ib.prefix for ib in plan.interfaces}
    unknown = sorted(set(lowerings) - plan_prefixes)
    if unknown:
        raise LoweringError(
            f"compile_native: lowerings reference unknown interface prefix(es) {unknown}; "
            f"plan has {sorted(plan_prefixes)}"
        )
    missing = sorted(plan_prefixes - set(lowerings))
    if missing:
        raise LoweringError(
            f"compile_native: no lowering provided for interface(s) {missing}. "
            "Partial native mode is not supported in this drop — supply a lowering "
            "for every interface or fall back to the Python-stepped Testbench."
        )

    capture_signals: dict[str, list[str]] = {}
    done_signals: dict[str, str] = {}

    with DSLModule(name) as w:
        clk_signals: dict[str, object] = {}
        rst_signals: dict[str, object | None] = {}

        for dom in plan.domains:
            clk_signals[dom.name] = w.input(dom.clock.name)
            if dom.reset is not None:
                rst_signals[dom.name] = w.input(dom.reset.name)
            else:
                rst_signals[dom.name] = None

        port_map: dict[str, object] = {}

        for prefix, lowering in lowerings.items():
            binding = plan.interface(prefix)
            if lowering.protocol != binding.protocol:
                raise LoweringError(
                    f"lowering for {prefix!r} protocol {lowering.protocol!r} does not match "
                    f"plan protocol {binding.protocol!r}"
                )
            domain = plan.domain(binding.domain_name)
            lowering.apply(
                w,
                binding=binding,
                domain=domain,
                clk=clk_signals[domain.name],  # type: ignore[arg-type]
                rst=rst_signals[domain.name],  # type: ignore[arg-type]
                port_map=port_map,
            )

            if isinstance(lowering, AXIStreamSinkLowering):
                if lowering.data_prng_seed is not None:
                    # PRNG check mode: no per-beat cap regs; expose error status instead.
                    capture_signals[prefix] = [
                        f"{prefix}_snk_err_cnt",
                        f"{prefix}_snk_err_flag",
                    ]
                else:
                    capture_signals[prefix] = [f"{prefix}_cap_{i}" for i in range(lowering.n_beats)]
                done_signals[prefix] = f"{prefix}_snk_done"
            elif isinstance(lowering, AXILiteMasterLowering):
                n_ops = len(lowering.operations)
                capture_signals[prefix] = [f"{prefix}_op_{i}_resp" for i in range(n_ops)] + [
                    f"{prefix}_op_{i}_rdata" for i in range(n_ops)
                ]
                done_signals[prefix] = f"{prefix}_master_done"
            elif isinstance(lowering, AXI4MasterLowering):
                n_ops = len(lowering.operations)
                capture_signals[prefix] = [f"{prefix}_op_{i}_resp" for i in range(n_ops)] + [
                    f"{prefix}_op_{i}_rdata" for i in range(n_ops)
                ]
                done_signals[prefix] = f"{prefix}_master_done"
            elif isinstance(lowering, AXI4SlaveLowering):
                capture_signals[prefix] = [f"{prefix}_slv_mem_{i}" for i in range(lowering.memory_depth)]
            elif isinstance(lowering, AXILiteSlaveLowering):
                capture_signals[prefix] = [f"{prefix}_slv_mem_{i}" for i in range(lowering.memory_depth)]
            elif isinstance(lowering, MemBusMasterLowering):
                n_ops = len(lowering.operations)
                capture_signals[prefix] = [f"{prefix}_op_{i}_rdata" for i in range(n_ops)]
                done_signals[prefix] = f"{prefix}_master_done"
            elif isinstance(lowering, MemBusResponderLowering):
                capture_signals[prefix] = [f"{prefix}_rsp_mem_{i}" for i in range(lowering.memory_depth)]
            else:
                capture_signals[prefix] = []

        # Connect DUT clock/reset ports through.
        for dom in plan.domains:
            port_map[dom.clock.name] = clk_signals[dom.name]
            if dom.reset is not None:
                port_map[dom.reset.name] = rst_signals[dom.name]

        # Tie off remaining DUT inputs to 0 (covers heartbeat anchors and any
        # un-bound user inputs).
        bound = set(port_map.keys())
        for dut_port in dut.input_ports():
            if dut_port.name not in bound:
                port_map[dut_port.name] = 0
                bound.add(dut_port.name)

        # Drop any keys that aren't actually DUT ports (shouldn't normally
        # happen, but defensive against duplicate domain-name aliases).
        dut_port_names = {p.name for p in dut.input_ports()} | {p.name for p in dut.output_ports()}
        clean_port_map = {k: v for k, v in port_map.items() if k in dut_port_names}

        w.instance(dut.name, "u_dut", ports=clean_port_map)

    wrapper_module = w.build()
    design = Design(modules=[wrapper_module, dut])
    return LoweredDesign(
        wrapper=wrapper_module,
        design=design,
        capture_signals=capture_signals,
        done_signals=done_signals,
        plan=plan,
    )
