# Parser Output Format Research

Research into how other Verilog/SystemVerilog tools format their parser output,
to inform design decisions for veriforge's output format.

## Tool Comparison Overview

| Tool | Language | Output Formats | Abstraction Level | Standard |
|------|----------|---------------|-------------------|----------|
| **Yosys** | C++ | JSON (netlist) | Post-synthesis netlist | Custom |
| **Verilator** | C++ | JSON AST (`.tree.json`) | Full internal AST | Custom |
| **slang** | C++ | JSON AST, Python bindings | CST/AST | Custom |
| **sv-parser** | Rust | Rust `SyntaxTree` struct | Concrete Syntax Tree | IEEE 1800-2017 |
| **Icarus Verilog** | C++ | VVP (compiled), no JSON | Compiled format | N/A |
| **Lark (ours)** | Python | `Tree` object, `.pretty()` text | Parse tree (CST) | IEEE 1364-2005 |

---

## 1. Yosys JSON Format (`write_json`)

**Purpose**: Represents the **post-synthesis netlist**, NOT the parse tree. This is
the elaborated, flattened circuit representation after synthesis passes.

**Abstraction**: Netlist-level (modules, cells, wires, connections as bit-level nets).

### Top-Level Structure

```json
{
  "creator": "Yosys <version>",
  "modules": {
    "<module_name>": {
      "attributes": { ... },
      "parameter_default_values": { ... },
      "ports": { ... },
      "cells": { ... },
      "memories": { ... },
      "netnames": { ... }
    }
  },
  "models": { ... }
}
```

### Port Details

```json
{
  "direction": "input" | "output" | "inout",
  "bits": [2, 3, 4],
  "offset": 0,
  "upto": 0,
  "signed": 0
}
```

- `bits` is a **bit vector** - an array of signal IDs (integers) or constant
  string values (`"0"`, `"1"`, `"x"`, `"z"`)
- `offset` and `upto` preserve the original HDL bit indexing
- Signals are identified by integer IDs that correspond to net connections

### Cell Details

```json
{
  "hide_name": 0,
  "type": "foo",
  "parameters": {
    "P": "00000000000000000000000000101010"
  },
  "attributes": {
    "src": "test.v:3.1-3.55"
  },
  "port_directions": {
    "A": "input",
    "B": "input",
    "Y": "output"
  },
  "connections": {
    "A": [3, 2],
    "B": [2, 3],
    "Y": [5, 4]
  }
}
```

- Parameters are stored as binary string literals (32-bit by default)
- `hide_name` indicates auto-generated names (prefixed with `$`)
- Connections map port names to bit vectors (integer signal IDs)
- `port_directions` only included for cells with known interfaces

### Net Details

```json
{
  "hide_name": 0,
  "bits": [2, 3],
  "offset": 0,
  "upto": 0,
  "signed": 0,
  "attributes": { ... }
}
```

### Key Design Decisions in Yosys JSON

1. **Flat signal namespace** - All signals get unique integer IDs
2. **Bit-level granularity** - Every connection is a vector of individual bit IDs
3. **Post-elaboration** - No procedural code, always blocks, or behavioral constructs
4. **Parameters as binary strings** - Not native JSON numbers
5. **Source location in attributes** - `"src": "file.v:line.col-line.col"`

### Relevance to Our Project

Yosys JSON is a **netlist format**, fundamentally different from a parse tree.
It's useful as an *output target* if we ever want to do synthesis, but NOT as a
model for our parser output format. Our parser operates at a much higher level
of abstraction (source-level syntax tree).

---

## 2. Verilator JSON AST Format (`.tree.json`)

**Purpose**: Dumps the **internal AST** after elaboration. Generated via
`--json-only` or `--dump-tree-json`.

**Abstraction**: Elaborated AST with type information, but preserving the
hierarchical structure.

### Node Structure

Each AST node is a JSON object:

```json
{
  "type": "<AstNodeTypeName>",
  "name": "<pretty_name>",
  "addr": "<hex_address_or_short_id>",
  "loc": "<filename>,<firstLine>:<firstCol>,<lastLine>:<lastCol>",
  "editNum": 42,
  "dtypep": "<dtype_address>",
  "<node_specific_fields>": "...",
  "<op_list_name>": [ ... child nodes ... ]
}
```

