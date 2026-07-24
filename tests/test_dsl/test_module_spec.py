"""Tests for the declarative ModuleSpec layer (veriforge.dsl.spec)."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl import In, Inout, Module, ModuleSpec, Out, OutReg, Param, Reg, Wire
from veriforge.sim import Clock, Simulator


class Counter(ModuleSpec):
    WIDTH = Param(8)
    clk = In()
    rst = In()
    en = In()
    count = OutReg("WIDTH")

    def body(self, m: Module) -> None:
        with m.seq(self.clk, rst=self.rst, rst_vals={self.count: 0}):
            with m.if_(self.en):
                self.count.next = self.count + 1


class TestModuleSpecBasics:
    def test_module_name_defaults_to_class_name(self):
        assert Counter().build().name == "Counter"

    def test_module_name_override(self):
        class Named(ModuleSpec):
            module_name = "my_mod"
            clk = In()

            def body(self, m: Module) -> None:
                pass

        assert Named().build().name == "my_mod"

    def test_ports_declared_in_class_order(self):
        mod = Counter().build()
        assert [p.name for p in mod.ports] == ["clk", "rst", "en", "count"]

    def test_param_width_reference(self):
        text = emit_module(Counter().build())
        assert "parameter WIDTH = 8" in text
        assert "output reg [WIDTH - 1:0] count" in text

    def test_param_override(self):
        text = emit_module(Counter(WIDTH=16).build())
        assert "parameter WIDTH = 16" in text

    def test_unknown_param_override_raises(self):
        with pytest.raises(TypeError, match="undeclared parameters"):
            Counter(DEPTH=4)

    def test_unknown_width_reference_raises(self):
        class Bad(ModuleSpec):
            data = In("NOPE")

            def body(self, m: Module) -> None:
                pass

        with pytest.raises(ValueError, match="does not name a Param"):
            Bad().build()

    def test_body_required(self):
        class NoBody(ModuleSpec):
            clk = In()

        with pytest.raises(NotImplementedError, match="body"):
            NoBody().build()

    def test_descriptor_access_outside_build_raises(self):
        spec = Counter()
        with pytest.raises(RuntimeError, match="only accessible during build"):
            _ = spec.clk

    def test_class_level_access_returns_descriptor(self):
        assert isinstance(Counter.clk, In)

    def test_all_item_kinds(self):
        class Kitchen(ModuleSpec):
            W = Param(4)
            a = In(2)
            b = Out(2)
            c = OutReg(2, init=0)
            d = Inout()
            w = Wire("W")
            r = Reg(2, depth=4)
            s = Reg(2, signed=True)

            def body(self, m: Module) -> None:
                m.assign(self.b, self.a)
                m.assign(self.w, 0)

        text = emit_module(Kitchen().build())
        assert "input [1:0] a" in text
        assert "output [1:0] b" in text
        assert "output reg [1:0] c = 0" in text
        assert "inout d" in text
        assert "wire [W - 1:0] w" in text
        assert "reg [1:0] r [0:3];" in text
        assert "reg signed [1:0] s;" in text

    def test_inheritance_extends_ports(self):
        class CounterWithLoad(Counter):
            load = In()
            load_val = In("WIDTH")

            def body(self, m: Module) -> None:
                with m.seq(self.clk, rst=self.rst, rst_vals={self.count: 0}):
                    with m.if_(self.load):
                        self.count.next = self.load_val
                    with m.elif_(self.en):
                        self.count.next = self.count + 1

        mod = CounterWithLoad().build()
        names = [p.name for p in mod.ports]
        assert names == ["clk", "rst", "en", "count", "load", "load_val"]

    def test_build_twice_is_independent(self):
        spec = Counter()
        m1 = spec.build()
        m2 = spec.build()
        assert m1 is not m2
        assert [p.name for p in m1.ports] == [p.name for p in m2.ports]


class TestModuleSpecSimulation:
    def test_counter_simulates(self):
        sim = Simulator(Counter().build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("rst", 1)
        sim.drive("en", 0)
        sim.run(max_time=25)
        assert int(sim.read("count")) == 0
        sim.drive("rst", 0)
        sim.drive("en", 1)
        sim.run(max_time=105)
        counted = int(sim.read("count"))
        assert counted > 0
        sim.drive("en", 0)
        sim.run(max_time=155)
        assert int(sim.read("count")) == counted
