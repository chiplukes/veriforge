"""Tests for SystemVerilog DSL builder methods and translator support.

Tests cover:
- typedef_enum, typedef_struct, typedef_union, typedef_alias builder methods
- import_pkg builder method
- to_dsl translator round-trip for SV constructs
- design_to_dsl with packages and interfaces
"""

from __future__ import annotations

from veriforge.convert.to_dsl import (
    design_to_dsl,
    interface_to_dsl,
    module_to_dsl,
    package_to_dsl,
)
from veriforge.dsl import Module, posedge
from veriforge.model.design import Design
from veriforge.model.expressions import Literal, Range
from veriforge.model.interface import Interface, Modport, ModportPort
from veriforge.model.nets import Net, NetKind
from veriforge.model.package import ImportDecl, Package
from veriforge.model.parameters import Parameter
from veriforge.model.ports import PortDirection
from veriforge.model.sv_types import (
    EnumMember,
    EnumType,
    StructField,
    StructType,
    TypedefDecl,
    UnionType,
)
from veriforge.model.variables import Variable, VariableKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exec_dsl(code: str):
    """Execute DSL code and return the built module."""
    ns = {}
    exec(code, ns)  # noqa: S102
    return ns["module"]


# ===================================================================
# Builder: typedef_enum
# ===================================================================


class TestTypedefEnum:
    """Test Module.typedef_enum() builder method."""

    def test_simple_enum(self):
        m = Module("top")
        m.typedef_enum("state_t", ["IDLE", "RUN", "DONE"])
        mod = m.build()
        assert len(mod.typedefs) == 1
        td = mod.typedefs[0]
        assert td.name == "state_t"
        assert td.enum_type is not None
        assert len(td.enum_type.members) == 3
        assert td.enum_type.members[0].name == "IDLE"
        assert td.enum_type.members[0].value is None

    def test_enum_with_values(self):
        m = Module("top")
        m.typedef_enum("state_t", [("IDLE", 0), ("RUN", 1), ("DONE", 2)])
        mod = m.build()
        td = mod.typedefs[0]
        assert td.enum_type.members[0].value.value == 0
        assert td.enum_type.members[1].value.value == 1
        assert td.enum_type.members[2].value.value == 2

    def test_enum_with_width(self):
        m = Module("top")
        m.typedef_enum("state_t", ["IDLE", "RUN"], width=2)
        mod = m.build()
        td = mod.typedefs[0]
        assert td.enum_type.width is not None
        assert td.enum_type.width.msb.value == 1  # [1:0]

    def test_enum_with_base_type(self):
        m = Module("top")
        m.typedef_enum("state_t", ["IDLE"], base_type="logic")
        mod = m.build()
        assert mod.typedefs[0].enum_type.base_type == "logic"

    def test_enum_signed(self):
        m = Module("top")
        m.typedef_enum("stype", ["A", "B"], signed=True)
        mod = m.build()
        assert mod.typedefs[0].enum_type.signed is True

    def test_multiple_enums(self):
        m = Module("top")
        m.typedef_enum("state_t", ["IDLE", "RUN"])
        m.typedef_enum("cmd_t", ["READ", "WRITE"])
        mod = m.build()
        assert len(mod.typedefs) == 2
        assert mod.typedefs[0].name == "state_t"
        assert mod.typedefs[1].name == "cmd_t"


# ===================================================================
# Builder: typedef_struct
# ===================================================================


class TestTypedefStruct:
    """Test Module.typedef_struct() builder method."""

    def test_simple_struct(self):
        m = Module("top")
        m.typedef_struct("bus_t", [("data", "logic", 8), ("valid", "logic")])
        mod = m.build()
        assert len(mod.typedefs) == 1
        td = mod.typedefs[0]
        assert td.name == "bus_t"
        assert td.struct_type is not None
        assert len(td.struct_type.fields) == 2
        assert td.struct_type.fields[0].name == "data"
        assert td.struct_type.fields[0].data_type == "logic"
        assert td.struct_type.fields[0].width is not None
        assert td.struct_type.fields[1].name == "valid"
        assert td.struct_type.fields[1].width is None

    def test_struct_packed(self):
        m = Module("top")
        m.typedef_struct("bus_t", [("data", "logic", 8)], packed=True)
        mod = m.build()
        assert mod.typedefs[0].struct_type.packed is True

    def test_struct_signed(self):
        m = Module("top")
        m.typedef_struct("bus_t", [("data", "logic")], signed=True)
        mod = m.build()
        assert mod.typedefs[0].struct_type.signed is True


