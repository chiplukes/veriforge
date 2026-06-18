# Testbench API comparison

Three approaches to writing a multi-clock-domain testbench with concurrent
drivers.  All examples drive two signals on independent clocks in a
cross-domain AND gate (`assign y = a & b`).

## The DUT

```verilog
module cross_domain_and (
    input  wire [7:0] a,
    input  wire [7:0] b,
    output wire [7:0] y
);
    assign y = a & b;
endmodule
```

Domain A has clock `clk_a` (period 10 ns).
Domain B has clock `clk_b` (period 17 ns) — truly asynchronous.

---

## Approach 1: Raw `Simulator` API

No framework at all.  You manage edge detection, phase ordering, and
idempotence yourself.

```python
from veriforge.sim.testbench import Simulator, Clock
from veriforge.sim.value import Value

sim = Simulator(module, engine="reference")

clk_a = Clock(sim.signal("clk_a"), period=10)
clk_b = Clock(sim.signal("clk_b"), period=17)
sim.fork(clk_a)
sim.fork(clk_b)

a_val, b_val = 0xAA, 0x55
prev_a, prev_b = 0, 0

for _ in range(20):
    if not sim.run_step():
        break

    clk_a_now = int(clk_a.value)
    clk_b_now = int(clk_b.value)

    # ── YOU track edges manually ──
    if prev_a == 0 and clk_a_now == 1:
        a_val = (a_val + 1) & 0xFF
        sim.drive("a", a_val)
    if prev_b == 0 and clk_b_now == 1:
        b_val = (b_val + 1) & 0xFF
        sim.drive("b", b_val)

    prev_a, prev_b = clk_a_now, clk_b_now
    print(f"t={sim.time:3d}  a={sim.read('a'):#04x}  "
          f"b={sim.read('b'):#04x}  y={sim.read('y'):#04x}")
```

**What you own:**

| Concern | Who handles it |
|---|---|
| Edge detection | You — `prev` / `current` tracking |
| Phase ordering | You — drive before step, read after |
| Cross-domain safety | You — ensure idempotent re-driving |
| Wrong-domain edge | N/A — you only drive on your edge |
| Contract enforcement | None |
| Clock scheduling | You — `Clock` + `sim.fork()` + `run_step()` |

---

## Approach 2: Class-based endpoints (current framework)

You implement `tick_pre()` / `tick_post()` on a class.  The framework calls
you.  `MultiDomainRunner` handles edge detection and wrong-domain safety.

```python
from veriforge.sim.bench.runtime import Testbench
from veriforge.sim.bench.plan import ClockDomain, ClockSpec, TestbenchPlan

plan = TestbenchPlan(
    top=module.name,
    domains=(
        ClockDomain(name="dom_a", clock=ClockSpec(name="clk_a", period_hint=10)),
        ClockDomain(name="dom_b", clock=ClockSpec(name="clk_b", period_hint=17)),
    ),
    interfaces=(),
)
bench = Testbench(module, engine="reference", plan=plan, strict=True)

class CounterDriver:
    """Drive a counter.  Holds the current value so wrong-domain tick_pre()
    re-drives the same value — idempotent at the wire level."""

    def __init__(self, domain, signal_name, start=0):
        self.sim = domain.sim
        self._held = Value(start, width=8)
        self._name = signal_name
        self._stepped = False

    def tick_pre(self):
        self.sim.drive(self._name, self._held)

    def sample_pre(self):
        pass

    def tick_post(self):
        # Only called when our domain's clock actually rose.
        if self._stepped:
            self._held = Value((self._held.val + 1) & 0xFF, width=8)
            self._stepped = False

    def step(self):
        self._stepped = True


class Watcher:
    """Print y on every rising edge of domain A."""

    def __init__(self, domain):
        self.sim = domain.sim
        self._sampled = None

    def tick_pre(self):
        pass

    def sample_pre(self):
        self._sampled = self.sim.read("y")

    def tick_post(self):
        print(f"t={self.sim.time:3d}  "
              f"y={self._sampled:#04x}")

    @property
    def sim_prop(self):
        return self.sim

dom_a = bench.domain("dom_a")
dom_b = bench.domain("dom_b")

driver_a = CounterDriver(dom_a, "a", start=0xAA)
driver_b = CounterDriver(dom_b, "b", start=0x55)
watcher = Watcher(dom_a)

dom_a.register(driver_a)
dom_a.register(watcher)
dom_b.register(driver_b)

for _ in range(12):
    driver_a.step()
    driver_b.step()
    bench.step()
```

