# veri-quickbench Integration Review

## Purpose

Review whether ideas from `veri-quickbench` should be adopted into this project,
with emphasis on practical support for AXI-Lite and AXI-Stream testbench
development.

This note replaces an older draft that was directionally useful but too
optimistic about compiled-engine support and too eager to jump into a DSL-heavy
implementation.

## Current Recommendation

Yes, this is worth prioritizing.

Pausing further `examples/pulp/...` import work in order to study and extract the
useful parts of `veri-quickbench` makes sense. The next bottleneck is not finding
more AXI examples. The real bottleneck is lacking reusable AXI-Lite and
AXI-Stream bus functional models and helper APIs, which would make future PULP
imports much cheaper and more deterministic.

The important change from the older draft is scope:

- Focus first on **pure-Python AXI-Stream and AXI-Lite helpers** on top of the
  existing Simulator API.
- Treat **full AXI4 BFMs** as a later phase.
- Treat **compiled-engine acceleration** as a future optimization, not the main
  justification for the feature.
- Do not make DSL protocol engines the first milestone.

## Why This Matters

The recent PULP AXI work showed the same problem repeatedly:

- imported RTL may preserve realistic interfaces,
- but local executable validation still requires hand-written stepping logic,
- each example needs custom read/write helpers,
- and there is no shared AXI or AXI-Stream transaction layer yet.

That makes every new AXI example more expensive than it should be.

`veri-quickbench` is relevant because it already solved a nearby problem:

- represent AXI-Stream transactions as frames,
- provide source/sink endpoint behavior,
- provide AXI transaction helpers,
- infer interfaces from port naming patterns,
- scaffold testbench code automatically.

Those ideas map well onto this repository even though the implementation model is
different.

## Source Project Summary

**Repository**: https://github.com/chiplukes/veri-quickbench

**Main useful subsystems**:

1. `tb_endpoints`
2. `tb_creator`

The most reusable ideas are:

- `AXIStreamFrame`
- AXI-Stream source/sink endpoint behavior
- AXI memory-model ideas
- interface detection from port names
- testbench scaffolding patterns

The least reusable parts are:

- MyHDL coroutine execution model
- Icarus/MyHDL cosimulation flow
- questionary-based interactive CLI flow

## What Already Exists Here

This repository already has a stronger foundation than `veri-quickbench` in a
few important areas.

### Strengths Already Present

1. A full Verilog parser and model layer instead of a lightweight parser.
2. Existing DSL interface definitions for `axi_stream()` and `axi4_lite()`.
3. Three simulator engines: `reference`, `vm`, and `compiled`.
4. Existing testbench support with `Simulator`, `Clock`, `run_step()`, and
   `drive()` / `read()`.
5. Existing generated or imported AXI examples that can serve as endpoint
   validation targets.

### Limits at Time of Research (pre-implementation)

1. The compiled engine truncates signals wider than 64 bits (still true).
2. `batch_run()` exists but is compiled-engine only (still true).
3. No async coroutine testbench runner (still deferred).
4. Full AXI4 interface support not in DSL library (still deferred).
5. No shared endpoint library for AXI-Stream, AXI-Lite, or AXI — **now resolved**
   (see Current Branch Status below).

## Scope Decisions Made During Implementation

The first pass focused on pure-Python AXI-Stream and AXI-Lite helpers against the
existing Simulator API, deferring full AXI4 BFMs, DSL protocol engines, and
compiled-engine acceleration. This proved correct: the Python endpoint layer
provided immediate value without needing new simulator features.

## Implementation Summary

All five first-wave milestones were completed. See **Current Branch Status** below
for the specific files and what remains deferred.

## Current Branch Status

The branch has moved beyond planning. Most of the first-wave infrastructure is
implemented and tested, but some of the originally motivating follow-through
work is still intentionally unfinished.

### Implemented on this branch

- `AXIStreamFrame` exists under `src/veriforge/sim/endpoints/frame.py`
- pure-Python AXI-Stream source/sink helpers exist under
  `src/veriforge/sim/endpoints/`
- a pure-Python AXI-Lite master helper exists under
  `src/veriforge/sim/endpoints/axi_lite_master.py`
- AXIS and AXI-Lite interface detection exists under
  `src/veriforge/sim/endpoints/detect.py`
- Python testbench skeleton generation exists under
  `src/veriforge/dsl/testbench.py`
- parsed-design entry points for Python skeleton generation exist under
  `src/veriforge/project.py`
- the public CLI now exposes parsing, DSL export, and Python testbench
  generation workflows, including machine-readable JSON responses
- at least one PULP-driven AXI-Lite regression now reuses the shared helper
  for real read and write transactions instead of relying only on ad hoc
  stepping
- the PULP AXI test harness can optionally dump VCD waveforms for inspection

### Still not done on this branch

- PULP example import work is still paused; the new helpers have not yet been
  applied back across the `examples/pulp/...` tree
- broader reuse across additional PULP AXI or AXI-Stream runners is not yet
  complete
- full AXI4 endpoint libraries and BFMs are still deferred
- DSL protocol engines are still deferred
- async coroutine-style testbench support is still deferred
- complex random traffic generation and protocol-violation injection are still
  out of scope
- wide-bus compiled-engine acceleration remains unresolved and is still a
  limitation for future AXI work
- clock/reset association heuristics for generated Python testbenches are still
  basic rather than production-grade for multi-domain designs
- there is not yet a dedicated end-to-end "quickbench-style" example showing
  the full intended workflow on top of the new infrastructure

### Interpretation

This means the branch already provides reusable AXI-Lite and AXI-Stream test
infrastructure, but it has not yet fully closed the loop back into the original
PULP-driven motivation. The remaining work is mostly integration, broader reuse,
and deferred higher-scope features rather than missing first-pass primitives.

## Bottom Line

The endpoint library delivered real value: AXI-Lite and AXI-Stream testbenches
are now significantly easier to write. The remaining work is integration across
the PULP example tree, plus deferred higher-scope features (full AXI4 BFMs,
DSL protocol engines, async coroutine support).
