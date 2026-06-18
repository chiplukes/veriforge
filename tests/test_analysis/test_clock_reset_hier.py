"""Hierarchical clock/reset extraction via instance port maps."""

from __future__ import annotations

from textwrap import dedent

from veriforge.analysis import (
    analyze_design,
    extract_clocks_resets,
    extract_clocks_resets_hier,
)
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(src: str):
    parser = verilog_parser(start="source_text")
    design = tree_to_design(parser.build_tree(text=dedent(src)))
    analyze_design(design)
    return design


def _multi_domain_design():
    return _parse(
        """
        module leaf (
            input  wire src_clk_i,
            input  wire src_rst_ni,
            input  wire dst_clk_i,
            input  wire dst_rst_ni,
            input  wire [7:0] din,
            output reg  [7:0] dout
        );
            always @(posedge src_clk_i or negedge src_rst_ni) begin
                if (!src_rst_ni) dout <= 8'h00;
                else             dout <= din;
            end
            always @(posedge dst_clk_i or negedge dst_rst_ni) begin
                if (!dst_rst_ni) dout <= 8'h00;
            end
        endmodule

        module wrapper (
            input  wire src_clk_i,
            input  wire src_rst_ni,
            input  wire dst_clk_i,
            input  wire dst_rst_ni,
            input  wire [7:0] din,
            output wire [7:0] dout
        );
            leaf u_leaf (
                .src_clk_i (src_clk_i),
                .src_rst_ni(src_rst_ni),
                .dst_clk_i (dst_clk_i),
                .dst_rst_ni(dst_rst_ni),
                .din       (din),
                .dout      (dout)
            );
        endmodule
        """
    )


def test_hier_promotes_clocks_and_resets_through_instance_port_map():
    design = _multi_domain_design()
    wrapper = design.get_module("wrapper")

    # The wrapper has no always blocks; local extractor finds nothing.
    local = extract_clocks_resets(wrapper)
    assert local.clocks == []
    assert local.resets == []

    hier = extract_clocks_resets_hier(wrapper, design)
    clock_names = sorted(c.name for c in hier.clocks)
    reset_names = sorted(r.name for r in hier.resets)
    assert clock_names == ["dst_clk_i", "src_clk_i"]
    assert reset_names == ["dst_rst_ni", "src_rst_ni"]

    # Reset polarities and clock pairings come from the leaf metadata.
    rst_by_name = {r.name: r for r in hier.resets}
    assert rst_by_name["src_rst_ni"].active_low is True
    assert rst_by_name["src_rst_ni"].clock == "src_clk_i"
    assert rst_by_name["dst_rst_ni"].clock == "dst_clk_i"


def test_hier_no_design_falls_back_to_local():
    design = _multi_domain_design()
    wrapper = design.get_module("wrapper")
    info = extract_clocks_resets_hier(wrapper, None)
    assert info.clocks == []
    assert info.resets == []


def test_hier_preserves_local_when_present():
    """When a module has local always blocks, hier just returns them."""
    design = _multi_domain_design()
    leaf = design.get_module("leaf")
    info = extract_clocks_resets_hier(leaf, design)
    assert sorted(c.name for c in info.clocks) == ["dst_clk_i", "src_clk_i"]