**What the framework handles:**

| Concern | Who handles it |
|---|---|
| Edge detection | `MultiDomainRunner` |
| Phase ordering | `tick_pre → settle → sample_pre → edge → tick_post` |
| Cross-domain safety | Wrong-domain `tick_post()` never fires |
| Idempotent re-driving | You hold values in `_held`; framework calls `tick_pre()` on every domain |
| Contract enforcement | `_GuardedSimulator` (`strict=True`) |
| Clock scheduling | `Testbench.__init__` |

---

## Approach 3: Generator-based endpoints (`GeneratorEndpoint`)

Write a single coroutine with `yield` marking the clock-edge boundary.
Everything before the first `yield` is `tick_pre`; everything between
the two `yield`s is `tick_post`.  No class, no method plumbing.

```python
from veriforge.sim.bench.runtime import Testbench
from veriforge.sim.bench.plan import ClockDomain, ClockSpec, TestbenchPlan

plan = TestbenchPlan(
    top=module.name,
    domains=(
        ClockDomain(name="dom_a", clock=ClockSpec(name="clk_a", period_hint=10)),
        ClockDomain(name="dom_b", clock=ClockSpec(name="clk_b", period_hint=17)),
    ),
    interfaces=(),
)
bench = Testbench(module, engine="reference", plan=plan, strict=True)

dom_a = bench.domain("dom_a")
dom_b = bench.domain("dom_b")

@dom_a.generator
def driver_a():
    val = 0xAA
    while True:
        dom_a.coordinator.sim.drive("a", val)
        yield                    # ← tick_pre done, wait for clk_a edge
        val = (val + 1) & 0xFF
        yield                    # ← tick_post done

@dom_b.generator
def driver_b():
    val = 0x55
    while True:
        dom_b.coordinator.sim.drive("b", val)
        yield                    # ← tick_pre done, wait for clk_b edge
        val = (val + 1) & 0xFF
        yield                    # ← tick_post done

@dom_a.generator
def watcher():
    while True:
        y = dom_a.coordinator.sim.read("y")
        yield
        print(f"t={dom_a.coordinator.sim.time:3d}  y={y:#04x}")
        yield

for _ in range(12):
    bench.step()
```

**What the framework handles** — same as Approach 2, plus:

| Concern | Who handles it |
|---|---|
| State across cycles | Generator local variables survive `yield` |
| Idempotent re-driving | Wrong-domain edge: generator discarded and factory re-called |
| Phase split | Two `yield`s replace three methods |

---

## What the `yield` boundary means

```
def driver_a():
    val = 0xAA
    while True:
        dom_a.coordinator.sim.drive("a", val)   # ← tick_pre: drive
        yield   # ─────────────────────────────  # ← clock edge boundary
        val = (val + 1) & 0xFF                    # ← tick_post: commit
        yield   # ─────────────────────────────  # ← done; wait for next tick_pre
```

| Generator state | `tick_pre()` | `tick_post()` |
|---|---|---|
| Before first `yield` | Runs here | — |
| Between yields | — | Runs here (only on risen domain) |
| After second `yield` | Next `tick_pre()` continues from here | — |

If `tick_post()` is never called (wrong-domain edge), the generator is
discarded and the factory re-creates it — restoring the state that was
captured before the first `yield`.

---

## When to use each

| Approach | Best for |
|---|---|
| **Raw API** | Quick experiments, single-domain, no protocol endpoints |
| **Class-based** | Complex state machines with many fields, reusable endpoint libraries |
| **Generator-based** | Simple drivers, watchers, one-shot sequences, readability over reuse |
