"""Tests for DSL Interface.to_model() — SV interface emit mode.

Verifies that a DSL Interface template can be converted to a model-layer
Interface with auto-generated modport declarations, and that the resulting
model can be emitted as valid SystemVerilog interface/endinterface blocks.
"""

from __future__ import annotations

import pytest

from veriforge.codegen import emit_interface
from veriforge.dsl.interface import Interface
from veriforge.model.ports import PortDirection


# ===================================================================
# Interface.to_model() basic functionality
# ===================================================================


class TestToModel:
    """Test DSL Interface → model Interface conversion."""

    def test_basic_interface(self):
        intf = (
            Interface("my_bus")
            .signal("data", width=8, src="master")
            .signal("valid", src="master")
            .signal("ready", src="slave")
        )
        model = intf.to_model()
        assert model.name == "my_bus"
        assert len(model.nets) == 3
        assert len(model.modports) == 2

    def test_net_names(self):
        intf = Interface("bus").signal("a", src="master").signal("b", src="slave")
        model = intf.to_model()
        net_names = [n.name for n in model.nets]
        assert net_names == ["a", "b"]

    def test_net_widths(self):
        intf = Interface("bus").signal("data", width=8, src="master").signal("valid", src="master")
        model = intf.to_model()
        # data is 8-bit: range [7:0]
        assert model.nets[0].width is not None
        # valid is 1-bit: no range
        assert model.nets[1].width is None

    def test_net_signed(self):
        intf = Interface("bus").signal("val", width=16, src="master", signed=True)
        model = intf.to_model()
        assert model.nets[0].signed is True

    def test_modport_names(self):
        intf = Interface("bus").signal("x", src="master")
        model = intf.to_model()
        mp_names = [mp.name for mp in model.modports]
        assert mp_names == ["master", "slave"]

    def test_master_modport_directions(self):
        intf = Interface("bus").signal("data", src="master").signal("ready", src="slave")
        model = intf.to_model()
        master = model.get_modport("master")
        assert master is not None
        ports = {p.name: p.direction for p in master.ports}
        assert ports["data"] == PortDirection.OUTPUT
        assert ports["ready"] == PortDirection.INPUT

    def test_slave_modport_directions(self):
        intf = Interface("bus").signal("data", src="master").signal("ready", src="slave")
        model = intf.to_model()
        slave = model.get_modport("slave")
        assert slave is not None
        ports = {p.name: p.direction for p in slave.ports}
        assert ports["data"] == PortDirection.INPUT
        assert ports["ready"] == PortDirection.OUTPUT

    def test_all_master_signals(self):
        intf = Interface("out_bus").signal("a", src="master").signal("b", src="master")
        model = intf.to_model()
        master = model.get_modport("master")
        assert all(p.direction == PortDirection.OUTPUT for p in master.ports)
        slave = model.get_modport("slave")
        assert all(p.direction == PortDirection.INPUT for p in slave.ports)

    def test_all_slave_signals(self):
        intf = Interface("in_bus").signal("a", src="slave").signal("b", src="slave")
        model = intf.to_model()
        master = model.get_modport("master")
        assert all(p.direction == PortDirection.INPUT for p in master.ports)
        slave = model.get_modport("slave")
        assert all(p.direction == PortDirection.OUTPUT for p in slave.ports)

    def test_empty_interface_raises(self):
        intf = Interface("empty")
        with pytest.raises(ValueError, match="no signals"):
            intf.to_model()


# ===================================================================
# emit_interface() from DSL-generated model
# ===================================================================


class TestEmitInterface:
    """Test emitting SV interface blocks from DSL Interface.to_model()."""

    def test_emit_basic(self):
        intf = (
            Interface("my_bus")
            .signal("data", width=8, src="master")
            .signal("valid", src="master")
            .signal("ready", src="slave")
        )
        text = emit_interface(intf.to_model())
        assert "interface my_bus;" in text
        assert "endinterface" in text

    def test_emit_wire_declarations(self):
        intf = Interface("bus").signal("data", width=8, src="master").signal("valid", src="master")
        text = emit_interface(intf.to_model())
        assert "wire [7:0] data;" in text
        assert "wire valid;" in text

    def test_emit_modport_master(self):
        intf = Interface("bus").signal("data", src="master").signal("ready", src="slave")
        text = emit_interface(intf.to_model())
        assert "modport master(output data, input ready);" in text

    def test_emit_modport_slave(self):
        intf = Interface("bus").signal("data", src="master").signal("ready", src="slave")
        text = emit_interface(intf.to_model())
        assert "modport slave(input data, output ready);" in text

    def test_emit_signed_signal(self):
        intf = Interface("bus").signal("val", width=16, src="master", signed=True)
        text = emit_interface(intf.to_model())
        assert "signed" in text
        assert "[15:0]" in text

    def test_emit_axi_stream(self):
        """Full AXI Stream interface — realistic use case."""
        axi_stream = (
            Interface("axi_stream")
            .signal("tvalid", src="master")
            .signal("tready", src="slave")
            .signal("tdata", width=8, src="master")
            .signal("tlast", src="master")
        )
        text = emit_interface(axi_stream.to_model())
        assert "interface axi_stream;" in text
        assert "wire tvalid;" in text
        assert "wire tready;" in text
        assert "wire [7:0] tdata;" in text
        assert "wire tlast;" in text
        assert "modport master(output tvalid, input tready, output tdata, output tlast);" in text
        assert "modport slave(input tvalid, output tready, input tdata, input tlast);" in text
        assert "endinterface" in text

    def test_emit_is_valid_sv(self):
        """Output structure follows SV interface grammar."""
        intf = Interface("simple").signal("x", src="master")
        text = emit_interface(intf.to_model())
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        assert lines[0] == "interface simple;"
        assert lines[-1] == "endinterface"


# ===================================================================
# Integration: codegen public API
# ===================================================================


class TestCodegenExports:
    """Verify emit_interface and emit_package are accessible from codegen."""

    def test_emit_interface_importable(self):
        from veriforge.codegen import emit_interface as ei  # noqa: PLC0415

        assert callable(ei)

    def test_emit_package_importable(self):
        from veriforge.codegen import emit_package as ep  # noqa: PLC0415

        assert callable(ep)


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    """Edge cases for to_model() conversion."""

    def test_single_signal(self):
        intf = Interface("minimal").signal("x", src="master")
        model = intf.to_model()
        assert len(model.nets) == 1
        assert len(model.modports) == 2

    def test_wide_signal(self):
        intf = Interface("wide").signal("data", width=256, src="master")
        model = intf.to_model()
        text = emit_interface(model)
        assert "[255:0]" in text

    def test_many_signals(self):
        intf = Interface("big")
        for i in range(10):
            src = "master" if i % 2 == 0 else "slave"
            intf.signal(f"sig{i}", src=src)
        model = intf.to_model()
        assert len(model.nets) == 10
        assert len(model.get_modport("master").ports) == 10
        assert len(model.get_modport("slave").ports) == 10

    def test_model_has_no_parameters(self):
        intf = Interface("bus").signal("x", src="master")
        model = intf.to_model()
        assert model.parameters == []

    def test_model_has_no_typedefs(self):
        intf = Interface("bus").signal("x", src="master")
        model = intf.to_model()
        assert model.typedefs == []

    def test_model_has_no_imports(self):
        intf = Interface("bus").signal("x", src="master")
        model = intf.to_model()
        assert model.imports == []
