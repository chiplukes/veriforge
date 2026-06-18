"""High-level testbench planning and runtime.

This package provides the full bench framework for simulating Verilog/SV DUTs
at the transaction level.  Key entry points:

:class:`Testbench`
    Main bench object.  Parses a module, infers clock/reset domains and
    interface bundles automatically, then exposes them via :meth:`~Testbench.domain`
    and :meth:`~Testbench.iface`.  Supports AXI4, AXI4-Lite, AXI4-Stream, and
    MemBus interfaces out of the box.

:class:`Domain`
    Represents one clock domain.  Use :meth:`~Domain.step` to advance cycles,
    :meth:`~Domain.reset` / :meth:`~Domain.release_reset` for reset control.

:func:`make_bench`
    Convenience wrapper that constructs a :class:`Testbench` from raw Verilog
    source text or a DSL :class:`~veriforge.dsl.Module`.

:class:`LoweredDesign` / :func:`compile_native`
    Engine-native (compiled/VM) lowering for high-speed simulation.  Call
    :func:`compile_native` with a :class:`Testbench` to get a
    :class:`LoweredDesign` that replaces Python endpoint callbacks with
    generated Verilog/C logic.

Typical usage::

    from veriforge.sim.bench import Testbench

    bench = Testbench(my_module, engine="vm")
    with bench.run():
        bench.reset_all()
        mem = bench.iface("mem")   # MemBusProxy — role inferred automatically
        axi = bench.iface("s_axi") # AXILiteProxy
        mem.write(0, 0xDEADBEEF)
        assert mem.read(0) == 0xDEADBEEF
"""

from __future__ import annotations

from .plan import (
    ClockDomain,
    ClockSpec,
    InterfaceBinding,
    PlanValidationError,
    ResetSpec,
    TestbenchPlan,
)
from .planner import (
    AmbiguousDomainError,
    NoDomainError,
    PlannerOverrides,
    build_plan,
)
from .interfaces import (
    AXI4Proxy,
    AXILiteProxy,
    AXILiteProtocolError,
    AXIStreamProxy,
    BenchTimeoutError,
    MemBusProxy,
    StreamProxy,
)
from .lowering import (
    AXI4MasterLowering,
    AXI4MasterOp,
    AXI4SlaveLowering,
    AXILiteMasterLowering,
    AXILiteOp,
    AXILiteSlaveLowering,
    AXIStreamSinkLowering,
    AXIStreamSourceLowering,
    InterfaceLowering,
    LoweredDesign,
    LoweringError,
    MemBusMasterLowering,
    MemBusOp,
    MemBusResponderLowering,
    compile_native,
)
from .runtime import Domain, Testbench, make_bench

__all__ = [
    "AXI4MasterLowering",
    "AXI4MasterOp",
    "AXI4Proxy",
    "AXI4SlaveLowering",
    "AXILiteMasterLowering",
    "AXILiteOp",
    "AXILiteProtocolError",
    "AXILiteProxy",
    "AXILiteSlaveLowering",
    "AXIStreamProxy",
    "AXIStreamSinkLowering",
    "AXIStreamSourceLowering",
    "AmbiguousDomainError",
    "BenchTimeoutError",
    "ClockDomain",
    "ClockSpec",
    "Domain",
    "InterfaceBinding",
    "InterfaceLowering",
    "LoweredDesign",
    "LoweringError",
    "MemBusMasterLowering",
    "MemBusOp",
    "MemBusProxy",
    "MemBusResponderLowering",
    "NoDomainError",
    "PlanValidationError",
    "PlannerOverrides",
    "ResetSpec",
    "StreamProxy",
    "Testbench",
    "TestbenchPlan",
    "build_plan",
    "compile_native",
    "make_bench",
]
