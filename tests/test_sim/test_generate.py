"""Tests for generate construct elaboration support.

Tests the generate elaboration pass (generate-for, generate-if, generate-case)
and simulation of designs using generate constructs across all three engines.
"""

import shutil

import pytest

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, BitSelect, Identifier, Literal, Range, UnaryOp
from veriforge.model.generate import (
    GenerateBlock,
    GenerateCase,
    GenerateCaseItem,
    GenerateFor,
    GenerateIf,
    GenvarDecl,
)
from veriforge.model.instances import Instance, PortConnection
from veriforge.model.nets import Net, NetKind
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.sim.elaborate import elaborate_generates
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")
_CROSS_ENGINES = ("reference", "vm", "compiled") if _has_compiler else ("reference", "vm")


# ── Helper builders ──────────────────────────────────────────────────


def _w(n: int) -> Range:
    return Range(Literal(n - 1), Literal(0))


def _make_generate_for_assign() -> Module:
    """Module with generate-for producing 4 continuous assigns.

    Equivalent Verilog:
        module gen_for_assign(input [3:0] in, output [3:0] out);
          generate
            for (genvar i = 0; i < 4; i = i + 1) begin : gen
              assign out[i] = ~in[i];
            end
          endgenerate
        endmodule
    """
    m = Module(
        "gen_for_assign",
        ports=[
            Port("in_sig", PortDirection.INPUT, width=_w(4)),
            Port("out_sig", PortDirection.OUTPUT, width=_w(4)),
        ],
        nets=[
            Net("in_sig", NetKind.WIRE, width=_w(4)),
            Net("out_sig", NetKind.WIRE, width=_w(4)),
        ],
    )

    body = GenerateBlock(
        name="gen",
        items=[
            ContinuousAssign(
                BitSelect(Identifier("out_sig"), Identifier("i")),
                UnaryOp("~", BitSelect(Identifier("in_sig"), Identifier("i"))),
            ),
        ],
    )

    gen_for = GenerateFor(
        genvar="i",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("i"), Literal(4)),
        update=BinaryOp("+", Identifier("i"), Literal(1)),
        body=body,
        update_op="=",
    )
    m.generate_blocks.append(gen_for)
    return m


def _make_generate_for_with_local_signals() -> Module:
    """Module with generate-for that declares local wire inside the loop.

    Equivalent Verilog:
        module gen_for_local(input [3:0] in, output [3:0] out);
          generate
            for (genvar i = 0; i < 4; i = i + 1) begin : gen
              wire temp;
              assign temp = in[i];
              assign out[i] = ~temp;
            end
          endgenerate
        endmodule
    """
    m = Module(
        "gen_for_local",
        ports=[
            Port("in_sig", PortDirection.INPUT, width=_w(4)),
            Port("out_sig", PortDirection.OUTPUT, width=_w(4)),
        ],
        nets=[
            Net("in_sig", NetKind.WIRE, width=_w(4)),
            Net("out_sig", NetKind.WIRE, width=_w(4)),
        ],
    )

    body = GenerateBlock(
        name="gen",
        items=[
            Net("temp", NetKind.WIRE),
            ContinuousAssign(
                Identifier("temp"),
                BitSelect(Identifier("in_sig"), Identifier("i")),
            ),
            ContinuousAssign(
                BitSelect(Identifier("out_sig"), Identifier("i")),
                UnaryOp("~", Identifier("temp")),
            ),
        ],
    )

    gen_for = GenerateFor(
        genvar="i",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("i"), Literal(4)),
        update=BinaryOp("+", Identifier("i"), Literal(1)),
        body=body,
        update_op="=",
    )
    m.generate_blocks.append(gen_for)
    return m


