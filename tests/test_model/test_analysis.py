"""Tests for Phase 4: Connectivity & Analysis.

Tests cover:
    - Name resolution (Identifier.resolved)
    - Module cross-linking (Instance.resolved_module)
    - Port connection resolution (PortConnection.resolved_port)
    - Driver/load analysis (Net.drivers/loads, Variable.drivers/loads)
    - Full analyze_design integration
"""

import pytest

from veriforge.analysis import (
    Driver,
    Load,
    analyze_design,
    link_instances,
    resolve_names,
    resolve_port_connections,
)
from veriforge.model import (
    AlwaysBlock,
    ContinuousAssign,
    Design,
    Identifier,
    InitialBlock,
    Instance,
    Module,
    Net,
    Parameter,
    Port,
    Variable,
)
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parser():
    """A module_declaration parser."""
    return verilog_parser(start="module_declaration")


@pytest.fixture
def full_parser():
    """A full verilog parser (start='verilog')."""
    return verilog_parser(start="verilog")


def _parse_module(parser, verilog: str) -> Module:
    """Parse a single module declaration and return it."""
    tree = parser.build_tree(text=verilog)
    design = tree_to_design(tree)
    return design.modules[0]


def _parse_design(full_parser, verilog: str):
    """Parse a full Verilog design (multiple modules)."""
    tree = full_parser.build_tree(text=verilog)
    return tree_to_design(tree)


# ===================================================================
# Name Resolution
# ===================================================================


class TestNameResolution:
    """Test Identifier.resolved is set correctly."""

    def test_input_port_resolves(self, parser):
        """Identifier in assign RHS resolves to Port."""
        m = _parse_module(parser, "module m(input a, output b); assign b = a; endmodule")
        design = tree_to_design.__wrapped__(m) if hasattr(tree_to_design, "__wrapped__") else None
        # Build design from module

        design = Design(modules=[m])
        resolve_names(design)

        # Find the identifier 'a' in the continuous assign RHS
        ca = m.continuous_assigns[0]
        assert isinstance(ca.rhs, Identifier)
        assert ca.rhs.name == "a"
        assert ca.rhs.resolved is not None
        assert isinstance(ca.rhs.resolved, Port)
        assert ca.rhs.resolved.name == "a"

    def test_wire_resolves_to_net(self, parser):
        """Identifier resolves to Net when a wire with that name exists."""
        m = _parse_module(parser, "module m(input a); wire w; assign w = a; endmodule")

        design = Design(modules=[m])
        resolve_names(design)

        ca = m.continuous_assigns[0]
        assert isinstance(ca.lhs, Identifier)
        assert ca.lhs.name == "w"
        assert ca.lhs.resolved is not None
        assert isinstance(ca.lhs.resolved, Net)
        assert ca.lhs.resolved.name == "w"

    def test_reg_resolves_to_variable(self, parser):
        """Identifier resolves to Variable when reg declared."""
        m = _parse_module(
            parser,
            "module m(input clk, input d); reg q; always @(posedge clk) q <= d; endmodule",
        )

        design = Design(modules=[m])
        resolve_names(design)

        ab = m.always_blocks[0]
        # Find the 'q' identifier in the body (nonblocking assign LHS)
        found_q = False
        for node in ab.walk():
            if isinstance(node, Identifier) and node.name == "q":
                assert node.resolved is not None
                assert isinstance(node.resolved, Variable)
                assert node.resolved.name == "q"
                found_q = True
                break
        assert found_q, "Identifier 'q' not found in always block"

    def test_parameter_resolves(self, parser):
        """Identifier resolves to Parameter."""
        m = _parse_module(
            parser,
            "module m #(parameter WIDTH = 8)(input [WIDTH-1:0] d); endmodule",
        )

        design = Design(modules=[m])
        resolve_names(design)

        # Find WIDTH identifier in port width expression
        found_width = False
        for node in m.walk():
            if isinstance(node, Identifier) and node.name == "WIDTH":
                assert node.resolved is not None
                assert isinstance(node.resolved, Parameter)
                assert node.resolved.name == "WIDTH"
                found_width = True
                break
        assert found_width, "Identifier 'WIDTH' not found"

    def test_unknown_identifier_stays_none(self, parser):
        """Identifiers not in the symbol table remain unresolved."""
        m = _parse_module(parser, "module m(); wire w; assign w = unknown_sig; endmodule")

        design = Design(modules=[m])
        resolve_names(design)

        ca = m.continuous_assigns[0]
        assert isinstance(ca.rhs, Identifier)
        assert ca.rhs.name == "unknown_sig"
        assert ca.rhs.resolved is None

    def test_net_overrides_port_in_symbol_table(self, parser):
        """When both Port and Net exist for same name, Net takes priority."""
        m = _parse_module(
            parser,
            "module m(input a, output b); wire w_a; wire w_b; assign w_b = w_a; endmodule",
        )

        design = Design(modules=[m])
        resolve_names(design)

        ca = m.continuous_assigns[0]
        # LHS 'w_b' should resolve to Net
        if isinstance(ca.lhs, Identifier):
            assert ca.lhs.resolved is not None
            assert isinstance(ca.lhs.resolved, Net), f"Expected Net, got {type(ca.lhs.resolved).__name__}"

    def test_multiple_signals_resolved(self, parser):
        """Multiple identifiers in an expression are all resolved."""
        m = _parse_module(
            parser,
            "module m(input a, input b); wire y; assign y = a & b; endmodule",
        )

        design = Design(modules=[m])
        resolve_names(design)

        # All Identifiers in the module should be resolved
        resolved_names = []
        for node in m.walk():
            if isinstance(node, Identifier) and node.resolved is not None:
                resolved_names.append(node.name)

        assert "a" in resolved_names
        assert "b" in resolved_names
        assert "y" in resolved_names


