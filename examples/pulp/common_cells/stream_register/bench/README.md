# `stream_register` — bench-framework testbench

A Wave A pulp port: drives the simple Pulp `stream_register` cell using
the high-level `Testbench(...)` framework + the new generic
ready/valid `StreamProxy`.

The DUT (`../rtl/stream_register.sv`) has two anonymous ready/valid
bundles:

| signals (DUT side)              | role                     | proxy name |
| --------------------------------| ------------------------ | ---------- |
| `valid_i`, `ready_o`, `data_i`  | slave (testbench drives) | `"in"`     |
| `valid_o`, `ready_i`, `data_o`  | master (testbench sinks) | `"out"`    |

Because the bundle prefixes are empty, the detector synthesizes the
proxy names `"in"` (slave-side) and `"out"` (master-side).

## Run it

```powershell
uv run python examples/pulp/common_cells/stream_register/bench/stream_register_bench.py
uv run python examples/pulp/common_cells/stream_register/bench/stream_register_bench.py --vcd waves.vcd
```

Output (success):

```
stream_register passed: 4-beat round-trip [0x11, 0x22, 0x33, 0x44]
```

## Regenerate from scratch

The scaffold itself was produced by the CLI and then hand-edited to
swap the embedded absolute `DUT_PATH` for a `__file__`-relative path
and replace the placeholder TODO stimulus with the assertion you see
now:

```powershell
uv run veriforge generate-python-testbench `
  -f examples/pulp/common_cells/stream_register/rtl/stream_register.sv `
  --module stream_register --enhanced --style bench `
  -o examples/pulp/common_cells/stream_register/bench/stream_register_bench.py
```

## Compare to the legacy testbench

The same DUT is exercised the "old way" by `../run_sim.py`, which uses
`Simulator` + `step_drive` directly and tests three scenarios
(capture-without-passthrough, blocked overwrite + refill, synchronous
clear). The bench-style scaffold focuses on the round-trip happy path
to demonstrate the high-level API; you can add the other scenarios by
parking on `iface.put(...)` and reading `bench.sim.read("data_o")`
between steps.
