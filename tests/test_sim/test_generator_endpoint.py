"""Tests for GeneratorEndpoint — generator-based endpoint adapter."""

from __future__ import annotations

from veriforge.model.design import Module
from veriforge.model.nets import Net, NetKind
from veriforge.model.expressions import Identifier, BinaryOp, Literal, Range
from veriforge.model.assignments import ContinuousAssign
from veriforge.sim.bench.runtime import Testbench
from veriforge.sim.endpoints._generator import GeneratorEndpoint
from veriforge.sim.value import Value


def _make_and_gate() -> Module:
    m = Module("and_gate")
    m.nets = [
        Net("a", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
        Net("b", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
        Net("y", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
    ]
    m.continuous_assigns = [
        ContinuousAssign(Identifier("y"), BinaryOp("&", Identifier("a"), Identifier("b"))),
    ]
    return m


class TestGeneratorEndpointUnit:
    """Unit tests for GeneratorEndpoint adapter."""

    def test_simple_driver_cycles(self):
        """Generator drives a value, increments after each tick_post."""
        values: list[int] = []

        def driver():
            val = 0
            for _ in range(3):
                values.append(val)  # tick_pre: record pre-edge value
                yield  # → wait for edge
                val += 1  # tick_post: commit
                yield  # → done

        ep = GeneratorEndpoint(driver)

        # Cycle 1: tick_pre → tick_post
        ep.tick_pre()
        assert values == [0]
        ep.tick_post()

        # Cycle 2
        ep.tick_pre()
        assert values == [0, 1]
        ep.tick_post()

        # Cycle 3
        ep.tick_pre()
        assert values == [0, 1, 2]
        ep.tick_post()

        # Cycle 4: generator exhausted, re-creates → val resets to 0
        ep.tick_pre()
        assert values == [0, 1, 2, 0]
        ep.tick_post()

    def test_wrong_domain_resets(self):
        """When tick_post is skipped (wrong domain), generator re-creates."""
        pre_count = [0]

        def driver():
            pre_count[0] += 1  # tick_pre: count
            yield
            # tick_post: no side effects in this test
            yield

        ep = GeneratorEndpoint(driver)

        # Correct cycle
        ep.tick_pre()
        assert pre_count[0] == 1
        ep.tick_post()

        # Wrong domain: tick_pre runs but tick_post skipped
        ep.tick_pre()
        assert pre_count[0] == 2  # resumed existing generator (no reset yet)
        # No tick_post

        # Next tick_pre: should re-create (old gen is stale)
        ep.tick_pre()
        assert pre_count[0] == 3  # fresh generator

    def test_generator_exhaustion_restarts(self):
        """When generator finishes, next tick_pre creates a fresh one."""
        runs = [0]

        def driver():
            runs[0] += 1
            yield  # tick_pre done
            yield  # tick_post done

        ep = GeneratorEndpoint(driver)

        ep.tick_pre()
        assert runs[0] == 1
        ep.tick_post()  # generator exhausted
        ep.tick_pre()  # should restart
        assert runs[0] == 2
        ep.tick_post()
        ep.tick_pre()
        assert runs[0] == 3


class TestGeneratorEndToEnd:
    """End-to-end tests using the bench framework with @domain.generator."""

    def test_single_domain_generator_combinational(self):
        """Combinational DUT: one domain drives, reads output after step."""
        m = _make_and_gate()
        bench = Testbench(m, engine="reference")
        bench.sim.drive("a", 0xAA)
        bench.sim.drive("b", 0x55)
        bench.step()
        assert bench.sim.read("y") == Value(0xAA & 0x55, width=8)

    def test_generator_with_combinational_coordinator(self):
        """Generator registered on combinational domain drives signal."""
        m = _make_and_gate()
        bench = Testbench(m, engine="reference")
        # Get the combinational domain
        d = bench._domains[bench.plan.domains[0].name]

        driven: list[int] = []

        @d.generator
        def driver():
            val = 0x42
            for _ in range(2):
                d.coordinator.sim.drive("a", val)
                driven.append(val)
                yield
                val += 1
                yield

        bench.sim.drive("b", 0xFF)
        bench.step()
        assert driven == [0x42]
        bench.step()
        assert driven == [0x42, 0x43]
        assert bench.sim.read("y") == Value(0x43 & 0xFF, width=8)