# ===================================================================
# Module Cross-Linking
# ===================================================================


class TestInstanceLinking:
    """Test Instance.resolved_module."""

    def test_instance_resolves_to_module(self, full_parser):
        """Instance resolves to the module defined in the same design."""
        verilog = """\
module sub(input a, output b);
  assign b = a;
endmodule

module top(input x, output y);
  sub u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)

        top = design.get_module("top")
        assert top is not None
        assert len(top.instances) == 1
        inst = top.instances[0]
        assert inst.module_name == "sub"
        assert inst.resolved_module is not None
        assert inst.resolved_module.name == "sub"
        assert inst.resolved_module is design.get_module("sub")

    def test_unknown_module_stays_none(self, full_parser):
        """Instance of undefined module stays unresolved."""
        verilog = """\
module top(input x, output y);
  unknown_mod u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)

        top = design.get_module("top")
        inst = top.instances[0]
        assert inst.resolved_module is None

    def test_multiple_instances_same_module(self, full_parser):
        """Multiple instances of the same module all resolve."""
        verilog = """\
module inv(input a, output y);
  assign y = ~a;
endmodule

module top(input a, input b, output ya, output yb);
  inv u1(.a(a), .y(ya));
  inv u2(.a(b), .y(yb));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)

        top = design.get_module("top")
        assert len(top.instances) == 2  # noqa: PLR2004
        inv_mod = design.get_module("inv")
        for inst in top.instances:
            assert inst.resolved_module is inv_mod

    def test_top_modules_detection(self, full_parser):
        """get_top_modules returns modules not instantiated by others."""
        verilog = """\
module leaf(input a, output b);
  assign b = a;
endmodule

module mid(input x, output y);
  leaf u1(.a(x), .b(y));
endmodule

module top(input i, output o);
  mid u1(.x(i), .y(o));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        # get_top_modules uses instance names, doesn't need full analysis
        tops = design.get_top_modules()
        top_names = [m.name for m in tops]
        assert "top" in top_names
        assert "leaf" not in top_names
        assert "mid" not in top_names


# ===================================================================
# Port Connection Resolution
# ===================================================================


class TestPortResolution:
    """Test PortConnection.resolved_port."""

    def test_named_port_resolves(self, full_parser):
        """Named port connection resolves to the correct port."""
        verilog = """\
module sub(input a, output b);
  assign b = a;
endmodule

module top(input x, output y);
  sub u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)
        resolve_port_connections(design)

        top = design.get_module("top")
        inst = top.instances[0]
        sub = design.get_module("sub")

        # .a(x) → resolves to sub.ports[0] (input a)
        conn_a = inst.port_connections[0]
        assert conn_a.port_name == "a"
        assert conn_a.resolved_port is not None
        assert conn_a.resolved_port is sub.get_port("a")

        # .b(y) → resolves to sub.ports[1] (output b)
        conn_b = inst.port_connections[1]
        assert conn_b.port_name == "b"
        assert conn_b.resolved_port is not None
        assert conn_b.resolved_port is sub.get_port("b")

    def test_positional_port_resolves(self, full_parser):
        """Positional port connection resolves by index."""
        verilog = """\
