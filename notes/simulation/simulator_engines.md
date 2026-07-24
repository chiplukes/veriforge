# Simulator Engines

## Overview

The project has 4 simulation engines that all implement the same IEEE 1364-2005
scheduling semantics (Active → NBA → Delta-cycle convergence). They share the
same `Simulator` API and all consume the same model AST from elaboration.

```python
sim = Simulator(module, engine="reference")  # tree-walking, ground truth
sim = Simulator(module, engine="vm")         # bytecode VM, pure-Python interpreter
sim = Simulator(module, engine="vm-fast")    # bytecode VM, Cython interpreter (falls back to "vm" if unavailable)
sim = Simulator(module, engine="compiled")   # design-specific Cython codegen
```

See also: [simulator_python.md](simulator_python.md),
[simulator_bytecode_vm.md](simulator_bytecode_vm.md),
[simulator_compile_cython.md](simulator_compile_cython.md),
[endpoint_timing_model.md](endpoint_timing_model.md) — **critical reading
for anyone writing custom endpoint or testbench logic.**,
[cosim.md](cosim.md) — cross-simulator validation against Icarus Verilog.

## Engine Comparison

| | Reference | VM | VM-Fast | Compiled |
|---|-----------|------|---------|----------|
| **Strategy** | Tree-walking interpreter | Bytecode compiler + interpreter | Bytecode compiler + Cython interpreter | Design-specific Cython codegen |
| **Signal storage** | `dict[str, Value]` | `list[int]` (val/mask/width arrays) | `list[int]` (val/mask/width arrays) | C struct with `long long` arrays |
| **Delta loop** | Python iteration | Pure-Python loop | Cython C loop | Inlined C delta loop |
| **Performance** | ~16K cycles/s | ~3-5× faster than reference | ~5-10× faster than reference | ~50-100× faster than reference |
| **Timing controls** | Native coroutines | Fallback to reference executor | Fallback to reference executor | Fallback to reference executor |
| **Completeness** | Ground truth — most complete | All constructs via fallback | All constructs via fallback | All constructs via fallback |
| **Compilation step** | None | AST → bytecode (fast) | AST → bytecode (fast) | AST → .pyx → .pyd/.so (slow first time, cached) |

## The VM Engine Split: `"vm"` vs `"vm-fast"`

The bytecode VM has two interpreter backends controlled by `engine=`:

- **`"vm"`** — always uses the pure-Python `Interpreter`. No compiled extension
  required. Useful as a baseline and for testing the Python interpreter path in
  isolation.
- **`"vm-fast"`** — uses the Cython `_interp_fast` extension when available,
  silently falling back to pure-Python if the extension is not built. This is
  the right choice for production use where performance matters.

Internally, `VMScheduler` exposes `force_python=True/False` to control this.
The env var `VERIFORGE_DISABLE_CYTHON_VM=1` forces the pure-Python path
regardless of which engine name is used.

## File Layout

```
src/veriforge/sim/
├── scheduler.py            # Reference engine scheduler
├── evaluator.py            # Reference expression evaluator
├── executor.py             # Reference statement executor
├── value.py                # 4-state Value type (shared by all engines)
├── elaborate.py            # Hierarchy flattening (shared by all engines)
├── testbench.py            # Simulator API (shared entry point, engine selection)
├── event_queue.py          # Shared primitives: TimedEvent, EventQueueMixin, CoroutineMixin, SignalDictBase
├── vm/
│   ├── opcodes.py          # 83-opcode instruction set
│   ├── compiler.py         # AST → bytecode compiler
│   ├── interpreter.py      # Stack-based bytecode interpreter
│   ├── vm_scheduler.py     # VM event-driven scheduler
│   └── _interp_fast.pyx    # Cython fast interpreter + C delta loop
├── compiled/
│   ├── codegen.py           # AST → design-specific .pyx code generation
│   ├── compiler.py          # .pyx → .pyd/.so compilation + caching
│   ├── compiled_scheduler.py # Compiled engine scheduler
│   └── _*.py               # Internal: expr/stmt emitters, codegen helpers, wide-signal path
└── bench/
    ├── lowering.py          # All InterfaceLowering classes + compile_native()
    ├── plan.py              # BenchPlan: multi-domain testbench plan (clock domains, interfaces)
    ├── planner.py           # Auto-detect interfaces and build BenchPlan from DSL wrapper
    ├── runtime.py           # Testbench / LoweredDesign runtime wrappers
    └── interfaces.py        # Interface role/binding detection helpers
```

