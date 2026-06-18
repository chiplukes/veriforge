# Reference Python Simulator

## Overview

The reference simulator is a pure-Python, tree-walking simulation engine
that interprets Verilog behavioral models at runtime. It is the original
engine and serves as the ground truth for correctness — the bytecode VM
is validated against it.

**Design philosophy**: clarity and auditability over speed. Every step
corresponds directly to the IEEE 1364-2005 simulation semantics, making
the code easy to reason about and debug.

**Key characteristics**:
- Tree-walking: recursively walks AST Expression/Statement nodes at runtime
- Dictionary-based signal state (signal name → `Value` object)
- 4-state logic (0, 1, x, z) via the `Value` type's `(val, mask, width)` encoding
- IEEE-compliant scheduling: Active → NBA → Delta-cycle convergence
- Coroutine-based suspend/resume for `#delay` and `@(event)` controls
- ~16,300 cycles/s on CPython 3.12 (100K-cycle counter benchmark, Linux)

## File Layout

```
src/veriforge/sim/
├── __init__.py         # Package exports: Simulator, Clock, Value, Scheduler, ...
├── testbench.py        # Simulator, SignalHandle, Clock (489 lines)
├── scheduler.py        # Scheduler, Process types, EventQueue (1,485 lines)
├── evaluator.py        # ExpressionEvaluator, EvalContext (888 lines)
├── executor.py         # StatementExecutor, NbaEntry (1,342 lines)
├── elaborate.py        # flatten_module, signal width resolution (2,460 lines)
├── value.py            # Value (4-state bit-vector), operators (605 lines)
├── event_queue.py      # EventQueueMixin, TimedEvent — shared by VM + compiled (266 lines)
├── trace.py            # attach_vcd — live VCD tracing
├── vcd.py              # VcdWriter — waveform dump
├── vcd_compare.py      # VCD comparison utilities
└── cosim.py            # IcarusCosim — Icarus Verilog cross-validation
```

Total reference engine core: ~4,320 lines (evaluator + executor + scheduler + value).

---

## Value Type (`value.py`)

The `Value` class encodes a Verilog 4-state bit-vector as three Python integers:

| Field | Type | Meaning |
|-------|------|---------|
| `val` | `int` | Bit values (0/1 at each position) |
| `mask` | `int` | x/z indicator (1 = x/z, 0 = defined) |
| `width` | `int` | Bit width |

Encoding per bit:

| State | val bit | mask bit |
|-------|---------|----------|
| 0 | 0 | 0 |
| 1 | 1 | 0 |
| x | 0 | 1 |
| z | 0 | 1 |

When `mask == 0` (the common case), `val` is a plain Python int and all
arithmetic maps directly to integer operations.

### Operator Coverage

`Value` implements the full Verilog operator set as Python methods:

- **Arithmetic**: `+`, `-`, `*`, `//` (div), `%`, `**` — propagate x on any x/z operand
- **Bitwise**: `&`, `|`, `^`, `~`, xnor — IEEE-correct per-bit x propagation
  (e.g. `x & 0 = 0`, `x | 1 = 1`)
- **Shift**: `<<`, `>>` — shift both val and mask
- **Comparison**: `eq`, `ne`, `lt`, `le`, `gt`, `ge` — return 1-bit Value; x if either operand is x/z
- **Case equality**: `case_eq`, `case_ne` — compare x/z bits too (`===`, `!==`)
- **Reduction**: `reduce_and`, `reduce_or`, `reduce_xor`, `reduce_nand`, `reduce_nor`, `reduce_xnor`
- **Logical**: `logical_and`, `logical_or`, `logical_not` — IEEE short-circuit x semantics
  (e.g. `0 && x = 0`, `1 || x = 1`)
- **Bit/range**: `__getitem__`, `set_bit`, `set_range`, `concat`, `replicate`, `resize`, `sign_extend`

### Caching

