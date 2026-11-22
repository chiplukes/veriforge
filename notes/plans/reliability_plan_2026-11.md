# Simulator reliability plan (2026-11)

**Status**: in progress. Steps 1a/1b (divergence locator, reducer), 2c
(corpus harness) and 5a (memory measurement) done, and step 2a (construct
census) started, on 2026-11-17.

## Why

The random differential fuzzer (`src/veriforge/fuzz/`, `notes/developer/fuzzer.md`)
has covered expression and statement semantics well: widths, signedness, x
handling, shifts, casts, functions, tasks, memories, `$display`. The
October coverage round (`fuzzer_coverage_2026-10.md`) closed with nothing open.

Bugs still show up in larger simulations, but they are a different kind.
The fixes made through November 2026 were almost all structural:

- hierarchical function/task binding, generated-register hierarchical refs;
- multidimensional packed-array indexing across engines;
- parameter expression widths during elaboration; genvar bit-selects in
  generate-if;
- reference fanout after indexed continuous assigns; nested assignment
  target propagation;
- compiled codegen that grew exponentially or ran out of memory on big designs.

The fuzzer builds small single modules (or a parent with one child). It
rarely generates deep hierarchies, generate blocks, parameter overrides,
packed types across ports, or designs large enough to hit codegen and
scheduler limits. When a large design fails, reducing it to a test case is
still done by hand: "fuzzer discovers pattern → human reduces → human
writes deterministic test".

A second cost: `known_issues.md` lists about 20 Icarus quirks. Each one is a
false mismatch that had to be investigated and then filtered out.

## Strategy

1. Make a failure in a large design cheap to reduce. This speeds up every
   later step.
2. Test against real code (a corpus) instead of only generated code, and
   measure which constructs real code uses that we do not test.
3. Add metamorphic checks: transforms that must not change behaviour,
   needing no external oracle.
4. Extend the fuzzer toward structure and scale, ranked by the census
   from step 2.
5. Treat memory and scale as their own target: measure every engine's time
   and memory on real designs, test growth against size (not only
   expression depth), and turn runaways into errors.

Oracles: Verilator is the main oracle for SystemVerilog and structural
work. Icarus is kept for 4-state (x/z) checks only. All four veriforge
modes are always compared with each other.

## Step 1: Triage tooling

### 1a. First-divergence locator

Input: the same design and stimulus run on two engines (or one engine and
Verilator), each producing a VCD.

- Align the signal hierarchies and find the earliest time and delta at
  which any signal differs (`sim/vcd_compare.py` already does part of
  this).
- Walk the fan-in cone of that signal back to the earliest diverging
  driver, using the elaborated design's driver/reader analysis. Report the
  instance, process or continuous assign, and source location.
- Generalize the design-specific pieces in
  `examples/darkriscv/diag_compare.py` and `examples/ibex/vcd_compare_verilator.py`.
- CLI: `uv run -m veriforge.sim.diverge A.vcd B.vcd --design ...`.

### 1b. Automatic design reducer

