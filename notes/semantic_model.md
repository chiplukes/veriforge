# Verilog Semantic Model

The **Verilog Semantic Model** is a structured Python object graph that
represents parsed Verilog source code as meaningful, navigable design data.
It sits between the raw parse tree (CST) produced by the Lark grammar and
any downstream analysis, transformation, or code generation.

Where a parse tree captures *syntax* — tokens and grammar rules — the
semantic model captures *meaning*: modules have ports with directions,
always blocks have sensitivity classifications, expressions form
evaluable trees, and signals track their drivers and loads.

```
  Verilog source text
        │
        ▼
  Lark parse tree (concrete syntax tree)      ← Layer 1
        │
        ▼
  Semantic model (Python object graph)        ← this document
        │
        ├──▶ Verilog code generation (emit)
        ├──▶ JSON serialization
        ├──▶ Connectivity analysis
        ├──▶ Simulation (all engines)
        ├──▶ DSL / Verilog-to-DSL translation
        └──▶ Lint analysis
```

## Building the Model

The model is created from parsed Verilog in three steps:

```python
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms import extract_comments, tree_to_design
from veriforge.codegen import emit_module

# 1. Extract and preserve comments (replaced with whitespace to keep positions)
source = open("counter.v").read()
cleaned, comments = extract_comments(source, source_file="counter.v")

# 2. Parse the cleaned source into a Lark tree
parser = verilog_parser(start="verilog")
tree = parser.build_tree(cleaned)

# 3. Transform the tree into the semantic model
design = tree_to_design(tree, source_file="counter.v",
                        comments=comments, source_text=source)

# Inspect the result
for module in design.modules:
    print(module.name, len(module.ports), "ports")

# Emit back to Verilog
for module in design.modules:
    print(emit_module(module))
```

---

## Core Concepts

### Everything is a VerilogNode

Every object in the model inherits from `VerilogNode`, which provides:

| Feature | Description |
|---------|-------------|
| **Source location** | File, line, column, end position (`node.loc`) |
| **Comments** | Leading and trailing comments attached to this construct (`node.comments`) |
| **Parent pointer** | Navigate upward to the containing construct (`node.parent`) |
| **Tree traversal** | `node.walk()` yields all descendants depth-first |
| **Type search** | `node.find(Port)` yields all `Port` descendants |
| **Root navigation** | `node.root()` walks up to the top-level `Design` node |
| **Serialization** | `node.to_dict()` for JSON-compatible output |

### Memory-Efficient Design

All model classes use `__slots__` instead of `__dict__`. This reduces
per-instance memory by 40–60% and makes the classes compatible with
future Cython compilation for simulation performance.

---

## Model Hierarchy

A parsed design forms a tree:

```
Design
 ├── Module                         (one or more)
 │    ├── Parameter / Localparam    (module-level constants)
 │    ├── Port                      (interface signals)
 │    ├── Net                       (wires, tri, supply, etc.)
 │    ├── Variable                  (reg, integer, real, time, event)
 │    ├── ContinuousAssign          (assign lhs = rhs)
 │    ├── Instance                  (sub-module instantiations)
 │    │    ├── ParameterBinding     (#(.WIDTH(8)))
 │    │    └── PortConnection       (.clk(sys_clk))
 │    ├── AlwaysBlock               (always @(...) ...)
 │    │    └── Statement tree       (if, case, assign, loops, ...)
 │    ├── InitialBlock              (initial ...)
 │    │    └── Statement tree
 │    ├── FunctionDecl              (function ... endfunction)
 │    ├── TaskDecl                  (task ... endtask)
 │    ├── GenerateFor / If / Case   (generate constructs)
 │    │    └── GenerateBlock        (items inside generate)
 │    ├── GenvarDecl                (genvar i)
 │    ├── TypedefDecl               (typedef enum/struct/union)
 │    ├── ImportDecl                (import pkg::item)
 │    └── SpecifyBlock              (specify ... endspecify, opaque)
 ├── Interface                      (SV interface declarations)
 │    └── Modport / ModportPort
 └── Package                        (SV package declarations)
```