def _make_generate_if_true() -> Module:
    """Module with generate-if where condition is true (USE_INVERT=1).

    Equivalent Verilog:
        module gen_if(input [7:0] a, output [7:0] y);
          parameter USE_INVERT = 1;
          generate
            if (USE_INVERT) begin
              assign y = ~a;
            end else begin
              assign y = a;
            end
          endgenerate
        endmodule
    """
    m = Module(
        "gen_if",
        ports=[
            Port("a", PortDirection.INPUT, width=_w(8)),
            Port("y", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=_w(8)),
            Net("y", NetKind.WIRE, width=_w(8)),
        ],
    )
    m.parameters.append(Parameter("USE_INVERT", default_value=Literal(1)))

    then_body = GenerateBlock(
        items=[
            ContinuousAssign(Identifier("y"), UnaryOp("~", Identifier("a"))),
        ]
    )
    else_body = GenerateBlock(
        items=[
            ContinuousAssign(Identifier("y"), Identifier("a")),
        ]
    )

    gen_if = GenerateIf(Identifier("USE_INVERT"), then_body, else_body)
    m.generate_blocks.append(gen_if)
    return m


def _make_generate_if_false() -> Module:
    """Module with generate-if where condition is false (USE_INVERT=0)."""
    m = _make_generate_if_true()
    m.parameters[0] = Parameter("USE_INVERT", default_value=Literal(0))
    return m


def _make_generate_case() -> Module:
    """Module with generate-case selecting operation based on parameter.

    parameter OP_MODE = 1;
    generate
      case (OP_MODE)
        0: assign y = a;      // pass-through
        1: assign y = ~a;     // invert
        default: assign y = 0;
      endcase
    endgenerate
    """
    m = Module(
        "gen_case",
        ports=[
            Port("a", PortDirection.INPUT, width=_w(8)),
            Port("y", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=_w(8)),
            Net("y", NetKind.WIRE, width=_w(8)),
        ],
    )
    m.parameters.append(Parameter("OP_MODE", default_value=Literal(1)))

    gen_case = GenerateCase(
        expression=Identifier("OP_MODE"),
        items=[
            GenerateCaseItem(
                values=[Literal(0)],
                body=GenerateBlock(
                    items=[
                        ContinuousAssign(Identifier("y"), Identifier("a")),
                    ]
                ),
            ),
            GenerateCaseItem(
                values=[Literal(1)],
                body=GenerateBlock(
                    items=[
                        ContinuousAssign(Identifier("y"), UnaryOp("~", Identifier("a"))),
                    ]
                ),
            ),
            GenerateCaseItem(
                is_default=True,
                body=GenerateBlock(
                    items=[
                        ContinuousAssign(Identifier("y"), Literal(0, width=8)),
                    ]
                ),
            ),
        ],
    )
    m.generate_blocks.append(gen_case)
    return m


def _make_generate_for_with_param() -> Module:
    """Module using parameter for loop bound.

    parameter N = 4;
    generate
      for (genvar i = 0; i < N; i = i + 1) begin : gen
        assign out[i] = in[i];
      end
    endgenerate
    """
    m = Module(
        "gen_param",
        ports=[
            Port("in_sig", PortDirection.INPUT, width=_w(8)),
            Port("out_sig", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("in_sig", NetKind.WIRE, width=_w(8)),
            Net("out_sig", NetKind.WIRE, width=_w(8)),
        ],
    )
    m.parameters.append(Parameter("N", default_value=Literal(4)))

    body = GenerateBlock(
        name="gen",
        items=[
            ContinuousAssign(
                BitSelect(Identifier("out_sig"), Identifier("i")),
                BitSelect(Identifier("in_sig"), Identifier("i")),
            ),
        ],
    )
    gen_for = GenerateFor(
        genvar="i",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("i"), Identifier("N")),
        update=BinaryOp("+", Identifier("i"), Literal(1)),
        body=body,
        update_op="=",
    )
    m.generate_blocks.append(gen_for)
    return m


