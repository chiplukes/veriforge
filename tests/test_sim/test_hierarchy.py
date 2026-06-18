"""Tests for module instantiation / hierarchy support.

Tests the hierarchy flattening pass and simulation of hierarchical designs
across all three engines: reference, VM, and compiled.
"""

import shutil

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, Range, UnaryOp
from veriforge.model.instances import Instance, PortConnection
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    IfStatement,
    NonblockingAssign,
    SensitivityEdge,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.elaborate import flatten_module
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")
_HIER_ENGINES = ("reference", "vm", "compiled") if _has_compiler else ("reference", "vm")


# ── Helper builders ──────────────────────────────────────────────────


def _w(n: int) -> Range:
    return Range(Literal(n - 1), Literal(0))


def _make_inverter() -> Module:
    """module inverter(input [7:0] a, output [7:0] y); assign y = ~a; endmodule"""
    return Module(
        "inverter",
        ports=[
            Port("a", PortDirection.INPUT, width=_w(8)),
            Port("y", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=_w(8)),
            Net("y", NetKind.WIRE, width=_w(8)),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("y"), UnaryOp("~", Identifier("a"))),
        ],
    )


def _make_adder() -> Module:
    """module adder(input [7:0] a, b, output [7:0] sum); assign sum = a + b; endmodule"""
    return Module(
        "adder",
        ports=[
            Port("a", PortDirection.INPUT, width=_w(8)),
            Port("b", PortDirection.INPUT, width=_w(8)),
            Port("sum", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=_w(8)),
            Net("b", NetKind.WIRE, width=_w(8)),
            Net("sum", NetKind.WIRE, width=_w(8)),
        ],
        continuous_assigns=[
            ContinuousAssign(
                Identifier("sum"),
                BinaryOp("+", Identifier("a"), Identifier("b")),
            ),
        ],
    )


def _make_counter() -> Module:
    """module counter(input clk, rst, output reg [7:0] count);
    always @(posedge clk)
        if (rst) count <= 0; else count <= count + 1;
    endmodule"""
    return Module(
        "counter",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("rst", PortDirection.INPUT),
            Port("count", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("rst", NetKind.WIRE),
        ],
        variables=[
            Variable("count", VariableKind.REG, width=_w(8)),
        ],
    )


def _add_counter_logic(mod: Module) -> Module:
    """Add always @(posedge clk) body to a counter module."""
    mod.always_blocks = [
        AlwaysBlock(
            IfStatement(
                Identifier("rst"),
                NonblockingAssign(Identifier("count"), Literal(0, width=8)),
                NonblockingAssign(
                    Identifier("count"),
                    BinaryOp("+", Identifier("count"), Literal(1, width=8)),
                ),
            ),
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        ),
    ]
    return mod


def _make_top_with_inverter() -> tuple[Module, Module, Design]:
    """Top module instantiating an inverter.

    module top(input [7:0] in_a, output [7:0] out_y);
        inverter u1 (.a(in_a), .y(out_y));
    endmodule
    """
    inv = _make_inverter()
    top = Module(
        "top",
        ports=[
            Port("in_a", PortDirection.INPUT, width=_w(8)),
            Port("out_y", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("in_a", NetKind.WIRE, width=_w(8)),
            Net("out_y", NetKind.WIRE, width=_w(8)),
        ],
        instances=[
            Instance(
                "inverter",
                "u1",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("in_a"), is_named=True),
                    PortConnection(port_name="y", expression=Identifier("out_y"), is_named=True),
                ],
            ),
        ],
    )
    design = Design(modules=[top, inv])
    link_instances(design)
    resolve_port_connections(design)

    return top, inv, design


def _make_top_with_adder() -> tuple[Module, Module, Design]:
    """Top module instantiating an adder.

    module top(input [7:0] x, y, output [7:0] result);
        adder u_add (.a(x), .b(y), .sum(result));
    endmodule
    """
    add = _make_adder()
    top = Module(
        "top",
        ports=[
            Port("x", PortDirection.INPUT, width=_w(8)),
            Port("y", PortDirection.INPUT, width=_w(8)),
            Port("result", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("x", NetKind.WIRE, width=_w(8)),
            Net("y", NetKind.WIRE, width=_w(8)),
            Net("result", NetKind.WIRE, width=_w(8)),
        ],
        instances=[
            Instance(
                "adder",
                "u_add",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("x"), is_named=True),
                    PortConnection(port_name="b", expression=Identifier("y"), is_named=True),
                    PortConnection(port_name="sum", expression=Identifier("result"), is_named=True),
                ],
            ),
        ],
    )
    design = Design(modules=[top, add])
    link_instances(design)
    resolve_port_connections(design)

    return top, add, design


