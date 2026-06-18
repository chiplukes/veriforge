# Bytecode VM Simulation Engine

## Overview

The bytecode VM is a high-performance alternative simulation engine that
coexists with the reference (pure-Python tree-walking) engine. Both engines
consume the same model AST and expose the same `Simulator` API; they differ
only in the execution strategy.

**Reference engine**: Walks Expression/Statement trees recursively at runtime.
Clean and auditable, but limited to ~16,300 cycles/s due to per-node Python
function calls, dict-based signal lookups, and Value object allocation.

**Bytecode VM** (this document): A two-phase engine:
1. **Compile** (at elaboration time): Walk the AST once and emit a flat array
   of bytecode instructions + a signal storage layout.
2. **Execute** (per simulation time step): Interpret the bytecode in a Cython
   C loop with array-indexed signal access and a fixed-size value stack.

### Why Bytecode?

The Verilator project demonstrates that the biggest simulation speedups come
from eliminating the overhead of dynamic dispatch and enabling static analysis
of the design. Our bytecode VM adopts several Verilator-inspired techniques:

| Technique | Verilator | Our VM |
|-----------|-----------|--------|
| Static ordering | Topological sort → C++ | Topological sort → bytecode order |
| Flat signal storage | C struct of uint32 | C `long long` arrays indexed by signal ID |
| Activity gating | Skip unchanged modules | Skip processes with clean inputs (CSR index) |
| Module flattening | Inline all hierarchy | Single flat signal namespace |
| Expression compilation | → C++ operators | → bytecode → Cython C switch |

### Benchmark Results

100K-cycle 8-bit counter benchmark on Linux (WSL2):

| Engine | Throughput | vs Reference |
|--------|-----------|-------------|
| Reference (CPython 3.12) | 16,333 cyc/s | 1.0× |
| VM + Cython | 161,480 cyc/s | 9.9× |
| Icarus Verilog 13.0 | 308,372 cyc/s | 18.9× |
| Verilator 5.x | 14,600,000 cyc/s | 894× |

