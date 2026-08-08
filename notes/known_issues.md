# Known Issues

> This file holds only currently **open** defects and a couple of
> non-defect reference notes worth keeping discoverable. The full
> historical record — every resolved bug, its root cause, the Icarus
> repro that confirmed it, and the fix methodology — has moved to
> [`known_issues_archive.md`](known_issues_archive.md) (August 2026
> cleanup, per `architecture_review_2026-07.md` item 9's "known_issues.md
> holds only defects" plan). If you're investigating something that
> smells like it might have been hit before, search the archive first —
> it's a much bigger and much more detailed document than this one.

## Open defects

### vm-fast: `**` (power) silently wrong for >64-bit operand or destination

**Status**: Open.
**Found**: August 2026, while root-causing and fixing a separate `**`
signedness/negative-exponent bug family (see the archive's "Compiled-engine
ternary/context-determined-operator codegen..." entry — this gap was a
pre-existing side-discovery, not introduced by or specific to that fix).
**Severity**: Medium-high in kind (silently WRONG, not even a crash or x)
but narrow in scope — only triggers when `**`'s base or exponent, or the
assignment destination, exceeds 64 bits, on the `vm-fast` engine
specifically.

Neither `Op.POW` nor `Op.SPOW` (`sim/vm/_interp_fast.pyx`) consult the
wide (`wflag`/`wv`/`wm`) stack representation at all — they only ever read
a stack slot's narrow low-word fields. For a >64-bit base or exponent this
computes a plausible-looking but wrong answer with no warning of any kind.
Pinned as strict `xfail` in `tests/test_sim/test_power_operator.py` (two
cases) so it can't silently regress further or get accidentally "fixed"
without the xfail marker forcing a deliberate look. The `compiled` engine
had the identical shape of gap and is now fixed (raises `NotImplementedError`
at codegen time instead of silently corrupting the result — see the
archive). `reference` and `vm` (pure Python, arbitrary-precision `int`)
are unaffected.

**Fix shape, if picked up**: mirror the compiled engine's fix
philosophy — either implement real wide `**` support in `_interp_fast.pyx`
(would need multi-word integer exponentiation over the `wv`/`wm` word
arrays), or at minimum raise a loud, clear error instead of a silent wrong
answer, matching the "loud failure beats silent corruption" precedent
already established for the compiled engine's own version of this gap.

### Compiled engine: wide (>64-bit) signal posedge/negedge not supported

**Status**: Open, by design (not attempted) — fails loudly, not silently.
**Found**: May 2026.

`always @(posedge clk)` / `@(negedge clk)` on a signal wider than 64 bits
raises `NotImplementedError` in the compiled engine rather than being
supported or silently truncated. Exercised by
`TestWideSignalExternalIO::test_wide_posedge_signal_probe_cross_engine`
(strict `xfail`). `reference`/`vm`/`vm-fast` are unaffected. Not scoped or
attempted — no design encountered so far has needed edge detection on a
signal that wide (clocks and resets are essentially always narrow); pick
this up only if a real design needs it.

### Compiled engine: wide (>64-bit) user-defined function port/return not supported

**Status**: Open, by design (not attempted) — fails loudly, not silently.
**Found**: August 2026, while root-causing an unrelated wide-function-call
codegen gap (see the archive's ternary/codegen-family entry for the full
discovery story).

User-defined functions with a wide (>64-bit) port or return type raise
`NotImplementedError` at codegen time
(`test_compiled_function_wide_port_raises`) rather than being silently
truncated. The generated `_user_func_XXX` call ABI is hardcoded to a
single native `long long` per argument/return at three points (the C
function signature, the port-binding write, and the return statement);
supporting a wide port/return would need a genuine multi-word call-ABI
redesign (pointer/array-based argument and return-value passing across
the narrow value emitter, narrow mask emitter, and wide emitter), not a
routing fix. A wide *argument expression* passed to a narrow port already
works correctly — this is specifically about the port/return declaration
itself. `reference`/`vm`/`vm-fast` are unaffected (Python `int` throughout,
no such limit). Confirmed out of scope with the user when found; not
attempted. No design encountered so far has actually needed it — scope as
its own work-plan item if one ever does.

For the full per-operation wide-signal coverage picture (everything else
is ✅, backed by real cross-engine tests), see
`notes/simulation/wide_signal_coverage.md` and
`notes/plans/architecture_review_2026-07.md`.

## Reference notes (not defects)

These are deliberate design decisions or investigated-and-closed
non-bugs, kept here because they're the kind of thing a future
investigation could easily mistake for a live defect and waste time
re-deriving.

### x and z share one representation (3-state, not 4-state)

**Status**: By design — documented limitation.

`sim/value.py` encodes x and z identically (`Value.z()` returns `Value.x()`).
Consequences: `===`/`!==` cannot distinguish x from z, tristate buses,
pullups, and high-impedance detection are not simulatable. This is a
deliberate RTL-subset trade-off (consistent with the support matrix's
"strength and tristate resolution: low priority"), but note that docs and
docstrings describing the simulator as "4-state" overstate it slightly.

### Icarus first-activation x-extension artifact (investigated, not a bug — do not replicate)

**Status**: Investigated and closed — not a simulator bug.
**Found**: triaging two fuzzer-generated mismatches deferred during the
`settle()`-bootstrap work on the `random-verilog-gen` branch
(`mismatch_01066`, `mismatch_01045`).

Icarus has a first-activation-specific quirk: a combinational `always`
block's very first evaluation, if its RHS is an ambiguous
self-determined-1-bit value (e.g. `o10 = r7 != $signed(o9[0]);` with
`r7`/`o9` both entirely undriven/all-x), writes the WHOLE destination as x
instead of correctly zero-extending. Every subsequent (re-)evaluation of
the identical block, and every continuous-assign equivalent
(`assign y = (a != b);`), is deterministic and correctly zero-extends
(e.g. `0000000x`, not `xxxxxxxx`) — matching `!=`'s IEEE-unsigned-result
rule and this codebase's own established, differential-fuzzer-verified
extension logic. Minimal, isolated reproduction directly against
`iverilog -g2012` (bypassing this codebase entirely) confirms the pattern:
only a combinational always block's literal first-ever activation is
affected; nothing else — not declared signedness, not procedural-vs-
continuous, not "leave prior bits untouched when ambiguous" — explains it.

This is not a documented IEEE requirement (extension rules are
expression-level, independent of assignment kind or activation count),
Verilator cannot be used to cross-check it (it does not model x as a true
third state), and reproducing it would require tracking "has this specific
always block's specific destination ever been driven by a fully-defined
value before" as extra hidden per-signal state purely to match a one-off
Icarus artifact — actively wrong, not a fix. **Do not attempt to make this
match Icarus.** This codebase's current zero-extension behavior (shared,
engine-independent, and already validated by the differential fuzzer
across thousands of cases) is treated as correct. The fuzzer harness's own
`_is_icarus_first_activation_artifact` filter (`src/veriforge/fuzz/
_runner.py`) auto-detects and filters this pattern so it doesn't keep
resurfacing as a fresh-looking mismatch in future fuzzer surveys.
