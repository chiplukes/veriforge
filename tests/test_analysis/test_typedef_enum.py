"""Tests for typedef and enum support (grammar, model extraction, emitter round-trip)."""

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.model.sv_types import EnumMember, EnumType, TypedefDecl
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_module(src: str):
    """Parse Verilog source and return the first module."""
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# Grammar parse tests — ensure parser accepts typedef/enum syntax
# ---------------------------------------------------------------------------


class TestGrammarParse:
    """Verify that the grammar accepts various typedef/enum forms."""

    def test_simple_enum_typedef(self):
        src = """\
module top;
    typedef enum {IDLE, RUN, DONE} state_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_enum_with_values(self):
        src = """\
module top;
    typedef enum {A = 0, B = 1, C = 2} abc_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_enum_logic_base_type(self):
        src = """\
module top;
    typedef enum logic [1:0] {S0, S1, S2, S3} state_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_enum_bit_base_type(self):
        src = """\
module top;
    typedef enum bit [2:0] {A, B, C} my_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_enum_int_base_type(self):
        src = """\
module top;
    typedef enum int {RED = 0, GREEN = 1, BLUE = 2} color_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_enum_signed_base(self):
        src = """\
module top;
    typedef enum logic signed [3:0] {NEG = -1, ZERO = 0, POS = 1} signed_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_typedef_base_type_alias(self):
        """typedef <base_type> <name>;  — a simple type alias."""
        src = """\
module top;
    typedef logic my_logic_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1

    def test_multiple_typedefs(self):
        src = """\
module top;
    typedef enum {A, B} ab_t;
    typedef enum logic [1:0] {X = 0, Y = 1} xy_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 2


# ---------------------------------------------------------------------------
# Model extraction tests — verify fields are populated correctly
# ---------------------------------------------------------------------------


class TestModelExtraction:
    """Verify that parsed typedefs produce correct model objects."""

    def test_simple_enum_members(self):
        src = """\
module top;
    typedef enum {IDLE, RUN, DONE} state_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.name == "state_t"
        assert td.enum_type is not None
        assert len(td.enum_type.members) == 3
        assert td.enum_type.members[0].name == "IDLE"
        assert td.enum_type.members[1].name == "RUN"
        assert td.enum_type.members[2].name == "DONE"
        assert all(m.value is None for m in td.enum_type.members)

    def test_enum_with_explicit_values(self):
        src = """\
module top;
    typedef enum {A = 0, B = 1, C = 2} abc_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.name == "abc_t"
        assert td.enum_type.members[0].value is not None
        assert td.enum_type.members[1].value is not None
        assert td.enum_type.members[2].value is not None

    def test_enum_base_type_logic_with_range(self):
        src = """\
module top;
    typedef enum logic [1:0] {S0, S1, S2, S3} state_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.base_type == "logic"
        assert td.enum_type.width is not None

    def test_enum_base_type_int(self):
        src = """\
module top;
    typedef enum int {RED, GREEN, BLUE} color_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.base_type == "int"
        assert td.enum_type.width is None

    def test_enum_signed(self):
        src = """\
module top;
    typedef enum logic signed [3:0] {A, B} signed_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.signed is True

    def test_no_enum_unsigned(self):
        src = """\
module top;
    typedef enum logic [3:0] {A, B} unsigned_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.signed is False

    def test_typedef_type_alias(self):
        src = """\
module top;
    typedef logic my_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.name == "my_t"
        assert td.enum_type is None
        assert td.type_ref is not None
        assert "logic" in td.type_ref

    def test_parent_reference(self):
        src = """\
