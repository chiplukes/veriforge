# Grammar-Driven Verilog Fuzzer — Plan

**Status**: design phase
**Motivation**: differential fuzzing `test_differential*.py` has been our most
effective bug-finding tool, but it is tightly scoped — each file fuzzes one
shape (expressions, statements, function calls) against one fixed module
template (8 input signals → 96-bit outputs). The idea is to replace this
disjoint set of narrow templates with a **single grammar-driven generator** that
can produce arbitrary, valid Verilog modules by walking the parse grammar,
generating model objects, and cross-checking **all** engines (reference, vm,
vm-fast, compiled) **plus** Icarus on every generated module, running as a
long-duration background process.

## Design Decisions

### 1. Grammar-driven generation (not post-hoc validation)

The Lark grammar's rule graph drives generation directly. At each rule
expansion point, the generator asks "what are the valid alternatives for this
non-terminal?", picks one randomly, and recurses. This guarantees the output is
valid by construction (it is a derivation in the grammar) rather than requiring
a validation pass afterwards.

The grammar rule graph already exists in two forms:
- `gen_tree.py` (`relation_def` objects: `nname`, `clst`, `plst`, `is_supported`)
- `parse_metadata.py` (`GrammarMetadataParser`: `RuleMetadata` with full
  section/BNF/priority/synthesizable/support/children/parents)

The fuzzer will build on `parse_metadata.GrammarMetadataParser` since it already
has child/parent tracking, priority/synthesizable/support tagging, and a clean
API. `gen_tree.py`'s `relation_def` graph is a simpler subset — we'll use
`parse_metadata` as the canonical source.

### 2. Support annotations sourced from the larkfile

Currently only 5 out of ~370 rules are explicitly tagged `SUPPORT: YES`. The
others have no SUPPORT tag. This needs to be filled in so the fuzzer knows
which rules it can safely generate. The canonical source will be `verilog.lark`
itself (via `// SUPPORT: YES/NO` comments) — the fuzz grammar guide reads
directly from the parsed metadata. As a first pass, we'll mark as SUPPORT: YES
all rules whose descendant constructs are covered by the extant test suite and
simulator, consulting `notes/support_matrix.md` for each area. Rules that map
to "Parse-only", "Partial", or "Out of scope" constructs get SUPPORT: NO (or
stay unset → treated as NO by default by the fuzzer).

### 3. Standalone tool, not a CI test

The fuzzer runs as a CLI tool (`uv run -m veriforge.fuzz`) and/or a standalone
script. It is **not** a pytest test. It runs continuously, logging every
mismatch to a structured file. When a mismatch is found, the usual workflow is:
```
1. Fuzzer logs: seed, generated Verilog, stimulus, engine results
2. Human reduces the failing Verilog to a minimal repro
3. Human writes a deterministic test in tests/test_sim/
4. Human fixes the bug
5. Human adds the case to known_issues.md if it can't be fixed yet
```

### 4. Icarus comparison on every module

All four veriforge engines can agree on a wrong result for the same underlying
bug (e.g. incorrect width handling in the expression evaluator that all
engines share). Icarus is the external oracle. The fuzzer runs:
```
always:  veriforge reference → oracle
always:  veriforge vm         → compare to oracle
always:  veriforge vm-fast    → compare to oracle
always:  veriforge compiled   → compare to oracle (when available)
always:  iverilog + vvp       → compare to oracle (when iverilog is available)
```

The Icarus path requires writing the generated Verilog to a temp file,
compiling with `iverilog`, running `vvp` to produce VCD, then comparing VCD
values against the reference engine's results. This is slower than the
internal cross-engine comparison (which uses in-memory Value objects), but
the coverage benefit justifies it. If Icarus is not installed, the fuzzer
skips that comparison and logs a warning on startup.

### 5. Model-object generation (not string generation)

Unlike the current `test_differential*.py` files which generate Verilog text
strings and re-parse them, the fuzzer generates **model objects** directly
(`Module`, `Port`, `Net`, `Variable`, `Statement`, `Expression`, etc.) and:
- Feeds them directly to `Simulator(module, engine=...)` — no re-parse needed
- Emits them via `verilog_emitter.emit_module()` for Icarus comparison
- Emits them to the log file for human inspection/reproduction

This is a fundamental architectural difference from the current test generators
and is the main implementation work.

---

## Architecture

