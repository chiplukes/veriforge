"""Tests for specify block support (opaque model + round-trip emission).

Tests cover:
  - Specify block extraction from parse tree
  - Source text capture via tree position metadata
  - Emitter output with source_text (faithful round-trip)
  - Emitter fallback without source_text
  - Multiple specify blocks in one module
  - Various specify item types: path delays, specparams, timing checks
  - to_dict() serialization
  - Round-trip: parse → model → emit → re-parse
"""

# ruff: noqa: PLR2004

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.model.design import Design, Module
from veriforge.model.specify import SpecifyBlock
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str, *, with_source_text: bool = True) -> Module:
    """Helper: parse source and return the first Module."""
    tree = parser.build_tree(source)
    kw = {"source_text": source} if with_source_text else {}
    design = tree_to_design(tree, source_file="test.v", **kw)
    assert isinstance(design, Design)
    assert len(design.modules) == 1
    return design.modules[0]


# ---- Basic extraction ----


class TestSpecifyExtraction:
    """Verify specify blocks are extracted from the parse tree."""

    def test_single_specify_block(self, parser):
        """A module with one specify block."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1
        sb = m.specify_blocks[0]
        assert isinstance(sb, SpecifyBlock)
        assert sb.raw_tree is not None
        assert sb.parent is m

    def test_specify_with_specparam(self, parser):
        """Specify block with specparam declarations."""
        source = """\
module m;
specify
  specparam tp = 10;
  specparam th = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1

    def test_specify_with_path_and_specparam(self, parser):
        """Mixed path delays and specparams."""
        source = """\
module m;
specify
  (a => b) = 5;
  specparam tp = 10;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1
        assert m.specify_blocks[0].source_text is not None
        assert "specparam" in m.specify_blocks[0].source_text
        assert "(a => b)" in m.specify_blocks[0].source_text

    def test_multiple_specify_blocks(self, parser):
        """Module with two specify blocks."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
specify
  specparam tp = 10;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 2
        assert all(isinstance(sb, SpecifyBlock) for sb in m.specify_blocks)

    def test_specify_with_other_items(self, parser):
        """Specify block alongside other module items."""
        source = """\
module m(input a, output b);
wire w;
specify
  (a => b) = 5;
endspecify
assign b = a;
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1
        assert len(m.nets) >= 1
        assert len(m.continuous_assigns) == 1

    def test_empty_specify_block(self, parser):
        """Specify block with no items."""
        source = """\
module m;
specify
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1

    def test_parallel_path(self, parser):
        """Parallel path connection (=>)."""
        source = """\
module m;
specify
  (a => b) = (2, 3);
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1

    def test_full_path(self, parser):
        """Full path connection (*>)."""
        source = """\
module m;
specify
  (a *> b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.specify_blocks) == 1


# ---- Source text capture ----


class TestSourceTextCapture:
    """Verify source_text is captured/absent as expected."""

    def test_source_text_present(self, parser):
        """When source_text is provided, it's captured on SpecifyBlock."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source, with_source_text=True)
        sb = m.specify_blocks[0]
        assert sb.source_text is not None
        assert sb.source_text.startswith("specify")
        assert sb.source_text.endswith("endspecify")

    def test_source_text_absent(self, parser):
        """When source_text is not provided, it's None."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source, with_source_text=False)
        sb = m.specify_blocks[0]
        assert sb.source_text is None


# ---- Emission ----


