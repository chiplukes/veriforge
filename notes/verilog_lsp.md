# Verilog LSP Server

A Language Server Protocol (LSP) server for Verilog/SystemVerilog, built on top of `veriforge`.

## Architecture

Three-tier parsing strategy balances responsiveness against correctness:

| Tier | Trigger | Tool | Purpose |
|---|---|---|---|
| 1 — Syntax | `textDocument/didOpen` and `textDocument/didChange` | Verible (`verible-verilog-lint`) when available; Lark (debounced) as fallback | Fast diagnostics; gates tier 2 |
| 2 — File | `textDocument/didSave` | veriforge parser/model | Semantic model for saved files |
| 3 — Full | Startup / interface change | veriforge parser/model (ThreadPoolExecutor) | Cross-file resolution, hierarchy |

**Verible as gatekeeper**: if Verible reports a `syntax-error` rule violation, the file is marked as having a syntax error and veriforge skips it.  The last clean analysis is served with a `> ⚠ Stale:` hover annotation.

**Lark fallback**: when Verible is not installed, a debounced Lark parse runs on every change instead so users still receive syntax diagnostics while typing between saves.

## Package Layout

```
veriforge_lsp/
  __main__.py          # Entry point: python -m veriforge_lsp
  server.py            # VerilogLanguageServer(LanguageServer) + INITIALIZE handler
  workspace.py         # Workspace: Verible + veriforge parser/model orchestration
  index.py             # LocationIndex: position→node and node→refs lookups
  protocol.py          # Type conversion helpers (LSP ↔ Lark SourceLocation)
  handlers/
    text_sync.py       # didOpen/Change/Save/Close
    navigation.py      # definition, references, hover
    symbols.py         # documentSymbol, workspaceSymbol
    extended.py        # verilog/* custom commands + hierarchy push
```

## Standard LSP Features

- **Hover** — Port direction/width, net drivers/loads, instance module, module ports/instances
- **Go-to-definition** — Follows `Identifier.resolved`, `Instance.resolved_module`, `PortConnection.resolved_port`
- **Find references** — All locations that reference the definition node
- **Document symbols** — Module tree with ports, nets, vars, params, instances, always blocks
- **Workspace symbols** — Query across all modules and ports

## Custom Extensions

All custom features are implemented as `workspace/executeCommand` commands:

| Command | Parameters | Returns |
|---|---|---|
| `verilog/setTopModule` | `{"moduleName": str \| null}` | `{"ok": bool, "hierarchyTree": {...}}` |
| `verilog/hierarchyGraph` | `{"top": str?, "maxDepth": int?, "format": "json" \| "text" \| "dot" \| "mermaid"?}` | `{"ok": bool, "hierarchyGraph": {...}, "visualization": str?}` |
| `verilog/resolveHierarchyChildren` | `{"moduleName": str, "instancePath": str?}` | `{"children": [...]}` |
| `verilog/traceSignal` | `{"textDocument": {...}, "position": {...}}` | `{"signal": {...}, "drivers": [...], "loads": [...]}` |
| `verilog/previewHierarchyBoundaryMove` | `{"direction": "pull_up" \| "push_down" \| "collapse" \| "extract", "selection": {...}, "targetParentPath": str?, "newModuleName": str?, "newInstanceName": str?, "extractedModuleName": str?}` | `{"ok": bool, "preview": {..., "engineKind": "boundary" \| "extract" \| "collapse"}, "details": {...}?, "edit": WorkspaceEdit?, "review": {"files": [...]}?}` |
| `verilog/applyHierarchyBoundaryMove` | same as preview | preview payload + `{"applied": false, "appliedByServer": false}` |
| `verilog/previewCollapseHierarchy` | `{"instancePath": "top/u_wrap"}` | Deprecated shim for collapse preview; use `verilog/previewHierarchyBoundaryMove` with `direction="collapse"` |
| `verilog/applyCollapseHierarchy` | `{"instancePath": "top/u_wrap"}` | Deprecated shim for collapse apply; use `verilog/applyHierarchyBoundaryMove` with `direction="collapse"` |
| `verilog/previewExtractModule` | `{"textDocument": {"uri": str}, "range": LSP Range, "extractedModuleName": str, "moduleName": str?, "instanceName": str?}` | Deprecated shim for extract preview; use `verilog/previewHierarchyBoundaryMove` with `direction="extract"` |
| `verilog/applyExtractModule` | same as preview extract | Deprecated shim for extract apply; use `verilog/applyHierarchyBoundaryMove` with `direction="extract"` |
| `verilog/previewHierarchyPullUp` | `{"selection": {"kind": "instance" \| "subtree" \| "module" \| "file", "instancePath": str?, "moduleName": str?, "file": str?}, "targetParentPath": str?}` | Deprecated shim for pull-up preview; use `verilog/previewHierarchyBoundaryMove` with `direction="pull_up"` |
| `verilog/previewHierarchyPushDown` | `{"selection": {"kind": "instance" \| "subtree" \| "module" \| "file", ...}, "newModuleName": str, "newInstanceName": str?, "targetParentPath": str?}` | Deprecated shim for push-down preview; use `verilog/previewHierarchyBoundaryMove` with `direction="push_down"` |
| `verilog.reparse` | none | `{"ok": true}` |

