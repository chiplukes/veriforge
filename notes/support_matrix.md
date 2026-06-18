# Support Matrix

This document is the **cross-surface entry point** for support status across the
project. It is meant to answer a practical question quickly:

> For a given construct, how far does support go across parsing, emission,
> simulation, DSL conversion, and editor tooling?

It is not a replacement for focused tests or subsystem-specific notes. Instead,
it links those detailed sources together so support claims live in one obvious
place.

`docs\grammar_support.md` remains the generated parser-rule inventory. It is
useful for grammar coverage and examples, but it does not describe the full
runtime/tooling stack.

## Legend

| Status | Meaning |
| --- | --- |
| Supported | Intended current surface; common use is covered. |
| Partial | Common cases work, but important gaps, fallbacks, or caveats remain. |
| Limited | Narrow subset only; available for some workflows but not broadly dependable. |
| Parse-only | Parser/model can preserve the construct, but later surfaces do not fully execute or transform it. |
| Fallback | Available through a slower or less-native path. |
| Planned | Explicitly desired, but not implemented yet. |
| Out of scope | Not part of the current project target. |
| Not applicable | That surface does not meaningfully apply to the construct. |

## Detailed sources by surface

Use this table when the matrix below says "Partial" and you need the subsystem's
current detailed story.

| Surface | Primary detail source | Notes |
| --- | --- | --- |
| Parser / model / emitter / formatter | `docs\grammar_support.md`, `notes\python_overview.md` | Grammar metadata plus codebase structure overview. |
| Reference and VM simulation | focused `tests\test_sim\...` files | Simulator test suites are the primary coverage record. |
| Compiled simulation | `notes\simulation\wide_signal_coverage.md` | Wide-signal operation coverage matrix for the compiled engine. |
| DSL builder | `notes\dsl\dsl_coverage.md` | Detailed builder-oriented construct matrix. |
| Verilog-to-DSL converter | `notes\dsl\dsl_conversion_coverage.md` | Explicit current unsupported output shapes and recommended next steps. |
| LSP | `notes\veriforge_lsp.md` | Standard features, custom commands, and current safe refactor subsets. |
| Hierarchy/refactor tooling | `notes\veriforge_lsp.md` | Implemented preview/apply surface, diagnostics, and remaining blocked cases. Forward work in `notes\plans\plan_refactor_tool.md`. |

## Scope notes

- The **DSL builder** column means "can the DSL construction API represent this
  area usefully?", not "does every downstream simulator path have full parity".
- The **Verilog-to-DSL** column means conversion from parsed Verilog/SystemVerilog
  into DSL code, which is intentionally more conservative than the builder.
- The **LSP** column means editor analysis/refactor support for saved or resolved
  workspace state, not runtime execution semantics.
- The **Compiled simulation** column stays at a high level; see `notes/simulation/wide_signal_coverage.md` for detailed operation coverage.

## Cross-surface construct matrix

| Construct area | Parser / model | Emitter / formatter | Reference / VM simulation | Compiled simulation | DSL builder | Verilog-to-DSL | LSP |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Core modules, ports, nets, regs, parameters, localparams | Supported | Supported | Supported | Partial | Supported | Supported for common cases | Supported |
| Continuous, blocking, and nonblocking assignments | Supported | Supported | Supported | Partial | Supported | Supported for common cases | Supported |
| Common expressions: arithmetic, bitwise, logical, comparisons, ternary | Supported | Supported | Supported | Partial | Supported for common operators | Partial | Partial |
| Bit/range/part selects, concatenation, replication | Supported | Supported | Supported | Partial | Supported | Partial | Partial |
| Module instantiation and multi-file elaboration/linking | Supported | Supported | Supported through elaboration/flattening | Partial | Supported for construction | Partial | Partial |
| Memories and memory element read/write flows | Supported | Supported | Supported for intended memory patterns | Partial | Supported for declaration/construction | Supported for simple memory forms | Partial |
| Delays, event controls, and timing-oriented procedural flow | Supported | Supported | Supported | Fallback / partial | Supported for common testbench use | Partial | Limited |
| Common system tasks: `$display`, `$write`, `$monitor`, `$finish`, `$stop` | Supported | Supported | Supported | Partial | Supported | Partial | Limited |
| Memory load tasks: `$readmemh`, `$readmemb` | Supported | Supported | Supported for intended memory patterns | Partial | Supported | Partial | Limited |
| Packages, imports, typedefs, enums | Supported | Supported | Supported for intended RTL subset | Partial | Partial | Partial | Partial |
| Packed structs and unions | Supported | Supported | Partial | Partial | Partial | Partial | Partial |
| Interfaces and modports | Supported | Supported | Partial | Partial | Partial | Partial | Partial |
| Assignment patterns | Supported | Supported | Partial | Partial | Partial | Partial | Limited |
| Functions and tasks | Supported | Supported | Partial | Partial | Out of scope / Python is preferred | Partial / unsupported | Partial |
| Generate constructs | Supported | Supported | Supported through elaboration | Partial | Out of scope / Python is preferred | Partial / unsupported | Partial |
| VCD dumping/comparison and external cosim validation | Not applicable / API-oriented | Not applicable | Supported | Partial | Not applicable | Not applicable | Not applicable |

