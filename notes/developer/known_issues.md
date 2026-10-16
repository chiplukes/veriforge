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

### Full-signal VCD tracing is very slow on large compiled designs

**Status**: Open performance limitation; correctness unaffected.
**Found**: September 2026, on the Windows `axis_col_correct` testbench.

The test finishes with `--engine compiled` but appears to stall when
`--vcd` is added. The flattened module has roughly 4,600 nets and 3,100
variables, and default tracing also includes memory elements. The shared
`VcdTraceSession` reads every traced signal and constructs a Python `Value`
on every time step, so full traces scale with signal count times time steps
even when few values change. For now, run without VCD or pass a small
`vcd_signals` list to `Testbench.run()`. A raw value/mask callback gave a
6.7x improvement in an isolated 7,700-signal benchmark, but it was not
validated against the real Windows design and was left out pending a
focused VCD performance pass.

### `vm`/`vm-fast`: loop-bearing combinational block behind a child instance doesn't re-fire on a second settle()

**Status**: Open, root-caused but not fixed. `reference`/`compiled` unaffected
(`vm` unaffected in the cases checked so far; only `vm-fast` confirmed).
**Found**: August 2026, during a 2-hour confidence-building fuzzing round
(seed 5000, `--verilator`).

An `always @(*)` block containing a `for`/`while` loop, instantiated one
level inside a child module, reads a stale (frozen at its first-ever
activation) value forever after, once its only input arrives via a
cross-instance continuous assign and a SECOND `settle()` follows a changed
top-level drive. Minimal repro and full analysis in `notes/developer/roadmap.md`
("Long confidence-building fuzzing round" -- root-caused, not fixed entry).

### Self-referential combinational reads (resolved 2026-10-14)

`always @(*) begin o5 = o6; ... o6 = ~something; end` (reading a signal
before the block's own write to it) used to give different answers on
reference, vm/vm-fast and compiled. All four now follow IEEE 1364-2005
9.7.5 as Icarus does: the block's own change doesn't re-trigger it, another
process's does (`notes/simulation/simulation_model.md`, "Sensitivity
Inference"). Verilator still differs here (it settles combinational logic
to a fixed point) -- not a veriforge bug.

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

### Compiled engine: a wide (>64-bit) function argument outside a statement

**Status**: Narrow remaining limit (2026-10-15). Function ports and returns
wider than 64 bits (and so `N'(e)` casts to more than 64 bits, which every
engine lowers to an N-bit function) work on compiled since 2026-10-15: a
wide argument is stored into the port's own storage just before the call
(hoisted, `_bind_wide_func_arg`), a wide return read from the return
signal's (`_hoist_user_func_call`). A call in a context with no statement to
hoist into raises `NotImplementedError` ("no statement to evaluate the
argument in") instead -- none known; `test_function_wide_port_and_return`.

### No `real` / `realtime` type

**Status**: Open feature gap (found 2026-10-09).

`real` variables, real literals and real arithmetic are not modeled on any
engine: `real r = 3.25;` stores the integer `325`. `$display`'s `%e`/`%f`/
`%g` work (Icarus-exact, with width/precision) but always format the
argument's *integer* value, which covers `$time`-style use. Real support
needs a real-valued representation alongside `Value` in all four engines.

### Multi-dimensional array sub-array access not supported

**Status**: Not supported, fails loudly (2026-10-12). `m[0] = m[1]` on
`reg m [0:1][0:2]` (an SV array slice) raises `NotImplementedError` from
`sim/ascending.py:flatten_unpacked_arrays`; it used to simulate wrongly. Icarus
doesn't support array slices either ("sorry: ... not yet supported").
Elements (`m[i][j]`), `$readmem` into rebased memories, hierarchical
references and the Python API (`sim.read("m[4]")`, VCD labels) all use declared
addresses (fixed 2026-10-12).

## Reference notes (not defects)

These are deliberate design decisions or investigated-and-closed
non-bugs, kept here because they're the kind of thing a future
investigation could easily mistake for a live defect and waste time
re-deriving.

### Streaming concatenation as an assignment target: Verilator and a wider source

**Status**: Supported since 2026-10-15 (`{<<8{a, b}} = s;`, `{>>{a, b}} <=
s;`, `assign {<<4{x, y}} = s;`; `sim/sv_functions.py:lower_streaming_targets`,
`tests/test_sim/test_streaming_concatenation_unpack.py`). It used to be a
parse error. One oracle difference: a source wider than the targets is
consumed from its left end (IEEE 1800-2017 11.4.14.3 -- the LRM's `{>>{a, b,
c}} = 100'b11111` gives `c = 1`), so `{<<8{a, b}} = 24'hABCDEF` gives a = cd,
b = ab; Verilator streams the whole source first and keeps its left end (ef,
cd). Same widths agree, ragged slices included. A narrower source is an
error (`ValueError`), as in the LRM. Not a veriforge bug -- do not
replicate Verilator here.

Also, in one fuzz design (seed 26133), Verilator unpacked `{<<65{r5, r6}}
<= 57'(...)` (54 + 3 bits) differently from `{<<57{...}}`, `{<<64{...}}`,
`{<<100{...}}` and the plain `{r5, r6} <=` -- all the identity for a 57-bit
stream, which veriforge and Verilator agree on for the others. A Verilator
bug with a 65-bit slice; the fuzzer's streaming targets don't use 65.

