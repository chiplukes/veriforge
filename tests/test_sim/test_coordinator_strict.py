"""Tests for EndpointCoordinator(strict=True) contract enforcement."""

from __future__ import annotations

import pytest

from veriforge.dsl import Module
from veriforge.sim.endpoints.helpers import (
    CombinationalCoordinator,
    DomainCoordinator,
    EndpointCoordinator,
    _GuardedSimulator,
)
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value


# ---------------------------------------------------------------------------
# Minimal DUT and sim factory
# ---------------------------------------------------------------------------


def _simple_module():
    m = Module("tb")
    m.input("clk")
    m.input("a")
    m.output("y")
    m.assign("y", "a")
    return m.build()


def _make_sim(module=None):
    if module is None:
        module = _simple_module()
    sim = Simulator(module, engine="reference")
    sim.drive("a", 0)
    sim.drive("clk", 0)
    sim.settle()
    clock = Clock(sim.signal("clk"), period=10)
    sim._schedule_clock_events(clock, max_time=5000)
    return sim


# ---------------------------------------------------------------------------
# Minimal mock endpoints
# ---------------------------------------------------------------------------


class _NopEndpoint:
    def __init__(self, sim):
        self.sim = sim

    def tick_pre(self):
        pass

    def sample_pre(self):
        pass

    def tick_post(self):
        pass


class _DriveInSamplePre:
    """Violates contract: drives during sample_pre."""

    def __init__(self, sim):
        self.sim = sim

    def tick_pre(self):
        pass

    def sample_pre(self):
        self.sim.drive("a", 1)  # illegal — sample_pre must not drive

    def tick_post(self):
        pass


class _ReadInTickPost:
    """Violates contract: reads during tick_post via sim.read()."""

    def __init__(self, sim):
        self.sim = sim
        self.last_read = None

    def tick_pre(self):
        pass

    def tick_post(self):
        self.last_read = self.sim.read("y")  # hazard — post-NBA, combinational not settled


# ---------------------------------------------------------------------------
# _GuardedSimulator unit tests (mock sim, no real elaboration needed)
# ---------------------------------------------------------------------------


class _MockSim:
    """Minimal stand-in for Simulator used by _GuardedSimulator unit tests."""

    _engine = "reference"

    def __init__(self):
        self._drives: list[tuple[str, object]] = []
        self._reads: list[str] = []

    def drive(self, name, value):
        self._drives.append((name, value))

    def read(self, name):
        self._reads.append(name)
        return Value(0, width=1)

    def settle(self):
        pass


class TestGuardedSimulator:
    def test_drive_outside_guarded_phases_passes(self):
        mock = _MockSim()
        g = _GuardedSimulator(mock)
        g._phase = "tick_pre"
        g.drive("x", 1)
        assert ("x", 1) in mock._drives

    def test_drive_in_sample_pre_raises(self):
        mock = _MockSim()
        g = _GuardedSimulator(mock)
        g._phase = "sample_pre"
        with pytest.raises(RuntimeError, match="sample_pre"):
            g.drive("x", 1)
        assert mock._drives == []

    def test_drive_in_idle_passes(self):
        mock = _MockSim()
        g = _GuardedSimulator(mock)
        g._phase = "idle"
        g.drive("x", 0)
        assert ("x", 0) in mock._drives

    def test_read_in_tick_post_warns(self, recwarn):
        mock = _MockSim()
        g = _GuardedSimulator(mock)
        g._phase = "tick_post"
        g.read("y")
        assert any("tick_post" in str(w.message) for w in recwarn)
        assert "y" in mock._reads

    def test_read_in_tick_pre_no_warning(self, recwarn):
        mock = _MockSim()
        g = _GuardedSimulator(mock)
        g._phase = "tick_pre"
        g.read("y")
        assert not any("tick_post" in str(w.message) for w in recwarn)

    def test_proxies_unknown_attributes(self):
        mock = _MockSim()
        g = _GuardedSimulator(mock)
        assert g._engine == "reference"

    def test_settle_delegates(self):
        settled = []
        mock = _MockSim()
        mock.settle = lambda: settled.append(True)
        g = _GuardedSimulator(mock)
        g.settle()
        assert settled == [True]


# ---------------------------------------------------------------------------
# EndpointCoordinator(strict=True) integration tests
# ---------------------------------------------------------------------------


