# Compiled Cython Simulation Engine

## Overview

The compiled Cython engine is the third simulation engine in the project,
alongside the reference (tree-walking) engine and the bytecode VM engine.
Instead of interpreting bytecode through a generic Cython switch loop, it
generates a **design-specific `.pyx` file** at elaboration time, compiles
it to a native extension module, and imports it — analogous to how Verilator
compiles Verilog to C++, but targeting Cython for seamless Python integration.

```
Model AST ──► Cython codegen ──► .pyx file ──► C compiler ──► .so/.pyd ──► import & run
              (our code)         (cache dir)    (gcc/msvc)    (cache dir)   (Python API)
```

The DUT is compiled; the testbench remains in Python and interacts with the
compiled module through the standard `Simulator` API (`signal()`, `drive()`,
`read()`, `run()`, `run_step()`). This is a drop-in replacement for
`engine="vm"` or `engine="reference"`.

For batch workloads (e.g. running thousands of cycles without per-cycle Python
callbacks), the `batch_run()` method on `CompiledScheduler` runs N clock cycles
entirely in C — yielding roughly 10–60× the throughput of step mode.

---

## Architecture

### File Layout

```
src/veriforge/sim/compiled/
├── __init__.py              # Public API: CythonCodegen, CythonCompiler, CompiledScheduler
├── codegen.py               # CythonCodegen — orchestrates full .pyx generation
├── compiler.py              # CythonCompiler — .pyx → .so/.pyd + caching
├── compiled_scheduler.py    # CompiledScheduler — Simulator-compatible adapter
├── _expr_emitter.py         # Expression → inline C emission mixin
├── _stmt_emitters.py        # Statement → Cython emission mixin
├── _process_compiler.py     # Process (always/initial/cont) compilation mixin
├── _gen_sections.py         # Delta-loop, SimCtx, and Python API generation
├── _gen_wide_section.py     # Wide (>64-bit) signal section generation
├── _gen_narrow_accessors.py # Narrow signal read/write accessor helpers
├── _gen_narrow_assign.py    # Narrow signal assignment codegen
├── _gen_narrow_stage.py     # Narrow signal staging helpers
├── _gen_narrow_tail.py      # Narrow signal tail codegen
├── _wide_emitter.py         # Wide expression emission helpers
└── _codegen_utils.py        # Shared codegen utility functions
```

### Where It Fits

```
┌────────────────────────────────────────────────────────────────┐
│  Simulator (testbench.py)                                      │
│  signal(), fork(), run() — same API for all step-mode engines  │
├────────────────────────────────────────────────────────────────┤
│  engine="compiled"   │  engine="vm"  │  engine="reference"     │
├──────────────────────┤               │                         │
│  CompiledScheduler   │  VMScheduler  │  Scheduler              │
│  Wraps the generated │  Bytecode     │  Tree-walking           │
│  extension module    │  interpreter  │  evaluator/executor     │
├──────────────────────┤               │                         │
│  Generated .pyx      │  CyContext    │                         │
│  (design-specific    │  (generic     │                         │
│   C code, nogil)     │   interpreter)│                         │
└──────────────────────┴───────────────┴─────────────────────────┘
```

### Scheduler Interface

`CompiledScheduler` implements the same interface as `Scheduler` and
`VMScheduler`, so `Simulator` can use it as a drop-in:

| Method | Description |
|--------|-------------|
| `elaborate(module, *, source_files=None)` | Generate .pyx, compile, import, initialize signals |
| `run(max_time=)` | Run event loop to completion |
| `run_step()` | Advance one time step, return True if events remain |
| `drive_signal(name, value)` | Drive a signal from the testbench |
| `read_signal(name)` | Read a signal value |
| `schedule_at(time, proc)` | Schedule a process at a given time |
| `load_memory(name, data)` | Bulk-write a DSL memory from a sequence or numpy array |
| `dump_memory(name, count)` | Read `count` elements from a DSL memory into a list |
| `time` (property) | Current simulation time |
| `display_output` (property) | Collected $display output |

