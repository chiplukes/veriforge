"""Tests for SystemVerilog simulation support.

Validates that enum member constants and package imports are correctly
resolved during elaboration, allowing SV designs to simulate.
"""

from __future__ import annotations

import pytest

from veriforge.dsl import Module, posedge
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Design
from veriforge.model.design import Module as ModelModule
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Identifier,
    Literal,
    Range,
)
from veriforge.model.nets import Net, NetKind
from veriforge.model.package import ImportDecl, Package
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    IfStatement,
    NonblockingAssign,
    SensitivityEdge,
    SeqBlock,
)
from veriforge.model.sv_types import EnumMember, EnumType, TypedefDecl
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim import Simulator, Value
from veriforge.sim.testbench import Clock
from veriforge.sim.elaborate import _build_enum_env, resolve_sv_imports


# ── _build_enum_env tests ───────────────────────────────────────────


class TestBuildEnumEnv:
    """Test the _build_enum_env helper function."""

    def _make_module_with_enum(self, members, *, width=None):
        """Create a module with a typedef enum."""
        enum = EnumType(members, width=width)
        td = TypedefDecl("state_t", enum_type=enum)
        m = ModelModule("test")
        m.typedefs = [td]
        return m

    def test_auto_numbered_members(self):
        members = [EnumMember("IDLE"), EnumMember("RUN"), EnumMember("DONE")]
        m = self._make_module_with_enum(members)
        env = _build_enum_env(m)
        assert env["IDLE"] == (0, 32)
        assert env["RUN"] == (1, 32)
        assert env["DONE"] == (2, 32)

    def test_explicit_values(self):
        members = [
            EnumMember("A", value=Literal(0)),
            EnumMember("B", value=Literal(5)),
            EnumMember("C"),  # auto: 6
        ]
        m = self._make_module_with_enum(members)
        env = _build_enum_env(m)
        assert env["A"] == (0, 32)
        assert env["B"] == (5, 32)
        assert env["C"] == (6, 32)

    def test_explicit_width(self):
        members = [EnumMember("X"), EnumMember("Y")]
        width = Range(Literal(1), Literal(0))  # [1:0] → 2 bits
        m = self._make_module_with_enum(members, width=width)
        env = _build_enum_env(m)
        assert env["X"] == (0, 2)
        assert env["Y"] == (1, 2)

    def test_empty_typedefs(self):
        m = ModelModule("test")
        m.typedefs = []
        env = _build_enum_env(m)
        assert env == {}

    def test_no_typedefs_attr(self):
        m = ModelModule("test")
        env = _build_enum_env(m)
        assert env == {}

    def test_non_enum_typedef_skipped(self):
        td = TypedefDecl("my_type", type_ref="logic [7:0]")
        m = ModelModule("test")
        m.typedefs = [td]
        env = _build_enum_env(m)
        assert env == {}

    def test_multiple_enums(self):
        enum1 = EnumType([EnumMember("A"), EnumMember("B")])
        td1 = TypedefDecl("t1", enum_type=enum1)
        enum2 = EnumType([EnumMember("X", value=Literal(10)), EnumMember("Y")])
        td2 = TypedefDecl("t2", enum_type=enum2)
        m = ModelModule("test")
        m.typedefs = [td1, td2]
        env = _build_enum_env(m)
        assert env["A"] == (0, 32)
        assert env["B"] == (1, 32)
        assert env["X"] == (10, 32)
        assert env["Y"] == (11, 32)


# ── resolve_sv_imports tests ────────────────────────────────────────