Implementation note: pygls executes `workspace/executeCommand` through
`@ls.command` registrations, not through a generic
`@ls.feature("workspace/executeCommand")` handler. Command callback parameter
annotations must be concrete classes such as `dict`; annotations like
`dict | None` are rejected by pygls argument preparation before the command body
runs.

Server also sends `verilog/hierarchyTree` notifications (server→client) after each full parse.

Apply-ready collapse, extract, and unified boundary-move responses include
`review.files` entries derived from the returned `WorkspaceEdit`. Each entry
contains `uri`, `file`, `currentLabel`, `proposedLabel`, `currentText`, and
`proposedText` so clients can show current-vs-future review buffers before
applying the edit. `WorkspaceEdit` remains the source of truth for applying the
refactor. Review payloads also mark this explicitly with
`review.atomic == true`, `review.applyStrategy == "workspace-edit"`, and
per-file `presentationOnly/acceptsWholeEdit` flags: clients must not treat one
review pane as an independently apply-able subset of a multi-file refactor.

Extract preview responses also include `preview.presentation`, a render-ready
summary for float-style UI clients. It carries `selectionText`,
`replacementText`, `normalizedLines`, `boundaryLines`, `generatedModuleText`,
`diagnosticLines`, `diffText`, and ordered `sections` entries so editor plugins
do not have to reconstruct the preview layout from raw fields.

Hierarchy nodes include optional refactor metadata (`instancePath`,
`wrapperClass`, `confidence`, `diagnostics`, and `refactorActions`) so clients
can highlight safe wrapper-collapse candidates without issuing a separate graph
request. Collapse apply is editor-applied: the server returns a standard
`WorkspaceEdit` in the command result and does not call server-initiated
`workspace/applyEdit`. If a source file is marked stale, collapse commands return
a blocking `stale-source` diagnostic instead of an edit. The current collapse
rewrite subset is limited to `pure_pass_through` wrappers; wrappers containing
combinational adaptation, behavioral logic, parameters, generate/interface
constructs, or unresolved children are classified/diagnosed but not rewritten.

Extract-module commands use the same editor-applied `WorkspaceEdit` contract.
The current safe subset accepts a source selection containing either complete
continuous assignments or complete simple always blocks in one module, optionally
with selected helper localparams and internal net/variable declarations. It
computes input/output/internal boundary signals and generates a replacement child
module. Selected helper declarations are copied into the child and removed from
the parent only when they are internal to the extracted logic. Always-block
extraction currently requires simple procedural assignment targets and blocks
hierarchical references, mixed assign/always selections, multiple outside
drivers for child-driven outputs, memory outputs, and selected boundary
declarations. If `moduleName` is omitted, the server derives the containing
module from the selected file range. Unsupported selections or stale source files
return blocking diagnostics and no edit. Preview metadata includes
`selectionNormalization`, which lists the complete semantic nodes covered by the
source selection and flags unsupported or partial selections before any rewrite
is proposed.