---

## Design & Module

### Design

The root container. A design can span multiple source files and contain
multiple modules.

| Field | Type | Description |
|-------|------|-------------|
| `modules` | `list[Module]` | All parsed module definitions |
| `interfaces` | `list[Interface]` | SV interface declarations |
| `packages` | `list[Package]` | SV package declarations |
| `source_files` | `list[str]` | Paths of files that were parsed |

Key methods:
- `get_module(name)` — look up a module by name
- `get_top_modules()` — modules never instantiated by another module
- `merge(other)` — merge a second `Design` into this one (deduplicates by name)
- `to_json(indent=2)` — serialize entire design to JSON

### Module

A single `module ... endmodule` declaration. This is the main container
for all design content.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Module name |
| `parameters` | `list[Parameter]` | Parameters and localparams |
| `ports` | `list[Port]` | Port declarations (ordered as in source) |
| `nets` | `list[Net]` | Wire declarations |
| `variables` | `list[Variable]` | Register/integer/real declarations |
| `instances` | `list[Instance]` | Sub-module instantiations |
| `continuous_assigns` | `list[ContinuousAssign]` | `assign` statements |
| `always_blocks` | `list[AlwaysBlock]` | `always` blocks |
| `initial_blocks` | `list[InitialBlock]` | `initial` blocks |
| `functions` | `list[FunctionDecl]` | Function declarations |
| `tasks` | `list[TaskDecl]` | Task declarations |
| `generate_blocks` | `list[...]` | Generate for/if/case + genvar decls |
| `specify_blocks` | `list[SpecifyBlock]` | Specify timing blocks (opaque) |
| `typedefs` | `list[TypedefDecl]` | typedef/enum/struct/union declarations |
| `imports` | `list[ImportDecl]` | SV package import statements |
| `interface_instances` | `list[tuple[str, Interface]]` | Interface instances `(instance_name, interface)` |
| `attributes` | `dict[str, str \| None]` | Verilog attributes `(* ... *)` |

Lookup helpers: `get_port(name)`, `get_net(name)`, `get_variable(name)`,
`get_parameter(name)`.

Filtered views: `input_ports()`, `output_ports()`, `inout_ports()`,
`all_signals()` (nets + variables combined).

---

## Ports, Nets, Variables, Parameters

These are the declarations that define a module's interface and internal
storage.

### Port

A module port: `input [7:0] data`, `output reg valid`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Port name |
| `direction` | `PortDirection` | `INPUT`, `OUTPUT`, or `INOUT` |
| `net_type` | `str \| None` | Wire type if specified (`"wire"`, `"tri"`, etc.) |
| `data_type` | `str \| None` | Variable type if specified (`"reg"`, `"integer"`) |
| `width` | `Range \| None` | Bit range `[7:0]`, or `None` for scalar |
| `signed` | `bool` | Whether declared `signed` |
| `default_value` | `Expression \| None` | Default value (ANSI-style) |

### Net

A wire or other net-type declaration: `wire [3:0] bus`, `tri1 enable`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Net name |
| `kind` | `NetKind` | `WIRE`, `TRI`, `WAND`, `WOR`, `SUPPLY0`, `SUPPLY1`, etc. |
| `width` | `Range \| None` | Bit range or `None` for scalar |
| `signed` | `bool` | Signed declaration |
| `dimensions` | `list[Range]` | Array dimensions: `wire [7:0] mem [0:15]` |
| `initial_value` | `Expression \| None` | Net initialization |
| `drivers` | `list[Driver]` | What drives this net (populated by analysis) |
| `loads` | `list[Load]` | What reads this net (populated by analysis) |

### Variable