def _make_top_with_counter() -> tuple[Module, Module, Design]:
    """Top module instantiating a counter.

    module top(input clk, rst, output [7:0] cnt);
        counter u_cnt (.clk(clk), .rst(rst), .count(cnt));
    endmodule
    """
    ctr = _add_counter_logic(_make_counter())
    top = Module(
        "top",
        ports=[
            Port("clk", PortDirection.INPUT),
            Port("rst", PortDirection.INPUT),
            Port("cnt", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("clk", NetKind.WIRE),
            Net("rst", NetKind.WIRE),
            Net("cnt", NetKind.WIRE, width=_w(8)),
        ],
        instances=[
            Instance(
                "counter",
                "u_cnt",
                port_connections=[
                    PortConnection(port_name="clk", expression=Identifier("clk"), is_named=True),
                    PortConnection(port_name="rst", expression=Identifier("rst"), is_named=True),
                    PortConnection(port_name="count", expression=Identifier("cnt"), is_named=True),
                ],
            ),
        ],
    )
    design = Design(modules=[top, ctr])
    link_instances(design)
    resolve_port_connections(design)

    return top, ctr, design


def _make_two_inverters_chained() -> tuple[Module, Module, Design]:
    """Top with two chained inverters: in → u1.inv → mid → u2.inv → out (double negation).

    module top(input [7:0] in_a, output [7:0] out_y);
        wire [7:0] mid;
        inverter u1 (.a(in_a), .y(mid));
        inverter u2 (.a(mid), .y(out_y));
    endmodule
    """
    inv = _make_inverter()
    top = Module(
        "top",
        ports=[
            Port("in_a", PortDirection.INPUT, width=_w(8)),
            Port("out_y", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("in_a", NetKind.WIRE, width=_w(8)),
            Net("out_y", NetKind.WIRE, width=_w(8)),
            Net("mid", NetKind.WIRE, width=_w(8)),
        ],
        instances=[
            Instance(
                "inverter",
                "u1",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("in_a"), is_named=True),
                    PortConnection(port_name="y", expression=Identifier("mid"), is_named=True),
                ],
            ),
            Instance(
                "inverter",
                "u2",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("mid"), is_named=True),
                    PortConnection(port_name="y", expression=Identifier("out_y"), is_named=True),
                ],
            ),
        ],
    )
    design = Design(modules=[top, inv])
    link_instances(design)
    resolve_port_connections(design)

    return top, inv, design


