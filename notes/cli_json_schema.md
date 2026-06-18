# CLI JSON Schema

This note defines the machine-readable output contract for `veriforge` CLI commands that support `--json`.

## Supported commands

The following subcommands support JSON output:

- `parse-file`
- `parse-directory`
- `export-dsl`
- `generate-python-testbench`
- `hierarchy` (all sub-subcommands: `graph`, `wrappers`, `collapse`, `extract`, `pull-up`, `push-down`)
- `lint`

Commands such as `tree`, `reconstruct`, and `format` remain text-oriented and do not provide `--json` output.

## Success envelope

Successful JSON responses use this top-level shape:

```json
{
  "command": "parse-file",
  "success": true,
  "result": {}
}
```

Fields:

- `command`: CLI subcommand name.
- `success`: Always `true` for successful execution.
- `result`: Command-specific payload.

## Error envelope

Runtime and parse-time failures use this top-level shape:

```json
{
  "command": "parse-file",
  "success": false,
  "error": {
    "type": "FileNotFoundError",
    "message": "[Errno 2] No such file or directory: 'rtl/missing.v'"
  }
}
```

Fields:

- `command`: CLI subcommand name.
- `success`: Always `false` for failed execution.
- `error.type`: Exception or parser error class name.
- `error.message`: Human-readable error message.

## Exit codes

- `0`: Successful execution.
- `1`: Runtime failure after argument parsing completed.
- `2`: Argument parsing failure for a JSON-capable subcommand.

## Command result payloads

### `parse-file`

```json
{
  "root": "rtl/top.v",
  "files": 1,
  "modules": 1,
  "interfaces": 0,
  "packages": 0,
  "top_modules": ["top"]
}
```

### `parse-directory`

```json
{
  "root": "rtl",
  "files": 12,
  "modules": 7,
  "interfaces": 1,
  "packages": 0,
  "top_modules": ["soc_top"]
}
```

### `export-dsl`

```json
{
  "output_dir": "out_dsl",
  "written": [
    "out_dsl/top.py",
    "out_dsl/child.py"
  ]
}
```

### `hierarchy`

All `hierarchy` sub-subcommands support `--json`. The `command` field in the envelope is the compound string `"hierarchy <subcommand>"` (e.g., `"hierarchy graph"`, `"hierarchy collapse"`).

**`hierarchy graph`** — hierarchy tree:

```json
{
  "root": "rtl/",
  "top": null,
  "nodes": [...],
  "edges": [...]
}
```

**`hierarchy wrappers`** — wrapper classification:

```json
{
  "root": "rtl/",
  "top": null,
  "wrappers": [
    {"instancePath": "top/u_wrap", "moduleName": "wrap", "wrapperClass": "pass_through", "confidence": "high"}
  ],
  "stats": {}
}
```

**`hierarchy collapse` / `hierarchy extract` / `hierarchy push-down`** — preview + optional apply:

```json
{
  "root": "rtl/",
  "top": null,
  "preview": {"ok": true, "diff": "...unified diff..."},
  "apply": {"applied": true, "writtenFiles": ["rtl/top.v"]}
}
```

`"apply"` is `null` when `--preview` is used instead of `--write`.

**`hierarchy pull-up`** — preview only (no `--write` yet):

```json
{
  "root": "rtl/",
  "top": null,
  "preview": {"ok": true, ...}
}
```

### `generate-python-testbench`

Key input flags (full list via `--help`):

| Flag | Description |
|------|-------------|
| `--file PATH` | Single DUT source file |
| `--directory DIR` | Multi-file project root |
| `--module NAME` | Target module (auto-detected if only one top module) |
| `--output PATH` | Write to file (prints to stdout if omitted) |
| `--enhanced` | Use `TestbenchPlan` for multi-domain, interface-aware generation |
| `--style bench\|legacy` | `bench` = `Testbench`/`bench.iface()` scaffold; `legacy` = raw `Simulator` |
| `--auto-deps` | Scan sibling files to embed child-module paths in `DEPS` |
| `--include-dir DIR` | Extra directory to search during `--auto-deps` (repeatable) |
| `--dut-source-path PATH` | Path embedded as `DUT_PATH` in `--style=bench` scaffold (defaults to `-f/--file`) |
| `--cosim` | Append a `validate_with_icarus()` helper; requires `iverilog`/`vvp` on PATH |
| `--explain-plan` | Print the inferred plan and exit (no code generated) |
| `--engine reference\|vm\|vm-fast\|compiled` | Target engine; `vm-fast` uses the Cython-accelerated VM; `compiled` emits `compile_native()` when lowerable |
| `--clock-override NAME=PERIOD` | Override inferred clock period |
| `--reset-override NAME=POLARITY` | Override inferred reset polarity (`active_high`\|`active_low`) |
| `--iface-domain PREFIX=DOMAIN` | Force interface binding to a specific clock domain |
| `--domain-alias CLOCK=ALIAS` | Rename a clock's domain label |
| `--no-strict` | Allow ambiguous domain inference instead of failing |

When writing to stdout:

```json
{
  "module_name": "top",
  "output_path": null,
  "text": "\"\"\"Auto-generated Python testbench skeleton.\"\"\"\n..."
}
```

When writing to a file:

```json
{
  "module_name": "top",
  "output_path": "tb_top.py",
  "written": true
}
```

When `--explain-plan` is used:

```json
{
  "module_name": "top",
  "plan": {
    "domains": [...],
    "interfaces": [...],
    ...
  }
}
```

### `lint`

```
veriforge lint <path> [--skip CODE ...] [--json]
```

| Flag | Meaning |
| --- | --- |
| `path` | File or directory to lint (positional) |
| `--skip CODE ...` | Suppress one or more lint codes (e.g. `UNDRIVEN UNUSED`) |
| `--json` | Emit results as JSON |

```json
{
  "path": "rtl/",
  "total": 2,
  "warnings": [
    {"code": "UNDRIVEN", "module": "top", "signal": "unused_wire", "instance": null, "message": "..."},
    {"code": "LATCH_INFERRED", "module": "fsm", "signal": null, "instance": null, "message": "..."}
  ]
}
```

### `format`

```
veriforge format <file> [--style knr|allman|gnu] [--write]
```

Text-only output (no `--json`). Prints formatted Verilog to stdout unless `--write` is specified, in which case the file is overwritten in place.

## Stability

This schema is intended for automation. Prefer matching on the top-level `command` and `success` fields first, then inspecting `result` or `error`.
