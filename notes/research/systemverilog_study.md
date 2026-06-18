# SystemVerilog Low-Hanging Fruit Study

## Methodology

Studied the lowRISC Ibex RISC-V core (ibex_core.sv, ibex_alu.sv) — a well-maintained,
production-quality open-source RTL project. Cataloged SV features by usage frequency
and classified by grammar/model impact.

## Feature Frequency in Real-World RTL

Ranked by how often each SV feature appears (Ibex core, ~5000 lines RTL):

| Rank | Feature | Frequency | Example |
|------|---------|-----------|---------|
| 1 | `logic` type | Every signal | `logic [31:0] operand_a_rev;` |
| 2 | `always_comb` | Every combo block | `always_comb begin ... end` |
| 3 | `always_ff` | Every sequential block | `always_ff @(posedge clk_i or negedge rst_ni)` |
| 4 | `unique case` | Most case statements | `unique case (operator_i) ... endcase` |
| 5 | `import` | Module headers | `import ibex_pkg::*;` |
| 6 | `package` | Type definitions | `package ibex_pkg; ... endpackage` |
| 7 | `typedef enum` | FSM states, opcodes | `typedef enum logic [1:0] { ... } state_e;` |
| 8 | Typed parameters | Module params | `parameter ibex_pkg::rv32b_e RV32B = ...` |
| 9 | `int unsigned` | For-loop variables | `for (int unsigned i = 0; i < 32; i++)` |
| 10 | `localparam logic` | Typed constants | `localparam logic [31:0] CRC_POLY = ...` |
| 11 | `always_latch` | Latch inference | `always_latch begin ... end` |
| 12 | `'0` / `'1` | Fill patterns | `assign result = '0;` |

## Classification by Implementation Effort

### Tier 1: Keywords Only (grammar change only, no new rules)

These just add a new keyword to an existing rule alternative.

**`logic` type**
- Grammar: Add `KW_LOGIC: "logic"` terminal, add to `net_type` rule
- Model: Add `LOGIC` to `NetKind` enum
- Rationale: In SV, `logic` replaces `wire`/`reg` distinction. It can appear in
  net declarations, port declarations, and reg declarations. For parsing purposes,
  treating it as a net type (like wire) is the simplest approach and handles the
  majority of real-world usage.

### Tier 2: New Rule Alternatives (minor grammar changes + small model updates)

**`always_comb` / `always_ff` / `always_latch`**
- Grammar: Three new keywords, three new construct rules, add to `module_or_generate_item`
- Model: Map `always_comb` → `SensitivityType.COMBINATIONAL`,
         `always_ff` → `SensitivityType.SEQUENTIAL`,
         `always_latch` → `SensitivityType.LATCH`
- Note: `always_ff` still takes a sensitivity list (`@(posedge clk)`)
  `always_comb` and `always_latch` do not take sensitivity lists

**`unique case` / `priority case`**
- Grammar: Two new keywords (`unique`, `priority`), add as optional prefix to `case_statement`
- Model: Add `qualifier` field to `CaseStatement` (str or None)

**SV integer types: `bit`, `byte`, `shortint`, `int`, `longint`**
- Grammar: Five new keywords, new declaration alternatives
- Model: Add to `VariableKind` enum

### Tier 3: New Grammar Sections (medium model changes)

**`typedef enum`**
- Grammar: New `type_declaration`, `enum_declaration` rules
- Model: New `TypedefDecl`, `EnumType` model classes
- Note: Even basic forms (`typedef enum logic [1:0] { A, B, C } name_e;`) require
  several new rules

**`package` / `import`**
- Grammar: New top-level `package_declaration` with `endpackage`, `import_declaration`
- Model: New `Package` model class, import resolution
- Note: Very common but requires namespace/scope resolution

**`struct` / `union`**
- Grammar: New `struct_declaration`, `union_declaration` rules
- Model: New composite type classes
- Note: Less common than enum but used in some RTL

### Out of Scope

- **Classes, OOP** — Verification feature, different paradigm
- **Assertions / SVA** — Complex sub-language
- **Covergroups, constraints, randomization** — Verification
- **Dynamic arrays, queues, associative arrays** — Verification
- **`interface` / `modport`** — Significant new construct (defer to later)
- **`++` / `--` operators** — Less common in RTL than expected (mostly in for-loop
  increments with `int`), and they complicate expression parsing

## What Was Done

All three tiers were implemented.

**Grammar** (`verilog.lark`): Added `KW_LOGIC`, `KW_ALWAYS_COMB`, `KW_ALWAYS_FF`,
`KW_ALWAYS_LATCH`, `KW_UNIQUE`, `KW_PRIORITY`, `KW_BIT`, `KW_BYTE`, `KW_SHORTINT`,
`KW_INT`, `KW_LONGINT` terminals. `logic` added to `net_type` and `reg_type`
alternatives. `always_comb_construct`, `always_ff_construct`, `always_latch_construct`
rules added. `unique`/`priority` prefix added to `case_statement`. SV integer type
declarations added.

**Model**: `NetKind.LOGIC` and `VariableKind.LOGIC/BIT/BYTE/SHORTINT/INT/LONGINT`
added. `AlwaysBlock` model handles `always_comb`/`always_ff`/`always_latch` via
`SensitivityType`. `CaseStatement.qualifier` added. Tier 3 features also implemented:
`TypedefDecl`, `EnumType` in `model/sv_types.py`; `Package` in `model/package.py`.
Transformer in `transforms/tree_to_model.py` updated for all new constructs.

**Emitter and tests**: Verilog emitter updated to emit `logic`, `always_comb`,
`always_ff`, `always_latch`, and case qualifiers. Grammar and model round-trip tests
added.

## Notes on `logic` Semantics

In SystemVerilog, `logic` is a 4-state data type that can be used in place of
both `wire` and `reg`. The key differences:
- `logic` cannot have multiple drivers (unlike `wire`)
- `logic` can be used in procedural blocks (like `reg`)
- `logic` can be used in continuous assignments (like `wire`)

For our parser, the simplest correct approach is:
- Add `logic` as both a net_type and a reg_type alternative
- In the model, `LOGIC` in `NetKind` for net declarations
- `LOGIC` in `VariableKind` for variable/reg declarations
- This mirrors how `logic` bridges the wire/reg divide

## Notes on `always_comb` vs `always @(*)`

Key differences:
- `always_comb` executes automatically at time 0 (no trigger needed)
- `always_comb` is sensitive to changes inside called functions
- `always_comb` forbids blocking timing controls
- Our simulator already does combinational bootstrap at t=0 via Phase 7 work

## Notes on `unique case`

- `unique case` = all case items are mutually exclusive + full (simulation warning if no match)
- `priority case` = first match has priority (like regular case but with full warning)
- `unique0 case` = like unique but no warning for no match (SV 2012)
- For parsing: just store the qualifier keyword. Semantics are for lint tools.
