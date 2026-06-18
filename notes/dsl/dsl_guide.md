# Hardware Construction DSL Guide

The DSL lets you build Verilog modules using Python expressions and context managers. Instead of writing Verilog text, you construct a structural/behavioral model that can be emitted to Verilog or simulated directly.

**Source:** `src/veriforge/dsl/builder.py` (~1480 lines), `src/veriforge/dsl/interface.py` (~170 lines)

## Quick Start

```python
from veriforge.dsl import Module, posedge
from veriforge.codegen.verilog_emitter import emit_module

with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    count = m.output_reg("count", width=8)

    with m.always(posedge(clk)):
        with m.if_(rst):
            count <<= 0
        with m.else_():
            count <<= count + 1

print(emit_module(m.build()))
```

Output:

```verilog
module counter(
    input clk,
    input rst,
    output reg [7:0] count
);

always @(posedge clk) begin
    if (rst)
        count <= 0;
    else
        count <= count + 1;
end

endmodule
```

## Core Concepts

### Module Builder

`Module(name)` is the top-level builder. It can be used as a context manager (`with Module(...) as m:`) or directly (`m = Module("name")`). Call `m.build()` at the end to get an immutable model `Module` object.

### Signal and Expr

Every declaration method returns a `Signal` (subclass of `Expr`). `Signal` objects capture hardware expressions via Python operator overloading — no computation happens at build time, only expression tree construction.

```python
a = m.input("a", width=8)
b = m.input("b", width=8)
result = (a + b) & 0xFF   # Expr wrapping BinaryOp("&", BinaryOp("+", ...), Literal(255))
```

### Assignments

Two assignment types exist:

| Syntax | Verilog Equivalent | Use Case |
|--------|-------------------|----------|
| `signal <<= expr` | `signal <= expr;` | Non-blocking (sequential `always` blocks) |
| `signal @= expr` | `signal = expr;` | Blocking (combinational `always` blocks) |
| `m.assign(lhs, rhs)` | `assign lhs = rhs;` | Continuous assignment (outside always) |
| `m.assign_nb(lhs, rhs)` | `lhs <= rhs;` | Non-blocking (method form) |
| `m.assign_b(lhs, rhs)` | `lhs = rhs;` | Blocking (method form) |

> **Note:** `signal.set(expr)` is a deprecated alias for `signal @= expr` and still works.
> `m.assign_nonblocking` / `m.assign_blocking` are long-form aliases for `m.assign_nb` / `m.assign_b`.

## Port and Signal Declarations

```python
# Ports
clk   = m.input("clk")                      # input clk
data  = m.input("data", width=8)             # input [7:0] data
sdata = m.input("sdata", width=8, signed=True)  # input signed [7:0] sdata
y     = m.output("y", width=4)               # output [3:0] y
q     = m.output_reg("q", width=8)           # output reg [7:0] q  (also creates a variable)
q0    = m.output_reg("q0", width=8, init=0)  # output reg [7:0] q0 = 0
bus   = m.inout("bus", width=8)              # inout [7:0] bus

# Internal signals
w = m.wire("w", width=16)                    # wire [15:0] w
w0 = m.wire("w0", width=8, init=0)           # wire [7:0] w0 = 0
r = m.reg("state", width=4)                  # reg [3:0] state
r0 = m.reg("cnt", width=8, init=0)           # reg [7:0] cnt = 0
i = m.integer("i")                           # integer i

# Memory arrays
mem = m.reg("mem", width=8, depth=256)       # reg [7:0] mem [0:255]
rom = m.wire("rom", width=32, depth=1024)    # wire [31:0] rom [0:1023]

# Parameters
W = m.parameter("WIDTH", default=8)          # parameter WIDTH = 8
H = m.localparam("HALF", value=4)            # localparam HALF = 4

# Parameterized width — width argument accepts Signal/Expr
data = m.input("data", width=W)              # input [WIDTH-1:0] data
```

## Expression Operators

All standard Verilog operators are available through Python overloading:

```python
# Arithmetic
a + b       a - b       a * b       a // b      a % b       a ** b

# Bitwise
a & b       a | b       a ^ b       ~a

# Logical shifts (via Python operators)
a << 2      a >> 1

# Arithmetic shifts (helpers — Python has no <<< / >>> syntax)
ashl(a, 2)  ashr(a, 1)   # a <<< 2, a >>> 1

# Comparison — return Expr, not bool
a == b      a != b      a < b       a <= b      a > b       a >= b

# Unary
-a          ~a

# Bit/range/part selection
data[3]                 # Bit select:  data[3]
data[7:4]               # Range select: data[7:4]
data.part_select(i, 8)      # Part select: data[i +: 8]
data.part_select_down(i, 8)  # Part select: data[i -: 8]
```

