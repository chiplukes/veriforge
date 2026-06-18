# veriforge Project Context for AI Assistants

## Project Overview

veriforge is a project focused on parsing, analyzing, transforming, generating, and simulating Verilog/SystemVerilog designs.

This is based on the https://github.com/lark-parser/lark project.

Verilog 2005 specification has BNF grammar which has been hand translated to EBNF for use with the Lark parser.

## Project Guidelines

### Documentation
- Check `/notes` folder for technical documentation before starting work
- See `notes/python_overview.md` for file listing to avoid duplicating functionality
- Keep notes focused on technical information, not process documentation
- keep documentation up to date with code changes

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
- This project has 3 different simulator engines: reference, VM, and compiled.  Each has different performance and compatibility characteristics.  See `notes/simulator_engines.md` for details.
- When bugs are found in one, make sure all simulators are kept in sync with the fix.  Use the same test cases to validate all engines.
- **Compiled engine performance**: Verilog `initial` blocks with timing (`#delay`, `while(1)`) run as Python coroutines and are slow. For fast simulation, use `batch_run()` from Python instead of Verilog clock generators. See `notes/simulator_engines.md` "Testbench Performance Patterns" section.

### Platform
- Primary development on Windows; support Linux where possible
