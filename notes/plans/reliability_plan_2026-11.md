# Simulator reliability plan (2026-11)

**Status**: in progress. Step 2a (construct census) started 2026-11-17.

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
