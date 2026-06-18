# Cython Compiled Cache (.cycache)

See also: [pcache.md](../pcache.md) for the parse-time pickle cache — a separate, unrelated cache.

## Problem

The compiled simulation engine converts Verilog modules into Cython `.pyx`
source, then compiles that through C into a native `.pyd` (Windows) or `.so`
(Linux) extension. This pipeline has two expensive steps:

1. **Codegen** — traversing the Verilog model to emit `.pyx` source (~seconds)
2. **C compilation** — Cython → C → native binary (~seconds)

During iterative development both steps run on every simulator startup,
even when the source files haven't changed.

## Solution

Two cache layers in `.cycache/` avoid redundant work:

### Layer 1: Cython Compilation Cache

Caches the `.pyx → .pyd` compilation step.

**Location**: `.cycache/<keyed_name>/<keyed_name>.pyd`

**Cache key** = SHA-256 of:
- `.pyx` source code
- Cython version
- Platform tag (`sys.platform-machine-pyX.Y`)
- `_CACHE_VERSION` (bumped when the codegen framework changes)

**Keyed module name** = `vtc_<module-hash>_<key>` where:
- `module-hash` is an 8-hex SHA-256 prefix of the requested module name
- `key` is the 16-hex content/environment cache key above

The full module name is intentionally not embedded in the on-disk path. This
keeps Windows linker/object paths short enough for deeply nested cache roots
and heavily-parameterized compiled test modules.

On hit, the existing `.pyd` is loaded directly — no C compilation needed.
On miss, the full Cython build runs and saves the `.pyd` for next time.

**Implementation**: `CythonCompiler.compile_pyx()` in
`src/veriforge/sim/compiled/compiler.py`.

### Layer 2: Codegen Cache (Elaboration Metadata)

Caches the codegen step — generating `.pyx` from the Verilog model.

**Location**: `.cycache/_elab_<hash>.json`

**Elab hash** = SHA-256 of:
- Module name
- All source file contents (sorted)
- All codegen infrastructure files (hashed together via `_codegen_infra_hash()`):
  `codegen.py`, `_expr_emitter.py`, `_gen_sections.py`, `_process_compiler.py`,
  `_stmt_emitters.py`, `_wide_emitter.py`, `_codegen_utils.py`, and `elaborate.py`
- `_CACHE_VERSION`
- Cython version
- Platform tag

The JSON sidecar stores:
- `keyed_name` — the Layer 1 cache key to find the `.pyd`
- `signal_map` — name → signal-ID mapping
- `sig_widths` — signal widths by ID
- `mem_map` — name → memory-ID mapping
- `mem_info` — (elem_width, depth) per memory ID
- `n_sigs`, `n_mems` — signal/memory counts

On hit, the `.pyd` is loaded by keyed name and the codegen metadata is
restored from JSON — `CythonCodegen.generate()` is skipped entirely.

**Implementation**: `CompiledScheduler.elaborate()` in
`src/veriforge/sim/compiled/compiled_scheduler.py`.

## Cache Directory Structure

```
.cycache/
├── _elab_<hash>.json                     # Layer 2: codegen metadata
├── <keyed_name>.lock                     # per-module file lock (filelock)
├── <keyed_name>/                         # Layer 1: compiled extension
│   ├── <keyed_name>.pyx                  # .pyx source (for debugging)
│   ├── <keyed_name>.c                    # Cython-generated C source
│   ├── setup.py                          # build script
│   ├── t/                                # C compiler temp artifacts (--build-temp)
│   ├── l/                                # build lib temp (--build-lib)
│   └── <keyed_name>.pyd                  # compiled extension
└── ...
```

## Cache Hit/Miss Logic

```
elaborate(module, source_files):
  1. Compute elab_hash from source files + codegen.py + versions
  2. Check for _elab_<hash>.json  (Layer 2)
     HIT  → load .pyd by keyed_name (Layer 1), restore metadata → done
     MISS → continue to step 3
  3. Run CythonCodegen.generate(module) → .pyx source
  4. Compute pyx cache key from .pyx source + versions
  5. Check for <keyed_name>.pyd  (Layer 1)
     HIT  → load existing .pyd → done
     MISS → compile .pyx → .pyd, save to cache
  6. Save _elab_<hash>.json with keyed_name + metadata  (Layer 2)
```

## Performance

| Scenario | Codegen | C Compile | Total |
|----------|---------|-----------|-------|
| First run (cold) | ~seconds | ~seconds | ~5-15s |
| Source unchanged (both layers hit) | skipped | skipped | ~10ms |
| Source changed, same codegen output | ~seconds | skipped | ~2-5s |
| codegen.py changed | ~seconds | ~seconds | ~5-15s |

## Activation

The codegen cache (Layer 2) activates automatically when `source_files` are
available — `Simulator` passes `design.source_files` to `elaborate()` for
the compiled engine. Without source files (e.g. DSL-built modules), only
the Layer 1 compilation cache applies.

The compilation cache (Layer 1) is always active unless disabled via:

```bash
VERILOG_TOOLS_NO_COMPILE_CACHE=1
```

## Cache Location Override

The cache directory can be overridden via environment variable:

```bash
VERILOG_TOOLS_COMPILE_CACHE=/path/to/cache
```

Or programmatically:

```python
from veriforge.sim.compiled.compiler import CythonCompiler
compiler = CythonCompiler(cache_dir="/path/to/cache")
```

## Cache Cleanup

- **Layer 1**: Stale cached extensions that fail to load are automatically
  removed and recompiled.
- **Layer 2**: Stale `_elab_*.json` files whose `.pyd` directory no longer
  exists are automatically cleaned up.
- **Manual**: `CythonCompiler.clear_cache()` removes all cached extensions.
  On Windows, in-use `.pyd` files cannot be removed until the process exits.

## Regression Test Behaviour

The full regression (`uv run pytest --run-slow -n auto`) runs thousands of
parametrized compiled-engine tests, each producing a unique DUT. Without
mitigation this fills `.cycache` with tens of GB of generated C source, object
files, and `.pyd` files.

### Per-test cache isolation (automatic)

`tests/conftest.py` contains an autouse fixture that redirects
`VERILOG_TOOLS_COMPILE_CACHE` to a per-test `tmp_path`-based directory for
every test in the suite. After each test the fixture calls `clear_cache()` to
delete the build artefacts (generated `.c` files, `build/` directories,
`setup.py`). These are the bulk of the disk usage.

On Windows, loaded `.pyd` files cannot be deleted while the process holds them.
Those files remain in pytest's temp tree (`%TEMP%/pytest-of-.../`) and are
cleaned up when pytest runs next time (pytest retains the last few runs). The
project `.cycache` directory never grows.

The fixture is xdist-safe: each worker is a separate process with its own
`tmp_path`, so there is no cross-worker contention.

### Developer override

If `VERILOG_TOOLS_COMPILE_CACHE` is already set in your shell environment the
autouse fixture is a no-op — your persistent cache is used as-is. Set it in
your shell profile to preserve cross-run caching during iterative development:

```bash
# bash / zsh
export VERILOG_TOOLS_COMPILE_CACHE=$HOME/.cache/veriforge/cycache

# PowerShell
$env:VERILOG_TOOLS_COMPILE_CACHE = "$HOME\.cache\veriforge\cycache"
```

### Session-level wipe

To clear the cache entirely before a run (e.g. after a codegen change):

```bash
uv run pytest --clear-cython-cache ...
```

This flag is processed in `pytest_sessionstart` and deletes the entire cache
directory before any tests collect. It is independent of the per-test
isolation fixture.
