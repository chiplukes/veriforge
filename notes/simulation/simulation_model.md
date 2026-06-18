# Simulation Model

A pure-Python event-driven simulation engine built on top of the
Verilog semantic model. The goal is a self-contained simulator that
requires no external tools — parse, analyze, and simulate Verilog
designs entirely within Python, with a testbench API designed for
native debugging.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│  Testbench (Python callable)                                   │
│  def test(sim):                                                │
│      sim.drive("clk", 1); sim.run_step(10)                     │
│      assert sim.read("count") == 5                             │
├────────────────────────────────────────────────────────────────┤
│  Simulator  (high-level facade)                                │
│  signal(), drive(), read(), fork(Clock), run(), run_step()     │
├────────────────────────────────────────────────────────────────┤
│  Scheduler  (event-driven kernel)                              │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────────┐  │
│  │ Event Queue   │  │ Process       │  │ Signal State       │  │
│  │ (time-ordered)│  │ Management    │  │ (4-state values)   │  │
│  └──────────────┘  └───────────────┘  └────────────────────┘  │
├────────────────────────────────────────────────────────────────┤
│  Statement Executor                                            │
│  Walk Statement trees → update signals                         │
│  execute() for combinational/always, execute_coroutine()       │
│  for initial blocks with suspend/resume                        │
├────────────────────────────────────────────────────────────────┤
│  Expression Evaluator                                          │
│  Walk Expression trees → return Value                          │
├────────────────────────────────────────────────────────────────┤
│  Semantic Model (existing)                                     │
│  Design, Module, AlwaysBlock, Statement, Expression, ...       │
└────────────────────────────────────────────────────────────────┘
```

---

## 4-State Value Type

Verilog signals are 4-state: each bit is `0`, `1`, `x`, or `z`. The
value representation uses a pair of integers (val, mask) where each bit
position encodes the state.

```
bit state    val bit    mask bit
─────────    ───────    ────────
    0           0          0
    1           1          0
    x           0          1     (could be 0 or 1, unknown)
    z           0          1     (high impedance)
```

For pure 0/1 signals (the common case), `mask == 0` and `val` is just
a plain Python int — arithmetic is fast. X and Z propagation only kicks
in when `mask != 0`.

```python
class Value:
    """4-state bit vector.

    Uses int-pair (val, mask) for compact, fast representation.
    When mask == 0, val is a plain Python int — no overhead.
    """
    __slots__ = ("mask", "type_info", "val", "width")

    def __init__(self, val: int = 0, *, width: int = 1, mask: int = 0, type_info: object | None = None):
        self.val = val
        self.mask = mask      # bits set to 1 are x or z
        self.width = width
        self.type_info = type_info

    @property
    def is_defined(self) -> bool:
        """True if no bits are x or z."""
        return self.mask == 0

    def __int__(self) -> int:
        """Convert to Python int. Raises if any bits are x/z."""
        if self.mask:
            raise ValueError("Cannot convert value with x/z bits to int")
        return self.val

    def __eq__(self, other) -> bool:
        if isinstance(other, int):
            return self.mask == 0 and self.val == other
        if isinstance(other, Value):
            return self.val == other.val and self.mask == other.mask
        return NotImplemented

    def __getitem__(self, index):
        """Bit select or range select."""
        ...

    # Verilog-style arithmetic with x propagation
    def __add__(self, other): ...
    def __and__(self, other): ...
    def __or__(self, other): ...
    def __xor__(self, other): ...
    def __invert__(self): ...
```

**Why int-pair and not a list of enum values?** Two reasons:
1. Python `int` operations on the common 0/1 case are a single
   operation, not N operations for N bits.
2. The int-pair maps directly to C integer arithmetic under Cython —
   no iteration, no object allocation per bit.

### X/Z Propagation Rules (IEEE 1364-2005)

```python
# Bitwise AND: x & 0 = 0, x & 1 = x, x & x = x
def bit_and(a: Value, b: Value) -> Value:
    # Result is 0 wherever either input is definitely 0
    definite_zero = (~a.val & ~a.mask) | (~b.val & ~b.mask)
    mask = (a.mask | b.mask) & ~definite_zero
    val = a.val & b.val & ~mask
    return Value(val, width=max(a.width, b.width), mask=mask)

# Bitwise OR: x | 1 = 1, x | 0 = x, x | x = x
def bit_or(a: Value, b: Value) -> Value:
    definite_one = (a.val & ~a.mask) | (b.val & ~b.mask)
    mask = (a.mask | b.mask) & ~definite_one
    val = (a.val | b.val) | definite_one
    return Value(val & ~mask, width=max(a.width, b.width), mask=mask)