def _make_generate_for_instance() -> tuple[Module, Design]:
    """Module with generate-for that instantiates submodules.

    module inverter(input [7:0] a, output [7:0] y);
      assign y = ~a;
    endmodule

    module top(input [7:0] in0, in1, output [7:0] out0, out1);
      generate
        for (genvar i = 0; i < 2; i = i + 1) begin : gen
          inverter inv(.a(in_i), .out(out_i));  // conceptual
        end
      endgenerate
    endmodule

    For simplicity, we wire ports using separate signals.
    """
    inv = Module(
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

    top = Module(
        "top",
        ports=[
            Port("in0", PortDirection.INPUT, width=_w(8)),
            Port("in1", PortDirection.INPUT, width=_w(8)),
            Port("out0", PortDirection.OUTPUT, width=_w(8)),
            Port("out1", PortDirection.OUTPUT, width=_w(8)),
        ],
        nets=[
            Net("in0", NetKind.WIRE, width=_w(8)),
            Net("in1", NetKind.WIRE, width=_w(8)),
            Net("out0", NetKind.WIRE, width=_w(8)),
            Net("out1", NetKind.WIRE, width=_w(8)),
        ],
    )

    # Build generate-for that instantiates 2 inverters
    # Each iteration: inverter inv(.a(in<i>), .y(out<i>));
    # We use generate-if within the for body to select ports based on i
    # Actually, simpler: use the genvar in the port names via separate wires
    # But that's complex with genvar substitution. Let's use a simpler approach:
    # Two separate wires per iteration, connected at module top level.

    # Approach: each iteration creates an instance. The port connections
    # reference signals that exist at the module level. We use bit-selects
    # or direct signals. Since genvar appears in the signal names isn't standard,
    # let's just create 2 inverter instances directly in the generate body,
    # each with different connections.
    #
    # Actually, the typical pattern is:
    #   for (genvar i = 0; i < 2; i++) begin : gen
    #     inverter inv(.a(data_in[i]), .y(data_out[i]));
    #   end
    # But this requires array port connections with genvar indices.
    # For simplicity, wire in0/out0 for i=0 and in1/out1 for i=1 using generate-if inside for.

    # Simplest: use generate-if with genvar to select signals
    body_items = []

    # if (i == 0) inverter inv(.a(in0), .y(out0));
    # else if (i == 1) inverter inv(.a(in1), .y(out1));
    then_0 = GenerateBlock(
        items=[
            Instance(
                "inverter",
                "inv",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("in0"), is_named=True),
                    PortConnection(port_name="y", expression=Identifier("out0"), is_named=True),
                ],
            ),
        ]
    )
    then_1 = GenerateBlock(
        items=[
            Instance(
                "inverter",
                "inv",
                port_connections=[
                    PortConnection(port_name="a", expression=Identifier("in1"), is_named=True),
                    PortConnection(port_name="y", expression=Identifier("out1"), is_named=True),
                ],
            ),
        ]
    )
    body_items.append(
        GenerateIf(
            BinaryOp("==", Identifier("i"), Literal(0)),
            then_0,
            GenerateBlock(
                items=[
                    GenerateIf(
                        BinaryOp("==", Identifier("i"), Literal(1)),
                        then_1,
                    ),
                ]
            ),
        )
    )

    body = GenerateBlock(name="gen", items=body_items)
    gen_for = GenerateFor(
        genvar="i",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("i"), Literal(2)),
        update=BinaryOp("+", Identifier("i"), Literal(1)),
        body=body,
        update_op="=",
    )
    top.generate_blocks.append(gen_for)

    design = Design(modules=[inv, top])
    from veriforge.analysis.resolver import link_instances  # noqa: PLC0415

    link_instances(design)

    return top, design


def _make_generate_for_increment() -> Module:
    """Module using i++ update syntax.

    generate
      for (genvar i = 0; i < 4; i++) begin : gen
        assign out[i] = in[i];
      end
    endgenerate
    """
    m = Module(
        "gen_inc",
        ports=[
            Port("in_sig", PortDirection.INPUT, width=_w(4)),
            Port("out_sig", PortDirection.OUTPUT, width=_w(4)),
        ],
        nets=[
            Net("in_sig", NetKind.WIRE, width=_w(4)),
            Net("out_sig", NetKind.WIRE, width=_w(4)),
        ],
    )

    body = GenerateBlock(
        name="gen",
        items=[
            ContinuousAssign(
                BitSelect(Identifier("out_sig"), Identifier("i")),
                BitSelect(Identifier("in_sig"), Identifier("i")),
            ),
        ],
    )
    gen_for = GenerateFor(
        genvar="i",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("i"), Literal(4)),
        update=None,
        body=body,
        update_op="post++",
    )
    m.generate_blocks.append(gen_for)
    return m