A register or other variable: `reg [7:0] count`, `integer i`, `real voltage`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Variable name |
| `kind` | `VariableKind` | `REG`, `INTEGER`, `REAL`, `REALTIME`, `TIME`, `EVENT` |
| `width` | `Range \| None` | Bit range |
| `signed` | `bool` | Signed declaration |
| `dimensions` | `list[Range]` | Memory array dimensions |
| `initial_value` | `Expression \| None` | Initial value |
| `drivers` | `list[Driver]` | What writes this variable (populated by analysis) |
| `loads` | `list[Load]` | What reads this variable (populated by analysis) |

### Parameter

A parameter or localparam: `parameter WIDTH = 8`, `localparam DEPTH = 2**WIDTH`.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Parameter name |
| `param_type` | `str \| None` | Type: `"integer"`, `"real"`, or `None` |
| `width` | `Range \| None` | Bit width |
| `signed` | `bool` | Signed declaration |
| `default_value` | `Expression \| None` | Default/assigned value |
| `is_local` | `bool` | `True` for `localparam` |

---

## Expressions

Expressions appear everywhere: port widths, parameter values, assignments,
conditions, sensitivity lists. They form a tree of typed nodes that can be
walked, serialized, and (eventually) evaluated.

### Expression Types

| Class | Verilog Example | Key Fields |
|-------|----------------|------------|
| `Identifier` | `clk`, `a.b.c` | `name`, `hierarchy`, `resolved` |
| `Literal` | `8'hFF`, `42`, `4'b1x0z` | `value`, `width`, `base`, `signed`, `is_x`, `is_z`, `original_text` |
| `StringLiteral` | `"hello"` | `value` |
| `BinaryOp` | `a + b`, `x == y` | `op`, `left`, `right` |
| `UnaryOp` | `~a`, `!valid`, `&bus` | `op`, `operand` |
| `TernaryOp` | `sel ? a : b` | `condition`, `true_expr`, `false_expr` |
| `Concatenation` | `{a, b, c}` | `parts` |
| `Replication` | `{4{data}}` | `count`, `value` |
| `AssignmentPattern` | `'{field: val, ...}` | `named_pairs`, `positional`, `default_value` |
| `BitSelect` | `bus[3]` | `target`, `index` |
| `RangeSelect` | `bus[7:4]` | `target`, `msb`, `lsb` |
| `PartSelect` | `data[i +: 8]` | `target`, `base`, `width`, `direction` |
| `FunctionCall` | `$clog2(W)`, `myfunc(x)` | `name`, `arguments`, `is_system` |
| `Mintypmax` | `1:2:3` | `min_val`, `typ_val`, `max_val` |

### Range

`Range` is a lightweight container (not a `VerilogNode`) used for bit
widths and array dimensions: `[msb : lsb]`. It holds two `Expression`
nodes for the bounds.

### Name Resolution

After the analysis pass, `Identifier.resolved` points to the declaration
that the name refers to — a `Port`, `Net`, `Variable`, or `Parameter`.
This transforms the flat name string into a live cross-reference.

---

## Instances & Continuous Assignments

### Instance

A module instantiation: `counter #(.WIDTH(8)) u1 (.clk(clk), .count(cnt));`

| Field | Type | Description |
|-------|------|-------------|
| `module_name` | `str` | Name of instantiated module |
| `instance_name` | `str` | Instance identifier |
| `instance_array` | `Range \| None` | Instance array range |
| `parameter_bindings` | `list[ParameterBinding]` | Parameter overrides |
| `port_connections` | `list[PortConnection]` | Port connections |
| `resolved_module` | `Module \| None` | Linked module (populated by analysis) |

Each `PortConnection` can be named (`.clk(sys_clk)`) or positional, and
tracks whether the port is unconnected (`.data()`). After analysis,
`resolved_port` links to the target module's `Port`.

Each `ParameterBinding` can be named (`.WIDTH(8)`) or positional.

### ContinuousAssign

`assign y = a & b;` — connects an expression to a net.

| Field | Type | Description |
|-------|------|-------------|
| `lhs` | `Expression` | Target (net lvalue) |
| `rhs` | `Expression` | Source expression |

