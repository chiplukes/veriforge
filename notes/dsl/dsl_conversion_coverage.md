# Verilog-to-DSL Conversion Coverage

This page tracks the current `veriforge.convert.to_dsl` coverage and gaps.
It complements `notes\dsl\dsl_coverage.md`, which focuses on the DSL builder
itself.

The converter is intentionally conservative: when it cannot produce equivalent
DSL code, it emits an `UNSUPPORTED` comment rather than silently dropping the
construct.

## Current supported conversion areas

| Area | Current converter behavior |
| --- | --- |
| Modules | Emits a `Module(...)` builder and final `module = m.build()`. |
| Ports | Converts common input, output, output-reg, and inout declarations. |
| Nets and regs | Converts common wire/reg declarations, widths, signedness, initial values, and simple memory depths. |
| Parameters and localparams | Converts common value and width forms. |
| Instances | Converts named instance connections and parameter dictionaries. |
| Assignments | Converts continuous, blocking, and nonblocking assignments for supported expression forms. |
| Always and initial blocks | Converts common sensitivity lists and statement bodies. |
| If/else and case | Converts common conditional and case structures. |
| Delay and event controls | Converts to `m.delay(...)` and `m.wait_event(...)` forms. |
| Common system tasks | Converts `$display`, `$write`, `$monitor`, `$finish`, `$stop`, `$readmemh`, and `$readmemb`. |
| Common system functions | Converts `$time`, `$clog2`, `$signed`, and `$unsigned` to explicit DSL helpers. |
| Packages and interfaces | Has dedicated package/interface conversion paths. |

## Expression gaps

| Construct | Current behavior | Suggested next step |
| --- | --- | --- |
| Binary XNOR: `~^`, `^~` | Lowered to bitwise NOT of XOR | Keep this lowering unless later width-specific DSL helpers become necessary. |
| Arithmetic shifts: `<<<`, `>>>` | Converted via explicit `ashl(...)` / `ashr(...)` helpers | Keep helper-based lowering so signed shift intent stays explicit in generated DSL. |
| Case equality: `===`, `!==` | Converted via explicit `case_eq(...)` / `case_ne(...)` helpers | Keep helper-based lowering so four-state intent stays explicit in generated DSL. |
| Reduction NAND/NOR/XNOR: `~&`, `~|`, `~^` | Lowered to bitwise NOT of `reduce_and/or/xor(...)` | Keep this lowering unless later one-bit helper APIs are added. |
| Common system functions: `$time`, `$clog2`, `$signed`, `$unsigned` | Converted via explicit `sim_time()`, `clog2(...)`, `signed(...)`, and `unsigned(...)` helpers | Extend this explicit-helper pattern only for stable, common builtins. |
| Unrecognized expression nodes | Emits `UNSUPPORTED expression: <type>` | Add targeted tests before adding conversions. |

## Statement gaps

| Construct | Current behavior | Suggested next step |
| --- | --- | --- |
| `while` | Emits `UNSUPPORTED: while loop` | Decide whether to model as Python control flow or leave manual. |
| `forever` | Emits `UNSUPPORTED: forever loop` | Usually testbench-oriented; likely manual unless DSL adds a direct helper. |
| `repeat` | Emits `UNSUPPORTED: repeat loop` | Could map to Python `for` for constant counts if safe. |
| `fork` / `join` | Emits `UNSUPPORTED: fork/join block` and attempts child statement conversion | Needs explicit parallel testbench DSL semantics before conversion. |
| `wait` | Emits `UNSUPPORTED: wait(...)` | Could map if DSL wait semantics become explicit. |
| `disable` | Emits `UNSUPPORTED: disable ...` | Low priority; control-flow semantics are nontrivial. |
| Event trigger `-> event` | Emits `UNSUPPORTED: -> ...` | Needs event object semantics in DSL. |
| Task enable | Emits `UNSUPPORTED: task(...)` | Depends on DSL task/function policy. |
| Unknown system tasks | Emits `UNSUPPORTED: $task(...)` | Add by usage frequency. |

