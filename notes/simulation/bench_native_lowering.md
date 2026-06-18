# Engine-Native Bench Lowering

The engine-native bench lowering lets constrained testbench primitives run
**inside** the VM or compiled Verilog engine instead of stepping through
Python. The result is that AXI-Stream source/sink, AXI-Lite master/slave,
AXI4 slave, AXI4 master, and MemBus master/responder stimulus can participate
in a compiled `batch_run()` loop at compiled-C speeds rather than paying a
per-cycle Python overhead.

See also: [simulator_engines.md](simulator_engines.md) for engine choice
and `batch_run` guidance.

---

## When to use engine-native vs Python-stepped

| Criterion | Python `Testbench` | `compile_native` |
|---|---|---|
| Arbitrary Python callbacks per beat | ✓ | ✗ |
| Runtime branching on DUT outputs | ✓ | ✗ |
| Maximum performance (compiled engine) | limited by coroutine step | ✓ (pure C loop) |
| Fixed, known-at-compile-time stimulus | either works | preferred |
| Multi-clock domain coordination | ✓ | ✓ |
| Protocol: AXI-Stream source/sink | ✓ | ✓ |
| Protocol: AXI-Lite master (scripted) | ✓ | ✓ |
| Protocol: AXI-Lite slave (memory-backed) | ✓ | ✓ |
| Protocol: AXI4 slave (memory-backed) | ✓ | ✓ |
| Protocol: AXI4 master (scripted ops) | ✓ | ✓ |
| Protocol: MemBus master (scripted ops) | ✓ | ✓ |
| Protocol: MemBus slave/responder | ✓ | ✓ |

**Rule of thumb**: if your stimulus is a fixed list of beats or register
writes/reads decided before the simulation starts, use `compile_native`. If
the testbench needs to inspect live DUT signals to decide what to send next,
use the Python `Testbench` instead.

---

## Architecture overview

```
Python test code
      │
      │  Testbench(parsed_module)  ──► build_plan() / planner
      │
      ▼
compile_native(bench, lowerings={...})
      │
      │  emits a DSL wrapper module containing:
      │    ┌─────────────────────────────────────┐
      │    │  clk / rst inputs                    │
      │    │  FSM fragments (one per interface)   │
      │    │  DUT instance (u_dut)               │
      │    │  output ports for captured values   │
      │    └─────────────────────────────────────┘
      │
      ▼
LoweredDesign(wrapper, design, capture_signals, done_signals, plan)
      │
      ├──► lowered.run(engine, vcd=...)      # convenience: handles clocks/reset/VCD
      │
      ├──► lowered.batch_run(cycles=1000)   # fastest: C-only loop (compiled engine)
      │
      └──► Simulator(lowered.wrapper, design=lowered.design, engine="compiled")
             sim.fork(Clock(...))
             sim.run(max_time=...)
             # or: sim.batch_run(...)     # manual path for custom timing
```

The wrapper is a normal DSL-generated `Module`. It has clock and reset as
`input` ports, output regs for every captured value, and instantiates the
DUT as `u_dut`. Because every bit of bench logic lives in synthesisable
HDL, all three engines (reference, VM, compiled) can run it without
coroutine fallback.

---

## API reference

### `compile_native(bench, *, lowerings, name="bench_native_top")`

```python
from veriforge.sim.bench import compile_native, Testbench
from veriforge.sim.bench import (
    AXIStreamSourceLowering,
    AXIStreamSinkLowering,
    AXILiteMasterLowering,
    AXILiteOp,
    AXILiteSlaveLowering,
    AXI4SlaveLowering,
    AXI4MasterLowering,
    AXI4MasterOp,
    MemBusMasterLowering,
    MemBusOp,
    MemBusResponderLowering,
)
```

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `bench` | `Testbench` | Constructed Testbench; only its `plan` and `module` are used. |
| `lowerings` | `dict[str, InterfaceLowering]` | Maps interface *prefix* to a lowering. Every interface in the plan must have an entry. |
| `name` | `str` | Name of the emitted wrapper module. |

**Returns** `LoweredDesign`:

| Attribute | Type | Description |
|-----------|------|-------------|
| `wrapper` | `Module` | DSL-built wrapper module (pass to `Simulator`). |
| `design` | `Design` | `Design(modules=[wrapper, dut])` for hierarchy resolution. |
| `capture_signals` | `dict[str, list[str]]` | Per-prefix list of output port names holding captured values. |
| `done_signals` | `dict[str, str]` | Per-prefix `done` output port name for sink/master lowerings. |
| `plan` | `TestbenchPlan` | The planner output; consumed by `lowered.run()` for clock/reset config. |

**Raises** `LoweringError` on: unknown prefix, missing lowering, role
mismatch, empty stimulus, bad `memory_depth`.

---

### `LoweredDesign.run(engine, *, max_time, vcd, vcd_timescale, vcd_signals)`

Convenience one-call entry point that handles clock scheduling, reset
sequencing, optional VCD attachment, and the simulation run.

```python
results: dict[str, int] = lowered.run(
    engine="compiled",      # "reference" | "vm" | "compiled"
    max_time=10_000,        # optional; defaults to 1000 * min_clock_period
    vcd="trace.vcd",        # optional VCD output path (str or Path)
    vcd_timescale="1ns",    # VCD $timescale directive (default "1ns")
    vcd_signals=None,       # None = record all; or list of signal name strings
)
```

**Returns** `dict[str, int]` mapping every name in `capture_signals` to its
integer value after the simulation completes.

**Behavior:**

