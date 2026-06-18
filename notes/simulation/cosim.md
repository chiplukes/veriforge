# Cross-Simulator Validation (Cosim)

## Overview

`IcarusCosim` in `veriforge.sim.cosim` compares our simulator engines
against [Icarus Verilog](https://steveicarus.github.io/iverilog/) — a
well-tested open-source Verilog simulator — by capturing and comparing VCD
output at every time step.  This gives a functional ground-truth check that
does not depend on any part of our own codebase.

Two comparison modes are supported:

| Mode | API | Use case |
|------|-----|----------|
| VCD comparison | `run()` / `run_all_engines()` | Whole-simulation diff |
| Cycle-by-cycle | `run_cycle_by_cycle()` | Find first divergent cycle |

## Installation

Icarus Verilog is **not** installed automatically.  Install it separately:

```bash
# macOS (Homebrew)
brew install icarus-verilog

# Ubuntu / Debian
sudo apt-get install iverilog

# Windows: download installer from https://bleyer.org/icarus/
# Ensure iverilog.exe and vvp.exe are on PATH, or install to C:\iverilog\bin
```

The tool discovery order for each executable (`iverilog`, `vvp`):

1. Environment variable `IVERILOG` / `VVP`
2. `shutil.which()` (searches `PATH`)
3. Windows-specific fallback directories (`C:\iverilog\bin`, etc.)

Use `find_icarus()` to probe at runtime:

```python
from veriforge.sim.cosim import find_icarus

if find_icarus() is None:
    print("Icarus Verilog not found — install it and re-run.")
```

## Quick Start

### Single-file design

```python
from veriforge.sim.cosim import IcarusCosim

verilog_src = """
module test;
    reg clk = 0;
    reg [7:0] counter = 0;
    always #5 clk = ~clk;
    always @(posedge clk) counter <= counter + 1;
    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test);
        #100 $finish;
    end
endmodule
"""

cosim = IcarusCosim(verilog_src=verilog_src)
result = cosim.run(engine="reference", max_time=100)
assert not result.diffs, "\n".join(result.diffs)
```

### Multi-file project

```python
cosim = IcarusCosim(
    files=["rtl/top.v", "rtl/alu.v", "sim/testbench.v"],
    top_module="testbench",
    defines={"SIM": "1"},
    work_dir="sim/",          # cwd for $readmemh
)
result = cosim.run(engine="vm", max_time=5000)
```

### All engines at once

```python
results = cosim.run_all_engines(max_time=1000)
# Returns dict[engine_name, CosimResult]
diffs = [d for r in results.values() for d in r.diffs]
if diffs:
    for d in diffs:
        print(d)
```

`run_all_engines()` runs Icarus **once** and compares each engine against
that single VCD — efficient for running all four engines without redundant
Icarus invocations.  Engines unavailable on the current machine are silently
skipped.

### Cycle-by-cycle debug

```python
mismatch = cosim.run_cycle_by_cycle(
    engine="reference",
    max_cycles=300,
    reset_cycles=10,
    clock_name="clk",
)
if mismatch:
    print(f"First mismatch at cycle {mismatch.cycle}:")
    for sig, ic_val, ref_val in mismatch.signals:
        print(f"  {sig}: icarus={ic_val}  ours={ref_val}")
```

This is useful when `run()` reports differences but you need to know *which
cycle* and *which signals* first diverge, making it easy to trace backward
to the root cause.

## API Reference

### `IcarusCosim`

```python
IcarusCosim(
    *,
    verilog_src: str | None = None,   # single-source mode
    files: list[str] | None = None,   # multi-file mode
    top_module: str | None = None,    # required for multi-file
    defines: dict[str, str] | None = None,
    work_dir: str | None = None,      # cwd for $readmemh etc.
    iverilog_path: str | None = None, # override auto-detection
    vvp_path: str | None = None,
    iverilog_flags: list[str] | None = None,
)
```

Methods:

| Method | Returns | Description |
|--------|---------|-------------|
| `run(engine, max_time, signals, ignore_signals, verbose)` | `CosimResult` | VCD comparison against one engine |
| `run_all_engines(engines, max_time, signals, ignore_signals)` | `dict[str, CosimResult]` | VCD comparison against all engines |
| `run_cycle_by_cycle(engine, max_cycles, reset_cycles, clock_name, ...)` | `CycleMismatch \| None` | Cycle-precise comparison |
| `run_icarus()` | `str` | Run Icarus and return raw VCD text |

### `available_engines()`

```python
from veriforge.sim.cosim import available_engines

engines = available_engines()
# → ["reference", "vm", "vm-fast"]         (no Cython toolchain)
# → ["reference", "vm", "vm-fast", "compiled"]  (Cython available)
```

Returns the engine names that can be used on the current machine.  Probes
the Cython toolchain with a small test compilation.

### `CosimResult`

```python
@dataclass
class CosimResult:
    diffs: list[str]            # human-readable diff lines (empty = match)
    icarus_signal_count: int
    ref_signal_count: int
    compared_signal_count: int
    icarus_vcd: str             # raw Icarus VCD text (for further analysis)
```

### `CycleMismatch`

```python
@dataclass
class CycleMismatch:
    cycle: int
    signals: list[tuple[str, str, str]]   # (name, icarus_val, our_val)
```

### `record_vcd(sim, max_time)`

Low-level helper: run an already-constructed `Simulator` and capture VCD as
a string.  Used internally by all comparison methods.

```python
from veriforge.sim.cosim import record_vcd
from veriforge.sim.testbench import Simulator

sim = Simulator(module, engine="vm")
vcd_text = record_vcd(sim, max_time=500)
```

## Using Cosim in Tests

The validation test suite (`tests/test_validation/test_iverilog_validation.py`)
uses a `_validate()` helper that runs all available engines:

```python
from veriforge.sim.cosim import IcarusCosim, available_engines, find_icarus

pytestmark = pytest.mark.skipif(
    find_icarus("iverilog") is None or find_icarus("vvp") is None,
    reason="Icarus Verilog (iverilog/vvp) not found",
)
_ENGINES = available_engines()


def _validate(verilog_src, *, max_time=1000, signals=None, ignore_signals=None) -> list[str]:
    cosim = IcarusCosim(verilog_src=verilog_src)
    results = cosim.run_all_engines(
        max_time=max_time, signals=signals, ignore_signals=ignore_signals
    )
    return [diff for result in results.values() for diff in result.diffs]
```

Then each test case is simply:

```python
def test_counter():
    diffs = _validate("""
        module test;
            reg [7:0] count = 0;
            reg clk = 0;
            always #5 clk = ~clk;
            always @(posedge clk) count <= count + 1;
            initial begin $dumpvars(0, test); #100 $finish; end
        endmodule
    """)
    assert not diffs, "\n".join(diffs)
```

The test is marked `skipif` so it is skipped cleanly on machines without
Icarus instead of failing.  The `pytestmark` skip applies to the entire
module, so you don't need to repeat it on every test function.

### Pytest markers

Tests in `test_iverilog_validation.py` use only a module-level
`pytestmark = pytest.mark.skipif(find_icarus(...) is None, ...)`.
There is no separate `requires_iverilog` marker in `conftest.py`.
See `notes/test_taxonomy.md` for the full marker list.

## Generated Testbench Integration

When you generate a Python testbench skeleton, pass `cosim=True` along with
`dut_source_path` to include a `validate_with_icarus()` helper:

```python
from veriforge.scaffold import generate_python_testbench_skeleton
from veriforge.project import parse_file

design = parse_file("my_dut.v")
skeleton = generate_python_testbench_skeleton(
    design,
    dut_source_path="my_dut.v",
    cosim=True,              # required — omitting this skips the cosim block
)
```

The generated skeleton includes a `validate_with_icarus()` function at the
bottom that:

1. Calls `generate_testbench()` to produce a Verilog testbench wrapper
2. Emits it as a string with `emit_module()`
3. Writes it to a `TemporaryDirectory`
4. Constructs `IcarusCosim(files=[..., tb_path], top_module=...)`
5. Calls `run_all_engines()` and raises `AssertionError` on any difference

Example generated function:

```python
def validate_with_icarus(module, *, max_time: int = 1000) -> None:
    """Validate all engines against Icarus Verilog.

    Generates a Verilog wrapper testbench and compares each available
    simulator engine's VCD output against the Icarus reference.
    Requires iverilog and vvp on PATH.
    """
    import tempfile
    from pathlib import Path
    from veriforge.codegen import emit_module
    from veriforge.dsl.testbench import generate_testbench
    from veriforge.sim.cosim import IcarusCosim, find_icarus

    if find_icarus() is None:
        print("Skipping cosim validation: iverilog not found on PATH.")
        return

    tb_model = generate_testbench(module, timeout=max_time)
    tb_verilog = emit_module(tb_model)

    with tempfile.TemporaryDirectory() as tmpdir:
        tb_path = str(Path(tmpdir) / (tb_model.name + ".v"))
        Path(tb_path).write_text(tb_verilog, encoding="utf-8")
        cosim = IcarusCosim(
            files=[_DUT_PATH, tb_path],
            top_module=tb_model.name,
        )
        results = cosim.run_all_engines(max_time=max_time)

    diffs = [d for r in results.values() for d in r.diffs]
    if diffs:
        for d in diffs:
            print(d)
        raise AssertionError(f"Cosim: {len(diffs)} differences vs Icarus")
    print("Cosim OK -", list(results.keys()), "match Icarus for", repr(module.name))
```

The `bench`-style skeleton (`style="bench"`) and the engine-native skeleton
(`style="bench"` + non-reference engine) include a similar function that
uses `parse_dut()` internally instead of taking a `module` argument.

## Relationship to the Test Suite

`tests/test_validation/test_iverilog_validation.py` — 74 test cases
covering combinational, sequential, and mixed designs.  These run against
all available engines via `run_all_engines()`.

`tests/test_validation/test_vm_vs_reference.py` — VCD-level cross-
validation of `"vm"` and `"vm-fast"` against `"reference"` (no Icarus
required).

Both test files are skipped gracefully when the required tools are not
installed, so the CI fast job always passes regardless of toolchain.
