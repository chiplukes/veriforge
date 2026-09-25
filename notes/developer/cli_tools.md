# CLI Tools

Developer/internals CLI tools — grammar visualization, hierarchy refactor
inspection, and grammar metadata generation. For the end-user CLI
(`veriforge tree`, `parse-file`, `generate-python-testbench`, etc.) see
[getting_started.md](../getting_started.md) and
[cli_json_schema.md](cli_json_schema.md).

## Verilog Parser

Parse Verilog files and display the AST:

```powershell
# Parse default test file
uv run python -m veriforge -t

# Parse a specific file
uv run python -m veriforge -f path/to/file.v -t

# With debug output
uv run python -m veriforge -f file.v -t -d

# Reconstruct Verilog from AST
uv run python -m veriforge -f file.v -t -r
```

Options:
| Flag | Description |
|------|-------------|
| `-f, --file PATH` | Path to Verilog file (default: tests/test_verilog_parser/verilog/verilog_all.v) |
| `-t, --tree` | Display parse tree |
| `-r, --reconstruct` | Reconstruct Verilog from parse tree |
| `-d, --debug` | Enable debug mode |
| `-parser {earley,lalr}` | Parser type (default: earley) |
| `-log LEVEL` | Logging level: debug, info, warning, error, critical |
| `--version` | Show version |

## Grammar Tree Visualization

Visualize the grammar rule hierarchy from `verilog.lark`:

```powershell
# Show supported rules only (default)
uv run python -m veriforge.lark_file.gen_tree

# Show all rules (including unsupported)
uv run python -m veriforge.lark_file.gen_tree --all

# Limit depth
uv run python -m veriforge.lark_file.gen_tree --depth 5

# Start from a specific rule
uv run python -m veriforge.lark_file.gen_tree --root module_declaration

# Quiet mode (rich tree only, no text output)
uv run python -m veriforge.lark_file.gen_tree -q

# Combined example
uv run python -m veriforge.lark_file.gen_tree -a -d 4 -r expression -q
```

Options:
| Flag | Description |
|------|-------------|
| `-a, --all` | Show all rules, not just supported ones |
| `-d, --depth N` | Maximum depth to display (default: 8) |
| `-r, --root RULE` | Root rule to start from (default: verilog) |
| `-q, --quiet` | Only show rich tree, suppress text output |

Output features:
- Unsupported rules shown in red with `(unsupported)` marker
- Terminals (UPPERCASE) shown dimmed
- Recursive references detected and marked
- Line numbers from `verilog.lark` shown after each rule

## Hierarchy Refactor Inspection

Inspect resolved project hierarchy and wrapper candidates:

```powershell
# Print wrapper classifications as JSON for editor/tooling integration
uv run python -m veriforge hierarchy wrappers rtl --top top --json

# Print the hierarchy graph as JSON, including Peovim-compatible node metadata
uv run python -m veriforge hierarchy graph rtl --top top --json

# Export graph formats for visualization
uv run python -m veriforge hierarchy graph rtl --top top --format dot
uv run python -m veriforge hierarchy graph rtl --top top --format mermaid

# Preview a pure pass-through wrapper collapse as JSON or unified diff
uv run python -m veriforge hierarchy collapse rtl --top top --instance top/u_wrap --preview --json
uv run python -m veriforge hierarchy collapse rtl --top top --instance top/u_wrap --preview

# Apply a safe pure pass-through wrapper collapse and reparse the project
uv run python -m veriforge hierarchy collapse rtl --top top --instance top/u_wrap --write --json

# Preview extracting selected continuous assignments into a child module
uv run python -m veriforge hierarchy extract rtl --module top --range rtl/top.v:42-47 --name extracted_logic --preview --json

# Limit serialized hierarchy depth
uv run python -m veriforge hierarchy graph rtl --top top --max-depth 3 --json
```

Initial classifications are conservative:

| Class | Meaning |
|-------|---------|
| `pure_pass_through` | Single-instance wrapper with direct port aliases or simple pass-through assigns. |
| `structural_wrapper` | Structural module with instances but not yet safe for automatic collapse. |
| `behavioral_wrapper` | Module contains always/initial/function/task behavior and is visualization-only for now. |
| `unknown_or_unsupported` | Unresolved child, generate/specify/interface complexity, recursion, or unsupported source shape. |

## Grammar Metadata Tool

Extract metadata from `verilog.lark` for documentation and testing:

```powershell
# Show statistics
uv run python -m veriforge.lark_file.parse_metadata --stats

# Generate markdown support table
uv run python -m veriforge.lark_file.parse_metadata --table -o docs/grammar_support.md

# Export as JSON
uv run python -m veriforge.lark_file.parse_metadata --json -o docs/grammar_deps.json

# Filter by section
uv run python -m veriforge.lark_file.parse_metadata --section A.8 --table

# Include dependencies in table
uv run python -m veriforge.lark_file.parse_metadata --table --deps

# Preview DEPS tag generation (dry run)
uv run python -m veriforge.lark_file.parse_metadata --generate-deps --dry-run
```

Options:
| Flag | Description |
|------|-------------|
| `--stats` | Show statistics summary |
| `--table` | Generate markdown support table (default) |
| `--json` | Output as JSON |
| `--section PREFIX` | Filter by section (e.g., "A.1", "A.8") |
| `--deps` | Include dependencies in table |
| `--generate-deps` | Generate DEPS tags in verilog.lark |
| `--dry-run` | Preview changes without modifying files |
| `--no-examples` | Exclude examples from table |
| `-o, --output FILE` | Output file path |
| `-f, --file FILE` | Path to verilog.lark |

Metadata tags extracted:
- `SECTION:` - Grammar section identifier
- `BNF:` - Original IEEE 1364-2005 BNF
- `PRIORITY:` - HIGH, MEDIUM, LOW
- `SYNTHESIZABLE:` - YES, NO, PARTIAL
