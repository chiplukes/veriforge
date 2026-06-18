# Python / DSL Boundary Semantics

## Build Time vs Simulation Time

The DSL has two distinct execution phases. Understanding this separation
is essential for correct usage.

### Build Time (Python Execution)

When Python code inside a `with Module(...) as m:` block runs, it creates
**model objects** (AST nodes). Every DSL statement — `m.input()`, `m.always()`,
`count <<= count + 1` — constructs a data structure describing hardware.
No simulation occurs.

**All standard Python code runs at build time:**

```python
with Module("example") as m:
    clk = m.input("clk")
    count = m.output_reg("count", width=8)

    print("Building module...")         # Runs NOW, at build time
    for i in range(4):                  # Python loop — unrolls at build time
        m.wire(f"w{i}", width=8)        # Creates 4 wires: w0, w1, w2, w3

    with m.always(posedge(clk)):
        count <<= count + 1             # Creates a NonblockingAssign AST node
```

The `print()` executes once during module construction. The `for` loop
creates four wire declarations — this is equivalent to Verilog `generate for`.

### Simulation Time (Event-Driven Evaluation)

After `m.build()` returns a `Module`, the `Simulator` evaluates the AST
nodes using an event-driven loop. It never re-executes the original Python
code. The simulator walks `Expression` and `Statement` trees, propagating
`Value` objects through the design.

```python
module = m.build()                      # Returns model AST
sim = Simulator(module)                 # Elaborates: creates signal state + processes
sim.run(max_time=100)                   # Runs event loop on the AST
```

## What Works Where

| Pattern | Phase | Correct? | Notes |
|---------|-------|----------|-------|
| `print("debug")` | Build | Yes | Useful for meta-programming debug |
| `m.display("count=%d", count)` | Sim | Yes | Creates `$display` — runs each sim cycle |
| `for i in range(N):` | Build | Yes | Unrolls — like `generate for` |
| `if PARAM > 4:` | Build | Yes | Conditional generation (Python int) |
| `m.if_(count > 4)` | Sim | Yes | Creates runtime `if` in hardware |
| `if count > 4:` | — | **Error** | `Expr.__bool__` raises `TypeError` |
| `count and reset` | — | **Error** | `Expr.__bool__` raises `TypeError` |
| `len(signal)` | — | **Error** | `Expr.__len__` raises `TypeError` |
| `for bit in signal:` | — | **Error** | `Expr.__iter__` raises `TypeError` |
| `[a, b, c]` as expr | — | **Error** | `_to_expr_node` rejects `list` |
| `3.14` as value | — | **Error** | `_to_expr_node` rejects `float` |

## Common Mistakes and Their Diagnostics

### Using Python `if` with a Signal

```python
# WRONG — Python evaluates the truthiness of the Expr object
with m.always(posedge(clk)):
    if count > 5:           # TypeError: Cannot use hardware expression as Python boolean
        count <<= 0

# CORRECT — creates a hardware if statement
with m.always(posedge(clk)):
    with m.if_(count > 5):
        count <<= 0
```

The `Expr.__bool__` method raises `TypeError` with the message:
*"Cannot use hardware expression as Python boolean. Use m.if_(expr) for conditional hardware logic."*

### Using Python `and`/`or`/`not`

```python
# WRONG — Python short-circuit operators call __bool__
with m.if_(a and b):       # TypeError

# CORRECT — use bitwise & for logical AND (or land/lor helpers)
with m.if_(a & b):         # Bitwise AND (works for 1-bit signals)
from veriforge.dsl import land, lor, lnot
with m.if_(land(a, b)):   # Logical AND: a && b
```

### Debug Output During Simulation

```python
# Build-time only — won't print during simulation
with m.always(posedge(clk)):
    print(f"count = {count}")   # Prints "count = Signal(count)" ONCE at build time

# Correct — creates $display that fires each clock edge during simulation
with m.always(posedge(clk)):
    m.display("count = %d", count)
```

### Python Loops for Hardware Generation

Python `for` loops are the DSL's equivalent of Verilog `generate for`.
They execute at build time and create multiple copies of hardware:

```python
# Creates 8 separate wires and assigns — like generate for
for i in range(8):
    w = m.wire(f"bit_{i}")
    m.assign(w, data[i])
```

This is correct and intentional. The loop runs once during construction,
producing 8 continuous assignments in the resulting module.

### Non-Translatable Python Code

Some Python code runs fine at build time but has no Verilog equivalent.
This is expected and useful for meta-programming:

```python
# Python dict for configuration — runs at build time, no Verilog output
config = {"width": 8, "depth": 256}
with Module("ram") as m:
    data = m.input("data", width=config["width"])
    mem = m.reg("mem", width=config["width"], depth=config["depth"])
```

The dict, string formatting, and arithmetic all execute in Python and
produce concrete values that feed into DSL declarations. The generated
Verilog has no trace of the Python data structures.

## Summary

| Concern | Answer |
|---------|--------|
| Can `print()` debug during sim? | No. Use `m.display()` for `$display`. |
| Can Python `if` control hardware? | No. Use `m.if_()`. `Expr.__bool__` catches this. |
| Can Python `for` generate hardware? | Yes. This is the generate mechanism. |
| What about Python lists/dicts? | Fine at build time for config. Rejected if passed as signal values. |
| What about `float`? | Rejected with *"use int instead"* message. |
| Is there sim-time Python callback? | Not yet. The `test_fn` runs before the event loop. |