The VM eliminates the three biggest costs in the reference engine, and the
Cython interpreter eliminates Python interpreter overhead entirely for the
inner loop. See `notes/benchmarks.md` for full methodology and results.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Simulator (testbench.py)                                      │
│  signal(), fork(), run() — same API for both engines           │
├────────────────────────────────────────────────────────────────┤
│                    engine="vm"                                 │
├──────────────────────────┬─────────────────────────────────────┤
│  VMScheduler             │  Reference Scheduler                │
│  (vm/vm_scheduler.py)    │  (scheduler.py)                     │
│  Orchestrates delta loop │  Uses tree-walking eval/exec        │
├──────────────────────────┤                                     │
│  CyContext               │                                     │
│  (vm/_interp_fast.pyx)   │                                     │
│  C-native context with   │                                     │
│  run_delta_loop() in C   │                                     │
├──────────────────────────┤                                     │
│  Compiler                │                                     │
│  (vm/compiler.py)        │                                     │
│  AST → bytecode          │                                     │
├──────────────────────────┤                                     │
│  Interpreter             │                                     │
│  (vm/interpreter.py)     │                                     │
│  Python fallback interp  │                                     │
├──────────────────────────┤                                     │
│  Opcodes                 │                                     │
│  (vm/opcodes.py)         │                                     │
│  83 instruction defs     │                                     │
└──────────────────────────┴─────────────────────────────────────┘
```

### File Layout

```
src/veriforge/sim/vm/
├── __init__.py          # Public API exports
├── opcodes.py           # Op IntEnum, 83 opcodes (151 lines)
├── compiler.py          # AST → bytecode compilation (2,601 lines)
├── interpreter.py       # Python bytecode interpreter (1,026 lines)
├── vm_scheduler.py      # VM-aware scheduler + coroutine fallback (1,566 lines)
├── _interp_fast.pyx     # Cython C interpreter + CyContext (4,588 lines)
└── _interp_fast.c       # Generated C from Cython (build artifact)
```

Total VM engine: ~9,932 lines.

### Signal Storage

All signals are stored in flat arrays indexed by a compile-time-assigned
integer ID (0..N-1):

```python
# Python lists (shared with Cython via CyContext)
sig_val:   list[int]    # Current integer value
sig_mask:  list[int]    # x/z mask (0 = defined, 1 = x/z)
sig_width: list[int]    # Bit width (constant, set at compile time)
sig_names: list[str]    # Name strings (for debug/display)
```

In the Cython fast path, these are copied into C `long long*` / `int*`
arrays owned by `CyContext`. Sync functions copy between Python lists and
C arrays at engine boundaries (initial blocks, coroutine resume/suspend).

The compiler builds `signal_map: dict[str, int]` mapping names to IDs.
At C level, all signal access uses integer indexing — zero dict lookups.

### Constant Pool

Literal values are collected into a deduplicated constant pool at compile time:

```python
const_pool: list[Value]          # Python (used by interpreter.py)
const_c_val:  list[int]          # Extracted for CyContext
const_c_mask: list[int]
const_c_width: list[int]
```

Deduplication key: `(val, mask, width)` → pool index.

### Memory Arrays

Verilog memory arrays (`reg [7:0] mem [0:255]`) are stored in separate flat
arrays indexed by `(mem_id, address)`:

```python
mem_val:  list[int]    # Flat: mem_val[base + addr]
mem_mask: list[int]
mem_info: list[tuple[int, int, int]]  # (elem_width, depth, base_addr) per mem_id
```

To handle sensitivity for memories, the compiler creates a synthetic 1-bit
**marker signal** per memory (`__mem_0_wr`, etc.). Combinational always
blocks that read a memory include the marker in their sensitivity set.
`STORE_MEM` / `NBA_MEM` opcodes mark the marker signal dirty so those
processes re-fire. The marker signal ID is encoded in the upper 16 bits
of the opcode's arg1: `arg1 = mem_id | (marker_sid << 16)`.

---

## Instruction Set

The VM is **stack-based**: expression evaluation pushes/pops `Value` objects
(Python interpreter) or `SVal` structs `{val, mask, width}` (Cython). Statement
execution consumes values from the stack to update signals.

**83 opcodes** organized into 13 categories:

### Instruction Format

Each instruction is a tuple: `(opcode, arg1, arg2)`

- `opcode`: `int` (from `Op` IntEnum)
- `arg1`, `arg2`: `int` operands (meaning depends on opcode, 0 if unused)

In Python: `list[tuple[int, int, int]]`.
In Cython: flattened into `int *all_ops` and `int *all_a1` arrays per program.

### Data Movement (9 opcodes)

| Opcode | Args | Stack Effect | Description |
|--------|------|-------------|-------------|
| `LOAD_SIG` | sig_id | → val | Push signal value |
| `LOAD_CONST` | const_id | → val | Push constant from pool |
| `STORE_SIG` | sig_id | val → | Blocking assign (marks dirty) |
| `NBA_SIG` | sig_id | val → | Non-blocking assign (queues NBA) |
| `STORE_BIT` | sig_id | val, idx → | Bit-select blocking assign |
| `NBA_BIT` | sig_id | val, idx → | Bit-select NBA |
| `STORE_RANGE` | sig_id | val, msb, lsb → | Range-select blocking assign |
| `NBA_RANGE` | sig_id | val, msb, lsb → | Range-select NBA |
| `RESIZE` | width | val → val' | Resize top-of-stack to given width |

### Arithmetic (8 opcodes)

`ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `POW` — unsigned binary, pop 2, push 1.
`SDIV`, `SMOD` — signed division/modulus (truncates toward zero).

### Bitwise (9 opcodes)

`BIT_AND`, `BIT_OR`, `BIT_XOR`, `BIT_XNOR`, `BIT_NOT` (unary),
`SHL`, `SHR`, `ASHL`, `ASHR`.

### Comparison (14 opcodes, push 1-bit result)

Unsigned/structural: `CMP_EQ`, `CMP_NE`, `CMP_LT`, `CMP_LE`, `CMP_GT`, `CMP_GE`,
`CMP_CASE_EQ`, `CMP_CASE_NE`, `CMP_CASEX`, `CMP_CASEZ`.

Signed: `CMP_SLT`, `CMP_SLE`, `CMP_SGT`, `CMP_SGE`.

### Logical (3 opcodes, push 1-bit result)

`LOG_AND`, `LOG_OR`, `LOG_NOT`.

### Unary / Reduction (8 opcodes)

`NEG`, `UPLUS`, `RED_AND`, `RED_OR`, `RED_XOR`, `RED_NAND`, `RED_NOR`, `RED_XNOR`.

### Special Expression (8 opcodes)