### Core Fields (every node)

- `type` - AST node class name (e.g., `AstModule`, `AstVar`, `AstAlways`, `AstAssign`)
- `name` - Human-readable name from `prettyName()`
- `addr` - Node address/ID for cross-references
- `loc` - Source location as `"filename,line:col,line:col"`
- `editNum` - Debug edit counter (optional, controlled by `--no-json-edit-nums`)
- `dtypep` - Reference to data type node (for typed nodes)

### Node-Specific Fields (examples)

**AstModule**:
```json
{
  "type": "AstModule",
  "origName": "counter",
  "verilogName": "counter",
  "level": 1,
  "modPublic": false,
  "isChecker": false,
  "isProgram": false,
  "timeunit": "NONE"
}
```

**AstVar**:
```json
{
  "type": "AstVar",
  "origName": "clk",
  "verilogName": "clk",
  "direction": "INPUT",
  "varType": "PORT",
  "isPrimaryIO": true,
  "isSigPublic": true,
  "isConst": false,
  "lifetime": "STATIC"
}
```

**AstAlways**:
```json
{
  "type": "AstAlways",
  "keyword": "ALWAYS"
}
```

**AstCase**:
```json
{
  "type": "AstCase",
  "kwd": "case",
  "full": true,
  "parallel": false,
  "unique": false
}
```

### Child Node Lists

Children are stored as named arrays (up to 4 "op" slots per node):

```json
{
  "type": "AstModule",
  "stmtsp": [
    { "type": "AstVar", ... },
    { "type": "AstAlways", ... }
  ],
  "activesp": [ ... ]
}
```

### Meta File (`.tree.meta.json`)

Separate file containing:
- File name to short-ID mapping
- Pointer/ID mapping tables
- Used for resolving cross-references in the main tree

```json
{
  "fileNames": { "a": "test.v", "b": "other.v" },
  "idPtrMap": { ... },
  "ptrNames": { ... }
}
```

### Key Design Decisions in Verilator JSON

1. **AST-centric** - Every node has a `type` field mapping to internal C++ class
2. **Cross-references via addresses** - `dtypep`, `addr` fields link nodes
3. **Separate meta file** - Keeps the tree file cleaner
4. **Source location on every node** - Compact format `"file,line:col,line:col"`
5. **Boolean fields only when true** - Uses `dumpJsonBoolIf` to omit false values
6. **Named child lists** - Not generic `children`, but semantic names like `stmtsp`
7. **Format still evolving** - Verilator docs warn JSON format may change

### Relevance to Our Project

Verilator's JSON is closest to what we might want - a hierarchical AST dump.
However, it's an **elaborated** AST (after type resolution, linking, etc.),
while our Lark output is a raw **parse tree**. The node naming (using `Ast`
prefix) and per-node metadata pattern is a good model.

---

## 3. slang (SystemVerilog Language Services)

**Purpose**: Full SystemVerilog compiler frontend with AST dump to JSON.

**Abstraction**: Both Concrete Syntax Tree (CST) and elaborated AST available.

### Output Capabilities

- `--ast-json` flag dumps the AST to JSON
- Python bindings via `pyslang` for programmatic access
- CST round-trips back to original source

### Python API Structure

```python
import pyslang

tree = pyslang.SyntaxTree.fromFile('test.sv')
mod = tree.root.members[0]
mod.header.name.value       # "memory"
mod.members[0].kind         # SyntaxKind.PortDeclaration
mod.members[1].header.dataType  # "reg [7:0]"
```

### Key Features

- `SyntaxKind` enum for all node types (matches IEEE spec grammar rules)
- Tree structure mirrors the formal grammar
- Round-trip capability (CST preserves all tokens including whitespace)
- Python bindings provide natural property access
- Session-based evaluation of expressions

### Relevance to Our Project

slang is a full SystemVerilog (IEEE 1800) compiler - much more ambitious scope.
The key takeaway is its use of enum-based node kinds that map to the formal
grammar, which is exactly how Lark's rule names already work for us.

---

## 4. sv-parser (Rust)

**Purpose**: SystemVerilog parser library, fully IEEE 1800-2017 compliant.

**Abstraction**: Concrete Syntax Tree with variant names from the IEEE spec.

### Output Structure