def _make_nested_hierarchy() -> tuple[Module, Design]:
    """Three-level hierarchy: top → mid → leaf.

    module leaf(input [7:0] a, output [7:0] y); assign y = a + 1; endmodule
    module mid(input [7:0] x, output [7:0] z);
        leaf u_leaf (.a(x), .y(z));
    endmodule
    module top(input [7:0] inp, output [7:0] outp);
        mid u_mid (.x(inp), .z(outp));
    endmodule
    """
    leaf = Module(
        "leaf",
        ports=[
            Port("a", PortDirection.INPUT, width=_w(8)),
            Port("y", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=_w(8)),
            Net("y", NetKind.WIRE, width=_w(8)),
        ],
        continuous_assigns=[
            ContinuousAssign(
                Identifier("y"),
                BinaryOp("+", Identifier("a"), Literal(1, width=8)),
            ),
        ],
    )

    mid = Module(
        "mid",
        ports=[
            Port("x", PortDirection.INPUT, width=_w(8)),
            Port("z", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("x", NetKind.WIRE, width=_w(8)),
            Net("z", NetKind.WIRE, width=_w(8)),
        ],
        instances=[
            Instance(
                "leaf",
                "u_leaf",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("x"), is_named=True),
                    PortConnection(port_name="y", expression=Identifier("z"), is_named=True),
                ],
            ),
        ],
    )

    top = Module(
        "top",
        ports=[
            Port("inp", PortDirection.INPUT, width=_w(8)),
            Port("outp", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("inp", NetKind.WIRE, width=_w(8)),
            Net("outp", NetKind.WIRE, width=_w(8)),
        ],
        instances=[
            Instance(
                "mid",
                "u_mid",
                port_connections=[
                    PortConnection(port_name="x", expression=Identifier("inp"), is_named=True),
                    PortConnection(port_name="z", expression=Identifier("outp"), is_named=True),
                ],
            ),
        ],
    )

    design = Design(modules=[top, mid, leaf])
    link_instances(design)
    resolve_port_connections(design)

    return top, design


def _make_top_with_mixed_logic() -> tuple[Module, Design]:
    """Top module with both local logic AND an instance.

    module top(input [7:0] a, b, output [7:0] sum, inv_a);
        adder u_add (.a(a), .b(b), .sum(sum));
        assign inv_a = ~a;
    endmodule
    """
    add = _make_adder()
    top = Module(
        "top",
        ports=[
            Port("a", PortDirection.INPUT, width=_w(8)),
            Port("b", PortDirection.INPUT, width=_w(8)),
            Port("sum", PortDirection.OUTPUT, width=_w(8)),
            Port("inv_a", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=_w(8)),
            Net("b", NetKind.WIRE, width=_w(8)),
            Net("sum", NetKind.WIRE, width=_w(8)),
            Net("inv_a", NetKind.WIRE, width=_w(8)),
        ],
        continuous_assigns=[
            ContinuousAssign(Identifier("inv_a"), UnaryOp("~", Identifier("a"))),
        ],
        instances=[
            Instance(
                "adder",
                "u_add",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("a"), is_named=True),
                    PortConnection(port_name="b", expression=Identifier("b"), is_named=True),
                    PortConnection(port_name="sum", expression=Identifier("sum"), is_named=True),
                ],
            ),
        ],
    )

    design = Design(modules=[top, add])
    link_instances(design)
    resolve_port_connections(design)

    return top, design


# ── Flatten tests ────────────────────────────────────────────────────


class TestFlatten:
    """Test the hierarchy flattening pass itself."""

    def test_no_instances_returns_same(self):
        """Module without instances is returned unchanged."""
        mod = _make_inverter()
        flat = flatten_module(mod)
        assert flat is mod

    def test_inverter_flat_has_no_instances(self):
        top, _inv, design = _make_top_with_inverter()
        flat = flatten_module(top, design=design)
        assert len(flat.instances) == 0

    def test_inverter_flat_signals(self):
        """Flattened module has prefixed child signals."""
        top, _inv, design = _make_top_with_inverter()
        flat = flatten_module(top, design=design)

        net_names = {n.name for n in flat.nets}
        assert "u1.a" in net_names
        assert "u1.y" in net_names

    def test_inverter_flat_port_wiring(self):
        """Port connections become continuous assigns."""
        top, _inv, design = _make_top_with_inverter()
        flat = flatten_module(top, design=design)

        # Should have wiring assigns: in_a→u1.a, u1.y→out_y
        # Plus the inlined assign from inverter: u1.y = ~u1.a
        assert len(flat.continuous_assigns) >= 3

    def test_inverter_flat_renamed_logic(self):
        """Inlined assign uses prefixed identifiers."""
        top, _inv, design = _make_top_with_inverter()
        flat = flatten_module(top, design=design)

        # Find the inlined assign (the one with ~)
        inlined = [
            ca
            for ca in flat.continuous_assigns
            if isinstance(ca.lhs, Identifier)
            and ca.lhs.name == "u1.y"
            and isinstance(ca.rhs, UnaryOp)
            and ca.rhs.op == "~"
        ]
        assert len(inlined) == 1
        assert inlined[0].rhs.operand.name == "u1.a"

    def test_two_instances_separate_signals(self):
        """Two instances of same module get separate signal namespaces."""
        top, _inv, design = _make_two_inverters_chained()
        flat = flatten_module(top, design=design)

        net_names = {n.name for n in flat.nets}
        assert "u1.a" in net_names
        assert "u1.y" in net_names
        assert "u2.a" in net_names
        assert "u2.y" in net_names

    def test_nested_hierarchy_flattens(self):
        """Three-level hierarchy flattens to single level."""
        top, design = _make_nested_hierarchy()
        flat = flatten_module(top, design=design)

        assert len(flat.instances) == 0
        net_names = {n.name for n in flat.nets}
        # After flattening: mid's leaf instance becomes u_mid.u_leaf.*
        assert "u_mid.u_leaf.a" in net_names or "u_mid.a" in net_names

    def test_original_module_unchanged(self):
        """Flattening doesn't modify the original module."""
        top, _inv, design = _make_top_with_inverter()
        orig_inst_count = len(top.instances)
        flatten_module(top, design=design)
        assert len(top.instances) == orig_inst_count

    def test_mixed_logic_preserves_local(self):
        """Local logic is preserved alongside inlined instance logic."""
        top, design = _make_top_with_mixed_logic()
        flat = flatten_module(top, design=design)

        # Should have: local assign (inv_a = ~a) + wiring + inlined adder logic
        lhs_names = [ca.lhs.name for ca in flat.continuous_assigns if isinstance(ca.lhs, Identifier)]
        assert "inv_a" in lhs_names  # local logic preserved

    def test_unresolved_module_raises(self):
        """Unresolved module raises ValueError."""
        top = Module(
            "top",
            instances=[
                Instance(
                    "nonexistent",
                    "u1",
                    port_connections=[
                        PortConnection(port_name="a", expression=Identifier("x"), is_named=True),
                    ],
                ),
            ],
        )
        with pytest.raises(ValueError, match="nonexistent"):
            flatten_module(top)

    def test_counter_preserves_always_block(self):
        """Sequential always block from submodule is inlined."""
        top, _ctr, design = _make_top_with_counter()
        flat = flatten_module(top, design=design)

        assert len(flat.always_blocks) == 1
        # The always block's sensitivity should reference u_cnt.clk
        ab = flat.always_blocks[0]
        assert len(ab.sensitivity_list) == 1
        edge = ab.sensitivity_list[0]
        assert isinstance(edge.signal, Identifier)
        assert edge.signal.name == "u_cnt.clk"