# ===================================================================
# Builder: typedef_union
# ===================================================================


class TestTypedefUnion:
    """Test Module.typedef_union() builder method."""

    def test_simple_union(self):
        m = Module("top")
        m.typedef_union("word_t", [("word", "logic", 32), ("byte_val", "logic", 8)])
        mod = m.build()
        assert len(mod.typedefs) == 1
        td = mod.typedefs[0]
        assert td.name == "word_t"
        assert td.union_type is not None
        assert len(td.union_type.fields) == 2

    def test_union_packed(self):
        m = Module("top")
        m.typedef_union("word_t", [("word", "logic", 32)], packed=True)
        mod = m.build()
        assert mod.typedefs[0].union_type.packed is True


# ===================================================================
# Builder: typedef_alias
# ===================================================================


class TestTypedefAlias:
    """Test Module.typedef_alias() builder method."""

    def test_simple_alias(self):
        m = Module("top")
        m.typedef_alias("byte_t", "logic [7:0]")
        mod = m.build()
        assert len(mod.typedefs) == 1
        td = mod.typedefs[0]
        assert td.name == "byte_t"
        assert td.type_ref == "logic [7:0]"


# ===================================================================
# Builder: import_pkg
# ===================================================================


class TestImportPkg:
    """Test Module.import_pkg() builder method."""

    def test_wildcard_import(self):
        m = Module("top")
        m.import_pkg("my_pkg")
        mod = m.build()
        assert len(mod.imports) == 1
        imp = mod.imports[0]
        assert imp.package_name == "my_pkg"
        assert imp.item_name == "*"
        assert imp.is_wildcard is True

    def test_specific_import(self):
        m = Module("top")
        m.import_pkg("my_pkg", "WIDTH")
        mod = m.build()
        imp = mod.imports[0]
        assert imp.package_name == "my_pkg"
        assert imp.item_name == "WIDTH"
        assert imp.is_wildcard is False

    def test_multiple_imports(self):
        m = Module("top")
        m.import_pkg("pkg_a")
        m.import_pkg("pkg_b", "ITEM")
        mod = m.build()
        assert len(mod.imports) == 2


# ===================================================================
# Translator: module_to_dsl with SV constructs
# ===================================================================