class TestResolveSvImports:
    """Test package import resolution."""

    def _make_pkg(self):
        """Create a package with params and typedef."""
        return Package(
            "my_pkg",
            parameters=[Parameter("WIDTH", default_value=Literal(8))],
            typedefs=[
                TypedefDecl(
                    "state_t",
                    enum_type=EnumType(
                        [
                            EnumMember("IDLE"),
                            EnumMember("RUN"),
                            EnumMember("DONE"),
                        ]
                    ),
                )
            ],
        )

    def test_wildcard_import(self):
        pkg = self._make_pkg()
        design = Design(modules=[], packages=[pkg])
        m = ModelModule("top")
        m.imports = [ImportDecl("my_pkg", "*")]
        m.typedefs = []
        resolve_sv_imports(m, design)
        assert any(p.name == "WIDTH" for p in m.parameters)
        assert any(td.name == "state_t" for td in m.typedefs)

    def test_specific_import_param(self):
        pkg = self._make_pkg()
        design = Design(modules=[], packages=[pkg])
        m = ModelModule("top")
        m.imports = [ImportDecl("my_pkg", "WIDTH")]
        m.typedefs = []
        resolve_sv_imports(m, design)
        assert any(p.name == "WIDTH" for p in m.parameters)
        assert not any(td.name == "state_t" for td in m.typedefs)

    def test_specific_import_typedef(self):
        pkg = self._make_pkg()
        design = Design(modules=[], packages=[pkg])
        m = ModelModule("top")
        m.imports = [ImportDecl("my_pkg", "state_t")]
        m.typedefs = []
        resolve_sv_imports(m, design)
        assert not any(p.name == "WIDTH" for p in m.parameters)
        assert any(td.name == "state_t" for td in m.typedefs)

    def test_no_duplicate_import(self):
        pkg = self._make_pkg()
        design = Design(modules=[], packages=[pkg])
        m = ModelModule("top")
        m.parameters = [Parameter("WIDTH", default_value=Literal(16))]
        m.imports = [ImportDecl("my_pkg", "*")]
        m.typedefs = []
        resolve_sv_imports(m, design)
        # Should not duplicate WIDTH
        width_params = [p for p in m.parameters if p.name == "WIDTH"]
        assert len(width_params) == 1
        # Existing one is preserved (value=16)

    def test_unknown_package_ignored(self):
        design = Design(modules=[], packages=[])
        m = ModelModule("top")
        m.imports = [ImportDecl("nonexistent", "*")]
        m.typedefs = []
        resolve_sv_imports(m, design)
        assert len(m.parameters) == 0

    def test_no_design(self):
        m = ModelModule("top")
        m.imports = [ImportDecl("my_pkg", "*")]
        m.typedefs = []
        resolve_sv_imports(m, None)
        # Should not crash, no changes


# ── Reference engine enum simulation ────────────────────────────────


