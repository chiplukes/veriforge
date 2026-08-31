# Fuzzer — Grammar-Driven Cross-Engine + Icarus/Verilator Fuzzing

The fuzzer generates arbitrary, valid Verilog modules by walking the parse
grammar, simulates them across **all** veriforge engines (reference, vm,
vm-fast, compiled), and cross-checks every result against **Icarus Verilog**
as an external oracle (on by default), optionally also against **Verilator**
(opt-in, `--verilator` -- see "Verilator Cross-Check" below). Mismatches are
logged to disk for reduction into dedicated test cases.

## Quick Start

```bash
# 100 modules, veriforge engines only (~1 module/second)
uv run -m veriforge.fuzz --max 100 --no-icarus

# 8 hours, with Icarus cross-check (~0.3 modules/second due to iverilog/vvp overhead)
uv run -m veriforge.fuzz --hours 8

# Run indefinitely, write artifacts to a custom directory, Ctrl-C to stop
uv run -m veriforge.fuzz --output my_fuzz_output --no-icarus

# Specify which veriforge engines to test
uv run -m veriforge.fuzz --max 50 --engines reference vm

# Custom starting seed (deterministic replay)
uv run -m veriforge.fuzz --seed 42 --max 10

# Also cross-check with Verilator (opt-in, slower -- see "Verilator Cross-Check")
uv run -m veriforge.fuzz --max 50 --verilator
```

## CLI Reference

```
uv run -m veriforge.fuzz [--seed N] [--max MODULES] [--hours H]
                         [--output DIR] [--no-icarus] [--verilator] [--engines E ...]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--seed` | 0 | Starting seed. Incremented after each module. |
| `--max` | none | Stop after N modules. |
| `--hours` | none | Stop after H hours. |
| `--output` | `fuzz_output` | Directory for mismatch artifacts and stats. |
| `--no-icarus` | off | Disable Icarus cross-check. |
| `--verilator` | off | Enable Verilator cross-check (opt-in: ~8-10x slower per module than Icarus). |
| `--engines` | all available | Veriforge engines to test: `reference`, `vm`, `vm-fast`, `compiled`. |

If neither `--max` nor `--hours` is specified, the fuzzer runs until interrupted.

## What It Generates

The fuzzer uses **seven module strategies**, chosen randomly with weights
favoring simpler shapes:

| Strategy | Description |
|----------|-------------|
| **feedforward** | Inputs → continuous assigns → outputs |
| **registered** | Inputs → regs → combinational outputs, clocked |
| **multi_always** | Multiple `always @*` blocks sharing internal wires/regs |
| **clocked_sequential** | Single `always @(posedge clk)` with nonblocking assigns |
| **nested_blocks** | Deeply nested `begin/end` with local variables |
| **mixed** | Random mix of assigns + always blocks + internal nets |
| **hierarchical** | Flat child module + parent module + an `Instance` connecting them |

Each module gets random signal widths (biased toward edge cases: 1, 8, 16,
32, 63, 64, 65, 80, 127, 128), random signedness, random types (each
input/output/wire/reg has an independent ~35% chance of being declared
`logic` instead of `wire`/`reg` -- see "Verilator Cross-Check" below for why
this needed its own oracle), 0-3 declared parameters (same edge-case-biased
widths/values), random expression trees (arithmetic, bitwise, logical,
relational, reduction, ternary, concat, streaming concat `{<<{...}}` /
`{<<N{...}}`, replicate, `$signed`/`$unsigned`), and random statement shapes
(if/else chains, case/casex/casez, for loops, while loops, begin/end
blocks).

