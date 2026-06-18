# Debugging Simulator Issues

Strategies and reusable code patterns for diagnosing failures in the
Python-stepped, bytecode VM, and compiled simulator engines. Most of the
techniques below assume the VM engine (`engine="vm"`), since it offers the
richest introspection surface — values, sensitivity sets, bytecode programs,
and per-process state are all reachable from Python.

---

## 1. Pick the right engine for the bug

| Symptom | Start with |
|---------|-----------|
| Wrong value after combinational expression | VM (`engine="vm"`) — easiest bytecode inspection |
| Sequential / NBA ordering issue | VM, then cross-check with reference (`engine="reference"`) |
| Compile-time crash | VM — Python tracebacks; reference engine often shadows the same parse |
| Discrepancy between fast and slow simulator | Reference engine is the spec; if reference disagrees with VM, the VM is wrong |
| Behaviour OK in VM but wrong in compiled | Likely an `_expr_emitter`/Cython codegen bug — re-emit with `print_cython=True` |

---

## 2. Standard scaffolding

Most diagnostics share the same setup. Pattern:

```python
from pathlib import Path
from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

RTL_DIR = Path("examples/.../rtl")
RTL_FILES = [...]
design = parse_files([str(RTL_DIR / f) for f in RTL_FILES])
dut = design.get_module("top_module")

overrides = PlannerOverrides(iface_domains={"s00_axi": "clk", ...})
bench = Testbench(dut, design=design, overrides=overrides, engine="vm")

# Reach into the VM internals:
sched = bench.sim._sched
c = sched.compiler          # Compiler — signal_map, mem_map, processes
interp = sched.interpreter  # Interpreter — sig_val, sig_mask, dirty, mem_val
```

Key attributes on `c` (the `Compiler`/`CompiledDesign`):

| Attribute | Description |
|-----------|-------------|
| `signal_map[name] -> sid` | Hierarchical name → signal id |
| `sig_val[sid]` / `sig_mask[sid]` / `sig_width[sid]` | Current value, X/Z mask, width |
| `mem_map[name] -> mid` | Hierarchical memory name → memory id |
| `mem_info[mid] -> (elem_width, depth, base_index)` | Memory layout |
| `mem_val[base + addr]` / `mem_mask[base + addr]` | Per-element value/mask |
| `processes` | List of `CompiledProcess` (each has `program`, kind, sensitivity) |
| `const_pool[idx] -> Value` | Constant referenced by `LOAD_CONST` opcodes |
| `mem_marker_sigs[mid] -> sid` | Synthetic 1-bit "dirty" signal that fires whenever the memory is written |

---

## 3. Listing and finding signals

Often the first question is "does that signal even exist in the compiled
design, and what width did it elaborate to?"

```python
prefix = "axi_crossbar_inst.axi_crossbar_wr_inst."
for n in sorted(c.signal_map):
    if n.startswith(prefix) and "." not in n[len(prefix):]:
        s = c.signal_map[n]
        print(f"  {n[len(prefix):]:35s} sid={s} w={c.sig_width[s]}")
```

If a signal you expected is missing, common causes are:

* It lives inside a generate block — look for prefixes like
  `s_ifaces[0].addr_inst.`.
* The optimiser collapsed it into a continuous assignment chain.
* A parameter that resolves to `X` collapsed the declared width to 1 bit,
  causing the elaborator to drop the wire (see §8).

---

## 4. Snapshotting signal transitions per cycle

Use this pattern to find *when* a signal changes value or mask. It wraps
`sched.run_step` so every clock step prints a diff against the previous
snapshot.

```python
watch = {
    "s_cpl_id":     c.signal_map["...s_cpl_id"],
    "thread_active": c.signal_map["...thread_active"],
    "thread_cpl_match": c.signal_map["...thread_cpl_match"],
}
last = {n: (c.sig_val[s], c.sig_mask[s]) for n, s in watch.items()}

def snap(label):
    for n, s in watch.items():
        cur = (c.sig_val[s], c.sig_mask[s])
        if cur != last[n]:
            print(f"  t={sched.time} {label}: {n}: "
                  f"{last[n][0]:#x}/m{last[n][1]:#x} -> "
                  f"{cur[0]:#x}/m{cur[1]:#x}")
            last[n] = cur

orig_step = sched.run_step
def traced_step(**kw):
    r = orig_step(**kw)
    snap("step")
    return r
sched.run_step = traced_step
```

