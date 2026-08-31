"""Unit/regression tests for the fuzzer's `logic`-declared signal generation
(`Signal.use_logic`, `fuzz/_signal_context.py`) and its Verilator cross-check
plumbing (`fuzz/_runner.py`).

See `notes/roadmap.md` ("`logic`-declared signal fuzzing + Verilator
cross-check") and `notes/fuzzer.md` for the design this exercises.
"""

from __future__ import annotations

import random

import pytest

from veriforge.codegen import emit_design
from veriforge.fuzz._module_gen import ModuleGenerator, Strategy
from veriforge.fuzz._signal_context import Signal, SignalContext
from veriforge.model.design import Design
from veriforge.model.nets import NetKind
from veriforge.model.ports import PortDirection
from veriforge.model.variables import VariableKind
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from ..test_sim.engines import ENGINES


class TestSignalUseLogic:
    """`Signal.use_logic` -> as_net/as_variable/as_port emission."""

    def test_default_is_false(self):
        assert Signal(name="a", width=1, signed=False, kind="wire").use_logic is False

    def test_as_net_wire_vs_logic(self):
        plain = Signal(name="w1", width=4, signed=False, kind="wire", use_logic=False)
        logic = Signal(name="w2", width=4, signed=False, kind="wire", use_logic=True)
        assert plain.as_net().kind == NetKind.WIRE
        assert logic.as_net().kind == NetKind.LOGIC

    def test_as_variable_reg_vs_logic(self):
        plain = Signal(name="r1", width=4, signed=False, kind="reg", use_logic=False)
        logic = Signal(name="r2", width=4, signed=False, kind="reg", use_logic=True)
        assert plain.as_variable().kind == VariableKind.REG
        assert logic.as_variable().kind == VariableKind.LOGIC

    def test_as_port_input_logic_sets_net_type(self):
        plain = Signal(name="i1", width=1, signed=False, kind="input", use_logic=False)
        logic = Signal(name="i2", width=1, signed=False, kind="input", use_logic=True)
        assert plain.as_port(PortDirection.INPUT).net_type is None
        assert logic.as_port(PortDirection.INPUT).net_type == "logic"

    def test_as_port_output_logic_replaces_reg_data_type(self):
        """A non-logic output still defaults to `output reg` (matching
        pre-existing behavior for procedurally-writable outputs); a `logic`
        output uses `net_type` instead and must NOT also carry `data_type
        == "reg"` (that would emit the nonsensical `output logic reg`)."""
        plain = Signal(name="o1", width=1, signed=False, kind="output", use_logic=False)
        logic = Signal(name="o2", width=1, signed=False, kind="output", use_logic=True)
        plain_port = plain.as_port(PortDirection.OUTPUT)
        logic_port = logic.as_port(PortDirection.OUTPUT)
        assert plain_port.net_type is None
        assert plain_port.data_type == "reg"
        assert logic_port.net_type == "logic"
        assert logic_port.data_type is None


class TestSignalContextGeneratesLogic:
    """`add_input`/`add_output`/`add_wire`/`add_reg` sometimes (not always,
    not never) produce `use_logic=True` signals."""

    @pytest.mark.parametrize("adder_name", ["add_input", "add_output", "add_wire", "add_reg"])
    def test_mix_of_logic_and_non_logic_across_seeds(self, adder_name):
        ctx = SignalContext()
        adder = getattr(ctx, adder_name)
        rng = random.Random(0)  # noqa: S311
        results = [adder(rng).use_logic for _ in range(200)]
        assert any(results), f"{adder_name} never produced a logic-typed signal in 200 draws"
        assert not all(results), f"{adder_name} always produced a logic-typed signal in 200 draws"