| Opcode | Args | Stack Effect | Description |
|--------|------|-------------|-------------|
| `BIT_SELECT` | | target, index → result | target[index] |
| `RANGE_SELECT` | | target, msb, lsb → result | target[msb:lsb] |
| `PART_SEL_UP` | | target, base, width → result | target[base +: width] |
| `PART_SEL_DOWN` | | target, base, width → result | target[base -: width] |
| `CONCAT` | n_parts | val_n..val_1 → result | {val_1, ..., val_n} |
| `REPLICATE` | | count, value → result | {count{value}} |
| `TERNARY` | | cond, true, false → result | x-merge on x/z condition |
| `SIGN_EXT` | target_width | val → val' | Sign-extend TOS to target_width bits |

### Control Flow (6 opcodes)

`JUMP`, `JUMP_IF_ZERO`, `JUMP_IF_NONZERO`, `DUP`, `POP`, `NOP`.

Backward `JUMP` instructions are guarded by a loop counter (default limit
100,000 iterations) to prevent infinite loops.

### Memory Array Operations (5 opcodes)

| Opcode | Args | Stack Effect | Description |
|--------|------|-------------|-------------|
| `LOAD_MEM` | mem_id | idx → val | Read memory element |
| `STORE_MEM` | mem_id\|marker<<16 | val, idx → | Blocking memory write |
| `NBA_MEM` | mem_id\|marker<<16 | val, idx → | Non-blocking memory write |
| `STORE_MEM_RANGE` | mem_id | val, idx, msb, lsb → | Blocking partial write (bit range) |
| `NBA_MEM_RANGE` | mem_id | val, idx, msb, lsb → | Non-blocking partial write (bit range) |

### System Tasks (10 opcodes)

| Opcode | Args | Stack Effect | Description |
|--------|------|-------------|-------------|
| `SYS_DISPLAY` | n_args\|fmt<<16 | args → | Format with $display semantics |
| `SYS_MONITOR` | n_args\|fmt<<16 | args → | $monitor (scheduler re-fires) |
| `SYS_FINISH` | | (none) | Halt simulation |
| `SYS_TIME` | | → val | Push current simulation time |
| `SYS_READMEM` | task_id | (none) | $readmemh/$readmemb from file |
| `SYS_FOPEN` | task_id | → fd | $fopen (returns file descriptor) |
| `SYS_FCLOSE` | | fd → | $fclose |
| `SYS_FDISPLAY` | n_args\|fmt<<16 | fd, args → | $fdisplay |
| `SYS_FWRITE` | n_args\|fmt<<16 | fd, args → | $fwrite |
| `SYS_FEOF` | | fd → 0/1 | $feof |

### Built-in Functions (2 opcodes)

`FUNC_CLOG2` ($clog2), `FUNC_RANDOM` ($random).

### Process Management (1 opcode)

`PROC_END` — terminates bytecode execution for the current process.

### Format Strings

`$display`/`$monitor`/`$fdisplay`/`$fwrite` support Verilog format strings
(`%d`, `%h`, `%b`, `%o`, `%s`, `%t`, `%m`, `%%`). Format strings are stored
in a `display_formats: list[str]` table at compile time. The format string
ID (1-indexed) is encoded in the upper 16 bits of `arg1`.

---

## Compiler Design (`compiler.py`, 2,601 lines)

### Overview

The compiler walks the model AST at elaboration time and produces:

1. **Signal layout**: `signal_map: dict[str, int]` + flat arrays
2. **Constant pool**: Deduplicated literal values
3. **Memory layout**: `mem_map`, flat `mem_val`/`mem_mask` arrays
4. **Compiled processes**: List of `CompiledProcess` objects, each containing:
   - Bytecode program (instruction array)
   - Process type (CONTINUOUS, COMBINATIONAL, SEQUENTIAL, INITIAL)
   - Sensitivity set (set of signal IDs)
   - Edge triggers (dict of signal_id → "posedge"/"negedge")
   - `has_timing` flag (True if process contains `#delay` or `@event`)
5. **Monitor programs**: Self-contained bytecoded programs for `$monitor` re-fire
6. **Task tables**: `readmem_tasks`, `fopen_tasks`, `display_formats`

### Compilation Pipeline

```
Module
  ├── nets/variables/ports → signal_map (name → ID) + optional mem_map
  ├── continuous_assigns    → CompiledProcess (CONTINUOUS)
  ├── always_blocks
  │   ├── combinational    → CompiledProcess (COMBINATIONAL)
  │   └── sequential       → CompiledProcess (SEQUENTIAL)
  └── initial_blocks       → CompiledProcess (INITIAL, has_timing=True/False)
```