def _make_nested_generate() -> Module:
    """Module with nested generate-for (2D array of assigns).

    generate
      for (genvar i = 0; i < 2; i = i + 1) begin : outer
        for (genvar j = 0; j < 2; j = j + 1) begin : inner
          // Creates 4 assigns total
          assign out[i*2+j] = in[i*2+j];
        end
      end
    endgenerate
    """
    m = Module(
        "gen_nested",
        ports=[
            Port("in_sig", PortDirection.INPUT, width=_w(4)),
            Port("out_sig", PortDirection.OUTPUT, width=_w(4)),
        ],
        nets=[
            Net("in_sig", NetKind.WIRE, width=_w(4)),
            Net("out_sig", NetKind.WIRE, width=_w(4)),
        ],
    )

    idx_expr = BinaryOp("+", BinaryOp("*", Identifier("i"), Literal(2)), Identifier("j"))
    idx_expr2 = BinaryOp("+", BinaryOp("*", Identifier("i"), Literal(2)), Identifier("j"))

    inner_body = GenerateBlock(
        name="inner",
        items=[
            ContinuousAssign(
                BitSelect(Identifier("out_sig"), idx_expr),
                BitSelect(Identifier("in_sig"), idx_expr2),
            ),
        ],
    )
    inner_for = GenerateFor(
        genvar="j",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("j"), Literal(2)),
        update=BinaryOp("+", Identifier("j"), Literal(1)),
        body=inner_body,
        update_op="=",
    )

    outer_body = GenerateBlock(
        name="outer",
        items=[inner_for],
    )
    outer_for = GenerateFor(
        genvar="i",
        init_value=Literal(0),
        condition=BinaryOp("<", Identifier("i"), Literal(2)),
        update=BinaryOp("+", Identifier("i"), Literal(1)),
        body=outer_body,
        update_op="=",
    )
    m.generate_blocks.append(outer_for)
    return m


# ── TestElaborateGenerates ────────────────────────────────────────────


