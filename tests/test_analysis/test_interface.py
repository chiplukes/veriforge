"""Tests for SystemVerilog interface and modport support."""

from veriforge.codegen.verilog_emitter import emit_interface, emit_design
from veriforge.model.interface import Interface, Modport, ModportPort
from veriforge.model.ports import PortDirection
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(src: str):
    """Parse Verilog source and return the design."""
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(text=src)
    return tree_to_design(tree)


def _parse_interface(src: str):
    """Parse source and return the first interface."""
    design = _parse(src)
    assert len(design.interfaces) >= 1, f"Expected interface, got {design}"
    return design.interfaces[0]


# ---------------------------------------------------------------------------
# Grammar parse tests
# ---------------------------------------------------------------------------


class TestGrammarParse:
    """Verify that the grammar accepts interface/modport syntax."""

    def test_minimal_interface(self):
        src = "interface my_bus; endinterface\n"
        iface = _parse_interface(src)
        assert iface.name == "my_bus"

    def test_interface_with_wire(self):
        src = """\
interface my_bus;
    wire [7:0] data;
endinterface
"""
        iface = _parse_interface(src)
        assert iface.name == "my_bus"

    def test_interface_with_modport(self):
        src = """\
interface my_bus;
    wire [7:0] data;
    wire valid;
    modport master(output data, output valid);
endinterface
"""
        iface = _parse_interface(src)
        assert len(iface.modports) >= 1

    def test_interface_with_multiple_modports(self):
        src = """\
interface axi_lite;
    wire [31:0] awaddr;
    wire awvalid;
    wire awready;
    modport master(output awaddr, output awvalid, input awready);
    modport slave(input awaddr, input awvalid, output awready);
endinterface
"""
        iface = _parse_interface(src)
        assert len(iface.modports) == 2

    def test_interface_with_parameters(self):
        src = """\
interface data_bus #(parameter WIDTH = 8);
    wire [WIDTH-1:0] data;
    modport producer(output data);
endinterface
"""
        iface = _parse_interface(src)
        assert len(iface.parameters) >= 1

    def test_interface_alongside_module(self):
        src = """\
interface my_bus;
    wire data;
endinterface

module top;
    wire x;
endmodule
"""
        design = _parse(src)
        assert len(design.interfaces) == 1
        assert len(design.modules) == 1

    def test_interface_with_reg(self):
        src = """\
interface my_bus;
    reg [7:0] data;
    modport reader(input data);
endinterface
"""
        iface = _parse_interface(src)
        assert len(iface.variables) >= 1

    def test_interface_with_assign(self):
        src = """\
interface my_bus;
    wire a;
    wire b;
    assign b = a;
endinterface
"""
        iface = _parse_interface(src)
        assert len(iface.continuous_assigns) >= 1


# ---------------------------------------------------------------------------
# Model extraction tests
# ---------------------------------------------------------------------------


class TestModelExtraction:
    """Verify that parsed interfaces produce correct model objects."""

    def test_interface_name(self):
        src = "interface axi_bus; endinterface\n"
        iface = _parse_interface(src)
        assert iface.name == "axi_bus"

    def test_nets_extracted(self):
        src = """\
interface my_bus;
    wire [7:0] data;
    wire valid;
    wire ready;
endinterface
"""
        iface = _parse_interface(src)
        names = {n.name for n in iface.nets}
        assert "data" in names
        assert "valid" in names
        assert "ready" in names

    def test_modport_name_and_ports(self):
        src = """\
interface my_bus;
    wire data;
    wire valid;
    modport master(output data, output valid);
endinterface
"""
        iface = _parse_interface(src)
        mp = iface.modports[0]
        assert mp.name == "master"
        assert len(mp.ports) == 2

    def test_modport_port_directions(self):
        src = """\
interface my_bus;
    wire data;
    wire ready;
    modport master(output data, input ready);
endinterface
"""
        iface = _parse_interface(src)
        mp = iface.modports[0]
        port_map = {p.name: p.direction for p in mp.ports}
        assert port_map["data"] == PortDirection.OUTPUT
        assert port_map["ready"] == PortDirection.INPUT

    def test_multiple_modport_names(self):
        src = """\
interface my_bus;
    wire [7:0] data;
    modport master(output data);
    modport slave(input data);
endinterface
"""
        iface = _parse_interface(src)
        names = {mp.name for mp in iface.modports}
        assert names == {"master", "slave"}

    def test_parameters_extracted(self):
        src = """\
interface data_bus #(parameter WIDTH = 8, parameter DEPTH = 4);
    wire [WIDTH-1:0] data;
endinterface
"""
        iface = _parse_interface(src)
        param_names = {p.name for p in iface.parameters}
        assert "WIDTH" in param_names

    def test_parent_references(self):
        src = """\
interface my_bus;
    wire data;
    modport master(output data);
endinterface
"""
        iface = _parse_interface(src)
        for n in iface.nets:
            assert n.parent is iface
        for mp in iface.modports:
            assert mp.parent is iface

    def test_to_dict(self):
        src = """\
interface my_bus;
    wire data;
    modport master(output data);
endinterface
"""
        iface = _parse_interface(src)
        d = iface.to_dict()
        assert d["name"] == "my_bus"
        assert "nets" in d
        assert "modports" in d

    def test_design_to_dict_with_interface(self):
        src = """\
interface my_bus;
    wire data;
endinterface

module top;
    wire x;
endmodule
"""
        design = _parse(src)
        d = design.to_dict()
        assert "interfaces" in d
        assert len(d["interfaces"]) == 1

    def test_interface_is_verilog_node(self):
        iface = Interface(name="test_iface")
        assert hasattr(iface, "parent")
        assert hasattr(iface, "loc")

    def test_modport_is_verilog_node(self):
        mp = Modport(name="master", ports=[ModportPort("sig", PortDirection.OUTPUT)])
        assert hasattr(mp, "parent")
        assert len(mp.ports) == 1


