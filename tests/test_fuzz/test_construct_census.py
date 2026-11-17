"""tools/construct_census.py counts rules, operator/keyword tokens and structural patterns
from parse trees, and its report ranks constructs real code uses that the fuzzer does not.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from veriforge.verilog_parser import VerilogParser

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "construct_census.py"


@pytest.fixture(scope="module")
def census():
    spec = importlib.util.spec_from_file_location("construct_census", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def parser():
    return VerilogParser(start="verilog")


_SRC = """
module top #(parameter W = 4) (input clk, input [W-1:0] a, output [W-1:0] q);
  genvar i, j;
  generate
    for (i = 0; i < 2; i = i + 1) begin : g
      if (W > 2) begin : h
        for (j = 0; j < 2; j = j + 1) begin : k
          always @(posedge clk) r[i] <= a >>> 1;
        end
      end
      sub #(.P(3)) u (.a(a), .b(q));
    end
  endgenerate
  assign q = u.x;
endmodule
"""


def test_tree_features(census, parser):
    feats = census.tree_features(parser.build_tree(_SRC))
    assert feats["loop_generate_construct"] == 2
    assert feats["if_generate_construct"] == 1
    assert feats["@nested_generate"] == 2  # the if and the inner for
    assert feats["@nested_generate_depth3"] == 1
    assert feats["@process_in_generate"] == 1
    assert feats["@instance_in_generate"] == 1
    assert feats["@param_override"] == 1
    assert feats["@dotted_name"] == 1
    assert feats["binary_operator:OP_BINARY_ARITH_SHIFTR"] == 1
    # Names, digits and keywords the rule is named after are not features.
    assert not any(":IDENTIFIER" in f or f.endswith(":KW_GENERATE") or ":KW_END" in f for f in feats)


def test_report_ranks_gaps(census):
    corpus = [
        {"path": "p1/a.v", "ok": True, "lines": 10, "features": {"x": 3, "y": 1, "shared": 1}},
        {"path": "p2/b.v", "ok": True, "lines": 10, "features": {"x": 1, "shared": 1}},
        {"path": "p2/c.v", "ok": False, "error": "UnexpectedCharacters", "detail": "line 1"},
    ]
    projects = {"p1/a.v": "c/p1", "p2/b.v": "c/p2", "p2/c.v": "c/p2"}
    fuzz = [{"strategy": "FEEDFORWARD", "seed": 0, "ok": True, "features": {"shared": 1, "only_fuzz": 2}}]
    report = census.build_report(corpus, projects, fuzz, {"x": "YES"})
    gaps = report.split("## 1.")[1].split("## 2.")[0]
    assert "| `x` | 2 | 2 | 4 | YES |" in gaps
    assert "`shared`" not in gaps
    assert "`y`" not in gaps  # single file, single project
    assert "| c/p2 | 1 / 2 |" in report
    assert "`only_fuzz`" in report.split("## 3.")[1]