### Expression Compilation

Expressions compile to stack-based bytecode in **post-order** (children
before parent):

```
  a + b   →  LOAD_SIG(a_id), LOAD_SIG(b_id), ADD
  a[3]    →  LOAD_SIG(a_id), LOAD_CONST(3), BIT_SELECT
  mem[i]  →  <compile i>, LOAD_MEM(mem_id)    // memory read
  c?t:f   →  <compile c>, <compile t>, <compile f>, TERNARY
```

The ternary operator was changed from a branch-based implementation to a
single `TERNARY` opcode that evaluates both branches and merges on x-condition.
This simplifies the instruction stream and handles x/z conditions correctly.

### Statement Compilation

```
  a = b + 1;     →  LOAD_SIG(b_id), LOAD_CONST(1), ADD, RESIZE(w), STORE_SIG(a_id)
  a <= b + 1;    →  LOAD_SIG(b_id), LOAD_CONST(1), ADD, RESIZE(w), NBA_SIG(a_id)

  if (cond)      →  <compile cond>, JUMP_IF_ZERO(L_else),
    s1;               <compile s1>, JUMP(L_end),
  else                L_else: <compile s2>,
    s2;               L_end:

  case (sel)     →  <compile sel>,
    val1: s1;         DUP, <compile val1>, CMP_EQ, JUMP_IF_NONZERO(L1),
    val2: s2;         DUP, <compile val2>, CMP_EQ, JUMP_IF_NONZERO(L2),
    default: s3;      JUMP(L_default),
  endcase             L1: POP, <compile s1>, JUMP(L_end),
                      L2: POP, <compile s2>, JUMP(L_end),
                      L_default: POP, <compile s3>, JUMP(L_end),
                      L_end:
```

### LHS Compilation

Assignment targets emit different opcodes based on target type. Every
identifier store is preceded by `RESIZE(target_width)`:

- **Identifier**: `RESIZE(w), STORE_SIG(id)` or `NBA_SIG(id)`
- **Bit select**: Compile index, then `STORE_BIT(id)` or `NBA_BIT(id)`
- **Range select**: Compile msb + lsb, then `STORE_RANGE(id)` or `NBA_RANGE(id)`
- **Part select**: Convert to effective msb/lsb via arithmetic, then `STORE_RANGE`
- **Memory element**: Compile index, then `STORE_MEM(mid|marker<<16)`
- **Concatenation**: DUP the RHS, extract bit ranges for each part, recursive store

### Timing Detection

The compiler returns `has_timing=True` from `_compile_stmt` when it encounters
`DelayControl`, `EventControl`, or `WaitStatement` nodes. Processes with
timing controls are routed to the reference executor's coroutine path by
the VMScheduler (see Coroutine Fallback below).

---

## Cython Interpreter (`_interp_fast.pyx`, 4,588 lines)

### Overview

The Cython module provides two tiers of acceleration:

1. **`_execute_core()`**: C function that interprets a single bytecode program
   against C arrays. No Python objects in the inner loop — all 4-state logic
   is done with `(val, mask, width)` integer triples stored in a fixed-size
   `SVal stack[256]`.

2. **`CyContext`**: Persistent C-native simulation context that owns all signal,
   constant, and program data in `malloc`'d C arrays. Provides `run_delta_loop()`
   which runs the entire delta-cycle convergence loop in C without acquiring
   the GIL.

### `_execute_core()` — The Inner Loop

The core function signature:

```c
cdef int _execute_core(
    const int *prog_ops,  const int *prog_a1,  int prog_len,
    long long *sig_val,   long long *sig_mask,  const int *sig_width,
    const long long *const_val, const long long *const_mask, const int *const_width,
    NBAEntry *nba_buf,    int *nba_count,
    int *dirty_buf,       int *dirty_count,
    long long sim_time,
    long long *mem_val,   long long *mem_mask,    // memory arrays
    long long *disp_buf,  int *disp_pos, int disp_cap,   // display output
) noexcept nogil
```

Key design decisions:
- **`noexcept nogil`**: No Python exceptions, no GIL — pure C execution
- **Fixed-size stack**: `SVal stack[256]` on the C stack (no allocation)
- **Separate opcode/arg arrays**: `prog_ops[pc]` and `prog_a1[pc]` instead of
  tuple unpacking — better cache locality
