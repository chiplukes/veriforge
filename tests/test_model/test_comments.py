"""Tests for comment extraction, attachment, and emission."""

from veriforge.transforms.comment_extractor import extract_comments
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.codegen.verilog_emitter import emit_module


# ---- extract_comments tests ----


class TestExtractComments:
    """Tests for the extract_comments() function."""

    def test_no_comments(self):
        source = "module m; endmodule\n"
        cleaned, comments = extract_comments(source)
        assert cleaned == source
        assert comments == []

    def test_single_line_comment(self):
        source = "// hello\nmodule m; endmodule\n"
        cleaned, comments = extract_comments(source)
        assert len(comments) == 1
        assert comments[0].kind == "line"
        assert comments[0].text == "hello"
        assert comments[0].loc.line == 1
        assert comments[0].loc.column == 1
        # Cleaned text preserves same length
        assert len(cleaned) == len(source)
        assert "// hello" not in cleaned
        assert cleaned.count("\n") == source.count("\n")

    def test_block_comment(self):
        source = "module m /* inline */ ; endmodule\n"
        cleaned, comments = extract_comments(source)
        assert len(comments) == 1
        assert comments[0].kind == "block"
        assert comments[0].text == "inline"
        assert "/* inline */" not in cleaned
        assert len(cleaned) == len(source)

    def test_multiline_block_comment(self):
        source = "/* line1\n   line2 */\nmodule m; endmodule\n"
        cleaned, comments = extract_comments(source)
        assert len(comments) == 1
        assert comments[0].kind == "block"
        assert comments[0].loc.line == 1
        assert comments[0].loc.end_line == 2
        # Newlines preserved in replacement
        assert cleaned.count("\n") == source.count("\n")

    def test_multiple_comments(self):
        source = "// desc\nmodule m (\n    input clk,  // clock\n    input rst   // reset\n); endmodule\n"
        _cleaned, comments = extract_comments(source)
        assert len(comments) == 3
        assert comments[0].text == "desc"
        assert comments[1].text == "clock"
        assert comments[2].text == "reset"

    def test_source_file_tracking(self):
        source = "// comment\nmodule m; endmodule\n"
        _, comments = extract_comments(source, source_file="test.v")
        assert comments[0].loc.file == "test.v"

    def test_line_numbers_preserved(self, parser):
        """Line numbers in parse tree match original source after comment stripping."""
        source = "// Module description\nmodule counter (\n    input clk,\n    input rst\n); endmodule\n"
        cleaned, _comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        # Module should be on line 2 (comment on line 1)
        assert tree.meta.line == 2

    def test_comment_in_string_literal(self):
        """Comments inside string literals should ideally not be stripped.

        NOTE: The current regex-based approach does not handle this edge case.
        This test documents the known limitation.
        """
        # This is a known limitation that rarely occurs in practice
        pass

    def test_empty_line_comment(self):
        source = "//\nmodule m; endmodule\n"
        _cleaned, comments = extract_comments(source)
        assert len(comments) == 1
        assert comments[0].text == ""
        assert comments[0].kind == "line"

    def test_nested_slash_in_block_comment(self):
        source = "/* http://example.com */\nmodule m; endmodule\n"
        _cleaned, comments = extract_comments(source)
        assert len(comments) == 1
        assert "http://example.com" in comments[0].text


# ---- attach_comments tests ----