A delta-debugging tool, in the style of C-Reduce, that works on the parsed
model (not text). It takes a design plus a predicate ("engines X and Y
mismatch", "compiled build crashes", "parse fails") and repeatedly:

- deletes module items, statements, ports and instances;
- flattens or inlines one instance;
- replaces an expression or operand with a constant or a single signal;
- narrows widths and lowers parameter values;
- re-emits through `codegen/verilog_emitter.py`, keeping each change only
  if the predicate still holds.

Output: a minimal `.v` file and a pytest skeleton that drops into
`tests/test_sim/test_fuzz_findings.py` style tests.

## Step 2: Real-design corpus

### 2a. Construct census (started)

Parse every source in the corpus and count grammar-rule usage per file and
per project. Do the same for a large batch of fuzzer-generated designs
(emitted, then re-parsed, so both sides are counted the same way). Report:

- rules real code uses that the fuzzer never generates, ranked by how
  often real code uses them;
- parse failures per project (unsupported syntax in real code);
- rules the fuzzer generates often but real code rarely uses (wasted fuzzer
  effort).

Tool: `tools/construct_census.py`.

### 2b. Corpus sources

- Already in the repo: `examples/` (ibex, picorv32, serv, femtorv,
  darkriscv, taxi, pulp common_cells/axi, axi, axis).
- Feature-organised suites: sv-tests (CHIPS Alliance), ivtest (Icarus
  regression suite), Verilator `test_regress`.
- More IP: OpenTitan `prim`, cv32e40p, ZipCPU / wb2axip, BaseJump STL.

Keep external corpora out of the repo (a fetch script plus a pinned commit
list), with an opt-in pytest marker.

### 2c. Per-module differential harness

For every module in every corpus project (not just top levels):

- elaborate with the default parameter values and a few random overrides;
- auto-generate stimulus: clock detection (`analysis/` clock/reset
  analysis), a reset sequence, random inputs for N cycles;
- run reference, vm, vm-fast and compiled, plus Verilator; compare outputs
  cycle by cycle;
- record unsupported constructs, elaboration errors, crashes and
  mismatches separately, and send mismatches to the reducer (1b).

## Step 3: Metamorphic testing

For any design (generated or corpus), transforms that should leave
behaviour unchanged, with all engines required to match before and after:

- flatten the hierarchy; unroll generate blocks; substitute parameter values;
- split expressions into temporary wires; inline functions;
- reorder module items and port connections; rename every identifier;
- `assign` ↔ `always_comb`; named ↔ positional port connections;
- emit → re-parse → simulate.

Reuse `transforms/` and `refactor/` where they exist. Flattening and
unrolling exercise elaboration paths, where many recent bugs were.

## Step 4: Structural fuzzer strategies

Choose and order these using the census (2a):

- generate for/if/case, nested, with genvar-derived widths and selects;
- parameter overrides (`#(...)` and `defparam`) with widths derived from
  parameters, `localparam` chains;
- hierarchies 3–5 levels deep with parameters passed down; hierarchical
  references up and down;
- packed multi-dimensional arrays and packed structs across ports;
- multiple clocks, negedge, clock enables;
- scale mode: stitch many generated modules into designs with thousands
  of processes, to hit compiled codegen and scheduler limits.

Code coverage: run `coverage.py` over `src/veriforge/sim/` during fuzz and
corpus runs. Report code neither reaches, and keep seeds that reach new lines.

## Step 5: Memory and scale (added 2026-11-17)

**Why.** Memory and blow-up problems are the most commonly fixed
simulation failures, mostly in the compiled engine:
- generated code growing faster than the design: exponential chain codegen
  (`65dc2cf`); codegen made linear for every node type (`a4f5e9a`; an
  8-term `&&` gave 334 MB of C, a 16-way select as a memory index used 27
  GB); nested streaming concatenations (`311351a`);
- the C compiler running out of memory (`4526a61`, table-driven dispatch);
- runtime and build memory: snapshot footprint (`642e8d5`, `69ae120`),
  scratch arrays sized by `$readmem` arguments (`bbed248`), the build
  thread's stack (`3872b71`).

On 2026-11-17 one runaway reference-engine check (`serv_rf_ram` with
invalid default parameters) grew to 29 GB, and the OOM killer took the WSL
VM down with it.

**Already covered.** Expression nesting: `test_codegen_linearity.py` checks
40 constructs × 39 contexts for linear growth, and `VERIFORGE_CODEGEN_STRICT`
plus the 4 MB unscoped-text stop guard emission. Nothing measures sizes
other than nesting depth, real designs, the C build, or runtime memory.

### 5a. Measure on real designs (done)

`tools/corpus_check.py` runs each engine in its own subprocess under an
address-space cap (`--mem-limit-gb`, inherited by the C compiler). Per run it
records peak resident memory of the engine and of its build children,
setup time (elaboration and build) and run time, and compiled's generated
code size. A run over time or memory where reference was fine is the
`engine-resource` outcome. The report ranks the heaviest runs per engine,
with each as a multiple of reference, and the largest generated code.
`--compile-cache` with a fresh directory measures every build.

First numbers (serv, 2026-11-17):
- Every compiled build generates about 2 MB of Cython/C, even for a
  trivial module, and takes about 4.5 s and 215 MB. A whole core (servile)
  takes 6.7 MB, 10 s and 330 MB. The floor looks like fixed runtime-helper
  code repeated in every build.
- `serv_rf_ram` (invalid defaults): reference and vm use 3.3 GB and 122 s,
  vm-fast and compiled 120 MB and 0.1 s.

### 5b. Scaling matrix beyond expression depth

Apply `test_codegen_linearity.py`'s method (measure at N, 2N, 4N; linear
growth gives a ratio of 2) to sizes rather than nesting depth:
- statements: if/else-if chains, `case` items, nested `begin`/loops,
  `for` loop bounds;
