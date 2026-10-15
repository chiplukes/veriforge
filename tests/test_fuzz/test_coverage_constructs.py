"""The fuzzer generates the constructs added for coverage on 2026-10-09
(notes/plans/fuzzer_coverage_2026-10.md): the wider operator pool, literal
operands, `N'(e)` casts, integer-type variables and user functions -- and
the result still parses and simulates.
"""

from __future__ import annotations

import random

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.fuzz._module_gen import ModuleGenerator, Strategy
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _sources(n: int, strategy: Strategy | None = None) -> list[str]:
    return [emit_design(ModuleGenerator(random.Random(seed)).generate_design(strategy)) for seed in range(n)]


def test_new_constructs_appear():
    text = "\n".join(_sources(150))
    for needle in (">>>", "<<<", "**", "===", "!==", ">=", "~^", "'sd", "'(", "integer ", "function automatic"):
        assert needle in text, f"no generated module contains {needle!r}"
    assert any(kind in text for kind in ("int ", "byte ", "shortint ", "longint "))


def test_with_functions_strategy_simulates():
    for src in _sources(8, Strategy.WITH_FUNCTIONS):
        design = tree_to_design(verilog_parser(start="source_text").build_tree(src))
        top = next(m for m in design.modules if m.name == "t")
        assert top.functions
        sim = Simulator(top, engine="reference", design=design)
        sim.run(max_time=0)


def test_tasks_impure_functions_and_displays_appear():
    tasks = "\n".join(_sources(20, Strategy.WITH_TASKS))
    assert "task automatic" in tasks
    assert "output" in tasks.split("task automatic", 1)[1]  # an output port
    assert "fimp(" in tasks  # the impure function is called
    clocked = "\n".join(_sources(30, Strategy.CLOCKED_SEQUENTIAL))
    assert '$display("#D' in clocked


def test_with_tasks_strategy_simulates():
    for src in _sources(6, Strategy.WITH_TASKS):
        design = tree_to_design(verilog_parser(start="source_text").build_tree(src))
        top = next(m for m in design.modules if m.name == "t")
        assert top.tasks
        Simulator(top, engine="reference", design=design).run(max_time=0)


def test_atom_ports_and_signed_memories_appear():
    """Added 2026-10-14: atom-typed module ports simulated as 0 and signed
    memory words read as unsigned on every engine, and nothing generated
    either. The ports keep their type through a round trip."""
    import re  # noqa: PLC0415

    text = "\n".join(_sources(120))
    ports = re.findall(r"(?:input|output) (byte|shortint|int|longint|integer|time)\b", text)
    assert {"byte", "int", "time"} <= set(ports)
    memory = "\n".join(_sources(80, Strategy.MEMORY))
    assert re.search(r"reg signed \[[^\]]*\] mem\d", memory)
    assert re.search(r"\b(byte|shortint|int)( unsigned)? mem\d", memory)
    for src in _sources(10):
        assert emit_design(tree_to_design(verilog_parser(start="source_text").build_tree(src))) == src
