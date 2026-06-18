# DSL Composability Benefits

Things the Python DSL can do that plain Verilog cannot.

This isn't about syntax convenience — Python operator overloading is nice,
but it's just sugar. The real value is **compositional power**: patterns
where the DSL enables hardware construction techniques that have no
equivalent in Verilog 2005 (and are awkward even in SystemVerilog).

---

## 1. Per-Stage Callable Operations

Verilog's `generate for` can replicate *identical* hardware. It cannot
vary the *operation* at each stage.

```python
pixel_pipeline = pipeline("pixel_pipe", data_width=16, stages=[
    ("multiply by 2",    lambda x: x << 1),
    ("add bias 16",      lambda x: x + 16),
    ("saturate to 255",  lambda x: mux(x > 255, 255, x)),
])
```

Each lambda receives the previous stage's output signal and returns an
expression tree. The generator wraps each in its own `always @(posedge clk)`
block with valid propagation, reset logic, and optional enable/stall.

**Why Verilog can't do this:** There is no Verilog construct that accepts a
function pointer or expression template as a generate parameter. You'd have
to hand-code every stage, use `ifdef` chains, or resort to external text
templating. The pipeline generator in
[pipeline_generator.py](../examples/composability/pipeline_generator.py)
produces a different structural skeleton per call — not just different
parameter values, but different *logic* at each stage.

This pattern generalizes beyond pipelines. Any structure where you want
"N copies of a wrapper, each with a different inner operation" can use
callable parameterization:

```python
# Replicated functional units with different ALU ops
alu_ops = [
    ("add",  lambda a, b: a + b),
    ("sub",  lambda a, b: a - b),
    ("and",  lambda a, b: a & b),
    ("xor",  lambda a, b: a ^ b),
]
for i, (name, op) in enumerate(alu_ops):
    m.comment(f"Functional unit {i}: {name}")
    m.assign(results[i], op(a, b))
```

---

## 2. Single-Source-of-Truth Declarative Generation

A Python dictionary defines a register map. One generator function reads
that dictionary and produces the complete register file: declarations,
reset values, write decode, read mux, output ports.

```python
peripheral_regs = {
    "CTRL":     {"offset": 0x00, "default": 0x01},
    "STATUS":   {"offset": 0x04, "readonly": True},
    "TX_DATA":  {"offset": 0x08},
    "RX_DATA":  {"offset": 0x0C, "readonly": True},
}
periph = register_bank("periph_regs", peripheral_regs)
```

**Why Verilog can't do this:** Adding a register in a hand-written Verilog
register file means editing four separate places: the `reg` declaration,
the reset clause, the write-decode case branch, and the read-decode case
branch. Miss one and you have a bug. The declarative approach edits one
line in one dict. See
[register_bank.py](../examples/composability/register_bank.py).

This isn't just about saving keystrokes. It's about **eliminating a class
of bugs** (inconsistent register maps) that are a routine source of silicon
re-spins in real projects.

The same pattern applies to any structure with correlated parts: interrupt
controllers (status register + mask register + pending logic per IRQ source),
CSR files, configuration tables.

---

## 3. Design-Space Exploration

Generate multiple design variants, emit each, and compare — all in a single
Python script:

```python
for taps in [4, 8, 16, 32]:
    for width in [8, 16]:
        mod = fir_filter(num_taps=taps, data_width=width, coeff_width=width)
        verilog = emit_module(mod.build())
        stats = analyze_verilog(verilog)
        print(f"taps={taps:>2} width={width:>2} → {stats['lines']:>4} lines, "
              f"{stats['regs']:>3} regs, {stats['always_blocks']:>2} always blocks")
```

Output (from [design_explorer.py](../examples/composability/design_explorer.py)):

```
FIR Filter Design-Space Exploration
  Taps DataW CoeffW |  Lines  Regs  Wires  Always
  4      8      8   |    40     9     10       2
  4     16     16   |    40     9     10       2
  8     16     16   |    56    17     18       2
 16     16     16   |    88    33     34       2
 32     16     16   |   152    65     66       2
```

**Why Verilog can't do this:** Verilog can parameterize a single module, but
it cannot iterate over parameter combinations, elaborate multiple variants,
or compute comparative metrics within the language. You'd need external TCL
scripts, Makefiles, or GUI tools.

With the DSL, a parameter sweep is a for-loop. You can add cost functions
(estimated area, estimated timing), Pareto filtering, even optimization —
all in Python.

---

## 4. Structural Conditionals (Beyond `generate if`)

Verilog's `generate if` can conditionally include structural blocks, but
only based on parameter expressions. Python `if` statements at elaboration
time can include or exclude anything based on arbitrary logic.