The `elaborate()` method is where the compilation happens. It:

1. Walks the module AST to compute signal layout and process categorization
   (reusing analysis logic from the VM compiler)
2. Calls `CythonCodegen` to generate the `.pyx` source
3. Calls `CythonCompiler` to compile (or load from cache) the extension
4. Imports the compiled module and creates the internal `CompiledSimulator`
   object (a `cdef class` in the generated extension)
5. Initializes signal values and builds the name→ID mapping

After elaboration, `drive_signal` / `read_signal` translate signal names
to integer IDs and delegate to the compiled module's `cpdef` methods.
`run_step` calls the compiled `step()` method which runs the design-specific
delta loop entirely in C (`nogil`).

---

## Why Compile Instead of Interpret?

The bytecode VM eliminates Python dispatch but still pays for:

1. **Opcode decode** — the `switch(op)` in `_execute_core()` runs per
   instruction, even though the instruction sequence is fixed per design
2. **Stack machine overhead** — every expression pushes/pops `SVal` structs
   through a value stack, adding memory traffic
3. **Generic code** — the interpreter handles all 83 opcodes for all designs;
   the C compiler cannot specialize or inline across instruction boundaries

A compiled approach eliminates all three. The generated C code is a direct
translation of the design's logic with expressions inlined, signals accessed
by direct array index, and no decode loop. The C compiler applies constant
folding, register allocation, and instruction scheduling across the entire
design.

### Comparison with Verilator

| Aspect | Verilator | This Engine |
|--------|-----------|-------------|
| Input | Verilog → AST | Model AST (already parsed) |
| Output | C++ source | Cython `.pyx` source |
| Compilation | g++/clang++ | Cython → C → gcc/msvc |
| Testbench language | C++ (or cocotb via VPI) | Python (native) |
| Two-state optimization | Yes (default) | Possible (skip mask ops) |
| Design-specific code | Yes | Yes |
| Runtime integration | Standalone binary | Python extension module |
| GIL interaction | N/A | `nogil` blocks for hot path |

---

## Dynamic Cython Compilation

The engine uses programmatic `cythonize()` + `importlib` to compile and
import extensions at runtime. This is the same pattern used by Jupyter's
`%%cython` magic and SageMath.

```python
# Simplified flow (actual implementation in compiler.py)
def compile_and_import(pyx_source: str, module_name: str, cache_dir: str):
    pyx_path = os.path.join(cache_dir, f"{module_name}.pyx")
    write_file(pyx_path, pyx_source)

    ext = Extension(module_name, [pyx_path])
    cythonize(ext, compiler_directives={
        "language_level": "3",
        "boundscheck": False, "wraparound": False,
        "cdivision": True, "initializedcheck": False,
        "nonecheck": False,
    })

    subprocess.check_call(
        [sys.executable, "setup.py", "build_ext", "--inplace"],
        cwd=cache_dir,
    )

    spec = importlib.util.spec_from_file_location(module_name, so_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

### Compilation Time

| Stage | Estimated Time | Notes |
|-------|---------------|-------|
| Code generation (AST → .pyx) | < 100ms | String formatting |
| Cython translation (.pyx → .c) | ~1-2s | Depends on code size |
| C compilation (.c → .so/.pyd) | ~3-8s | gcc/msvc |
| **Total first-time compile** | **~5-10s** | One-time per design |
| Cached re-import | < 50ms | Skip compilation |

### Caching

Compiled binaries are cached by a hash of the generated `.pyx` source plus
the Python version, Cython version, and platform tag. Cache location:
`.cycache/` under CWD by default (override with `VERILOG_TOOLS_COMPILE_CACHE`
env var). On cache hit, the
extension is imported directly without recompilation.

---

## Generated Code Structure

Each design produces a self-contained `.pyx` module. The structure is
illustrated here for a counter design with `clk`, `rst`, `count` signals
and one continuous assign.

### Signal Constants and Context

```cython
# cython: language_level=3, boundscheck=False, wraparound=False
# cython: cdivision=True, initializedcheck=False, nonecheck=False