class TestModuleToDslSV:
    """Test to_dsl translator for SV constructs on Module."""

    def test_typedef_enum_round_trip(self):
        m = Module("top")
        m.input("clk")
        m.typedef_enum("state_t", [("IDLE", 0), ("RUN", 1), ("DONE", 2)], width=2)
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'typedef_enum("state_t"' in code
        assert '("IDLE", 0)' in code
        assert '("RUN", 1)' in code
        assert '("DONE", 2)' in code
        assert "width=2" in code
        # Execute the generated code and verify round-trip
        mod2 = _exec_dsl(code)
        assert len(mod2.typedefs) == 1
        assert mod2.typedefs[0].name == "state_t"
        assert len(mod2.typedefs[0].enum_type.members) == 3

    def test_typedef_enum_no_values(self):
        m = Module("top")
        m.typedef_enum("cmd_t", ["READ", "WRITE", "NOP"])
        mod = m.build()
        code = module_to_dsl(mod)
        assert '"READ"' in code
        assert '"WRITE"' in code
        mod2 = _exec_dsl(code)
        assert len(mod2.typedefs[0].enum_type.members) == 3
        assert mod2.typedefs[0].enum_type.members[0].value is None

    def test_typedef_enum_base_type(self):
        m = Module("top")
        m.typedef_enum("state_t", ["IDLE"], base_type="logic", width=2)
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'base_type="logic"' in code
        mod2 = _exec_dsl(code)
        assert mod2.typedefs[0].enum_type.base_type == "logic"

    def test_typedef_enum_signed(self):
        m = Module("top")
        m.typedef_enum("stype", ["A", "B"], signed=True)
        mod = m.build()
        code = module_to_dsl(mod)
        assert "signed=True" in code
        mod2 = _exec_dsl(code)
        assert mod2.typedefs[0].enum_type.signed is True

    def test_typedef_struct_round_trip(self):
        m = Module("top")
        m.typedef_struct("bus_t", [("data", "logic", 8), ("valid", "logic")], packed=True)
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'typedef_struct("bus_t"' in code
        assert '("data", "logic", 8)' in code
        assert '("valid", "logic")' in code
        assert "packed=True" in code
        mod2 = _exec_dsl(code)
        assert len(mod2.typedefs) == 1
        st = mod2.typedefs[0].struct_type
        assert st.packed is True
        assert len(st.fields) == 2
        assert st.fields[0].name == "data"

    def test_typedef_struct_signed(self):
        m = Module("top")
        m.typedef_struct("bus_t", [("data", "logic")], signed=True)
        mod = m.build()
        code = module_to_dsl(mod)
        mod2 = _exec_dsl(code)
        assert mod2.typedefs[0].struct_type.signed is True

    def test_typedef_union_round_trip(self):
        m = Module("top")
        m.typedef_union("word_t", [("word", "logic", 32), ("byte_val", "logic", 8)], packed=True)
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'typedef_union("word_t"' in code
        assert "packed=True" in code
        mod2 = _exec_dsl(code)
        assert len(mod2.typedefs) == 1
        ut = mod2.typedefs[0].union_type
        assert ut.packed is True
        assert len(ut.fields) == 2

    def test_typedef_alias_round_trip(self):
        m = Module("top")
        m.typedef_alias("byte_t", "logic [7:0]")
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'typedef_alias("byte_t", "logic [7:0]")' in code
        mod2 = _exec_dsl(code)
        assert mod2.typedefs[0].type_ref == "logic [7:0]"

    def test_import_wildcard_round_trip(self):
        m = Module("top")
        m.import_pkg("my_pkg")
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'import_pkg("my_pkg")' in code
        mod2 = _exec_dsl(code)
        assert len(mod2.imports) == 1
        assert mod2.imports[0].package_name == "my_pkg"
        assert mod2.imports[0].is_wildcard is True

    def test_import_specific_round_trip(self):
        m = Module("top")
        m.import_pkg("my_pkg", "WIDTH")
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'import_pkg("my_pkg", "WIDTH")' in code
        mod2 = _exec_dsl(code)
        assert mod2.imports[0].item_name == "WIDTH"

    def test_mixed_sv_constructs(self):
        """Module with enums, structs, imports, and regular Verilog."""
        m = Module("top")
        clk = m.input("clk")
        m.import_pkg("my_pkg")
        m.typedef_enum("state_t", [("IDLE", 0), ("ACTIVE", 1)], width=2)
        m.typedef_struct("pkt_t", [("data", "logic", 8), ("valid", "logic")], packed=True)
        out = m.output_reg("out")
        with m.always(posedge(clk)):
            out <<= 0
        mod = m.build()
        code = module_to_dsl(mod)
        mod2 = _exec_dsl(code)
        assert len(mod2.imports) == 1
        assert len(mod2.typedefs) == 2
        assert len(mod2.ports) == 2
        assert len(mod2.always_blocks) == 1


# ===================================================================
# Translator: package_to_dsl
# ===================================================================