---

## Behavioral: Always & Initial Blocks

### AlwaysBlock

Wraps an `always @(...) statement` and classifies its behavior.

| Field | Type | Description |
|-------|------|-------------|
| `sensitivity_list` | `list[SensitivityEdge]` | Edges: posedge/negedge/level + signal |
| `sensitivity_type` | `SensitivityType` | `COMBINATIONAL`, `SEQUENTIAL`, `LATCH`, or `UNKNOWN` |
| `body` | `Statement` | The statement tree inside |

The sensitivity classification is automatically inferred:
- **COMBINATIONAL**: `@(*)` or all level-sensitive signals
- **SEQUENTIAL**: at least one `posedge`/`negedge` edge
- **LATCH**: combinational with incomplete `if` (no `else`)
- **UNKNOWN**: could not classify

### InitialBlock

`initial begin ... end` — simulation-time initialization. Holds a `body`
statement tree.

---

## Statements

Statements are the procedural logic inside always and initial blocks. They
form a tree structure that mirrors the source nesting.

| Class | Verilog | Key Fields |
|-------|---------|------------|
| `BlockingAssign` | `a = b;` | `lhs`, `rhs` |
| `NonblockingAssign` | `q <= d;` | `lhs`, `rhs` |
| `IfStatement` | `if (...) ... else ...` | `condition`, `then_body`, `else_body` |
| `CaseStatement` | `case/casex/casez` | `case_type`, `expression`, `items` |
| `CaseItem` | `2'b01: ...` / `default:` | `values`, `body`, `is_default` |
| `ForLoop` | `for (i=0; ...)` | `init`, `condition`, `update`, `body` |
| `WhileLoop` | `while (cond) ...` | `condition`, `body` |
| `ForeverLoop` | `forever ...` | `body` |
| `RepeatLoop` | `repeat (n) ...` | `count`, `body` |
| `SeqBlock` | `begin ... end` | `name`, `statements`, `local_vars` |
| `ParBlock` | `fork ... join` | `name`, `statements`, `local_vars` |
| `WaitStatement` | `wait (cond) ...` | `condition`, `body` |
| `DisableStatement` | `disable blk;` | `target` |
| `EventTrigger` | `-> evt;` | `event` |
| `TaskEnable` | `my_task(args);` | `task_name`, `arguments` |
| `SystemTaskCall` | `$display(...)` | `task_name`, `arguments` |
| `DelayControl` | `#5 stmt;` | `delay`, `body` |
| `EventControl` | `@(posedge clk) stmt;` | `events`, `body` |

---

## Functions & Tasks

### FunctionDecl

`function [7:0] add; ... endfunction`

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Function name |
| `return_range` | `Range \| None` | Return bit width |
| `return_kind` | `str \| None` | `"integer"`, `"real"`, or `None` |
| `is_automatic` | `bool` | Automatic (re-entrant) function |
| `ports` | `list[Port]` | Function input ports |
| `body` | `Statement \| None` | Function body |

### TaskDecl

`task drive_bus; ... endtask`

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Task name |
| `is_automatic` | `bool` | Automatic (re-entrant) task |
| `ports` | `list[Port]` | Task ports (input/output/inout) |
| `body` | `Statement \| None` | Task body |

---

## Generate Constructs

Generate constructs create hardware parametrically at elaboration time.

| Class | Verilog | Key Fields |
|-------|---------|------------|
| `GenerateFor` | `for (i=0; i<N; i=i+1)` | `genvar`, `init_value`, `condition`, `update`, `body` |
| `GenerateIf` | `if (WIDTH > 8)` | `condition`, `then_body`, `else_body` |
| `GenerateCase` | `case (MODE)` | `expression`, `items` |
| `GenerateCaseItem` | `2'd1: ...` / `default:` | `values`, `is_default`, `body` |
| `GenerateBlock` | `begin : name ... end` | `name`, `items` |
| `GenvarDecl` | `genvar i, j;` | `names` |