## DSL and conversion summary

This section keeps the high-level DSL story visible without replacing the more
detailed DSL-specific notes.

| Area | DSL builder | Verilog-to-DSL converter | Notes |
| --- | --- | --- | --- |
| Ports, wires, regs, parameters, localparams | Supported | Supported for common cases | See `notes\dsl\dsl_coverage.md`. |
| Arithmetic, bitwise, logical, comparison, ternary, concat, replication, selects | Supported for common operators | Partial | Arithmetic shifts and case equality now convert through explicit DSL helpers; broader control-flow and module-level converter gaps remain. See `notes\dsl\dsl_conversion_coverage.md`. |
| Always blocks, initial blocks, if/else, case | Supported | Supported for common cases | Control-flow-heavy testbench constructs remain less complete in the converter. |
| System tasks and common system functions | Supported for common cases | Partial | Converter support is intentionally incremental; `$time`, `$clog2`, `$signed`, and `$unsigned` now map to explicit DSL helpers. |
| Functions, tasks, generate blocks | Python functions and Python control flow are preferred | Partial / unsupported | Some Verilog constructs are intentionally translated into Python-side patterns rather than direct DSL equivalents; see `notes\dsl\dsl_conversion_coverage.md` for manual rewrite guidance. |
| Specify blocks | Out of scope for DSL construction | Unsupported | Parsed/emitted opaquely elsewhere. |

## Hierarchy refactor and editor support

| Area | Status | Notes |
| --- | --- | --- |
| Hierarchy graph and wrapper classification | Supported | CLI and LSP expose resolved hierarchy payloads with stable slash-separated instance paths and wrapper metadata. |
| Graph visualization export | Supported | CLI and LSP can produce text, DOT, and Mermaid hierarchy views. |
| Pure pass-through wrapper collapse preview | Supported | CLI and LSP return structured diagnostics, edit plans, renames, diffs, and `WorkspaceEdit` payloads. |
| Pure pass-through wrapper collapse apply | Partial | CLI can write guarded parent-module replacements. LSP returns editor-applied `WorkspaceEdit` payloads and does not initiate `workspace/applyEdit`. |
| Structural, behavioral, parameterized, generate, interface, and unresolved wrappers | Planned / blocked | These are classified and diagnosed, but collapse is intentionally blocked until safer transforms are implemented. |
| Extract selected logic into a submodule | Partial | CLI, LSP, and Peovim preview/write support complete continuous assignments, complete simple always/initial blocks, safe selected instance groups with direct signal connectivity, and coherent mixed structural selections that combine assigns with instance groups, plus helper declaration movement and selected parent-parameter pass-through. Broader connection patterns and better selection UX remain planned. |
| Hierarchy boundary movement API | Partial | Unified `previewHierarchyBoundaryMove` / `applyHierarchyBoundaryMove` covers all four directions. Pull-up apply: direct-parent instance moves and design-wide child-definition moves (complete assigns/always/initial/instances, including from generate branches). Push-down apply: whole-module-wrap and instance/subtree selections; range push-down routes to the extract engine. Cross-file and cross-tree moves remain planned; see `notes\plans\plan_refactor_tool.md`. |

## Out-of-scope or low-priority areas

| Area | Status | Notes |
| --- | --- | --- |
| Specify timing execution | Out of scope | Specify blocks may be parsed and emitted opaquely, but timing semantics are not executed. |
| Gate / UDP / config-library source text | Partial / parse-oriented | Not the main RTL-oriented execution target. |
| Full strength and tristate resolution | Partial / low priority | Modern internal RTL often avoids this style. |
| SV verification features: classes, assertions/SVA, covergroups, constraints/randomize, dynamic arrays, queues, bind, program blocks | Out of scope | These are outside the current RTL-oriented subset. |

## Maintenance rules

1. Update this file when a feature becomes newly supported, intentionally
   unsupported, or delegated to a different plan.
2. Link support claims to detailed notes, tests, or examples when possible.
3. Do not expand compiled-simulator claims here without checking the active
   plans in `notes\plans\` and
   `notes\simulation\wide_signal_coverage.md`.
4. Keep `notes\dsl\dsl_coverage.md` and
   `notes\dsl\dsl_conversion_coverage.md` as the detailed DSL sources; this file
   is the cross-surface overview.