module top;
    typedef enum {A, B} ab_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.parent is mod

    def test_typedef_is_verilog_node(self):
        """TypedefDecl should be a VerilogNode."""
        td = TypedefDecl(name="t", enum_type=EnumType(members=[EnumMember(name="A")]))
        assert hasattr(td, "parent")
        assert hasattr(td, "loc")

    def test_to_dict(self):
        src = """\
module top;
    typedef enum {X, Y} xy_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        d = td.to_dict()
        assert d["name"] == "xy_t"
        assert "enum_type" in d
        assert len(d["enum_type"]["members"]) == 2


# ---------------------------------------------------------------------------
# Emitter round-trip tests — parse → emit → re-parse
# ---------------------------------------------------------------------------


class TestEmitterRoundTrip:
    """Verify typedef declarations survive parse → emit → re-parse."""

    def test_simple_enum_round_trip(self):
        src = """\
module top;
    typedef enum {IDLE, RUN, DONE} state_t;
endmodule
"""
        mod = _parse_module(src)
        emitted = emit_module(mod)
        mod2 = _parse_module(emitted)
        assert len(mod2.typedefs) == 1
        assert mod2.typedefs[0].name == "state_t"
        assert len(mod2.typedefs[0].enum_type.members) == 3

    def test_enum_with_values_round_trip(self):
        src = """\
module top;
    typedef enum {A = 0, B = 1, C = 2} abc_t;
endmodule
"""
        mod = _parse_module(src)
        emitted = emit_module(mod)
        mod2 = _parse_module(emitted)
        td2 = mod2.typedefs[0]
        assert td2.name == "abc_t"
        assert all(m.value is not None for m in td2.enum_type.members)

    def test_enum_logic_range_round_trip(self):
        src = """\
module top;
    typedef enum logic [1:0] {S0, S1, S2, S3} state_t;
endmodule
"""
        mod = _parse_module(src)
        emitted = emit_module(mod)
        mod2 = _parse_module(emitted)
        td2 = mod2.typedefs[0]
        assert td2.enum_type.base_type == "logic"
        assert td2.enum_type.width is not None

    def test_multiple_typedefs_round_trip(self):
        src = """\
module top;
    typedef enum {A, B} ab_t;
    typedef enum logic [1:0] {X = 0, Y = 1} xy_t;
endmodule
"""
        mod = _parse_module(src)
        emitted = emit_module(mod)
        mod2 = _parse_module(emitted)
        assert len(mod2.typedefs) == 2
        names = {td.name for td in mod2.typedefs}
        assert names == {"ab_t", "xy_t"}

    def test_typedef_alongside_other_declarations(self):
        """Typedef mixed with normal Verilog declarations."""
        src = """\
module top(input clk, output reg [7:0] data);
    typedef enum {IDLE, ACTIVE} state_t;
    wire ready;
    reg [3:0] count;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs) == 1
        assert len(mod.nets) >= 1
        assert len(mod.variables) >= 1

        emitted = emit_module(mod)
        mod2 = _parse_module(emitted)
        assert len(mod2.typedefs) == 1
        assert mod2.typedefs[0].name == "state_t"


# ---------------------------------------------------------------------------
# Edge-case & error-resilience tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for typedef/enum parsing."""

    def test_single_member_enum(self):
        src = """\
module top;
    typedef enum {ONLY_ONE} one_t;
endmodule
"""
        mod = _parse_module(src)
        assert len(mod.typedefs[0].enum_type.members) == 1

    def test_enum_member_with_expression_value(self):
        """Enum member with non-trivial constant expression."""
        src = """\
module top;
    typedef enum {A = 2'b00, B = 2'b01, C = 2'b10} bits_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.members[0].value is not None

    def test_no_typedefs(self):
        """Module with no typedefs should have empty list."""
        src = """\
module top;
    wire a;
endmodule
"""
        mod = _parse_module(src)
        assert mod.typedefs == []

    def test_enum_base_type_byte(self):
        src = """\
module top;
    typedef enum byte {X, Y, Z} byte_enum_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.base_type == "byte"

    def test_enum_base_type_shortint(self):
        src = """\
module top;
    typedef enum shortint {P, Q} short_t;
endmodule
"""
        mod = _parse_module(src)
        td = mod.typedefs[0]
        assert td.enum_type.base_type == "shortint"
