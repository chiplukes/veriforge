"""Tests for SystemVerilog struct/union support.

Covers grammar parsing, model extraction, emitter output, round-trip
fidelity, and edge cases for ``typedef struct`` and ``typedef union``
declarations.
"""

from __future__ import annotations

from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.codegen.verilog_emitter import emit_design, emit_module
from veriforge.model import (
    Design,
    Literal,
    Module,
    Range,
    StructField,
    StructType,
    TypedefDecl,
    UnionType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(text: str):
    """Parse Verilog text and return the Lark tree."""
    vp = verilog_parser(start="source_text")
    return vp.build_tree(text)


def _design(text: str) -> Design:
    """Parse and extract a Design model."""
    tree = _parse(text)
    return tree_to_design(tree)


def _module(text: str) -> Module:
    """Parse, extract, and return the first module."""
    design = _design(text)
    assert len(design.modules) >= 1
    return design.modules[0]


# ---------------------------------------------------------------------------
# Grammar parse tests
# ---------------------------------------------------------------------------


class TestStructGrammarParse:
    """Verify Lark grammar accepts struct/union declarations."""

    def test_struct_packed_basic(self):
        tree = _parse("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        assert tree is not None

    def test_struct_unpacked(self):
        tree = _parse("""module top;
typedef struct {
    int count;
    integer total;
} counters_t;
endmodule
""")
        assert tree is not None

    def test_struct_packed_signed(self):
        tree = _parse("""module top;
typedef struct packed signed {
    bit [15:0] value;
    bit flag;
} signed_data_t;
endmodule
""")
        assert tree is not None

    def test_union_packed(self):
        tree = _parse("""module top;
typedef union packed {
    logic [31:0] word;
    logic [7:0] byte_val;
} word_t;
endmodule
""")
        assert tree is not None

    def test_union_unpacked(self):
        tree = _parse("""module top;
typedef union {
    int a;
    integer b;
} u_t;
endmodule
""")
        assert tree is not None

    def test_struct_in_package(self):
        tree = _parse("""package my_pkg;
typedef struct packed {
    logic [7:0] addr;
    logic [7:0] data;
} pkt_t;
endpackage
""")
        assert tree is not None

    def test_union_in_package(self):
        tree = _parse("""package my_pkg;
typedef union packed {
    logic [15:0] half;
    logic [7:0] byte_val;
} overlay_t;
endpackage
""")
        assert tree is not None

    def test_struct_single_field(self):
        tree = _parse("""module top;
typedef struct packed {
    logic valid;
} single_t;
endmodule
""")
        assert tree is not None

    def test_struct_many_fields(self):
        tree = _parse("""module top;
typedef struct packed {
    logic [7:0] a;
    logic [7:0] b;
    logic [7:0] c;
    logic [7:0] d;
    logic [7:0] e;
} wide_t;
endmodule
""")
        assert tree is not None

    def test_struct_with_bit_type(self):
        tree = _parse("""module top;
typedef struct packed {
    bit [3:0] nibble;
    bit flag;
} bit_struct_t;
endmodule
""")
        assert tree is not None

    def test_struct_with_reg_type(self):
        tree = _parse("""module top;
typedef struct packed {
    reg [7:0] data;
} reg_struct_t;
endmodule
""")
        assert tree is not None

    def test_struct_with_integer_atom_types(self):
        tree = _parse("""module top;
typedef struct {
    int a;
    integer b;
    shortint c;
    longint d;
    byte e;
} atom_struct_t;
endmodule
""")
        assert tree is not None

    def test_struct_and_enum_together(self):
        tree = _parse("""module top;
typedef enum logic [1:0] { IDLE, RUN, DONE } state_t;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        assert tree is not None

    def test_struct_with_signed_field(self):
        tree = _parse("""module top;
typedef struct packed {
    logic signed [15:0] value;
    logic flag;
} signed_field_t;
endmodule
""")
        assert tree is not None


# ---------------------------------------------------------------------------
# Model extraction tests
# ---------------------------------------------------------------------------


class TestStructModelExtraction:
    """Verify tree_to_design extracts StructType/UnionType correctly."""

    def test_struct_typedef_name(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        assert len(mod.typedefs) == 1
        td = mod.typedefs[0]
        assert td.name == "bus_t"
        assert isinstance(td, TypedefDecl)

    def test_struct_type_present(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        td = mod.typedefs[0]
        assert td.struct_type is not None
        assert isinstance(td.struct_type, StructType)

    def test_struct_packed_flag(self):
        mod = _module("""module top;
typedef struct packed {
    logic valid;
} bus_t;
endmodule
""")
        assert mod.typedefs[0].struct_type.packed is True

    def test_struct_unpacked_flag(self):
        mod = _module("""module top;
typedef struct {
    int count;
} cnt_t;
endmodule
""")
        assert mod.typedefs[0].struct_type.packed is False

    def test_struct_signed_flag(self):
        mod = _module("""module top;
typedef struct packed signed {
    bit [15:0] value;
} signed_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert st.packed is True
        assert st.signed is True

    def test_struct_fields_count(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
    logic ready;
} bus_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert len(st.fields) == 3

    def test_struct_field_names(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert st.fields[0].name == "data"
        assert st.fields[1].name == "valid"

    def test_struct_field_data_type(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    bit flag;
} bus_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert st.fields[0].data_type == "logic"
        assert st.fields[1].data_type == "bit"

    def test_struct_field_width(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert st.fields[0].width is not None
        assert st.fields[1].width is None

    def test_struct_field_signed(self):
        mod = _module("""module top;
typedef struct packed {
    logic signed [15:0] value;
    logic flag;
} bus_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert st.fields[0].signed is True
        assert st.fields[1].signed is False

    def test_union_type_present(self):
        mod = _module("""module top;
typedef union packed {
    logic [31:0] word;
    logic [7:0] byte_val;
} word_t;
endmodule
""")
        td = mod.typedefs[0]
        assert td.union_type is not None
        assert isinstance(td.union_type, UnionType)
        assert td.struct_type is None

    def test_union_packed_flag(self):
        mod = _module("""module top;
typedef union packed {
    logic [15:0] half;
} u_t;
endmodule
""")
        assert mod.typedefs[0].union_type.packed is True

    def test_union_fields(self):
        mod = _module("""module top;
typedef union packed {
    logic [31:0] word;
    logic [15:0] half;
    logic [7:0] byte_val;
} overlay_t;
endmodule
""")
        ut = mod.typedefs[0].union_type
        assert len(ut.fields) == 3
        assert ut.fields[0].name == "word"
        assert ut.fields[1].name == "half"
        assert ut.fields[2].name == "byte_val"

    def test_struct_in_package_model(self):
        design = _design("""package my_pkg;
typedef struct packed {
    logic [7:0] addr;
    logic [7:0] data;
} pkt_t;
endpackage
""")
        assert len(design.packages) == 1
        pkg = design.packages[0]
        assert len(pkg.typedefs) == 1
        td = pkg.typedefs[0]
        assert td.name == "pkt_t"
        assert td.struct_type is not None

    def test_parent_references(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        td = mod.typedefs[0]
        assert td.parent is mod
        st = td.struct_type
        for field in st.fields:
            assert field.parent is st

    def test_integer_atom_fields(self):
        mod = _module("""module top;
typedef struct {
    int a;
    integer b;
    byte c;
} atom_t;
endmodule
""")
        st = mod.typedefs[0].struct_type
        assert st.fields[0].data_type == "int"
        assert st.fields[1].data_type == "integer"
        assert st.fields[2].data_type == "byte"


# ---------------------------------------------------------------------------
# Emitter tests
# ---------------------------------------------------------------------------


class TestStructEmitter:
    """Verify emitter produces correct Verilog for struct/union typedefs."""

    def test_emit_struct_packed(self):
        mod = _module("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        output = emit_module(mod)
        assert "typedef struct packed {" in output
        assert "logic [7:0] data;" in output
        assert "logic valid;" in output
        assert "} bus_t;" in output

    def test_emit_struct_unpacked(self):
        mod = _module("""module top;
typedef struct {
    int count;
} cnt_t;
endmodule
""")
        output = emit_module(mod)
        assert "typedef struct {" in output
        assert "int count;" in output
        assert "} cnt_t;" in output

    def test_emit_struct_packed_signed(self):
        mod = _module("""module top;
typedef struct packed signed {
    bit [15:0] value;
} signed_t;
endmodule
""")
        output = emit_module(mod)
        assert "typedef struct packed signed {" in output
        assert "} signed_t;" in output

    def test_emit_union_packed(self):
        mod = _module("""module top;
typedef union packed {
    logic [31:0] word;
    logic [7:0] byte_val;
} overlay_t;
endmodule
""")
        output = emit_module(mod)
        assert "typedef union packed {" in output
        assert "logic [31:0] word;" in output
        assert "} overlay_t;" in output

    def test_emit_design_with_struct_package(self):
        design = _design("""package my_pkg;
typedef struct packed {
    logic [7:0] data;
} pkt_t;
endpackage
""")
        output = emit_design(design)
        assert "typedef struct packed {" in output
        assert "} pkt_t;" in output

    def test_emit_signed_field(self):
        mod = _module("""module top;
typedef struct packed {
    logic signed [15:0] value;
    logic flag;
} bus_t;
endmodule
""")
        output = emit_module(mod)
        assert "logic signed [15:0] value;" in output


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


class TestStructRoundTrip:
    """Verify parse → model → emit → re-parse round-trip fidelity."""

    def _round_trip(self, text: str):
        """Parse, emit, re-parse, and verify same model."""
        design1 = _design(text)
        emitted = emit_design(design1)
        design2 = _design(emitted)
        return design1, design2, emitted

    def test_rt_struct_packed(self):
        d1, d2, _ = self._round_trip("""module top;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        td1 = d1.modules[0].typedefs[0]
        td2 = d2.modules[0].typedefs[0]
        assert td1.name == td2.name
        st1 = td1.struct_type
        st2 = td2.struct_type
        assert st1.packed == st2.packed
        assert st1.signed == st2.signed
        assert len(st1.fields) == len(st2.fields)
        for f1, f2 in zip(st1.fields, st2.fields):
            assert f1.name == f2.name
            assert f1.data_type == f2.data_type

    def test_rt_struct_packed_signed(self):
        d1, d2, _ = self._round_trip("""module top;
typedef struct packed signed {
    bit [15:0] value;
    bit flag;
} signed_bus_t;
endmodule
""")
        st1 = d1.modules[0].typedefs[0].struct_type
        st2 = d2.modules[0].typedefs[0].struct_type
        assert st1.packed == st2.packed is True
        assert st1.signed == st2.signed is True

    def test_rt_union_packed(self):
        d1, d2, _ = self._round_trip("""module top;
typedef union packed {
    logic [31:0] word;
    logic [7:0] byte_val;
} overlay_t;
endmodule
""")
        ut1 = d1.modules[0].typedefs[0].union_type
        ut2 = d2.modules[0].typedefs[0].union_type
        assert ut1.packed == ut2.packed
        assert len(ut1.fields) == len(ut2.fields)

    def test_rt_struct_in_package(self):
        d1, d2, _ = self._round_trip("""package my_pkg;
typedef struct packed {
    logic [7:0] addr;
    logic [7:0] data;
} pkt_t;
endpackage
""")
        td1 = d1.packages[0].typedefs[0]
        td2 = d2.packages[0].typedefs[0]
        assert td1.name == td2.name
        assert len(td1.struct_type.fields) == len(td2.struct_type.fields)

    def test_rt_struct_and_enum_together(self):
        d1, d2, _ = self._round_trip("""module top;
typedef enum logic [1:0] { IDLE, RUN, DONE } state_t;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        assert len(d1.modules[0].typedefs) == 2
        assert len(d2.modules[0].typedefs) == 2
        # Check enum is still there
        enum_td = [t for t in d2.modules[0].typedefs if t.enum_type is not None]
        struct_td = [t for t in d2.modules[0].typedefs if t.struct_type is not None]
        assert len(enum_td) == 1
        assert len(struct_td) == 1

    def test_rt_struct_unpacked(self):
        d1, d2, _ = self._round_trip("""module top;
typedef struct {
    int count;
    integer total;
} counters_t;
endmodule
""")
        st1 = d1.modules[0].typedefs[0].struct_type
        st2 = d2.modules[0].typedefs[0].struct_type
        assert st1.packed == st2.packed is False
        assert len(st1.fields) == len(st2.fields)


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestStructEdgeCases:
    """Edge cases and repr / to_dict coverage."""

    def test_struct_field_repr(self):
        f = StructField("data", "logic")
        assert "StructField" in repr(f)
        assert "data" in repr(f)

    def test_struct_field_repr_with_width(self):
        w = Range(Literal("7"), Literal("0"))
        f = StructField("data", "logic", width=w)
        r = repr(f)
        assert "width=" in r

    def test_struct_field_repr_signed(self):
        f = StructField("value", "logic", signed=True)
        assert "signed=True" in repr(f)

    def test_struct_type_repr(self):
        f = StructField("data", "logic")
        st = StructType([f], packed=True)
        r = repr(st)
        assert "StructType" in r
        assert "packed" in r

    def test_union_type_repr(self):
        f = StructField("word", "logic")
        ut = UnionType([f], packed=True, signed=True)
        r = repr(ut)
        assert "UnionType" in r
        assert "packed" in r
        assert "signed" in r

    def test_struct_type_to_dict(self):
        f = StructField("data", "logic")
        st = StructType([f], packed=True, signed=True)
        d = st.to_dict()
        assert d["packed"] is True
        assert d["signed"] is True
        assert len(d["fields"]) == 1
        assert d["fields"][0]["name"] == "data"

    def test_union_type_to_dict(self):
        f = StructField("word", "logic")
        ut = UnionType([f], packed=True)
        d = ut.to_dict()
        assert d["packed"] is True
        assert len(d["fields"]) == 1

    def test_typedef_decl_struct_repr(self):
        f = StructField("data", "logic")
        st = StructType([f])
        td = TypedefDecl("bus_t", struct_type=st)
        assert "struct=" in repr(td)

    def test_typedef_decl_union_repr(self):
        f = StructField("word", "logic")
        ut = UnionType([f])
        td = TypedefDecl("word_t", union_type=ut)
        assert "union=" in repr(td)

    def test_typedef_decl_to_dict_struct(self):
        f = StructField("data", "logic")
        st = StructType([f], packed=True)
        td = TypedefDecl("bus_t", struct_type=st)
        d = td.to_dict()
        assert d["name"] == "bus_t"
        assert "struct_type" in d
        assert "enum_type" not in d

    def test_typedef_decl_to_dict_union(self):
        f = StructField("word", "logic")
        ut = UnionType([f], packed=True)
        td = TypedefDecl("word_t", union_type=ut)
        d = td.to_dict()
        assert d["name"] == "word_t"
        assert "union_type" in d

    def test_typedef_child_nodes_struct(self):
        f = StructField("data", "logic")
        st = StructType([f])
        td = TypedefDecl("bus_t", struct_type=st)
        children = td._child_nodes()
        assert len(children) == 1
        assert children[0] is st

    def test_typedef_child_nodes_union(self):
        f = StructField("word", "logic")
        ut = UnionType([f])
        td = TypedefDecl("word_t", union_type=ut)
        children = td._child_nodes()
        assert len(children) == 1
        assert children[0] is ut

    def test_struct_type_child_nodes(self):
        f1 = StructField("a", "logic")
        f2 = StructField("b", "bit")
        st = StructType([f1, f2])
        children = st._child_nodes()
        assert len(children) == 2

    def test_union_type_child_nodes(self):
        f = StructField("word", "logic")
        ut = UnionType([f])
        assert len(ut._child_nodes()) == 1

    def test_struct_field_child_nodes_no_width(self):
        f = StructField("data", "logic")
        assert f._child_nodes() == []

    def test_struct_field_child_nodes_with_width(self):
        w = Range(Literal("7"), Literal("0"))
        f = StructField("data", "logic", width=w)
        children = f._child_nodes()
        assert len(children) == 1
        assert children[0] is w

    def test_struct_field_to_dict_full(self):
        w = Range(Literal("7"), Literal("0"))
        f = StructField("data", "logic", width=w, signed=True)
        d = f.to_dict()
        assert d["name"] == "data"
        assert d["data_type"] == "logic"
        assert d["signed"] is True
        assert "width" in d

    def test_struct_no_packed_no_signed_repr(self):
        f = StructField("x", "int")
        st = StructType([f])
        r = repr(st)
        assert "StructType" in r

    def test_union_no_packed_no_signed_repr(self):
        f = StructField("x", "int")
        ut = UnionType([f])
        r = repr(ut)
        assert "UnionType" in r

    def test_struct_unpacked_to_dict(self):
        f = StructField("x", "int")
        st = StructType([f])
        d = st.to_dict()
        assert "packed" not in d
        assert "signed" not in d

    def test_module_with_struct_and_other_items(self):
        """Struct typedef coexists with nets, variables, etc."""
        mod = _module("""module top;
wire clk;
reg [7:0] data_reg;
typedef struct packed {
    logic [7:0] data;
    logic valid;
} bus_t;
endmodule
""")
        assert len(mod.nets) >= 1
        assert len(mod.variables) >= 1
        assert len(mod.typedefs) == 1
