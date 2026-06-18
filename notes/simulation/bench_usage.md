# Python Testbench (`Testbench`) Usage Guide

This guide covers the Python-stepped testbench workflow built on
`veriforge.sim.bench`.  For the engine-native (compiled/VM) path that
removes the per-cycle Python overhead, see
[bench_native_lowering.md](bench_native_lowering.md).

---

## Quick start

```python
from veriforge.project import parse_file
from veriforge.sim.bench import Testbench

module = parse_file("my_dut.v")

bench = Testbench(module, engine="vm")
with bench.run():
    bench.reset_all()
    axis_in  = bench.iface("s_axis")   # AXI-Stream slave — bench sends
    axis_out = bench.iface("m_axis")   # AXI-Stream master — bench receives

    axis_in.put([0x11, 0x22, 0x33])
    frame = axis_out.get(timeout=200)
    assert list(frame.data) == [0x11, 0x22, 0x33]
```

`Testbench` auto-infers clock/reset ports and all AXI4, AXI4-Lite, AXI4-Stream,
and MemBus interface bundles.  No boilerplate port wiring is required.

---

## Construction

```python
bench = Testbench(
    module,                    # parsed ModelModule (from parse_file / parse_files)
    engine="vm",               # "reference" | "vm" | "compiled"
    overrides=None,            # PlannerOverrides or dict — optional
    strict=True,               # raise PlanValidationError on ambiguous clocks
    default_clock_period=10,   # full clock period in simulator time units
    max_sim_time=1_000_000,    # upper bound for the event queue
)
```

**`make_bench`** is a thin alias for `Testbench(module, **kwargs)`:

```python
from veriforge.sim.bench import make_bench
bench = make_bench(module, engine="vm")   # module is a parsed ModelModule
```

---

## `bench.run()` context manager

`run()` is a lightweight context manager that optionally attaches a VCD
recorder and ensures it is finalized on exit (even if the test raises).
Clock scheduling and domain setup happen in `Testbench.__init__`, not here.

```python
with bench.run():
    bench.reset_all()
    # ... test logic ...

# With VCD capture:
with bench.run(vcd="dump.vcd"):
    bench.reset_all()
    ...

# Filtered signals only:
with bench.run(vcd="dump.vcd", vcd_signals=["clk", "s_axis_tvalid"]):
    bench.reset_all()
    ...
```

`run(vcd=None, vcd_timescale="1ns", vcd_signals=None)` — all arguments are optional.

### `bench.reset_all()`

Asserts all known resets for 4 cycles, then releases them and settles for 2
cycles.  Safe to call multiple times within a single `run()` context.

---

## Clock domains

`Testbench.domain(name)` returns the `Domain` object for a given clock domain.
Most single-clock designs have a single domain inferred automatically; you
rarely need to call this directly.

```python
dom = bench.domain("clk")   # get the "clk" domain
dom.step(10)                  # advance 10 rising edges on that domain
dom.assert_reset()            # drive reset to its asserted level (no step)
dom.release_reset()           # drive reset to its released level (no step)
```

For multi-clock designs the planner creates one `Domain` per clock.  The
`MultiDomainRunner` keeps all domains in lock-step so the earliest-deadline
domain always advances first.

---

## `bench.iface(name)` — proxy types

`bench.iface(name)` returns a protocol-specific proxy.  The proxy type is
chosen by the planner from the DUT's port bundle:

| Port prefix | Proxy type | Role keyword |
|---|---|---|
| AXI4-Stream (`tvalid`, `tready`, `tdata`) | `AXIStreamProxy` | `"slave"` or `"master"` |
| AXI4-Lite (`awvalid`, `awaddr`, ...) | `AXILiteProxy` | `"slave"` or `"master"` |
| AXI4 (`awvalid`, `awid`, `awlen`, ...) | `AXI4Proxy` | `"slave"` or `"master"` |
| MemBus (`wen`/`we`, `wdata`, `rdata`, `addr`) | `MemBusProxy` | `"slave"` or `"master"` |

**Role convention**: `"slave"` means the *DUT* is the slave — the bench drives
write/read transactions *into* the DUT.  `"master"` means the *DUT* is the
master — the bench responds to DUT-initiated transactions.