## Architecture

### Shared Infrastructure (event_queue.py)

All 3 engines share primitives from `event_queue.py`:
- **`TimedEvent`** — Priority queue entry `(time, seq, payload)`
- **`EventQueueMixin`** — `heapq`-based event scheduling (`_schedule_event`, `_pop_events_at`)
- **`CoroutineMixin`** — Coroutine lifecycle for timing-control fallback:
  `_run_initial_coro()`, `_resume_initial_coro()`, `_start_always_coro()`, `_resume_always_coro()`.
  Each engine implements 3 hooks: `_coro_sync_in()`, `_coro_sync_out()`, `_coro_post_resume()`.
- **`SignalDictBase`** — Dict-like wrapper over engine signal storage.
  Each engine implements 3 hooks: `_sig_map()`, `_read_sid()`, `_write_sid()`.
- **Public API** — `drive_signal()`, `read_signal()`, `signal_names()`, `schedule_at()`
- **Bootstrap sequence** — initial blocks → continuous assigns → combinational always → delta settle

### Engine-Specific Hooks

Each engine implements:
- **`_run_delta_loop()`** — One delta cycle: continuous assigns + edge-triggered sequential logic
- **`_raw_read(sid)`** / **`_raw_write(sid, val, mask)`** — Signal storage access
- **`_run_continuous_assigns()`** — Evaluate all continuous assign processes

### Timing Control Fallback

VM and compiled engines cannot natively handle `#delay` or `@(edge)` controls.
When a process contains timing, the engine falls back to the reference
`StatementExecutor.execute_coroutine()`:

1. Sync internal signal state → reference `EvalContext`  — O(n_signals) copy
2. Resume the coroutine (runs under reference executor)
3. Sync reference `EvalContext` → internal signal state  — O(n_signals) copy
4. Schedule next resume based on timing control

**Performance cost:** each coroutine resume costs ~200 µs (two full O(n_signals)
signal syncs + Python overhead). For a clock generator (`always #5 clk = ~clk`)
this caps the compiled engine at ~reference-engine speed for that process and
adds O(n_signals) work per edge. **Rule of thumb: keep clock generation in the
testbench `Clock` helper / `batch_run()`, not in DUT `initial`/`always` blocks
— or expect ~10–100× slowdown for the affected processes.**

Initial blocks with system tasks (`$display`, `$readmemh`, etc.) also route
through the reference executor fallback, incurring the same sync cost on every
elaboration call.

The compiled engine emits a `UserWarning` during `Simulator` construction for
each process that will fall back, naming the process index, the fallback reason,
and a cost estimate.

## Key Design Decisions

### Mask Propagation in Compiled Engine

The compiled engine's continuous assign code generation tracks per-expression
x/z masks via `_emit_mask_expr()`. For ternary expressions (`cond ? a : b`),
only the selected branch's mask propagates — preventing false X from unselected
branches with uninitialized signals.

### Bootstrap Guard

Initial blocks and always-with-timing scheduling run only once (guarded by
`_bootstrapped` flag). Continuous assign re-evaluation and combinational always
blocks run on every `run()` call to propagate external `drive()` changes.

## Test Coverage

### Testing Strategy

Every engine is tested independently — the test suite does not rely on the
auto-selection logic to exercise both VM backends. Key principle: using the
package chooses the fastest available engine, but testing must verify that
each engine works correctly on its own.