# ── Simulation tests (reference engine) ─────────────────────────────


class TestHierarchySimReference:
    """Simulate hierarchical designs with the reference engine."""

    def test_inverter_sim(self):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("in_a", Value(0xAA, width=8))
        sim.run(max_time=0)
        assert sim.read("out_y") == Value(0x55, width=8)

    def test_adder_sim(self):
        top, _add, design = _make_top_with_adder()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("x", Value(10, width=8))
        sim.drive("y", Value(20, width=8))
        sim.run(max_time=0)
        assert sim.read("result") == 30

    def test_chained_inverters_identity(self):
        """Double inversion = identity."""
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("in_a", Value(0x42, width=8))
        sim.run(max_time=0)
        assert sim.read("out_y") == Value(0x42, width=8)

    def test_nested_hierarchy_sim(self):
        """Three-level hierarchy: leaf adds 1."""
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("inp", Value(99, width=8))
        sim.run(max_time=0)
        assert sim.read("outp") == 100

    def test_mixed_logic_sim(self):
        """Both local and instance logic work."""
        top, design = _make_top_with_mixed_logic()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("a", Value(0x0F, width=8))
        sim.drive("b", Value(0x01, width=8))
        sim.run(max_time=0)
        assert sim.read("sum") == 0x10
        assert sim.read("inv_a") == Value(0xF0, width=8)

    def test_counter_clocked(self):
        """Counter instance counts on clock edges."""
        top, _ctr, design = _make_top_with_counter()
        sim = Simulator(top, engine="reference", design=design)

        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)

        # Hold reset high for first 15 time units
        sim.drive("rst", Value(1, width=1))
        sim.run(max_time=15)
        assert sim.read("cnt") == 0

        # Release reset and run for a few clock cycles
        sim.drive("rst", Value(0, width=1))
        sim.run(max_time=55)
        # After release, counter should have incremented
        count_val = sim.read("cnt")
        assert count_val.val > 0


