"""`m.interface(..., reg=True, init=...)` gives the output regs initial values.

Designs that rely on FPGA configuration (INIT) for their power-up state need
every control register declared with a value, including the ones created by
`m.interface()`.
"""

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.dsl import Interface, Module, posedge
from veriforge.project import Design
from veriforge.sim import Simulator
from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON


def _axis(width=8):
    i = Interface("axi_stream")
    i.signal("tvalid", src="master")
    i.signal("tready", src="slave")
    i.signal("tdata", width=width, src="master")
    return i


def _emit(init, role="master"):
    m = Module("iface_init")
    clk = m.input("clk")
    bus = m.interface("m_axis" if role == "master" else "s_axis", _axis(), role=role, reg=True, init=init)
    with m.always(posedge(clk)):
        if role == "master":
            bus.tvalid <<= ~bus.tvalid
            bus.tdata <<= bus.tdata + 1
        else:
            bus.tready <<= ~bus.tready
    return emit_design(Design(modules=[m.build()]))


def test_one_value_for_every_output():
    v = _emit(0)
    assert "output reg m_axis_tvalid = 0" in v
    assert "output reg [7:0] m_axis_tdata = 0" in v
    assert "input m_axis_tready" in v


def test_per_signal_values():
    v = _emit({"tvalid": 0})
    assert "output reg m_axis_tvalid = 0" in v
    assert "output reg [7:0] m_axis_tdata;" in v or "output reg [7:0] m_axis_tdata\n" in v
    assert "output reg s_axis_tready = 1" in _emit({"tready": 1}, role="slave")


def test_no_init_unchanged():
    assert "output reg m_axis_tvalid = " not in _emit(None)


def test_rejects_bad_use():
    m = Module("bad")
    with pytest.raises(ValueError, match="reg=True"):
        m.interface("a", _axis(), role="master", init=0)
    with pytest.raises(ValueError, match="tready"):
        m.interface("b", _axis(), role="master", reg=True, init={"tready": 1})


def _engines():
    return ["reference", "vm"] + (["vm-fast"] if _HAS_CYTHON else [])


@pytest.mark.parametrize("engine", _engines())
def test_output_reg_init_simulates(engine):
    # The port's `= init` reached the emitted Verilog but not the simulator: output_reg
    # also creates the variable the engines initialize, and it had no initial value.
    m = Module("init_sim")
    m.input("clk")
    m.output_reg("q", width=8, init=0x5A)
    m.output_reg("u", width=8)
    bus = m.interface("m_axis", _axis(), role="master", reg=True, init={"tvalid": 1, "tdata": 0x33})
    assert bus is not None
    sim = Simulator(m.build(), engine=engine)
    sim.run(lambda s: None, max_time=1)
    assert sim.read("q") == 0x5A
    assert str(sim.read("u")) == "8'bxxxxxxxx"
    assert sim.read("m_axis_tvalid") == 1
    assert sim.read("m_axis_tdata") == 0x33