Hierarchy pull-up and push-down previews use the shared boundary-movement
contract. They resolve module, file, instance, or subtree selections, report
source/parent/target endpoints, moved-item summaries, and before/after hierarchy
intent. Pull-up can now return an apply-ready `WorkspaceEdit` for two safe
subsets:

1. direct-parent instance moves, where child parameters are substituted, child
   internal declarations/instances are prefixed with the instance name,
   connected `output reg` parent wires are promoted to regs, and the selected
   instance statement is replaced with the pulled-up logic;
2. design-wide child-definition moves, where a source range inside a reusable
   child module selects complete `always` / `initial` blocks, complete
   continuous assignments, or selected nested child instances (including
   assign+instance structural selections) plus supporting declarations,
   parameters, or localparams. The
   child module is rewritten once, and every parent instance site across the
   design receives a specialized lifted copy of that logic. This subset also
   supports ownership transfer when the moved logic drives an existing child
   output port, as long as each affected instance connects that output to a
   simple parent signal. Selected nested instances may also use writable
   complex child-side output expressions such as concatenations and
   bit/range-selects; those expressions are rewritten against the specialized
   parent-side lifted nets. Selected overrideable child parameters can remain
   on the rewritten child interface while still being specialized into the
   lifted parent logic, selected child localparams are removed from the
   rewritten child when they are owned only by the moved logic, and moved
   logic may reference child-defined functions. Referenced child functions are
   copied into each rewritten parent module with per-site-prefixed names, and
   function calls in the lifted logic are rewritten to those copied helpers.
   Modules may also contain unrelated top-level generate constructs that are
   preserved during the rewrite. Parent instance sites nested under generate
   blocks are now also rewritten in place, so the lifted logic is inserted
   into the same generate branch as the rewritten child instance instead of
   being hoisted to the parent module top level. This same design-wide flow
   also supports selecting complete continuous assignments, instances, and
   always/initial blocks from inside child generate branches; the rewritten
   parent receives a copied minimal generate wrapper around the lifted logic so
   the child-side generate condition is preserved at each site. User-defined
   tasks remain blocked in this flow.

The instance-move pull-up edit plan is intentionally minimal: declaration
promotions are separate edits from the instance-site replacement, so review
clients should apply all edits in the returned `WorkspaceEdit` when building
the proposed file view. The child-definition pull-up path reports
`metadata.scope == "design-wide"` together with `siteCount`, `parentModules`,
and `sitePaths` so clients can make the blast radius obvious before apply.
Unsupported pull-up cases remain preview-only with `applyReady: false`.
Pull-up `targetParentPath` is accepted so editor source/destination marks can
round-trip through the request; for now it must match the selected instance's
direct parent. Push-down apply is now available for the whole-module-wrap
subset (Slice A): the selected module is replaced with a wrapper whose body is
a single pass-through child instance, and a new child module containing the
original body is appended. Module/instance name collisions and unsupported
features (functions, tasks, typedefs, generate/specify blocks, interface
instances, empty bodies) block the apply with explicit diagnostics; narrower
push-down selections remain preview-only with `applyReady: false`. Push-down
now also accepts `instance`
and `subtree` selection kinds, which resolve to the instance's underlying
module and rewrite that module in place; when the resolved module is
instantiated at multiple sites the preview attaches a non-blocking
`push-down-module-multi-instance` warning and reports `instanceSiteCount`
in `metadata` so editors can flag the broader impact. Push-down does not
yet honor `targetParentPath`; supplying one returns a blocking
`push-down-target-not-supported` diagnostic. Apply-ready push-down previews
report the rewritten module via `afterHierarchy.rewrittenModule` and
`metadata.rewrittenModule`.