# ── Simulation tests (VM engine) ────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestHierarchySimVM:
    """Simulate hierarchical designs with the VM engines."""

    def test_inverter_sim(self, engine):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("in_a", Value(0xAA, width=8))
        sim.run(max_time=0)
        assert sim.read("out_y") == Value(0x55, width=8)

    def test_adder_sim(self, engine):
        top, _add, design = _make_top_with_adder()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("x", Value(10, width=8))
        sim.drive("y", Value(20, width=8))
        sim.run(max_time=0)
        assert sim.read("result") == 30

    def test_chained_inverters(self, engine):
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("in_a", Value(0x42, width=8))
        sim.run(max_time=0)
        assert sim.read("out_y") == Value(0x42, width=8)

    def test_nested_hierarchy(self, engine):
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("inp", Value(99, width=8))
        sim.run(max_time=0)
        assert sim.read("outp") == 100

    def test_mixed_logic(self, engine):
        top, design = _make_top_with_mixed_logic()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("a", Value(0x0F, width=8))
        sim.drive("b", Value(0x01, width=8))
        sim.run(max_time=0)
        assert sim.read("sum") == 0x10
        assert sim.read("inv_a") == Value(0xF0, width=8)


# ── Simulation tests (compiled engine) ──────────────────────────────


@pytest.mark.skipif(not _has_compiler, reason="No C compiler available")
class TestHierarchySimCompiled:
    """Simulate hierarchical designs with the compiled engine."""

    _cython = pytest.importorskip("Cython")

    def test_inverter_sim(self):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("in_a", Value(0xAA, width=8))
        sim.run(max_time=0)
        assert sim.read("out_y") == Value(0x55, width=8)

    def test_adder_sim(self):
        top, _add, design = _make_top_with_adder()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("x", Value(10, width=8))
        sim.drive("y", Value(20, width=8))
        sim.run(max_time=0)
        assert sim.read("result") == 30

    def test_chained_inverters(self):
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("in_a", Value(0x42, width=8))
        sim.run(max_time=0)
        assert sim.read("out_y") == Value(0x42, width=8)

    def test_nested_hierarchy(self):
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("inp", Value(99, width=8))
        sim.run(max_time=0)
        assert sim.read("outp") == 100

    def test_mixed_logic(self):
        top, design = _make_top_with_mixed_logic()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("a", Value(0x0F, width=8))
        sim.drive("b", Value(0x01, width=8))
        sim.run(max_time=0)
        assert sim.read("sum") == 0x10
        assert sim.read("inv_a") == Value(0xF0, width=8)


# ── Cross-validation tests ──────────────────────────────────────────


class TestHierarchyCrossValidation:
    """Cross-validate hierarchy simulation across all engines."""

    ENGINES = ["reference", "vm"]  # noqa: RUF012

    @pytest.fixture(autouse=True)
    def _check_compiler(self):
        if _has_compiler:
            try:
                pytest.importorskip("Cython")
                self.ENGINES = ["reference", "vm", "compiled"]
            except pytest.skip.Exception:
                pass

    def test_inverter_cross(self):
        results = {}
        for eng in self.ENGINES:
            top, _inv, design = _make_top_with_inverter()
            sim = Simulator(top, engine=eng, design=design)
            sim.drive("in_a", Value(0xAA, width=8))
            sim.run(max_time=0)
            results[eng] = sim.read("out_y")
        ref = results["reference"]
        for eng in self.ENGINES[1:]:
            assert results[eng] == ref, f"{eng} disagrees: {results[eng]} != {ref}"

    def test_adder_cross(self):
        results = {}
        for eng in self.ENGINES:
            top, _add, design = _make_top_with_adder()
            sim = Simulator(top, engine=eng, design=design)
            sim.drive("x", Value(37, width=8))
            sim.drive("y", Value(19, width=8))
            sim.run(max_time=0)
            results[eng] = sim.read("result")
        ref = results["reference"]
        for eng in self.ENGINES[1:]:
            assert results[eng] == ref, f"{eng} disagrees: {results[eng]} != {ref}"

    def test_chained_cross(self):
        results = {}
        for eng in self.ENGINES:
            top, _inv, design = _make_two_inverters_chained()
            sim = Simulator(top, engine=eng, design=design)
            sim.drive("in_a", Value(0xBE, width=8))
            sim.run(max_time=0)
            results[eng] = sim.read("out_y")
        ref = results["reference"]
        for eng in self.ENGINES[1:]:
            assert results[eng] == ref, f"{eng} disagrees: {results[eng]} != {ref}"

    def test_nested_cross(self):
        results = {}
        for eng in self.ENGINES:
            top, design = _make_nested_hierarchy()
            sim = Simulator(top, engine=eng, design=design)
            sim.drive("inp", Value(50, width=8))
            sim.run(max_time=0)
            results[eng] = sim.read("outp")
        ref = results["reference"]
        for eng in self.ENGINES[1:]:
            assert results[eng] == ref, f"{eng} disagrees: {results[eng]} != {ref}"

    def test_mixed_cross(self):
        results = {}
        for eng in self.ENGINES:
            top, design = _make_top_with_mixed_logic()
            sim = Simulator(top, engine=eng, design=design)
            sim.drive("a", Value(0x33, width=8))
            sim.drive("b", Value(0x11, width=8))
            sim.run(max_time=0)
            results[eng] = {"sum": sim.read("sum"), "inv_a": sim.read("inv_a")}
        ref = results["reference"]
        for eng in self.ENGINES[1:]:
            for sig in ("sum", "inv_a"):
                assert results[eng][sig] == ref[sig], f"{eng} disagrees on {sig}: {results[eng][sig]} != {ref[sig]}"