class TestModuleGeneratorEmitsLogic:
    """Integration: `ModuleGenerator` actually threads `use_logic` through
    into emitted `logic` declarations, including the wire->variable
    promotion path (`_module_gen.py`) for a wire later written by an always
    block."""

    def test_some_seeds_emit_logic_declarations(self):
        """Not every seed will roll a logic-typed signal, but across a
        reasonable sample at least one module must contain `logic` -- a
        regression guard against `use_logic` silently becoming dead code
        again (as `as_variable`'s old kind-based branch was before this
        feature)."""
        found = False
        for seed in range(60):
            rng = random.Random(seed)  # noqa: S311
            gen = ModuleGenerator(rng)
            strategy = rng.choice([s for s in Strategy if s is not Strategy.WITH_FUNCTIONS])
            mod = gen.generate(strategy, name=f"m{seed}")
            design = Design()
            design.modules.append(mod)
            if "logic" in emit_design(design):
                found = True
                break
        assert found

    def test_wire_promoted_to_always_written_variable_stays_logic(self):
        """Regression: `_module_gen.py`'s wire->variable promotion (for a
        wire written by an always block) used to hardcode `VariableKind.REG`
        unconditionally, silently dropping `logic`-ness. Seed/strategy below
        deterministically produces a `logic`-declared wire ("w4") promoted
        this way -- confirmed by direct search across seeds 0-300."""
        rng = random.Random(4)  # noqa: S311
        gen = ModuleGenerator(rng)
        mod = gen.generate(Strategy.MULTI_ALWAYS, name="m")
        promoted = next(v for v in mod.variables if v.name == "w4")
        assert promoted.kind == VariableKind.LOGIC


class TestLogicTypedPortsSimulateCorrectly:
    """`logic`-typed ports/nets/variables must simulate identically to their
    `wire`/`reg` counterparts across every available engine -- this is the
    same signal, just with a keyword that now survives parsing (see the
    `_extract_port_declaration` fix this feature depended on)."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_mixed_logic_wire_reg_param_module(self, engine):
        src = """
            module dut #(parameter [3:0] P = 4'd3) (
                input logic clk,
                input wire [3:0] a,
                output logic [3:0] o,
                output reg [3:0] r
            );
                logic [3:0] w1;
                wire [3:0] w2;
                reg [3:0] r2;
                assign w2 = a ^ P;
                assign w1 = w2;
                assign o = w1;
                always @(posedge clk) begin
                    r <= a;
                    r2 <= w1;
                end
            endmodule
        """
        parser = verilog_parser(start="module_declaration")
        tree = parser.build_tree(text=src)
        design = tree_to_design(tree)
        mod = design.modules[0]

        sim = Simulator(mod, engine=engine)
        sim.drive("clk", Value(0, width=1))
        sim.drive("a", Value(0b0110, width=4))
        sim.settle()
        sim.drive("clk", Value(1, width=1))
        sim.settle()

        assert int(sim.read("o")) == (0b0110 ^ 3)
        assert int(sim.read("r")) == 0b0110


class TestHierarchicalStrategyNoSelfReferentialOutput:
    """Regression: `_gen_hierarchical`'s "parent output woven from instance
    wires" step could generate a self-referential assign (`assign o9 =
    o9;` or `assign o9 = {w7, o9};`) since it called `ctx.add_output()`
    for the new output BEFORE building its own RHS expression, and built
    that RHS without the `exclude=` parameter every other strategy passes
    to `pick_readable()`/`expr()`/`leaf()` to prevent exactly this. Not a
    veriforge simulation bug -- but Verilator (correctly) rejects the
    resulting degenerate module as unresolvable circular combinational
    logic, which the fuzzer's Verilator cross-check then miscounted as a
    simulator mismatch. See `notes/roadmap.md`/`notes/known_issues.md`."""

    def test_no_self_referential_assign_across_many_seeds(self):
        import re

        from veriforge.codegen import emit_design
        from veriforge.fuzz._module_gen import ModuleGenerator, Strategy

        for seed in range(500):
            rng = random.Random(seed)  # noqa: S311
            gen = ModuleGenerator(rng)
            design = gen.generate_design(Strategy.HIERARCHICAL)
            text = emit_design(design)
            for m in re.finditer(r"assign\s+(\w+)\s*=\s*(.*?);", text, re.DOTALL):
                lhs, rhs = m.group(1), m.group(2)
                assert not re.search(rf"\b{re.escape(lhs)}\b", rhs), (
                    f"seed {seed}: self-referential assign {m.group(0)!r}"
                )