Push-down also accepts a **range** selection (`selection.kind == "range"`)
that internally routes to the extract engine: the LSP detects a range
selection (presence of `selection.kind == "range"`, an LSP-style
`selection.range`, or both `selection.startLine`/`selection.endLine`) and
calls the unified boundary engine with `direction="extract"`. The response
shape is identical to extract's payload (with `preview.boundary`,
`preview.presentation`, file-creation edits for the new child module file,
etc.); the LSP also stamps `preview.metadata.pushDownMode = "range"` and
`preview.metadata.origin = "extract"` so editors can tell the origin
without inspecting the schema. `targetParentPath` is rejected for range
push-down with `push-down-target-not-supported`. Range push-down keeps
extract's confidence (`"preview"`) and diagnostic codes verbatim so the
existing extract-module help/UX surface continues to apply.

### Unified boundary-move API

`verilog/previewHierarchyBoundaryMove` and
`verilog/applyHierarchyBoundaryMove` are the canonical commands for all
four boundary movements. The `direction` field selects the engine
(`pull_up`, `push_down`, `collapse`, `extract`); the response carries an
`engineKind` discriminator on `preview` plus an optional `details` object
holding the engine-specific payload (extract / collapse). The legacy
per-direction commands (`previewHierarchyPullUp`, `previewHierarchyPushDown`,
`previewCollapseHierarchy`, `applyCollapseHierarchy`, `previewExtractModule`,
`applyExtractModule`) are now **soft-deprecated**: they remain supported as
thin shims over the same unified core, but the server logs a deprecation
warning when they are invoked. New code and generated code actions should use
the unified surface directly.

## Configuration

Per-project config at `<workspace_root>/.veriforge_lsp.json`:

```json
{
  "top_module": "top",
  "include_dirs": ["rtl", "ip"],
  "parse_options": {
    "defines": ["SIMULATION=1", "DATA_W=32"]
  },
  "verible_lint_path": "verible-verilog-lint",
  "verible_rules": ["-line-length", "-no-tabs"]
}
```

- `top_module`: pin the top module for hierarchy commands.
- `include_dirs`: directories added to the include search path on every parse.
- `parse_options.defines`: preprocessor defines as `"KEY"` or `"KEY=VALUE"` strings.
- `verible_lint_path`: path to the `verible-verilog-lint` binary (defaults to PATH search).
- `verible_rules`: Verible rule overrides; merged with any `--verible-rules` CLI flags.

## Requirements and entry points

Install the optional LSP dependencies before running the server:

```bash
uv pip install -e ".[lsp]"
```

The package exposes two equivalent entry points:

```bash
uv run python -m veriforge_lsp
uv run veriforge-lsp
```

The server defaults to stdio mode for editor clients. For local debugging, TCP
mode is available:

```bash
uv run veriforge-lsp --tcp --host 127.0.0.1 --port 2087
```

Verible (`verible-verilog-lint`) is used when available for fast syntax
diagnostics. Configure a non-default executable with `verible_lint_path` in
`.veriforge_lsp.json`. Rule overrides can be supplied with `--verible-rules`, for
example:

```bash
uv run veriforge-lsp --verible-rules=-line-length,-no-tabs
```

## LocationIndex

Position-to-node lookup uses an `IntervalTree` with `line * 10_000 + column` encoding.
This enables 2D containment queries without a 2D tree (good for files < 10,000 columns wide).

- Lark uses 1-based line and column numbers
- LSP uses 0-based line and character numbers
- `node_at(file, lsp_line, lsp_char)` converts internally before lookup

## Running

```bash
# Install with LSP extras
uv pip install -e ".[lsp]"

# Start server (stdio mode, used by editor clients)
uv run python -m veriforge_lsp
# or
uv run veriforge-lsp
```

## Tests

```bash
uv run --extra test --extra lsp python -m pytest tests/test_lsp/ -q
```