- structure: processes per module, continuous-assign chains, instances
  and generate iterations (each with a process or instance inside),
  ports, fan-out of one signal to N readers;
- data: memory depth and word width (64·k bits), number of memories,
  `$readmem` sizes.

Measure generated code size (codegen only, fast), and for a subset the
build's peak memory and the runtime memory. Fail on super-linear growth
where linear is expected.

### 5c. Runtime memory over long runs

Run designs for 1k and 10k cycles on each engine, and check that resident
memory is flat (snapshot journals, NBA queues, VCD buffers, trace hubs):
a leak shows as growth with cycles.

### 5d. Guard rails

- Compiled build: an address-space cap on the Cython and C-compiler child
  processes (configurable, e.g. `VERIFORGE_BUILD_MEMORY_GB`), so a runaway
  compile fails with an explanation instead of OOM-killing the machine.
  Estimate the generated C size before invoking the compiler and stop
  above a budget with the largest processes named.
- Reference/vm: refuse a select or value of absurd width (the
  `serv_rf_ram` case) with an error instead of allocating.

### 5e. Build floor

Find what makes up compiled's ~2 MB per build. If it is mostly fixed
helpers, move them into a shared, precompiled runtime extension, so each
design's build only compiles its own code. That would cut build time and
build memory for every design.

## Census results (2026-11-17, `examples/` only)

Run: `uv run python tools/construct_census.py` (30 s with 8 jobs). Corpus:
281 unique sources in 12 projects (pulp makes up 176 of them), 274 parsed.
Fuzzer: 40 designs × 10 strategies, all emitted and re-parsed.

**Never generated by the fuzzer, used across many projects.** These match
the recent structural bug classes closely:

| construct | corpus files | projects |
|---|---:|---:|
| parameter override on an instance (`#(.P(..))`) | 120 | 9 |
| `wire x = expr;` (net declaration assignment) | 34 | 9 |
| `initial` blocks | 56 | 8 |
| `localparam` | 49 | 7 |
| processes inside generate blocks | 45 | 7 |
| indexed part-select `a[b +: w]` | 20 | 7 |
| generate-if (and `else`) | 51 | 6 |
| nested generate (depth 2–4) | 28 | 6 |
| instances inside generate blocks | 15 | 6 |
| `@(a or b)`, `negedge` event controls | 76 / 75 | 5 / 4 |
| constant system functions (`$clog2`...) | 39 | 5 |
| dotted names (struct fields, hierarchical refs) | 29 | 5 |
| `{N{...}}` in constant expressions | 15 | 3 |
| typed parameters (`parameter int unsigned`, `logic`, `bit`) | 105 | 4 |
| SV: `'0`/`'1` fill literals, `always_comb`/`always_ff`, typedef, packed struct, enum, package/import, `unique case`, `++`, multiple packed ranges | 7–71 | 2 |

`delay_value` (35 files, 6 projects) is mostly testbench `#N` delays.

**Generated far less often than real code uses them.** `if`/`else`
statements are in 55% of corpus files but only 6% of fuzzer designs (only
the NESTED_BLOCKS and HIERARCHICAL strategies generate them). The same holds
for module instances (58% vs 10%), generate blocks (23% vs 4%), and `==`, `!=`,
`&&`, `||`, binary `-`.

