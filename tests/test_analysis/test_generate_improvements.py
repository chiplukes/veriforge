"""Tests for SV generate improvements.

Tests cover:
1. Grammar parsing of new genvar iteration forms (++, --, +=, -=, etc.)
2. Grammar parsing of inline genvar declarations (for (genvar i = 0; ...))
3. Grammar parsing of unique/priority case in generate
4. Model extraction verifying new fields (update_op, genvar_local, qualifier)
5. Emitter round-trip for all new forms
6. Edge cases (repr, to_dict, child_nodes)
"""

from __future__ import annotations

from veriforge.verilog_parser import verilog_parser
from veriforge.model.generate import GenerateCase, GenerateFor, GenvarDecl
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.codegen.verilog_emitter import emit_module


def _build_tree(verilog: str):
    """Parse Verilog text and return the Lark tree."""
    vp = verilog_parser(start="source_text")
    return vp.build_tree(verilog)


def _parse(verilog: str):
    """Parse Verilog snippet and return Design."""
    tree = _build_tree(verilog)
    return tree_to_design(tree)


def _parse_only(verilog: str):
    """Just parse, don't build model."""
    _build_tree(verilog)


def _get_gen_for(verilog: str) -> GenerateFor:
    """Parse and extract the first GenerateFor."""
    design = _parse(verilog)
    for g in design.modules[0].generate_blocks:
        if isinstance(g, GenerateFor):
            return g
    msg = "No GenerateFor found"
    raise AssertionError(msg)


def _get_gen_case(verilog: str) -> GenerateCase:
    """Parse and extract the first GenerateCase."""
    design = _parse(verilog)
    for g in design.modules[0].generate_blocks:
        if isinstance(g, GenerateCase):
            return g
    msg = "No GenerateCase found"
    raise AssertionError(msg)


# ===========================================================================
# Grammar parse tests — genvar iteration forms
# ===========================================================================


class TestGenvarIterationParse:
    """Test that new iteration forms parse without errors."""

    def test_classic_assign(self):
        v = "module m; genvar i; for (i=0; i<4; i=i+1) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_post_increment(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_post_decrement(self):
        v = "module m; genvar i; for (i=3; i>=0; i--) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_pre_increment(self):
        v = "module m; genvar i; for (i=0; i<4; ++i) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_pre_decrement(self):
        v = "module m; genvar i; for (i=3; i>=0; --i) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_plus_assign(self):
        v = "module m; genvar i; for (i=0; i<8; i+=2) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_minus_assign(self):
        v = "module m; genvar i; for (i=7; i>=0; i-=1) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_mul_assign(self):
        v = "module m; genvar i; for (i=1; i<16; i*=2) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_div_assign(self):
        v = "module m; genvar i; for (i=8; i>0; i/=2) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_mod_assign(self):
        v = "module m; genvar i; for (i=0; i<7; i%=3) begin : g wire x; end endmodule"
        _parse_only(v)


# ===========================================================================
# Grammar parse tests — inline genvar declaration
# ===========================================================================


class TestInlineGenvarParse:
    """Test that inline genvar declarations parse correctly."""

    def test_inline_genvar(self):
        v = "module m; for (genvar i=0; i<4; i=i+1) begin : g wire x; end endmodule"
        _parse_only(v)

    def test_inline_genvar_with_increment(self):
        v = "module m; for (genvar i=0; i<4; i++) begin : g wire x; end endmodule"
        _parse_only(v)


# ===========================================================================
# Grammar parse tests — unique/priority case generate
# ===========================================================================


class TestCaseGenerateQualifierParse:
    """Test that unique/priority/unique0 case generate constructs parse."""

    def test_unique_case_generate(self):
        v = "module m; unique case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        _parse_only(v)

    def test_priority_case_generate(self):
        v = "module m; priority case (SEL) 0: begin : s0 wire a; end 1: begin : s1 wire b; end endcase endmodule"
        _parse_only(v)

    def test_unique0_case_generate(self):
        v = "module m; unique0 case (TYPE) 0: begin : t0 wire a; end default: begin : td wire b; end endcase endmodule"
        _parse_only(v)

    def test_plain_case_generate_still_works(self):
        v = "module m; case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        _parse_only(v)