The `body` of each generate construct is a `GenerateBlock` containing any
valid module items — nets, instances, assigns, always blocks, or further
nested generate constructs.

---

## Specify Block

Specify blocks (`specify ... endspecify`) contain timing constraints,
path delays, and timing checks. Because the specify sub-language has ~50
grammar rules and is non-synthesizable, it is stored **opaquely** — the raw
Lark parse tree and original source text are preserved for faithful
round-trip emission without full semantic analysis.

| Field | Type | Description |
|-------|------|-------------|
| `raw_tree` | `Tree` | Original Lark parse tree |
| `source_text` | `str \| None` | Verbatim source for round-trip emission |

---

## Comments

Comments are first-class data in the model. They are extracted from the
source *before* parsing (since Lark discards them), then attached to
model nodes based on proximity:

- **Leading**: a comment on the line(s) immediately before a construct
- **Trailing**: a comment on the same line, after a construct

Each `Comment` stores:
| Field | Type | Description |
|-------|------|-------------|
| `text` | `str` | Comment content (without `//` or `/* */` delimiters) |
| `loc` | `SourceLocation` | Position in the original source |
| `kind` | `str` | `"line"` for `//`, `"block"` for `/* */` |
| `position` | `str` | `"leading"`, `"trailing"`, or `"inline"` |

Comments survive the full round trip: source → model → emitted Verilog.

---

## Connectivity Analysis

After building the model, the analysis pass populates cross-references:

```python
from veriforge.analysis import analyze_design

analyze_design(design)  # mutates in-place
```

This runs four passes:

1. **Link instances** — `Instance.resolved_module` → `Module`
2. **Resolve names** — `Identifier.resolved` → `Port` / `Net` / `Variable` / `Parameter`
3. **Resolve port connections** — `PortConnection.resolved_port` → `Port`
4. **Analyze connectivity** — populate `Net.drivers`, `Net.loads`,
   `Variable.drivers`, `Variable.loads`

After analysis, you can trace signals through the hierarchy:

```python
for net in module.nets:
    for driver in net.drivers:
        print(f"  {net.name} ← driven by {driver.source}")
    for load in net.loads:
        print(f"  {net.name} → read by {load.consumer}")
```

---

## Code Generation

The emitter reconstructs Verilog source from model objects:

```python
from veriforge.codegen import emit_design, emit_module

text = emit_module(module)   # single module
text = emit_design(design)   # all modules
```

The emitter handles:
- All structural elements (ports, nets, variables, parameters)
- Instances with named/positional port and parameter connections
- Continuous assignments
- Always/initial blocks with full statement trees
- Functions and tasks
- Generate constructs (for/if/case)
- Specify blocks (verbatim from `source_text` or token-walk fallback)
- Comment placement (leading and trailing)

The emitted Verilog is semantically equivalent to the original. This is
validated by an automated round-trip test suite: parse → model → emit →
re-parse → compare models, with external validation via Icarus Verilog
(`iverilog -t null`).

---

## Serialization

Every model object supports `to_dict()` for JSON-compatible output:

```python
design.to_json(indent=2)
```

```json
{
  "type": "Design",
  "modules": [{
    "type": "Module",
    "name": "counter",
    "ports": [
      {"type": "Port", "name": "clk", "direction": "input"},
      {"type": "Port", "name": "count", "direction": "output",
       "width": {"msb": {"type": "Literal", "value": 7},
                 "lsb": {"type": "Literal", "value": 0}}}
    ],
    "always_blocks": [{
      "type": "AlwaysBlock",
      "sensitivity_type": "sequential",
      "body": { "..." }
    }]
  }]
}
```

---

## Enums