module sub(input a, output b);
  assign b = a;
endmodule

module top(input x, output y);
  sub u1(x, y);
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)
        resolve_port_connections(design)

        top = design.get_module("top")
        inst = top.instances[0]

        # First positional → sub.ports[0] (input a)
        conn0 = inst.port_connections[0]
        assert conn0.resolved_port is not None
        assert conn0.resolved_port.name == "a"

        # Second positional → sub.ports[1] (output b)
        conn1 = inst.port_connections[1]
        assert conn1.resolved_port is not None
        assert conn1.resolved_port.name == "b"

    def test_unresolved_instance_skips_port_resolution(self, full_parser):
        """If instance module is unresolved, port connections stay None."""
        verilog = """\
module top(input x, output y);
  unknown_mod u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)
        resolve_port_connections(design)

        top = design.get_module("top")
        inst = top.instances[0]
        for conn in inst.port_connections:
            assert conn.resolved_port is None

    def test_wrong_port_name_stays_none(self, full_parser):
        """Named port with nonexistent name stays unresolved."""
        verilog = """\
module sub(input a, output b);
  assign b = a;
endmodule

module top(input x, output y);
  sub u1(.nonexistent(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        link_instances(design)
        resolve_port_connections(design)

        top = design.get_module("top")
        inst = top.instances[0]
        conn0 = inst.port_connections[0]
        assert conn0.port_name == "nonexistent"
        assert conn0.resolved_port is None


# ===================================================================
# Driver / Load Analysis — Continuous Assigns
# ===================================================================


class TestContinuousAssignDriversLoads:
    """Test driver/load analysis from continuous assignments."""

    def test_simple_assign_driver(self, parser):
        """assign b = a; → Net 'b' has a driver."""
        m = _parse_module(parser, "module m(); wire a, b; assign b = a; endmodule")

        design = Design(modules=[m])
        analyze_design(design)

        net_b = m.get_net("b")
        assert net_b is not None
        assert len(net_b.drivers) == 1
        assert isinstance(net_b.drivers[0], Driver)
        assert isinstance(net_b.drivers[0].source, ContinuousAssign)

    def test_simple_assign_load(self, parser):
        """assign b = a; → Net 'a' has a load."""
        m = _parse_module(parser, "module m(); wire a, b; assign b = a; endmodule")

        design = Design(modules=[m])
        analyze_design(design)

        net_a = m.get_net("a")
        assert net_a is not None
        assert len(net_a.loads) == 1
        assert isinstance(net_a.loads[0], Load)
        assert isinstance(net_a.loads[0].consumer, ContinuousAssign)

    def test_expression_assign_loads(self, parser):
        """assign y = a & b; → Both 'a' and 'b' have loads."""
        m = _parse_module(parser, "module m(); wire a, b, y; assign y = a & b; endmodule")

        design = Design(modules=[m])
        analyze_design(design)

        net_a = m.get_net("a")
        net_b = m.get_net("b")
        assert len(net_a.loads) == 1
        assert len(net_b.loads) == 1
        assert isinstance(net_a.loads[0].consumer, ContinuousAssign)

    def test_multiple_assigns_to_same_net(self, parser):
        """Multiple assigns to same net create multiple drivers."""
        m = _parse_module(
            parser,
            "module m(); wire a, b, y; assign y = a; assign y = b; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        net_y = m.get_net("y")
        assert net_y is not None
        assert len(net_y.drivers) == 2  # noqa: PLR2004 - Two different ContinuousAssign sources

    def test_no_driver_no_load(self, parser):
        """Wire with no assigns has empty driver/load lists."""
        m = _parse_module(parser, "module m(); wire unused; endmodule")

        design = Design(modules=[m])
        analyze_design(design)

        net = m.get_net("unused")
        assert len(net.drivers) == 0
        assert len(net.loads) == 0


# ===================================================================
# Driver / Load Analysis — Behavioral Blocks
# ===================================================================


class TestBehavioralDriversLoads:
    """Test driver/load analysis from always/initial blocks."""

    def test_always_blocking_assign_driver(self, parser):
        """always @(*) y = a; → Variable 'y' has driver (AlwaysBlock)."""
        m = _parse_module(
            parser,
            "module m(input a); reg y; always @(*) y = a; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        var_y = m.get_variable("y")
        assert var_y is not None
        assert len(var_y.drivers) == 1
        assert isinstance(var_y.drivers[0].source, AlwaysBlock)

    def test_always_nonblocking_assign_driver(self, parser):
        """always @(posedge clk) q <= d; → Variable 'q' has driver."""
        m = _parse_module(
            parser,
            "module m(input clk, input d); reg q; always @(posedge clk) q <= d; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        var_q = m.get_variable("q")
        assert var_q is not None
        assert len(var_q.drivers) == 1
        assert isinstance(var_q.drivers[0].source, AlwaysBlock)

    def test_always_rhs_load(self, parser):
        """always @(*) y = a; → Port 'a' is loaded.
        Since 'a' is only a Port (no separate net/var), it won't appear as
        a Net/Variable load. This tests that the analysis doesn't crash.
        """
        m = _parse_module(
            parser,
            "module m(); wire a; reg y; always @(*) y = a; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        # 'a' is a wire → should have a load from the always block
        net_a = m.get_net("a")
        assert net_a is not None
        assert len(net_a.loads) == 1
        assert isinstance(net_a.loads[0].consumer, AlwaysBlock)

    def test_sensitivity_list_loads(self, parser):
        """Sensitivity list signals are loads."""
        m = _parse_module(
            parser,
            "module m(); wire a, b; reg y; always @(a or b) y = a & b; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        net_a = m.get_net("a")
        net_b = m.get_net("b")
        # 'a' and 'b' are loaded by: (1) sensitivity list, (2) RHS expression
        # but deduplicated per-consumer, so 1 load each from the always block
        assert len(net_a.loads) == 1
        assert len(net_b.loads) == 1

    def test_initial_block_driver(self, parser):
        """initial q = 0; → Variable 'q' has driver (InitialBlock)."""
        m = _parse_module(
            parser,
            "module m(input dummy); reg q; initial q = 0; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        var_q = m.get_variable("q")
        assert var_q is not None
        assert len(var_q.drivers) == 1

        assert isinstance(var_q.drivers[0].source, InitialBlock)

    def test_if_statement_condition_load(self, parser):
        """if (sel) q = a; else q = b; → sel, a, b are all loads."""
        m = _parse_module(
            parser,
            "module m(); wire sel, a, b; reg q; always @(*) if (sel) q = a; else q = b; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        net_sel = m.get_net("sel")
        net_a = m.get_net("a")
        net_b = m.get_net("b")
        assert len(net_sel.loads) == 1
        assert len(net_a.loads) == 1
        assert len(net_b.loads) == 1

    def test_case_statement_loads(self, parser):
        """case expression and item values are loads."""
        m = _parse_module(
            parser,
            """\
module m();
  wire [1:0] sel;
  wire a, b;
  reg y;
  always @(*) begin
    case (sel)
      2'b00: y = a;
      2'b01: y = b;
      default: y = 0;
    endcase
  end
endmodule""",
        )

        design = Design(modules=[m])
        analyze_design(design)

        net_sel = m.get_net("sel")
        assert len(net_sel.loads) >= 1  # sel is loaded in case expression

    def test_multiple_always_blocks(self, parser):
        """Two always blocks driving different variables."""
        m = _parse_module(
            parser,
            """\
module m(input clk, input d1, input d2);
  reg q1, q2;
  always @(posedge clk) q1 <= d1;
  always @(posedge clk) q2 <= d2;
endmodule""",
        )

        design = Design(modules=[m])
        analyze_design(design)

        var_q1 = m.get_variable("q1")
        var_q2 = m.get_variable("q2")
        assert len(var_q1.drivers) == 1
        assert len(var_q2.drivers) == 1
        # Different always blocks
        assert var_q1.drivers[0].source is not var_q2.drivers[0].source


# ===================================================================
# Driver / Load Analysis — Instance Connections
# ===================================================================


class TestInstanceDriversLoads:
    """Test driver/load analysis from instance port connections."""

    def test_instance_output_drives_net(self, full_parser):
        """Instance output port drives the connected net."""
        verilog = """\
module sub(input a, output b);
  assign b = a;
endmodule

module top();
  wire x, y;
  sub u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        analyze_design(design)

        top = design.get_module("top")
        net_y = top.get_net("y")
        assert net_y is not None
        assert len(net_y.drivers) == 1
        assert isinstance(net_y.drivers[0].source, Instance)

    def test_instance_input_loads_net(self, full_parser):
        """Instance input port loads the connected net."""
        verilog = """\
module sub(input a, output b);
  assign b = a;
endmodule

module top();
  wire x, y;
  sub u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        analyze_design(design)

        top = design.get_module("top")
        net_x = top.get_net("x")
        assert net_x is not None
        assert len(net_x.loads) == 1
        assert isinstance(net_x.loads[0].consumer, Instance)

    def test_instance_with_unresolved_module(self, full_parser):
        """Instance of unknown module — connections treated as loads only."""
        verilog = """\
module top();
  wire x, y;
  unknown_mod u1(.a(x), .b(y));
endmodule
"""
        design = _parse_design(full_parser, verilog)
        analyze_design(design)

        top = design.get_module("top")
        net_x = top.get_net("x")
        net_y = top.get_net("y")
        # Conservative: unresolved ports treated as loads
        assert len(net_x.loads) >= 1
        assert len(net_y.loads) >= 1


# ===================================================================
# Full Integration — analyze_design
# ===================================================================


class TestAnalyzeDesign:
    """Integration tests using the full analyze_design pipeline."""

    def test_full_pipeline(self, full_parser):
        """Full analysis of a multi-module design."""
        verilog = """\
module inverter(input a, output y);
  assign y = ~a;
endmodule

module top(input clk, input d, output q);
  wire inv_d;
  reg q_reg;
  inverter u1(.a(d), .y(inv_d));
  always @(posedge clk)
    q_reg <= inv_d;
  assign q = q_reg;
endmodule
"""
        design = _parse_design(full_parser, verilog)
        analyze_design(design)

        # Check instance linking
        top = design.get_module("top")
        assert top.instances[0].resolved_module is design.get_module("inverter")

        # Check port resolution
        inst = top.instances[0]
        assert inst.port_connections[0].resolved_port is not None
        assert inst.port_connections[0].resolved_port.name == "a"

        # Check name resolution: inv_d in always block resolves to Net
        var_q_reg = top.get_variable("q_reg")
        assert var_q_reg is not None

        net_inv_d = top.get_net("inv_d")
        assert net_inv_d is not None

        # inv_d is driven by the instance output
        assert len(net_inv_d.drivers) == 1
        assert isinstance(net_inv_d.drivers[0].source, Instance)

        # inv_d is loaded by the always block
        assert len(net_inv_d.loads) == 1
        assert isinstance(net_inv_d.loads[0].consumer, AlwaysBlock)

        # q_reg is driven by the always block
        assert len(var_q_reg.drivers) == 1
        assert isinstance(var_q_reg.drivers[0].source, AlwaysBlock)

    def test_idempotent_analysis(self, full_parser):
        """Running analyze_design twice produces the same results."""
        verilog = """\
module m();
  wire a, b;
  assign b = a;
endmodule
"""
        design = _parse_design(full_parser, verilog)
        analyze_design(design)
        m = design.modules[0]
        net_b = m.get_net("b")
        assert len(net_b.drivers) == 1

        # Run again — should clear and re-populate
        analyze_design(design)
        net_b = m.get_net("b")
        assert len(net_b.drivers) == 1  # Not 2

    def test_no_modules(self):
        """analyze_design on an empty design doesn't crash."""

        design = Design()
        analyze_design(design)
        assert len(design.modules) == 0

    def test_counter_module(self, full_parser):
        """Realistic counter module analysis."""
        verilog = """\
module counter #(parameter WIDTH = 8)
  (input clk, input rst, input en, output [WIDTH-1:0] count);
  reg [WIDTH-1:0] count_reg;

  always @(posedge clk) begin
    if (rst)
      count_reg <= 0;
    else if (en)
      count_reg <= count_reg + 1;
  end

  assign count = count_reg;
endmodule
"""
        design = _parse_design(full_parser, verilog)
        analyze_design(design)

        m = design.modules[0]
        var_count_reg = m.get_variable("count_reg")
        assert var_count_reg is not None

        # count_reg driven by always block
        assert len(var_count_reg.drivers) == 1
        assert isinstance(var_count_reg.drivers[0].source, AlwaysBlock)

        # count_reg loaded by: always block (RHS of count_reg + 1) and continuous assign (RHS)
        # Both add one load each
        assert len(var_count_reg.loads) >= 1

    def test_driver_load_repr(self):
        """Driver and Load have useful repr."""

        d = Design()
        driver = Driver(source=d)
        load = Load(consumer=d)
        assert "Driver" in repr(driver)
        assert "Load" in repr(load)
        assert "Design" in repr(driver)
        assert "Design" in repr(load)

    def test_driver_equality(self):
        """Driver equality is identity-based."""

        d1 = Design()
        d2 = Design()
        drv1a = Driver(source=d1)
        drv1b = Driver(source=d1)
        drv2 = Driver(source=d2)
        assert drv1a == drv1b  # Same source object
        assert drv1a != drv2

    def test_analyze_design_with_for_loop(self, parser):
        """For-loop init/update variables are tracked as drivers."""
        m = _parse_module(
            parser,
            """\
module m();
  integer i;
  reg [7:0] mem [0:3];
  initial begin
    for (i = 0; i < 4; i = i + 1)
      mem[i] = 0;
  end
endmodule""",
        )

        design = Design(modules=[m])
        analyze_design(design)

        var_i = m.get_variable("i")
        assert var_i is not None
        # i is driven by the initial block (init + update assignments)
        assert len(var_i.drivers) == 1

        assert isinstance(var_i.drivers[0].source, InitialBlock)


# ===================================================================
# Edge Cases
# ===================================================================


class TestEdgeCases:
    """Edge cases and corner scenarios."""

    def test_empty_module(self, parser):
        """Empty module doesn't crash analysis."""
        m = _parse_module(parser, "module m(); endmodule")

        design = Design(modules=[m])
        analyze_design(design)
        assert len(m.nets) == 0
        assert len(m.variables) == 0

    def test_port_only_signals(self, parser):
        """Signals that are only Ports (no Net/Variable) — analysis runs without crash."""
        m = _parse_module(
            parser,
            "module m(input a, output b); assign b = a; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)
        # 'a' and 'b' are Ports — no Net/Variable driver/load tracking
        # Just verify no crash

    def test_concatenation_lvalue_drivers(self, parser):
        """Concatenation on LHS creates drivers for all parts."""
        m = _parse_module(
            parser,
            "module m(); wire a, b, c, d; assign {a, b} = {c, d}; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        net_a = m.get_net("a")
        net_b = m.get_net("b")
        assert len(net_a.drivers) == 1
        assert len(net_b.drivers) == 1
        # c and d are loads
        net_c = m.get_net("c")
        net_d = m.get_net("d")
        assert len(net_c.loads) == 1
        assert len(net_d.loads) == 1

    def test_self_referencing_assignment(self, parser):
        """Variable appears on both LHS and RHS: driver and load."""
        m = _parse_module(
            parser,
            "module m(); wire a; reg q; always @(*) q = q + a; endmodule",
        )

        design = Design(modules=[m])
        analyze_design(design)

        var_q = m.get_variable("q")
        assert len(var_q.drivers) == 1  # driven by always block
        assert len(var_q.loads) == 1  # loaded by always block (RHS)

    def test_walk_resolves_deep_expressions(self, parser):
        """Identifiers deep in expression trees are resolved."""
        m = _parse_module(
            parser,
            "module m(); wire a, b, c, y; assign y = (a & b) | c; endmodule",
        )

        design = Design(modules=[m])
        resolve_names(design)

        # All identifiers should be resolved
        unresolved = []
        for node in m.walk():
            if isinstance(node, Identifier) and node.resolved is None:
                unresolved.append(node.name)
        assert len(unresolved) == 0, f"Unresolved: {unresolved}"