```

---

## Signal Handles

Testbench code interacts with design signals through `SignalHandle`
objects. These wrap the model's `Net` / `Variable` / `Port` objects
and provide read/write access to the simulation state.

```python
class SignalHandle:
    """Runtime handle to a signal in the simulation."""
    __slots__ = ("_name", "_sched", "_width")

    @property
    def name(self) -> str: ...

    @property
    def value(self) -> Value:
        """Read current signal value."""
        return self._sched.read_signal(self._name)

    @value.setter
    def value(self, new_val):
        """Drive signal from testbench."""
        if isinstance(new_val, int):
            new_val = Value(new_val, width=self._width)
        elif isinstance(new_val, str):
            new_val = Value.from_verilog(new_val)
        self._sched.drive_signal(self._name, new_val)

    @property
    def width(self) -> int: ...
```

### Reading and Driving

There are two ways to interact with signals — through `SignalHandle`
objects or through the `Simulator` convenience methods:

```python
# Via SignalHandle
handle = sim.signal("count")
val = handle.value              # → Value object
n = int(handle.value)           # → Python int (raises if x/z)
handle.value = 1                # drive integer
handle.value = "8'hFF"          # drive from Verilog literal

# Via Simulator convenience methods
sim.drive("clk", 1)             # drive by name
val = sim.read("count")         # read by name → Value
```

---

## Testbench API

The simulator uses a **synchronous callback API**. The testbench
function receives a `Simulator` instance and drives signals / advances
time imperatively. There is no `async/await` — the test controls
simulation time explicitly.

### Simulator Class

```python
class Simulator:
    """High-level simulation facade."""

    def __init__(self, module: Module, *, engine: str = "reference",
                 design: Design | None = None, delta_limit: int = 10_000):
        """Create simulator and elaborate immediately."""

    @property
    def time(self) -> int:
        """Current simulation time."""

    @property
    def display_output(self) -> list[str]:
        """Collected $display output."""

    def signal(self, name: str) -> SignalHandle:
        """Get a signal handle by name."""

    def drive(self, name: str, value) -> None:
        """Drive a signal by name (int, str, or Value)."""

    def read(self, name: str) -> Value:
        """Read a signal's current value by name."""

    def fork(self, clock: Clock) -> None:
        """Register a clock generator (accepts Clock objects only)."""

    def run(self, test_fn: Callable | None = None, *, max_time: int = 1_000_000) -> None:
        """Run test function with max simulation time."""

    def run_step(self, *, max_time: int = 1_000_000) -> bool:
        """Advance simulation by one time step. Returns False when finished."""
```

### Trigger Classes

Trigger classes exist as data structures with `check(old, new)` methods
for edge detection. They are used internally by the scheduler for
sensitivity matching, not as awaitable objects.

```python
class RisingEdge:
    """Detects 0→1 transition."""
    def __init__(self, signal: SignalHandle): ...
    def check(self, old: Value, new: Value) -> bool: ...

class FallingEdge:
    """Detects 1→0 transition."""
    def __init__(self, signal: SignalHandle): ...

class Edge:
    """Detects any value change."""
    def __init__(self, signal: SignalHandle): ...

class Timer:
    """Time delay marker."""
    def __init__(self, time: int): ...

class ReadOnly:
    """Marker for read-only region (stub)."""

class NextTimeStep:
    """Marker for next time step (stub)."""
```

### Clock Generator

```python
class Clock:
    """Built-in clock generator utility.

    Works via pre-scheduled toggle events in the event queue,
    not via async coroutines.

    Usage:
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.fork(Clock(sim.signal("clk"), period=10, duty=0.3))
    """

    def __init__(self, signal: SignalHandle, *, period: int,
                 duty: float = 0.5):
        self.signal = signal
        self.high_time = max(1, int(period * duty))
        self.low_time = max(1, period - self.high_time)
```

### Testbench Patterns

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock

# Parse and build model
design = parse_file("counter.v")
module = design.get_module("counter")

# Create simulator
sim = Simulator(module)

# Fork the clock before calling run()
sim.fork(Clock(sim.signal("clk"), period=10))

# Define test — synchronous callback, not async
def test_counter(sim):
    # Drive reset high
    sim.drive("rst", 1)

    # Advance time explicitly (max_time is keyword-only)
    sim.run_step(max_time=25)

    # Drive reset low
    sim.drive("rst", 0)

    # Run more time and check results
    sim.run_step(max_time=100)
    assert sim.read("count") == Value(5, width=8)

# Run (schedules pre-forked clocks, calls test_fn, then runs scheduler to max_time)
sim.run(test_counter, max_time=200)
```