Proxies are created **lazily** on the first `iface()` call.  Create proxies
**before** `bench.reset_all()` if the endpoint must observe the DUT during
the settle cycles (e.g. a MemBusResponder or AXILiteResponder acting as a
slave memory for a DUT master).

---

## `AXIStreamProxy`

```python
axis = bench.iface("s_axis")   # DUT is slave, bench sends frames
```

### Sending frames

```python
axis.put([0x11, 0x22, 0x33])                   # list of byte values
axis.put(b"\x11\x22\x33")                       # bytes / bytearray
axis.put([0xAB], dest=1, user=0xFF)             # with sideband signals
axis.put([0x01, 0x02], last_user=1)             # TUSER=1 only on last beat
```

### Building explicit frames

```python
frame = axis.frame([0x11, 0x22, 0x33], dest=2, tid=0)
axis.put_frame(frame)
```

### Draining (source side)

```python
axis.wait_drain(timeout=500)   # block until DUT has consumed all queued beats
```

### Receiving frames (sink side)

```python
axis_out = bench.iface("m_axis")   # DUT is master, bench receives
frame = axis_out.get(timeout=200)  # block until TLAST received
assert list(frame.data) == [0x11, 0x22, 0x33]
```

### Checking without blocking

```python
if axis_out.pending:                # True if at least one complete frame is ready
    frame = axis_out.get(timeout=1)
```

### Expecting an exact frame

```python
axis_out.expect([0x11, 0x22, 0x33], timeout=200)   # raises AssertionError on mismatch
```

### Backpressure

```python
from veriforge.sim.endpoints import PauseGenerator

axis_out.pause = PauseGenerator.duty(0.5)   # 50 % random backpressure
```

### Layout overrides

```python
from veriforge.sim.bench import PlannerOverrides
bench = Testbench(
    module,
    overrides=PlannerOverrides(iface_layouts={
        "s_axis": {"elements_per_beat": 4, "element_size_bits": 8, "endian": "big"},
    }),
)
```

---

## `AXILiteProxy`

```python
axi = bench.iface("s_axi")   # DUT is AXI-Lite slave, bench is master
```

### Register writes and reads

```python
axi.write(0x00, 0xDEADBEEF)           # write 32-bit value to offset 0
value = axi.read(0x04)                 # read back
axi.write_then_read(0x08, 0x1234)      # write then read-back (returns read value)
```

### Accessing the write log (DUT-master role)

When the planner detects that `m_axi` is a DUT-master port, `bench.iface("m_axi")`
returns a responder proxy automatically (no `role=` override needed):

```python
responder = bench.iface("m_axi")  # DUT is master → proxy is a responder
bench.domain("clk").step(20)
print(responder.write_log)    # list of (addr, data, strb) tuples captured
print(responder.read_log)     # list of addr values read by DUT
```

### Prepopulating the responder memory

```python
responder = bench.iface("m_axi")
responder.memory.update({0x00: 0xABCD, 0x04: 0x1234})
```

---

## `AXI4Proxy`

```python
axi4 = bench.iface("s_axi4")   # DUT is AXI4 slave
axi4.write(addr=0x100, data=b"\xDE\xAD\xBE\xEF")
data = axi4.read(addr=0x100, length=4)
```

For burst and ID-tagged transactions, build `AXI4Frame` objects directly or
use the `queue_write` / `queue_read` methods on the underlying `AXI4Responder`
(accessible as `axi4._responder`).

---

## `MemBusProxy`

```python
mem = bench.iface("bus")   # DUT is MemBus slave (has addr, wen, wdata, rdata)
mem.write(0x00, 0xCAFE)
val = mem.read(0x04)
```

The proxy auto-detects both `wen`/`we` naming variants and matching `rdata`/
`wdata` widths.

---

## `StreamProxy`

For raw handshake bundles (a `valid`/`ready` pair without AXI framing):

```python
stream = bench.iface("data_in")   # DUT is a plain stream slave
stream.put([0x11, 0x22])
stream.wait_drain()
```

---

## PauseGenerator — backpressure injection

`PauseGenerator` provides randomised backpressure on any source or sink proxy.
Setting it on a source gates `tvalid`; on a sink it gates `tready`.

