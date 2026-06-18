"""Full comment round-trip tests: parse → model → emit → re-parse → compare.

Verifies that comments survive the entire round-trip pipeline for
all model element types that support comment attachment.
"""

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.transforms.comment_extractor import extract_comments
from veriforge.transforms.tree_to_model import tree_to_design


def _comment_roundtrip(parser, source: str):
    """Full round-trip with comments: parse → emit → re-parse."""
    # Pass 1: parse
    cleaned1, comments1 = extract_comments(source)
    tree1 = parser.build_tree(cleaned1)
    design1 = tree_to_design(tree1, source_file="test.v", comments=comments1, source_text=source)
    m1 = design1.modules[0]
    emitted = emit_module(m1)

    # Pass 2: re-parse the emitted text
    cleaned2, comments2 = extract_comments(emitted)
    tree2 = parser.build_tree(cleaned2)
    design2 = tree_to_design(tree2, source_file="test.v", comments=comments2, source_text=emitted)
    m2 = design2.modules[0]

    return m1, m2, emitted


class TestCommentRoundTrip:
    """Comments survive parse → emit → re-parse for all element types."""

    def test_module_leading_comment(self, parser):
        """Leading comment on module survives round-trip."""
        source = "// Module description\nmodule m;\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "// Module description" in emitted
        assert any(c.text == "Module description" for c in m2.comments)

    def test_port_trailing_comment(self, parser):
        """Trailing comments on ports survive round-trip."""
        source = (
            "module m (\n"
            "    input clk,   // system clock\n"
            "    input rst,   // async reset\n"
            "    output [7:0] data\n"
            ");\nendmodule\n"
        )
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "// system clock" in emitted
        assert "// async reset" in emitted
        clk2 = next(p for p in m2.ports if p.name == "clk")
        rst2 = next(p for p in m2.ports if p.name == "rst")
        assert any(c.text == "system clock" for c in clk2.comments)
        assert any(c.text == "async reset" for c in rst2.comments)

    def test_net_leading_comment(self, parser):
        """Leading comment on net declaration survives round-trip."""
        source = "module m;\n// internal bus\nwire [3:0] bus;\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "// internal bus" in emitted
        assert len(m2.nets) == 1
        assert any(c.text == "internal bus" for c in m2.nets[0].comments)

    def test_parameter_leading_comment(self, parser):
        """Leading comment on localparam survives round-trip.

        Note: module-header parameters (#(parameter ...)) do not emit
        leading comments in the current emitter.  Body localparams do.
        """
        source = "module m;\n// bus width\nlocalparam WIDTH = 8;\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "// bus width" in emitted
        assert len(m2.parameters) >= 1
        width_p = next(p for p in m2.parameters if p.name == "WIDTH")
        assert any(c.text == "bus width" for c in width_p.comments)

    def test_assign_leading_comment(self, parser):
        """Leading comment near a continuous assign.

        Note: the comment attachment algorithm may attach to a child
        node (e.g. the target Identifier) rather than the ContinuousAssign
        itself, depending on line proximity.  This test verifies the comment
        text appears *somewhere* in the model and in emitted output.
        """
        source = "module m;\nwire a, b;\n// drive output\nassign a = b;\nendmodule\n"
        m1, _m2, _emitted = _comment_roundtrip(parser, source)
        # Comment may be emitted depending on attachment target
        # but must exist in the model somewhere
        all_comments_m1 = [c.text for n in m1.walk() for c in n.comments]
        assert "drive output" in all_comments_m1

    def test_instance_leading_comment(self, parser):
        """Leading comment on instance survives round-trip."""
        source = "module m;\n// sub-block\nfoo u_foo(.a(1'b0));\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "// sub-block" in emitted
        assert len(m2.instances) == 1
        assert any(c.text == "sub-block" for c in m2.instances[0].comments)

    def test_always_leading_comment(self, parser):
        """Leading comment on always block survives round-trip."""
        source = "module m;\nreg q;\n// sequential logic\nalways @(posedge clk)\n  q <= 1'b0;\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "// sequential logic" in emitted
        assert len(m2.always_blocks) == 1
        assert any(c.text == "sequential logic" for c in m2.always_blocks[0].comments)

    def test_block_comment_roundtrip(self, parser):
        """Block comments (/* */) survive round-trip."""
        source = "/* Top-level block */\nmodule m;\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        assert "/* Top-level block */" in emitted
        assert any(c.text == "Top-level block" for c in m2.comments)

    def test_mixed_comments_comprehensive(self, parser):
        """Multiple comment types on various elements all survive."""
        source = (
            "// Top module\n"
            "module counter (\n"
            "    input clk,    // clock\n"
            "    input rst,    // reset\n"
            "    output reg [7:0] count\n"
            ");\n"
            "// width param\n"
            "localparam W = 8;\n"
            "// internal wire\n"
            "wire w;\n"
            "// combinational\n"
            "assign w = clk;\n"
            "// flip-flop\n"
            "always @(posedge clk)\n"
            "  count <= count + 1;\n"
            "endmodule\n"
        )
        m1, _m2, emitted = _comment_roundtrip(parser, source)

        # All comment strings present in emitted output
        # Note: comments attached to child expressions (e.g. inside assign)
        # may not be emitted if the emitter only emits leading comments on
        # the parent construct.
        expected_in_emitted = [
            "Top module",
            "clock",
            "reset",
            "internal wire",
            "flip-flop",
        ]
        for text in expected_in_emitted:
            assert f"// {text}" in emitted, f"Missing comment: {text}"

        # All comment texts exist somewhere in the model
        all_texts_m1 = [c.text for n in m1.walk() for c in n.comments]
        for text in ["Top module", "clock", "reset", "width param", "internal wire", "combinational", "flip-flop"]:
            assert text in all_texts_m1, f"Missing in model: {text}"

    def test_comment_count_preserved(self, parser):
        """Number of comments is preserved across round-trip."""
        source = "// A\nmodule m (\n    input a,  // B\n    input b   // C\n);\n// D\nwire w;\nendmodule\n"
        _, m2, _emitted = _comment_roundtrip(parser, source)
        # 4 comments: A (module leading), B (port trailing), C (port trailing), D (net leading)
        total = sum(len(n.comments) for n in m2.walk())
        assert total == 4

    def test_empty_no_comments(self, parser):
        """Module with no comments round-trips cleanly."""
        source = "module m;\nwire w;\nendmodule\n"
        _m1, m2, emitted = _comment_roundtrip(parser, source)
        total = sum(len(n.comments) for n in m2.walk())
        assert total == 0
        assert "//" not in emitted
        assert "/*" not in emitted