from libc.string cimport memcpy

DEF N_SIGS = 5
DEF SIG_CLK = 0
DEF SIG_RST = 1
DEF SIG_COUNT = 2
DEF SIG_SUM = 3
DEF SIG_A = 4

DEF W_CLK = 1
DEF W_RST = 1
DEF W_COUNT = 8
DEF W_SUM = 9
DEF W_A = 8

cdef struct SimCtx:
    long long val[N_SIGS]
    long long mask[N_SIGS]
    int       width[N_SIGS]
    long long nba_val[N_SIGS]
    long long nba_mask[N_SIGS]
    int       dirty[N_SIGS]
    int       nba_pending
    long long sim_time

cdef inline long long wmask(int w) noexcept nogil:
    if w >= 64:
        return -1
    return (1LL << w) - 1
```

### Process Functions

One `cdef inline` function per process, all `noexcept nogil`:

```cython
cdef inline void cont_0(SimCtx *c) noexcept nogil:
    """assign sum = a + count"""
    cdef long long v = (c.val[SIG_A] + c.val[SIG_COUNT]) & wmask(W_SUM)
    cdef long long m = c.mask[SIG_A] | c.mask[SIG_COUNT]
    if m:
        v = 0
        m = wmask(W_SUM)
    if v != c.val[SIG_SUM] or m != c.mask[SIG_SUM]:
        c.val[SIG_SUM] = v
        c.mask[SIG_SUM] = m
        c.dirty[SIG_SUM] = 1

cdef inline void seq_0(SimCtx *c) noexcept nogil:
    """always @(posedge clk) if (rst) count <= 0 else count <= count + 1"""
    if c.val[SIG_RST] & 1:
        c.nba_val[SIG_COUNT] = 0
        c.nba_mask[SIG_COUNT] = 0
    else:
        c.nba_val[SIG_COUNT] = (c.val[SIG_COUNT] + 1) & wmask(W_COUNT)
        c.nba_mask[SIG_COUNT] = c.mask[SIG_COUNT]
    c.nba_pending = 1
```

### Delta Loop

Design-specific — sensitivity checks are inlined, no CSR lookup:

```cython
cdef int delta_loop(SimCtx *c, long long *sv, long long *sm) noexcept nogil:
    cdef int it, i, changed
    cdef int fire_seq_0 = 0

    # Edge detection: posedge clk
    if (c.val[SIG_CLK] & 1) and not (sv[SIG_CLK] & 1):
        fire_seq_0 = 1

    for it in range(1000):
        changed = 0

        # Continuous assigns on dirty inputs
        if c.dirty[SIG_A] or c.dirty[SIG_COUNT]:
            cont_0(c)

        # Sequential processes (fire once per time step)
        if fire_seq_0:
            seq_0(c)
            fire_seq_0 = 0

        # Apply NBAs
        if c.nba_pending:
            for i in range(N_SIGS):
                if c.nba_val[i] != c.val[i] or c.nba_mask[i] != c.mask[i]:
                    c.val[i] = c.nba_val[i]
                    c.mask[i] = c.nba_mask[i]
                    c.dirty[i] = 1
                    changed = 1
            c.nba_pending = 0

        # Re-run dirty continuous assigns
        if c.dirty[SIG_A] or c.dirty[SIG_COUNT]:
            cont_0(c)

        # Clear dirty flags, check convergence
        for i in range(N_SIGS):
            if c.dirty[i]:
                changed = 1
            c.dirty[i] = 0

        if not changed:
            break

    return it