**Generated often, rarely in real code.** `signed` ports, parameters and
regs (≈0% corpus vs 40–80% fuzz), streaming concatenation (0% vs 36%),
reduction XOR/NOR/XNOR/NAND, tasks, automatic functions, `int` variables.
That is the expression-semantics work from October. It is still worth
keeping, but the default strategy weights could shift toward structure.

**Parser gap found.** `input wire logic clk` (port with net type plus data
type) is a parse error. It accounts for all 7 failures, all in taxi
`vendor/` (recorded in `known_issues.md`).

**Next for 2a.** Add sv-tests and ivtest as corpora (external checkouts via
`--corpus`). Separate testbench files from RTL so testbench-only constructs
(`#delay`, `initial`) are ranked separately. Use the results to order the
step 4 strategies. First candidates: parameter overrides with `localparam`
chains, generate-if and nested generate with processes and instances,
`if`/`else` in every clocked strategy, and `+:` selects.

## Done

- (2026-11-17) Plan written.
- (2026-11-17) Step 2a: `tools/construct_census.py` (tests:
  `tests/test_fuzz/test_construct_census.py`); first results above.
- (2026-11-17) Step 1a: `veriforge.sim.diverge` (tests:
  `tests/test_sim/test_diverge.py`; usage in `notes/simulation/debug.md`).
  It runs two engines on the same stimulus, or loads two VCDs, and walks
  back through the drivers of the flattened design. On its first real
  run (servile SoC, reference vs compiled) it found that compiled drops
  the carry of `{c, s} = a + b`. vm and vm-fast agree
  with reference on servile, `axis_skid_buf` and `fifo_v3`.
- (2026-11-17) Step 1b: `veriforge.reduce` (tests:
  `tests/test_sim/test_reduce.py`). Passes: delete, statements,
  expressions. Predicates: engine mismatch, error regex, external
  command. Validity guard: Icarus. Checks run in a worker process with a
  timeout. Writes a pytest skeleton. On the full servile SoC, focused on
  the locator's root signal (`--signal cpu.ctrl.pc_plus_4_cy`), it went
  from 81,535 to 343 characters in 680 checks (29 min, almost all compiled
  builds), leaving just the carry assign inside the original hierarchy.
- (2026-11-17) Fixed the compiled concatenation-target bug: the RHS
  of `{...} = rhs` is now evaluated at the target's total width
  (`_concat_assign_rhs_width`, procedural and continuous). Wide continuous
  targets take the scratch path. The fix covers the carry, sign extension
  of a signed RHS, and x (`tests/test_sim/test_concat_target_context.py`,
  all engines, >64 bits).

- (2026-11-17) Fuzzer: the harness now starts `initial` blocks with one
  `run(max_time=0)`, but only for designs that have any, so the Icarus
  start-up alignment is unchanged. The feedforward and clocked strategies
  now write `{a, b} = e` targets and give registers `initial` values. The
  first campaign found a vm-fast start-up crash: any 64-bit memory plus an
  `initial` block (`sync_mem_from_lists`, signed words; fixed,
  `tests/test_sim/test_memory64_initial.py`). It also found an Icarus quirk
  with 2-state concatenation parts, now filtered (`known_issues.md`).
- (2026-11-17) Step 2c: `tools/corpus_check.py`. Its smoke run found a
  reference bug: a replication with a parameter count, including 0, was
  sized as its inner width (`{a, {B{1'b0}}}`, serv_csr). Fixed,
  `tests/test_sim/test_replication_count.py`.

- (2026-11-17) First full corpus run (247 checks, four engines). 69 of
  87 mismatches were one start-up inconsistency: a reset driven before
  start-up and passed through a port fired a submodule's `negedge rst_n`
  on vm/vm-fast/compiled after `run()` (and on vm/vm-fast after `settle()`),
  but not on reference. Fixed on every engine: a pre-start drive's
  continuous fan-out is a starting value too
  (`tests/test_sim/test_startup_reset_through_port.py`; full `tests/test_sim/`
  8086 passed).