# ── Hierarchical signal access tests ────────────────────────────────


class TestHierarchicalSignalAccess:
    """Test hierarchical signal access API: signals(), hierarchy(), signal() errors."""

    def test_read_internal_signal_via_dotted_name(self):
        """Access submodule internal signal using hierarchical name."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("in_a", Value(0xAA, width=8))
        sim.run(max_time=0)
        # u1.a should equal in_a (input port wired)
        handle = sim.signal("u1.a")
        assert handle.value == Value(0xAA, width=8)
        # u1.y should equal out_y (output port wired)
        assert sim.signal("u1.y").value == Value(0x55, width=8)

    def test_nested_hierarchy_deep_signal(self):
        """Access deeply nested signal using multi-level dotted name."""
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("inp", Value(99, width=8))
        sim.run(max_time=0)
        # u_mid.u_leaf.a should be the input to the leaf module
        val = sim.signal("u_mid.u_leaf.a").value
        assert val == Value(99, width=8)
        # u_mid.u_leaf.y should be output = a + 1
        val = sim.signal("u_mid.u_leaf.y").value
        assert val == Value(100, width=8)

    def test_signals_returns_sorted_list(self):
        """signals() returns all signal names sorted."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        names = sim.signals()
        assert isinstance(names, list)
        assert names == sorted(names)
        # Should contain both top-level and hierarchical signals
        assert "in_a" in names
        assert "out_y" in names
        assert "u1.a" in names
        assert "u1.y" in names

    def test_signals_with_prefix_filter(self):
        """signals(pattern) filters by prefix."""
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine="reference", design=design)
        u1_signals = sim.signals("u1.")
        assert all(n.startswith("u1.") for n in u1_signals)
        u2_signals = sim.signals("u2.")
        assert all(n.startswith("u2.") for n in u2_signals)
        # u1 and u2 should have same set of local names
        u1_local = {n.removeprefix("u1.") for n in u1_signals}
        u2_local = {n.removeprefix("u2.") for n in u2_signals}
        assert u1_local == u2_local

    def test_signals_empty_prefix_returns_all(self):
        """signals('') returns all signals (everything starts with empty string)."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        assert sim.signals("") == sim.signals()

    def test_signals_no_match(self):
        """signals() with non-matching prefix returns empty list."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        assert sim.signals("nonexistent.") == []

    def test_hierarchy_single_instance(self):
        """hierarchy() returns instance→module mapping for single instance."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        h = sim.hierarchy()
        assert h == {"u1": "inverter"}

    def test_hierarchy_two_instances(self):
        """hierarchy() maps each instance path to its module name."""
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine="reference", design=design)
        h = sim.hierarchy()
        assert h == {"u1": "inverter", "u2": "inverter"}

    def test_hierarchy_nested(self):
        """hierarchy() includes all levels of nested hierarchy."""
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine="reference", design=design)
        h = sim.hierarchy()
        assert "u_mid" in h
        assert h["u_mid"] == "mid"
        assert "u_mid.u_leaf" in h
        assert h["u_mid.u_leaf"] == "leaf"

    def test_hierarchy_no_instances(self):
        """hierarchy() returns empty dict for module without instances."""
        mod = _make_inverter()
        sim = Simulator(mod, engine="reference")
        assert sim.hierarchy() == {}

    def test_signal_unknown_raises_key_error(self):
        """signal() with unknown name raises KeyError with helpful message."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        with pytest.raises(KeyError, match="not found"):
            sim.signal("nonexistent")

    def test_signal_typo_suggests_close_match(self):
        """signal() with typo suggests close matches."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        with pytest.raises(KeyError, match="Did you mean"):
            sim.signal("in_b")  # close to "in_a"

    def test_hierarchy_returns_copy(self):
        """hierarchy() returns a copy, not the internal dict."""
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="reference", design=design)
        h1 = sim.hierarchy()
        h2 = sim.hierarchy()
        assert h1 == h2
        h1["extra"] = "should not affect h2"
        assert "extra" not in sim.hierarchy()


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestHierarchicalSignalAccessVM:
    """Hierarchical signal access using the VM engines."""

    def test_read_internal_signal(self, engine):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("in_a", Value(0xAA, width=8))
        sim.run(max_time=0)
        assert sim.signal("u1.a").value == Value(0xAA, width=8)
        assert sim.signal("u1.y").value == Value(0x55, width=8)

    def test_nested_deep_signal(self, engine):
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("inp", Value(99, width=8))
        sim.run(max_time=0)
        assert sim.signal("u_mid.u_leaf.a").value == Value(99, width=8)
        assert sim.signal("u_mid.u_leaf.y").value == Value(100, width=8)

    def test_signals_listing(self, engine):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine=engine, design=design)
        names = sim.signals()
        assert "u1.a" in names
        assert "u1.y" in names

    def test_signals_prefix_filter(self, engine):
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine=engine, design=design)
        assert all(n.startswith("u1.") for n in sim.signals("u1."))

    def test_hierarchy(self, engine):
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine=engine, design=design)
        h = sim.hierarchy()
        assert h["u_mid"] == "mid"
        assert h["u_mid.u_leaf"] == "leaf"

    def test_signal_unknown_raises(self, engine):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine=engine, design=design)
        with pytest.raises(KeyError, match="not found"):
            sim.signal("nonexistent")


@pytest.mark.skipif(not _has_compiler, reason="No C compiler available")
class TestHierarchicalSignalAccessCompiled:
    """Hierarchical signal access using the compiled engine."""

    _cython = pytest.importorskip("Cython")

    def test_read_internal_signal(self):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("in_a", Value(0xAA, width=8))
        sim.run(max_time=0)
        assert sim.signal("u1.a").value == Value(0xAA, width=8)
        assert sim.signal("u1.y").value == Value(0x55, width=8)

    def test_nested_deep_signal(self):
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine="compiled", design=design)
        sim.drive("inp", Value(99, width=8))
        sim.run(max_time=0)
        assert sim.signal("u_mid.u_leaf.a").value == Value(99, width=8)
        assert sim.signal("u_mid.u_leaf.y").value == Value(100, width=8)

    def test_signals_listing(self):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="compiled", design=design)
        names = sim.signals()
        assert "u1.a" in names
        assert "u1.y" in names

    def test_signals_prefix_filter(self):
        top, _inv, design = _make_two_inverters_chained()
        sim = Simulator(top, engine="compiled", design=design)
        assert all(n.startswith("u1.") for n in sim.signals("u1."))

    def test_hierarchy(self):
        top, design = _make_nested_hierarchy()
        sim = Simulator(top, engine="compiled", design=design)
        h = sim.hierarchy()
        assert h["u_mid"] == "mid"
        assert h["u_mid.u_leaf"] == "leaf"

    def test_signal_unknown_raises(self):
        top, _inv, design = _make_top_with_inverter()
        sim = Simulator(top, engine="compiled", design=design)
        with pytest.raises(KeyError, match="not found"):
            sim.signal("nonexistent")


_VERILOG_GENERATED_STRUCT_ARRAY_HIER = """\
module gen_struct_array_dut;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    for (genvar i = 0; i < 2; i = i + 1) begin : gen_outs
        item_t items [1:0];

        if (i == 0) begin : gen_init0
            initial begin
                items[0].data = 8'hA5;
                items[0].tag = 4'h3;
                items[1].data = 8'h5A;
                items[1].tag = 4'hC;
            end
        end else begin : gen_init1
            initial begin
                items[0].data = 8'h11;
                items[0].tag = 4'h4;
                items[1].data = 8'h22;
                items[1].tag = 4'h6;
            end
        end
    end