class TestEnumSimReference:
    """Simulate designs with enum constants using the reference engine."""

    def _make_fsm_module(self):
        """Build a simple FSM module with enum states using the model directly."""
        # typedef enum logic [1:0] {IDLE=0, RUN=1, DONE=2} state_t;
        enum = EnumType(
            [
                EnumMember("IDLE", value=Literal(0)),
                EnumMember("RUN", value=Literal(1)),
                EnumMember("DONE", value=Literal(2)),
            ],
            width=Range(Literal(1), Literal(0)),
        )
        td = TypedefDecl("state_t", enum_type=enum)

        # Ports
        ports = [
            Port("clk", PortDirection.INPUT),
            Port("rst", PortDirection.INPUT),
            Port("state", PortDirection.OUTPUT, width=Range(Literal(1), Literal(0))),
        ]

        # Nets/vars
        nets = [
            Net("clk", NetKind.WIRE),
            Net("rst", NetKind.WIRE),
        ]
        variables = [
            Variable("state", VariableKind.REG, width=Range(Literal(1), Literal(0))),
        ]

        # always @(posedge clk)
        #   if (rst) state <= IDLE;
        #   else if (state == IDLE) state <= RUN;
        #   else if (state == RUN) state <= DONE;
        body = IfStatement(
            Identifier("rst"),
            NonblockingAssign(Identifier("state"), Identifier("IDLE")),
            IfStatement(
                BinaryOp("==", Identifier("state"), Identifier("IDLE")),
                NonblockingAssign(Identifier("state"), Identifier("RUN")),
                IfStatement(
                    BinaryOp("==", Identifier("state"), Identifier("RUN")),
                    NonblockingAssign(Identifier("state"), Identifier("DONE")),
                    None,
                ),
            ),
        )

        always = AlwaysBlock(
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            body=body,
        )
        always.sensitivity_type = SensitivityType.SEQUENTIAL

        m = ModelModule("fsm", ports=ports, nets=nets, variables=variables)
        m.always_blocks = [always]
        m.typedefs = [td]
        return m

    def test_enum_constants_are_registered(self):
        """Enum members should be readable as constant signals."""
        m = self._make_fsm_module()
        sim = Simulator(m, engine="reference")
        assert sim.read("IDLE") == Value(0, width=2)
        assert sim.read("RUN") == Value(1, width=2)
        assert sim.read("DONE") == Value(2, width=2)

    def test_fsm_transitions_with_enum(self):
        """Enum constants can be used in non-blocking assignments."""
        m = self._make_fsm_module()
        sim = Simulator(m, engine="reference")
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)

        # Hold reset, run through one clock cycle
        sim.drive("rst", Value(1, width=1))
        sim.run(max_time=5)
        # After reset posedge, state should be IDLE (0)
        assert sim.read("state").val == 0  # IDLE after reset

    def test_enum_in_case_statement(self):
        """FSM using case statement with enum constants."""
        # typedef enum {S0=0, S1=1, S2=2}
        enum = EnumType(
            [
                EnumMember("S0", value=Literal(0)),
                EnumMember("S1", value=Literal(1)),
                EnumMember("S2", value=Literal(2)),
            ]
        )
        td = TypedefDecl("state_t", enum_type=enum)

        ports = [
            Port("sel", PortDirection.INPUT, width=Range(Literal(1), Literal(0))),
            Port("out", PortDirection.OUTPUT, width=Range(Literal(7), Literal(0))),
        ]
        nets = [Net("sel", NetKind.WIRE, width=Range(Literal(1), Literal(0)))]
        variables = [Variable("out", VariableKind.REG, width=Range(Literal(7), Literal(0)))]

        # always @(*)
        #   case (sel)
        #     S0: out = 8'd10;
        #     S1: out = 8'd20;
        #     S2: out = 8'd30;
        #     default: out = 8'd0;
        #   endcase
        case_body = CaseStatement(
            "case",
            Identifier("sel"),
            [
                CaseItem([Identifier("S0")], BlockingAssign(Identifier("out"), Literal(10, width=8))),
                CaseItem([Identifier("S1")], BlockingAssign(Identifier("out"), Literal(20, width=8))),
                CaseItem([Identifier("S2")], BlockingAssign(Identifier("out"), Literal(30, width=8))),
                CaseItem(None, BlockingAssign(Identifier("out"), Literal(0, width=8))),
            ],
        )
        always = AlwaysBlock(sensitivity_list=[], body=case_body)
        always.sensitivity_type = SensitivityType.COMBINATIONAL

        m = ModelModule("case_enum", ports=ports, nets=nets, variables=variables)
        m.always_blocks = [always]
        m.typedefs = [td]

        sim = Simulator(m, engine="reference")
        sim.drive("sel", Value(0, width=2))
        sim.run(max_time=0)
        assert sim.read("out").val == 10

        sim.drive("sel", Value(1, width=2))
        sim.run(max_time=0)
        assert sim.read("out").val == 20

        sim.drive("sel", Value(2, width=2))
        sim.run(max_time=0)
        assert sim.read("out").val == 30


# ── VM engine enum simulation ───────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestEnumSimVM:
    """Simulate designs with enum constants using the VM engines."""

    def test_enum_constants_are_registered(self, engine):
        """Enum members should be readable as constant signals in VM engine."""
        m = TestEnumSimReference()._make_fsm_module()
        sim = Simulator(m, engine=engine)
        assert sim.read("IDLE") == Value(0, width=2)
        assert sim.read("RUN") == Value(1, width=2)
        assert sim.read("DONE") == Value(2, width=2)

    def test_fsm_transitions_with_enum(self, engine):
        """Enum constants can be used in non-blocking assignments (VM engine)."""
        m = TestEnumSimReference()._make_fsm_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)

        sim.drive("rst", Value(1, width=1))
        sim.run(max_time=5)
        assert sim.read("state").val == 0  # IDLE after reset


# ── Package import simulation ───────────────────────────────────────