# ---------------------------------------------------------------------------
# Emitter tests
# ---------------------------------------------------------------------------


class TestEmitter:
    """Verify interface emission."""

    def test_emit_minimal_interface(self):
        iface = Interface(name="my_bus")
        text = emit_interface(iface)
        assert "interface my_bus;" in text
        assert "endinterface" in text

    def test_emit_interface_with_modport(self):
        src = """\
interface my_bus;
    wire data;
    modport master(output data);
endinterface
"""
        iface = _parse_interface(src)
        text = emit_interface(iface)
        assert "modport master(output data);" in text

    def test_emit_design_with_interface(self):
        src = """\
interface my_bus;
    wire data;
endinterface

module top;
    wire x;
endmodule
"""
        design = _parse(src)
        text = emit_design(design)
        assert "interface my_bus;" in text
        assert "module top" in text
        assert "endinterface" in text
        assert "endmodule" in text


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Verify parse → emit → re-parse round-trip."""

    def test_simple_round_trip(self):
        src = """\
interface my_bus;
    wire [7:0] data;
    wire valid;
    modport master(output data, output valid);
    modport slave(input data, input valid);
endinterface
"""
        iface = _parse_interface(src)
        text = emit_interface(iface)
        # Re-parse
        full_src = text + "\n"
        iface2 = _parse_interface(full_src)
        assert iface2.name == "my_bus"
        assert len(iface2.nets) == len(iface.nets)
        assert len(iface2.modports) == 2

    def test_round_trip_with_parameters(self):
        src = """\
interface data_bus #(parameter WIDTH = 8);
    wire [WIDTH-1:0] data;
    modport producer(output data);
endinterface
"""
        iface = _parse_interface(src)
        text = emit_interface(iface)
        iface2 = _parse_interface(text + "\n")
        assert iface2.name == "data_bus"
        assert len(iface2.parameters) >= 1
        assert len(iface2.modports) == 1

    def test_round_trip_preserves_directions(self):
        src = """\
interface my_bus;
    wire data;
    wire ready;
    modport master(output data, input ready);
endinterface
"""
        iface = _parse_interface(src)
        text = emit_interface(iface)
        iface2 = _parse_interface(text + "\n")
        mp = iface2.modports[0]
        port_map = {p.name: p.direction for p in mp.ports}
        assert port_map["data"] == PortDirection.OUTPUT
        assert port_map["ready"] == PortDirection.INPUT


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for interface parsing."""

    def test_empty_interface(self):
        src = "interface empty_bus; endinterface\n"
        iface = _parse_interface(src)
        assert iface.name == "empty_bus"
        assert iface.nets == []
        assert iface.modports == []

    def test_no_interfaces_in_design(self):
        src = "module top; wire x; endmodule\n"
        design = _parse(src)
        assert design.interfaces == []

    def test_modport_with_inout(self):
        src = """\
interface bidir_bus;
    wire data;
    modport endpoint(inout data);
endinterface
"""
        iface = _parse_interface(src)
        mp = iface.modports[0]
        assert mp.ports[0].direction == PortDirection.INOUT

    def test_interface_with_localparam(self):
        src = """\
interface my_bus;
    localparam SIZE = 8;
    wire [SIZE-1:0] data;
endinterface
"""
        iface = _parse_interface(src)
        local_params = [p for p in iface.parameters if p.is_local]
        assert len(local_params) >= 1
