# Wide Signal Coverage Matrix

This document tracks compiled-engine support and test coverage for wide (>64-bit)
Verilog operations. Each row records whether a C-level primitive exists, whether
the recursive wide emitter handles the operation, and whether cross-engine
(VM vs compiled) regression tests exist.

The recursive emitter (`_emit_wide_expr_to_scratch` in `_wide_emitter.py`) is the
primary path for wide expression evaluation. When it returns None the
`_emit_wide_py_bits_lines` fallback is tried; if that also returns None the
scalar path is used (which only reads the low 64 bits — a correctness hazard
for operands that have nonzero upper words).

## Operation Coverage

| Operation | C Primitive | Recursive Emitter | Tests | Notes |
|---|---|---|---|---|
| **Bitwise AND** (`&`) | `wide_and` | ✅ | ✅ 100+ | mask propagation, const operand |
| **Bitwise OR** (`\|`) | `wide_or` | ✅ | ✅ 100+ | mask propagation, const operand |
| **Bitwise XOR** (`^`, `~^`, `^~`) | `wide_xor` | ✅ | ✅ 100+ | both xnor aliases handled |
| **Bitwise NOT** (`~`) | `wide_not` | ✅ | ✅ ~20 | `not_shift_*` family |
| **Add** (`+`) | `wide_add` | ✅ | ✅ 150+ | const operand, nested trees, mask |
| **Subtract** (`-`) | `wide_sub` | ✅ | ✅ 150+ | asymmetric (const - signal) |
| **Negate** (unary `-`) | `wide_neg` | ✅ | ✅ ~20 | two's complement verified |
| **Unary plus** (`+`) | inline | ✅ identity | ✅ implicit | trivial pass-through |
| **Multiply** (`*`) | `wide_mul` | ✅ | ✅ ~20 | sizing families covered |
| **Divide** (`/`) | `wide_div` | ✅ | ✅ ~12 | zero divisor returns X — `test_cont_div_zero_divisor_returns_x` |
| **Modulo** (`%`) | `wide_mod` | ✅ | ✅ ~12 | zero divisor returns X — `test_cont_mod_zero_divisor_returns_x` |
| **Left shift** (`<<`) | `wide_shl` | ✅ | ✅ 200+ | const + variable amounts |
| **Logical right shift** (`>>`) | `wide_shr` | ✅ | ✅ 200+ | sizing families, dynamic bounds |
| **Arithmetic right shift** (`>>>`) | `wide_ashr` | ✅ | ✅ ~30 | `$signed()` wrapper detection |
| **Equality** (`==`, `!=`) | `wide_cmp_eq/ne` | ✅ | ✅ | cross-engine |
| **Less / greater** (`<`, `<=`, `>`, `>=`) | `wide_cmp_lt/le` | ✅ | ✅ | signed variants via `$signed()` |
| **Reduction OR** (`\|a`) | `wide_reduce_or` | ✅ | ✅ ~20 | `reduction_shift_*` family |
| **Reduction AND** (`&a`) | `wide_reduce_and` | ✅ | ✅ ~20 | source-width param required |
| **Reduction XOR** (`^a`, `~^a`, `^~a`) | `wide_reduce_xor` | ✅ | ✅ ~20 | parity, all aliases |
| **Reduction NOR** (`~\|a`) | `wide_reduce_or` + invert | ✅ | ✅ | included in reduction family |
| **Reduction NAND** (`~&a`) | `wide_reduce_and` + invert | ✅ | ✅ | included in reduction family |
| **Logical NOT** (`!a`) | `wide_reduce_or` + invert | ✅ fixed 2026-05-03 | ✅ 3 | **was bug**: scalar path read only low 64 bits |
| **Logical AND** (`&&`) | OR-reduce + AND | ✅ fixed 2026-05-03 | ✅ 3 | **was bug**: scalar path read only low 64 bits |
| **Logical OR** (`\|\|`) | OR-reduce + OR | ✅ fixed 2026-05-03 | ✅ 3 | **was bug**: scalar path read only low 64 bits |
| **Bit select** (`a[i]`) | `wide_slice_extract` | ✅ Identifier targets | ✅ 30+ | `dynamic_bit_extract_*` |
| **Constant range select** (`a[msb:lsb]`) | `wide_slice_extract` | ✅ | ✅ 100+ | sizing families, nested |
| **Dynamic range select** | `wide_slice_extract` | ✅ | ✅ | runtime-computed bounds |
| **Part select** (`a[base+:w]`, `a[base-:w]`) | `wide_slice_extract` | ✅ | ✅ 50+ | both directions |
| **Concatenation** (`{a, b, ...}`) | `wide_shl` + `wide_or` | ✅ | ✅ 200+ | multi-part, nested, with literals |
| **Replication** (`{n{a}}`) | `wide_replicate` | ✅ | ✅ ~50 | |
| **Ternary / mux** (`sel ? a : b`) | `wide_mux` | ✅ | ✅ | `ternary_shift_*` |
| **Blocking assign** | dedicated emitter | ✅ | ✅ 3000+ | primary assignment form |
| **NBA (non-blocking assign)** | dedicated emitter | ✅ | ✅ 2500+ | sequential logic |
| **Continuous assign** | dedicated emitter | ✅ | ✅ | all operator families |
| **Memory element read** | `wide_load_wmem{mid}` | ✅ | ✅ isolated | `TestWideSignalMemory` — 65/96/129-bit elements |
| **Memory element write** | dedicated emitter | ✅ | ✅ isolated | `TestWideSignalMemory` — NBA and blocking paths |
| **Memory range write** | dedicated emitter | ✅ | ✅ isolated | `TestWideSignalMemory` — overwrite-same-addr test |
| **Struct field read (wide)** | `wide_slice_extract` on packed base | ✅ | ✅ | `TestWideStructFieldSignals` — cont/combo/seq, 65-bit field |
| **Struct field write (wide)** | dedicated emitter | ✅ | ✅ | `TestWideStructFieldSignals` — packed bus output verified |
| **Edge detection on wide signal** | N/A — not supported | ✅ raises | ✅ | `TestWideEdgeDetection` — `NotImplementedError` for posedge/negedge on >64-bit |

