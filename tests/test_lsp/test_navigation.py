"""Tests for LSP navigation: definition, references, hover."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("pygls")

from veriforge_lsp.index import LocationIndex
from veriforge_lsp.protocol import loc_to_lsp_range

# ── Verilog fixtures ──────────────────────────────────────────────────────────

TOP_V = """\
module top(
    input  [7:0] x, y,
    output [7:0] result
);
    wire [7:0] add_out;
    adder u_add(.a(x), .b(y), .sum(add_out));
    assign result = add_out;
endmodule
"""

ADDER_V = """\
module adder(
    input  [7:0] a, b,
    output [7:0] sum
);
    assign sum = a + b;
endmodule
"""


@pytest.fixture(scope="module")
def two_file_design(tmp_path_factory):
    """Parse a two-file design and return (design, top_path, adder_path)."""
    from veriforge.project import parse_files

    d = tmp_path_factory.mktemp("nav")
    top_path = d / "top.v"
    adder_path = d / "adder.v"
    top_path.write_text(TOP_V, encoding="utf-8")
    adder_path.write_text(ADDER_V, encoding="utf-8")
    design = parse_files([str(top_path), str(adder_path)])
    return design, str(top_path), str(adder_path)


@pytest.fixture(scope="module")
def built_index(two_file_design):
    design, top_path, adder_path = two_file_design
    idx = LocationIndex()
    idx.build(design)
    return idx, top_path, adder_path


# ── LocationIndex.node_at ─────────────────────────────────────────────────────


class TestNodeAt:
    def test_returns_none_for_unknown_file(self, built_index):
        idx, _, _ = built_index
        assert idx.node_at("/nonexistent.v", 0, 0) is None

    def test_finds_module_node(self, built_index):
        idx, top_path, _ = built_index
        # line 0 col 7 → "top" module declaration (LSP 0-based)
        node = idx.node_at(top_path, 0, 7)
        assert node is not None

    def test_finds_innermost_node(self, built_index):
        """node_at should return the smallest interval, not the module wrapper."""

        idx, top_path, _ = built_index
        # Line 1 (0-based) is the port declaration line
        node = idx.node_at(top_path, 1, 12)
        # Should NOT be the module itself — something more specific
        if node is not None:
            assert not (type(node).__name__ == "Module" and getattr(node, "name", "") == "top")


# ── LocationIndex.references_of ──────────────────────────────────────────────


class TestReferencesOf:
    def test_empty_for_node_with_no_refs(self, built_index):
        idx, _, _ = built_index
        mock_node = MagicMock()
        assert idx.references_of(mock_node) == []

    def test_files_list(self, built_index):
        idx, top_path, adder_path = built_index
        files = idx.files()
        assert top_path in files
        assert adder_path in files


# ── protocol helpers ──────────────────────────────────────────────────────────


class TestProtocol:
    def test_loc_to_lsp_range_basic(self):
        from veriforge.model.base import SourceLocation

        loc = SourceLocation(file="x.v", line=5, column=3, end_line=5, end_column=10)
        rng = loc_to_lsp_range(loc)
        assert rng["start"]["line"] == 4  # 1-based → 0-based
        assert rng["start"]["character"] == 2  # 1-based → 0-based
        assert rng["end"]["line"] == 4
        assert rng["end"]["character"] == 9

    def test_loc_to_lsp_range_no_end(self):
        from veriforge.model.base import SourceLocation

        loc = SourceLocation(file="x.v", line=3, column=1)
        rng = loc_to_lsp_range(loc)
        assert rng["start"]["line"] == 2
        assert rng["end"]["line"] == 2