**Important:** Comparison operators return `Expr` objects, not Python booleans. Using a `Signal` in an `if` statement or `bool()` raises `TypeError` — use `m.if_(expr)` instead.

Reverse operators work too: `1 + a` produces `BinaryOp("+", Literal(1), Identifier("a"))`.

## Helper Functions

These are top-level functions imported from `veriforge.dsl`:

```python
from veriforge.dsl import cat, rep, mux, land, lor, lnot
from veriforge.dsl import reduce_and, reduce_or, reduce_xor
from veriforge.dsl import ashl, ashr, case_eq, case_ne
from veriforge.dsl import clog2, signed, unsigned, sim_time

cat(a, b, c)        # Concatenation: {a, b, c}
rep(4, a)            # Replication:   {4{a}}
mux(sel, t, f)       # Ternary:       sel ? t : f

land(a, b)           # Logical AND:   a && b
lor(a, b)            # Logical OR:    a || b
lnot(a)              # Logical NOT:   !a

reduce_and(a)        # Reduction AND: &a
reduce_or(a)         # Reduction OR:  |a
reduce_xor(a)        # Reduction XOR: ^a

# Operators not expressible via Python's own operator overloading:
ashl(a, b)           # Arithmetic left shift:  a <<< b
ashr(a, b)           # Arithmetic right shift: a >>> b
case_eq(a, b)        # Case equality:          a === b  (four-state)
case_ne(a, b)        # Case inequality:        a !== b  (four-state)

# System function helpers:
clog2(a)             # $clog2(a)
signed(a)            # $signed(a)
unsigned(a)          # $unsigned(a)
sim_time()           # $time
```

## Behavioral Blocks

### Always Blocks

```python
# Sequential (posedge/negedge) → non-blocking assignments
with m.always(posedge(clk)):
    q <<= d

# Sequential with async reset
with m.always(posedge(clk), negedge(rst_n)):
    with m.if_(~rst_n):
        q <<= 0
    with m.else_():
        q <<= d

# Combinational (empty sensitivity = @(*)) → blocking assignments
with m.always():
    y @= a & b

# Combinational with explicit level sensitivity
with m.always(a, b):
    y @= a & b
```

Sensitivity classification is automatic:
- Edge triggers only → `SensitivityType.SEQUENTIAL`
- Level triggers or empty → `SensitivityType.COMBINATIONAL`

### Initial Blocks

```python
with m.initial():
    q @= 0
```

## System Tasks

System tasks (`$display`, `$finish`, etc.) are available inside `always` and `initial` blocks:

```python
with m.initial():
    m.display("Hello, World!")        # $display("Hello, World!");
    m.display("count = %d", count)    # $display("count = %d", count);
    m.write("no newline")             # $write("no newline");
    m.monitor("sig=%b", sig)          # $monitor("sig=%b", sig);
    m.finish()                        # $finish;
    m.stop()                          # $stop;
```

### Memory Initialization

```python
mem = m.reg("mem", width=8, depth=256)
with m.initial():
    m.readmemh("data.hex", mem)       # $readmemh("data.hex", mem);
    m.readmemb("data.bin", mem)       # $readmemb("data.bin", mem);
```

### String Literals

Python strings are automatically converted to Verilog string literals in expression
contexts. This is primarily used as arguments to system tasks:

```python
m.display("count = %d", count)   # "count = %d" becomes StringLiteral
```

## Delay and Event Control

Delays and event waits are essential for testbench code. They work inside
`always` and `initial` blocks.

### Standalone Delay

```python
with m.initial():
    m.assign(data, 0)
    m.delay(10)             # #10
    m.assign(data, 0xFF)
    m.delay(100)            # #100
    m.finish()
```

### Delay with Body

Use `with` to wrap statements under a delay:

```python
with m.initial():
    with m.delay(50):       # #50 data = 8'hAA;
        m.assign(data, 0xAA)
```

### Edge Waits

```python
with m.initial():
    m.wait_posedge(clk)     # @(posedge clk)
    m.wait_negedge(rst)     # @(negedge rst)
```

