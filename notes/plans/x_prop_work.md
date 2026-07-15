# X-Propagation Correctness: Wide Arithmetic Right Shift (`>>>`)

## Summary

The VM (and reference) engine uses an **overly conservative** X-propagation rule
for wide arithmetic right shift (`>>>`): if any source bit is X, the entire result
is set to X.  The compiled Cython engine was originally more precise (only the
directly-shifted bit and any sign-extension bits derived from an X sign bit become
X).  The precise behavior matches IEEE 1800 semantics and Icarus Verilog.

This discrepancy was found during the `TestWideSignalExternalIO` slow-test run.
The current state punts by aligning the compiled engine with the VM's conservative
rule so the tests pass.  The correct long-term fix is to make the VM (and
reference engine) precise, then revert the compiled engine's workaround.

---

## Correct Semantics

For `$signed(a) >>> N` where `a` is a wide signal with one X bit at position `k`:

| bit position in result | value |
|---|---|
| `k - N` (if in range) | X — the source X bit shifted right |
| top N bits (sign extension) | X **only if** the sign bit (`a[width-1]`) is X |
| all other positions | known (0 or 1) |

**Example** (`value1` from the failing tests):
- Input: 65-bit `(1 << 64) | 0x123456789ABCDEF0`, `mask = 1 << 17` (X at bit 17)
- Operation: `$signed(a) >>> 4`, 33-bit destination
- Sign bit `a[64] = 1` (known)
- Correct result: `33'b1100010011010101111x0110111101111` — X at bit 13 only
- Icarus Verilog agrees: mixes of X and known bits in shifted results are normal

---

## Current State (workaround)

**File**: `src/veriforge/sim/compiled/_gen_wide_section.py`  
**Function**: `wide_ashr` (generated Cython, ~line 280)

A "has any X → all X" early-return was added to match the VM:

```python
# VM semantics: any X bit in source → entire result is all-X
has_x = 0
for i in range(n):
    if am[i]: has_x = 1; break
if has_x:
    for i in range(n):
        remaining_w = dst_width - i * 64
        if remaining_w <= 0: break
        dv[i] = 0
        dm[i] = _word_mask64(remaining_w)
    return
```

**To revert** (restore precise behavior): remove those lines, leaving `wide_ashr`
to start directly with the sign-bit extraction:

```python
sign_word = (src_width - 1) >> 6
sign_pos  = (src_width - 1) & 63
...
```

---

## VM Bug Location

**File**: `src/veriforge/sim/vm/_interp_fast.pyx`  
**Opcode**: `OP_ASHR` (~line 1721)

The wide path (triggered when `w > 64 or wflag[sp]`) contains:

```cython
# If left operand has any X bits, result is all-X
has_x = 0
for wi in range(WIDE_WORDS):
    if wm[sp * WIDE_WORDS + wi]: has_x = 1; break
if has_x:
    for wi in range(WIDE_WORDS):
        wv[sp * WIDE_WORDS + wi] = 0
        wm[sp * WIDE_WORDS + wi] = 0xFFFFFFFFFFFFFFFF
    _wm_mask_to_width(&wm[sp * WIDE_WORDS], w)
```

This block should be replaced with precise X propagation:

1. Find the sign bit value and mask: `wsp = (w-1) >> 6`, `bit_in_word = (w-1) & 63`,
   `sign_v = (wv[sp*WIDE_WORDS + wsp] >> bit_in_word) & 1`,
   `sign_m = (wm[sp*WIDE_WORDS + wsp] >> bit_in_word) & 1`.
2. Perform a word-by-word logical right shift of both `wv` and `wm` (same as
   for `OP_SHR`).
3. Fill vacated top bits with `sign_v` / `sign_m` rather than all-X.

The existing `else` branch (lines after the `has_x` block) already performs the
correct logical shift and sign-fill for the **known-only** case; the fix is to
remove the early `has_x` bail-out and let that branch handle X bits too.

---

## Reference Engine

Check whether the reference engine (Python-level `Value` arithmetic or the
reference executor) also has the conservative rule. If it shares the VM's
opcode path the fix above is sufficient; if it has a separate Python
implementation search for `>>>` or `ashr` in
`src/veriforge/sim/executor.py` / `src/veriforge/sim/vm/interpreter.py`.

---

## Tests Affected

**File**: `tests/test_sim/test_compiled.py`  
**Class**: `TestWideSignalExternalIO`

Parametrize entries with the `value1` fixture that has `mask=1 << 17` (or
similar mid-word X bit):

```
test_wide_combo_blocking_signed_shift_helper_sizing_cross_engine[65-33-4-value1]
test_wide_seq_nba_signed_shift_helper_sizing_cross_engine[65-33-4-value1]
test_wide_combo_blocking_signed_shift_var_helper_sizing_cross_engine[65-33-4-value1]
test_wide_seq_nba_signed_shift_var_helper_sizing_cross_engine[65-33-4-value1]
```

After fixing the VM (and reverting `wide_ashr`), these tests should pass with the
compiled result `33'b1100010011010101111x0110111101111` matching the (now correct)
VM result.  No test code changes should be needed.

---

## Completion Checklist

- [ ] Remove `has_x` early-return from `wide_ashr` in `_gen_wide_section.py`
- [ ] Replace `OP_ASHR` wide-path `has_x` block in `_interp_fast.pyx` with
      precise shift + sign-fill
- [ ] Confirm reference engine uses same opcode path (no separate fix needed)
- [ ] Run `--run-slow` regression; verify the 4 `signed_shift_helper_sizing`
      tests pass with X only at the expected shifted bit position
- [ ] Optionally add a narrow-path smoke test for `>>>` with X in a data bit
      to lock in the precise semantics