class TestPackageToDsl:
    """Test package_to_dsl translator."""

    def test_empty_package(self):
        pkg = Package("empty_pkg")
        code = package_to_dsl(pkg)
        assert "Package: empty_pkg" in code

    def test_package_with_params(self):
        pkg = Package(
            "my_pkg",
            parameters=[
                Parameter("WIDTH", default_value=Literal(8, original_text="8"), is_local=True),
                Parameter("DEPTH", default_value=Literal(4, original_text="4"), is_local=False),
            ],
        )
        code = package_to_dsl(pkg)
        assert "localparam WIDTH = 8" in code
        assert "parameter DEPTH = 4" in code

    def test_package_with_typedef_enum(self):
        pkg = Package(
            "my_pkg",
            typedefs=[
                TypedefDecl(
                    "state_t",
                    enum_type=EnumType(
                        [
                            EnumMember("IDLE"),
                            EnumMember("RUN"),
                        ]
                    ),
                ),
            ],
        )
        code = package_to_dsl(pkg)
        assert "typedef enum { IDLE, RUN } state_t" in code

    def test_package_with_imports(self):
        pkg = Package(
            "my_pkg",
            imports=[ImportDecl("other_pkg", "*")],
        )
        code = package_to_dsl(pkg)
        assert "import other_pkg::*" in code

    def test_package_with_typedef_struct(self):
        pkg = Package(
            "my_pkg",
            typedefs=[
                TypedefDecl(
                    "bus_t",
                    struct_type=StructType(
                        [
                            StructField("data", "logic"),
                            StructField("valid", "logic"),
                        ]
                    ),
                ),
            ],
        )
        code = package_to_dsl(pkg)
        assert "typedef struct { data, valid } bus_t" in code

    def test_package_with_typedef_union(self):
        pkg = Package(
            "my_pkg",
            typedefs=[
                TypedefDecl(
                    "word_t",
                    union_type=UnionType(
                        [
                            StructField("word", "logic"),
                        ]
                    ),
                ),
            ],
        )
        code = package_to_dsl(pkg)
        assert "typedef union { word } word_t" in code

    def test_package_with_typedef_alias(self):
        pkg = Package(
            "my_pkg",
            typedefs=[
                TypedefDecl("byte_t", type_ref="logic [7:0]"),
            ],
        )
        code = package_to_dsl(pkg)
        assert "typedef logic [7:0] byte_t" in code


# ===================================================================
# Translator: interface_to_dsl
# ===================================================================


class TestInterfaceToDsl:
    """Test interface_to_dsl translator."""

    def test_empty_interface(self):
        intf = Interface("empty_intf")
        code = interface_to_dsl(intf)
        assert "Interface: empty_intf" in code

    def test_interface_with_nets(self):
        intf = Interface(
            "axi_lite",
            nets=[
                Net(
                    "awaddr", NetKind.WIRE, width=Range(Literal(31, original_text="31"), Literal(0, original_text="0"))
                ),
                Net("awvalid", NetKind.WIRE),
            ],
        )
        code = interface_to_dsl(intf)
        assert "wire" in code
        assert "awaddr" in code
        assert "awvalid" in code

    def test_interface_with_modport(self):
        intf = Interface(
            "axi_lite",
            modports=[
                Modport(
                    "master",
                    [
                        ModportPort("awaddr", PortDirection.OUTPUT),
                        ModportPort("awready", PortDirection.INPUT),
                    ],
                ),
            ],
        )
        code = interface_to_dsl(intf)
        assert "modport master" in code
        assert "output awaddr" in code
        assert "input awready" in code

    def test_interface_with_params(self):
        intf = Interface(
            "axi_lite",
            parameters=[
                Parameter("ADDR_W", default_value=Literal(32, original_text="32")),
            ],
        )
        code = interface_to_dsl(intf)
        assert "parameter ADDR_W = 32" in code

    def test_interface_with_imports(self):
        intf = Interface(
            "axi_lite",
            imports=[ImportDecl("types_pkg", "*")],
        )
        code = interface_to_dsl(intf)
        assert "import types_pkg::*" in code

    def test_interface_with_variables(self):
        intf = Interface(
            "my_intf",
            variables=[
                Variable(
                    "state_reg",
                    VariableKind.REG,
                    width=Range(Literal(3, original_text="3"), Literal(0, original_text="0")),
                ),
            ],
        )
        code = interface_to_dsl(intf)
        assert "reg" in code
        assert "state_reg" in code