### Event Control with Body

Use `wait_event()` with `posedge()`/`negedge()` for event-controlled blocks:

```python
with m.initial():
    with m.wait_event(posedge(clk)):    # @(posedge clk) data <= 1;
        data <<= 1
```

### Complete Testbench Pattern

```python
with Module("tb") as m:
    clk  = m.input("clk")
    data = m.output_reg("data", width=8)

    with m.initial():
        m.assign(data, 0)
        m.delay(10)
        m.assign(data, 0xFF)
        m.wait_posedge(clk)
        m.display("data = %h", data)
        m.delay(100)
        m.finish()
```

### If / Elif / Else

```python
with m.always(posedge(clk)):
    with m.if_(sel == 0):
        q <<= a
    with m.elif_(sel == 1):
        q <<= b
    with m.elif_(sel == 2):
        q <<= c
    with m.else_():
        q <<= d
```

`elif_()` and `else_()` must immediately follow an `if_()` or `elif_()` block. Nesting is supported:

```python
with m.if_(a):
    with m.if_(b):
        q <<= 1
```

### Case Statements

```python
with m.always():
    with m.case(sel) as c:
        with c.when(0):
            y @= a
        with c.when(1):
            y @= b
        with c.when(2, 3):     # Multiple values in one arm
            y @= c_val
        with c.default():
            y @= 0
```

Variants: `m.casex(expr)` and `m.casez(expr)`.

## Continuous Assignments

```python
m.assign(y, a + b)      # assign y = a + b;
m.assign(y, 0)          # assign y = 0;
m.assign(y, mux(sel, a, b))  # assign y = sel ? a : b;
```

## Module Instantiation

Use `m.instance()` to instantiate sub-modules with named port and parameter connections:

```python
from veriforge.dsl import Module, posedge
from veriforge.codegen.verilog_emitter import emit_module

# Define a reusable counter module
with Module("counter") as counter_mod:
    clk   = counter_mod.input("clk")
    rst   = counter_mod.input("rst")
    count = counter_mod.output_reg("count", width=8)
    with counter_mod.always(posedge(clk)):
        with counter_mod.if_(rst):
            count <<= 0
        with counter_mod.else_():
            count <<= count + 1

# Instantiate it inside a top-level module
with Module("top") as top:
    sys_clk = top.input("sys_clk")
    sys_rst = top.input("sys_rst")
    cnt     = top.wire("cnt", width=8)

    top.instance("counter", "u_counter", ports={
        "clk":   sys_clk,
        "rst":   sys_rst,
        "count": cnt,
    })

print(emit_module(top.build()))
```

Output:

```verilog
module top(
    input sys_clk,
    input sys_rst
);

wire [7:0] cnt;

counter u_counter(
    .clk(sys_clk),
    .rst(sys_rst),
    .count(cnt)
);

endmodule
```

### Parameter Overrides

Pass parameter values with the `parameters` argument:

```python
top.instance("counter", "u_wide_counter",
    ports={"clk": sys_clk, "rst": sys_rst, "count": wide_cnt},
    parameters={"WIDTH": 16})
```

Emits: `counter #(.WIDTH(16)) u_wide_counter(.clk(sys_clk), ...);`

### Unconnected Ports

Use `None` for ports that should be left unconnected:

```python
top.instance("counter", "u_cnt",
    ports={"clk": sys_clk, "rst": sys_rst, "count": None})
```

Emits: `.count()`

### Multiple Instances

Python loops work naturally for arrays of instances:

```python
with Module("top") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    counts = [m.wire(f"cnt_{i}", width=8) for i in range(4)]

    for i in range(4):
        m.instance("counter", f"u_cnt_{i}", ports={
            "clk":   clk,
            "rst":   rst,
            "count": counts[i],
        })
```

### Method Signature

```python
m.instance(
    module_name: str,        # Module type to instantiate
    instance_name: str,      # Instance identifier
    ports: dict | None,      # Named port connections {port_name: signal_or_expr_or_None}
    parameters: dict | None, # Named parameter overrides {param_name: value}
)
```

## LHS Targets

Bit selects, range selects, and concatenations work as assignment targets:

```python
with m.always(posedge(clk)):
    data[3] <<= 1          # Bit select LHS
    data[7:0] <<= 0xFF     # Range select LHS
    cat(a, b) <<= c        # Concatenation LHS: {a, b} <= c
```