**The `hierarchical` strategy** generates a child module from one of the
other six (flat) strategies, then a parent module (always named `t`, the
fuzzer's fixed top-module name) that instantiates it. Every child *output*
port connects to a freshly-declared parent wire (required: Verilog output
ports must bind to a net). Every child *input* port connects to a parent-side
expression that's forced to be a concat/streaming-concat roughly half the
time -- targeting "wide concatenation feeding a module port", the shape that
caused several real compiled-engine bugs found via `axis_pix_correction2`
(see `notes/roadmap.md`) before this fuzzing round existed. Some of the
parent's own outputs are, in turn, woven from the instance's output wires
via concat/streaming-concat ("concat *out of* a port"). No width-matching is
forced on port-connection actuals beyond the output side -- Verilog's own
implicit truncation/extension at a port connection is exactly the same as
at any continuous-assign RHS, which every other strategy already leaves
unconstrained. Nested hierarchy (a child that itself instantiates something)
is a non-goal for now -- the child is always a flat strategy.

Since a `hierarchical` module is two `Module`s, not one, the fuzzer's
generation entry point is `ModuleGenerator.generate_design() -> Design`
(`Design(modules=[mod])` normally, `Design(modules=[child, parent])` for
`hierarchical`) -- `generate() -> Module` still exists and stays
single-module for any external caller that only wants one flat module.

## How It Compares

For each generated module:

1. Random stimulus vectors (4–12 vectors, ~15% of signals x-contaminated)
2. Simulate with the **reference** engine → oracle results
3. Simulate with each **non-reference** engine → compare bit-for-bit
   (value **and** x/z mask) against the oracle
4. Emit Verilog text, compile with `iverilog -g2012`, run `vvp`, capture
   `$display` output → compare against oracle
5. If `--verilator` is passed: compile+run the *same* testbench with
   `verilator --binary` → compare against oracle (see "Verilator
   Cross-Check")

A mismatch on any engine is logged immediately.

## Verilator Cross-Check

`--verilator` (opt-in, off by default) additionally cross-checks against
Verilator, needed specifically to validate `logic`-declared signals: Icarus
Verilog's SystemVerilog support is weaker than its Verilog-2005 core, making
it a shaky sole oracle for `logic`. Verilator's `--binary` mode compiles and
links a self-contained executable from the *same* `$display`-based
testbench `_build_testbench` already generates for Icarus -- no separate
harness needed.

Two Verilator-specific limitations shape how the comparison works:

- **2-state only**: Verilator does not model `x`/`z` as a real third state
  (confirmed directly: driving `4'bxxxx` into a `logic` net and reading it
  back gives `0000`, not `x` -- see "Icarus first-activation x-extension
  artifact" below, which documents the same limitation independently).
  `_compare_verilator` therefore only compares `.val`, and only for
  `(vector, signal)` pairs where the *reference* engine itself reports the
  signal as fully defined (`mask == 0`) -- anywhere the oracle shows
  ambiguity is skipped rather than compared, since Verilator's answer for a
  genuinely undefined case is arbitrary.
- **Ragged streaming-concat chunking gap**: Verilator agrees with veriforge
  on `{<<n{...}}` whenever the combined operand width is an exact multiple
  of the slice size `n`, but computes a different (non-LRM) result whenever
  it isn't -- confirmed directly and independently of both simulators by
  hand-deriving IEEE 1800-2017 §11.4.14.1's chunk-from-MSB-then-reverse
  algorithm: `{<<3{8'b11010010}}` should give (and veriforge's
  reference/vm/vm-fast all agree on) `10100110`; Verilator gives `01001011`
  instead (the result of chunking from the LSB end, landing the incomplete
  chunk at the opposite end). The evenly-divisible case (`{<<4{...}}` on the
  same operand) matches exactly in both, isolating the gap to the ragged
  case specifically. Since fuzzed slice sizes rarely divide the fuzzed
  operand width evenly, the Verilator cross-check is skipped for the whole
  module whenever any streaming concatenation exists anywhere in the design
  -- the same coarse `has_streaming_concat` skip Icarus already gets (for a
  different reason: Icarus rejects the construct outright).

## Mismatch Artifacts

Each mismatch creates a numbered directory under the output path:

```
fuzz_output/
├── stats.json              # running totals
├── mismatch_00042/
│   ├── info.json            # seed, strategy, timestamp, engines
│   ├── module.v             # generated Verilog source
│   └── mismatches.txt       # human-readable diff details
└── ...
```

### Interpreting Mismatches

A veriforge engine mismatch against the reference engine means the
generated Verilog exposes a behavioral divergence between simulation
backends. These are the most actionable bugs.

An Icarus mismatch (veriforge reference vs iverilog/vvp) means the
two simulators disagree. The divergence may be:

- A **veriforge bug** (the reference engine is wrong)
- An **Icarus bug** (unlikely but possible for obscure constructs)
- A **semantic ambiguity** in the Verilog spec (worth investigating)

### Repro Workflow

```
1. Fuzzer logs mismatch → fuzz_output/mismatch_00042/
2. Open module.v — examine the failing Verilog
3. Reduce to a minimal repro (remove unrelated ports/signals/statements
   until the mismatch disappears, then back up one step)
4. Add a deterministic test in tests/test_sim/test_xxx.py
5. Fix the bug
```

## Relationship to test_differential*.py

The fuzzer is a **standalone long-running tool** — it is not a pytest test
and is not run in CI. The `tests/test_sim/test_differential*.py` files are
**fast, bounded CI regression tests** that exercise specific Verilog shapes
(expressions, statements, function calls) with fixed signal sets. They run
in seconds and catch regressions. The fuzzer runs for hours or days and
discovers new bug patterns.

When the fuzzer finds a divergence, the expected workflow is:

```
fuzzer discovers pattern → human reduces → human writes deterministic test
→ human fixes bug → deterministic test prevents regression
```

## Architecture

```
parse_metadata.GrammarMetadataParser  ← SUPPORT: YES/NO from verilog.lark
           ↓
      GrammarGuide  (weighted random rule selection)
           ↓
      SignalContext  (dynamic signal pool, scope nesting)
           ↓
   ┌───────────────────────────────┐
   │ ExpressionGenerator           │
   │ StatementGenerator            │
   │ ModuleGenerator (7 strategies)│
   └───────────────────────────────┘
           ↓
   Design (one or two Module model objects --
   two only for the hierarchical strategy)
           ↓
    ┌──────┴───────┐
    ↓              ↓
 Simulator     verilog_emitter
 (ref/vm/       → iverilog/vvp
  vm-fast/
  compiled)
    ↓              ↓
 Compare ←─────────┘
    ↓
 Mismatch log
```

## Known Limitations

- **Functions and generate constructs** are not yet generated (planned).
- **Icarus widths** may differ by one bit for some output signals — this is
  being investigated; the mismatch log records the discrepancy.
- **Loop hangs** — some generated `while`/`for` loops may exceed the
  simulator's 100k-iteration safety limit. The fuzzer logs these as errors
  and moves on.
- **Output ports** use `output reg` or `output logic` (never plain
  `output`/wire alone for a procedurally-written output), which Icarus
  requires `-g2012` to accept.
- **Streaming concatenation `{<<{...}}` is skipped for the Icarus
  cross-check entirely, and for the Verilator cross-check whenever enabled**
  (for the whole design, not just the top module -- the hierarchical
  strategy's child module draws from the same expression machinery and can
  independently contain one too): Icarus Verilog has no support for the
  construct at all ("sorry: Streaming concatenation not supported",
  confirmed directly); Verilator supports it but disagrees with the LRM (and
  with veriforge) on the ragged/incomplete-final-chunk case -- see
  "Verilator Cross-Check" above for both. Cross-engine comparison (reference
  vs vm/vm-fast/compiled) still runs normally for these modules.
- **Hierarchy is one level deep** — the `hierarchical` strategy's child is
  always a flat (non-`hierarchical`) strategy; nested instantiation isn't
  generated. Parameterized/overridden port widths on the instantiation
  aren't generated either (no parameter port overrides, i.e. no `#(...)` on
  the *instantiation* itself -- module-level parameter declarations with
  random default values are generated independently of hierarchy).
- The fuzzer generates only **IEEE 1364-2005 + select SystemVerilog**
  constructs. SV-only features (interfaces, packages, enums, structs) are
  not yet in the generation pool.