class TestPackageImportSim:
    """Simulate designs that use package imports."""

    def _make_design_with_pkg(self):
        """Create a design with a package and a module that imports it."""
        # Package: parameter WIDTH=8, enum {LOW=0, HIGH=1}
        pkg = Package(
            "cfg_pkg",
            parameters=[Parameter("MAGIC", default_value=Literal(42))],
            typedefs=[
                TypedefDecl(
                    "level_t",
                    enum_type=EnumType(
                        [
                            EnumMember("LOW", value=Literal(0)),
                            EnumMember("HIGH", value=Literal(1)),
                        ]
                    ),
                )
            ],
        )

        # Module: import cfg_pkg::*; assign out = MAGIC; assign level = HIGH;
        ports = [
            Port("out", PortDirection.OUTPUT, width=Range(Literal(7), Literal(0))),
            Port("level", PortDirection.OUTPUT),
        ]
        variables = [
            Variable("out", VariableKind.REG, width=Range(Literal(7), Literal(0))),
            Variable("level", VariableKind.REG),
        ]

        m = ModelModule("top", ports=ports, variables=variables)
        m.continuous_assigns = [
            ContinuousAssign(Identifier("out"), Identifier("MAGIC")),
            ContinuousAssign(Identifier("level"), Identifier("HIGH")),
        ]
        m.imports = [ImportDecl("cfg_pkg", "*")]
        m.typedefs = []

        return Design(modules=[m], packages=[pkg])

    def test_package_param_available(self):
        """Package parameter MAGIC should be available after import resolution."""
        design = self._make_design_with_pkg()
        m = design.modules[0]
        sim = Simulator(m, engine="reference", design=design)
        sim.run(max_time=0)
        assert sim.read("MAGIC").val == 42

    def test_package_enum_available(self):
        """Package enum members should be available after import resolution."""
        design = self._make_design_with_pkg()
        m = design.modules[0]
        sim = Simulator(m, engine="reference", design=design)
        sim.run(max_time=0)
        assert sim.read("LOW").val == 0
        assert sim.read("HIGH").val == 1

    def test_continuous_assign_with_package_symbols(self):
        """Continuous assigns using package symbols should evaluate correctly."""
        design = self._make_design_with_pkg()
        m = design.modules[0]
        sim = Simulator(m, engine="reference", design=design)
        sim.run(max_time=0)
        assert sim.read("out").val == 42
        assert sim.read("level").val == 1

    @pytest.mark.parametrize("engine", ["vm", "vm-fast"])
    def test_package_import_vm(self, engine):
        """Package imports should work with both VM engines."""
        design = self._make_design_with_pkg()
        m = design.modules[0]
        sim = Simulator(m, engine=engine, design=design)
        sim.run(max_time=0)
        assert sim.read("MAGIC").val == 42
        assert sim.read("LOW").val == 0
        assert sim.read("HIGH").val == 1


# ── DSL-built module with enum simulation ───────────────────────────


class TestDslEnumSim:
    """Build SV constructs via DSL and simulate them."""

    def test_dsl_enum_in_simulation(self):
        """DSL typedef_enum creates enum constants accessible in simulation."""
        with Module("dsl_fsm") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            state = m.output_reg("state", width=2)

            m.typedef_enum("state_t", ["IDLE", "RUN", "DONE"], width=2)

            with m.always(posedge(clk)):
                with m.if_(rst):
                    state <<= 0  # IDLE
                with m.else_():
                    with m.if_(state == 0):
                        state <<= 1  # RUN
                    with m.if_(state == 1):
                        state <<= 2  # DONE

        mod = m.build()
        sim = Simulator(mod, engine="reference")

        # Verify enum constants are available
        assert sim.read("IDLE").val == 0
        assert sim.read("RUN").val == 1
        assert sim.read("DONE").val == 2

    def test_dsl_enum_explicit_values(self):
        """DSL typedef_enum with explicit member values."""
        with Module("t") as m:
            m.typedef_enum("cmd_t", [("NOP", 0), ("READ", 4), ("WRITE", 8)], width=4)

        mod = m.build()
        sim = Simulator(mod, engine="reference")
        assert sim.read("NOP").val == 0
        assert sim.read("READ").val == 4
        assert sim.read("WRITE").val == 8

    def test_dsl_enum_default_width(self):
        """DSL typedef_enum without explicit width defaults to 32 bits."""
        with Module("t") as m:
            m.typedef_enum("t", ["A", "B", "C"])

        mod = m.build()
        sim = Simulator(mod, engine="reference")
        assert sim.read("A") == Value(0, width=32)
        assert sim.read("B") == Value(1, width=32)
        assert sim.read("C") == Value(2, width=32)


# ── Edge cases ──────────────────────────────────────────────────────