- (2026-11-17) Triage of the second corpus run's 20 mismatches and 13 engine errors:
  - compiled: an indexed part-select with an x/z base read data (base taken as 0), and the whole-signal slice helpers read out-of-range bits as a defined 0 (`test_partselect_x_base.py`, verilog-axi crossbar);
  - compiled: a signed `+ - * / %` re-sign-extended a nested context-determined operand from its static self-width (`x * y + c`, `(x + y) + c`; ibex `mac_res_signed`; `test_signed_nested_arith.py`);
  - compiled: a continuous assign reading a field of an unpacked array of packed structs lost the x mask (`test_struct_array_field_mask.py`, pulp mailbox wrapper);
  - reference: `$bits(typedef)` returned 1 (folded to a constant in `lower_sv_functions` for every engine; `test_bits_of_typedef.py`, ibex `cs_registers`);
  - harness false positives: modules invalid with their default parameters (struct fields on `parameter type t = logic`, zero-width `[N-1:0]` ports) are skipped and listed; clocks are detected by name token (`wb_clk_i`, `src_clk_i`, `aclk`), so they are no longer driven as random data racing other inputs.

- (2026-11-17) Third corpus run: 211 agree, 2 mismatches (from 20), 10 engine errors. Fixed after it:
  - every engine (elaboration): a generate block's localparam was one shared, unscoped parameter, so an instance override using it (`.ProvideValUpd(Cnt == 10)`, ibex mhpmcounters) saw the last iteration's value; now substituted per iteration, including into typedef widths (`test_generate_loop_scoping.py`; a typedef case found by the pulp axi_lite_dw DSL bench during the change);
  - every engine (parser): named function arguments (`f(.rs(a), .b(1))`) were dropped; now kept as `FunctionCall.arg_names` and lowered to positional (`test_named_function_args.py`, ibex compressed decoder);
  - reference: range/part-select writes to a generate-loop signal (`gen[0].c.q[63:40]`) were dropped by a guard meant for unresolved struct-memory names;
  - reference: a unary `- ~ +` evaluated its operand at the narrower context, truncating a nested shift (`-(a <<< 7)` into 1 bit; fuzz hierarchical seed 5057; `test_unary_context_width.py`);
  - compiled: `'0`/`'1`/`'x` into a range of a >64-bit signal failed to build (`test_wide_range_fill_literal.py`);
  - vm-fast: `write_mem` raised OverflowError for a 64-bit element with its top bit set (2-D packed input ports, ibex_if_stage);
  - harness: type parameters passed down to a struct-using child also mark the parent invalid with its defaults (`axi_to_axi_lite`).
- (2026-11-17) Re-emitting ibex for the reducer (which re-parses every candidate) found:
  - every engine: package-qualified values without an `import` (`input [p::W-1:0] a`, `M == p::C`) were never resolved -- 1-bit ports and x values; now resolved on a copy of the design (`test_package_qualified_values.py`);
  - every engine: a size cast in a parameter value (`32'(!RV32E) << 8`) -- the parser built `!R` as `Identifier("!")` and the constant evaluator had no WidthCast case (`test_const_width_cast.py`);
  - emitter: `pkg::name` written as `pkg.name`, assignment patterns, select targets of generate-block arrays, port-less functions, `$time()`, generate-block functions/typedefs, and typedef ranges (`[W:-:1:0]`, `$clog2W`) all produced text that doesn't parse (`tests/test_formatter/test_emit_sv_roundtrip.py`); `EnumType`/typedef-alias `_child_nodes` returned a Range, which broke `walk()`.