## Test Selectors

```
# All wide signal external I/O tests (5563 total)
uv run pytest tests/test_sim/test_compiled.py::TestWideSignalExternalIO

# Logical operator corrections (!, &&, ||)
uv run pytest tests/test_sim/test_compiled.py::TestWideLogicalOps

# Arithmetic family (includes divide-by-zero)
uv run pytest tests/test_sim/test_compiled.py::TestWideUnifiedBehavioralCrossVal

# Memory element ops
uv run pytest tests/test_sim/test_compiled.py::TestWideSignalMemory

# Struct field wide signals
uv run pytest tests/test_sim/test_compiled.py::TestWideStructFieldSignals

# Edge detection guard
uv run pytest tests/test_sim/test_compiled.py::TestWideEdgeDetection

# Shift family
uv run pytest tests/test_sim/test_compiled.py::TestWideSignalExternalIO -k "shift_cross"

# Select / concat family
uv run pytest tests/test_sim/test_compiled.py::TestWideSignalExternalIO -k "extract_cross or concat_cross"

# Signed arithmetic
uv run pytest tests/test_sim/test_compiled.py::TestWideSignalExternalIO -k "signed_shift_cross"
```

## Architecture Notes

- The recursive emitter (`_emit_wide_expr_to_scratch` in `_wide_emitter.py`) is the
  single correct path for wide expression evaluation. All new wide operations should
  be added there first.
- `_rhs_needs_wide_eval` must be kept in sync with the recursive emitter: any
  operation that can produce a 1-bit result from a wide operand must be listed
  there so narrow-LHS assignments are routed through the wide path.
- The `_emit_wide_py_bits_lines` fallback handles expressions the recursive
  emitter doesn't cover (primarily `Concatenation` of non-Identifier parts) by
  calling `_emit_py_expr` and `_emit_py_mask_expr`. These Python helpers must
  also handle any operator that can appear in a py_bits-evaluated expression.
- Codegen is split across 12 files: `codegen.py`, `_gen_sections.py`,
  `_stmt_emitters.py`, `_wide_emitter.py`, `_expr_emitter.py`,
  `_process_compiler.py`, `_codegen_utils.py`, `_gen_wide_section.py`,
  `_gen_narrow_accessors.py`, `_gen_narrow_assign.py`, `_gen_narrow_stage.py`,
  `_gen_narrow_tail.py`. All hash into the infra hash in `compiled_scheduler.py`.