---

## How Combinational Logic Works

An always block classified as `COMBINATIONAL` re-evaluates whenever
any of its input signals change. The simulator handles this through
sensitivity tracking and delta cycles.

### Example: Combinational Adder

Given this Verilog:
```verilog
module adder(
    input  [7:0] a, b,
    output [8:0] sum
);
    assign sum = a + b;
endmodule
```

The model has a `ContinuousAssign` with `lhs = Identifier("sum")` and
`rhs = BinaryOp("+", Identifier("a"), Identifier("b"))`. The simulator:

1. Registers this assign as sensitive to signals `a` and `b`
2. Whenever `a` or `b` changes, evaluates the RHS expression tree
3. Drives the result onto `sum`
4. If `sum` changed, propagates to anything sensitive to `sum`

### Example: Combinational Mux (always block)

```verilog
module mux4(
    input  [1:0] sel,
    input  [7:0] a, b, c, d,
    output reg [7:0] y
);
    always @(*) begin
        case (sel)
            2'd0: y = a;
            2'd1: y = b;
            2'd2: y = c;
            2'd3: y = d;
        endcase
    end
endmodule
```

The `AlwaysBlock` has `sensitivity_type = COMBINATIONAL`. The simulator:

1. Builds a sensitivity set: `{sel, a, b, c, d}` (all signals read in body)
2. Whenever any of these change, executes the statement tree:
   - Evaluates `sel` → gets Value
   - Matches against case items (plain int compare when no x/z)
   - Executes the matched `BlockingAssign`: sets `y` immediately
3. If `y` changed, triggers anything sensitive to `y`

### Sensitivity Inference for `@(*)`

For `@(*)` (wildcard sensitivity), the simulator must determine which
signals the block reads. This is done by walking the statement tree
and collecting all `Identifier` nodes used in read contexts:

```python
def _infer_sensitivity(block: AlwaysBlock) -> set[str]:
    """Collect all signal names read by this always block."""
    reads: set[str] = set()
    for node in block.body.walk():
        if isinstance(node, Identifier):
            # Skip LHS of assignments (those are writes)
            if not _is_lhs_target(node):
                reads.add(node.name)
    return reads
```

The existing analysis pass (Layer 3) already populates `Identifier.resolved`,
so signal lookup is a direct pointer dereference — no name lookup at runtime.

---

## Blocking vs. Non-Blocking Assignments

This is the most important simulation semantic distinction in Verilog,
and it's where many beginners (and some tools) get it wrong.

### Blocking Assignment (`=`)

Executes **immediately** — the LHS is updated before the next statement
runs. Used in combinational logic.

```verilog
always @(*) begin
    temp = a & b;      // temp is updated NOW
    y = temp | c;      // reads the just-updated temp
end
```

Execution steps:
```
1. Evaluate (a & b)     → result = 0x0F
2. Update temp = 0x0F   ← happens immediately
3. Evaluate (temp | c)  → uses temp=0x0F, result = 0x3F
4. Update y = 0x3F      ← happens immediately
```

In the simulator:
```python
def _exec_blocking_assign(self, stmt: BlockingAssign, ctx: ExecContext):
    rhs_val = self._eval_expression(stmt.rhs, ctx)
    # Update immediately — visible to subsequent statements
    self._write_signal(stmt.lhs, rhs_val, immediate=True)
```

### Non-Blocking Assignment (`<=`)

**Schedules** the update for the end of the current time step (NBA
region). All RHS values are read before any LHS values are updated.
Used in sequential (clocked) logic.

```verilog
always @(posedge clk) begin
    q1 <= d;           // reads d NOW, schedules q1 update
    q2 <= q1;          // reads q1's CURRENT value (not the new d)
end
```

Execution steps:
```
1. Evaluate d       → result = 0xAB
2. Schedule: q1 ← 0xAB  (deferred, not yet applied)
3. Evaluate q1      → current value 0x55 (NOT 0xAB — hasn't changed yet)
4. Schedule: q2 ← 0x55  (deferred)
--- end of active region ---
5. Apply q1 = 0xAB      ← NBA region
6. Apply q2 = 0x55      ← NBA region
```

In the simulator:
```python
def _exec_nonblocking_assign(self, stmt: NonblockingAssign, ctx: ExecContext):
    rhs_val = self._eval_expression(stmt.rhs, ctx)
    # Schedule for NBA region — NOT applied yet
    self._schedule_nba(stmt.lhs, rhs_val)
```

### Why This Matters: The Swap Example