- Compiled gaps found on the way (they refuse to build, not silent): a size cast wider than 64 bits (`128'(x[100:0] + 1)`), `$countones` of a >64-bit value, `**` with a variable exponent on a >64-bit base. (2026-11-19: the cast and `$countones` already worked in every shape tried; `**` and the picorv32 string are fixed, below.)
- (2026-11-18) The ibex reducer run crashed WSL: a candidate's generate loop fell back to 2**16 iterations, the compiled build was 1.3M lines of Cython (2.7M for an earlier candidate), and a timed-out check's build kept running beside the next. Fixed both ways: reducer checks run capped (`--mem-limit-gb`) in their own process group, killed whole on timeout, with the best design checkpointed to `-o`; processes differing only in constants share one generated body (`_families.py`, `VERIFORGE_COMPILE_FAMILY_MIN`), so a generate loop costs a wrapper per iteration instead of a body (about 8x smaller for a flip-flop loop).
- (2026-11-19) Wide gaps (`tests/test_sim/test_wide_pow_string_countbits.py`, all engines):
  - compiled: `**` whose exponent reads a wide value (`x ** x[3:0]`) or is wider than 64 bits stopped the build; the exponent is now evaluated into scratch words and passed to `wide_pow_w`;
  - compiled: a string literal into a >64-bit destination stopped the build (picorv32's `dbg_ascii_state = "ld_rs1"`); picorv32 now agrees with reference on all 8 modules;
  - every engine: `$countbits` was unimplemented (reference x, compiled 0, vm/vm-fast raised); now lowered per bit with `$countones`. x and z share one representation, so a `'x` or `'z` control counts both. Icarus 14 aborts on `$countbits`.
- (2026-11-19) Struct array fields (`tests/test_sim/test_struct_array_field_index.py`): the parser keeps `m[idx].f` with its index as text, and every engine read that text as a signal name. An expression index (`m[k ^ 1].f`, `m[k[0]].f`) was silently not read or written on reference, raised on vm/vm-fast, and raised or used the wrong element on compiled; in a submodule even a plain name failed (flattening prefixed `u.m` but not the index), and compiled could not resolve a numeric one (`i_tb.addr_map[0].idx`, the 4 pulp xbar test benches). `lower_struct_array_fields` now rewrites every such access as an element part-select, `m[idx][hi:lo]`, on each module before flattening.
- (2026-11-19) Struct-typed parameters written as a value, not a pattern (`parameter axi_pkg::xbar_cfg_t Cfg = '0`, pulp's axi_lite_xbar), found once those benches built on compiled: fields read x on reference/vm/vm-fast and 0 on compiled (even for `'1`), and `$bits(Cfg)` was the `'0`'s 32. `resolve_sv_imports` now brings in a package-qualified parameter type (and the package's own types its fields use unqualified), `lower_struct_param_fields` takes the layout from the declared type, and a typedef'd parameter's width is its type's (`test_struct_param_fields.py`). Both axi_lite_xbar benches now agree on all four engines when run with their own `axi_pkg`.
- (2026-11-20) The ibex reducer run (22 h, 349857 -> 904 chars) drifted: Icarus rejects the emitted ibex, so candidates were not validity-checked, and the result's divergence is an undeclared name (`err_q[i] <= err_d` with `err_d` deleted), which reads x on reference/vm/vm-fast and 0 on compiled. The original divergence is internal, `fifo_i.err_q`/`rdata_q[0]` at the second posedge: compiled wrote the FIFO entry that reference kept x. Reduced from `ibex_fetch_fifo` alone with a pinned `--cmd` check that also rejected undeclared names (a first pinned run drifted the same way). Cause, compiled: a continuous assign to a bit select (`assign entry_en[i] = ...`) that went x kept the bit's old value and only set its mask, and a condition reads the value alone, so `if (entry_en[i])` was taken (`tests/test_sim/test_x_bit_write_condition.py`; part, indexed, wide, procedural and NBA writes were already right). ibex now shows no divergent signal over 32 cycles.
- Reducer follow-up: without Icarus a reduction can drift into an undeclared name. Compiled now reads one as a 1-bit x like the others (`tests/test_sim/test_undeclared_name_reads_x.py`; its mask fallbacks were 0, and a wide continuous assign with one raised), so such a drift no longer makes a divergence of its own; rejecting a candidate that reads a name the input declares nowhere would still keep repros clean.
- (2026-11-20) Found on the way (`tests/test_sim/test_struct_pattern_target.py`, all engines):
  - parser: `'{b: 1, default: 0}` kept `default` as a field name after a named field (it lexes as an IDENTIFIER there), so no struct matched and every engine raised (parameters stayed unresolved);
  - parser: a parameter typed by a bare typedef name (`parameter m_t On`, ibex's `ibex_mubi_t`/`pmp_cfg_t`) lost its type, so `$bits(On)` was 32 and the emitter dropped the type;
  - every engine: a pattern's struct was picked by its field names alone (two typedefs with the same names packed with the wrong layout), compiled packed only the `default:` of a mixed pattern, positional items kept their own widths, and a cast or `$bits` inside a pattern was never lowered (`_rewrite`/`iter_nodes` skipped the `(field, value)` pairs). `lower_struct_patterns` now rewrites a pattern assigned to a struct target as per-field casts in the target's layout.
  - regression from 26a1eba (the slow external-I/O matrix, `--run-slow`): lowering a numeric-index field (`dst[0].data`) turned a dynamic range or part select of it into a select of a select, which compiled could not write and reference/vm read wrong. `lower_struct_array_fields` now folds a constant part select too, and keeps a dynamic select's access as parsed when the index is a number or a plain name (an expression index still lowers to a select of a select; a dynamic select of such a field in a submodule was never supported).
  - Still open: a struct-typed declaration with an initializer (`n_t s = '{...};`) does not parse.
- (2026-11-20) Parameter arrays (`tests/test_sim/test_parameter_arrays.py`, all engines): the parser dropped a parameter's unpacked dimension (`parameter pmp_cfg_t PmpCfgRst[PMP_MAX_REGIONS] = '{...}`), so every engine read every element as 0 (no divergence: all agreed) and the emitter wrote it without the dimension. `Parameter.dimensions` now holds it; `lower_parameter_arrays` (before flattening, every module) packs the array (element i at `(i - low) * W`, struct items in the element type's layout) and rewrites `A[i]`, `A[i][3:0]`, `A[i].f` as selects; overrides pass the packed value. Constant evaluation now sizes a nested concatenation or a cast inside one. Not done: more than one unpacked dimension, a variable-index select of an element's bits (stays a select of a select).
- (2026-11-20) Emitter order: packages first, each after the packages it names, and a package's items in source order (a typedef using a parameter, a parameter of a typedef: ibex_pkg); built-in-Python packages keep imports, parameters, typedefs, functions, tasks.
- Icarus 14 rejects named struct patterns (`localparam s_t X = '{a: 1'b1, b: 2'd2};`, ibex_pkg's own source), so `--valid iverilog` cannot guard an ibex reduction whatever the emitter does.
- Parser gaps found on the way: an element of a package parameter array (`p::A[1]`) and an index-keyed pattern (`'{1: 8'd7, default: 8'd9}`) do not parse.
- Corpus harness: `examples/pulp` holds six different `axi_pkg.sv` copies and the harness merges a project's files into one design, so the first copy wins and the xbar benches get one without `xbar_rule_64_t` (they still show as compiled engine errors in a corpus run). A per-subproject design would fix it.
- Still open from the corpus: `axi_lite_mailbox` is over 6 GB on compiled.

## Follow-ups found along the way

- Open: source locations of preprocessed files count preprocessed lines,
  so an `` `include `` shifts them (known_issues.md).
- Open: reference and vm grow without bound (29 GB, OOM-killed WSL) on selects whose bounds come from invalid
  default parameters (`serv_rf_ram`, `known_issues.md`). Parser gap:
  `localparam` in a parameter port list.
- Reducer ideas not done yet: inline one instance (flatten), narrow
  widths and parameter values, test candidates in parallel.
- Locator ideas: walking through memories by address (it walks by array
  name), and through function and task bodies called from a process
  (their reads of module variables are not followed).