class TestEndpointCoordinatorStrict:
    def test_strict_patches_endpoint_sim(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=True)
        assert isinstance(ep.sim, _GuardedSimulator)
        assert ep.sim._real is sim

    def test_non_strict_does_not_patch_endpoint_sim(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=False)
        assert ep.sim is sim

    def test_strict_drive_in_sample_pre_raises(self):
        sim = _make_sim()
        ep = _DriveInSamplePre(sim)
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=True)
        with pytest.raises(RuntimeError, match="sample_pre"):
            coordinator.step()

    def test_strict_read_in_tick_post_warns(self, recwarn):
        sim = _make_sim()
        ep = _ReadInTickPost(sim)
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=True)
        coordinator.step()
        tick_post_warnings = [w for w in recwarn if "tick_post" in str(w.message)]
        assert tick_post_warnings, "expected a UserWarning for read in tick_post"

    def test_strict_normal_endpoint_no_errors(self, recwarn):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=True)
        coordinator.step()
        tick_post_warnings = [w for w in recwarn if "tick_post" in str(w.message)]
        assert tick_post_warnings == []

    def test_guard_phase_resets_to_idle_after_step(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=True)
        coordinator.step()
        assert coordinator._guard._phase == "idle"

    def test_endpoint_without_sim_attr_is_skipped(self):
        class _NoSimAttr:
            def tick_pre(self):
                pass

            def tick_post(self):
                pass

        sim = _make_sim()
        ep = _NoSimAttr()
        coordinator = EndpointCoordinator(sim, [ep], clock_name="clk", strict=True)
        coordinator.step()  # must not raise


# ---------------------------------------------------------------------------
# DomainCoordinator(strict=True) tests
# ---------------------------------------------------------------------------


class TestDomainCoordinatorStrict:
    def test_strict_patches_endpoint_sim(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coord = DomainCoordinator(sim, [ep], clock_name="clk", strict=True)
        assert isinstance(ep.sim, _GuardedSimulator)
        assert ep.sim._real is sim

    def test_non_strict_does_not_patch_endpoint_sim(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coord = DomainCoordinator(sim, [ep], clock_name="clk", strict=False)
        assert ep.sim is sim

    def test_strict_drive_in_sample_pre_raises(self):
        sim = _make_sim()
        ep = _DriveInSamplePre(sim)
        coord = DomainCoordinator(sim, [ep], clock_name="clk", strict=True)
        coord.tick_pre()
        with pytest.raises(RuntimeError, match="sample_pre"):
            coord.sample_pre()
        coord._set_phase("idle")  # clean up phase

    def test_strict_read_in_tick_post_warns(self, recwarn):
        sim = _make_sim()
        ep = _ReadInTickPost(sim)
        coord = DomainCoordinator(sim, [ep], clock_name="clk", strict=True)
        coord.tick_pre()
        coord.tick_post()
        tick_post_warnings = [w for w in recwarn if "tick_post" in str(w.message)]
        assert tick_post_warnings, "expected a UserWarning for read in tick_post"

    def test_strict_normal_endpoint_no_errors(self, recwarn):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coord = DomainCoordinator(sim, [ep], clock_name="clk", strict=True)
        coord.tick_pre()
        coord.sample_pre()
        coord.tick_post()
        tick_post_warnings = [w for w in recwarn if "tick_post" in str(w.message)]
        assert tick_post_warnings == []
        assert coord._guard._phase == "idle"

    def test_endpoint_without_sim_attr_is_skipped(self):
        class _NoSimAttr:
            def tick_pre(self):
                pass

            def tick_post(self):
                pass

        sim = _make_sim()
        ep = _NoSimAttr()
        coord = DomainCoordinator(sim, [ep], clock_name="clk", strict=True)
        coord.tick_pre()
        coord.tick_post()  # must not raise


# ---------------------------------------------------------------------------
# CombinationalCoordinator(strict=True) tests
# ---------------------------------------------------------------------------


class TestCombinationalCoordinatorStrict:
    def test_strict_patches_endpoint_sim(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coord = CombinationalCoordinator(sim, [ep], strict=True)
        assert isinstance(ep.sim, _GuardedSimulator)
        assert ep.sim._real is sim

    def test_non_strict_does_not_patch_endpoint_sim(self):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coord = CombinationalCoordinator(sim, [ep], strict=False)
        assert ep.sim is sim

    def test_strict_drive_in_sample_pre_raises(self):
        sim = _make_sim()
        ep = _DriveInSamplePre(sim)
        coord = CombinationalCoordinator(sim, [ep], strict=True)
        with pytest.raises(RuntimeError, match="sample_pre"):
            coord.step()

    def test_strict_read_in_tick_post_warns(self, recwarn):
        sim = _make_sim()
        ep = _ReadInTickPost(sim)
        coord = CombinationalCoordinator(sim, [ep], strict=True)
        coord.step()
        tick_post_warnings = [w for w in recwarn if "tick_post" in str(w.message)]
        assert tick_post_warnings, "expected a UserWarning for read in tick_post"

    def test_strict_normal_endpoint_no_errors(self, recwarn):
        sim = _make_sim()
        ep = _NopEndpoint(sim)
        coord = CombinationalCoordinator(sim, [ep], strict=True)
        coord.step()
        tick_post_warnings = [w for w in recwarn if "tick_post" in str(w.message)]
        assert tick_post_warnings == []

    def test_endpoint_without_sim_attr_is_skipped(self):
        class _NoSimAttr:
            def tick_pre(self):
                pass

            def tick_post(self):
                pass

        sim = _make_sim()
        ep = _NoSimAttr()
        coord = CombinationalCoordinator(sim, [ep], strict=True)
        coord.step()  # must not raise