```
Grammar Metadata (parse_metadata → RuleMetadata graph)
         │
         ▼
   GrammarGuide (filters by SUPPORT/PRIORITY, provides weighted randomization)
         │
         ▼
   SignalContext (tracks available signals, widths, signedness, scopes)
         │
         ▼
   ModuleGenerator (orchestrates strategy, ports, body items)
    ├── ExpressionGenerator (produces Expression model objects)
    ├── StatementGenerator (produces Statement model objects)
    └── Strategy (module shape: feedforward, registered, multi-always, ...)
         │
         ▼
   Module (model object)
         │
    ┌────┴────┐
    ▼         ▼
 Simulator  verilog_emitter
 (all       │
  engines)  ▼
         iverilog/vvp
              │
              ▼
         VCD comparison
              │
              ▼
         Match / Mismatch log
```

---

## Phase 1: Support Tag Audit (no code — grammar update)

**Goal**: fill in `SUPPORT: YES/NO` for every rule in `verilog.lark`.

Currently 5 YES, 3 NO, 362 unset. The fuzzer needs this to know which grammar
paths are safe to generate. Rules that map to constructs the simulator can
actually evaluate get `SUPPORT: YES`. Rules that map to unsimulatable or
unsupported constructs get `SUPPORT: NO`.

**Guidelines** (consulting `notes/support_matrix.md`):
- YES: core modules/ports/nets/regs/params, continuous/blocking/nonblocking
  assigns, expressions (all operators), selects/concat/replicate, module
  instances, always/initial blocks, if/case/for/while/forever/seq blocks,
  functions, generate constructs, event control, system tasks ($display etc.)
- NO: specify blocks, gate primitives, UDPs, configs, real/realtime types,
  procedural continuous assign (force/release), task enable (caller side,
  task *declaration* may be YES), fork/join, SV verification features
- Rules that are syntactic sugar for YES rules (e.g. `always_comb` →
  `always @*`) get YES.
- Terminal-only rules (identifiers, numbers, keywords) get YES.

**Steps**:
1. Run `uv run python -m veriforge.lark_file.parse_metadata --stats` to get
   the baseline unset count.
2. For each section in `verilog.lark`, add `// SUPPORT: YES` or
   `// SUPPORT: NO` comments above each rule, consulting `support_matrix.md`
   for the area's status.
3. Re-run `--stats` to confirm 0 unset.
4. Re-generate `docs/grammar_support.md` via `--table -o docs/grammar_support.md`.

**Accept**: `uv run python -m veriforge.lark_file.parse_metadata --stats` shows
`by_support: unset = 0`. The grammar support table in docs is updated.

---

## Phase 2: Core Infrastructure (`src/veriforge/fuzz/`)

### 2.1 `fuzz/_grammar_guide.py`

Wraps `parse_metadata.GrammarMetadataParser` for the fuzzer's needs.

```python
class GrammarGuide:
    def __init__(self, lark_file=None, only_supported=True):
        ...
    def rule(self, name: str) -> RuleMetadata | None
    def children(self, name: str) -> list[str]         # direct children of a rule
    def reachable_from(self, name: str) -> set[str]    # transitive closure
    def pick_child(self, rng, name: str, weights=None) -> str  # weighted random
```

### 2.2 `fuzz/_signal_context.py`

Tracks every signal available at the current generation point. This is the
replacement for both `FIXED_SIGNALS` and `extra_signals` from the current tests.

```python
@dataclass
class Signal:
    name: str
    width: int
    signed: bool
    kind: str  # "input", "wire", "reg", "local", "output", "parameter"

class SignalContext:
    def add_input(self, name, width, signed) -> Signal
    def add_wire(self, name, width, signed) -> Signal
    def add_reg(self, name, width, signed) -> Signal
    def add_output(self, name, width, signed) -> Signal
    def add_parameter(self, name, width, signed, value) -> Signal
    def push_scope(self) -> None                # begin a new local scope
    def pop_scope(self) -> None                 # end scope, pop locals
    def add_local(self, name, width, signed) -> Signal

    def pick_readable(self, rng) -> Signal      # any signal that can be read
    def pick_writable(self, rng) -> Signal      # regs, locals, outputs
    def pick_width(self, rng, range) -> int     # biased: edge widths first

    def all_inputs(self) -> list[Signal]
    def all_outputs(self) -> list[Signal]
    def all_wires(self) -> list[Signal]
    def all_regs(self) -> list[Signal]
    def write_ports(self, mod: Module) -> None  # convert to model Port objects
    def write_nets(self, mod: Module) -> None   # convert to model Net objects
    def write_vars(self, mod: Module) -> None   # convert to model Variable objects
```

### 2.3 `fuzz/_expression_gen.py`

Port of `test_differential.py`'s `_gen_expr()` — same operators, same depth
control, same callables support — but producing `Expression` model objects
instead of strings.