The cross-validation file (`test_validation/test_vm_vs_reference.py`) always
runs `"vm"` and `"vm-fast"` separately and compares each against `"reference"`.
`"compiled"` is compared when the Cython toolchain is available.

### Parametrize Convention

Tests that are meaningful across multiple engines use:
```python
@pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast"])
# or for compiled-engine tests:
ENGINES = ["reference", "vm", "vm-fast", "compiled"]
```

Tests that target VM internals specifically (e.g. `TestArithmeticOps`,
`TestXZPropagation`) are parametrized over `["vm", "vm-fast"]` only.

### Coverage by File

Cross-engine tests (parametrized across reference + vm + vm-fast, + compiled when available):
- `test_darkriscv_constructs.py` — DarkRISCV-style patterns
- `test_structural_patterns.py` — 21 structural pattern classes
- `test_vm_vs_reference.py` — VCD-level cross-validation, all engines

VM-specific unit tests (parametrized over vm + vm-fast):
- `test_vm.py` — bytecode compiler, interpreter, scheduler, arithmetic/logic/X-Z ops

Additional sim tests:
- `test_scheduler.py`, `test_evaluator.py`, `test_executor.py`, `test_testbench.py`
- `test_value.py`, `test_value_widths.py`, `test_vcd.py`
- `test_hierarchy.py`, `test_memory.py`, `test_generate.py`, `test_function_task.py`
- `test_sim_sv.py`, `test_precedence_and_fixes.py`, `test_param_width.py`
- `test_compiled.py` — compiled engine unit tests (4500+ parametrized, most `@pytest.mark.slow`)
- `test_compiled_batch_run_propagation.py`, `test_compiled_latent_risks.py`
- `test_wide_signal_catchall.py`, `test_multi_domain_runner.py`
- Endpoint tests: `test_axi_lite_master.py`, `test_axis_endpoints.py`, `test_axis_frame.py`,
  `test_membus_endpoints.py`, `test_stream_protocol.py`
- Bench native tests: `test_bench_native.py`, `test_bench_plan.py`, `test_bench_planner.py`,
  `test_bench_runtime.py`, `test_planner_naming_fallback.py`
- Cross-engine with real-world designs: `test_ibex_examples.py`, `test_pulp_axi_examples.py`,
  `test_pulp_common_cells_examples.py`, `test_pulp_ready_valid_examples.py`
- `test_coordinator_strict.py`, `test_combinational_coordinator.py`, `test_interface_detection.py`

Integration tests:
- `test_darkriscv.py` — preprocess, parse, simulation boot

## Testbench Performance Patterns

The compiled engine can execute in two modes, and the choice of testbench
structure determines which mode is used — leading to a **500x performance
difference**.

### The problem: `initial` blocks with timing

Any `initial` block containing `#delay`, `@(event)`, or `while(1)` with
timing runs as a **Python coroutine** in the VM and compiled engines.
Each coroutine resume requires:

1. Sync all (or targeted) signal values from C arrays → Python `EvalContext`
2. Resume the Python coroutine (one `next(coro)` call)
3. Sync changed values back from Python → C arrays
4. Re-insert into the Python event queue

This costs ~200μs per resume. For a clock generator
(`initial while(1) #5 CLK = !CLK`), that's one resume per clock edge —
**100,000 Python round-trips for 500K time units**. The compiled engine's
C delta loop finishes in ~1μs per edge, but the Python wrapper overhead
dominates.

### Impact by pattern

| Testbench pattern | Engine mode | Per-edge cost | 500K time units |
|---|---|---|---|
| `initial while(1) #5 CLK = !CLK` | Step (coroutine) | ~200μs | ~163s |
| No timing, `batch_run()` from Python | Batch (pure C) | ~1μs | ~0.3s |

### Guidelines for fast simulation

1. **Never put clock generators in Verilog `initial` blocks** when using
   the compiled engine. Use `batch_run()` instead, which toggles the clock
   in a C loop with `nogil`.

