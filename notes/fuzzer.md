# Fuzzer — Grammar-Driven Cross-Engine + Icarus Fuzzing

The fuzzer generates arbitrary, valid Verilog modules by walking the parse
grammar, simulates them across **all** veriforge engines (reference, vm,
vm-fast, compiled), and cross-checks every result against **Icarus Verilog**
as an external oracle. Mismatches are logged to disk for reduction into
dedicated test cases.

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
```

## CLI Reference

```
uv run -m veriforge.fuzz [--seed N] [--max MODULES] [--hours H]
                         [--output DIR] [--no-icarus] [--engines E ...]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--seed` | 0 | Starting seed. Incremented after each module. |
| `--max` | none | Stop after N modules. |
| `--hours` | none | Stop after H hours. |
| `--output` | `fuzz_output` | Directory for mismatch artifacts and stats. |
| `--no-icarus` | off | Disable Icarus cross-check. |
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
32, 63, 64, 65, 80, 127, 128), random signedness, 0-3 declared parameters
(same edge-case-biased widths/values), random expression trees (arithmetic,
bitwise, logical, relational, reduction, ternary, concat, streaming concat
`{<<{...}}` / `{<<N{...}}`, replicate, `$signed`/`$unsigned`), and random
statement shapes (if/else chains, case/casex/casez, for loops, while loops,
begin/end blocks).

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

A mismatch on any engine is logged immediately.

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
- **Output ports** always use `output reg`, which Icarus requires `-g2012`
  to accept.
- **Streaming concatenation `{<<{...}}` is skipped for the Icarus
  cross-check entirely** (for the whole design, not just the top module --
  the hierarchical strategy's child module draws from the same expression
  machinery and can independently contain one too): Icarus Verilog has no
  support for the construct at all ("sorry: Streaming concatenation not
  supported", confirmed directly). Cross-engine comparison (reference vs
  vm/vm-fast/compiled) still runs normally for these modules.
- **Hierarchy is one level deep** — the `hierarchical` strategy's child is
  always a flat (non-`hierarchical`) strategy; nested instantiation isn't
  generated. Parameterized/overridden port widths on the instantiation
  aren't generated either (no parameter port overrides, i.e. no `#(...)` on
  the *instantiation* itself -- module-level parameter declarations with
  random default values are generated independently of hierarchy).
- The fuzzer generates only **IEEE 1364-2005 + select SystemVerilog**
  constructs. SV-only features (interfaces, packages, enums, structs) are
  not yet in the generation pool. `logic`-declared signals are not
  generated either (deferred to a separate follow-up needing a
  Verilator-based oracle, since Icarus's SV support is weaker there).
