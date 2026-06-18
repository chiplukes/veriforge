# Simulation Overview

The simulation system is an event-driven Verilog simulator implemented in
Python. It reads the same model AST produced by the parser and executes it
according to IEEE 1364-2005 scheduling semantics (Active → NBA → delta-cycle
convergence). Four engines share one API and can be swapped with a single
keyword argument.

---

## Minimal example (raw Simulator API)

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock

design = parse_file("counter.v")
module = design.get_module("counter")

sim = Simulator(module, engine="vm-fast")
clk = sim.signal("clk")
sim.fork(Clock(clk, period=10))   # must call fork() before run()

sim.drive("rst", 1)
sim.run(max_time=20)              # settle reset
sim.drive("rst", 0)
sim.run(max_time=500)             # run 500 time units
print(sim.read("count"))
```

## Testbench framework example (auto-wired AXI/Stream interfaces)

```python
from veriforge.sim.bench import Testbench

bench = Testbench(module, engine="vm-fast")
with bench.run():
    bench.reset_all()
    axis_in  = bench.iface("s_axis")   # AXIStreamProxy — auto-detected
    axis_out = bench.iface("m_axis")
    axis_in.put([0xDE, 0xAD, 0xBE, 0xEF])
    frame = axis_out.get(timeout=100)
```

---

## Reading map

### First: core concepts

| Note | What you learn |
|------|---------------|
| [simulation/simulation_model.md](simulation/simulation_model.md) | `Value` (4-state bit-vector), `SignalHandle`, `Clock`, `Simulator` API — start here for the raw primitives |
| [simulation/simulator_engines.md](simulation/simulator_engines.md) | All four engines side-by-side: strategy, signal storage, performance, file layout, shared architecture, timing-control fallback cost |

### Then: testbench framework

| Note | What you learn |
|------|---------------|
| [simulation/bench_usage.md](simulation/bench_usage.md) | `Testbench` class — `bench.run()` context manager, proxy API (AXI-Stream, AXI-Lite, AXI4, MemBus), `bench.step()`, `bench.reset_all()` |
| [simulation/endpoint_timing_model.md](simulation/endpoint_timing_model.md) | Exactly when `tick_pre` / `sample_pre` / `tick_post` run each cycle, with a concrete bug example showing why phase matters |
| [simulation/testbench_phase_contract.md](simulation/testbench_phase_contract.md) | Rules for writing correct custom endpoints — what each phase must and must not do, and how multi-clock (async) safety is maintained |
| [simulation/generator_tb.md](simulation/generator_tb.md) | Three testbench styles side-by-side: raw `Simulator` API, class-based endpoints, and `@domain.generator` coroutine endpoints — shows the trade-offs and when to use each |

### For performance

| Note | What you learn |
|------|---------------|
| [simulation/simulator_engines.md §Testbench Performance Patterns](simulation/simulator_engines.md) | `batch_run()` vs step mode, the 500× gap from `initial`-block clock generators, recommended testbench structure |
| [simulation/bench_native_lowering.md](simulation/bench_native_lowering.md) | `compile_native()` — runs AXI/Stream/MemBus stimulus inside the compiled C loop, no Python overhead per cycle |
| [simulation/cycache.md](simulation/cycache.md) | How the two-layer `.cycache/` system avoids recompiling unchanged designs; env vars; test isolation |

### Engine internals (deep dives)

| Note | What you learn |
|------|---------------|
| [simulation/simulator_python.md](simulation/simulator_python.md) | Reference tree-walking engine: `EvalContext`, `ExpressionEvaluator`, `StatementExecutor`, `Scheduler`, dirty-set tracking, optimization history |
| [simulation/simulator_bytecode_vm.md](simulation/simulator_bytecode_vm.md) | Bytecode VM: 83-opcode instruction set, compiler, stack-based interpreter, Cython fast path (`_interp_fast.pyx`) |
| [simulation/simulator_compile_cython.md](simulation/simulator_compile_cython.md) | Compiled engine: AST → design-specific `.pyx` codegen, generated code structure, expression/statement emitters, `batch_run()` |

### Debugging and validation

| Note | What you learn |
|------|---------------|
| [simulation/debug.md](simulation/debug.md) | Debugging strategies: picking the right engine, signal snapshotting, bytecode inspection, VCD dumps, hang diagnosis with `py-spy` |
| [simulation/cosim.md](simulation/cosim.md) | `IcarusCosim` — cross-validates the Python engines against Icarus Verilog via VCD comparison |

### Wide signals (>64 bit)

| Note | What you learn |
|------|---------------|
| [simulation/wide_signal_coverage.md](simulation/wide_signal_coverage.md) | Per-operation coverage matrix for the compiled engine: which ops have C primitives, which are tested, test class selectors |

---

## Engine selection at a glance

| Need | Engine |
|------|--------|
| Debug Verilog logic, set breakpoints | `"reference"` |
| Good performance, no build step | `"vm-fast"` |
| Maximum throughput, step-by-step testbench | `"compiled"` |
| Maximum throughput, batch workloads (thousands of cycles) | `"compiled"` + `batch_run()` or `compile_native()` |
| Validate VM against ground truth | `"reference"` + `"vm"` parametrized |

## Where this fits in the project

The simulation system sits on top of the **parser and model** (`veriforge.project`,
`veriforge.model`) — it never calls the parser itself, only consumes the
`Module` AST. See [architecture.md](architecture.md) for the full project map and
[getting_started.md](getting_started.md) for installation and first steps.