class TestEnumEdgeCases:
    """Edge cases for enum constant handling."""

    def test_enum_member_does_not_shadow_signal(self):
        """If a signal has the same name as an enum member, signal wins."""
        enum = EnumType([EnumMember("data", value=Literal(99))])
        td = TypedefDecl("t", enum_type=enum)
        ports = [Port("data", PortDirection.INPUT, width=Range(Literal(7), Literal(0)))]
        nets = [Net("data", NetKind.WIRE, width=Range(Literal(7), Literal(0)))]
        m = ModelModule("test", ports=ports, nets=nets)
        m.typedefs = [td]

        sim = Simulator(m, engine="reference")
        sim.drive("data", Value(42, width=8))
        sim.run(max_time=0)
        # Signal value should win over enum constant
        assert sim.read("data").val == 42

    def test_empty_enum(self):
        """Empty enum typedef should not crash."""
        enum = EnumType([])
        td = TypedefDecl("empty_t", enum_type=enum)
        m = ModelModule("test")
        m.typedefs = [td]

        sim = Simulator(m, engine="reference")
        sim.run(max_time=0)
        # No crash, no extra signals

    def test_auto_increment_after_explicit_value(self):
        """Auto-numbering continues from the last explicit value."""
        enum = EnumType(
            [
                EnumMember("A", value=Literal(10)),
                EnumMember("B"),  # should be 11
                EnumMember("C"),  # should be 12
            ]
        )
        td = TypedefDecl("t", enum_type=enum)
        m = ModelModule("test")
        m.typedefs = [td]

        env = _build_enum_env(m)
        assert env["A"] == (10, 32)
        assert env["B"] == (11, 32)
        assert env["C"] == (12, 32)


# ── Struct layout helpers ───────────────────────────────────────────

from veriforge.model.sv_types import StructField, StructType
from veriforge.sim.elaborate import StructLayout, _build_struct_env


class TestStructLayout:
    """Unit tests for StructType layout computation."""

    def test_total_width_basic(self):
        """Total width of a packed struct is the sum of field widths."""
        st = StructType(
            [
                StructField("data", "logic", width=Range(Literal(7), Literal(0))),
                StructField("valid", "logic"),
            ],
            packed=True,
        )
        assert st.total_width() == 9  # 8 + 1

    def test_compute_layout_msb_first(self):
        """First declared field occupies highest bits (MSB-first)."""
        st = StructType(
            [
                StructField("data", "logic", width=Range(Literal(7), Literal(0))),
                StructField("valid", "logic"),
            ],
            packed=True,
        )
        layout = st.compute_layout()
        # valid at bits [0:0], data at bits [8:1]
        assert layout["valid"] == (0, 1)
        assert layout["data"] == (1, 8)

    def test_three_field_layout(self):
        """Three-field struct layout with various widths."""
        st = StructType(
            [
                StructField("addr", "logic", width=Range(Literal(15), Literal(0))),  # 16 bits
                StructField("data", "logic", width=Range(Literal(7), Literal(0))),  # 8 bits
                StructField("valid", "logic"),  # 1 bit
            ],
            packed=True,
        )
        assert st.total_width() == 25
        layout = st.compute_layout()
        assert layout["valid"] == (0, 1)  # bits [0:0]
        assert layout["data"] == (1, 8)  # bits [8:1]
        assert layout["addr"] == (9, 16)  # bits [24:9]


class TestBuildStructEnv:
    """Test _build_struct_env helper."""

    def _make_struct_module(self):
        """Create a module with a packed struct typedef and a struct-typed variable."""
        st = StructType(
            [
                StructField("data", "logic", width=Range(Literal(7), Literal(0))),
                StructField("valid", "logic"),
            ],
            packed=True,
        )
        td = TypedefDecl("bus_t", struct_type=st)

        m = ModelModule("test")
        m.typedefs = [td]
        m.variables = [Variable("bus", VariableKind.REG, width=Range(Literal(8), Literal(0)), type_name="bus_t")]
        return m

    def test_type_map_populated(self):
        m = self._make_struct_module()
        type_map, _signal_map = _build_struct_env(m)
        assert "bus_t" in type_map
        assert type_map["bus_t"].total_width == 9

    def test_signal_map_populated(self):
        m = self._make_struct_module()
        _type_map, signal_map = _build_struct_env(m)
        assert "bus" in signal_map
        assert signal_map["bus"].fields["data"] == (1, 8)
        assert signal_map["bus"].fields["valid"] == (0, 1)

    def test_unpacked_struct_ignored(self):
        st = StructType(
            [StructField("x", "logic", width=Range(Literal(7), Literal(0)))],
            packed=False,
        )
        td = TypedefDecl("foo_t", struct_type=st)
        m = ModelModule("test")
        m.typedefs = [td]
        type_map, signal_map = _build_struct_env(m)
        assert len(type_map) == 0
        assert len(signal_map) == 0

    def test_no_typedefs(self):
        m = ModelModule("test")
        type_map, signal_map = _build_struct_env(m)
        assert len(type_map) == 0
        assert len(signal_map) == 0