class TestElaborateGenerates:
    """Unit tests for the generate elaboration pass."""

    def test_no_generates_returns_same(self):
        """Module without generates returns the same object."""
        m = Module("empty", ports=[])
        result = elaborate_generates(m)
        assert result is m

    def test_generate_for_unrolls_assigns(self):
        """Generate-for produces 4 continuous assigns."""
        m = _make_generate_for_assign()
        result = elaborate_generates(m)
        assert result is not m
        assert len(result.generate_blocks) == 0
        # Original 0 assigns + 4 generated assigns
        assert len(result.continuous_assigns) == 4

    def test_generate_for_genvar_substitution(self):
        """Genvar references are replaced with literal values."""
        m = _make_generate_for_assign()
        result = elaborate_generates(m)
        # Check that the assigns have literal indices, not Identifier("i")
        for ca in result.continuous_assigns:
            for node in ca.walk():
                if isinstance(node, Identifier):
                    assert node.name != "i", "Genvar 'i' was not substituted"

    def test_generate_for_local_signals_scoped(self):
        """Local signals inside generate-for get scoped names."""
        m = _make_generate_for_with_local_signals()
        result = elaborate_generates(m)
        # 4 iterations, each declares a local "temp" wire
        local_nets = [n for n in result.nets if "temp" in n.name]
        assert len(local_nets) == 4
        expected_names = {f"gen[{i}].temp" for i in range(4)}
        actual_names = {n.name for n in local_nets}
        assert actual_names == expected_names

    def test_generate_for_local_signals_renamed_in_assigns(self):
        """Identifiers referencing local signals are renamed with scope prefix."""
        m = _make_generate_for_with_local_signals()
        result = elaborate_generates(m)
        # Each iteration produces 2 assigns, total 8
        assert len(result.continuous_assigns) == 8
        # Check that "temp" identifiers are scoped
        for ca in result.continuous_assigns:
            for node in ca.walk():
                if isinstance(node, Identifier) and "temp" in node.name:
                    assert node.name.startswith("gen["), f"Local signal not scoped: {node.name}"

    def test_generate_for_embedded_struct_indices_resolved(self, parser):
        """Embedded ``[genvar]`` indices inside struct-field identifier text should be concretized."""
        source = """module top(input logic [1:0] in_sig, output logic [1:0] out_sig);
typedef struct packed {
    logic data;
} item_t;
item_t items[2];
generate
    for (genvar i = 0; i < 2; i = i + 1) begin : gen
        assign items[i].data = in_sig[i];
        assign out_sig[i] = items[i].data;
    end
endgenerate
endmodule"""
        from veriforge.transforms.tree_to_model import tree_to_design

        tree = parser.build_tree(source)
        design = tree_to_design(tree, source_file="test_generate_embedded_indices.sv")
        top = design.modules[0]
        result = elaborate_generates(top)

        item_refs = {
            ".".join([*node.hierarchy, node.name]) if node.hierarchy else node.name
            for assign in result.continuous_assigns
            for node in assign.walk()
            if isinstance(node, Identifier) and node.hierarchy and node.hierarchy[0].startswith("items[")
        }
        assert item_refs == {"items[0].data", "items[1].data"}
        assert all("[i]" not in name for name in item_refs)

    def test_generate_if_true_branch(self):
        """Generate-if with true condition selects then branch."""
        m = _make_generate_if_true()
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 1
        ca = result.continuous_assigns[0]
        # Should be: assign y = ~a (inversion)
        assert isinstance(ca.rhs, UnaryOp)
        assert ca.rhs.op == "~"

    def test_generate_if_false_branch(self):
        """Generate-if with false condition selects else branch."""
        m = _make_generate_if_false()
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 1
        ca = result.continuous_assigns[0]
        # Should be: assign y = a (pass-through)
        assert isinstance(ca.rhs, Identifier)
        assert ca.rhs.name == "a"

    def test_generate_case_match(self):
        """Generate-case selects the matching case item."""
        m = _make_generate_case()
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 1
        ca = result.continuous_assigns[0]
        # OP_MODE=1 selects inversion
        assert isinstance(ca.rhs, UnaryOp)
        assert ca.rhs.op == "~"

    def test_generate_case_default(self):
        """Generate-case falls through to default when no match."""
        m = _make_generate_case()
        # Set OP_MODE to 99 (no explicit match)
        m.parameters[0] = Parameter("OP_MODE", default_value=Literal(99))
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 1
        ca = result.continuous_assigns[0]
        # Default: assign y = 0
        assert isinstance(ca.rhs, Literal)

    def test_generate_for_with_param_bound(self):
        """Generate-for uses parameter value for loop bound."""
        m = _make_generate_for_with_param()
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 4

    def test_generate_for_increment_op(self):
        """Generate-for with i++ update syntax."""
        m = _make_generate_for_increment()
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 4

    def test_genvar_decl_ignored(self):
        """GenvarDecl is a no-op during elaboration."""
        m = Module("gv", ports=[])
        m.generate_blocks.append(GenvarDecl(names=["i", "j"]))
        result = elaborate_generates(m)
        assert len(result.continuous_assigns) == 0

    def test_generate_for_instances_promoted(self):
        """Generate-for with instances adds them to module.instances."""
        top, _design = _make_generate_for_instance()
        result = elaborate_generates(top)
        # 2 iterations, each produces 1 instance
        assert len(result.instances) == 2
        # Instance names should be scoped
        names = {inst.instance_name for inst in result.instances}
        assert "gen[0].inv" in names
        assert "gen[1].inv" in names

    def test_nested_generate_for(self):
        """Nested generate-for (2D) produces correct number of assigns."""
        m = _make_nested_generate()
        result = elaborate_generates(m)
        # 2 outer * 2 inner = 4 assigns
        assert len(result.continuous_assigns) == 4

    def test_unnamed_block_gets_genblk_name(self):
        """Unnamed generate-for body gets auto-generated genblk name."""
        m = Module("unnamed", ports=[])
        m.nets.append(Net("in_sig", NetKind.WIRE, width=_w(2)))
        m.nets.append(Net("out_sig", NetKind.WIRE, width=_w(2)))

        body = GenerateBlock(
            items=[  # no name!
                Net("temp", NetKind.WIRE),
                ContinuousAssign(Identifier("temp"), BitSelect(Identifier("in_sig"), Identifier("i"))),
            ]
        )
        gen_for = GenerateFor(
            genvar="i",
            init_value=Literal(0),
            condition=BinaryOp("<", Identifier("i"), Literal(2)),
            update=BinaryOp("+", Identifier("i"), Literal(1)),
            body=body,
            update_op="=",
        )
        m.generate_blocks.append(gen_for)
        result = elaborate_generates(m)
        local_nets = [n for n in result.nets if "temp" in n.name]
        assert len(local_nets) == 2
        # Should use genblk1[0], genblk1[1]
        assert any("genblk" in n.name for n in local_nets)

    def test_elaborate_preserves_original(self):
        """elaborate_generates does not mutate the original module."""
        m = _make_generate_for_assign()
        orig_gen_count = len(m.generate_blocks)
        orig_assign_count = len(m.continuous_assigns)
        _ = elaborate_generates(m)
        assert len(m.generate_blocks) == orig_gen_count
        assert len(m.continuous_assigns) == orig_assign_count

    def test_param_override(self):
        """Parameter override changes loop bound."""
        m = _make_generate_for_with_param()
        result = elaborate_generates(m, param_values={"N": 2})
        assert len(result.continuous_assigns) == 2