endmodule

module gen_struct_array_tb;
    gen_struct_array_dut dut();
endmodule
"""


@pytest.mark.parametrize("engine", _HIER_ENGINES)
def test_generated_struct_array_hierarchical_api_cross_engine(engine, tmp_path):
    """sim.read() should resolve generated local struct-array fields by hierarchical name."""
    from veriforge.project import parse_files

    src = tmp_path / "generated_struct_array_hier.sv"
    src.write_text(_VERILOG_GENERATED_STRUCT_ARRAY_HIER)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("gen_struct_array_tb")
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)

    assert sim.read("dut.gen_outs[0].items[0].data").val == 0xA5
    assert sim.read("dut.gen_outs[0].items[1].tag").val == 0xC
    assert sim.read("dut.gen_outs[1].items[0].data").val == 0x11
    assert sim.read("dut.gen_outs[1].items[1].tag").val == 0x6


@pytest.mark.parametrize("engine", _HIER_ENGINES)
def test_generated_struct_array_hierarchical_signal_handle_cross_engine(engine, tmp_path):
    """sim.signal() should expose generated local struct-array fields as readable handles."""
    from veriforge.project import parse_files

    src = tmp_path / "generated_struct_array_hier_signal.sv"
    src.write_text(_VERILOG_GENERATED_STRUCT_ARRAY_HIER)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("gen_struct_array_tb")
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)

    data_handle = sim.signal("dut.gen_outs[0].items[0].data")
    tag_handle = sim.signal("dut.gen_outs[1].items[1].tag")

    assert data_handle.width == 8
    assert data_handle.value.val == 0xA5
    assert tag_handle.width == 4
    assert tag_handle.value.val == 0x6


@pytest.mark.parametrize("engine", _HIER_ENGINES)
def test_generated_struct_array_hierarchical_signals_listing_cross_engine(engine, tmp_path):
    """sim.signals() should list generated local struct-array elements and fields."""
    from veriforge.project import parse_files

    src = tmp_path / "generated_struct_array_hier_signals.sv"
    src.write_text(_VERILOG_GENERATED_STRUCT_ARRAY_HIER)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("gen_struct_array_tb")
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)

    names = sim.signals("dut.gen_outs[0].")

    assert "dut.gen_outs[0].items[0]" in names
    assert "dut.gen_outs[0].items[0].data" in names
    assert "dut.gen_outs[0].items[0].tag" in names
    assert "dut.gen_outs[0].items[1]" in names
    assert "dut.gen_outs[0].items[1].data" in names
    assert "dut.gen_outs[0].items[1].tag" in names


# ── Hierarchical identifier evaluation in $display ──────────────────


class TestHierarchicalIdentifierDisplay:
    """Test that $display can read submodule signals via hierarchical refs."""

    _VERILOG = """\