### x and z share one representation (3-state, not 4-state)

**Status**: By design — documented limitation.

`sim/value.py` encodes x and z identically (`Value.z()` returns `Value.x()`).
Consequences: `===`/`!==` cannot distinguish x from z, tristate buses,
pullups, and high-impedance detection are not simulatable. This is a
deliberate RTL-subset trade-off (consistent with the support matrix's
"strength and tristate resolution: low priority"), but note that docs and
docstrings describing the simulator as "4-state" overstate it slightly.

### Icarus: `**` with a negative exponent and 64-bit-or-wider operands

Icarus returns 0 for any negative signed exponent once the operands are 64
bits or wider (`1 ** -3`, `(-1) ** -3`, `0 ** -3` all 0), while for 32-bit
operands it follows IEEE 1364-2005 Table 5-6 (1, -1, x). Every veriforge
engine follows Table 5-6 at all widths; the fuzzer only generates small
unsigned exponents. Not a veriforge bug -- do not replicate.

### Icarus: out-of-range memory accesses (multi-dimensional arrays, constant words)

Found by the fuzzer's MEMORY strategy (2026-10-12); veriforge follows IEEE
1800 7.4.6 and Verilator here, and the generator avoids these shapes:

- **Multi-dimensional arrays, variable index**: Icarus range-checks only the
  flattened address, so `m[0][3]` of `reg m [0:1][0:2]` reads and writes
  `m[1][0]` (with constant indices it returns x, as IEEE says).
- **Multi-dimensional arrays, negative bounds**: an unsigned 32-bit index
  like `i + -3` is address -3 to Icarus's 1-D arrays and to Verilator, but
  not on a multi-dimensional array.
- **Constant out-of-range word with a select**: `mem[2][9:1]` of
  `reg [12:0] mem [6:3]` reads the whole element (13 bits) as x instead of 9.

- **Wide index in a continuous assign**: an index wider than 32 bits is
  truncated to its low 32 bits procedurally (Icarus, Verilator, veriforge), but
  Icarus doesn't truncate one in a continuous assign (`assign o = m[{h, 31'd0,
  b} + 4]` reads x when `h` is non-zero).
- **Negative narrow signed index**: `m[$signed(i[2:0])]` with `i = 3'b100`
  (-4) is out of range per IEEE, in veriforge, and in Icarus inside an
  initial block; Icarus in a continuous assign or `always` block, and
  Verilator everywhere, read it as unsigned (`m[4]`).
- **Narrow unsigned index, negative bounds**: `m[i]` of `reg m [-1:0]` with
  `i = 3'd7` is out of range (IEEE, veriforge: x), but Icarus reads the
  index as signed in its own width -- -1 -- and returns `m[-1]` (found
  2026-10-14, fuzz seed 19099). A 32-bit index (`m[i + 0]`) is fine.
- **Negative genvar in an unsigned context**: `(a || b) ^ g` with g = -2 into
  a 70-bit word -- a genvar is a signed 32-bit value, zero-extended in an
  unsigned expression (IEEE, Verilator, veriforge); Icarus sign-extends it.

Indices are 32-bit two's-complement integers in both Icarus and Verilator:
`m[i + -3]` (unsigned 0xfffffffd) is address -3, and an index wider than 32
bits counts only its low 32 bits.

### Icarus: input port expressions are self-determined

Icarus evaluates an input port connection expression at its own width and
pads it to the port (`c u(.i9(-{a, 4'd0}))` with a 9-bit port gives
`001110000`, with a "Padding 2 high bits" warning). IEEE 1364 12.3.9 treats
the connection as a continuous assignment, evaluated at the port's width:
Verilator and veriforge give `111110000`. The fuzzer wraps hierarchical
input-port expressions in a size cast to the port's width, which means the
same in every simulator. Not a veriforge bug -- do not replicate.

### Icarus: `$signed`/`$unsigned` of a bare parameter is ignored

`parameter signed [69:0] p3 = 70'sd952465042086495645460;` -- Icarus prints
`$unsigned(p3)` as negative (the cast is dropped). The same cast of a reg,
or of `{p3}`, works. Verilator and veriforge print it unsigned. The fuzzer
casts `{p}` instead of `p`. Not a veriforge bug -- do not replicate.

### Icarus: `always @*` sensitivity drops identifiers in constant-folded branches

`always @(*) begin ... if (p1[38]) w8 = 1; else if (i3 ** 2'd3) w8 = 2; ...`
with `p1[38]` a constant 1: Icarus drops `i3` from the implicit sensitivity
list. If nothing else external is read, the block never runs, so its
outputs stay x. IEEE puts every identifier read in the statement into `@*`.
Not a veriforge bug -- do not replicate.

### All engines run combinational blocks once at t=0 (Icarus doesn't)

