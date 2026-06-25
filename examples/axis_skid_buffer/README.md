# AXI-Stream skid buffer example

This example walks through the full veriforge testbench-generation workflow,
from a raw Verilog file to a running, back-pressure-aware simulation.

## What is a skid buffer?

`axis_skid_buf.v` is a single-register AXI-Stream pipeline stage. It adds
exactly one clock cycle of registered latency between `s_axis` and `m_axis`.
`s_axis_tready` is driven combinatorially:

```verilog
assign s_axis_tready = !m_axis_tvalid || m_axis_tready;
```

This means the source sees zero-latency back-pressure: when the output
register is full and the downstream holds `m_axis_tready` low, `s_axis_tready`
drops immediately, stalling the source. This is the key property under test.

## Step 1 — inspect the inferred plan

Before generating any code, check what veriforge infers about the DUT:

```bash
uv run veriforge generate-python-testbench \
    --file examples/axis_skid_buffer/axis_skid_buf.v \
    --explain-plan
```

Expected output:

```
TestbenchPlan(top='axis_skid_buf')
  domains:
    - clk: clock=clk (posedge, period=?); reset=rst (active-high, sync)
  interfaces:
    - m_axis (axi_stream, role=master) -> domain=clk [structural]
    - s_axis (axi_stream, role=slave)  -> domain=clk [structural]
```

The plan shows clocks, resets, and every detected interface. Read it before
generating so you can spot any incorrect inferences and add overrides.

## Step 2 — generate the scaffold

```bash
uv run veriforge generate-python-testbench \
    --file examples/axis_skid_buffer/axis_skid_buf.v \
    --enhanced --style=bench \
    --output /tmp/test_skid_scaffold.py
```

The generated file contains:

- `drive_s_axis(bench)` — stub that calls `iface.put([0x00, 0x01, 0x02, 0x03])`
- `expect_m_axis(bench)` — stub that calls `iface.get(timeout=200)`
- `run_smoke_test()` with a `NUM_FRAMES` pre-load / drain loop
- Commented-out `PauseGenerator` lines for both source gaps and sink back-pressure

## Step 3 — understand the generated code

### `put()` and auto-tlast

```python
s_axis.put([0x10, 0x11, 0x12, 0x13])
```

`put()` queues the frame into the source without advancing the clock.
`tlast=1` is set automatically on the final beat — you do not need to set it
explicitly. To override per-beat tlast, pass `last=[0, 0, 0, 1]` or any
list of the same length as the data.

### Pre-load / drain pattern

```python
# Queue all input frames first — no clock steps happen.
for frame_data in FRAMES:
    s_axis.put(frame_data)

# Drain output independently — the source feeds beats automatically as
# get() steps the simulation clock.
for expected_data in FRAMES:
    m_axis.expect(expected_data, timeout=200)
```

Separating the two loops is important: it decouples stimulus generation from
output checking. The source can run ahead of the sink, which is what happens
in real hardware. A tight send-then-receive loop forces an artificial
one-packet-at-a-time cadence.

### `expect()` vs `get()`

| Method | Description |
|--------|-------------|
| `iface.expect(data, timeout=N)` | Assert-style: raises `AssertionError` on mismatch |
| `iface.get(timeout=N)` | Returns an `AXIStreamFrame`; inspect `frame.data` yourself |

Both step the clock internally until the frame's `tlast` beat is consumed.

## Step 4 — add back-pressure

```python
from veriforge.sim.endpoints import PauseGenerator

# Gate tready low ~25% of cycles on the output.
m_axis.pause = PauseGenerator(1, 4)

# Gate tvalid low ~25% of cycles on the input.
s_axis.pause = PauseGenerator(1, 4)
```

`PauseGenerator(num, denom)` returns `True` (pause this cycle) with
probability `num / denom`. When used on an `AXIStreamProxy` for a sink
interface, it gates `tready`; for a source interface, it gates `tvalid`.
Pass `seed=N` for reproducible random sequences.

## Step 5 — run the example

```bash
uv run python examples/axis_skid_buffer/test_axis_skid_buf.py
```

With a VCD waveform:

```bash
uv run python examples/axis_skid_buffer/test_axis_skid_buf.py --vcd build/skid.vcd
gtkwave build/skid.vcd
```

Expected output:

```
=== Inferred TestbenchPlan ===
...

  received [16, 17, 18, 19]
  received [160, 161, 162]
  received [255]
test_basic: PASSED

  received [0, 1, 2, 3, 4, 5, 6, 7] (with back-pressure)
  received [8, 9, 10, 11, 12, 13, 14, 15] (with back-pressure)
  received [16, 17, 18, 19, 20, 21, 22, 23] (with back-pressure)
test_backpressure: PASSED

All tests passed.
```