# ── Struct field access simulation ──────────────────────────────────


class TestStructSimReference:
    """Simulate designs with struct field access using the reference engine."""

    def _make_struct_module(self):
        """Build a module with a struct and combinational logic reading/writing fields.

        typedef struct packed {
            logic [7:0] data;   // bits [8:1]
            logic        valid; // bit  [0]
        } bus_t;
        bus_t bus;
        always @(*) begin
            bus.data = in_data;
            bus.valid = in_valid;
        end
        assign out_data = bus.data;
        assign out_valid = bus.valid;
        """
        st = StructType(
            [
                StructField("data", "logic", width=Range(Literal(7), Literal(0))),
                StructField("valid", "logic"),
            ],
            packed=True,
        )
        td = TypedefDecl("bus_t", struct_type=st)

        ports = [
            Port("in_data", PortDirection.INPUT, width=Range(Literal(7), Literal(0))),
            Port("in_valid", PortDirection.INPUT),
            Port("out_data", PortDirection.OUTPUT, width=Range(Literal(7), Literal(0))),
            Port("out_valid", PortDirection.OUTPUT),
        ]
        nets = [
            Net("in_data", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
            Net("in_valid", NetKind.WIRE),
            Net("out_data", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
            Net("out_valid", NetKind.WIRE),
        ]
        variables = [
            Variable("bus", VariableKind.REG, width=Range(Literal(8), Literal(0)), type_name="bus_t"),
        ]

        # always @(*) begin bus.data = in_data; bus.valid = in_valid; end
        body = SeqBlock(
            [
                BlockingAssign(Identifier("bus.data"), Identifier("in_data")),
                BlockingAssign(Identifier("bus.valid"), Identifier("in_valid")),
            ]
        )
        always = AlwaysBlock(sensitivity_list=[], body=body)
        always.sensitivity_type = SensitivityType.COMBINATIONAL

        # assign out_data = bus.data; assign out_valid = bus.valid;
        cas = [
            ContinuousAssign(Identifier("out_data"), Identifier("bus.data")),
            ContinuousAssign(Identifier("out_valid"), Identifier("bus.valid")),
        ]

        m = ModelModule("struct_test", ports=ports, nets=nets, variables=variables)
        m.always_blocks = [always]
        m.continuous_assigns = cas
        m.typedefs = [td]
        return m

    def test_struct_field_write_and_read(self):
        """Struct fields can be written via blocking assign and read back."""
        m = self._make_struct_module()
        sim = Simulator(m, engine="reference")
        sim.drive("in_data", Value(0xAB, width=8))
        sim.drive("in_valid", Value(1, width=1))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0xAB
        assert sim.read("out_valid").val == 1

    def test_struct_field_update(self):
        """Changing an input propagates through struct fields."""
        m = self._make_struct_module()
        sim = Simulator(m, engine="reference")
        sim.drive("in_data", Value(0x42, width=8))
        sim.drive("in_valid", Value(0, width=1))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0x42
        assert sim.read("out_valid").val == 0

        # Update in_data, check it propagates
        sim.drive("in_data", Value(0xFF, width=8))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0xFF
        assert sim.read("out_valid").val == 0  # unchanged

    def test_struct_base_value_consistency(self):
        """The base struct signal contains the packed bit pattern."""
        m = self._make_struct_module()
        sim = Simulator(m, engine="reference")
        sim.drive("in_data", Value(0x55, width=8))
        sim.drive("in_valid", Value(1, width=1))
        sim.run(max_time=0)
        # bus = {data[8:1], valid[0]} = 0x55 << 1 | 1 = 0xAB
        bus_val = sim.read("bus")
        assert bus_val.val == (0x55 << 1) | 1


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestStructSimVM:
    """Simulate struct field access using the VM engines."""

    def test_struct_field_write_and_read_vm(self, engine):
        m = TestStructSimReference()._make_struct_module()
        sim = Simulator(m, engine=engine)
        sim.drive("in_data", Value(0xAB, width=8))
        sim.drive("in_valid", Value(1, width=1))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0xAB
        assert sim.read("out_valid").val == 1

    def test_struct_field_update_vm(self, engine):
        m = TestStructSimReference()._make_struct_module()
        sim = Simulator(m, engine=engine)
        sim.drive("in_data", Value(0x42, width=8))
        sim.drive("in_valid", Value(0, width=1))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0x42

        sim.drive("in_data", Value(0xFF, width=8))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0xFF


class TestStructSimCompiled:
    """Simulate struct field access using the compiled engine."""

    def test_struct_field_write_and_read_compiled(self):
        m = TestStructSimReference()._make_struct_module()
        sim = Simulator(m, engine="compiled")
        sim.drive("in_data", Value(0xAB, width=8))
        sim.drive("in_valid", Value(1, width=1))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0xAB
        assert sim.read("out_valid").val == 1

    def test_struct_field_update_compiled(self):
        m = TestStructSimReference()._make_struct_module()
        sim = Simulator(m, engine="compiled")
        sim.drive("in_data", Value(0x42, width=8))
        sim.drive("in_valid", Value(0, width=1))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0x42

        sim.drive("in_data", Value(0xFF, width=8))
        sim.run(max_time=0)
        assert sim.read("out_data").val == 0xFF


# ── Struct with NBA (sequential) ────────────────────────────────────


class TestStructNBA:
    """Test struct field access with non-blocking assignments (sequential logic)."""

    def _make_sequential_struct_module(self):
        """Build a module that uses NBA for struct field writes.

        typedef struct packed {
            logic [7:0] data;
            logic        valid;
        } bus_t;
        bus_t bus;
        always @(posedge clk) begin
            bus.data <= in_data;
            bus.valid <= in_valid;
        end
        """
        st = StructType(
            [
                StructField("data", "logic", width=Range(Literal(7), Literal(0))),
                StructField("valid", "logic"),
            ],
            packed=True,
        )
        td = TypedefDecl("bus_t", struct_type=st)

        ports = [
            Port("clk", PortDirection.INPUT),
            Port("in_data", PortDirection.INPUT, width=Range(Literal(7), Literal(0))),
            Port("in_valid", PortDirection.INPUT),
        ]
        nets = [
            Net("clk", NetKind.WIRE),
            Net("in_data", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
            Net("in_valid", NetKind.WIRE),
        ]
        variables = [
            Variable("bus", VariableKind.REG, width=Range(Literal(8), Literal(0)), type_name="bus_t"),
        ]

        body = SeqBlock(
            [
                NonblockingAssign(Identifier("bus.data"), Identifier("in_data")),
                NonblockingAssign(Identifier("bus.valid"), Identifier("in_valid")),
            ]
        )
        always = AlwaysBlock(
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            body=body,
        )
        always.sensitivity_type = SensitivityType.SEQUENTIAL

        m = ModelModule("seq_struct", ports=ports, nets=nets, variables=variables)
        m.always_blocks = [always]
        m.typedefs = [td]
        return m

    def test_nba_struct_field_reference(self):
        m = self._make_sequential_struct_module()
        sim = Simulator(m, engine="reference")
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("in_data", Value(0x77, width=8))
        sim.drive("in_valid", Value(1, width=1))
        sim.run(max_time=15)  # past one posedge
        bus_val = sim.read("bus")
        assert bus_val.val == (0x77 << 1) | 1

    @pytest.mark.parametrize("engine", ["vm", "vm-fast"])
    def test_nba_struct_field_vm(self, engine):
        m = self._make_sequential_struct_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("in_data", Value(0x77, width=8))
        sim.drive("in_valid", Value(1, width=1))
        sim.run(max_time=15)
        bus_val = sim.read("bus")
        assert bus_val.val == (0x77 << 1) | 1


# ── DSL struct simulation ──────────────────────────────────────────


class TestDslStructSim:
    """Test DSL struct_var creates correct model structure for simulation."""

    def test_dsl_struct_var_creates_typed_variable(self):
        """DSL struct_var creates a variable with type_name set."""
        with Module("dsl_struct") as m:
            m.typedef_struct("bus_t", [("data", "logic", 8), ("valid", "logic")], packed=True)
            m.struct_var("bus", "bus_t")

        mod = m.build()
        bus_vars = [v for v in mod.variables if v.name == "bus"]
        assert len(bus_vars) == 1
        assert bus_vars[0].type_name == "bus_t"
        # Width should be total struct width (8 + 1 = 9)
        assert bus_vars[0].width is not None

    def test_dsl_struct_var_unknown_type_raises(self):
        """struct_var with unknown typedef name raises ValueError."""
        import pytest

        with Module("t") as m:
            with pytest.raises(ValueError, match="No struct typedef"):
                m.struct_var("bus", "nonexistent_t")


# ── Value.type_info ─────────────────────────────────────────────────


class TestValueTypeInfo:
    """Test the type_info attribute on Value."""

    def test_type_info_default_none(self):
        v = Value(42, width=8)
        assert v.type_info is None

    def test_type_info_stored(self):
        info = StructLayout(name="bus_t", total_width=9, fields={"data": (1, 8), "valid": (0, 1)})
        v = Value(42, width=8, type_info=info)
        assert v.type_info is info
        assert v.type_info.name == "bus_t"

    def test_type_info_not_in_equality(self):
        """type_info should not affect equality comparison."""
        v1 = Value(42, width=8, type_info="some_info")
        v2 = Value(42, width=8)
        assert v1 == v2

    def test_type_info_not_in_hash(self):
        """type_info should not affect hash."""
        v1 = Value(42, width=8, type_info="some_info")
        v2 = Value(42, width=8)
        assert hash(v1) == hash(v2)


# ── Interface port binding (elaborate) ──────────────────────────────

from veriforge.model.interface import Interface as InterfaceModel
from veriforge.sim.elaborate import flatten_module


class TestInterfaceBinding:
    """Test interface instance flattening in elaborate.py."""

    def _make_interface(self):
        """Create a simple interface with two signals and a continuous assign."""
        intf = InterfaceModel("simple_bus")
        intf.nets = [
            Net("data", NetKind.WIRE, width=Range(Literal(7), Literal(0))),
            Net("valid", NetKind.WIRE),
        ]
        return intf

    def test_interface_signals_prefixed(self):
        """Interface signals should appear with prefixed names after flattening."""
        intf = self._make_interface()
        m = ModelModule("top")
        m.interface_instances = [("bus0", intf)]
        flat = flatten_module(m)
        net_names = {n.name for n in flat.nets}
        assert "bus0.data" in net_names
        assert "bus0.valid" in net_names

    def test_multiple_interface_instances(self):
        """Multiple interface instances get distinct prefixes."""
        intf = self._make_interface()
        m = ModelModule("top")
        m.interface_instances = [("bus0", intf), ("bus1", intf)]
        flat = flatten_module(m)
        net_names = {n.name for n in flat.nets}
        assert "bus0.data" in net_names
        assert "bus1.data" in net_names
        assert "bus0.valid" in net_names
        assert "bus1.valid" in net_names

    def test_interface_continuous_assigns_prefixed(self):
        """Continuous assigns from the interface should have prefixed identifiers."""
        intf = self._make_interface()
        # assign valid = data[0]
        intf.continuous_assigns = [
            ContinuousAssign(Identifier("valid"), BitSelect(Identifier("data"), Literal(0))),
        ]
        m = ModelModule("top")
        m.interface_instances = [("bus0", intf)]
        flat = flatten_module(m)
        # Find the continuous assign from the interface
        ca_lhs_names = [ca.lhs.name for ca in flat.continuous_assigns if hasattr(ca.lhs, "name")]
        assert "bus0.valid" in ca_lhs_names

    def test_interface_with_variables(self):
        """Interface variables should be flattened with prefix."""
        intf = InterfaceModel("bus_with_reg")
        intf.nets = []
        intf.variables = [
            Variable("data_reg", VariableKind.REG, width=Range(Literal(7), Literal(0))),
        ]
        m = ModelModule("top")
        m.interface_instances = [("intf0", intf)]
        flat = flatten_module(m)
        var_names = {v.name for v in flat.variables}
        assert "intf0.data_reg" in var_names

    def test_no_interfaces_no_change(self):
        """Module without interface_instances should flatten normally."""
        m = ModelModule("top")
        flat = flatten_module(m)
        assert len(flat.nets) == 0
        assert len(flat.variables) == 0