```rust
// Returns SyntaxTree with preprocessed string + parsed tree
let (syntax_tree, _) = parse_sv(&path, &defines, &includes, false, false);

// Iterable - each node is a RefNode enum variant
for node in &syntax_tree {
    match node {
        RefNode::ModuleDeclarationAnsi(x) => { ... }
        RefNode::ModuleDeclarationNonansi(x) => { ... }
        _ => ()
    }
}
```

### Key Features

- `RefNode` enum variants follow **exactly** the grammar rule names from
  "Annex A Formal syntax" of IEEE 1800-2017
- `Locate` struct provides token position information
- `SyntaxTree::get_str(&locate)` retrieves original source text
- Library-level tool (no JSON output built-in; users write their own)

### Relevance to Our Project

sv-parser's approach of naming tree nodes after the IEEE BNF production rules
is directly analogous to what we already have with Lark. Our rule names in
`verilog.lark` are hand-translated from IEEE 1364-2005 Annex A, so our tree
node names already map to the standard grammar. This validates our approach.

---

## 5. Icarus Verilog (iverilog)

**Purpose**: Open-source Verilog simulator.

**Output**: Compiles to VVP (Verilog Virtual Processor) intermediate format,
which is a custom text-based instruction format. Does not produce a standard
JSON/XML AST dump.

**Relevance**: Useful as a **validation reference** (parse what iverilog parses)
but not as an output format model.

---

## 6. Our Current Output (Lark Tree)

### Current Format: `tree.pretty()`

```
source_text
  description
    module_declaration
      module_keyword  module
      module_identifier
        simple_identifier  counter
      list_of_port_declarations
        port_declaration
          input_declaration
            net_type  wire
            simple_identifier  clk
        port_declaration
          output_declaration
            reg_type  reg
            range
              constant_expression  7
              constant_expression  0
            simple_identifier  count
```

### Lark Tree Object API

```python
tree = parser.parse(verilog_text)

tree.data          # Rule name: "source_text"
tree.children      # List of child Tree or Token objects
tree.meta.line     # Source line (with propagate_positions)
tree.meta.column   # Source column

# Tokens
token.type         # Terminal name: "SIMPLE_IDENTIFIER"
token.value        # Actual text: "counter"
token.line         # Source line
token.column       # Source column
```

### Current Capabilities

- `build_tree(text)` - Returns Lark `Tree` object
- `reconstruct(tree)` - Converts tree back to Verilog text
- `tree.pretty()` - Indented text representation
- Rich print support via `rich.print(tree)`
- Position tracking via `propagate_positions=True`

---

## What Was Done

The approach taken was to use the **model layer** as the primary structured output,
not a raw Lark parse tree JSON. The raw `tree_to_json` approach described in the
recommendations was not implemented as a first-class feature.

Current output capabilities:

- `tree.pretty()` — Lark indented text tree (raw parse output, unchanged)
- `reconstruct(tree)` — Round-trips back to Verilog source
- `Design.to_dict()` / `Design.to_json()` in `model/design.py` — Model-level JSON
  serialization covering modules, ports, parameters, instances, and interfaces.
  This is the "module summary JSON" option from the recommendations. It operates
  on the elaborated model, not the raw CST.

The raw parse tree (CST) JSON was not built as a separate output. The model-layer
JSON proved sufficient for downstream tooling. The CLI (`__main__.py`) exposes
JSON output via `Design.to_json()`.

The comparison of other tools' formats in sections 1–5 above remains accurate
reference material for understanding how our abstraction level differs from tools
like Yosys (post-synthesis netlist) vs. Verilator (elaborated AST) vs. sv-parser
(concrete syntax tree).

---

## References

- Yosys JSON: `backends/json/json.cc` in [YosysHQ/yosys](https://github.com/YosysHQ/yosys)
- Verilator JSON: `--json-only` flag, `V3AstNodes.cpp` dumpJson methods in
  [verilator/verilator](https://github.com/verilator/verilator)
- slang: [sv-lang.com](https://sv-lang.com/), `--ast-json` flag,
  [MikePopoloski/slang](https://github.com/MikePopoloski/slang)
- sv-parser: [dalance/sv-parser](https://github.com/dalance/sv-parser),
  IEEE 1800-2017 Annex A naming
- Lark: [lark-parser/lark](https://github.com/lark-parser/lark),
  `Tree` and `Token` classes