- **Inline width masks**: `mask_for_width()` as `cdef inline` with special
  case for width ≥ 64
- **Direct signal writes**: `STORE_SIG` compares before writing and only
  marks dirty if the value actually changed (avoids false triggers)
- **Display output buffer**: Flat `long long` array with layout
  `[fmt_id, n_args, is_monitor, v0, m0, w0, v1, m1, w1, ...]` per event

### `CyContext` — Persistent C Context

`CyContext` is a Cython `cdef class` that owns all simulation data in
`malloc`'d C arrays:

```
CyContext fields:
  sig_val/sig_mask/sig_width     # Signal arrays (long long*/int*)
  const_val/const_mask/const_width  # Constant pool
  all_ops/all_a1                 # Flattened program arrays
  prog_offset/prog_length        # Per-process index into all_ops
  nba_buf, dirty_buf, disp_buf   # Output buffers
  mem_val/mem_mask/mem_info      # Memory arrays
```

#### Setup Methods

- **`setup(sig_val, sig_mask, sig_width, const_*, programs)`**: Allocate C
  arrays and copy data from Python lists. Programs are flattened: all
  instruction opcodes concatenated into `all_ops`, with `prog_offset[i]`
  and `prog_length[i]` recording where each process starts.

- **`setup_memory(mem_val, mem_mask, mem_info)`**: Copy memory arrays to C.

- **`setup_processes(proc_types, sig_sens_lists, cont_*, edge_*)`**: Build
  the delta-loop data structures (see CSR Sensitivity Index below).

#### CSR Sensitivity Index

For delta-loop convergence, CyContext stores the sensitivity mapping as a
**Compressed Sparse Row (CSR)** structure — O(1) lookup per dirty signal:

```
sens_offset: int[sig_count + 1]   # CSR row pointers
sens_procs:  int[total_entries]   # Process indices
```

To find all processes sensitive to signal `sid`:
```c
for (j = sens_offset[sid]; j < sens_offset[sid + 1]; j++)
    triggered[sens_procs[j]] = 1;
```

Continuous assigns have a separate CSR:
```
cont_sens_offset: int[cont_count + 1]
cont_sens_sigs:   int[total_entries]   # Signal IDs
```

### `run_delta_loop()` — C Delta Loop

The entire delta-cycle convergence loop runs in C (`nogil`):

```
run_delta_loop(changed_sids, delta_limit):
  Phase 0: Run dirty continuous assigns on initial changed set
  Phase 1: Iterate until convergence:
    1. Collect triggered procs (combo via CSR + seq via edge check)
    2. Execute all triggered procs via _execute_core()
    3. Merge blocking-assign dirty signals
    4. Apply NBAs (compare-before-write)
    5. Apply memory NBAs
    6. Run dirty continuous assigns
    7. If no changes → converged; break
```

Edge detection for sequential processes is done inline:
- Snapshot arrays `snap_val/snap_mask` are taken at the start of each time step
- `seq_fired[pid]` flag prevents a sequential process from firing more than
  once per time step

### Sync Functions

Because initial blocks and always blocks with timing controls use the
reference executor, signal state must be synced between C arrays and Python:

- **`sync_signals_to_lists(py_val, py_mask)`**: Copy C arrays → Python lists
- **`sync_signals_from_lists(py_val, py_mask)`**: Copy Python lists → C arrays
- **`sync_mem_to_lists` / `sync_mem_from_lists`**: Same for memory arrays

---

## VM Scheduler Design (`vm_scheduler.py`, 1,566 lines)

### Overview

`VMScheduler` provides the same interface as the reference `Scheduler` but
uses compiled bytecode processes and the Cython interpreter. It manages the
event queue, delta loop orchestration, and the coroutine fallback for timing
controls.

### Elaboration Phase

1. Call `Compiler.compile_module(module)` — produces signal arrays, constant
   pool, bytecode programs, memory layouts
2. Create `Interpreter` (Python fallback) wired to compiler's arrays
3. Categorize processes: continuous, combinational, sequential, initial
4. Build inverted indices: `_sig_to_cont`, `_sig_to_combo` (signal ID →
   list of processes, for fast dirty lookup)
5. If Cython available: create `CyContext`, call `setup()`, `setup_memory()`,
   `setup_processes()` with CSR data
6. Build reverse signal map (`sid → name`) for coroutine sync optimization
7. Set up reference executor for timing-control fallback

