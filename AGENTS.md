# VeriForge Project Instructions

## Project Overview

veriforge is a project focused on parsing, analyzing, transforming, generating, and simulating Verilog/SystemVerilog designs.

The parser uses [Lark](https://github.com/lark-parser/lark), with Verilog 2005 BNF hand-translated to EBNF and extended for SystemVerilog.

## Project Guidelines

### Documentation

- Start with the [codebase map](.codemap.md) to find the relevant implementation and existing helpers.
- Check [notes/](notes/) for technical documentation before starting work. Useful entry points are the [developer guide](notes/developer_guide.md), [architecture](notes/developer/architecture.md), and [AI getting started guide](notes/ai_getting_started.md).
- See [notes/developer/files.md](notes/developer/files.md) for the file listing to avoid duplicating functionality.
- Keep notes focused on technical information, not process documentation
- Keep documentation up to date with code changes.

### Codebase Map Maintenance

- Preserve existing `# cm:ID` source markers when editing code. Keep each ID attached to the implementation it identifies, including when moving or renaming that implementation.
- Add a unique source marker and a matching entry in [.codemap.md](.codemap.md) when adding an important public API, subsystem, or implementation entry point. Small private helpers do not each need an entry.
- Update the map's names, paths, and descriptions when code moves or behavior changes. When mapped code is removed, remove its obsolete map entry; do not reuse its ID for unrelated code.
- Before finishing code edits, check the affected map entries: every `cm://ID` must resolve to exactly one source marker, and new source markers must have map entries. Use `rg -n 'cm:ID' src veriforge_lsp` to locate an anchor.

### Code Style

- This is a uv-managed project: use `uv run` instead of `python` directly
- Line length limit: 120 characters
- Lint with `uv run ruff check <path>`, format with `uv run ruff format <path>`

### Testing

- Check for related test files in `/tests` when modifying code
- When running tests, use `uv run pytest <path> --tb=no -q` for pass/fail summary (minimal output)
- Use verbose flags (`-v`, `--tb=short`) only when debugging specific failures
- Do NOT pipe pytest output through PowerShell cmdlets (`Select-Object`, `Tee-Object`, etc.) — it causes truncation and repeat runs
- Wait for a test command to fully complete before running another; do not retry on empty output
- the full test run: `uv run pytest tests/ --tb=no -q` takes a long time to complete.  Whenever this test is issued, just stop.  Continue after the output is copied into the chat dialog.

### Simulator Engines

- This project has three simulator engines and four selectable modes: `reference`, `vm`, `vm-fast`, and `compiled`. See [notes/simulation/simulator_engines.md](notes/simulation/simulator_engines.md) for details.
- When bugs are found in one, make sure all simulators are kept in sync with the fix.  Use the same test cases to validate all engines.
- Include values wider than 64 bits when a simulator change could affect widths, selects, storage, or arithmetic.
- **Compiled engine performance**: Verilog `initial` blocks with timing (`#delay`, `while(1)`) run as Python coroutines and are slow. For fast simulation, use `batch_run()` from Python instead of Verilog clock generators. See [simulator engine notes](notes/simulation/simulator_engines.md) for testbench performance patterns.

### Platform

- Primary development on Windows; support Linux where possible

### Completion Response and Suggested Commit

- After completing work, summarize what changed and report validation results, including any checks that were not run or did not pass.
- Provide a suggested Git commit message: a concise, one-line subject followed by a blank line and a bullet list suitable for the commit description.
- Write the subject in the imperative mood. Use the bullets to explain the concrete changes, their purpose, and relevant validation; keep the proposed message scoped to the work completed.
- Providing a suggested message does not mean creating a commit; commit only when the user asks.