```python
def single_port_ram(data_width, depth, sync_read=True, style="auto"):
    m = Module("single_port_ram")
    # ...
    if sync_read:
        rdata = m.output_reg("rdata", width=data_width)
        with m.always(posedge(clk)):
            with m.if_(re):
                rdata <<= mem[addr]
    else:
        rdata = m.output("rdata", width=data_width)
        m.assign(rdata, mem[addr])

    if style == "block":
        mem.attr("ram_style", "block")
    elif style == "distributed":
        mem.attr("ram_style", "distributed")
    return m
```

The `sync_read` parameter changes the **port type** (`output reg` vs
`output`), the **assignment kind** (sequential vs continuous), and the
**surrounding block structure** (always block vs assign). Verilog's
`generate if` cannot change a port declaration's type.

More powerful examples:

- Conditionally add ports based on feature flags
- Include/exclude entire sub-module instances
- Choose between different reset strategies (sync vs async)
- Generate debug-only logic (extra output ports, assertions) that disappears
  in production builds

```python
def my_module(debug=False):
    m = Module("my_mod")
    # ... normal design ...
    if debug:
        dbg_state = m.output("dbg_state", width=4)
        m.assign(dbg_state, state)
        dbg_count = m.output("dbg_count", width=32)
        m.assign(dbg_count, cycle_counter)
    return m
```

---

## 5. Interface Abstraction with Automatic Direction Flipping

Define a bus once. Bind it as master or slave and all port directions
are derived automatically:

```python
axi_s = axi_stream(data_width=32)

# Master: tvalid/tdata/tlast → output, tready → input
m_axis = producer.interface("m_axis", axi_s, role="master", reg=True)

# Slave: tvalid/tdata/tlast → input, tready → output
s_axis = consumer.interface("s_axis", axi_s, role="slave")
```

Connect the two through internal wires in a top module:

```python
axis = top.wire_interface("axis", axi_s)
top.instance("producer", "i_prod", ports={
    "clk": clk, **axis.port_map("m_axis")
})
top.instance("consumer", "i_cons", ports={
    "clk": clk, **axis.port_map("s_axis")
})
```

**Why Verilog can't do this:** Verilog 2005 has no interfaces. A 5-channel
AXI4-Lite bus has ~20 signals. Each must be declared manually in every
module that uses it, with the correct direction, correct width, and correct
naming prefix. Swap master/slave and you re-derive 20 port directions. The
DSL's `Interface` + `role=` does this automatically, and `port_map()`
handles prefix translation for instance connections.

SystemVerilog has `interface` with `modport`, but:
- Many FPGA tools have limited SV interface support
- SV interfaces can't be parameterized as flexibly (conditional signals,
  computed widths)
- SV modports can't add `output reg` vs `output wire` per-role

The DSL's interface factory pattern supports all of this:

```python
def axi_stream(data_width=8, tid_width=0, tuser_width=0):
    intf = (Interface("axi_stream")
        .signal("tvalid", src="master")
        .signal("tready", src="slave")
        .signal("tdata", width=data_width, src="master")
        .signal("tlast", src="master"))
    if tid_width > 0:
        intf.signal("tid", width=tid_width, src="master")
    if tuser_width > 0:
        intf.signal("tuser", width=tuser_width, src="master")
    return intf
```

---

## 6. Computed Widths and Derived Parameters

Python computes widths from structural parameters at elaboration time:

```python
addr_width = (depth - 1).bit_length() or 1     # FIFO, RAM
ptr_width = addr_width + 1                       # Wrap-around detection
product_width = a_width + b_width                # Multiplier output
acc_bits = product_width + ceil(log2(num_taps))  # FIR accumulation headroom
strb_width = data_width // 8                     # AXI write strobes
```

Verilog has `$clog2` for address widths, but nothing for general arithmetic
in port declarations or parameter expressions. You can't write
`parameter ACC_W = A_WIDTH + B_WIDTH + $clog2(NUM_TAPS)` and have it
work reliably across tools. In the DSL it's just Python math.

This matters for library code. A FIR filter factory that takes `data_width`,
`coeff_width`, and `num_taps` needs to compute the accumulator width to
avoid overflow. In Verilog, the user must compute this manually and pass
it as a parameter. In the DSL, the library does it correctly every time.

---

## 7. Programmatic Scaling

Python loops generate repetitive structures with per-instance customization:

```python
# 16-channel DMA: 4 registers per channel, computed addresses
for ch in range(16):
    base = 0x100 + ch * 0x10
    dma_regs[f"CH{ch}_SRC"]  = {"offset": base + 0x00}
    dma_regs[f"CH{ch}_DST"]  = {"offset": base + 0x04}
    dma_regs[f"CH{ch}_LEN"]  = {"offset": base + 0x08, "width": 16}
    dma_regs[f"CH{ch}_CTRL"] = {"offset": base + 0x0C, "width": 8}
```

16 channels × 4 registers = 64 registers from 4 lines of Python. Change
`range(16)` to `range(32)` and you get a 128-register DMA controller.

Verilog's `generate for` can replicate identical instances, but it can't:
- Compute address offsets per instance
- Vary register widths per instance
- Produce unique names based on loop index and context
- Mix generated registers into a hand-written parent structure

---

## 8. Module Introspection and Auto-Generation

The DSL builds a Python object model (AST) of the design, which can be
inspected programmatically. This enables tools that would require external
parsers in a Verilog-only flow:

**Auto testbench generation:**

```python
tb = generate_testbench(dut.build(), clock_period=10, reset_duration=20)
```

The generator introspects the module's port list, detects clock/reset
signals by naming convention, and produces a complete testbench — clock
toggle, reset sequence, VCD setup, timeout watchdog. See
[testbench.py](../src/veriforge/dsl/testbench.py).

**Potential extensions** (not yet implemented but enabled by the approach):

- Auto-generate bus functional models from interface definitions
- Generate documentation (port tables, register maps) from the same source
- Generate firmware header files (`#define REG_CTRL_OFFSET 0x00`) from the
  register map dict
- Lint/check the design before emitting Verilog (width mismatches, missing
  connections)

---

## 9. Cross-Cutting Concerns as Wrappers

Python functions can wrap modules to add cross-cutting behavior:

```python
def add_debug_ports(module_fn, signals_to_expose):
    """Wrap a module factory, adding debug output ports."""
    def wrapper(*args, **kwargs):
        m = module_fn(*args, **kwargs)
        for sig_name in signals_to_expose:
            sig = m._find_signal(sig_name)  # hypothetical
            dbg = m.output(f"dbg_{sig_name}", width=sig.width)
            m.assign(dbg, sig)
        return m
    return wrapper

def add_pipeline_valids(module_fn, stages):
    """Wrap a module, adding valid-chain tracking."""
    # ...
```

In Verilog, adding debug ports to a module means editing the module
definition — you can't "wrap" it. In SystemVerilog, `bind` can inject
monitors, but it can't add ports. The DSL's Python-object model allows
true structural decoration.

---

## 10. Reusable Library Composition

Library modules compose like function calls:

```python
# Build a system from library primitives
top = Module("audio_pipeline")
clk = top.input("clk")
rst = top.input("rst")
audio_in = top.input("audio_in", width=16)
audio_out = top.output("audio_out", width=16)

# Instantiate library components with different parameters
fifo_in = sync_fifo(data_width=16, depth=64, style="block")
fir = fir_filter(data_width=16, coeff_width=16, num_taps=32)
fifo_out = sync_fifo(data_width=16, depth=64, style="distributed")

# Wire them together using interfaces
# ...
```

Each library function returns a fully-configured `Module` with correct
widths, attributes, and reset logic. No copy-paste, no parameter
miscalculation, no forgotten synthesis attributes.

In Verilog, a "library" is a collection of `.v` files with `parameter`
declarations. The user must:
- Know which parameters exist and what values are legal
- Calculate derived widths manually
- Remember to add synthesis attributes
- Hope the parameters actually work in combination (untested corners)

The Python factory pattern enforces constraints and computes derived values
automatically.

---

## Summary

| Capability | Verilog 2005 | DSL |
|---|---|---|
| Different logic per generated stage | Impossible | Lambda/callable params |
| Single-source register map | Edit 4+ places per register | One dict entry |
| Design-space parameter sweep | External scripts required | Python for-loop |
| Conditional port types | `generate if` (limited) | Python `if` (unlimited) |
| Bus direction auto-flip | Manual (error-prone) | `role="master"/"slave"` |
| Computed accumulator widths | `$clog2` only | Full Python math |
| N-channel with per-channel config | `generate for` (identical only) | Python loop (any variation) |
| Auto testbench from module | External tool required | `generate_testbench()` |
| Structural decorators/wrappers | Not possible | Python function composition |
| Library with enforced constraints | Parameter docs + hope | Factory functions |

The common theme: **Verilog is a description language; the DSL is a
construction language.** Verilog describes hardware textually. The DSL
*builds* hardware programmatically, with all the compositional power of
a general-purpose programming language available at elaboration time.