```python
class ExpressionGenerator:
    def __init__(self, guide: GrammarGuide, ctx: SignalContext):
        ...
    def leaf(self, rng) -> Expression        # Identifier, BitSelect, RangeSelect
    def literal(self, rng, width=None) -> Literal
    def expr(self, rng, depth, callables=()) -> Expression
    def binary(self, rng, depth, callables) -> Expression
    def unary(self, rng, depth, callables) -> Expression
    def reduction(self, rng, depth, callables) -> Expression
    def ternary(self, rng, depth, callables) -> Expression
    def concat(self, rng, depth, callables) -> Expression
    def replicate(self, rng, depth, callables) -> Expression
    def cast(self, rng, depth, callables) -> Expression
    def call(self, rng, depth, callables) -> Expression  # FunctionCall
```

Key differences from the string-based `_gen_expr`:
- Returns typed `Expression` objects with proper `BinaryOp(op=..., left=..., right=...)` construction
- Uses `SignalContext.pick_readable()` instead of `FIXED_SIGNALS`
- Literals get `Literal(value=N, width=W, base='b'|'h'|'d')`
- No div-by-zero string hack — instead generate non-zero literals for `/`, `%` RHS

### 2.4 `fuzz/_statement_gen.py`

Port of `test_differential_statements.py`'s `_gen_stmt()` to produce
`Statement` model objects.

```python
class StatementGenerator:
    def __init__(self, ctx: SignalContext, expr_gen: ExpressionGenerator):
        ...
    def stmt(self, rng, depth, target_signal: Signal) -> Statement
    def leaf_assignment(self, rng, target) -> Statement
    def if_chain(self, rng, depth, target) -> IfStatement
    def case_stmt(self, rng, depth, target) -> CaseStatement
    def for_loop(self, rng, depth, target) -> ForLoop
    def while_loop(self, rng, depth, target) -> WhileLoop
    def seq_block(self, rng, depth, target) -> SeqBlock
```

### 2.5 `fuzz/_module_gen.py`

Top-level module builder. Given a strategy and complexity hint, produces a
complete `Module` model object.

```python
class ModuleGenerator:
    def __init__(self, guide: GrammarGuide, rng: random.Random):
        ...
    def generate(self, strategy: Strategy, complexity: float) -> Module:
        """Generate a module using the given strategy.

        complexity is a 0.0-1.0 hint controlling port count, expression depth,
        statement count, etc.
        """
```

### 2.6 `fuzz/_strategies.py`

```python
class Strategy(enum.Enum):
    FEEDFORWARD = "feedforward"        # inputs → continuous assigns → outputs
    REGISTERED = "registered"          # inputs → regs → combinational → outputs
    MULTI_ALWAYS = "multi_always"      # several always @* blocks
    CLOCKED_SEQUENTIAL = "clocked_seq" # clock + always @(posedge clk)
    WITH_FUNCTIONS = "with_functions"  # functions called from expressions
    NESTED_BLOCKS = "nested_blocks"    # begin/end with local vars
    GENERATE = "generate"              # generate for/if constructs (limited)
    MIXED = "mixed"                    # random mix of the above
```

Each strategy is a generator function or class that builds module contents
following a specific structural pattern, parametrized by complexity.

---

## Phase 3: Fuzz Runner

### 3.1 `fuzz/__main__.py`

The entry point. Usage:

```
uv run -m veriforge.fuzz [--seed N] [--timeout HOURS] [--output DIR]
```

### 3.2 `fuzz/_runner.py`

```python
@dataclass
class FuzzLog:
    """A single fuzz result, stored to disk."""
    seed: int
    module_name: str
    verilog_source: str
    strategy: str
    stimulus: list[dict[str, Value]]
    results: dict[str, dict[str, Value]]  # engine → {signal → Value}
    mismatches: list[MismatchRecord]

class FuzzRunner:
    def __init__(self, output_dir: Path, seed: int = 0):
        ...
    def run_forever(self, max_hours: float | None = None):
        """Main loop."""
        while not should_stop:
            seed += 1
            self._run_one(seed)
    def _run_one(self, seed: int) -> FuzzLog:
        ...
```

Main loop per seed:
```
1. Pick random strategy + complexity
2. Generate Module model object
3. Pick random stimulus vectors:
   - Ports → random bit patterns, some with x/z contamination
   - Clock (if used) → toggle pattern
4. For each available engine:
   - Simulate with stimulus
   - Record all output signal values at each time step
5. Compare all non-reference engines to reference
6. Emit Verilog via verilog_emitter
7. Run iverilog/vvp, compare VCD to reference results
8. If any mismatch → log full repro to output dir
9. If match → log stats only (count), periodic stats dump
```

### 3.3 Stimulus generation

Unlike the current tests which drive fixed input signals, the fuzzer must
generate stimulus for whatever ports the generated module happens to have:

- **Combinational inputs**: random values per timestep, some with x/z
- **Clock**: square wave with randomized period
- **Reset**: optional reset pulse at start
- **Parameterized widths**: stimulus values match port bit widths

### 3.4 Icarus integration

```
1. Write generated Verilog to temp file
2. Build testbench wrapper:
   - Instantiate the generated module
   - Drive inputs with same stimulus as veriforge reference run
   - Dump VCD
3. iverilog -o out.vvp testbench.v module.v
4. vvp out.vvp → output.vcd
5. Parse VCD with vcd_reader
6. Compare signal values at each timestep to reference engine results
7. Report any differences
```

The testbench wrapper is generated by the fuzzer itself — it knows the port
list and stimulus from step 2, so it can emit a trivial Verilog testbench that
instantiates the DUT and drives the recorded stimulus values.

---

## Phase 4: Logging and Repro

### Log format

Each mismatch creates a directory under the output path:

```
fuzz_output/
├── stats.json           # running totals: modules, mismatches by engine
├── mismatch_0042/        # seed 42 had a mismatch
│   ├── info.json         # seed, strategy, complexity, engine, signals
│   ├── module.v          # generated Verilog
│   ├── stimulus.json     # recorded stimulus
│   ├── oracle.json       # reference engine results
│   └── engine_vm.json    # VM engine results (mismatched)
```

### Repro command

```
uv run -m veriforge.fuzz --repro fuzz_output/mismatch_0042
```
Re-runs just that seed with detailed output.

---

## Phase 5: Integration Notes

### Existing test files

Do not modify `test_differential*.py`. They remain as fast, focused CI tests.
The fuzzer is a separate tool with a different purpose — long-running background
exploration vs. bounded CI regression.

### Model object reuse

The fuzzer reuses existing model classes (`model.expressions`, `model.statements`,
`model.design`, etc.) and the emitter (`codegen.verilog_emitter`). No new model
classes are needed.

### Grammar metadata

The fuzzer imports `parse_metadata.GrammarMetadataParser` at runtime. No
pre-generation step needed — it parses the `.lark` file on startup (fast,
<100ms for 2800 lines). This means any `SUPPORT:` tag changes in the grammar
are immediately reflected.

### Known engine gaps

The fuzzer must be aware of known gaps (like compiled engine's >64-bit function
call limitation) and either skip those constructs when using that engine or
mark expected mismatches as known. This can be done via a `known_gaps.py`
config file that maps `(engine, construct)` → expected behavior.

---

## Implementation Order

| Phase | Effort | Dependencies |
|-------|--------|-------------|
| Phase 1: Support tag audit | M | none |
| Phase 2.1: GrammarGuide | S | Phase 1 |
| Phase 2.2: SignalContext | M | none |
| Phase 2.3: ExpressionGenerator | M | 2.2 |
| Phase 2.4: StatementGenerator | L | 2.2, 2.3 |
| Phase 2.5: ModuleGenerator | M | 2.2-2.4 |
| Phase 2.6: Strategies | M | 2.5 |
| Phase 3: Runner + stimulus + Icarus | L | Phase 2 |
| Phase 4: Logging + repro | S | Phase 3 |
| Phase 5: Known gaps config | S | Phase 3 |

Total: ~2-3 weeks of focused work. The largest single item is the
StatementGenerator (2.4) because it must handle scope, local variables, and
loop bounds correctly in model-object form. The ExpressionGenerator (2.3) is
moderate because it maps cleanly from the existing string-based `_gen_expr`.

---

## Open Questions

1. **Should generated modules always be self-contained?** Yes — no includes,
   no external module references. This avoids Icarus library-path issues.
   Exception: the fuzzer COULD instantiate previously-generated modules to
   build multi-module designs, but start simple.

2. **How to prevent infinite loops / hangs in simulation?** The fuzzer must
   limit loop iteration counts (use small static bounds, not unbounded while(1)).
   The `#0` delay hack used in the current statement tests provides a safety net
   but isn't ideal. Better: generate loops with compile-time constant bounds.

3. **Should the fuzzer generate SystemVerilog constructs?** Not initially.
   Stick to IEEE 1364-2001/2005 Verilog. SystemVerilog constructs (interfaces,
   packages, enums, structs, always_comb/always_ff) can be added later once
   the core is proven.

4. **Where does the SUPPORT metadata live long-term?** In `verilog.lark`. The
   fuzzer reads from `parse_metadata` at startup. The `docs/grammar_support.md`
   table is a generated view — it should be regenerated after any grammar
   metadata changes (via `uv run python -m veriforge.lark_file.parse_metadata
   --table -o docs/grammar_support.md`).