Always print **both `val` and `mask`** — a signal that "looks like 0" with
`mask != 0` is actually `X` (undefined), which behaves completely differently
in logic operations (`1 && X = X`, not `1 && 0 = 0`).

---

## 5. Locating the bytecode that writes a signal

When a signal has the wrong value, find the process(es) that drive it and
inspect their bytecode. Continuous assigns are individual `CompiledProcess`
entries.

```python
from veriforge.sim.vm.interpreter import Op

target_sid = c.signal_map["...thread_cpl_match"]
target_bit = 0   # for [n] writes from generate-for loops

for i, p in enumerate(c.processes):
    for j, (opv, a1, a2) in enumerate(p.program):
        if opv != Op.STORE_BIT.value or a1 != target_sid:
            continue
        # Find the BIT_SELECT before this STORE_BIT to know which bit
        for k in range(j - 1, -1, -1):
            if p.program[k][0] == Op.BIT_SELECT.value:
                prev = p.program[k - 1]
                if prev[0] == Op.LOAD_CONST.value and c.const_pool[prev[1]].val == target_bit:
                    print(f"Proc {i} writes {target_sid}[{target_bit}]")
                break
```

`STORE_SIG`, `STORE_BIT`, `STORE_RANGE`, `NBA_*` are the writer opcodes worth
filtering on.

---

## 6. Dumping a process's bytecode

Once you've identified the offending process, print it in a readable form:

```python
sid_to_name = {v: k for k, v in c.signal_map.items()}
mid_to_name = {v: k for k, v in c.mem_map.items()}

for i, (opv, a1, a2) in enumerate(proc.program):
    op_name = Op(opv).name
    extra = ""
    if op_name == "LOAD_SIG":
        extra = f"  # {sid_to_name.get(a1, '?')}"
    elif op_name in ("STORE_SIG", "STORE_BIT", "STORE_RANGE"):
        extra = f"  # -> {sid_to_name.get(a1, '?')}"
    elif op_name == "LOAD_MEM":
        extra = f"  # {mid_to_name.get(a1, '?')}"
    elif op_name == "LOAD_CONST":
        cv = c.const_pool[a1]
        extra = f"  # const = {cv.val} (w={cv.width})"
    print(f"  [{i:3d}] {op_name:15s} a1={a1:4d} a2={a2}{extra}")
```

---

## 7. Stepping bytecode by hand

When the process clearly *should* compute the right answer, manually
re-execute its instructions while printing the stack after every opcode.
This is what catches the truly subtle bugs: stale operands, missed
sensitivities, and X-propagation through `&&` / `==`.

```python
from veriforge.sim.value import Value

def trace_program(c, prog):
    stack = []
    for i, (opv, a1, a2) in enumerate(prog):
        op = Op(opv).name
        if op == "LOAD_SIG":
            stack.append(Value(c.sig_val[a1], width=c.sig_width[a1], mask=c.sig_mask[a1]))
        elif op == "LOAD_CONST":
            stack.append(c.const_pool[a1])
        elif op == "BIT_SELECT":
            idx = stack.pop(); tgt = stack.pop()
            stack.append(tgt[idx.val])
        elif op == "LOAD_MEM":
            addr = stack.pop()
            ew, _depth, base = c.mem_info[a1]
            stack.append(Value(c.mem_val[base + addr.val], width=ew,
                               mask=c.mem_mask[base + addr.val]))
        elif op == "CMP_EQ":
            b = stack.pop(); a = stack.pop(); stack.append(a.eq(b))
        elif op == "LOG_AND":
            b = stack.pop(); a = stack.pop(); stack.append(a.logical_and(b))
        # ... add ops you need
        top = stack[-1] if stack else None
        top_s = (f"top={top.val:#x}/m{top.mask:#x}(w={top.width})"
                 if top is not None else "empty")
        print(f"  [{i:2d}] {op:12s} a1={a1}  -> {top_s}")
```

**Important:** Use `len(stack) > 0` rather than `if stack:` — the latter
calls `__bool__` on the top `Value`, which raises if it contains x/z.

---

## 8. Intercepting interpreter `execute` calls

`Interpreter` uses `__slots__`, so you cannot directly monkey-patch its
methods. Wrap it with a proxy that forwards everything else:

```python
class InterpProxy:
    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)
    def __getattr__(self, n):
        return getattr(self._inner, n)
    def __setattr__(self, n, v):
        setattr(self._inner, n, v)
    def execute(self, prog):
        if prog is target_proc.program:
            # snapshot inputs, call real execute, snapshot outputs
            ...
        return self._inner.execute(prog)

sched.interpreter = InterpProxy(sched.interpreter)
```

This is the simplest way to count fires per cycle, log inputs/outputs, or
break on a specific process.

---

## 9. VCD dumps for visual inspection

For wide cone-of-influence bugs, a VCD is much faster than printf
instrumentation. The bench accepts a `vcd=` argument on `run()`:

```python
with bench.run(vcd="_debug.vcd"):
    bench.reset_all()
    s00 = bench.iface("s00_axi")
    s00.write(addr, data, timeout_cycles=40)
    bench.step(10)  # capture idle cycles after the transaction
```

Tips:

* Use **short `timeout_cycles`** (e.g. 8) on expected-to-fail transactions
  so the dump captures the failure window without multi-minute hangs.
* `bench.step(N)` advances `N` clocks with no endpoint activity, useful for
  bracketing transactions with idle periods so signal transitions are easy to
  read in a waveform viewer.
* The VCD uses the flattened hierarchical name path — memories appear as
  individual bit-slice signals.
* Open with GTKWave, Surfer, or any standard viewer.

---

## 10. Parameter / elaboration bugs

When widths look wrong (1 bit instead of 10, etc.), the culprit is usually
the elaborator failing to resolve a parameter expression. Check directly:

```python
from veriforge.sim.elaborate import _eval_const_expr, _build_param_env

env = _build_param_env(some_module)
for p in some_module.parameters:
    try:
        v = _eval_const_expr(p.default_value, env) if p.default_value else None
        print(f"  {p.name} = {v}")
    except Exception as e:
        print(f"  {p.name} = FAIL: {type(e).__name__}: {e}")
```

A parameter whose default is silently dropped will still appear in
`flat.parameters` as a raw `BinaryOp`/`FunctionCall` expression. Downstream
code then evaluates it in a scope where its dependencies aren't visible and
falls back to `X` — which propagates through every width expression that
references it.

If you find a forward-reference issue (param `B` depends on `A`, but `A` is
declared after `B`), `_build_param_env` must iterate to a fixed point.

---

## 11. Comparing engines

When you're not sure whether a discrepancy is a VM bug or a real RTL bug,
rerun the same test on the reference engine:

```python
bench_ref = Testbench(dut, design=design, overrides=overrides, engine="reference")
```

The reference engine is slower but follows the Verilog LRM very closely.
If the reference engine *agrees with the buggy VM*, your test is probably
exposing an RTL or test bug, not a simulator bug. If the reference engine
*disagrees*, you have a VM bug to localise.

---

## 12. Performance triage

Diagnostic scripts can appear to "hang" when they're really just slow.
Useful thresholds for the VM on a complex design:

* `run_step()`: roughly 0.5–2 s per call for a multi-thousand-signal design.
* A 120-iteration `wait_until(...)` loop on a stuck signal can take several
  minutes before timing out.

If you only need to confirm a failure mode, lower the timeout dramatically
(e.g. `timeout_cycles=8`). For batch sweeps (16+ transactions), prefer
the engine-native lowering path (`bench.compile_native(...)`) so all activity
runs inside the compiled engine without per-step Python overhead — see
[bench_native_lowering.md](bench_native_lowering.md).

---

## 13. Cleanup

Diagnostic scripts and VCDs live at the repo root with the `_diag_*.py` /
`_*.vcd` prefix by convention. They are intentionally **not** committed —
delete them once the bug is fixed and a real regression test has been added
under `tests/`.


## 14. Debugging a "hang"/simulation freeze

When the simulator appears to stall, use `py-spy` to sample the live Python
(and Cython) call stack without stopping the process.

```python
# Print the PID at script startup so you can attach py-spy
import os
print(f"PID:{os.getpid()}")
```

```bash
# Install py-spy (once)
uv tool install py-spy

# Dump current call stack — include --native for Cython frames
py-spy dump --pid <PID> --native

# Record a flame graph of where time is being spent
py-spy record --pid <PID> -o tmp.svg --native
```