- `_WIDTH_CACHE`: memoizes `(1 << width) - 1` to avoid recomputing width masks
- `_X_CACHE`: singletons for `Value.x(w)` up to width 64 (since Values are immutable)
- Width mask computation is inlined in `__init__` to avoid function-call overhead

### Construction Helpers

- `Value.from_verilog("8'hFF")` — parses Verilog literal strings with per-digit x/z support
- `Value.x(width)`, `Value.z(width)` — all-x / all-z values (cached)
- `Value.from_int(n, width)` — fully-defined value from Python int

---

## Expression Evaluator (`evaluator.py`)

### EvalContext

`EvalContext` holds signal state as a dictionary and tracks writes for
dirty-set computation:

```python
class EvalContext:
    _signals:         dict[str, Value]           # signal name → current value
    _dirty:           set[str] | None            # signals written (optional)
    _originals:       dict[str, Value] | None    # first pre-write snapshots
    time:             int                        # simulation time (for $time)
    _signal_signed:   dict[str, bool]            # declared-signed signals
    _struct_types:    dict[str, object]          # signal_name → StructLayout
    _struct_type_map: dict[str, object]          # typedef name → StructLayout
    _memories:        dict[str, tuple[list[Value], int]]  # name → (data, elem_width)
    _memory_names:    set[str]                   # fast membership test
    _memory_bases:    dict[str, int]             # non-zero LSB offsets for memories
    _signal_bases:    dict[str, int]             # non-zero LSB offsets for signals
```

**Dirty tracking**: The scheduler sets `_originals` before running a region.
Each `write_signal` records the value *before the first write* to that signal.
After all processes run, the scheduler compares final values against originals
to determine the *true* dirty set. This correctly handles sequences like
`A = 0; A = 1;` — only the net effect matters.

### ExpressionEvaluator

Walks Expression AST nodes and returns a `Value`. Uses flat `type(expr) is X`
dispatch (pointer compare, no MRO walk) with the hottest types first:

1. **Identifier** (most frequent) — inlined `ctx._signals.get(name)`
2. **Literal** (cached) — `id(expr)` → Value in `_literal_cache`
3. **BinaryOp** — recursive eval of left/right, then `_eval_binary_op`
4. **UnaryOp** — recursive eval, then `_eval_unary_op`
5. **TernaryOp** — condition determines branch; x-condition merges both
6. **Concatenation** — eval all parts, `concat()` them
7. **BitSelect** — eval target + index, then `target[index]`
8. **RangeSelect** — eval target + msb + lsb, then `target[msb:lsb]`
9. **Replication** — eval count + value, then `value.replicate(count)`
10. **AssignmentPattern** — named or positional struct/array literal (`'{...}`)
11. **PartSelect** — eval base + width, compute effective msb:lsb
12. **FunctionCall** — built-in system functions (`$clog2`, `$signed`, etc.)
13. **StringLiteral** — char bytes → integer
14. **Mintypmax** — evaluate typ value

### Literal Caching

`_literal_cache: dict[int, Value]` maps `id(expr)` to the computed Value.
Since AST Literal nodes are constants, this avoids re-parsing the literal
string on every evaluation cycle. The cache is keyed by Python object ID,
which is stable for the lifetime of the AST.

### Binary/Unary Operator Dispatch

`_eval_binary_op` and `_eval_unary_op` are module-level functions (not
methods) for minimal call overhead. They use flat if/elif chains mapping
operator strings (`"+"`, `"&"`, `"=="`, etc.) to `Value` method calls.

---

## Statement Executor (`executor.py`)

### Overview

`StatementExecutor` walks Statement AST nodes and mutates an `EvalContext`.
Uses the same `type(stmt) is X` dispatch pattern as the evaluator.

### Two Execution Modes

1. **`execute(stmt, ctx)`** — synchronous, raises `SuspendExecution` on
   timing controls. The call stack is lost on suspension, so this is only
   used for always blocks (which are re-executed from scratch each trigger).

2. **`execute_coroutine(stmt, ctx)`** — generator that *yields*
   `SuspendExecution` instead of raising it. Python generators preserve
   the full call-frame stack across yields, so nested control flow
   (if/case/for/while) resumes exactly where it left off. Used for
   initial blocks.