```verilog
// WRONG — blocking: a stays the same, b gets old a
always @(posedge clk) begin
    a = b;    // a is now b's value
    b = a;    // b is now also b's old value (a was just overwritten)
end

// CORRECT — non-blocking: proper swap
always @(posedge clk) begin
    a <= b;   // schedule: a will become b
    b <= a;   // schedule: b will become a (reads a's current value)
end
```

### Simulation Scheduling Regions (IEEE 1364)

```
Time slot T:
  ┌─────────────────────────────┐
  │ Active Region               │
  │ • Evaluate continuous assigns│
  │ • Execute blocking assigns   │
  │ • Evaluate RHS of NBA (<=)   │
  │ • Schedule NBA updates       │
  │ • Wake testbench coroutines  │
  ├─────────────────────────────┤
  │ NBA Region                  │
  │ • Apply non-blocking updates │
  ├─────────────────────────────┤
  │ Delta Cycle Check           │
  │ • Did any signal change?     │
  │ • YES → repeat Active Region │
  │ • NO → advance to next time  │
  └─────────────────────────────┘

Advance to Time slot T+1
```

A delta cycle is a re-evaluation at the same simulation time. It happens
when a signal update triggers another sensitive process. Delta cycles
continue until all signals are stable (a fixpoint).

Example: combinational chain `a → b → c`:
```
Delta 0: testbench drives a = 1
Delta 1: assign b = a; → b becomes 1
Delta 2: assign c = b; → c becomes 1
Stable: no more changes, advance time
```

---

## Simulation Kernel

### Event Queue

```python
class SimEvent:
    """A scheduled event in the simulation."""
    __slots__ = ("callback", "signal", "time", "value")

class EventQueue:
    """Time-ordered priority queue of simulation events."""

    def __init__(self):
        self._queue: list[SimEvent] = []  # heapq-managed
        self._current_time: int = 0

    def schedule(self, time: int, event: SimEvent):
        heapq.heappush(self._queue, (time, event))

    def pop_current(self) -> list[SimEvent]:
        """Pop all events at the current time."""
        events = []
        while self._queue and self._queue[0][0] == self._current_time:
            events.append(heapq.heappop(self._queue)[1])
        return events

    def advance(self) -> int | None:
        """Advance to the next event time. Returns new time or None if empty."""
        if not self._queue:
            return None
        self._current_time = self._queue[0][0]
        return self._current_time
```

### Process Types

The scheduler manages several kinds of processes:

| Process | Source | Trigger | Implementation |
|---------|--------|---------|----------------|
| **ContinuousProcess** | `assign x = y;` | Any input signal change | Re-evaluates RHS, drives LHS |
| **AlwaysProcess** | `always @(...)` | Sensitivity list / edge match | Executes body via `execute()` |
| **InitialProcess** | `initial begin...end` | Runs once at t=0 | Uses `execute_coroutine()` generator for suspend/resume |
| **_ClockToggle** | `sim.fork(Clock(...))` | Pre-scheduled timer events | Lightweight toggle, not a formal process type |

### Main Simulation Loop

```python
class Scheduler:
    def run(self, max_time: int):
        while not self._event_queue.is_empty():
            t = self._event_queue.peek_time()
            if t > max_time:
                break
            self._current_time = t
            self._run_time_step()

    def _run_time_step(self):
        # Process events at current time
        events = self._event_queue.pop_at(self._current_time)
        for proc in events:
            self._execute_process(proc)

        # Delta cycle loop
        while True:
            # Apply NBA updates
            self._apply_nba_updates()
            # Re-run continuous assigns and triggered always blocks
            self._run_active_region()
            if not self._has_pending_changes():
                break

        # Invoke time-step callback (e.g., VCD recording)
        if self._on_time_step:
            self._on_time_step(self)
```

### Expression Evaluator

Walks the `Expression` tree from our semantic model and returns a `Value`:

```python
def _eval_expression(self, expr: Expression, ctx: ExecContext) -> Value:
    if isinstance(expr, Literal):
        return Value(expr.value, width=expr.width or 32)

    if isinstance(expr, Identifier):
        # Identifier.resolved already points to the declaration
        return ctx.read_signal(expr.resolved)

    if isinstance(expr, BinaryOp):
        left = self._eval_expression(expr.left, ctx)
        right = self._eval_expression(expr.right, ctx)
        return _eval_binary_op(expr.op, left, right)

    if isinstance(expr, UnaryOp):
        operand = self._eval_expression(expr.operand, ctx)
        return _eval_unary_op(expr.op, operand)

    if isinstance(expr, TernaryOp):
        cond = self._eval_expression(expr.condition, ctx)
        if cond.is_defined and int(cond):
            return self._eval_expression(expr.true_expr, ctx)
        elif cond.is_defined:
            return self._eval_expression(expr.false_expr, ctx)
        else:
            # Condition is x/z — result is x for differing bits
            t = self._eval_expression(expr.true_expr, ctx)
            f = self._eval_expression(expr.false_expr, ctx)
            return _merge_xz(t, f)

    if isinstance(expr, Concatenation):
        parts = [self._eval_expression(p, ctx) for p in expr.parts]
        return _concatenate(parts)

    if isinstance(expr, BitSelect):
        target = self._eval_expression(expr.target, ctx)
        index = self._eval_expression(expr.index, ctx)
        return target[int(index)]

    if isinstance(expr, RangeSelect):
        target = self._eval_expression(expr.target, ctx)
        msb = int(self._eval_expression(expr.msb, ctx))
        lsb = int(self._eval_expression(expr.lsb, ctx))
        return target[msb:lsb]

    ...
```

The evaluator is deliberately written as a flat `if/elif` chain (not
visitor pattern, not dict dispatch). This translates directly to a C
switch statement under Cython — no virtual call overhead.

### Statement Executor

Walks the `Statement` tree and mutates simulation state:

```python
def _exec_statement(self, stmt: Statement, ctx: ExecContext):
    if isinstance(stmt, BlockingAssign):
        rhs = self._eval_expression(stmt.rhs, ctx)
        self._write_signal(stmt.lhs, rhs, immediate=True)

    elif isinstance(stmt, NonblockingAssign):
        rhs = self._eval_expression(stmt.rhs, ctx)
        self._schedule_nba(stmt.lhs, rhs)

    elif isinstance(stmt, IfStatement):
        cond = self._eval_expression(stmt.condition, ctx)
        if cond.is_defined and int(cond):
            if stmt.then_body:
                self._exec_statement(stmt.then_body, ctx)
        elif stmt.else_body:
            self._exec_statement(stmt.else_body, ctx)

    elif isinstance(stmt, CaseStatement):
        sel = self._eval_expression(stmt.expression, ctx)
        for item in stmt.items:
            if item.is_default:
                if item.body:
                    self._exec_statement(item.body, ctx)
                return
            for val_expr in item.values:
                val = self._eval_expression(val_expr, ctx)
                if _case_match(stmt.case_type, sel, val):
                    if item.body:
                        self._exec_statement(item.body, ctx)
                    return

    elif isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            self._exec_statement(s, ctx)

    elif isinstance(stmt, ForLoop):
        self._exec_statement(stmt.init, ctx)
        while True:
            cond = self._eval_expression(stmt.condition, ctx)
            if not (cond.is_defined and int(cond)):
                break
            if stmt.body:
                self._exec_statement(stmt.body, ctx)
            self._exec_statement(stmt.update, ctx)

    elif isinstance(stmt, SystemTaskCall):
        self._exec_system_task(stmt, ctx)

    elif isinstance(stmt, DelayControl):
        # Suspend process for delay amount
        delay = self._eval_expression(stmt.delay, ctx)
        ctx.suspend_for(int(delay))
        if stmt.body:
            self._exec_statement(stmt.body, ctx)

    ...
```

### Coroutine-Based Execution for Initial Blocks

Initial blocks can contain `#delay` statements that must suspend
execution and resume later at the correct point — including deep
inside nested `begin/end` blocks, `if/else`, `case`, and loops.

Raising `SuspendExecution` from `execute()` loses the call stack.
The solution is `execute_coroutine()` — a Python generator that
**yields** `SuspendExecution` instead of raising it. Python generators
preserve the full call frame stack across yields, so when the
scheduler calls `next(coroutine)` the execution resumes exactly where
it left off.

```python
def execute_coroutine(self, stmt, ctx):
    """Generator-based executor for initial blocks.

    Yields SuspendExecution at #delay and @(event) points.
    The scheduler advances the generator with next() after the
    delay/event resolves.
    """
    if isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            yield from self.execute_coroutine(s, ctx)

    elif isinstance(stmt, DelayControl):
        delay = self._evaluator.eval(stmt.delay, ctx.eval_ctx)
        yield SuspendExecution(delay=int(delay))
        # Resumes here after delay — execute body
        if stmt.body:
            yield from self.execute_coroutine(stmt.body, ctx)

    elif isinstance(stmt, ForLoop):
        self.execute(stmt.init, ctx)
        while True:
            cond = self._evaluator.eval(stmt.condition, ctx.eval_ctx)
            if not (cond.is_defined and int(cond)):
                break
            if stmt.body:
                yield from self.execute_coroutine(stmt.body, ctx)
            self.execute(stmt.update, ctx)

    # ... handles all statement types
```