2. **Move reset sequences to Python**. Instead of `initial #1000 RES = 0;`,
   pass `events=[(100, "RES", 0)]` to `batch_run()` which applies the
   event inside the C loop.

3. **Keep Verilog testbenches minimal** — just wire declarations and
   module instantiation. No `initial` blocks with timing, no `always`
   blocks in the testbench.

4. **Use `sim.run(max_time=0)`** to execute simple initial blocks like
   `$readmemh` and settle combinational logic before switching to
   `batch_run()`.

### Recommended testbench structure

**Verilog (minimal wrapper — no timing)**:
```verilog
module testbench;
    reg CLK = 0;
    reg RES = 1;
    // No initial blocks! Clock and reset driven by Python.
    my_dut dut(.clk(CLK), .rst(RES));
endmodule
```

**Python (drives everything)**:
```python
sim = Simulator(top, engine="compiled", design=design)
sim.run(max_time=0)  # $readmemh + settle

# Single batch_run call — entire sim in C
events = [(100, "RES", 0)]  # release reset at cycle 100
sim.batch_run(50000, "CLK", clock_period=10, events=events)

# Read results — display_output is populated by batch_run
print(sim.display_output)
print(sim.read("dut.PC"))
```

### `batch_run` API

```python
sim.batch_run(
    cycles=50000,           # number of full clock cycles
    clock_name="CLK",       # signal to toggle (posedge then negedge)
    clock_period=10,         # time units per cycle (for sim.time tracking)
    events=[                 # optional: scheduled signal changes
        (100, "RES", 0),     # at cycle 100, set RES = 0
        (200, "IRQ", 1),     # at cycle 200, set IRQ = 1
        (201, "IRQ", 0),     # at cycle 201, clear IRQ
    ],
)
```

Events are applied before the posedge of the specified cycle, inside the
C loop. They must be sorted by cycle number. This allows driving reset
sequences, interrupt pulses, and other one-shot stimulus without leaving C.

## Engine-Native Bench Lowering

`batch_run` works when clock and reset are the only testbench externals.
For AXI-Stream, AXI-Lite, and AXI4 stimulus that also needs to run at
compiled-engine speed, use `compile_native` from `veriforge.sim.bench`.

`compile_native` synthesises a DSL wrapper module containing the bench
FSMs alongside the DUT so that the *entire* testbench — bench stimulus
and DUT logic — runs as a single compiled module with no Python
coroutine overhead.

Supported lowerings:

| Lowering | DUT-side role | Bench role |
|---|---|---|
| `AXIStreamSourceLowering` | AXI-Stream slave | Fixed-beat source FSM (PRNG or fixed data) |
| `AXIStreamSinkLowering` | AXI-Stream master | Always-ready capture sink |
| `AXILiteMasterLowering` | AXI-Lite slave | Scripted write/read master FSM |
| `AXILiteSlaveLowering` | AXI-Lite master | Memory-backed single-beat slave responder |
| `AXI4SlaveLowering` | AXI4 master | Memory-backed INCR-burst responder |
| `AXI4MasterLowering` | AXI4 slave | Scripted single-beat write/read master FSM |
| `MemBusMasterLowering` | MemBus slave | Scripted synchronous-bus write/read master |
| `MemBusResponderLowering` | MemBus master | Memory-backed synchronous-bus slave responder |

```python
from veriforge.sim.bench import (
    Testbench, compile_native,
    AXILiteMasterLowering, AXILiteOp, AXI4SlaveLowering,
)

bench = Testbench(dut_module)
lowered = compile_native(
    bench,
    lowerings={"s_axil": AXILiteMasterLowering(operations=[
        AXILiteOp.write(0x00, 0xDEAD_BEEF),
        AXILiteOp.read(0x00),
    ])},
)
sim = Simulator(lowered.wrapper, design=lowered.design, engine="compiled")
# ... run as normal, read lowered.capture_signals after sim.run(...)
```

See [bench_native_lowering.md](bench_native_lowering.md) for full
API reference, all eight lowerings, complete examples, and performance guidance.