| Enum | Values |
|------|--------|
| `PortDirection` | `INPUT`, `OUTPUT`, `INOUT` |
| `NetKind` | `WIRE`, `TRI`, `WAND`, `WOR`, `TRIAND`, `TRIOR`, `TRI0`, `TRI1`, `SUPPLY0`, `SUPPLY1`, `UWIRE`, `TRIREG` |
| `VariableKind` | `REG`, `INTEGER`, `REAL`, `REALTIME`, `TIME`, `EVENT` |
| `SensitivityType` | `COMBINATIONAL`, `SEQUENTIAL`, `LATCH`, `UNKNOWN` |

---

## SystemVerilog Extensions

The model extends the Verilog 2005 core with SystemVerilog constructs. These
classes live in dedicated sub-modules of `veriforge.model`.

### TypedefDecl, EnumType, EnumMember (`model/sv_types.py`)

`typedef enum`, `typedef struct packed`, and `typedef union packed` declarations
inside a module.

| Class | Key Fields |
|-------|-----------|
| `TypedefDecl` | `name`, `enum_type: EnumType\|None`, `struct_type: StructType\|None`, `type_ref: str\|None` |
| `EnumType` | `members: list[EnumMember]`, `base_type: str\|None`, `width: Range\|None`, `signed: bool` |
| `EnumMember` | `name`, `value: Expression\|None` |
| `StructType` | `fields: list[StructField]`, `packed: bool`, `signed: bool` |
| `StructField` | `name`, `width: Range\|None`, `signed: bool`, `type_ref: str\|None` |
| `AssignmentPattern` | `named_pairs`, `positional`, `default_value` — SV `'{...}` expression |

Stored in `Module.typedefs`. Supports `typedef enum`, `typedef struct packed`,
`typedef union packed`, and type-alias (`typedef <base> name`) forms.

### Interface, Modport, ModportPort (`model/interface.py`)

SV `interface ... endinterface` declarations. Stored in `Design.interfaces`.

| Class | Key Fields |
|-------|-----------|
| `Interface` | `name`, `parameters`, `nets`, `variables`, `continuous_assigns`, `modports`, `typedefs` |
| `Modport` | `name`, `ports: list[ModportPort]` |
| `ModportPort` | `name`, `direction: PortDirection` |

Module-level interface instances (e.g. `MyIf u_if(...)`) are recorded in
`Module.interface_instances` as `(instance_name, Interface)` tuples.

### Package, ImportDecl (`model/package.py`)

SV `package ... endpackage` declarations. Stored in `Design.packages`.

| Class | Key Fields |
|-------|-----------|
| `Package` | `name`, `parameters`, `typedefs`, `functions`, `tasks` |
| `ImportDecl` | `package_name`, `item_name` (`"*"` for wildcard) |

`Module.imports` holds all `import pkg::item;` statements for the module.

---

## Grammar Coverage

The model covers IEEE 1364-2005 (Verilog 2005) plus a significant SystemVerilog
subset. The underlying Lark grammar has 345 rules and 44 terminals. Every major
Verilog construct is represented:

- Module declarations with ANSI and non-ANSI port styles
- All net types and variable types
- Full expression tree including ternary, concatenation, replication, part-select,
  and SV assignment patterns (`'{...}`)
- Complete statement hierarchy (17 statement types + `CaseItem`)
- Always block sensitivity classification
- Module instantiation with named and positional connections
- Functions and tasks (including `automatic`)
- Generate for/if/case constructs
- Specify blocks (opaque)
- Comments (line and block, leading, trailing, and inline)

SystemVerilog constructs modelled:

- `typedef enum`, `typedef struct packed`, `typedef union packed`
- `interface ... endinterface` with modport declarations
- `package ... endpackage` with `import` statements
- SV assignment patterns (`'{named: val}`, `'{positional}`, `'{default: val}`)
- `always_ff`/`always_comb`/`always_latch` keywords (parsed; keyword not preserved
  in the model field — stored as a plain `AlwaysBlock`)

**Known limitation**: Signed number base markers (`'sd`, `'sh`) are lost
during parsing because of how the Lark grammar tokenizes them. The grammar
uses anonymous terminals for the `s`/`S` prefix, which are discarded by
the parser's `keep_all_tokens=False` setting.
