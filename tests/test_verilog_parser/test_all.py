import logging
from pathlib import Path

import pytest

from veriforge.verilog_parser import verilog_parser

log = logging.getLogger(__name__)

VERILOG_DIR = Path(__file__).parent / "verilog"


@pytest.fixture
def parser():
    """Create a parser instance for testing."""
    return verilog_parser(start="verilog")


class TestVerilogParser:
    """Test suite for Verilog parser."""

    def test_parser_creation(self):
        """Test that the parser can be created."""
        p = verilog_parser(start="verilog")
        assert p is not None
        assert p.parser is not None

    def test_parse_simple_module(self, parser):
        """Test parsing a simple module with port list."""
        tree = parser.build_tree(VERILOG_DIR / "v_module1.v")
        assert tree is not None
        assert tree.data == "verilog"
        # Check we have a module declaration somewhere in the tree
        modules = list(tree.find_data("module_declaration"))
        assert len(modules) == 1

    def test_parse_module_with_parameters(self, parser):
        """Test parsing a module with parameters and always blocks."""
        tree = parser.build_tree(VERILOG_DIR / "verilog_all.v")
        assert tree is not None
        assert tree.data == "verilog"
        # Check for parameter declarations
        params = list(tree.find_data("parameter_declaration"))
        assert len(params) >= 1

    def test_parse_string_input(self, parser):
        """Test parsing from a string."""
        code = "module test_mod(input a, output b); endmodule"
        tree = parser.build_tree(code)
        assert tree is not None
        assert tree.data == "verilog"

    def test_parse_string_with_timescale_directive(self, parser):
        """Common parser-blocking directives are stripped before parsing."""
        code = "`timescale 1ns / 1ps\nmodule test_mod(input a, output b); endmodule"
        tree = parser.build_tree(code)
        assert tree is not None
        assert tree.data == "verilog"

    def test_empty_module(self, parser):
        """Test parsing minimal module."""
        code = "module empty(); endmodule"
        tree = parser.build_tree(code)
        assert tree is not None

    def test_parse_file_with_timescale_directive(self, parser, tmp_path):
        """File parsing should tolerate leading stripped directives too."""
        source = "`timescale 1 ns / 1 ps\nmodule timed(); endmodule\n"
        path = tmp_path / "timed.v"
        path.write_text(source, encoding="utf-8")
        tree = parser.build_tree(path)
        assert tree is not None
        assert tree.data == "verilog"


class TestModuleDeclarations:
    """Test module declaration variations."""

    @pytest.fixture
    def module_parser(self):
        """Parser starting at module_declaration rule."""
        return verilog_parser(start="module_declaration")

    def test_module_with_port_list(self, module_parser):
        """Test old-style port declaration."""
        code = "module foo(a, b, c); endmodule"
        tree = module_parser.build_tree(code)
        assert tree.data == "module_declaration"

    def test_module_with_port_declarations(self, module_parser):
        """Test ANSI-style port declarations."""
        code = "module foo(input a, output b); endmodule"
        tree = module_parser.build_tree(code)
        assert tree.data == "module_declaration"

    def test_module_with_parameters(self, module_parser):
        """Test module with parameter port list."""
        code = "module foo #(parameter WIDTH=8) (input [WIDTH-1:0] data); endmodule"
        tree = module_parser.build_tree(code)
        assert tree.data == "module_declaration"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

    # test_earley(f=Path(__file__).parent.absolute() / "verilog" / "vex2.v")