The scheduler manages this for `InitialProcess`:

```python
def _execute_process(self, proc):
    if isinstance(proc, InitialProcess):
        if proc._coroutine is None:
            proc._coroutine = self._executor.execute_coroutine(
                proc.block.body, proc._ctx)
        try:
            suspension = next(proc._coroutine)
            # Re-schedule after delay
            self.schedule_at(self._current_time + suspension.delay, proc)
        except StopIteration:
            pass  # Initial block completed
    ...
```

### LHS Target Width Truncation

When writing to a signal, the executor truncates the value to match
the target's declared width. This prevents unsized literals (e.g.,
`din = 1` stored as 32 bits) from corrupting signal widths during
concatenation and assignment:

```python
def _write_target(self, target, value, ctx, *, nba=False):
    if isinstance(target, Identifier):
        current = ctx.eval_ctx.read_signal(target.name)
        if current.width != value.width:
            value = value.resize(current.width)
        ...
```

---

## Testbench Runner

### Basic Usage

```python
from veriforge.project import parse_file
from veriforge.sim import Simulator, Clock

# Parse the design
design = parse_file("counter.v")
module = design.get_module("counter")

# Create simulator and fork clock before calling run()
sim = Simulator(module)
sim.fork(Clock(sim.signal("clk"), period=10))

# Define test (synchronous callback)
def test_counter(sim):
    sim.drive("rst", 1)
    sim.run_step(max_time=25)
    sim.drive("rst", 0)
    sim.run_step(max_time=200)

    assert sim.read("count") == Value(10, width=8)

# Run
sim.run(test_counter, max_time=500)
```

### VCD Waveform Output

The `VcdWriter` is a standalone utility for writing IEEE 1364-2001
compliant VCD files. It is not currently integrated into the Simulator
directly — it must be wired manually via the scheduler's `_on_time_step`
callback:

```python
from veriforge.sim import VcdWriter

# Standalone usage
with VcdWriter("dump.vcd", timescale="1ns") as vcd:
    vcd.add_signal("clk", width=1)
    vcd.add_signal("count", width=8)
    vcd.write_header()
    vcd.set_time(0)
    vcd.change("clk", Value(0, width=1))
    vcd.change("count", Value(0, width=8))
    vcd.set_time(5)
    vcd.change("clk", Value(1, width=1))
    ...

# Integration via _on_time_step callback (used by validation harness)
scheduler._on_time_step = lambda sched: record_signals(sched)
```

### VCD Comparison Utility

The `vcd_compare` module parses and compares VCD files from different
simulators. Used by the Icarus Verilog validation harness.

```python
from veriforge.sim.vcd_compare import parse_vcd, compare_vcd

ref_data = parse_vcd(iverilog_vcd_text, strip_hierarchy=True)
test_data = parse_vcd(our_sim_vcd_text, strip_hierarchy=True)

diffs = compare_vcd(
    ref_data, test_data,
    signals=["clk", "count", "out"],  # optional filter
    ignore_signals=["_vcd_time"],
    max_time=200,
)
assert not diffs, f"Mismatches:\n" + "\n".join(diffs)
```

---

## Debugging: Why Pure Python Matters

### The cocotb Debugging Problem

cocotb runs Python inside a Verilog simulator process via VPI (C
foreign function interface). This creates fundamental debugging
obstacles:

1. **stdin is owned by the simulator**, not Python. `pdb.set_trace()`
   and `breakpoint()` cannot read user input. The cocotb docs
   explicitly state: *"Using `import pdb; pdb.set_trace()` directly is
   also frequently not possible, due to the way that simulators
   interfere with stdin."*

2. **The workaround is remote_pdb** — connecting via telnet to a TCP
   socket. This works but is clunky: you lose IDE integration, syntax
   highlighting, variable inspection, and conditional breakpoints.

