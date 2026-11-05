# Packed multidimensional array selects

Packed dimensions describe contiguous fields of a single bit vector.
Selecting a dimension removes that dimension and retains all the bits in
the dimensions to its right. For example, each `s7_corr[g][k]` below is an
8-bit element:

```systemverilog
logic [1:0][3:0][7:0] s7_corr;
integer g, k;
always @(posedge clk)
  for (g = 0; g < 2; g = g + 1)
    for (k = 0; k < 4; k = k + 1)
      s7_corr[g][k] <= data ^ (g*4+k);
```

Previously, all simulator modes registered this declaration as four
16-bit memory words. The second selector then selected one bit from a
word, leaving most bits X. The same modeling mistake affected nested
packed selectors inside unpacked memory elements.

`lower_packed_dimensions` in [packed.py](../../src/veriforge/sim/packed.py)
runs automatically before engine selection. It keeps the outer packed
dimension as the simulator's memory address level and folds the other
packed dimensions into the word width. The example becomes two 32-bit
words, with `s7_corr[g][k]` selecting eight bits starting at `k*8` in word
`g`. Genuine unpacked address dimensions remain separate. Whole-vector
values retain their SystemVerilog bit order.

The pass handles nested indices, whole rows, constant and indexed ranges,
and ascending or nonzero-based packed bounds. Each selected dimension is
checked before offsets are combined: an invalid inner index must not
alias a neighboring element. Out-of-range reads produce X, writes leave
other elements alone, and partially out-of-range ranges retain their
in-range bits. Function/task declaration scopes are handled separately.
Constant in-range slices remain a single slice; ranges that need
individual bounds checks become concatenations of guarded slices.

Hierarchy flattening preserves `packed_only` metadata and connects packed
ports as whole vectors. Only genuine unpacked port dimensions expand into
element connections. Array query functions such as `$size` and `$left`
use the original packed dimension order before storage normalization.
Packed row and element selects remain unsigned even when the complete
array is declared signed; an explicit `$signed` cast still sign-extends.
Python row access retains the declared addresses for ascending ranges.

The compiled engine uses its wide scratch evaluator for computed writes
to whole arrays with wide elements and for part-selects of memory words
inside larger expressions. This preserves high value bits and X masks,
including slices whose source is wider than the result.

[Regression tests](../../tests/test_sim/test_packed_multidim_selects.py)
cover all four simulator modes, blocking/nonblocking nested loops, 8-bit
and 96-bit elements, dynamic and invalid indices, partial writes,
mixed-direction ranges, packed elements in multidimensional unpacked
memories, and four packed dimensions through generated child instances.

External validation used Verilator for the exact dynamic nested-loop repro
with 8-bit and 96-bit elements. Icarus rejects variable outer packed
indices in that repro, so its comparisons used 18 constant-index controls
for bit order, query results, memory elements, hierarchy, partial writes,
X masks, and signedness. Those controls use in-range selectors: Icarus
also aliases some constant out-of-range inner packed indices. The shared
VeriForge tests independently require each invalid dimension to read X
and leave other elements unchanged on writes.
