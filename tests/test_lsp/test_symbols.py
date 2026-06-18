"""Tests for LSP document and workspace symbol helpers."""

from __future__ import annotations

import pytest

pytest.importorskip("pygls")

ADDER_V = """\
module adder #(parameter WIDTH = 8) (
    input  [WIDTH-1:0] a, b,
    output [WIDTH-1:0] sum
);
    wire [WIDTH-1:0] carry;
    assign sum = a + b;
endmodule
"""


@pytest.fixture(scope="module")
def adder_module(tmp_path_factory):
    from veriforge.project import parse_files

    d = tmp_path_factory.mktemp("sym")
    p = d / "adder.v"
    p.write_text(ADDER_V, encoding="utf-8")
    design = parse_files([str(p)])
    return design.modules[0], str(p)


class TestModuleToSymbol:
    def test_module_symbol_name(self, adder_module):
        from veriforge_lsp.handlers.symbols import _module_to_symbol

        mod, _ = adder_module
        sym = _module_to_symbol(mod)
        assert sym.name == "adder"

    def test_port_children_present(self, adder_module):
        from veriforge_lsp.handlers.symbols import _module_to_symbol

        mod, _ = adder_module
        sym = _module_to_symbol(mod)
        child_names = [c.name for c in (sym.children or [])]
        assert "a" in child_names or "sum" in child_names

    def test_net_children_present(self, adder_module):
        from veriforge_lsp.handlers.symbols import _module_to_symbol

        mod, _ = adder_module
        sym = _module_to_symbol(mod)
        child_names = [c.name for c in (sym.children or [])]
        assert "carry" in child_names

    def test_parameter_children_present(self, adder_module):
        from veriforge_lsp.handlers.symbols import _module_to_symbol

        mod, _ = adder_module
        sym = _module_to_symbol(mod)
        child_names = [c.name for c in (sym.children or [])]
        assert "WIDTH" in child_names

    def test_symbol_has_range(self, adder_module):
        from veriforge_lsp.handlers.symbols import _module_to_symbol

        mod, _ = adder_module
        sym = _module_to_symbol(mod)
        assert sym.range is not None


class TestWidthStr:
    def test_no_width_returns_empty(self):
        from unittest.mock import MagicMock
        from veriforge_lsp.handlers.symbols import _width_str

        node = MagicMock(spec=[])  # no attributes
        assert _width_str(node) == ""

    def test_width_with_msb_lsb(self):
        from unittest.mock import MagicMock
        from veriforge_lsp.handlers.symbols import _width_str

        rng = MagicMock(msb=7, lsb=0)
        node = MagicMock(width=rng)
        assert _width_str(node) == "[7:0]"
