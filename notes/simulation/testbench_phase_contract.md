# Testbench Phase Contract

Endpoints used by the bench framework (`AXIStreamSource`, `AXIStreamSink`,
`AXILiteMaster`, `AXILiteResponder`, `StreamProxy`, etc.) must follow a strict
three-phase lifecycle that brackets each rising clock edge:

| Phase         | Purpose                                                                 |
| ------------- | ----------------------------------------------------------------------- |
| `tick_pre()`  | Drive DUT input signals for the upcoming edge. **Must be idempotent.**  |
| `sample_pre()`| Capture DUT outputs immediately before the edge into endpoint state.     |
| `tick_post()` | Commit state changes (pop queues, append received beats) after the edge. |

This is not a stylistic convention — it is what makes the bench framework
**safe under truly-asynchronous multi-clock testbenches** driven by
`MultiDomainRunner`.

## Why the contract exists

`MultiDomainRunner.step()` (see `sim/endpoints/helpers.py`) does the following
per logical step:

1. Calls `tick_pre()` on **every** domain.
2. Settles combinational logic at the current simulation time.
3. Calls `sample_pre()` on **every** domain.
4. Advances the scheduler (`sim.run_step()`) until **at least one** clock rises.
5. Calls `tick_post()` only on the domain(s) whose clock actually rose.

For truly-async domains (e.g., `s_clk` at 10 ns and `m_clk` at 17 ns), step 4
will frequently advance time past edges on *other* domains. The wrong domain's
`tick_pre()` and `sample_pre()` will, in general, be called when its clock is
**not** about to rise. The contract below is what makes that safe.

## Per-phase rules

### `tick_pre()` — drive only, must be idempotent

- Drive every signal the endpoint owns to its current intended value.
- The same value must be re-driven on every call until a state change is
  explicitly committed in `tick_post()`.
- **Never** consume queue items, advance internal pointers, or mutate
  observable state.
- **Never** drive single-cycle pulses without a handshake — if some other
  domain's edge fires first, the pulse will be observed by the wrong clock.

### `sample_pre()` — capture only

- Read DUT outputs you intend to commit later (e.g., `tready`, `tvalid`,
  `tdata`).
- Stash the captured values in `_sampled_*` fields.
- Set a `_sampled_handshake` (or equivalent) flag if a transfer occurred at
  this edge.
- **Never** drive signals from `sample_pre()`.

### `tick_post()` — commit, dispatched only on risen domains

- `MultiDomainRunner` calls this method only for domains whose clock actually
  rose this step, so it is safe to:
  - Pop a queue head after a successful handshake.
  - Append a sampled beat to the received-frames queue.
  - Advance internal counters that represent "edges seen on my clock".

## The protocol invariant that rescues handshake endpoints

Even though `tick_pre()` runs on every domain every step, AXI-Stream
(and AXI-Lite, AXI4, ready/valid) endpoints survive because the *protocol*
already requires the source to hold `VALID`/`DATA` stable until the handshake
completes. So:

- A source's `tick_pre()` re-drives the **same** held beat until its own
  `tick_post()` pops the queue.
- A wrong-domain `tick_pre()` call is therefore a no-op at the wire level.
- A sink's `tick_post()` (which is the only place beats are committed) only
  runs on its own risen edge, so cross-domain edges never corrupt the
  received stream.

This is verified end-to-end by
`tests/test_dsl/test_taxi_axis_async_fifo_dualclk.py`, which runs a
`MultiDomainRunner` against a true dual-clock async FIFO with independent
clocks and randomized backpressure on both sides under strict-mode
protocol monitoring.

## Patterns that **break** the contract

These are the canonical hazards. The endpoints in the framework do not exhibit
them, but custom user testbench code can.

1. **Single-cycle fire-and-forget strobes from `tick_pre`.**
   A 1-cycle `start_pulse` driven in `tick_pre()` and expected to be consumed
   by the local clock will be sampled by any clock that fires first in a
   multi-domain run. Use a handshake or extend the strobe until acknowledged.

2. **Read-then-write within a single `tick_pre`.**
   ```python
   # WRONG: a wrong-domain edge can fire between the read and the write
   def tick_pre(self):
       if int(self.dut_resp.value):
           step_drive(..., self.ack.name, 1)
   ```
   Split: capture in `sample_pre`, commit in `tick_post`.

3. **State machines that advance in `tick_pre`.**
   ```python
   # WRONG: state advances on every wrong-domain tick as well
   def tick_pre(self):
       if self.state == "REQ":
           self.state = "WAIT"
   ```
   Advance state from `tick_post()` only; treat `tick_pre()` as a pure
   re-application of the current state's outputs.

4. **Same-edge flags read by multiple `tick_post`s.**
   `_sampled_handshake` works because `tick_post` is called only on the risen
   domain. Do not add code that consumes the flag from a sibling endpoint or
   from a domain whose clock did not rise.

## When you don't use the framework

DUTs without a matching endpoint use the raw `Simulator` API directly. There
is no `tick_pre`/`sample_pre`/`tick_post` discipline imposed; the user is
responsible for writing single-edge logic correctly. See
`notes/simulation/simulator_engines.md` for the raw-API patterns.

## Cross-references

- `src/veriforge/sim/endpoints/helpers.py` — `MultiDomainRunner.step()`
- `src/veriforge/sim/endpoints/axis_source.py` — reference implementation
- `src/veriforge/sim/endpoints/axis_sink.py` — reference implementation
- `tests/test_dsl/test_taxi_axis_async_fifo_dualclk.py` — truly-async coverage
- `tests/test_sim/test_multi_domain_runner.py` — combinational multi-domain
- `notes/known_issues.md` — broader catalogue of simulator latent risks