```python
from veriforge.sim.endpoints import PauseGenerator

# Fractional duty-cycle (preferred)
gen = PauseGenerator.duty(0.5)            # ~50 % pause rate
gen = PauseGenerator.duty(0.3, seed=42)  # 30 %, reproducible

# Integer form: N paused cycles out of every D
gen = PauseGenerator(3, 10)              # 30 % pause rate

# Extremes
gen = PauseGenerator.always()            # full stall
gen = PauseGenerator.never()             # full throughput

axis_out.pause = gen
axis_in.pause = gen
```

The generator is sampled **once per clock cycle** in the pre-tick phase so its
internal counter always advances at the correct rate regardless of how many
tick phases run per cycle.

---

## Multi-clock domain

```python
bench = Testbench(module, engine="vm")
with bench.run():
    bench.reset_all()

    fast = bench.domain("fast_clk")  # 200 MHz domain
    slow = bench.domain("slow_clk")  # 50 MHz domain

    # Step 8 fast cycles and 2 slow cycles simultaneously
    fast.step(8)
    slow.step(2)
```

The `MultiDomainRunner` underneath interleaves all domains by wall-clock time.
`domain.step(N)` advances the *specific* domain N edges and the runner
time-multiplexes with all other domains automatically.

---

## PlannerOverrides — non-standard ports

When port names don't match the default heuristics, use `PlannerOverrides`:

```python
from veriforge.sim.bench import PlannerOverrides

overrides = PlannerOverrides(
    reset_polarities={"n_rst": "active_low"},   # "active_low" or "active_high"
    clock_periods={"sys_clk": 20},              # override inferred period
    domain_aliases={"sys_clk": "main"},         # rename the domain
    iface_layouts={
        "s_axis": {
            "elements_per_beat": 2,
            "element_size_bits": 16,
        },
    },
)
bench = Testbench(module, overrides=overrides)
```

All fields are optional mappings. `iface_domains` forces a specific interface
to a named clock domain; `relaxed_iface_signals` suppresses strict signal-set
checks for protocols with optional ports.

---

## Error handling

| Exception | When raised |
|---|---|
| `BenchTimeoutError` | Transaction did not complete within `timeout` cycles |
| `AXIStreamProtocolError` | TDATA/TKEEP changed while TVALID=1, TREADY=0 (strict mode) |
| `AXILiteProtocolError` | AWVALID/WVALID/ARVALID deasserted before READY, or address/data changed while unacknowledged (strict mode) |
| `PlanValidationError` | Planner could not uniquely identify a clock or reset port |
| `AmbiguousDomainError` | Multiple clock candidates found with no override |
| `NoDomainError` | No clock candidates found with no override |

---

## VCD waveform capture

Pass `vcd=` to `bench.run()` — VCD recording starts before the with-block body
and is finalized on exit:

```python
bench = Testbench(module, engine="vm")
with bench.run(vcd="dump.vcd"):              # all signals
    bench.reset_all()
    ...

with bench.run(vcd="dump.vcd", vcd_signals=["clk", "s_axis_tvalid"]):
    bench.reset_all()
    ...
```

---

## When to use `Testbench` vs `compile_native`

| Criterion | `Testbench` | `compile_native` |
|---|---|---|
| Arbitrary Python callbacks per beat | ✓ | ✗ |
| Runtime branching on DUT outputs | ✓ | ✗ |
| Maximum simulation speed | moderate | ✓ (pure C loop) |
| Fixed, known-at-compile-time stimulus | either | preferred |
| Protocol monitor (`strict=True`) | ✓ | ✗ |

See [bench_native_lowering.md](bench_native_lowering.md) for the lowering guide.

---

## See also

* `notes/simulation/endpoint_timing_model.md` — when callbacks fire relative to posedge
* `notes/simulation/bench_native_lowering.md` — engine-native lowering
* `notes/simulation/simulator_engines.md` — reference vs VM vs compiled engine trade-offs
* `src/veriforge/sim/bench/__init__.py` — exported names
* `tests/test_sim/test_bench_runtime.py` — integration tests with real DUTs
* `tests/test_dsl/test_testbench_bench_style.py` — idiomatic usage patterns