### Execution Flow

```
run(max_time)
  ├── Execute initial blocks at t=0
  │   ├── Without timing: VM interpreter directly
  │   └── With timing: reference executor coroutine
  ├── Schedule always blocks with timing controls
  ├── Bootstrap continuous assigns + combinational always at t=0
  ├── Activate $monitor (if registered)
  ├── Fire t=0 time-step callback
  └── _run_event_loop(max_time)
       └── for each time step:
            ├── Advance time
            ├── Snapshot signals for edge detection
            ├── Pop and execute events (clock toggles, coroutine resumes)
            ├── If Cython + process tables ready:
            │    └── CyContext.run_delta_loop(changed, delta_limit)  // all in C
            ├── Else (Python fallback):
            │    └── Python delta loop (run_dirty_continuous → collect_triggered → ...)
            ├── Fire $monitor if arguments changed
            └── Fire time-step callback
```

### Coroutine Fallback

Processes with timing controls (`#delay`, `@(event)` — flagged with
`has_timing=True`) cannot be compiled to flat bytecode because they require
process suspension at arbitrary points. These are handled by the reference
executor's coroutine mechanism:

1. **Sync**: `_sync_ref_ctx(names)` copies VM signal values → reference
   `EvalContext` (optimized: only syncs signals the coroutine touches)
2. **Execute**: `execute_coroutine(block.body, ref_ctx)` — Python generator
   that yields at suspension points
3. **Sync back**: `_sync_from_ref_ctx(names)` copies reference signals → VM
   arrays, tracking which signals actually changed
4. **Schedule**: For `#delay`, schedule a resume event at `time + delay`
5. **Repeat**: On resume, sync → `next(coro)` → sync back → schedule next

For always blocks with timing (e.g. `always #5 clk = ~clk;`), the coroutine
is re-created when it completes (always blocks loop forever).

**Sync optimization**: The scheduler pre-computes `_coro_sync_names[proc_id]`
— the set of signal names each coroutine reads or writes. Only those signals
are synced, avoiding a full-array copy on every resume.

### $monitor Re-fire

`$monitor` fires once when first encountered (via `SYS_MONITOR` opcode),
then the scheduler re-fires it at the end of each time step if any monitored
signal changed. Implementation:

1. First execution: interpreter sets `active_monitor_id`
2. Scheduler detects activation via `_check_monitor_activation()`
3. Stores `(monitor_program, sensitivity_sigs)` and snapshots current values
4. `_fire_monitors()` at end of each time step: compare current vs snapshot,
   if changed → execute the monitor mini-program → collect display output

### Display Output Draining

With Cython, `$display` output is buffered in a flat C array during
`run_delta_loop()`. After the loop returns, `_drain_cy_display()` reads
the buffer via `CyContext.drain_display_buffer()` and formats each event
using `_format_display()`.

### Inverted Indices

For O(|changed|) process lookup instead of scanning all processes:

```python
_sig_to_cont: dict[int, list[CompiledProcess]]   # continuous assigns
_sig_to_combo: dict[int, list[CompiledProcess]]   # combinational always
```

In Cython, these are replaced by the CSR structure in `CyContext`.

---

## Integration with Simulator

```python
# Reference engine (default)
sim = Simulator(module, engine="reference")

# Bytecode VM engine (auto-uses Cython if available)
sim = Simulator(module, engine="vm")
```

Both engines expose identical behavior. Cross-validation tests (41 test
functions) run each test on both engines and compare final signal values,
display output, and VCD waveforms.

---

## Testing

- **191 VM unit tests** (`tests/test_sim/test_vm.py`): Cover all opcodes,
  statement types, edge cases, memory arrays, file I/O, $monitor
- **41 cross-validation tests** (`tests/test_validation/test_vm_vs_reference.py`): Run
  the same test on both engines and compare results
- **74 Icarus validation tests** (`tests/test_validation/test_iverilog_validation.py`):
  Compare VM output against Icarus Verilog reference output

---

## Current Limitations

- **`disable` statement** — not supported in VM compilation
- **True `fork/join`** — parallel blocks are still compiled as sequential with a warning
- **Timed statements suspend through the scheduler** — `#delay`, `@(event)`, and `wait`
  are supported through the surrounding simulator, but they do not stay on the pure
  straight-line bytecode fast path
- **Bytecode executes the elaborated / flattened module** — hierarchy and generate
  support lives in elaboration first, then the VM runs the flattened result