3. **VS Code/debugpy integration is still an open issue** (cocotb
   [#2111](https://github.com/cocotb/cocotb/issues/2111), opened 2020,
   still open as of 2025). The PR for it ([#2103](https://github.com/cocotb/cocotb/pull/2103))
   has been in draft status for years.

4. **COCOTB_PDB_ON_EXCEPTION** was added in 2020 to drop into pdb on
   test failures, but it has known bugs — quitting the debugger doesn't
   work properly ([#4973](https://github.com/cocotb/cocotb/issues/4973),
   Sep 2025), and Ctrl-C while the simulator is running crashes it
   ([#4837](https://github.com/cocotb/cocotb/issues/4837)).

In short: cocotb debugging has improved from "impossible" to "painful
with workarounds." The fundamental issue — Python embedded inside a C
simulator process — hasn't changed.

### Our Approach: Native Python Execution

Because our simulator IS Python, none of these problems exist:

```python
# This just works — no special setup, no remote connections
def test_counter(sim):
    clk = sim.signal("clk")
    sim.fork(Clock(clk, period=10))

    sim.drive("rst", 1)
    sim.run_step(max_time=25)

    breakpoint()  # ← standard Python debugger, works perfectly

    sim.drive("rst", 0)
    sim.run_step(max_time=50)
    assert sim.read("count") == Value(1, width=8)
```

What works out of the box:
- **`breakpoint()` / `pdb.set_trace()`** — standard Python, stdin is yours
- **VS Code debugger** — set breakpoints, inspect variables, step through
- **PyCharm debugger** — same, full IDE integration
- **Conditional breakpoints** — `if sim.read("count") == 5: breakpoint()`
- **Variable inspection** — hover over variables in the debugger
- **Step into simulation** — step from testbench into the expression
  evaluator, see exactly how `a + b` is computed
- **pytest integration** — `--pdb` flag drops into debugger on failure

This is possible because the simulation loop is pure Python. The
scheduler runs synchronously, so the debugger sees normal Python frames
throughout.

### Debugging the Verilog Logic Itself

Because the expression evaluator and statement executor walk our model
trees, you can set breakpoints in the simulation engine to debug the
*Verilog logic*, not just the testbench:

```python
# In the evaluator, set a conditional breakpoint:
# Break when signal "count" is being evaluated
def _eval_expression(self, expr, ctx):
    if isinstance(expr, Identifier) and expr.name == "count":
        pass  # ← set breakpoint here

    ...
```

---

## Cython Acceleration

The simulation speedup is achieved through the **bytecode VM** rather than
Cython-compiling the tree-walking evaluator. The VM compiles the AST to a
compact 74-opcode stack instruction set, then runs it in a tight C loop via
`_interp_fast.pyx`. This yields ~9.9× over the reference engine.

The design patterns that make the VM Cython-ready:

1. **`__slots__` on all classes** — maps to C struct fields, no `__dict__`
2. **Flat if/elif dispatch** in the interpreter — becomes a C switch
3. **Int-pair `Value` type** — maps to C integer arithmetic
4. **No closures in the hot path** — straight procedural code

The `engine="vm-fast"` selector uses the compiled interpreter; `engine="vm"`
falls back to pure-Python when Cython is unavailable. The reference engine
(`engine="reference"`) and compiled engine (`engine="compiled"`) are the
other two options. See `notes/simulation/simulator_bytecode_vm.md` for
the VM architecture details.

---

## Hardware Construction DSL

`src/veriforge/dsl/` provides an operator-overloaded Python API for
designing hardware. Signal objects build model nodes instead of computing
values — Python expressions become circuit descriptions.

```python
with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    count = m.output_reg("count", width=8)

    with m.always(posedge(clk)):
        with m.if_(rst):
            count <<= 0
        with m.else_():
            count <<= count + 1

# Emit Verilog for synthesis
print(emit_verilog(m))

# Or simulate directly — no Verilog round-trip needed
sim = Simulator(m.build())
sim.run(test_counter)
```

Key design points:
- **Thin layer** — builds the existing model classes (Module, Port,
  AlwaysBlock, BinaryOp, etc.) rather than a parallel IR
- **Python loops = generate** — `for i in range(8)` unrolls at
  elaboration time, producing parameterized hardware naturally
- **Functions = reusable blocks** — Python functions that return
  signal expressions become composable hardware generators
- **Direct simulation** — constructed modules feed straight into the
  simulator without Verilog emission
- **Verilog output** — existing codegen emits synthesizable RTL for
  real tool chains (Vivado, Quartus, Yosys)

---

## Icarus Verilog Validation

### Overview

The simulator is cross-validated against Icarus Verilog (v12.0) to
verify correctness. Both simulators run the same Verilog source, produce
VCD output, and the results are compared signal-by-signal at every
time step.

### Validation Harness

Located in `tests/test_validation/test_iverilog_validation.py`:

1. **`_run_iverilog(verilog_src)`** — writes Verilog to temp dir,
   compiles with `iverilog -o`, runs `vvp`, returns VCD text
2. **`_run_our_sim(verilog_src, max_time)`** — parses Verilog, creates
   Simulator, records VCD via `_on_time_step` callback, returns VCD text
3. **`_validate()`** — runs both simulators and calls `compare_vcd()`
4. Tests auto-skip when iverilog is not found

### Coverage: What's Validated (74 tests)

**Combinational Logic (8 tests):**
- Arithmetic: `+`, `-`, `*`, `%`
- Bitwise: `&`, `|`, `^`, `~`
- Shift: `<<`, `>>`
- Ternary: `? :`
- Concatenation: `{a, b}`
- Reduction: `&`, `|`, `^`
- Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
- Logical: `&&`, `||`, `!`

**Extended Expressions (9 tests):**
- Divide, power (`**`)
- XNOR (`~^`), reduction NAND/NOR/XNOR
- Replication (`{N{expr}}`)
- Case equality (`===`/`!==`)
- Arithmetic shifts (`<<<`/`>>>`)
- Unary plus, mixed-width arithmetic

**Sequential Logic (3 tests):**
- D flip-flop with reset (posedge clk, posedge rst)
- 8-bit counter with enable
- 4-bit shift register with serial input

**Extended Sequential (5 tests):**
- DFF with enable, counter with load
- Up/down counter, pipeline registers
- Async reset DFF

**Extended Combinational (5 tests):**
- Priority encoder, 3-to-8 decoder
- ALU, cascaded assigns, 4:1 mux

**Initial Blocks (4 tests):**
- `#delay` suspend/resume across nested blocks
- if/else inside initial blocks
- case statement inside initial blocks
- for loop inside initial blocks

**Extended Statements (7 tests):**
- while loop, repeat loop
- forever with `$finish` (delay-based)
- Nested if/else, case with default
- Nested for loops, multiple initial blocks

**LHS Targets (4 tests):**
- Bit-select LHS, range-select LHS
- Concatenation LHS, NBA bit-select

**Mixed Logic (3 tests):**
- Combinational + initial blocks together
- always @(*) combinational block
- 4-to-1 multiplexer

**Timing and Interaction (6 tests):**
- Negedge trigger, clock divider
- Combo+seq interaction, two always blocks
- Variable delays, LFSR

**Wide & Edge Cases (5 tests):**
- 16-bit arithmetic, single-bit ops
- All-zeros/all-ones boundary values
- Sign extension, complex expressions

**Bit/Range Select (2 tests):**
- Single-bit select (`data[i]`)
- Range select (`data[7:4]`, `data[3:0]`)

**Edge Cases (4 tests):**
- Zero-delay initial blocks (`#0`)
- 8-bit overflow wrap
- Unary minus (`-a`)
- Logical operators on multi-bit values

### Coverage Gaps

Constructs our simulator supports but NOT yet validated against iverilog:

| Category | Unvalidated Constructs |
|----------|----------------------|
| **Expressions** | part select (`+:`/`-:`), `$clog2`, `$signed`/`$unsigned` |
| **Statements** | `casex`/`casez`, `disable`, `fork...join`, event trigger (`->`), task enable, `@(posedge/negedge)` in initial blocks |
| **System tasks** | `$display`, `$write`, `$monitor` |

### Bugs Found During Validation

Seven simulator/parser bugs were discovered and fixed through cross-validation:

1. **Initial block suspend/resume** — `#delay` raised `SuspendExecution`
   which lost the call stack. Fixed by adding `execute_coroutine()`.

2. **Unsized literal width corruption** — `din = 1` stored as 32-bit
   Value; concatenation `{sr[2:0], din}` produced 35 bits instead of 4.
   Fixed by adding width truncation in `_write_target()`.

3. **Integer variable width** — `integer i` parsed with `width=None`,
   initialized as 1-bit. Fixed by adding `_var_width()` that returns
   32 for INTEGER, 64 for REAL/TIME.

4. **Range-select LHS transform** — `data[3:0] = 4'hA` produced
   `BitSelect(index=3)` instead of `RangeSelect(msb=3, lsb=0)`.
   Bug was in `_apply_range_or_select()`: the `else` branch treated
   `msb_constant_expression` as a single expression. Fixed by
   delegating to `_build_range_select()` for range child types.

5. **Continuous assigns not re-evaluated after NBA** —
   `assign doubled = count << 1` was not updated when `count`
   changed via non-blocking assignment. Delta cycle loop applied
   NBAs but didn't re-run continuous assigns. Fixed by calling
   `_run_continuous_assigns()` after `apply_nba()` in the scheduler.

6. **VCD x-value normalization** — `"x"` and `"xxxxxxxx"` didn't
   compare equal, causing false mismatches. Fixed by collapsing
   all-x/all-z strings to a single character in `_normalize_vcd_value()`.

7. **Multiply result width** — `Value.__mul__` used
   `max(self.width, other.width)`, truncating products of mixed-width
   operands. IEEE 1364-2005 §5.4.1 says multiply width = sum of
   operand widths. Fixed accordingly.