class TestSpecifyEmission:
    """Verify emitter output for specify blocks."""

    def test_emit_with_source_text(self, parser):
        """Faithful round-trip when source_text is available."""
        source = """\
module m;
specify
  (a => b) = 5;
  specparam tp = 10;
endspecify
endmodule"""
        m = _parse_module(parser, source, with_source_text=True)
        out = emit_module(m)
        assert "specify" in out
        assert "endspecify" in out
        assert "(a => b) = 5;" in out
        assert "specparam tp = 10;" in out

    def test_emit_without_source_text(self, parser):
        """Fallback emission when source_text is absent."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source, with_source_text=False)
        out = emit_module(m)
        # Fallback produces something between specify/endspecify
        assert "specify" in out
        assert "endspecify" in out

    def test_emit_preserves_indentation(self, parser):
        """Emitted specify block is indented inside the module."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source, with_source_text=True)
        out = emit_module(m)
        lines = out.splitlines()
        specify_lines = [line for line in lines if "specify" in line.lower() or "(a =>" in line or "specparam" in line]
        for line in specify_lines:
            # All specify-related lines should be indented (inside module body)
            assert line.startswith("    ") or line.startswith("  ")

    def test_emit_multiple_specify(self, parser):
        """Both specify blocks appear in emitted output."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
specify
  specparam tp = 10;
endspecify
endmodule"""
        m = _parse_module(parser, source, with_source_text=True)
        out = emit_module(m)
        assert out.count("specify") >= 4  # 2x specify + 2x endspecify


# ---- Round-trip ----


class TestSpecifyRoundTrip:
    """Parse → model → emit → re-parse → compare."""

    def _roundtrip(self, parser, source: str):
        """Parse, emit, re-parse, return both modules."""
        tree1 = parser.build_tree(source)
        design1 = tree_to_design(tree1, source_text=source)
        emitted = emit_module(design1.modules[0])
        tree2 = parser.build_tree(emitted)
        design2 = tree_to_design(tree2, source_text=emitted)
        return design1.modules[0], design2.modules[0], emitted

    def test_roundtrip_path_delay(self, parser):
        """Path delay survives round-trip."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m1, m2, _ = self._roundtrip(parser, source)
        assert len(m1.specify_blocks) == len(m2.specify_blocks) == 1
        # Both should have source_text
        assert m2.specify_blocks[0].source_text is not None
        assert "(a => b) = 5;" in m2.specify_blocks[0].source_text

    def test_roundtrip_specparam(self, parser):
        """Specparam survives round-trip."""
        source = """\
module m;
specify
  specparam tp = 10;
endspecify
endmodule"""
        m1, m2, _ = self._roundtrip(parser, source)
        assert len(m1.specify_blocks) == len(m2.specify_blocks) == 1
        assert "specparam tp = 10;" in m2.specify_blocks[0].source_text

    def test_roundtrip_complex(self, parser):
        """Complex specify block with multiple items."""
        source = """\
module m;
specify
  (a => b) = 5;
  (c => d) = (2, 3);
  specparam tp = 10;
endspecify
endmodule"""
        m1, m2, emitted = self._roundtrip(parser, source)
        assert len(m1.specify_blocks) == len(m2.specify_blocks) == 1
        assert "(a => b) = 5;" in emitted
        assert "specparam tp = 10;" in emitted


# ---- Serialization ----


class TestSpecifySerialization:
    """to_dict() tests."""

    def test_specify_block_to_dict(self, parser):
        """SpecifyBlock.to_dict() includes type."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        d = m.specify_blocks[0].to_dict()
        assert d["type"] == "SpecifyBlock"

    def test_module_to_dict_includes_specify(self, parser):
        """Module.to_dict() includes specify_blocks when present."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        d = m.to_dict()
        assert "specify_blocks" in d
        assert len(d["specify_blocks"]) == 1
        assert d["specify_blocks"][0]["type"] == "SpecifyBlock"

    def test_module_to_dict_no_specify(self, parser):
        """Module.to_dict() omits specify_blocks when empty."""
        source = "module m; endmodule"
        m = _parse_module(parser, source)
        d = m.to_dict()
        assert "specify_blocks" not in d

    def test_repr(self, parser):
        """SpecifyBlock repr."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert repr(m.specify_blocks[0]) == "SpecifyBlock()"

    def test_child_nodes_empty(self, parser):
        """SpecifyBlock has no child nodes (opaque)."""
        source = """\
module m;
specify
  (a => b) = 5;
endspecify
endmodule"""
        m = _parse_module(parser, source)
        assert m.specify_blocks[0]._child_nodes() == []