### Statement Types Handled (17)

| Statement | Execution |
|-----------|-----------|
| `BlockingAssign` | Eval RHS, write to LHS immediately |
| `NonblockingAssign` | Eval RHS, queue `NbaEntry` for later |
| `SeqBlock` | Execute statements sequentially |
| `ParBlock` | Execute sequentially (true fork/join not implemented) |
| `IfStatement` | Eval condition, branch |
| `CaseStatement` | Eval selector, match items (case/casex/casez) |
| `ForLoop` | Init, condition-check, body, update |
| `WhileLoop` | Condition-check, body |
| `ForeverLoop` | Body (infinite, exits via suspend or $finish) |
| `RepeatLoop` | Eval count, body N times |
| `DelayControl` | Raise/yield `SuspendExecution(delay=N)` |
| `EventControl` | Raise/yield `SuspendExecution(events=...)` |
| `WaitStatement` | Check condition, execute body or suspend |
| `DisableStatement` | Raise `DisableBlock` (caught by named SeqBlock) |
| `EventTrigger` | Toggle event signal value |
| `SystemTaskCall` | Route to `_exec_system_task` |
| `TaskEnable` | No-op (user-defined tasks not simulated) |

### Non-Blocking Assignment (NBA) Queue

Non-blocking assignments (`<=`) do not update signals immediately. Instead,
`_write_target` with `immediate=False` appends an `NbaEntry(lhs_name, value)`
to `nba_queue`. The scheduler applies all NBAs at the end of the Active region
via `apply_nba(ctx)`, which returns the set of signal names that actually changed.

### LHS Target Types (4)

`_write_target(lhs, value, ctx, immediate)` handles:

1. **Identifier** — simple signal write (with width resize)
2. **BitSelect** — eval index, `set_bit` on current value
3. **RangeSelect** — eval msb/lsb, `set_range` on current value
4. **Concatenation** — decompose RHS by part widths, recursive `_write_target`

### System Tasks

- `$display`, `$write` — format arguments, append to `display_output`
- `$monitor` — same as `$display` (re-fire handled by scheduler)
- `$finish`, `$stop` — raise `StopExecution`

---

## Scheduler (`scheduler.py`)

### Process Types

| Type | Class | Trigger |
|------|-------|---------|
| Continuous assign | `ContinuousProcess` | RHS signal changes |
| Combinational always | `AlwaysProcess` | Any input signal changes (`@(*)`) |
| Sequential always | `AlwaysProcess` | Clock edge (`posedge`/`negedge`) |
| Initial block | `InitialProcess` | Once at t=0 |

### Event Queue

`EventQueue` is a min-heap (`heapq`) of `_TimedEvent(time, process, seq)`.
`seq` is an insertion counter for stable ordering within the same time slot.

### Elaboration

`elaborate(module)` initializes all signals to x, creates processes, and
builds the sensitivity index (`_sig_to_procs: dict[str, list[Process]]`).

Signal widths are computed from Range objects (nets/ports) and VariableKind
(INTEGER=32, REAL/TIME=64).

Sensitivity for `@(*)` blocks is inferred by walking the AST body to collect
all Identifier reads. Explicit sensitivity lists extract signal names and
edge types from `SensitivityEdge` nodes.

### Simulation Loop

```
run(max_time)
  ├── Schedule initial blocks at t=0
  ├── Bootstrap continuous assigns
  ├── Bootstrap combinational always blocks at t=0
  └── _run_time_step(max_time)
       └── for each time step:
            ├── Advance time
            ├── Snapshot signals for edge detection
            ├── Pop and execute events
            ├── Delta cycle loop:
            │    ├── Run active region (all triggered processes)
            │    ├── Apply NBAs
            │    ├── Re-run dirty continuous assigns
            │    ├── Collect triggered (combo + sequential with edge)
            │    └── Repeat until no changes
            └── Fire time-step callback
```

### Active Region (`_run_active_region`)

