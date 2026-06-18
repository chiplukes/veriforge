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