## Comments

Comments attach to declarations and blocks and appear in the emitted Verilog.

### Signal Comments

Call `.comment()` on any signal. Port comments become trailing (`// text` on
the same line); wire/reg comments become leading (line above):

```python
clk   = m.input("clk").comment("100 MHz system clock")
rst   = m.input("rst").comment("Active-high synchronous reset")
count = m.output_reg("count", width=8).comment("Free-running counter")
state = m.reg("state", width=2).comment("FSM state")
bus   = m.wire("bus", width=8).comment("Internal data bus")
```

Emits:

```verilog
module counter (
    input clk,  // 100 MHz system clock
    input rst,  // Active-high synchronous reset
    output reg [7:0] count  // Free-running counter
);

    // FSM state
    reg [1:0] state;
    // Internal data bus
    wire [7:0] bus;
```

Multiple `.comment()` calls add multiple lines. Chaining returns `self`.

### Block and Assign Comments

Pass `comment=` to `m.always()`, `m.initial()`, or `m.assign()`:

```python
with m.always(posedge(clk), comment="State register"):
    ...

with m.initial(comment="Reset values"):
    q @= 0

m.assign(y, a + b, comment="Adder output")
```

Emits:

```verilog
    // State register
    always @(posedge clk) ...

    // Reset values
    initial ...

    // Adder output
    assign y = a + b;
```

### Standalone Comments

Use `m.comment()` to insert free-standing comments at the current position.
The comment appears above the next declaration, assignment, or block:

```python
m = Module("adder_tree")
a = [m.input(f"a{i}", width=8) for i in range(4)]
s01   = m.wire("s01", width=9)
s23   = m.wire("s23", width=9)
total = m.output("total", width=10)

m.comment("Stage 1: partial sums")
m.assign(s01, a[0] + a[1])
m.assign(s23, a[2] + a[3])

m.comment("Stage 2: final sum")
m.assign(total, s01 + s23)
```

Emits:

```verilog
    // Stage 1: partial sums
    assign s01 = a0 + a1;
    assign s23 = a2 + a3;

    // Stage 2: final sum
    assign total = s01 + s23;
```

`m.comment()` works before any item — ports, wires, regs, assigns, always
blocks, initial blocks, and instances.  The comment is consumed by the next
item added and won't appear twice.  It can be combined with
`comment=` on `m.assign()` / `m.always()` (standalone comment comes first).

Pass `block=True` for block comments:

```python
m.comment("Copyright 2026 ACME Corp", block=True)
m.assign(y, a + b)
```

Emits:

```verilog
    /* Copyright 2026 ACME Corp */
    assign y = a + b;
```

## Synthesis Attributes

Call `.attr()` on any signal to attach synthesis attributes. They appear
as `(* ... *)` above the declaration in emitted Verilog:

```python
state = m.reg("state", width=3).attr("fsm_encoding", "one_hot")
clk_buf = m.wire("clk_buf").attr("dont_touch")
data = m.input("data", width=8).attr("io_standard", "LVCMOS33")
```

Emits:

```verilog
    (* fsm_encoding = "one_hot" *)
    reg [2:0] state;
    (* dont_touch *)
    wire clk_buf;
```

For ports, the attribute appears on the line above the port in the port list.

Multiple `.attr()` calls combine into a single `(* ... *)` line. Chaining
with `.comment()` works:

```python
state = m.reg("state", width=3).attr("fsm_encoding", "one_hot").comment("FSM state")
```

## Interfaces (Signal Bus Grouping)

The `Interface` class groups related signals into reusable bus templates,
similar to SystemVerilog `interface` + `modport`. Since we emit Verilog 2005,
the interface expands into flat ports with a naming prefix.

### Defining an Interface

```python
from veriforge.dsl import Interface

axi_stream = (Interface("axi_stream")
    .signal("tvalid", src="master")
    .signal("tready", src="slave")
    .signal("tdata", width=8, src="master")
    .signal("tlast", src="master"))
```

Each signal declares which *role* drives it (`src="master"` or `src="slave"`).
This mirrors SystemVerilog modport semantics — when you bind with a role, signals
whose `src` matches get output ports, others get input ports.

### Binding to a Module

Use `m.interface()` to create prefixed ports:

```python
m = Module("producer")
clk = m.input("clk")
m_axis = m.interface("m_axis", axi_stream, role="master")
```