1. Set `ctx._originals = {}` to start tracking writes
2. Execute each process (catching `StopExecution` and `SuspendExecution`)
3. Compute true dirty set: compare final values against originals
4. Re-run only continuous assigns whose RHS reads overlap the dirty set
5. Collect `$display` output

### Edge Detection

Sequential processes fire at most once per time step. Edge conditions are
checked against `_prev_signals` (snapshotted at the start of each time step):

- **posedge**: transition to 1 from 0, x, or z
- **negedge**: transition to 0 from 1, x, or z

### Dirty Continuous Assigns

`_run_dirty_continuous_assigns(dirty)` skips assigns whose sensitivity set
is disjoint from the dirty set. When an assign changes its output, the output
signal name is added to `dirty` so downstream assigns are re-evaluated in the
same pass — handling multi-stage combinational chains.

---

## Testbench API (`testbench.py`)

### Simulator

Top-level entry point. Wraps either the reference `Scheduler` or the VM
`VMScheduler` behind a unified API:

```python
sim = Simulator(module, engine="reference")   # tree-walking (default)
sim = Simulator(module, engine="vm")          # bytecode VM
```

Key methods:
- `signal(name)` → `SignalHandle` (cached)
- `fork(Clock(...))` → schedule periodic clock toggles
- `run(test_fn, max_time=...)` → elaborate + run event loop
- `drive(name, value)` / `read(name)` → direct signal access
- `display_output` → collected `$display` strings

### SignalHandle

Read/write proxy to a signal:
- `.value` property reads from scheduler, `.value = x` writes
- Accepts `int`, `Value`, or Verilog string (`"8'hFF"`)

### Clock Generator

`Clock(signal, *, period: int, duty: float = 0.5)` pre-schedules toggle
events up to `max_time`. Events are `_ClockToggle` objects (duck-typed
Process) for the reference engine, or `("clock_toggle", name, Value)`
tuples for the VM. Note that `period` is keyword-only.

---

## Optimization History

The reference engine has been through two optimization rounds:

### Round 1: Hot-Path Optimizations
- **Literal caching**: `id(expr)` → Value in `_literal_cache`, avoiding
  repeated literal parsing
- **`type() is X` dispatch**: replaces `isinstance()` — single pointer compare
  instead of MRO walk. Hottest types (Identifier, Literal, BinaryOp) tested first
- **Inlined signal reads**: `ctx._signals.get(name)` inlined in `eval()` and
  `_write_target()` to skip method-call overhead
- **Local variable caching**: `stack_append = stack.append` pattern in interpreter
- **Width mask cache**: `_WIDTH_CACHE` and `_X_CACHE` for `Value` construction

### Round 2: Scheduler-Level Optimizations
- **True dirty set**: Compare final values against pre-region originals instead
  of tracking individual writes. Handles `A=0; A=1;` correctly (no false dirty)
- **Dirty continuous assigns**: Only re-evaluate assigns whose RHS reads
  overlap the changed signal set, with cascading dirty propagation
- **Edge detection snapshots**: One snapshot per time step, not per delta cycle.
  Sequential processes fire at most once per time step
- **Module-level operator functions**: `_eval_binary_op` and `_eval_unary_op`
  as free functions to avoid method-lookup overhead

### Performance Bottlenecks

The fundamental performance ceiling of the tree-walking approach:

1. **Recursive tree walking**: Each expression node requires a Python function
   call. A simple `a + b` is 3 calls (eval a, eval b, eval +)
2. **Dictionary-based signal access**: `ctx._signals.get(name)` on every
   Identifier — hash + lookup per signal read
3. **Value object allocation**: Every arithmetic operation creates a new Value
   object on the Python heap
4. **`type()` dispatch chains**: Even with `is` (pointer compare), the if/elif
   chain is O(n) in the number of expression types

These bottlenecks motivated the creation of the bytecode VM engine, which
eliminates all four via flat instruction arrays, integer-indexed signal
storage, stack-based value reuse, and opcode-switched dispatch.