# ===================================================================
# Translator: design_to_dsl with SV constructs
# ===================================================================


class TestDesignToDslSV:
    """Test design_to_dsl with packages, interfaces, and modules."""

    def test_design_with_package(self):
        pkg = Package(
            "my_pkg",
            parameters=[Parameter("W", default_value=Literal(8, original_text="8"), is_local=True)],
        )
        m = Module("top")
        m.input("clk")
        mod = m.build()
        design = Design(modules=[mod], packages=[pkg])
        code = design_to_dsl(design)
        assert "Package: my_pkg" in code
        assert "Module: top" in code

    def test_design_with_interface(self):
        intf = Interface(
            "axi_lite",
            nets=[Net("awaddr", NetKind.WIRE)],
            modports=[
                Modport("master", [ModportPort("awaddr", PortDirection.OUTPUT)]),
            ],
        )
        m = Module("top")
        m.input("clk")
        mod = m.build()
        design = Design(modules=[mod], interfaces=[intf])
        code = design_to_dsl(design)
        assert "Interface: axi_lite" in code
        assert "Module: top" in code

    def test_design_ordering(self):
        """Packages appear before interfaces, interfaces before modules."""
        pkg = Package("pkg1")
        intf = Interface("intf1")
        m = Module("mod1")
        mod = m.build()
        design = Design(modules=[mod], packages=[pkg], interfaces=[intf])
        code = design_to_dsl(design)
        pkg_pos = code.index("Package: pkg1")
        intf_pos = code.index("Interface: intf1")
        mod_pos = code.index("Module: mod1")
        assert pkg_pos < intf_pos < mod_pos

    def test_design_with_all_sv_constructs(self):
        """Full design: package, interface, module with SV constructs."""
        pkg = Package(
            "my_pkg",
            parameters=[Parameter("WIDTH", default_value=Literal(8, original_text="8"), is_local=True)],
            typedefs=[
                TypedefDecl(
                    "state_t",
                    enum_type=EnumType(
                        [
                            EnumMember("IDLE", value=Literal(0, original_text="0")),
                            EnumMember("ACTIVE", value=Literal(1, original_text="1")),
                        ]
                    ),
                ),
            ],
        )
        intf = Interface(
            "axi_lite",
            parameters=[Parameter("ADDR_W", default_value=Literal(32, original_text="32"))],
            nets=[Net("awaddr", NetKind.WIRE)],
            modports=[
                Modport("master", [ModportPort("awaddr", PortDirection.OUTPUT)]),
                Modport("slave", [ModportPort("awaddr", PortDirection.INPUT)]),
            ],
        )
        m = Module("top")
        m.import_pkg("my_pkg")
        m.typedef_enum("cmd_t", [("READ", 0), ("WRITE", 1)])
        m.input("clk")
        out = m.output_reg("out")
        with m.always(posedge(m._signals["clk"])):
            out <<= 0
        mod = m.build()
        design = Design(modules=[mod], packages=[pkg], interfaces=[intf])
        code = design_to_dsl(design)
        # Verify all sections present
        assert "Package: my_pkg" in code
        assert "Interface: axi_lite" in code
        assert "Module: top" in code
        assert "import_pkg" in code
        assert "typedef_enum" in code

    def test_module_with_multiple_typedefs_roundtrip(self):
        """Multiple typedefs of different kinds in one module."""
        m = Module("top")
        m.typedef_enum("state_t", ["IDLE", "RUN"])
        m.typedef_struct("pkt_t", [("header", "logic", 8), ("payload", "logic", 32)], packed=True)
        m.typedef_union("data_t", [("word", "logic", 32), ("half", "logic", 16)])
        m.typedef_alias("addr_t", "logic [15:0]")
        mod = m.build()
        code = module_to_dsl(mod)
        mod2 = _exec_dsl(code)
        assert len(mod2.typedefs) == 4
        assert mod2.typedefs[0].enum_type is not None
        assert mod2.typedefs[1].struct_type is not None
        assert mod2.typedefs[2].union_type is not None
        assert mod2.typedefs[3].type_ref is not None
