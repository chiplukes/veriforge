# Parse Cache (.pcache)

See also: [cycache.md](simulation/cycache.md) for the compiled-engine Cython cache — a separate, unrelated cache.

## Problem

Parsing Verilog/SystemVerilog files with the Lark Earley parser is slow — the
Ibex design (19 modules, 1 package, 22 files) takes ~60s to parse. During
iterative development on simulation or analysis, re-parsing unchanged files
on every run is wasteful.

## Solution

Cache per-file `Design` objects as pickle files in a directory of your choice.
On subsequent runs, unchanged files are loaded from cache (~1ms each) instead
of re-parsed (~3s each).

Caching is **opt-in**: it is disabled by default and must be enabled explicitly
via the `cache_dir` parameter.

## Cache Location

```
<your_cache_dir>/                         # any directory you specify
├── <parser_hash>_<path_hash>_<source_hash>_<stem>.pickle
└── ...
```

There is no default cache directory. You must pass `cache_dir` explicitly to
`parse_files()` or `parse_directory()`. A common convention is `.pcache/` in
the project root, but the location is entirely up to the caller.

## Cache Key Design

Each cache file is keyed by three hashes:

### 1. Parser Hash (`parser_hash[:16]`)

SHA-256 of the concatenated contents of all parser infrastructure files:

```
lark_file/verilog.lark        # grammar rules
verilog_parser.py             # parser wrapper
preprocessor.py               # macro expansion
transforms/tree_to_model.py   # tree → model transform
model/*.py                    # all model classes
```

This hash changes automatically when any parser/model code is modified during
development — no manual version bumping required.

### 2. Path Hash (`path_hash[:16]`)

SHA-256 of the **normalized absolute file path**. This disambiguates files that
share the same stem and content but live in different directories (e.g.,
`rtl/top.v` vs `tests/top.v`).

### 3. Source Hash (`source_hash[:16]`)

SHA-256 of the **raw file bytes** (before preprocessing). Preprocessing happens
separately at parse time; the cache key uses the on-disk content so that changes
to define values or include paths are handled by the caller passing different
`defines` / `include_paths` arguments (which influence the parsed output, not
the cache key).

### Cache Filename

```
{parser_hash[:16]}_{path_hash[:16]}_{source_hash[:16]}_{stem}.pickle
```

Example: `a3f8b2c1d4e56789_7c1d4ef890123456_d9e8f7a6b5c4d3e2_ibex_core.pickle`

## Cache Hit/Miss Logic

```
For each source file:
  1. Read raw file bytes
  2. Compute source_hash = SHA-256(raw_bytes)[:16]
  3. Compute path_hash   = SHA-256(normalized_abs_path)[:16]
  4. Compute parser_hash = SHA-256(parser infrastructure files)[:16]  (cached in-process)
  5. cache_file = cache_dir / {parser_hash}_{path_hash}_{source_hash}_{stem}.pickle
  6. If cache_file exists → load Design from pickle (CACHE HIT)
  7. Else → parse file, save Design to pickle (CACHE MISS)

After all files processed:
  8. Merge all per-file Designs
  9. Run link_instances() on merged Design
```

## Pickle Safety

The `Design` object graph has circular `parent` references (every Port, Net,
etc. points back to its Module). These are handled by:

1. **Before save**: Set all `.parent` refs to `None` and clear `._parse_tree`
2. **Pickle**: Serialize the stripped Design
3. **After load**: Rebuild `.parent` back-references

The `link_instances()` cross-references are NOT cached — they depend on
cross-file state and are recomputed after merging (it's fast, just dict
lookups).

## Performance Expectations

| Scenario | Time |
|----------|------|
| First run (cold cache) | ~60s (same as without cache) + ~50ms pickle writes |
| All cached (no changes) | ~1-2s (read 19 pickles + merge + link) |
| One file changed | ~4s (re-parse 1 + read 18 from cache) |
| Parser code changed | ~60s (full re-parse, all caches invalid) |

## API

```python
from veriforge.project import parse_files, parse_directory

# Caching disabled by default — must opt in:
design = parse_files(files, preprocess=True, defines=DEFINES)            # no cache

# Enable cache in a specific directory:
design = parse_files(files, preprocess=True, cache_dir=".pcache/")

# parse_directory also supports cache_dir:
design = parse_directory("rtl/", cache_dir=".pcache/")

# Disable explicitly (same as default):
design = parse_files(files, cache_dir=None)
```

## Cache Cleanup

On each run (when `cache_dir` is given), any `.pickle` file in the cache
directory whose name does not start with the current 16-hex `parser_hash`
prefix is deleted automatically. This removes stale entries from old parser
versions without manual intervention.
