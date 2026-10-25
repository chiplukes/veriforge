"""One expression object used by several statements (DSL), on every engine.

The DSL hands the same Python expression to each statement that uses it, so the
model holds one node in several places. The compiled engine caches a hoisted
temporary per node (`_et_node_vals`, keyed by identity); a temporary hoisted inside
an if or case arm was reused by a later arm, or after the branch, where it had
never been assigned (found in gfwx-fpga's lifting_vertical2: a row-end flag shared
by three arms of one if/elif chain read 0 in the second and third).
"""

from __future__ import annotations

import pytest

from veriforge.dsl import Module, posedge
from veriforge.model.design import Design
from veriforge.sim.bench import Testbench

from ..engines import ENGINES


def _run(top, engine: str, steps: list[dict[str, int]], out: str) -> list[int]:
    """Drive each step's inputs, clock once, and read `out` after the edge."""
    tb = Testbench(top, engine=engine, design=Design(modules=[top]), max_sim_time=1000)
    seen = []
    with tb.run():
        for inputs in steps:
            for name, value in inputs.items():
                tb.sim.drive(name, value)
            tb.sim.settle()
            tb.step(1)
            seen.append(int(tb.sim.read(out)))
    return seen


def _if_elif_module():
    m = Module("shared_if_elif")
    clk = m.input()
    a = m.input()
    b = m.input()
    x = m.input(width=3)
    y = m.output_reg(init=0)
    eq = x == 3  # one node, assigned in both arms
    with m.always(posedge(clk)):
        with m.if_(a):
            y <<= eq
        with m.elif_(b):
            y <<= eq
    return m.build()


@pytest.mark.parametrize("engine", ENGINES)
def test_shared_node_in_if_and_elif_arms(engine):
    steps = [{"a": 1, "b": 0, "x": 3}, {"a": 0, "b": 0, "x": 0}, {"a": 0, "b": 1, "x": 3}, {"a": 0, "b": 1, "x": 2}]
    assert _run(_if_elif_module(), engine, steps, "y") == [1, 1, 1, 0]


def _case_module():
    m = Module("shared_case")
    clk = m.input()
    sel = m.input(width=2)
    x = m.input(width=4)
    y = m.output_reg(init=0)
    big = x > 9  # one node, assigned in three arms (a compare is hoisted to a temporary)
    with m.always(posedge(clk)):
        with m.case(sel) as sm:
            with sm.when(0):
                y <<= big
            with sm.when(1):
                y <<= big
            with sm.default():
                y <<= big
    return m.build()


@pytest.mark.parametrize("engine", ENGINES)
def test_shared_node_in_case_arms(engine):
    steps = [{"sel": 0, "x": 12}, {"sel": 1, "x": 12}, {"sel": 2, "x": 12}, {"sel": 1, "x": 3}, {"sel": 2, "x": 3}]
    assert _run(_case_module(), engine, steps, "y") == [1, 1, 1, 0, 0]


def _after_branch_module():
    m = Module("shared_after_branch")
    clk = m.input()
    a = m.input()
    x = m.input(width=3)
    y = m.output_reg(init=0)
    z = m.output_reg(init=0)
    eq = x == 5  # used inside an arm, then again after the branch
    with m.always(posedge(clk)):
        with m.if_(a):
            y <<= eq
        z <<= eq
    return m.build()


@pytest.mark.parametrize("engine", ENGINES)
def test_shared_node_after_a_branch(engine):
    top = _after_branch_module()
    steps = [{"a": 1, "x": 5}, {"a": 0, "x": 5}, {"a": 0, "x": 1}]
    assert _run(top, engine, steps, "z") == [1, 1, 0]
    assert _run(_after_branch_module(), engine, steps, "y") == [1, 1, 1]
