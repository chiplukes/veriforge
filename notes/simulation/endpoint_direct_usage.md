# Using AXI-Stream Endpoints Directly

This guide covers the lower-level endpoint API — `AXIStreamSource`, `AXIStreamSink`,
and `EndpointCoordinator` — for cases where the high-level `Testbench.iface()` proxy
is not appropriate, typically when:

- The DUT module is built programmatically (not parsed from a `.v` file).
- The testbench needs direct control over the per-beat loop (custom protocol mocking,
  per-beat callbacks, non-standard sideband signals).
- The DUT has only part of an AXI-Stream interface (e.g. no `tlast`).

For most use cases prefer `Testbench.iface()` — see `notes/simulation/bench_usage.md`.

---

## Minimal setup

```python
from veriforge.sim import Simulator
from veriforge.sim.endpoints import AXIStreamSource, AXIStreamSink, EndpointCoordinator

dut = build_my_module()           # returns a veriforge.model.design.Module
sim = Simulator(dut)

# Reset
sim.drive("clk", 0)
sim.drive("rst", 1)
sim.settle()
sim.drive("clk", 1); sim.settle(); sim.drive("clk", 0); sim.settle()
sim.drive("rst", 0)

# Endpoints
src = AXIStreamSource(sim, "s_axis")   # drives s_axis_tvalid/tdata/tlast/tuser
snk = AXIStreamSink(sim, "m_axis")     # drives m_axis_tready, captures frames

coord = EndpointCoordinator(sim, [src, snk], clock_name="clk", strict=True)

# Queue stimulus
src.send([0x01, 0x02, 0x03])

# Run until sink has a frame
coord.run_until(lambda: not snk.empty(), max_steps=200, message="frame not received")

frame = snk.recv()
assert list(frame.data) == [0x01, 0x02, 0x03]
```

---

## The three-phase cycle

`EndpointCoordinator.step()` executes in strict order each clock cycle:

```
tick_pre()    — drive tvalid/tdata/tready onto wires
settle()      — propagate combinational logic
sample_pre()  — read tready/tvalid (pre-posedge D-input snapshot)
run_step()    — posedge fires, NBA registers take new values
tick_post()   — commit: pop queue on handshake, append received beats
```

`sample_pre()` captures the signal state **before** the clock edge — this is the
stable "D-input" that flip-flops will sample. This is the only correct place to
detect whether a handshake occurred.

See `notes/simulation/endpoint_timing_model.md` for a detailed explanation with diagrams.

---

## Critical: tready must be combinatorial on the DUT

`AXIStreamSource.sample_pre()` reads `tready` before the posedge and uses it to
decide whether the beat presented in `tick_pre()` will be consumed at that posedge.
This is correct — but only if `tready` reflects the DUT's current state **combinatorially**.

If `tready` is a registered output (an `output_reg` in the veriforge DSL), it shows
the value set at the **previous** posedge. When a DUT transitions from a "not ready"
state to a "ready" state, the registered tready lags one cycle behind:

```
posedge N-1: DUT was in IDLE → registered tready ← 0 ; state ← ACTIVE
posedge N:   DUT is in ACTIVE, internally accepts a beat
             sample_pre at cycle N reads tready = 0 (from posedge N-1) → NO advance
             posedge N: DUT also registers tready ← 1
posedge N+1: sample_pre reads tready = 1 → source advances the beat pointer
             posedge N+1: DUT in ACTIVE, sees the SAME beat on the bus → double-consumption
```

**Fix**: drive `s_axis_tready` as a combinatorial `output` from the DUT, not a
registered `output_reg`:

```python
# In veriforge DSL — correct: combinatorial tready
s_tready = m.output("s_axis_tready")   # wire type, not a register
...
m.assign(s_tready, (state == ST_ACTIVE) & i_can_accept)
```

With combinatorial tready, after the posedge where `state` NBA-fires to `ST_ACTIVE`,
`settle()` in the **same** cycle propagates the new state through the combinatorial
path, so `sample_pre()` sees `tready=1` and advances the beat pointer before the
DUT consumes it. No double-consumption.

Registered tready is safe only if the DUT's internal consumption logic is also gated
on the registered tready (not on some other ready condition), accepting the one-cycle
bubble. The two must match; a mismatch is an AXI-Stream compliance violation.

---

## Sending frames with sideband (tuser, tdest)

`AXIStreamSource.send()` accepts `AXIStreamFrame` objects:

```python
from veriforge.sim.endpoints import AXIStreamFrame

frame = AXIStreamFrame(
    [0xAB, 0xCD],
    elements_per_beat=1,
    element_size_bits=8,
)
frame.tuser = [0x03, 0x00]   # per-beat tuser values
src.send(frame)
```

Or construct via `Testbench.iface()` helper methods:

```python
frame = bench.iface("s_axis").frame([0xAB, 0xCD], user=0x03)
```

---

## Mixing manual sim_step with endpoints

If only part of the testbench uses the endpoint framework (e.g. the DUT output side
uses `AXIStreamSink` but the input side is driven manually), you can call the phases
yourself:

```python
for cycle in range(max_cycles):
    # Manual drives go here (before tick_pre propagates endpoint state)
    sim.drive("encode_o_done", my_done_flag)

    snk.tick_pre()
    sim.settle()
    snk.sample_pre()
    # posedge
    sim.drive("clk", 1); sim.settle()
    sim.drive("clk", 0)
    snk.tick_post()

    if not snk.empty():
        break
```

The key invariant: always call `sample_pre()` **after** `settle()` and **before**
the posedge; always call `tick_post()` **after** the posedge.

---

## Strict mode and protocol checking

Pass `strict=True` to `EndpointCoordinator` (or directly to `AXIStreamSink`) to
enable AXI-Stream protocol monitoring:

```python
coord = EndpointCoordinator(sim, [src, snk], clock_name="clk", strict=True)
snk  = AXIStreamSink(sim, "m_axis", strict=True)
```

In strict mode, `AXIStreamSink` raises `AXIStreamProtocolError` if:
- `tvalid` is de-asserted before a completed handshake (spec §2.2.1)
- `tdata` or `tlast` changes while `tvalid=1` without a handshake

In strict mode, the `EndpointCoordinator` also raises `RuntimeError` if any endpoint
calls `sim.drive()` during `sample_pre()`, or warns on `sim.read()` during `tick_post()`.

---

## See also

- `notes/simulation/bench_usage.md` — high-level `Testbench` + proxy API
- `notes/simulation/endpoint_timing_model.md` — precise per-phase timing diagram
- `notes/simulation/testbench_phase_contract.md` — phase contract rules for custom endpoints
- `src/veriforge/sim/endpoints/axis_source.py` — `AXIStreamSource` reference implementation
- `src/veriforge/sim/endpoints/axis_sink.py` — `AXIStreamSink` reference implementation