```

### Python API (Generated)

```cython
cdef class CompiledSim:
    cdef SimCtx ctx
    cdef long long _snap_v[N_SIGS]
    cdef long long _snap_m[N_SIGS]

    def __init__(self):
        cdef int i
        for i in range(N_SIGS):
            self.ctx.val[i] = 0
            self.ctx.mask[i] = wmask(self.ctx.width[i])
            self.ctx.dirty[i] = 0
            self.ctx.nba_val[i] = 0
            self.ctx.nba_mask[i] = 0
        self.ctx.nba_pending = 0
        self.ctx.sim_time = 0

    cpdef void drive(self, int sid, long long v, long long m):
        if v != self.ctx.val[sid] or m != self.ctx.mask[sid]:
            self.ctx.val[sid] = v
            self.ctx.mask[sid] = m
            self.ctx.dirty[sid] = 1

    cpdef tuple read(self, int sid):
        return (self.ctx.val[sid], self.ctx.mask[sid])

    cpdef int step(self):
        memcpy(self._snap_v, self.ctx.val, N_SIGS * sizeof(long long))
        memcpy(self._snap_m, self.ctx.mask, N_SIGS * sizeof(long long))
        cdef int deltas
        with nogil:
            deltas = delta_loop(&self.ctx, self._snap_v, self._snap_m)
        return deltas

    cpdef void set_time(self, long long t):
        self.ctx.sim_time = t

    cpdef int batch_run(self, int cycles, int clk_sid):
        """Run N clock cycles entirely in C. Returns final cycle count."""
        cdef int cy
        with nogil:
            for cy in range(cycles):
                memcpy(self._snap_v, self.ctx.val, N_SIGS * sizeof(long long))
                memcpy(self._snap_m, self.ctx.mask, N_SIGS * sizeof(long long))
                self.ctx.val[clk_sid] = 1
                self.ctx.dirty[clk_sid] = 1
                delta_loop(&self.ctx, self._snap_v, self._snap_m)

                memcpy(self._snap_v, self.ctx.val, N_SIGS * sizeof(long long))
                memcpy(self._snap_m, self.ctx.mask, N_SIGS * sizeof(long long))
                self.ctx.val[clk_sid] = 0
                self.ctx.dirty[clk_sid] = 1
                delta_loop(&self.ctx, self._snap_v, self._snap_m)

                self.ctx.sim_time += 1
        return cy
```

---

## Expression Codegen

Expressions compile to inline C expressions (no stack, no temporaries):

```
AST Expression                    Generated Cython
───────────────                   ─────────────────
BinaryOp("+", a, b)         →    (c.val[SIG_A] + c.val[SIG_B]) & wmask(W)
BinaryOp("&", a, b)         →    c.val[SIG_A] & c.val[SIG_B]
UnaryOp("~", a)             →    (~c.val[SIG_A]) & wmask(W)
TernaryOp(cond, t, f)       →    (t_expr if (cond_expr & 1) else f_expr)
BitSelect(a, 3)             →    (c.val[SIG_A] >> 3) & 1
RangeSelect(a, 7, 4)        →    (c.val[SIG_A] >> 4) & 0xF
Concat(a, b)                →    (c.val[SIG_A] << W_B) | c.val[SIG_B]
```

For complex sub-expressions, the codegen emits `cdef long long` temporaries
to keep generated lines readable and avoid repeated evaluation.

### X/Z Propagation

The codegen generates 4-state logic by default (`val`/`mask` pairs). When
static analysis determines that signals are always fully defined (e.g.,
testbench-driven inputs, registers with reset), 2-state code is generated
instead — mask operations are omitted entirely for those paths.

---

## Statement Codegen

```
AST Statement                     Generated Cython
───────────────                   ─────────────────
BlockingAssign(y, expr)      →    c.val[SIG_Y] = <expr>; c.dirty[SIG_Y] = 1
NonblockingAssign(q, expr)   →    c.nba_val[SIG_Q] = <expr>; c.nba_pending = 1
IfStatement(cond, t, f)      →    if <cond>: ... else: ...
CaseStatement(sel, items)    →    if sel == v0: ... elif sel == v1: ...
ForLoop(i, 0, N, body)       →    for i in range(N): ...
```

### LHS Targets

- **Identifier** → direct array write + dirty flag
- **BitSelect** → read-modify-write with bit mask
- **RangeSelect** → read-modify-write with range mask
- **Concatenation** → decompose by part widths, recursive writes
- **Memory element** → flat array write + synthetic dirty marker

---

## Timing Control Fallback

Initial blocks with `#delay` and `@(event)` cannot be compiled to static
C code (they require process suspension). These are handled identically to
the bytecode VM's approach:

1. The compiled scheduler detects `has_timing` on initial/always blocks
2. Those processes route to the reference executor's coroutine mechanism
3. Signal state is synced between compiled C arrays and the reference
   `EvalContext` before/after coroutine execution
4. Only signals the coroutine reads/writes are synced (same optimization
   as the VM's `_coro_sync_names`)

The compiled delta loop handles the "fast path" (continuous assigns +
combinational + sequential processes) — where 99% of simulation time is
spent.

---

## Batch Mode: `batch_run()`

The generated `batch_run()` method runs N clock cycles entirely in C, giving
most of the throughput benefit of full compiled simulation while keeping the
testbench in Python:

```python
sim = Simulator(module, engine="compiled")
sim.drive("rst", 1)
sim.batch_run(cycles=5)   # 5 clock cycles in C
sim.drive("rst", 0)
sim.batch_run(cycles=100) # 100 cycles in C
assert sim.read("count") == 100  # back in Python
```

`batch_run` also accepts scheduled events that are applied inside the C loop:

```python
sim = Simulator(top, engine="compiled", design=design)
sim.run(max_time=0)  # execute $readmemh, settle

# Single call — clock, reset, and sim all in C
sim.batch_run(
    cycles=50000,
    clock_name="CLK",
    clock_period=10,
    events=[
        (100, "RES", 0),   # release reset at cycle 100
    ],
)
```

Events are `(cycle, signal_name, value)` tuples applied before the posedge
of the given cycle. They must be sorted by cycle number. This eliminates
the need for multiple `batch_run`/`drive` round-trips.

### Testbench structure for maximum batch_run performance

The compiled engine's performance is determined by how much of the simulation
runs in C vs. Python. Any `initial` block with timing (`#delay`, `while(1)`,
`@(event)`) creates a Python coroutine that forces the event-loop path.

**Avoid** (forces step mode, ~170K cyc/s):
```verilog
initial while(1) #5 CLK = !CLK;  // Python coroutine per edge!
initial begin #1000 RES = 0; end  // Python coroutine for reset
```

**Recommended** (pure batch mode, ~10M cyc/s):
```verilog
module testbench;
    reg CLK = 0;
    reg RES = 1;
    // No initial blocks with timing.
    // Clock and reset driven by batch_run().
    my_dut dut(.clk(CLK), .rst(RES));
endmodule
```

See `examples/darkriscv/run_fast.py` for a complete working example that
achieves 512x speedup over the event-loop path on the DarkRISCV SoC.

---

## Bulk Memory I/O: `load_memory` / `dump_memory`

For testbenches that use DSL memories as stimulus ROMs or output capture
buffers (the typical `batch_run` pattern), two convenience methods let
Python pre-load and post-read memory contents without per-element
`drive("mem[i]", v)` calls.

### API

```python
sim.load_memory(name: str, data) -> None
```

Writes `data[0]..data[len(data)-1]` into the compiled memory named
`name` starting at address 0. `data` may be any sequence or numpy array
of integers; each element is truncated to the memory's element width.

```python
sim.dump_memory(name: str, count: int) -> list[int]
```

Reads addresses `0..count-1` from the compiled memory named `name` and
returns them as a Python `list[int]`.

```python
sim.memory_names -> list[str]
```

Returns the flat names of all DSL memories in the compiled design.
Returns `[]` for non-compiled engines.

All three raise `NotImplementedError` if called on a non-compiled engine,
and `ValueError` (with a list of available names) if the memory name is
not found.

### Typical pattern

```python
sim = Simulator(top, engine="compiled", design=design)

# Pre-load stimulus ROM
sim.load_memory("tb_src__rom", pixels)

# Run entirely in C — no Python per cycle
sim.batch_run(n_cycles, "clk")

# Read output buffer
n_words = int(sim.read("tb_sink__word_count"))
encoded = sim.dump_memory("tb_sink__buf", n_words)
```

### Implementation note

`load_memory` calls `mem_write` (or `mem_write_wide` for >64-bit elements)
once per element via the compiled `CompiledSim` Cython object. For most
realistic ROM sizes (< 10M elements) this loop is fast enough to be
negligible compared to `batch_run`. A true zero-overhead bulk-copy path
(passing a typed memoryview into Cython) is a future enhancement.

---

## Performance

### Expected Throughput

| Engine | Throughput | vs Reference | vs Icarus |
|--------|-----------|-------------|-----------|
| Reference (tree-walking) | 16,333 cyc/s | 1x | 19x slower |
| VM + Cython (bytecode) | 161,480 cyc/s | ~10x | 2x slower |
| **Compiled (step mode)** | ~500K-1M cyc/s (est.) | ~30-60x | ~2-3x faster |
| **Compiled (batch/TB)** | ~2-5M cyc/s (est.) | ~120-300x | ~6-16x faster |
| Verilator (C++) | 14.6M cyc/s | ~894x | 47x faster |

### Why Faster Than VM

- No opcode decode loop (~30% of VM time)
- No value stack push/pop (~20% of VM time)
- C compiler optimizes across entire expression trees
- Signal access is compile-time-constant array indexing

### Why Slower Than Verilator

- Verilator generates highly-optimized C++ with 2-state fast paths
- Verilator does statement-level scheduling (evaluate only what changed)
- Verilator supports multi-threaded simulation
- Verilator has 20+ years of optimization

---

## Codegen Scalability

### Streaming Codegen (`generate_to_file`)

For large designs the `generate()` method can require significant RAM because
it builds the entire `.pyx` string in memory before writing it to disk.
`CythonCodegen.generate_to_file(module, path)` is the streaming alternative:

- Writes each section to disk as it is generated.
- Streams the process-function section **one function at a time**, so peak
  memory is bounded by the largest single process function rather than the
  entire file.
- Returns a SHA-256 hex digest of the source bytes for use as a cache key.
- The compiled-scheduler `_elaborate_inner` path uses this method exclusively
  since the streaming path was adopted.

Callers that need the source as a string (e.g. tests, tooling) continue to use
`generate()` unchanged.

### Long Sensitivity Conditions

The delta loop emits one sensitivity check per cont/combo process.  For designs
with many-signal sensitivity sets the naive `trigger[0] or trigger[1] or …`
pattern creates very long single lines.  Lines above `_MAX_INLINE_SENS` (6)
signals are automatically split across multiple short continuation lines:

```cython
# 270-signal sensitivity set: 45 lines ≤ 113 chars each instead of one 3.2 KB line
if (trigger[0] or trigger[1] or trigger[2] or trigger[3] or trigger[4] or trigger[5]
        or trigger[6] or trigger[7] or trigger[8] or trigger[9] or trigger[10] or trigger[11]
        ...
        or trigger[264] or trigger[265] or trigger[266] or trigger[267] or trigger[268] or trigger[269]):
    cont_0(c)
```

### Expression Temporaries (O(k²) → O(k) fix)

For designs with deeply nested arithmetic or bitwise chains (`a + b + c + … + z`
or `a | b | c | … | z` with k terms), the expression emitters would previously
generate O(k²) inline strings.  Named C temporaries (`_et{n}_v`, `_et{n}_m`)
break each chain level into O(1) references.

**`+`/`-` chains** (`_emit_binary`):
- When `_emit_binary` sees `expr.op in {'+','-'}` and the left operand is itself
  a `+`/`-` chain, it hoists the left value+mask pair to named temps in
  `self._et_pending`.

**`|`/`&` chains** (`_emit_mask_expr`):
- For `|` and `&`, `_emit_mask_expr` must compute both `lm` (mask of left) and
  `lv` (value of left) to build the 4-state propagation formula.  Without
  hoisting, each level re-expands the left subtree, giving O(k²) mask strings.
- When in a temp context and `expr.left` is itself a `|`/`&` BinaryOp,
  `_emit_mask_expr` hoists both `lv` and `lm` of `expr.left` to `_et{n}_v` /
  `_et{n}_m`, records them in `_et_node_vals` / `_et_node_masks`, and uses
  short temp names in the formula.
- `_emit_expr` checks `_et_node_vals` first, returning the cached temp name
  immediately so the right side of the chain doesn't re-expand the left.

**TernaryOp chains** (`_emit_expr` / `_emit_mask_expr`):
- For a right-recursive k-deep ternary mux, `_emit_ternary_value_mask_exprs`
  calls both `_emit_expr(false_branch)` and `_emit_mask_expr(false_branch)`,
  and each recurses into the same sub-chain independently — giving 2^k calls.
- Fix: after computing a TernaryOp's value+mask, BOTH are immediately hoisted
  to `_et{n}_v`/`_et{n}_m` and cached in `_et_node_vals`/`_et_node_masks`.
  The other emitter finds its cache entry on first call and returns immediately.
  This converts O(2^k) recursion to O(k) — verified at k=24 in 0.001s.

**Python path TernaryOp** (`_emit_py_expr` / `_emit_py_mask_expr`):
- The `py=True` path in `_emit_ternary_value_mask_exprs` has the same 2^k structure.
- Fix: `_py_val_cache` / `_py_mask_cache` dicts (keyed by `id(expr)`, reset per
  wide-assign) memoize Python-path ternary results.  Whichever emitter runs first
  caches both val+mask so the second call returns O(1).

**Python path `|`/`&`** (`_emit_py_mask_expr`):
- `_emit_py_mask_expr` for `|`/`&` calls `_emit_py_mask_expr(left)` then
  `_emit_py_expr(left)` independently → O(k²) for a k-deep wide `|`/`&` chain.
- Fix: check `_py_val_cache` before calling `_emit_py_expr`; `_emit_py_expr` for
  BinaryOp now populates `_py_val_cache` for both operands via `setdefault`.

**Python path `+`/`-`** (`_emit_py_expr`):
- `_emit_py_expr` for `+`/`-` calls `_emit_py_expr(left)` then `_emit_py_mask_expr(left)`
  independently; if left is a TernaryOp chain this was 2^k × 2 = 2^(k+1).
- Fixed as side-effect: `setdefault` in BinaryOp + TernaryOp memoization handles it.

**`_emit_wide_py_bits_lines` guard** (`_wide_emitter.py`):
- Called before the Cython fallback for every continuous assign.
  `_rhs_max_accessed_signal_width(rhs)` can inflate `eval_width` above 64 for a
  narrow (32-bit) LHS when the mux data path accesses a wide signal, causing the
  wide Python emitters to run on a TernaryOp chain and OOM.
- B1 fix: return None immediately when `_signal_widths[dst_sid] <= _WORD_BITS`.
  Narrow LHS signals are always handled correctly by the Cython fallback.
- Cache reset: `_py_val_cache = {}; _py_mask_cache = {}` at entry of each wide
  assign so memoized strings from different assigns don't cross-contaminate via id reuse.

**Continuous assigns** (`_compile_continuous_assigns`):
- The fallthrough scalar path now opens a `_et_pending = []` context before
  calling `_emit_expr` and `_emit_mask_expr`, then prepends the drained `et_lines`
  to the process body.  This activates `+`/`-`, `|`/`&`, and TernaryOp hoisting
  for continuous assigns (previously only active for always-block assignments).

**Infrastructure**:
- `_et_node_masks: dict[int, str]` and `_et_node_vals: dict[int, str]` cache
  temp names by AST node identity.  Both are reset per always-block body
  (`_compile_always_body`) and per continuous-assign fallthrough.
- The `_et_node_masks` cache check was generalized from `+`/`-`-only to all
  BinaryOp so that `|`/`&` hoist targets are also short-circuited.
- `_hoist_inline_cdefs` moves `cdef long long _et{n}_v = …` lines to function
  level so they are never inside an `if`/`elif` block (unchanged).
- Result: max line length stays O(1) per level (measured at 199 chars for all k).
  Total generated code is O(k); number of named temps is O(k).

Tested in `TestExpressionTemporaries` (+/- chains), `TestOrChainTemporaries`
(|/& chains), and `TestTernaryChainTemporaries` (right-recursive mux, the
actual gfwx-fpga assign 255 pattern) in `test_compiled.py`.

### Deferred Always-Block Compilation

The remaining scalability bottleneck after the streaming text fix was that
`_compile_always_blocks` called `_emit_stmt(block.body, indent=1)` for ALL
always blocks before opening the output file, accumulating the compiled IR
(lists of code lines) for every block simultaneously in Python heap.  For large
designs this grows to tens of GB despite the streaming write path.

The fix: `_compile_always_blocks` now stores each block's raw AST body
(`block.body`, a tiny pointer) rather than the compiled `list[str]`.  The
actual `_emit_stmt` call is deferred to `_gen_process_functions_to` via the
new `_compile_always_body(block_body)` helper, which compiles one block at a
time, emits it to disk immediately, and lets the IR be garbage-collected before
moving to the next block.

Result: peak Python heap during `generate_to_file` stays at ~1 MB regardless
of design size (measured at n=20, 40, 80 registers; previously grew linearly
with design complexity to 27.5 GB before OOM on a 270-signal design).

---

## Memory Arrays

Memory arrays (`reg [7:0] mem [0:255]`) are handled with the same layout
as the VM engine:

```cython
DEF MEM_0_WIDTH = 8
DEF MEM_0_DEPTH = 256

# Flat arrays in SimCtx
long long mem_val[MEM_0_DEPTH]
long long mem_mask[MEM_0_DEPTH]
```

Each memory has a synthetic 1-bit dirty marker signal. Memory writes set
the marker dirty so that processes reading from that memory re-fire during
the delta loop.

---

## $display and System Tasks

The same approach as the bytecode VM: display output is buffered in a flat
C array during the `nogil` delta loop. After the delta loop returns, the
scheduler drains the buffer and formats each entry as a Python string using
the stored format string index and argument values.

```cython
# In the generated .pyx
DEF DISP_BUF_CAP = 4096

cdef struct SimCtx:
    # ... signals ...
    long long disp_buf[DISP_BUF_CAP]
    int disp_pos
```

---

## Limitations

- **Raw codegen still expects elaborated input** — direct compiled codegen rejects
  unflattened module instantiation / hierarchy and unelaborated generate blocks
- **Flattened multi-dimensional unpacked memories only** — compiled now supports
  full element accesses like `mem[i][j]` by flattening unpacked dimensions into
  one storage depth, but subarray-value semantics remain limited
- **Wide-signal support remains partial** — external `>64`-bit signal round-trips now
  work, but internal compiled expression / assignment / NBA codegen still has
  remaining single-word assumptions
- **C compiler required** — runtime compilation needs gcc/MSVC installed
- **First-time compile latency** — ~5-10s per design (cached after)

---

## Engine Selection Guide

| Need | Engine |
|------|--------|
| Debug Verilog logic with Python breakpoints | `reference` |
| Good performance, no compile delay | `vm` |
| Maximum per-cycle performance, step-by-step TB | `compiled` (step mode) |
| Maximum throughput, minimal Python callbacks | `compiled` + `batch_run()` |
| Full Verilog 2005 language coverage | `reference` |
| VCD waveform recording | any (compiled syncs per time step) |