module counter_mod(input clk, input rst);
    reg [7:0] count;
    always @(posedge clk) begin
        if (rst)
            count <= 0;
        else
            count <= count + 1;
    end
endmodule

module testbench;
    reg clk = 0;
    reg rst = 1;
    always #5 clk = ~clk;
    counter_mod uut (.clk(clk), .rst(rst));
    initial begin
        @(posedge clk);
        rst <= 0;
        repeat (4) @(posedge clk);
        $finish;
    end
    always @(posedge clk) begin
        $display("TRACE t=%0d count=%0d", $time, uut.count);
    end
endmodule
"""

    @pytest.mark.parametrize("engine", ["reference", "vm"])
    def test_display_hierarchical_ref(self, engine, tmp_path):
        """$display using uut.count should resolve to submodule signal."""
        from veriforge.project import parse_files

        src = tmp_path / "test.v"
        src.write_text(self._VERILOG)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("testbench")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=100)
        traces = [line for line in sim.display_output if line.startswith("TRACE")]
        assert len(traces) >= 3, f"Expected >=3 traces, got {len(traces)}"
        # After rst deasserts, count should become 0, then 1, 2, ...
        vals = []
        for t in traces:
            # Parse "TRACE t=NN count=VV"
            for part in t.split():
                if part.startswith("count="):
                    vals.append(part.split("=", 1)[1])
        # After rst deasserts, count should become 0 (hierarchical ref resolved)
        assert "0" in vals, f"Expected count=0 after reset, got {vals}"
