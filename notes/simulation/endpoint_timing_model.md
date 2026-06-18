# Endpoint Timing Model

This document describes the precise ordering of operations in one Python testbench
clock cycle and explains why reading DUT outputs in the wrong phase can silently
produce incorrect results. This is the most important timing contract to understand
when writing custom endpoint logic (Level 3 in the testbench access-level model).

For an overview of all three access levels — proxy API, raw signal access, and custom
endpoints — see `notes/user_guide.md §13c`.

---

## The Three-Phase Cycle

`EndpointCoordinator.step()` (and `MultiDomainRunner.step()`) executes the
following operations in a fixed order every clock cycle:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Phase 1 — tick_pre()                                                       │
│  Python drives output signals: tvalid, tdata, tlast, tready, etc.           │
│  These are applied immediately to the signal state but combinational logic   │
│  has not yet re-evaluated.                                                   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│  Phase 2 — _settle_current_time()                                            │
│  sim.settle()  — propagates drives through combinational logic               │
│  Combinational logic re-evaluates. 'assign' and always_comb blocks settle.  │
│  After this, all combinational DUT outputs reflect the newly driven inputs.  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│  Phase 3 — sample_pre()                                                      │
│  *** READ DUT OUTPUTS HERE ***                                               │
│  Signal values here are stable and reflect what the DUT's flip-flops will   │
│  capture on the upcoming rising clock edge. This is the "D input" state.    │
│                                                                              │
│  e.g.: _sampled_handshake = (int(tvalid.value)==1 and int(tready.value)==1) │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                               run_step() — advance to the next event
                               ┌────────────────────────────────────┐
                               │  Clock toggles 0→1                 │
                               │  always @(posedge clk) blocks fire  │
                               │  Non-Blocking Assignments scheduled │
                               │  NBA delta-cycle fires:             │
                               │    registers take new values (Q)    │
                               └────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│  Phase 4 — tick_post()                                                       │
│  Act on what sample_pre() captured. Do NOT re-read registered signals here. │
│  Signal values here reflect the state AFTER the clock edge — i.e. the       │
│  beginning of the NEXT cycle's combinational phase.                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The Critical Rule

> **Read registered DUT outputs in `sample_pre()`. Use the snapshot in `tick_post()`.**
> **Never re-read registered signals inside `tick_post()`.**

For a synchronous handshake (`tvalid & tready` both high at the clock edge), the
correct question is: *were both signals high just before the edge?* That is exactly
what `sample_pre()` answers. By the time `tick_post()` runs, `run_step()` has already
fired all Non-Blocking Assignments, so register outputs reflect the state of the
*next* cycle — which may be entirely different.

**Combinational signals are safe to read anywhere** — they always reflect the current
function of their inputs and have no memory. Only registered outputs (flip-flop Q
outputs) are affected.

---

## Concrete Example: The axis_async_fifo Bug

This bug was discovered (and fixed) in `axis_source.py` during Wave F development
(May 2026, commit `6836a8e`).

The `axis_async_fifo` has a reset synchronizer chain:

```verilog
always @(posedge s_clk) begin
    s_rst_sync1_reg <= s_rst;
    s_rst_sync2_reg <= s_rst_sync1_reg;
    s_rst_sync3_reg <= s_rst_sync2_reg;   // gates s_axis_tready
end
```

At the first cycle after reset is de-asserted, the sync chain clears via Non-Blocking
Assignment, which releases `s_axis_tready`. The exact sequence was:

```
sample_pre():
    tready = 0  (sync chain not yet cleared — this is the D-input value)
    _sampled_handshake = (tvalid=1 AND tready=0) = FALSE  ← CORRECT

run_step() fires posedge:
    s_rst_sync3_reg NBA fires
    tready becomes 1 combinationally  ← NEXT cycle's state

tick_post() BEFORE THE FIX:
    if self._sampled_handshake OR int(self.tready.value) == 1:
                                   ^^^^^^^^^^^^^^^^^^^^^^^^
                                   reads 1 — but this is NEXT cycle's tready!
    → self._current_beat = None   ← beat falsely marked as consumed

DUT: captured tready=0 at clock edge → did NOT write the beat
Python: thought beat was sent → queue advanced
Result: 1-byte frame silently dropped
```

**The fix** was to remove the `or int(self.tready.value) == 1` fallback entirely.
`_sampled_handshake` from `sample_pre()` is the only correct source of truth.

This bug was latent for a long time because simple loopback modules drive `tready`
combinationally (always 1 from cycle 0). The async FIFO's registered reset synchronizer
was the first DUT where the fallback could trigger incorrectly.

---

## Rules for Writing Custom Endpoint Logic

### DO

```python
def sample_pre(self) -> None:
    # Snapshot registered DUT outputs here — this is "just before the clock edge"
    self._saw_valid = int(self.tvalid.value) == 1
    self._sampled_ready = int(self.tready.value) == 1
    self._sampled_handshake = self._saw_valid and self._sampled_ready

def tick_post(self) -> None:
    # Act on the snapshot — do NOT re-read registered signals
    if self._sampled_handshake:
        self._current_beat = None
        self._advance_state()
```

### DON'T

```python
def tick_post(self) -> None:
    # WRONG: reading a registered DUT output here gives NEXT cycle's value
    if int(self.tready.value) == 1:        # ← post-NBA, next-cycle state
        self._current_beat = None
    if int(self.some_flag.value) == 1:     # ← same hazard if flag is registered
        self._record_result()
```

### Edge cases

| Signal type | `tick_pre` | `sample_pre` | `tick_post` |
|---|---|---|---|
| Combinational assign (`assign y = a & b`) | ✅ safe to read | ✅ safe to read | ✅ safe to read |
| Registered (always @posedge clk) | ✅ safe to drive | ✅ correct pre-edge snapshot | ❌ post-NBA (next-cycle state) |
| Unknown (external DUT, no source) | — | ✅ safest | ❌ may be post-NBA |

When in doubt about whether a signal is registered or combinational, always snapshot
in `sample_pre()`.

---

## Why This Matters for Custom Handshake Logic

Any protocol that detects "did the DUT accept / present something this cycle?" is
subject to this hazard. Examples beyond AXI-Stream:

- **AXI4 write-address handshake**: `awvalid & awready` — if `awready` comes from a
  registered state machine, it must be sampled in `sample_pre()`.
- **Valid/ready flow control on memory interfaces**: same principle.
- **Waiting for a done flag**: if `done` is registered (not combinational), reading it
  in `tick_post()` tells you whether the DUT has set `done` for the *next* cycle, not
  this one.
- **Any `wait until signal == 1` polling loop**: the correct place to check is
  `sample_pre()`, not in a `tick_post()` callback.

---

## Relationship to Verilog Scheduling Semantics

This maps directly to the Verilog IEEE 1364 scheduling model (see also
`notes/simulation/simulation_model.md`):

- `sample_pre()` = "Observation point" — just before the Active region of a time step.
  This is the stable state that flip-flops sample. This is `$time` in Verilog.
- `run_step()` = Active region + NBA region of the time step at the clock edge.
- `tick_post()` = after all events at this time step have settled. Signal values
  equal what you would read at `$time + 0` in the next Verilog time step.

The correct RTL mental model: a handshake "happened at cycle N" if and only if both
VALID and READY were 1 in the Observation point of cycle N.
