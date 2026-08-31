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

**Broader family, confirmed again (August 2026 fresh survey, seeds
3000-3400, `reference`/`vm`/`vm-fast` + Icarus, 400 modules, 5
mismatches — zero of them a genuine bug in this codebase)**: the same
underlying weakness — Icarus mishandling x-propagation on a
combinational block's first activation — also shows up in a
**cross-signal** form the original filter doesn't need to catch (the
filter's own heuristic is specifically about a signal reading its own
prior value; these don't). Rigorously derived from first principles for
`mismatch_03212` (kept as the clearest repro; see
`notes/known_issues_archive.md` if this needs revisiting): `o4 = {i1[1],
o5, i2};` reads `o5` — a sibling signal in the SAME always block that
hasn't been assigned yet this activation (still X, first-ever
evaluation) — and our engines correctly propagate that into a
partially-ambiguous `o4` (mask exactly matching `o5`'s own bit
positions in the concat); Icarus instead resolves the whole thing to a
clean, fully-defined `0`. The other four mismatches from the same
survey (`mismatch_03056`, `_03251`, `_03379`, `_03392`) all share the
same general shape (self-referential or cross-signal procedural reads
feeding an x-sensitive expression, discrepancy appears on specific
vectors/first-activation only, our three engines always agree with each
other) and were pattern-matched against this confirmed case rather than
each independently re-derived from scratch — reasonable given the
survey's overall signal (zero mismatches pointed at anything other than
this family) but worth a fully independent derivation for any of the
other four if picked up again specifically. **Possible future
improvement, not attempted**: `_is_icarus_first_activation_artifact`
could likely be extended to also catch this cross-signal variant
(any signal read before its own first assignment within the same
activation of the same process, not just the destination's own prior
value), which would reduce noise in future surveys — scope this as its
own small work item if the false-positive rate becomes annoying rather
than doing it speculatively now.


### Verilator ragged streaming-concat chunking gap (investigated, not a bug -- do not replicate)

**Status**: Investigated and closed -- not a simulator bug.
**Found**: smoke-testing the fuzzer's new `--verilator` cross-check
(added alongside `logic`-declared signal generation, see `notes/fuzzer.md`),
seeds 0-40.

Verilator's `{<<n{...}}` streaming concatenation agrees with veriforge
exactly whenever the combined operand width is an exact multiple of the
slice size `n`, but computes a genuinely different result whenever it
isn't (a "ragged"/incomplete-final-chunk operand). Confirmed directly and
independently of both simulators by hand-deriving IEEE 1800-2017
SS11.4.14.1's algorithm (split the operand's bit stream into `n`-bit chunks
starting from the MSB end -- the last, LSB-most chunk is shorter if the
width isn't a multiple of `n` -- then reassemble the chunks in reverse
order, each chunk's own bit order preserved): for `{<<3{8'b11010010}}`,
this gives `10100110`, matching veriforge (`reference`/`vm`/`vm-fast` all
agree, and this matches `Value.stream_reverse`'s own docstring citation of
the same LRM section) exactly. Verilator instead gives `01001011` -- the
result of chunking from the LSB end instead, landing the incomplete chunk
at the MSB end pre-reversal. The evenly-divisible case (`{<<4{...}}` on the
identical 8-bit operand) gives `00101101` in both -- isolating the gap
specifically to the ragged case, not streaming concatenation in general.

Since fuzzed slice sizes rarely divide the fuzzed operand width evenly, the
fuzzer's `--verilator` cross-check skips the whole module whenever any
streaming concatenation exists anywhere in the design -- the same coarse
`has_streaming_concat` skip already used for Icarus (which rejects the
construct outright, a different reason for the same treatment). **Do not
attempt to make veriforge match Verilator here** -- veriforge's existing
behavior is the one that matches the independently-hand-derived LRM
algorithm.