## Module-level gaps

| Construct | Current behavior | Suggested next step |
| --- | --- | --- |
| Function declarations | Emits `UNSUPPORTED: function ...` | Prefer Python expression helpers; manual rewrite patterns are documented below. |
| Task declarations | Emits `UNSUPPORTED: task ...` | Prefer Python builder helpers; manual rewrite patterns are documented below. |
| Generate blocks | Emits `UNSUPPORTED: generate block (...)` | Prefer Python elaboration-time control flow; manual rewrite patterns are documented below. |
| Specify blocks | Emits `UNSUPPORTED: specify block` | Keep out of scope unless specify timing becomes a project target. |
| Real, realtime, time, event variables | Emits `UNSUPPORTED: <kind> <name>` | Low priority unless DSL gains explicit support. |

## Manual conversion patterns

The converter intentionally leaves some Verilog constructs as `UNSUPPORTED`
comments because the DSL treats them as Python-side structure instead of as
first-class model nodes. The recommended manual translations are:

### Function declarations -> Python expression helpers

Use a Python function that returns a DSL expression, then call that helper from
assignments or procedural code.

**Verilog**

```verilog
function [7:0] pick;
    input sel;
    input [7:0] a, b;
    begin
        pick = sel ? a : b;
    end
endfunction

assign y = pick(sel, a, b);
```

**DSL**

```python
def pick(sel, a, b):
    return mux(sel, a, b)

m.assign(y, pick(sel, a, b))
```

Use this pattern when the Verilog function is combinational and side-effect
free. If the function body is really just expression composition, keep it as a
Python expression helper rather than trying to model a Verilog function node.

### Task declarations -> Python builder helpers

Use a Python function that emits DSL statements through the existing builder
surface.

**Verilog**

```verilog
task clear_q;
    begin
        q <= 0;
        valid <= 0;
    end
endtask
```

**DSL**

```python
def clear_q(q, valid):
    q <<= 0
    valid <<= 0
```

Then call that helper inside the surrounding DSL control-flow context:

```python
with m.always(posedge(clk)):
    with m.if_(rst):
        clear_q(q, valid)
```

Use this pattern for reusable statement groups or testbench actions. If the
task relied on timing, event control, or true parallel behavior, keep the logic
manual until the DSL grows an explicit equivalent.

### Generate blocks -> Python elaboration-time control flow

Translate `generate` constructs into ordinary Python control flow that runs
while building the module.

**Verilog**

```verilog
genvar i;
generate
    for (i = 0; i < LANES; i = i + 1) begin : g_lane
        assign out[i] = in[i] & en[i];
    end
endgenerate
```

**DSL**

```python
for i in range(LANES):
    m.assign(out[i], in[i] & en[i])
```

Likewise:

- `generate if` -> Python `if`
- `generate case` -> Python `if` / `elif` / `else`
- `genvar` -> ordinary Python loop variable

This is only appropriate for elaboration-time decisions: parameters, constants,
and other values known when the module is built. Do not use this pattern for
runtime procedural control flow.

### What should stay unsupported for now

Keep these as manual conversions or explicit `UNSUPPORTED` outputs until the
DSL has matching semantics:

- behavioral `while`, `forever`, `repeat`
- `wait`, `disable`, and event trigger `->`
- `fork` / `join`
- `specify` blocks

## Recommended implementation order

1. Keep converter tests in sync so every current `UNSUPPORTED` output shape stays pinned.
2. Add converter coverage for any newly supported expression lowering so regressions stay obvious.
3. Expand the manual conversion guidance when new intentional non-goals are identified.
4. Only convert control-flow-heavy testbench constructs after the DSL has direct
   semantics for them.

## Validation commands

Run focused converter tests with:

```powershell
uv run pytest tests\test_dsl\test_convert_to_dsl.py --tb=no -q
```

For changes that affect DSL round trips, also run:

```powershell
uv run pytest tests\test_dsl\test_roundtrip_dsl.py --tb=no -q
```