# ── TestGenerateSimReference ──────────────────────────────────────────


class TestGenerateSimReference:
    """Simulate generate-based designs with the reference engine."""

    def test_generate_for_invert_bits(self):
        """Generate-for inverting each bit."""
        m = _make_generate_for_assign()
        sim = Simulator(m, engine="reference")
        sim.drive("in_sig", Value(0b1010, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        # ~0b1010 masked to 4 bits = 0b0101 = 5
        assert result.val & 0xF == 0b0101

    def test_generate_if_invert(self):
        """Generate-if selecting inversion."""
        m = _make_generate_if_true()
        sim = Simulator(m, engine="reference")
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0x55

    def test_generate_if_passthrough(self):
        """Generate-if selecting pass-through."""
        m = _make_generate_if_false()
        sim = Simulator(m, engine="reference")
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0xAA

    def test_generate_case_invert(self):
        """Generate-case selecting inversion (OP_MODE=1)."""
        m = _make_generate_case()
        sim = Simulator(m, engine="reference")
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0x55

    def test_generate_for_with_local_signals(self):
        """Generate-for with local signals simulates correctly."""
        m = _make_generate_for_with_local_signals()
        sim = Simulator(m, engine="reference")
        sim.drive("in_sig", Value(0b1100, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b0011

    def test_generate_for_with_instances(self):
        """Generate-for with instantiated submodules."""
        top, design = _make_generate_for_instance()
        sim = Simulator(top, engine="reference", design=design)
        sim.drive("in0", Value(0xAA, width=8))
        sim.drive("in1", Value(0x55, width=8))
        sim.run(max_time=0)
        assert sim.read("out0").val & 0xFF == 0x55
        assert sim.read("out1").val & 0xFF == 0xAA

    def test_nested_generate(self):
        """Nested generate-for simulates correctly."""
        m = _make_nested_generate()
        sim = Simulator(m, engine="reference")
        sim.drive("in_sig", Value(0b1010, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b1010


# ── TestGenerateSimVM ────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestGenerateSimVM:
    """Simulate generate-based designs with the VM engines."""

    def test_generate_for_invert_bits(self, engine):
        m = _make_generate_for_assign()
        sim = Simulator(m, engine=engine)
        sim.drive("in_sig", Value(0b1010, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b0101

    def test_generate_if_invert(self, engine):
        m = _make_generate_if_true()
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0x55

    def test_generate_case_invert(self, engine):
        m = _make_generate_case()
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0x55

    def test_generate_for_with_local_signals(self, engine):
        m = _make_generate_for_with_local_signals()
        sim = Simulator(m, engine=engine)
        sim.drive("in_sig", Value(0b1100, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b0011

    def test_nested_generate(self, engine):
        m = _make_nested_generate()
        sim = Simulator(m, engine=engine)
        sim.drive("in_sig", Value(0b1010, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b1010


# ── TestGenerateSimCompiled ──────────────────────────────────────────


@pytest.mark.skipif(not _has_compiler, reason="No C compiler available")
class TestGenerateSimCompiled:
    """Simulate generate-based designs with the compiled engine."""

    def test_generate_for_invert_bits(self):
        m = _make_generate_for_assign()
        sim = Simulator(m, engine="compiled")
        sim.drive("in_sig", Value(0b1010, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b0101

    def test_generate_if_invert(self):
        m = _make_generate_if_true()
        sim = Simulator(m, engine="compiled")
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0x55

    def test_generate_case_invert(self):
        m = _make_generate_case()
        sim = Simulator(m, engine="compiled")
        sim.drive("a", Value(0xAA, width=8))
        sim.run(max_time=0)
        result = sim.read("y")
        assert result.val & 0xFF == 0x55

    def test_generate_for_with_local_signals(self):
        m = _make_generate_for_with_local_signals()
        sim = Simulator(m, engine="compiled")
        sim.drive("in_sig", Value(0b1100, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b0011

    def test_nested_generate(self):
        m = _make_nested_generate()
        sim = Simulator(m, engine="compiled")
        sim.drive("in_sig", Value(0b1010, width=4))
        sim.run(max_time=0)
        result = sim.read("out_sig")
        assert result.val & 0xF == 0b1010


# ── TestGenerateCrossValidation ───────────────────────────────────────


class TestGenerateCrossValidation:
    """Cross-engine validation: reference vs VM vs compiled."""

    @pytest.mark.skipif(not _has_compiler, reason="No C compiler available")
    @pytest.mark.parametrize(
        "builder",
        [
            _make_generate_for_assign,
            _make_generate_for_with_local_signals,
            _make_generate_if_true,
            _make_generate_if_false,
            _make_generate_case,
            _make_nested_generate,
        ],
        ids=[
            "for_assign",
            "for_local",
            "if_true",
            "if_false",
            "case",
            "nested",
        ],
    )
    def test_cross_engine(self, builder):
        """All three engines should produce the same result."""
        m = builder()

        # Determine signals to drive and read
        inputs = [p.name for p in m.ports if p.direction == PortDirection.INPUT]
        outputs = [p.name for p in m.ports if p.direction == PortDirection.OUTPUT]

        drive_val = 0xAA
        width = 8
        if any("4" in str(getattr(p, "width", "")) for p in m.ports):
            drive_val = 0b1010
            width = 4

        results = {}
        for engine in ("reference", "vm", "compiled"):
            mod = builder()
            sim = Simulator(mod, engine=engine)
            for inp in inputs:
                sim.drive(inp, Value(drive_val, width=width))
            sim.run(max_time=0)
            results[engine] = {out: sim.read(out).val for out in outputs}

        assert results["reference"] == results["vm"], f"reference != vm: {results['reference']} vs {results['vm']}"
        assert results["reference"] == results["compiled"], (
            f"reference != compiled: {results['reference']} vs {results['compiled']}"
        )


_VERILOG_GENERATE_STRUCT_ARRAY_HIER = """\
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

    initial begin
        #1;
        $display(
            "GEN_STRUCT_ARRAY data0=%0h tag1=%0h data2=%0h tag3=%0h",
            dut.gen_outs[0].items[0].data,
            dut.gen_outs[0].items[1].tag,
            dut.gen_outs[1].items[0].data,
            dut.gen_outs[1].items[1].tag
        );
    end
endmodule
"""


@pytest.mark.parametrize("engine", _CROSS_ENGINES)
def test_generate_struct_array_hierarchical_cross_engine(engine, tmp_path):
    """Generate-block local struct arrays should resolve through hierarchical prefixes across engines."""
    from veriforge.project import parse_files

    src = tmp_path / "test_generate_struct_array_hier.sv"
    src.write_text(_VERILOG_GENERATE_STRUCT_ARRAY_HIER)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("gen_struct_array_tb")
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=20)
    lines = sim.display_output
    assert any("gen_struct_array data0=a5 tag1=c data2=11 tag3=6" in line.lower() for line in lines), (
        f"Generate struct-array hierarchical read failed ({engine}): {lines}"
    )