# ===========================================================================
# Model extraction tests — genvar iteration
# ===========================================================================


class TestGenvarIterationModel:
    """Test that new iteration forms extract correctly into model objects."""

    def test_classic_assign(self):
        v = "module m; genvar i; for (i=0; i<4; i=i+1) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "="
        assert gen.update is not None
        assert gen.genvar == "i"
        assert gen.genvar_local is False

    def test_post_increment(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "post++"
        assert gen.update is None

    def test_post_decrement(self):
        v = "module m; genvar i; for (i=3; i>=0; i--) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "post--"
        assert gen.update is None

    def test_pre_increment(self):
        v = "module m; genvar i; for (i=0; i<4; ++i) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "pre++"
        assert gen.update is None

    def test_pre_decrement(self):
        v = "module m; genvar i; for (i=3; i>=0; --i) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "pre--"
        assert gen.update is None

    def test_plus_assign(self):
        v = "module m; genvar i; for (i=0; i<8; i+=2) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "+="
        assert gen.update is not None

    def test_minus_assign(self):
        v = "module m; genvar i; for (i=7; i>=0; i-=1) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "-="
        assert gen.update is not None

    def test_mul_assign(self):
        v = "module m; genvar i; for (i=1; i<16; i*=2) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "*="
        assert gen.update is not None

    def test_div_assign(self):
        v = "module m; genvar i; for (i=8; i>0; i/=2) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "/="
        assert gen.update is not None

    def test_mod_assign(self):
        v = "module m; genvar i; for (i=0; i<7; i%=3) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.update_op == "%="
        assert gen.update is not None


# ===========================================================================
# Model extraction tests — inline genvar
# ===========================================================================


class TestInlineGenvarModel:
    """Test inline genvar flag in model."""

    def test_inline_genvar_flag(self):
        v = "module m; for (genvar i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.genvar_local is True
        assert gen.genvar == "i"

    def test_external_genvar_flag(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        assert gen.genvar_local is False

    def test_inline_no_separate_genvar_decl(self):
        """Inline genvar should not produce a separate GenvarDecl."""
        v = "module m; for (genvar i=0; i<4; i++) begin : g wire x; end endmodule"
        design = _parse(v)
        genvars = [g for g in design.modules[0].generate_blocks if isinstance(g, GenvarDecl)]
        assert len(genvars) == 0


# ===========================================================================
# Model extraction tests — case generate qualifier
# ===========================================================================


class TestCaseGenerateQualifierModel:
    """Test qualifier extraction for case generate."""

    def test_unique_qualifier(self):
        v = "module m; unique case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        assert gen.qualifier == "unique"

    def test_priority_qualifier(self):
        v = "module m; priority case (SEL) 0: begin : s0 wire a; end 1: begin : s1 wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        assert gen.qualifier == "priority"

    def test_unique0_qualifier(self):
        v = "module m; unique0 case (TYPE) 0: begin : t0 wire a; end default: begin : td wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        assert gen.qualifier == "unique0"

    def test_no_qualifier(self):
        v = "module m; case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        assert gen.qualifier is None


# ===========================================================================
# Emitter round-trip tests
# ===========================================================================


class TestEmitterRoundTrip:
    """Test that new forms emit correctly and round-trip."""

    def _round_trip(self, verilog: str) -> str:
        """Parse → model → emit → return emitted text."""
        design = _parse(verilog)
        return emit_module(design.modules[0])

    def test_classic_for_roundtrip(self):
        v = "module m; genvar i; for (i = 0; i < 4; i = i + 1) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "i = i + 1" in emitted

    def test_post_increment_roundtrip(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "i++" in emitted

    def test_post_decrement_roundtrip(self):
        v = "module m; genvar i; for (i=3; i>=0; i--) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "i--" in emitted

    def test_pre_increment_roundtrip(self):
        v = "module m; genvar i; for (i=0; i<4; ++i) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "++i" in emitted

    def test_pre_decrement_roundtrip(self):
        v = "module m; genvar i; for (i=3; i>=0; --i) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "--i" in emitted

    def test_plus_assign_roundtrip(self):
        v = "module m; genvar i; for (i=0; i<8; i+=2) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "i += 2" in emitted

    def test_minus_assign_roundtrip(self):
        v = "module m; genvar i; for (i=7; i>=0; i-=1) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "i -= 1" in emitted

    def test_inline_genvar_roundtrip(self):
        v = "module m; for (genvar i=0; i<4; i++) begin : g wire x; end endmodule"
        emitted = self._round_trip(v)
        assert "genvar i = 0" in emitted
        assert "i++" in emitted

    def test_unique_case_roundtrip(self):
        v = "module m; unique case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        emitted = self._round_trip(v)
        assert "unique case" in emitted

    def test_priority_case_roundtrip(self):
        v = "module m; priority case (SEL) 0: begin : s0 wire a; end 1: begin : s1 wire b; end endcase endmodule"
        emitted = self._round_trip(v)
        assert "priority case" in emitted

    def test_unique0_case_roundtrip(self):
        v = "module m; unique0 case (TYPE) 0: begin : t0 wire a; end default: begin : td wire b; end endcase endmodule"
        emitted = self._round_trip(v)
        assert "unique0 case" in emitted


# ===========================================================================
# Full re-parse round-trip tests — parse, emit, re-parse, compare
# ===========================================================================


class TestReParseRoundTrip:
    """Verify emitted Verilog re-parses to equivalent model."""

    def _reparse_check(self, verilog: str):
        """Parse → emit → re-parse → assert same structure."""
        design1 = _parse(verilog)
        emitted = emit_module(design1.modules[0])
        design2 = _parse(emitted)
        return design1, design2

    def test_post_increment_reparse(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        _d1, d2 = self._reparse_check(v)
        gen1 = _get_gen_for(v)
        gen2 = next(g for g in d2.modules[0].generate_blocks if isinstance(g, GenerateFor))
        assert gen1.update_op == gen2.update_op
        assert gen1.genvar == gen2.genvar

    def test_inline_genvar_reparse(self):
        v = "module m; for (genvar i=0; i<4; i++) begin : g wire x; end endmodule"
        _d1, d2 = self._reparse_check(v)
        gen2 = next(g for g in d2.modules[0].generate_blocks if isinstance(g, GenerateFor))
        assert gen2.genvar_local is True
        assert gen2.update_op == "post++"

    def test_plus_assign_reparse(self):
        v = "module m; genvar i; for (i=0; i<8; i+=2) begin : g wire x; end endmodule"
        _d1, d2 = self._reparse_check(v)
        gen2 = next(g for g in d2.modules[0].generate_blocks if isinstance(g, GenerateFor))
        assert gen2.update_op == "+="

    def test_unique_case_reparse(self):
        v = "module m; unique case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        _d1, d2 = self._reparse_check(v)
        gen2 = next(g for g in d2.modules[0].generate_blocks if isinstance(g, GenerateCase))
        assert gen2.qualifier == "unique"

    def test_priority_case_reparse(self):
        v = "module m; priority case (SEL) 0: begin : s0 wire a; end 1: begin : s1 wire b; end endcase endmodule"
        _d1, d2 = self._reparse_check(v)
        gen2 = next(g for g in d2.modules[0].generate_blocks if isinstance(g, GenerateCase))
        assert gen2.qualifier == "priority"


# ===========================================================================
# Edge case tests — repr, to_dict, _child_nodes
# ===========================================================================


class TestEdgeCases:
    """Edge case tests for new model fields."""

    def test_generate_for_repr_with_op(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        r = repr(gen)
        assert "post++" in r
        assert "i" in r

    def test_generate_for_to_dict_update_op(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        d = gen.to_dict()
        assert d["update_op"] == "post++"
        assert "update" not in d  # update is None for ++

    def test_generate_for_to_dict_classic(self):
        v = "module m; genvar i; for (i=0; i<4; i=i+1) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        d = gen.to_dict()
        assert d["update_op"] == "="
        assert "update" in d

    def test_generate_for_to_dict_genvar_local(self):
        v = "module m; for (genvar i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        d = gen.to_dict()
        assert d["genvar_local"] is True

    def test_generate_for_child_nodes_no_update(self):
        v = "module m; genvar i; for (i=0; i<4; i++) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        children = gen._child_nodes()
        # init_value, condition, body (no update)
        assert len(children) == 3

    def test_generate_for_child_nodes_with_update(self):
        v = "module m; genvar i; for (i=0; i<4; i=i+1) begin : g wire x; end endmodule"
        gen = _get_gen_for(v)
        children = gen._child_nodes()
        # init_value, condition, update, body
        assert len(children) == 4

    def test_generate_case_repr_with_qualifier(self):
        v = "module m; unique case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        r = repr(gen)
        assert "unique" in r

    def test_generate_case_repr_no_qualifier(self):
        v = "module m; case (MODE) 0: begin : m0 wire a; end default: begin : md wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        r = repr(gen)
        assert "unique" not in r

    def test_generate_case_to_dict_with_qualifier(self):
        v = "module m; priority case (SEL) 0: begin : s0 wire a; end default: begin : sd wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        d = gen.to_dict()
        assert d["qualifier"] == "priority"

    def test_generate_case_to_dict_no_qualifier(self):
        v = "module m; case (SEL) 0: begin : s0 wire a; end default: begin : sd wire b; end endcase endmodule"
        gen = _get_gen_case(v)
        d = gen.to_dict()
        assert "qualifier" not in d


# ===========================================================================
# Complex combination tests
# ===========================================================================


class TestCombinations:
    """Test combinations of new features."""

    def test_inline_genvar_with_complex_body(self):
        """Inline genvar with instances and assigns in body."""
        v = """\
module m;
  for (genvar i = 0; i < 4; i++) begin : gen_slice
    wire [7:0] data;
    assign data = 8'hFF;
  end
endmodule"""
        gen = _get_gen_for(v)
        assert gen.genvar_local is True
        assert gen.update_op == "post++"
        assert gen.body.name == "gen_slice"
        assert len(gen.body.items) == 2

    def test_nested_generate_with_qualifier(self):
        """Qualified case inside a generate region."""
        v = """\
module m;
  generate
    unique case (MODE)
      0: begin : mode0
        wire a;
      end
      default: begin : mode_def
        wire b;
      end
    endcase
  endgenerate
endmodule"""
        gen = _get_gen_case(v)
        assert gen.qualifier == "unique"
        assert len(gen.items) == 2

    def test_multiple_generate_forms(self):
        """Module with multiple different generate constructs."""
        v = """\
module m;
  genvar j;
  for (genvar i = 0; i < 4; i++) begin : g1
    wire x;
  end
  for (j = 0; j < 8; j += 2) begin : g2
    wire y;
  end
  unique case (SEL)
    0: begin : s0 wire a; end
    default: begin : sd wire b; end
  endcase
endmodule"""
        design = _parse(v)
        gens = design.modules[0].generate_blocks
        # Should have GenvarDecl(j), GenerateFor(i, inline), GenerateFor(j, +=), GenerateCase(unique)
        gen_fors = [g for g in gens if isinstance(g, GenerateFor)]
        gen_cases = [g for g in gens if isinstance(g, GenerateCase)]
        gen_decls = [g for g in gens if isinstance(g, GenvarDecl)]

        assert len(gen_fors) == 2
        assert len(gen_cases) == 1
        assert len(gen_decls) == 1

        # First for: inline genvar i with post++
        assert gen_fors[0].genvar_local is True
        assert gen_fors[0].update_op == "post++"

        # Second for: external genvar j with +=
        assert gen_fors[1].genvar_local is False
        assert gen_fors[1].update_op == "+="

        # Case with qualifier
        assert gen_cases[0].qualifier == "unique"
