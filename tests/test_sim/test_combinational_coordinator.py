"""Tests for CombinationalCoordinator — clockless/combinational DUT support."""

from __future__ import annotations

import pytest

from veriforge.dsl import Module
from veriforge.sim.endpoints import CombinationalCoordinator
from veriforge.sim.testbench import Simulator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _and_module():
    m = Module("and_gate")
    a = m.input("a")
    b = m.input("b")
    y = m.output("y")
    m.assign(y, a & b)
    return m.build()


def _or_module():
    m = Module("or_gate")
    a = m.input("a")
    b = m.input("b")
    y = m.output("y")
    m.assign(y, a | b)
    return m.build()


def _make_sim(module=None):
    if module is None:
        module = _and_module()
    sim = Simulator(module, engine="reference")
    sim.drive("a", 0)
    sim.drive("b", 0)
    sim.settle()
    return sim


# ---------------------------------------------------------------------------
# Minimal comb endpoints
# ---------------------------------------------------------------------------


class _InputDriver:
    """Drives 'a' and 'b' with a user-supplied (a, b) tuple."""

    def __init__(self, sim):
        self.sim = sim
        self.inputs = (0, 0)

    def tick_pre(self):
        a, b = self.inputs
        self.sim.drive("a", a)
        self.sim.drive("b", b)

    def tick_post(self):
        pass


class _OutputSampler:
    """Captures 'y' after settle."""

    def __init__(self, sim):
        self.sim = sim
        self.result: int | None = None

    def tick_pre(self):
        pass

    def tick_post(self):
        self.result = int(self.sim.read("y"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCombinationalCoordinator:
    def test_and_gate_drive_and_sample(self):
        sim = _make_sim()
        driver = _InputDriver(sim)
        sampler = _OutputSampler(sim)
        coord = CombinationalCoordinator(sim, [driver, sampler])

        driver.inputs = (1, 1)
        coord.step()
        assert sampler.result == 1

        driver.inputs = (1, 0)
        coord.step()
        assert sampler.result == 0

        driver.inputs = (0, 0)
        coord.step()
        assert sampler.result == 0

    def test_step_returns_true(self):
        sim = _make_sim()
        coord = CombinationalCoordinator(sim, [])
        assert coord.step() is True

    def test_run_until_predicate_met(self):
        sim = _make_sim()
        driver = _InputDriver(sim)
        sampler = _OutputSampler(sim)
        coord = CombinationalCoordinator(sim, [driver, sampler])
        step = [0]

        def _drive_then_check():
            if step[0] == 0:
                driver.inputs = (0, 1)
            elif step[0] == 1:
                driver.inputs = (1, 1)
            step[0] += 1
            return sampler.result == 1

        coord.run_until(_drive_then_check, max_steps=10, message="y == 1")
        assert sampler.result == 1

    def test_run_until_timeout_raises(self):
        sim = _make_sim()
        coord = CombinationalCoordinator(sim, [])
        with pytest.raises(TimeoutError):
            coord.run_until(lambda: False, max_steps=5, message="never")

    def test_sample_pre_is_called(self):
        """Endpoints with a sample_pre hook must have it called between tick_pre and tick_post."""
        sim = _make_sim()
        order: list[str] = []

        class _Ordered:
            def __init__(self, sim):
                self.sim = sim

            def tick_pre(self):
                order.append("tick_pre")

            def sample_pre(self):
                order.append("sample_pre")

            def tick_post(self):
                order.append("tick_post")

        coord = CombinationalCoordinator(sim, [_Ordered(sim)])
        coord.step()
        assert order == ["tick_pre", "sample_pre", "tick_post"]

    def test_no_clock_required(self):
        """CombinationalCoordinator must not access a clock signal."""
        m = Module("pure_comb")
        a = m.input("a")
        y = m.output("y")
        m.assign(y, ~a)  # inverter, no clock
        sim = Simulator(m.build(), engine="reference")
        sim.drive("a", 0)
        sim.settle()

        captured = []

        class _Inv:
            def __init__(self, s):
                self.sim = s

            def tick_pre(self):
                self.sim.drive("a", 1)

            def tick_post(self):
                captured.append(int(self.sim.read("y")))

        coord = CombinationalCoordinator(sim, [_Inv(sim)])
        coord.step()
        assert captured == [0]  # ~1 = 0 (1-bit)

    def test_or_gate(self):
        sim = _make_sim(_or_module())
        driver = _InputDriver(sim)
        sampler = _OutputSampler(sim)
        coord = CombinationalCoordinator(sim, [driver, sampler])

        driver.inputs = (0, 0)
        coord.step()
        assert sampler.result == 0

        driver.inputs = (0, 1)
        coord.step()
        assert sampler.result == 1
