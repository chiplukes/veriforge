# Fuzzer coverage expansion (queued 2026-10-09)

**Status**: queued, not started.

## Why

On 2026-10-09 a user report (compiled `21'((p36 + c) >>> 15)`) led to a run
of bugs the random differential fuzzer (`src/veriforge/fuzz/`) never
generated, several of them in every engine
(`notes/developer/known_issues_archive.md`, "Expression context width /
signedness propagation" and "Functions, tasks, integers and $display gaps"):

- `>>>`, `<<` by a literal wider than the context, bitwise ops whose
  operands need the context width or the context's signedness;
- `N'(e)` size casts (silently dropped by the parser);
- `integer`/`int` arithmetic (unsigned on every engine!);
- signed function/task ports and locals, task locals, impure functions
  (module-variable writes, `$display` in a function);
- `$display` radix variants, `%m`, `%e/%f/%g`.

## Gaps in the current generators

- `_expression_gen.py` operator pool: no `>>>`, `<<<`, `**`, `===`,
  `!==`, `>`, `>=`, binary `~^`/`^~`; shifts mostly by small literals.
- Casts: only `$signed`/`$unsigned`; no `N'(e)`.
- Literals: always unsigned 32-bit decimal -- no sized, signed (`8'sd5`),
  negative, x/z, or unsized-signed literals.
- Signals: `reg`/`logic` vectors only (`_signal_context.py`); no `integer`/
  `int`/`byte`/`shortint`/`longint`, no `int unsigned`.
- Width relationships: generated widths are small and similar, so
  "operand wider than the target" (the reported shape) is rare; bias some
  expressions to assign a wide computation to a narrow target and vice
  versa, and to cross 64 bits (compiled's wide emitter).
- Functions exist only as `callables` in expressions (check: signed ports,
  locals of every kind, `integer` returns); no tasks, no impure functions,
  no functions with `$display`.
- No `$display`-based observation: outputs are compared as port values
  only, so display formatting and statement ordering inside a time step
  aren't exercised (could add an optional `$display` of each output with
  random format specifiers, compared against Icarus output).

## Approach

1. Extend the pools above behind per-feature weights so a run can target
   one feature (e.g. `--focus=shifts,casts,integers`).
2. Keep Icarus as the oracle (as `tests/test_sim/test_expression_semantics.py`
   does); every new construct must be in Icarus's `-g2012` subset.
3. Run a few thousand seeds per feature against all four engines; triage
   with the existing minimizer; add each confirmed bug's minimized repro to
   the matching regression file.
