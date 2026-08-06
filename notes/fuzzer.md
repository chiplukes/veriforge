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

The fuzzer uses **six module strategies**, chosen randomly with weights
favoring simpler shapes:

| Strategy | Description |
|----------|-------------|
| **feedforward** | Inputs → continuous assigns → outputs |
| **registered** | Inputs → regs → combinational outputs, clocked |
| **multi_always** | Multiple `always @*` blocks sharing internal wires/regs |
| **clocked_sequential** | Single `always @(posedge clk)` with nonblocking assigns |
| **nested_blocks** | Deeply nested `begin/end` with local variables |
| **mixed** | Random mix of assigns + always blocks + internal nets |

Each module gets random signal widths (biased toward edge cases: 1, 8, 16,
32, 63, 64, 65, 80, 127, 128), random signedness, random expression trees
(arithmetic, bitwise, logical, relational, reduction, ternary, concat,
replicate, `$signed`/`$unsigned`), and random statement shapes (if/else
chains, case/casex/casez, for loops, while loops, begin/end blocks).

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
   │ ModuleGenerator (6 strategies)│
   └───────────────────────────────┘
           ↓
       Module (model object)
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
- The fuzzer generates only **IEEE 1364-2005 + select SystemVerilog**
  constructs. SV-only features (interfaces, packages, enums, structs) are
  not yet in the generation pool.