1. Creates `Simulator(wrapper, design=design, engine=engine)`.
2. Calls `sim.fork(Clock(...))` for each domain clock from the plan.
3. Asserts all resets (using the polarity in the plan's `ResetSpec`).
4. Calls `sim.run(max_time=min_period * 4)` to sequence reset.
5. Releases all resets.
6. If `vcd` is set, calls `attach_vcd(sim, vcd, ...)` to start recording.
7. Calls `sim.run(max_time=max_time)`.
8. Closes the VCD session (even if `sim.run` raises).
9. Returns `{name: int(sim.signal(name).value) for name in all capture signals}`.

For custom timing control (e.g., `batch_run`, interleaved stimulus), use
the manual `Simulator` path shown in the examples below.

---

### `LoweredDesign.batch_run(cycles, *, clock_name, clock_period, reset_cycles)`

Fastest execution path for data-driven lowered designs on the compiled
engine. All clock toggling and reset sequencing happen inside a single C
`nogil` loop with no per-cycle Python overhead.

```python
results: dict[str, int] = lowered.batch_run(
    cycles=1000,         # total clock cycles to run (> reset_cycles)
    clock_name=None,     # auto-detected from single-domain plans
    clock_period=None,   # auto-detected from plan period_hint (default: 10)
    reset_cycles=4,      # cycles to hold reset before releasing
)
```

**Parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `cycles` | `int` | `1000` | Total clock cycles. Must be > `reset_cycles`. |
| `clock_name` | `str \| None` | auto | Clock signal to toggle. Required for multi-domain designs. |
| `clock_period` | `int \| None` | auto | Clock period (time units). Falls back to `period_hint` or 10. |
| `reset_cycles` | `int` | `4` | Cycles to keep reset asserted before releasing. |

**Behavior:**

1. Resolves `clock_name` / `clock_period` from the plan (or raises `ValueError`
   for multi-domain designs without an explicit `clock_name`).
2. Builds batch `events`: asserts each domain's reset at cycle 0 and releases
   it at `reset_cycles`.
3. Creates `Simulator(wrapper, design=design, engine="compiled")`.
4. Calls `sim.batch_run(cycles, clock_name, clock_period, events=events)`.
5. Returns `{name: int(sim.signal(name).value) for name in all capture signals}`.

**Limitations:**

* Compiled engine only — always uses `engine="compiled"`.
* VCD tracing is not available (the C loop does not invoke Python callbacks).
  Use `run()` if you need a waveform.
* Only one clock is driven; multi-domain designs must provide `clock_name`
  and the secondary-domain logic must be clocked by the same signal or accept
  driven-zero clocks.

**Raises** `ValueError` on `reset_cycles >= cycles` or missing `clock_name`
for a multi-domain plan.

---

### `AXIStreamSourceLowering`

Drives a stream of beats into a DUT AXI-Stream **slave** port. Two data modes — exactly one must be active:

**ROM mode** (default): supply a non-empty `beats` list. `tdata` is served from a registered ROM keyed by a beat counter.

**PRNG mode**: set `n_prng_beats` > 0. `tdata` is driven by a 32-bit Galois LFSR. Pair with `AXIStreamSinkLowering(data_prng_seed=...)` using the same seed for end-to-end data integrity testing at scale.

Both modes support an optional LFSR-driven pause generator (`prng_bits` / `pause_threshold`) that randomly de-asserts `tvalid` to stress back-pressure handling.

```python
# ROM mode
AXIStreamSourceLowering(
    beats=[0xA1, 0xB2, 0xC3, 0xD4],
    data_width=8,
)

# PRNG mode with ~50% random pause rate
AXIStreamSourceLowering(
    n_prng_beats=10_000,
    data_prng_seed=0xACE1,
    data_width=8,
    prng_bits=4,
    pause_threshold=8,   # pause when lfsr[3:0] < 8
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `beats` | `Sequence[int]` | `()` | Beat data values (ROM mode). Non-empty when `n_prng_beats == 0`. |
| `n_prng_beats` | `int` | `0` | Beats in PRNG mode. Set > 0 to enable; `beats` must be empty. |
| `data_prng_seed` | `int` | `0xACE1` | LFSR seed for PRNG data. `0` is treated as `0xACE1`. |
| `data_width` | `int` | `8` | Width of `tdata` in bits. |
| `prng_bits` | `int` | `0` | Low-order LFSR bits used for pause. `0` disables pausing (`tvalid` always asserted when `cnt < N`). |
| `pause_threshold` | `int` | `0` | Pause when `lfsr[prng_bits-1:0] < pause_threshold`. Range `[0, 2**prng_bits]`. |
| `prng_seed` | `int` | `0xACE1` | LFSR seed for the pause generator. |

**DUT port role**: DUT is a `slave` (it accepts data from the bench source).

---

### `AXIStreamSinkLowering`

Receives beats from a DUT AXI-Stream **master** port. Two data modes:

**Capture mode** (default): stores each accepted beat into a separate `<prefix>_cap_<i>` output reg. Suitable for small frame counts.

**PRNG check mode**: set `data_prng_seed` to match the source's `data_prng_seed`. A shadow LFSR advances per beat and compares `tdata` against the expected value. No per-beat capture regs — scales to millions of beats. Read `<prefix>_snk_err_cnt` / `<prefix>_snk_err_flag` after the run.

Both modes support an LFSR-driven back-pressure generator (`prng_bits` / `pause_threshold`) that randomly de-asserts `tready`.

```python
# Capture mode
AXIStreamSinkLowering(n_beats=4, data_width=8)

# PRNG check mode (end-to-end integrity, no per-beat storage)
AXIStreamSinkLowering(
    n_beats=10_000,
    data_prng_seed=0xACE1,   # must match AXIStreamSourceLowering
    data_width=8,
    prng_bits=4,
    pause_threshold=8,        # ~50% random back-pressure on tready
)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `n_beats` | `int` | — | Number of beats to capture / check (required). |
| `data_width` | `int` | `8` | Width of `tdata` in bits. |
| `data_prng_seed` | `int \| None` | `None` | Enables PRNG check mode when not `None`. Must match source seed. |
| `prng_bits` | `int` | `0` | Low-order LFSR bits for back-pressure. `0` = `tready` always high. |
| `pause_threshold` | `int` | `0` | Assert back-pressure when `lfsr[prng_bits-1:0] < pause_threshold`. |
| `prng_seed` | `int` | `0xACE1` | LFSR seed for the back-pressure generator. |

**Capture output ports — capture mode:**

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_cap_<i>` | `data_width` | Beat *i* data (`i` = 0 .. n_beats-1) |
| `<prefix>_snk_done` | 1 | High once all *n_beats* captured |

**Capture output ports — PRNG check mode:**

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_snk_err_cnt` | 32 | Count of beats where `tdata` ≠ expected LFSR value |
| `<prefix>_snk_err_flag` | 1 | 1 if any beat mismatched |
| `<prefix>_snk_done` | 1 | High once all *n_beats* checked |

**DUT port role**: DUT is a `master` (it produces data into the bench sink).

---

### `AXILiteMasterLowering`

Drives a scripted sequence of AXI-Lite write and read operations against
a DUT AXI-Lite **slave** port. Operations are ROM-encoded and replayed by
an FSM. The FSM drives AW+W channels in parallel for writes and AR for
reads, waits for B/R responses, and advances to the next operation.

```python
AXILiteMasterLowering(
    operations=[
        AXILiteOp.write(addr=0x00, data=0xDEADBEEF),
        AXILiteOp.write(addr=0x04, data=0xCAFEBABE, strb=0x3),  # low 2 bytes only
        AXILiteOp.read(addr=0x00),
        AXILiteOp.read(addr=0x04),
    ],
    addr_width=32,   # default
    data_width=32,   # default
)
```

**`AXILiteOp` factories:**

```python
AXILiteOp.write(addr, data, *, strb=None)  # strb defaults to all-1 (all bytes)
AXILiteOp.read(addr)
```

**Capture output ports** (accessible after run, index `i` = op number):

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_op_<i>_resp` | 2 | B response (for writes) or R response (for reads) |
| `<prefix>_op_<i>_rdata` | `data_width` | Read data for read ops; 0 for write ops |
| `<prefix>_master_done` | 1 | High once the last operation completes |

**DUT port role**: DUT is a `slave` (the bench drives as master).

---

### `AXILiteSlaveLowering`

Acts as an AXI-Lite memory-backed **slave** responder for a DUT AXI-Lite
**master** port. Accepts single-beat write and read transactions, applies
WSTRB byte-merge writes, and always responds OKAY. Each memory cell is
exposed as a wrapper output port for inspection after the simulation.

```python
AXILiteSlaveLowering(
    memory_depth=16,            # number of words (each word = data_width bits)
    data_width=32,              # default
    addr_width=32,              # default
    initial_memory={            # optional: pre-seed specific words
        0: 0xAAAA0000,
        1: 0xAAAA0001,
    },
)
```

**Word addressing**: the byte address is right-shifted by `log2(data_width/8)`.
For `data_width=32`, address `0x04` → word index 1.

**Transaction priority**: when AW and AR arrive in the same cycle, AW is
accepted first (AR is blocked until AW is processed).

**Capture output ports**:

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_slv_mem_<i>` | `data_width` | Memory word at index *i* after simulation |
| `<prefix>_slv_aw_count` | 16 | Number of AW transactions accepted |
| `<prefix>_slv_ar_count` | 16 | Number of AR transactions accepted |

**DUT port role**: DUT is a `master` (the bench acts as a slave responder).

**Limitations:**
- Single-outstanding transactions only (no interleaving).
- No exclusive access.

---

### `AXI4SlaveLowering`

Acts as an AXI4 memory-backed **slave** responder for a DUT AXI4 **master**
port. Supports INCR bursts of arbitrary length, WSTRB byte-merge writes,
and always responds OKAY (`2'b00`). Each memory cell is exposed as a wrapper
output port for easy inspection after the simulation.

```python
AXI4SlaveLowering(
    memory_depth=16,            # number of words (each word = data_width bits)
    data_width=32,              # default
    addr_width=32,              # default
    id_width=0,                 # set to DUT's actual ID width if present
    initial_memory={            # optional: pre-seed specific words
        0: 0xAAAA0000,
        1: 0xAAAA0001,
    },
)
```

**Word addressing**: the byte address is right-shifted by `log2(data_width/8)`.
For `data_width=32`, address `0x10` → word index 4.

**Capture output ports** (one per word):

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_slv_mem_<i>` | `data_width` | Memory word at index *i* after simulation |
| `<prefix>_slv_aw_count` | 8 | Number of AW transactions accepted |
| `<prefix>_slv_w_count` | 8 | Number of W beats accepted |
| `<prefix>_slv_ar_count` | 8 | Number of AR transactions accepted |

**DUT port role**: DUT is a `master` (the bench acts as a slave responder).

**Limitations:**
- Single outstanding transaction (AW handshake blocks AR until B completes).
- FIXED bursts still increment address per beat (use INCR only).
- WRAP bursts not modeled.
- No exclusive access.

---

### `AXI4MasterLowering`

Drives a scripted sequence of single-beat AXI4 write and read operations against a DUT AXI4 **slave** port. Mirrors `AXILiteMasterLowering` but uses full AXI4 signaling (`awlen=0`, `wlast=1`, `awburst/arburst=INCR`). ID signals are driven as zero.

```python
AXI4MasterLowering(
    operations=[
        AXI4MasterOp.write(addr=0x00, data=0x12345678),
        AXI4MasterOp.write(addr=0x04, data=0xABCDABCD, strb=0x3),  # low 2 bytes only
        AXI4MasterOp.read(addr=0x00),
        AXI4MasterOp.read(addr=0x04),
    ],
    addr_width=32,  # default
    data_width=32,  # default
    id_width=0,     # set to DUT's ID width if present; bench drives 0 on all IDs
)
```

**`AXI4MasterOp` factories:**

```python
AXI4MasterOp.write(addr, data, *, strb=None)  # strb defaults to all-1
AXI4MasterOp.read(addr)
```

**Capture output ports** (index `i` = op number):

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_op_<i>_resp` | 2 | B response (writes) or R response (reads) |
| `<prefix>_op_<i>_rdata` | `data_width` | Read data for read ops; 0 for write ops |
| `<prefix>_master_done` | 1 | High once the last operation completes |

**DUT port role**: DUT is a `slave` (the bench drives as AXI4 master).

---

### `MemBusMasterLowering`

Drives a scripted sequence of synchronous memory-bus (MemBus) write and read operations against a DUT **slave** port. Writes complete in one cycle; reads take two cycles (request cycle then data-capture cycle).

```python
MemBusMasterLowering(
    operations=[
        MemBusOp.write(addr=0x00, data=0xDEADBEEF),
        MemBusOp.write(addr=0x04, data=0xCAFEBABE, be=0x3),  # low 2 bytes only
        MemBusOp.read(addr=0x00),
        MemBusOp.read(addr=0x04),
    ],
    addr_width=32,   # default
    data_width=32,   # default
    has_ren=False,   # True if DUT has a separate ren port
    be_width=0,      # set to data_width//8 if DUT has byte-enable port
)
```

**`MemBusOp` factories:**

```python
MemBusOp.write(addr, data, *, be=None)  # be=None → all bytes enabled
MemBusOp.read(addr)
```

**Capture output ports** (index `i` = op number):

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_op_<i>_rdata` | `data_width` | Captured read data; 0 for write ops |
| `<prefix>_master_done` | 1 | High once the last operation completes |

**DUT port role**: DUT is a `slave` (the bench drives as MemBus master).

**Note:** MemBus uses word addresses directly (unlike AXI byte addresses). Address `0x01` means word index 1.

---

### `MemBusResponderLowering`

Acts as a synchronous memory-backed MemBus **slave** responder for a DUT **master** port. Drives `rdata` from backing memory combinatorially; optionally pulses `rvalid` for one cycle after a read. On writes, applies optional byte-enable masking.

```python
MemBusResponderLowering(
    memory_depth=16,        # number of words (each word = data_width bits)
    data_width=32,          # default
    addr_width=32,          # default
    has_be=False,           # True if DUT drives a byte-enable port
    has_ren=False,          # True if DUT drives a separate ren port
    has_rvalid=False,       # True if DUT expects an rvalid input
    initial_memory={        # optional: pre-seed specific words
        0: 0xAAAA0000,
        1: 0xAAAA0001,
    },
)
```

**Word addressing**: MemBus uses word addresses directly. Address `0x01` means word index 1.

**Capture output ports**:

| Port | Width | Description |
|------|-------|-------------|
| `<prefix>_rsp_mem_<i>` | `data_width` | Memory word at index *i* after simulation |
| `<prefix>_rsp_wr_count` | 16 | Number of write transactions accepted |
| `<prefix>_rsp_rd_count` | 16 | Number of read transactions accepted |

**DUT port role**: DUT is a `master` (the bench acts as MemBus slave/responder).

---

## Complete examples

### Example 1 — AXI-Stream loopback (source → DUT → sink)

#### Preferred: one-call `lowered.run()`

```python
from veriforge.project import parse_file
from veriforge.sim.bench import Testbench, compile_native
from veriforge.sim.bench import AXIStreamSourceLowering, AXIStreamSinkLowering

design = parse_file("rtl/my_axis_dut.sv")
dut = design.get_module("my_axis_dut")

bench = Testbench(dut)
lowered = compile_native(
    bench,
    lowerings={
        "s_axis": AXIStreamSourceLowering(beats=[0x01, 0x02, 0x03, 0x04], data_width=8),
        "m_axis": AXIStreamSinkLowering(n_beats=4, data_width=8),
    },
)

# Clock scheduling, reset sequencing, VCD attachment, and run are all automatic.
results = lowered.run("compiled", vcd="trace.vcd")

for i in range(4):
    print(f"beat {i}: {results[f'm_axis_cap_{i}']:#04x}")
print("done:", results.get("m_axis_snk_done"))
```

#### Fastest: `lowered.batch_run()` (compiled engine, no Python overhead)

```python
# Single call — entire sim including reset in C. No VCD.
results = lowered.batch_run(cycles=1000, reset_cycles=4)

for i in range(4):
    print(f"beat {i}: {results[f'm_axis_cap_{i}']:#04x}")
print("done:", results.get("m_axis_snk_done"))
```

#### Manual path (full control / VCD / custom timing)

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock
from veriforge.sim.bench import Testbench, compile_native
from veriforge.sim.bench import AXIStreamSourceLowering, AXIStreamSinkLowering

design = parse_file("rtl/my_axis_dut.sv")
dut = design.get_module("my_axis_dut")

bench = Testbench(dut)
lowered = compile_native(
    bench,
    lowerings={
        "s_axis": AXIStreamSourceLowering(beats=[0x01, 0x02, 0x03, 0x04], data_width=8),
        "m_axis": AXIStreamSinkLowering(n_beats=4, data_width=8),
    },
)

sim = Simulator(lowered.wrapper, design=lowered.design, engine="compiled")
clk = sim.signal("clk")
rst_n = sim.signal("rst_n")

rst_n.value = 1
sim.fork(Clock(clk, period=10))
sim.run(max_time=40)     # reset period
rst_n.value = 1

sim.run(max_time=10 * 50)  # run enough cycles for all beats to flow

# All 4 beats captured
for i in range(4):
    print(f"beat {i}: {int(sim.signal(f'm_axis_cap_{i}').value):#04x}")

print("done:", int(sim.signal("m_axis_snk_done").value))
```

---

### Example 2 — AXI-Lite master: write then read registers

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock
from veriforge.sim.bench import Testbench, compile_native
from veriforge.sim.bench import AXILiteMasterLowering, AXILiteOp

design = parse_file("rtl/my_regs.sv")
dut = design.get_module("my_regs")

bench = Testbench(dut)
lowered = compile_native(
    bench,
    lowerings={
        "s_axil": AXILiteMasterLowering(
            operations=[
                AXILiteOp.write(addr=0x00, data=0x12345678),
                AXILiteOp.write(addr=0x04, data=0xABCDABCD),
                AXILiteOp.read(addr=0x00),
                AXILiteOp.read(addr=0x04),
            ],
        ),
    },
)

sim = Simulator(lowered.wrapper, design=lowered.design, engine="vm")
clk = sim.signal("clk")
rst_n = sim.signal("rst_n")

rst_n.value = 0
sim.fork(Clock(clk, period=10))
sim.run(max_time=40)
rst_n.value = 1
sim.run(max_time=10 * 40)

assert int(sim.signal("s_axil_master_done").value) == 1
# op 0 and op 1 are writes (resp should be OKAY = 0)
assert int(sim.signal("s_axil_op_0_resp").value) == 0
assert int(sim.signal("s_axil_op_1_resp").value) == 0
# op 2 and op 3 are reads
print(f"reg[0] = {int(sim.signal('s_axil_op_2_rdata').value):#010x}")
print(f"reg[1] = {int(sim.signal('s_axil_op_3_rdata').value):#010x}")
```

---

### Example 3 — AXI4 slave: bench absorbs a DUT master burst

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock
from veriforge.sim.bench import Testbench, compile_native
from veriforge.sim.bench import AXI4SlaveLowering

design = parse_file("rtl/dma_master.sv")
dut = design.get_module("dma_master")

bench = Testbench(dut)
lowered = compile_native(
    bench,
    lowerings={
        "m_axi": AXI4SlaveLowering(
            memory_depth=64,
            data_width=32,
            addr_width=32,
            initial_memory={0: 0xDEAD0000},   # word 0 pre-loaded
        ),
    },
)

sim = Simulator(lowered.wrapper, design=lowered.design, engine="compiled")
clk = sim.signal("clk")
rst_n = sim.signal("rst_n")

rst_n.value = 0
sim.fork(Clock(clk, period=10))
sim.run(max_time=40)
rst_n.value = 1
sim.run(max_time=10 * 200)

# Inspect what the DUT wrote into memory
for i in range(4):
    word = int(sim.signal(f"m_axi_slv_mem_{i}").value)
    print(f"mem[{i}] = {word:#010x}")

# Transaction counters
print("AW txns:", int(sim.signal("m_axi_slv_aw_count").value))
print("W  beats:", int(sim.signal("m_axi_slv_w_count").value))
```

---

### Example 4 — AXI-Lite slave: bench responds to a DUT master

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock
from veriforge.sim.bench import Testbench, compile_native
from veriforge.sim.bench import AXILiteSlaveLowering

design = parse_file("rtl/cfg_master.sv")
dut = design.get_module("cfg_master")

bench = Testbench(dut)
lowered = compile_native(
    bench,
    lowerings={
        "m_axil": AXILiteSlaveLowering(
            memory_depth=16,
            data_width=32,
            addr_width=32,
            initial_memory={0: 0xDEAD0000},   # word 0 pre-loaded
        ),
    },
)

sim = Simulator(lowered.wrapper, design=lowered.design, engine="compiled")
clk = sim.signal("clk")
rst_n = sim.signal("rst_n")

rst_n.value = 0
sim.fork(Clock(clk, period=10))
sim.run(max_time=40)
rst_n.value = 1
sim.run(max_time=10 * 100)

# Inspect words written by the DUT
for i in range(4):
    word = int(sim.signal(f"m_axil_slv_mem_{i}").value)
    print(f"mem[{i}] = {word:#010x}")

# Transaction counters
print("AW txns:", int(sim.signal("m_axil_slv_aw_count").value))
print("AR txns:", int(sim.signal("m_axil_slv_ar_count").value))
```

---

### Example 5 — Partial write strobe (AXI-Lite)

```python
# Write only the lower 2 bytes of a 32-bit register, then read back.
ops = [
    AXILiteOp.write(addr=0x08, data=0x0000_CAFE, strb=0b0011),  # bytes 0,1 only
    AXILiteOp.read(addr=0x08),
]
lowered = compile_native(bench, lowerings={"s_axil": AXILiteMasterLowering(operations=ops)})
```

---

## Accessing captured data

All capture output ports are **wrapper** top-level ports, not DUT ports.
After the simulation completes, read them with `sim.signal(name)`:

```python
# Capture signals index (available on LoweredDesign)
print(lowered.capture_signals)
# {"s_axis": ["s_axis_cap_0", "s_axis_cap_1", ...], ...}

print(lowered.done_signals)
# {"s_axis": "s_axis_snk_done"}

# Read via Simulator after sim.run(...)
val = int(sim.signal(lowered.capture_signals["s_axis"][0]).value)
done = int(sim.signal(lowered.done_signals["s_axis"]).value)
```

For DUT-internal signals (e.g. a `done` reg inside the DUT itself), use the
`u_dut.` hierarchy prefix:

```python
dut_done = int(sim.signal("u_dut.done").value)
```

---

## Signal naming conventions

| Signal | Naming |
|--------|--------|
| Source counter | `<prefix>_src_cnt` |
| Source valid/data/last/ready wires | `<prefix>_src_tvalid`, etc. |
| Sink counter | `<prefix>_snk_cnt` |
| Sink done flag | `<prefix>_snk_done` |
| Sink capture regs (capture mode) | `<prefix>_cap_<i>` |
| Sink error count (PRNG mode) | `<prefix>_snk_err_cnt` |
| Sink error flag (PRNG mode) | `<prefix>_snk_err_flag` |
| AXI-Lite / AXI4 master op response | `<prefix>_op_<i>_resp` |
| AXI-Lite / AXI4 master op read data | `<prefix>_op_<i>_rdata` |
| AXI-Lite / AXI4 / MemBus master done | `<prefix>_master_done` |
| AXI-Lite slave memory cells | `<prefix>_slv_mem_<i>` |
| AXI-Lite slave transaction counters | `<prefix>_slv_aw_count`, `_slv_ar_count` |
| AXI4 slave memory cells | `<prefix>_slv_mem_<i>` |
| AXI4 slave transaction counters | `<prefix>_slv_aw_count`, `_slv_w_count`, `_slv_ar_count` |
| MemBus master read data capture | `<prefix>_op_<i>_rdata` |
| MemBus responder memory cells | `<prefix>_rsp_mem_<i>` |
| MemBus responder transaction counters | `<prefix>_rsp_wr_count`, `_rsp_rd_count` |
| DUT instance | always `u_dut` |

---

## Performance

Because all bench logic is synthesisable HDL, the compiled engine's
C delta loop runs it without any Python coroutine overhead:

| Mode | Per-cycle cost | Example: 10K cycles |
|------|---------------|---------------------|
| Python `Testbench` + compiled engine | ~200µs/resume × timing steps | ~20–200s |
| `compile_native` + compiled engine | ~1µs C delta step | ~0.01s |

The crossover point is roughly when you need more than a few dozen
Python-side decisions per simulation run. For fixed-stimulus regression
tests, `compile_native` is almost always faster.

---

## Choosing `memory_depth` for `AXI4SlaveLowering`

Each word in the slave memory becomes an `output_reg` port in the wrapper
module. The compiled engine's Cython code-generator must compile this
module — larger depth means a bigger generated `.pyx` file and a longer
first-compile time.

| `memory_depth` | Ports added | First-compile on Cython |
|-----------------|-------------|--------------------------|
| ≤ 16 | ≤ 16 | < 30s |
| ≤ 64 | ≤ 64 | ~60–90s |
| > 128 | > 128 | > 120s (may time out) |

For large memories where you only need to inspect a few locations,
read `u_dut.<signal_name>` directly instead of relying on the wrapper
output ports, or use `engine="vm"` (no compilation step).

---

## Extending with a custom lowering

Implement the `InterfaceLowering` protocol:

```python
from dataclasses import dataclass
from veriforge.sim.bench.lowering import InterfaceLowering, LoweringError
from veriforge.dsl import Module as DSLModule

@dataclass
class MyProtocolLowering:
    protocol: str = "my_protocol"
    role: str = "slave"   # DUT-side role this lowering pairs with

    def apply(self, wrapper: DSLModule, *, binding, domain, clk, rst, port_map):
        # emit DSL wires/regs/always blocks into `wrapper`
        # populate port_map[dut_port_name] = wrapper_signal for every
        # signal in binding.signals
        ...
```

Pass an instance as a value in the `lowerings` dict to `compile_native`.
`LoweringError` is the correct exception type for subset violations.
