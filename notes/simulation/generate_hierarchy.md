# Generated scope references

A reference such as `g_sp[SP_K-1].p0` names one concrete generated register.
Its index is a constant expression evaluated during elaboration. With
`SP_K=3`, all engine modes must receive the same path, `g_sp[2].p0`.

```systemverilog
parameter SP_K = 3;
for (genvar i=0; i<SP_K; i=i+1) begin : g_sp
    reg [95:0] p0;
    always @(posedge clk) p0 <= d ^ i;
end
assign y = g_sp[SP_K-1].p0;
```

The parser retains the grouping of index expressions when constructing
hierarchical identifiers. Concatenating only grammar tokens would drop
parentheses, turning `(SP_K-1)*2` into the different expression `SP_K-1*2`.
Generate elaboration resolves parameter and genvar indices in their local
environment, including sibling references such as `g_sp[i-1].p0`. Module
parameter overrides apply before resolution. Nested scope indices resolve
independently, including expressions with constant functions or selects.

Flattening preserves the complete path when prefixing module instances:
`g_sp[2].p0` becomes `u.g_sp[2].p0`. This also applies when the reference ends
in a struct field. A qualified reference must not be renamed merely because
its final component matches a local register name.

Index parsing caches expression ASTs rather than evaluated values, so different
parameter overrides and generate iterations cannot reuse another scope's
result. Changed model roots are copied; the input identifier paths remain
available for later elaboration with different overrides. Dynamic memory-field
indices such as `items[addr].data` retain their runtime index.

Previously expression-based scope names survived into engine lookup unchanged.
The named register was not found, yielding X in reference/VM and, for the
reported shape, zero in compiled simulation. Literal-index reads alone did not
exercise this gap.

See [the regression cases](../../tests/test_sim/test_generate_hierarchy_indices.py)
for all four engine modes, 96-bit values and masks, nested scopes, genvar
expressions, instance overrides, and input-model preservation.

## Function and task identity

Function and task calls retain their complete hierarchical target during parsing:
`u0.f(a)` and `u1.f(a)` are distinct calls. Flattening gives each instance's
declarations distinct names, qualifies internal calls in that instance, and
renames a function's return variable together with its declaration. Port and
local-variable names retain their subprogram scope. Repeated flattening of
nested instances prepends the outer instance path consistently.

Generated-instance calls such as `g[COUNT-1].u.f(a)` use the same constant-index
resolution as signal references. The common Simulator setup validates qualified
function/task targets after flattening, before selecting an engine; unresolved
hierarchical calls raise `ValueError`. Root-qualified calls such as
`top.u0.f(a)` are matched to the corresponding flattened path.

Compiled function symbols use an injective encoding for qualified or escaped
names. Replacing punctuation with underscores would alias `u0.f` and `u0_f`;
the encoding also escapes source names that resemble its own reserved prefix.
The original lexical subprogram name is retained separately for `%m`, so the
lookup path does not get printed twice.

Inlining a task's copied body scopes its expression-temporary and guard caches
to that expansion. This prevents a later task body from reusing a temporary
whose identity key belonged to a now-discarded copy. The temporary counter
continues across expansions so emitted names stay distinct.

See [hierarchical subprogram regressions](../../tests/test_sim/test_hierarchical_subprograms.py)
for colliding leaf names, internal/nested calls, generated instances, parameter
overrides, repeated task calls, 96-bit data and X masks, compiled symbol
collisions, and unresolved-call diagnostics across all four engine modes.