Emits:

```verilog
module producer(
    input clk,
    output m_axis_tvalid,
    input m_axis_tready,
    output [7:0] m_axis_tdata,
    output m_axis_tlast
);
```

Swap to `role="slave"` and all directions flip:

```python
m = Module("consumer")
s_axis = m.interface("s_axis", axi_stream, role="slave")
# input s_axis_tvalid, output s_axis_tready, input [7:0] s_axis_tdata, ...
```

### Registered Outputs

Pass `reg=True` to use `output_reg` for all driven signals:

```python
m_axis = m.interface("m_axis", axi_stream, role="master", reg=True)
# output reg m_axis_tvalid, output reg [7:0] m_axis_tdata, ...
```

### Accessing Individual Signals

The returned `BoundInterface` provides dotted access:

```python
with m.always(posedge(clk)):
    m_axis.tvalid <<= 1
    m_axis.tdata  <<= count
    m_axis.tlast  <<= count == 255
```

All standard DSL operations work: bit selects, assignments, `.comment()`,
`.attr()`, etc.

### Internal Bus Wires

Use `m.wire_interface()` for internal wires (no role needed):

```python
top = Module("top")
clk = top.input("clk")
axis = top.wire_interface("axis", axi_stream)
# wire axis_tvalid, wire axis_tready, wire [7:0] axis_tdata, wire axis_tlast
```

### Instance Connections with `port_map()`

The `port_map()` method returns a dict suitable for `**` expansion:

```python
top.instance("producer", "i_prod", ports={
    "clk": clk,
    "rst": rst,
    **axis.port_map("m_axis"),   # {"m_axis_tvalid": axis_tvalid, ...}
})
top.instance("consumer", "i_cons", ports={
    "clk": clk,
    **axis.port_map("s_axis"),   # {"s_axis_tvalid": axis_tvalid, ...}
})
```

When called without arguments, `port_map()` uses its own prefix.
Pass a different prefix to match the target instance's port names.

### Parameterized Interfaces

Use a factory function for parameterized bus widths:

```python
def axi_stream(data_width=8):
    return (Interface("axi_stream")
        .signal("tvalid", src="master")
        .signal("tready", src="slave")
        .signal("tdata", width=data_width, src="master")
        .signal("tlast", src="master"))

m_axis = m.interface("m_axis", axi_stream(data_width=64), role="master")
# output [63:0] m_axis_tdata
```

### Complete Example: AXI-Stream System

```python
from veriforge.dsl import Interface, Module, posedge
from veriforge.codegen.verilog_emitter import emit_module

# Define bus template
axi_s = (Interface("axi_stream")
    .signal("tvalid", src="master")
    .signal("tready", src="slave")
    .signal("tdata", width=8, src="master")
    .signal("tlast", src="master"))

# Producer
prod = Module("axi_producer")
clk = prod.input("clk")
rst = prod.input("rst")
m_axis = prod.interface("m_axis", axi_s, role="master", reg=True)
cnt = prod.reg("cnt", width=8)
with prod.always(posedge(clk)):
    with prod.if_(rst):
        m_axis.tvalid <<= 0
        m_axis.tdata  <<= 0
        cnt <<= 0
    with prod.else_():
        m_axis.tvalid <<= 1
        m_axis.tdata  <<= cnt
        m_axis.tlast  <<= cnt == 255
        cnt <<= cnt + 1

# Consumer
cons = Module("axi_consumer")
clk_c = cons.input("clk")
s_axis = cons.interface("s_axis", axi_s, role="slave")
cons.assign(s_axis.tready, 1)

# Top-level wiring
top = Module("top")
clk_t = top.input("clk")
rst_t = top.input("rst")
axis = top.wire_interface("axis", axi_s)
top.instance("axi_producer", "i_prod", ports={
    "clk": clk_t, "rst": rst_t,
    **axis.port_map("m_axis"),
})
top.instance("axi_consumer", "i_cons", ports={
    "clk": clk_t,
    **axis.port_map("s_axis"),
})

for mod in [prod, cons, top]:
    print(emit_module(mod.build()))
    print()
```

## Python-Powered Generation

Since the DSL is plain Python, loops and functions replace Verilog's `generate`:

```python
# Unrolled inverter array
m = Module("inv_array")
inputs  = [m.input(f"in_{i}") for i in range(4)]
outputs = [m.output(f"out_{i}") for i in range(4)]
for i in range(4):
    m.assign(outputs[i], ~inputs[i])

# Adder tree
m = Module("adder_tree")
a = [m.input(f"a{i}", width=8) for i in range(4)]
s01   = m.wire("s01", width=9)
s23   = m.wire("s23", width=9)
total = m.output("total", width=10)
m.assign(s01, a[0] + a[1])
m.assign(s23, a[2] + a[3])
m.assign(total, s01 + s23)
```

## Emission

`m.build()` returns a `veriforge.model.design.Module` object. Pass it to the emitter:

```python
from veriforge.codegen.verilog_emitter import emit_module

module = m.build()
verilog_text = emit_module(module)
print(verilog_text)
```

## Simulation

DSL-built modules can be simulated directly without emitting Verilog:

```python
from veriforge.dsl import Module, posedge
from veriforge.sim import Simulator, Clock

# Build a counter
with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    count = m.output_reg("count", width=8)
    with m.always(posedge(clk)):
        with m.if_(rst):
            count <<= 0
        with m.else_():
            count <<= count + 1

module = m.build()

# Simulate
sim = Simulator(module)
sim.fork(Clock(sim.signal("clk"), period=10))

def test(s):
    s.drive("rst", 1)             # Assert reset

sim.run(test, max_time=5)
assert sim.read("count") == 0    # Counter held at 0

# Combinational example — no clock needed
m2 = Module("adder")
a = m2.input("a", width=8)
b = m2.input("b", width=8)
s = m2.output("sum", width=9)
m2.assign(s, a + b)

sim2 = Simulator(m2.build())
sim2.drive("a", 10)
sim2.drive("b", 20)
sim2.run(lambda s: None, max_time=100)
assert sim2.read("sum") == 30
```

### Simulator API Summary

| Method | Description |
|--------|-------------|
| `Simulator(module)` | Create simulator, elaborate module |
| `sim.signal(name)` | Get a `SignalHandle` for a named signal |
| `sim.drive(name, value)` | Drive a signal by name |
| `sim.read(name)` | Read current signal value |
| `sim.fork(Clock(...))` | Start a clock generator |
| `sim.run(test_fn, max_time=N)` | Run simulation with optional test setup function |
| `sim.time` | Current simulation time |

## Complete Example: 4-to-1 Mux

```python
from veriforge.dsl import Module
from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim import Simulator

with Module("mux4") as m:
    sel = m.input("sel", width=2)
    a = m.input("a", width=8)
    b = m.input("b", width=8)
    c = m.input("c", width=8)
    d = m.input("d", width=8)
    y = m.output_reg("y", width=8)

    with m.always():
        with m.case(sel) as cs:
            with cs.when(0):
                y @= a
            with cs.when(1):
                y @= b
            with cs.when(2):
                y @= c
            with cs.default():
                y @= d

# Emit
print(emit_module(m.build()))

# Simulate
sim = Simulator(m.build())
sim.drive("a", 42)
sim.drive("b", 99)
sim.drive("sel", 1)
sim.run(lambda s: None, max_time=100)
assert sim.read("y") == 99
```

## Error Handling

The DSL raises clear errors for common mistakes:

- `signal <<= expr` **outside** an always/initial block → `RuntimeError`
- `signal @= expr` **outside** an always/initial block → `RuntimeError`
- `signal.set(expr)` **outside** an always/initial block → `RuntimeError` (deprecated alias for `@=`)
- `bool(signal)` → `TypeError` (use `m.if_(signal)` instead)
- `m.else_()` not immediately after `m.if_()` → `RuntimeError`
- `m.build()` with unclosed blocks → `RuntimeError`
- `m.display()` / `m.finish()` / `m.delay()` outside always/initial → `RuntimeError`
- `m.wait_posedge()` / `m.wait_negedge()` / `m.wait_event()` outside always/initial → `RuntimeError`

## Exports

Everything needed is in one import:

```python
from veriforge.dsl import (
    BoundInterface,  # Interface bound to a module
    Interface,       # Bus/interface template
    Module,          # Module builder
    Signal,          # Named signal proxy
    Expr,            # Expression proxy
    posedge,         # posedge sensitivity
    negedge,         # negedge sensitivity
    cat,             # Concatenation: {a, b, c}
    rep,             # Replication: {4{a}}
    mux,             # Ternary mux: sel ? t : f
    land,            # Logical AND: a && b
    lor,             # Logical OR:  a || b
    lnot,            # Logical NOT: !a
    reduce_and,      # Reduction AND: &a
    reduce_or,       # Reduction OR:  |a
    reduce_xor,      # Reduction XOR: ^a
    ashl,            # Arithmetic left shift:  a <<< b
    ashr,            # Arithmetic right shift: a >>> b
    case_eq,         # Case equality:   a === b
    case_ne,         # Case inequality: a !== b
    clog2,           # System function: $clog2(a)
    signed,          # System function: $signed(a)
    unsigned,        # System function: $unsigned(a)
    sim_time,        # System function: $time
)
```



## Writing a Python testbench

The `veriforge.sim.bench` package provides a transaction-level
testbench DSL on top of the Python simulator. Given a parsed module it
discovers clocks, resets, and bus interfaces (AXI-Stream, AXI-Lite),
groups them into clock *domains*, and exposes high-level
`put` / `get` / `expect` primitives that step the simulator on the
appropriate clock.

### Quick start

```python
from veriforge.sim.bench import Testbench

bench = Testbench(dut_module)
with bench.run():
    bench.reset_all()
    bench.iface("m_axis").put([0x11, 0x22, 0x33])
    frame = bench.iface("s_axis").get(timeout=200)
    assert list(frame.data) == [0x11, 0x22, 0x33]
```

A complete runnable example lives at
`examples/python_testbench/axi_stream_loopback.py` and demonstrates
multi-domain operation, payload variants (`list[int]` / `bytes`),
`expect`, and `BenchTimeoutError`.

### Plan inference

`Testbench(module)` calls `build_plan(module, overrides)` which returns
a `TestbenchPlan` describing every domain and interface. Use
`bench.plan.summary()` to print a human-readable view including the
*reason* each binding was made (`naming`, `override`, `default`).

### Overrides

When the heuristics pick the wrong domain or a clock period needs
fixing, supply `PlannerOverrides`:

```python
from veriforge.sim.bench import PlannerOverrides, Testbench

overrides = PlannerOverrides(
    iface_domains={"b_axis_in": "bclk", "b_axis_out": "bclk"},
    clock_periods={"bclk": 8},
)
bench = Testbench(dut, overrides=overrides)
```

A plain `dict` with the same keys (`iface_domains`, `clock_periods`,
`domain_aliases`) is also accepted.

### Proxy API (`bench.iface(prefix)`)

The proxy returned by `iface` is **role-inverted**: a DUT *slave* AXIS
bundle is exposed as an `AXIStreamSource` (`put`); a DUT *master* AXIS
bundle is exposed as an `AXIStreamSink` (`get` / `expect` /
`wait_drain`). Calling the wrong-direction method raises `RuntimeError`.

| Method | Source/Sink | Purpose |
|--------|-------------|---------|
| `put(data, last=True)` | source | Drive a frame onto the bus. |
| `put_frame(frame)` | source | Drive an `AXIStreamFrame`. |
| `wait_drain(timeout=None)` | source | Block until all queued frames are accepted. |
| `get(timeout=None)` | sink | Pop the next received frame; raises `BenchTimeoutError` if none arrives within `timeout` cycles of *this* domain. |
| `expect(payload, ...)` | sink | `get` + assertion-style compare. |
| `pending()` | sink | Number of received-but-not-yet-popped frames. |

### AXI-Lite

`AXILiteProxy` exposes `write(addr, data, strb=...)` and
`read(addr) -> data` for DUT-slave AXI-Lite interfaces. Note: the
underlying `AXILiteMaster` runs the simulator on its own clock during
a transaction, so other domains do not advance while AXI-Lite is in
flight.

### Multi-domain stepping

`Testbench` always uses `MultiDomainRunner` internally (even with a
single domain), giving uniform stepping semantics. `timeout` on sink
methods is measured in **rising edges of that interface's domain
clock** — not wall-clock simulator time.

### Limitations

* DSL `Module` objects with only combinational `assign` statements
  produce no clocks; the planner raises `NoDomainError`. Use parsed
  Verilog containing an `always @(posedge clk ...)` block, or add a
  small heartbeat counter to anchor the clock.
* `AXILiteProxy` currently only supports DUT-slave role.
* Engine-native lowering (Phase 9) is not yet wired in; bench tests
  always run on the reference engine.