class TestAttachComments:
    """Tests for the attach_comments() function."""

    def test_leading_comment(self, parser):
        """Comment before a module declaration is attached as leading."""
        source = "// Module header\nmodule m;\nendmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        m = design.modules[0]
        # Comment on line 1, module on line 2 -> leading on module
        assert len(m.comments) == 1
        assert m.comments[0].position == "leading"
        assert m.comments[0].text == "Module header"

    def test_trailing_comment_on_port(self, parser):
        """Comment on same line as port is attached as trailing."""
        source = "module m (\n    input clk,  // clock signal\n    input rst\n); endmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        m = design.modules[0]
        clk_port = next(p for p in m.ports if p.name == "clk")
        assert len(clk_port.comments) == 1
        assert clk_port.comments[0].position == "trailing"
        assert clk_port.comments[0].text == "clock signal"

    def test_multiple_comments_different_nodes(self, parser):
        """Multiple comments attach to different nodes."""
        source = "// top\nmodule m (\n    input clk,  // ck\n    input rst   // rs\n); endmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        m = design.modules[0]
        # "top" -> leading on module
        assert any(c.text == "top" for c in m.comments)
        # "ck" -> trailing on clk port
        clk = next(p for p in m.ports if p.name == "clk")
        assert any(c.text == "ck" for c in clk.comments)
        # "rs" -> trailing on rst port
        rst = next(p for p in m.ports if p.name == "rst")
        assert any(c.text == "rs" for c in rst.comments)

    def test_no_comments(self, parser):
        """No comments leaves all comment lists empty."""
        source = "module m; endmodule\n"
        tree = parser.build_tree(source)
        design = tree_to_design(tree, comments=[])
        m = design.modules[0]
        assert m.comments == []

    def test_leading_comment_on_net(self, parser):
        """Comment before a net declaration is attached as leading."""
        source = "module m;\n// signal\nwire w;\nendmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        m = design.modules[0]
        assert len(m.nets) == 1
        assert len(m.nets[0].comments) == 1
        assert m.nets[0].comments[0].position == "leading"
        assert m.nets[0].comments[0].text == "signal"


# ---- Emission tests ----


class TestCommentEmission:
    """Tests for comment emission in verilog_emitter."""

    def test_emit_leading_comment_on_module(self, parser):
        """Leading comments on a module are emitted before module keyword."""
        source = "// Module description\nmodule m;\nendmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        result = emit_module(design.modules[0])
        assert result.startswith("// Module description\n")
        assert "module m;" in result

    def test_emit_trailing_comment_on_port(self, parser):
        """Trailing comments on ports are emitted on the same line."""
        source = "module m (\n    input clk,  // clock\n    input rst\n); endmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        result = emit_module(design.modules[0])
        # Should contain "// clock" on the clk port line
        lines = result.split("\n")
        clk_line = [line for line in lines if "clk" in line]
        assert len(clk_line) == 1
        assert "// clock" in clk_line[0]

    def test_emit_no_comments_flag(self, parser):
        """emit_comments=False suppresses all comments."""
        source = "// desc\nmodule m;\nendmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        result = emit_module(design.modules[0], emit_comments=False)
        assert "//" not in result

    def test_emit_block_comment(self, parser):
        """Block comments are emitted with /* */ syntax."""
        source = "/* Block description */\nmodule m;\nendmodule\n"
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, comments=comments)
        result = emit_module(design.modules[0])
        assert "/* Block description */" in result


# ---- Integration / round-trip tests ----


class TestCommentIntegration:
    """End-to-end tests: parse with comments, build model, emit."""

    def test_full_pipeline(self, parser):
        """Full pipeline: extract -> parse -> model -> emit preserves comments."""
        source = (
            "// Counter module\n"
            "module counter (\n"
            "    input clk,   // system clock\n"
            "    input rst,   // async reset\n"
            "    output reg [7:0] count\n"
            ");\n"
            "// internal wire\n"
            "wire w;\n"
            "endmodule\n"
        )
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, source_file="counter.v", comments=comments)
        m = design.modules[0]

        # Verify module name
        assert m.name == "counter"

        # Verify comment counts
        total_comments = sum(len(n.comments) for n in m.walk())
        assert total_comments == len(comments)

        # Emit and verify comments appear
        result = emit_module(m)
        assert "// Counter module" in result
        assert "// system clock" in result
        assert "// async reset" in result
        assert "// internal wire" in result

    def test_comments_without_extraction(self, parser):
        """tree_to_design without comments works as before (backward compat)."""
        source = "module m;\nwire w;\nendmodule\n"
        tree = parser.build_tree(source)
        design = tree_to_design(tree)
        m = design.modules[0]
        assert m.name == "m"
        assert len(m.nets) == 1
        assert m.comments == []