Every veriforge engine runs each combinational `always @*` block (and each
continuous assign) once at t=0, `always_comb`-style. Icarus runs an
`always @*` block only on an input change. A block whose inputs never
change, e.g. `o10 = -r9` with `r9` a 2-state `shortint` that stays at its
initial 0, therefore stays x in Icarus. Strict 1364 `@*` semantics side
with Icarus here; this is a deliberate choice, and the fuzzer sees it as an
occasional "first activation" difference.

### Icarus: `signed_port | 1'b1` treated as signed

With `input logic signed [1:0] i5`, Icarus divides by `(i5 | 1'b1)` as if
it were signed (sign-extended: `x / 3` gives 0 in a 127-bit context). The
`1'b1` operand makes the expression unsigned, and the same expression on a
reg is right in Icarus. Verilator and veriforge zero-extend. Not a
veriforge bug -- do not replicate.

### compiled: operand order around impure function calls

compiled evaluates every user-function call in a statement before the
statement itself. Each call is guarded, so a call is skipped where `?:`,
`&&` or `||` wouldn't evaluate it (see known_issues_archive.md, "Short-
circuit evaluation"). A plain read of a signal the function writes, in
another operand of the same expression, therefore sees the value after the
call: `x ? !r8 : f()`, where `f` changes `r8` and `x` is x. Icarus and the
other engines evaluate left to right. IEEE 1364 5.1.2 leaves the order
unspecified, so both are legal. The fuzzer doesn't read the impure
function's counter in the statements that call it.

### Icarus: `integer unsigned` prints as signed

Icarus prints `integer unsigned iu = 32'hffff_ffff;` with `%0d` as `-1`
yet evaluates `iu > 0` as 1, so it does treat the variable as unsigned
everywhere except in display formatting. Verilator prints 4294967295, as
IEEE 1800 requires; veriforge does the same (2026-10-10,
`tests/test_sim/test_integer_atom_types.py`). Not a veriforge bug -- do
not replicate.

### Icarus: bit/part selects of a signed memory word are signed

For `reg signed [7:0] m [0:1]`, Icarus prints `m[0][7:4]` (and `m[0][7]`)
of `-2` with `%0d` as `-1`, though the same select of a signed *vector*
prints 15. IEEE 1800 5.5.1 makes every bit/part select unsigned; Verilator
prints 15, and so does veriforge (2026-10-13,
`tests/test_sim/test_signed_memories_and_atom_ports.py`). The memory word
itself (`m[0]`) is signed on all three. Not a veriforge bug -- do not
replicate.

### Icarus: a negation divided at more than 64 bits is 0

`wire [126:0] e = -{i1, i3} / one;` (any negated operand, `one` = 1) is 0
on Icarus; at 64 bits (`wire [63:0]`) it's right. Verilator and veriforge
give the 127-bit two's complement. Found 2026-10-14 replaying an old fuzz
mismatch. Not a veriforge bug -- do not replicate.

### Icarus: a reduction of an oversized unsized literal

Icarus sizes the unsized literal `4294967295` to 33 bits (so `~&` of it is
1, as veriforge has it), but then folds `$signed(~&4294967295)` to `1`
instead of a 1-bit -1: assigned to 16 bits it gives `0001`, where
`$signed(~&33'h0ffffffff)` gives `ffff` (veriforge: `ffff` for both).
Verilator sizes the literal to 32 bits (`fuzzer.md`). Found 2026-10-14
(fuzz seed 8071). Not a veriforge bug -- do not replicate.

### Icarus: real formats of signed expressions, leading x/X

Two Icarus `$display` quirks the fuzzer works around (veriforge follows
IEEE): `%e`/`%f`/`%g` format a signed *signal* as signed but a signed
*expression* (`~s`, `-s`, `s - k`) as unsigned (`%d` is signed for both);
a value wider than 53 bits converts to a double one ulp off
(apparently truncated rather than rounded); and an x-condition ternary
(`c ? r : r`, all x) -- and any x from a logical/relational operator
(`a || b`) -- prints `X`
instead of `x` under `%o`/`%h`; and a partly-x *signed* value under
`%e`/`%f`/`%g` keeps the bits below the lowest x and fills above with the
sign bit if known-1 (`8'sb1000_00x1` -> -1.00; veriforge reads x bits as
0, -127.00). Not veriforge bugs -- do not replicate.

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
`notes/developer/known_issues_archive.md` if this needs revisiting): `o4 = {i1[1],
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
(added alongside `logic`-declared signal generation, see `notes/developer/fuzzer.md`),
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
(2026-10-13: Verilator also gets `{<<100{n}}` of an 8-bit `n` wrong -- one
chunk, so the identity -- and an `always @*` assigning `{<<66{a[197:0]}}`,
an evenly-divided wide stream; both hand-checked against the same rule.)

Since fuzzed slice sizes rarely divide the fuzzed operand width evenly, the
fuzzer's `--verilator` cross-check skips the whole module whenever any
streaming concatenation exists anywhere in the design -- the same coarse
`has_streaming_concat` skip already used for Icarus (which rejects the
construct outright, a different reason for the same treatment). **Do not
attempt to make veriforge match Verilator here** -- veriforge's existing
behavior is the one that matches the independently-hand-derived LRM
algorithm.
