"""Fuzz the parser with alternative spellings of the same design.

The emitter prints one spelling of each construct, so the fuzz runner never
fed the parser others: a function's old-style scalar `input j;` was taken
for a local variable (the argument never bound), and an old-style function's
or task's local declarations were dropped. Here each fuzz design is emitted
again with an `EmitStyle` (old-style ports, `@*`, `,`-separated sensitivity
lists, redundant parentheses, a `generate` region: parsed and re-emitted
canonically it must match the canonical emission; plus re-spelled literals,
positional instance connections, `else begin if`, named blocks, net
declaration assignments, a block-local `for` variable and re-grouped case
items, which change the model: it must simulate the same).
"""

from __future__ import annotations

import contextlib
import io
import random
import re

import pytest

from veriforge.analysis.resolver import link_instances
from veriforge.codegen.verilog_emitter import EmitStyle, emit_design, emit_style
from veriforge.fuzz._module_gen import ModuleGenerator
from veriforge.fuzz._runner import FuzzRunner
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(text: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(text), source_file="f.v")


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    with contextlib.redirect_stdout(io.StringIO()):
        return FuzzRunner(
            output_dir=str(tmp_path_factory.mktemp("syntax")), seed=0, engines=["reference"], icarus=False
        )


@pytest.mark.parametrize("seed", range(40))
def test_syntax_variant(runner, seed):
    rng = random.Random(seed)
    design = ModuleGenerator(rng).generate_design(None)
    vectors = runner._gen_stimulus(design.get_module("t"), rng)
    canonical = emit_design(design)
    style = EmitStyle(
        old_style_ports=rng.random() < 0.7,
        star_paren=rng.random() < 0.5,
        sensitivity_comma=rng.random() < 0.5,
        extra_parens=rng.random() < 0.5,
        generate_region=rng.random() < 0.5,
    )
    with emit_style(style):
        variant = emit_design(design)
    assert emit_design(_parse(variant)) == emit_design(_parse(canonical)), style
    style.literal_rng = random.Random(seed)
    style.positional_connections = rng.random() < 0.6
    style.else_begin_if = rng.random() < 0.5
    style.named_blocks = rng.random() < 0.5
    style.net_decl_assign = rng.random() < 0.6
    style.for_var_outside = rng.random() < 0.5
    style.case_rng = random.Random(seed + 1)
    link_instances(design)  # (positional connections need the child's port order)
    with emit_style(style):
        respelled = emit_design(design)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert runner._simulate(respelled, "reference", vectors) == runner._simulate(canonical, "reference", vectors)


def test_old_style_function_and_task_locals():
    source = """module t;
  function automatic [7:0] f;
    input [2:0] a;
    reg signed [3:0] l1;
    begin l1 = a; f = l1; end
  endfunction
  task tk;
    input [2:0] a;
    output [3:0] q;
    integer l2;
    begin l2 = a; q = l2; end
  endtask
endmodule
"""
    module = _parse(source).modules[0]
    assert [v.name for v in module.functions[0].locals] == ["l1"]
    assert [v.name for v in module.tasks[0].locals] == ["l2"]


_ALL_KNOBS = """module t(input clk, input [2:0] s, output reg [7:0] o, output [7:0] w);
  wire [7:0] n;
  reg [7:0] lv1;
  assign n = {5'd0, s} + 8'd1;
  assign w = n ^ lv1;
  for (genvar g = 0; g < 2; g = g + 1) begin : gb
    wire x = s[g];
  end
  always @(posedge clk) begin
    if (s == 3'd0)
      o <= 8'd10;
    else if (s == 3'd1)
      o <= 8'd11;
    else
      case (s)
        3'd2: o <= 8'd20;
        3'd3: o <= 8'd20;
        3'd4: o <= n;
        default: begin
          o <= 0;
          for (int lv1 = 0; lv1 < 3; lv1 = lv1 + 1) o <= o + lv1 + gb[1].x;
        end
      endcase
  end
endmodule
"""


def test_all_model_changing_knobs(runner):
    design = _parse(_ALL_KNOBS)
    style = EmitStyle(
        generate_region=True,
        else_begin_if=True,
        named_blocks=True,
        net_decl_assign=True,
        for_var_outside=True,
        case_rng=random.Random(3),
    )
    with emit_style(style):
        variant = emit_design(design)
    for text in ("endgenerate", "begin : vfb", "wire [7:0] n = ", "int lv1;", "3'd2, 3'd3:"):
        assert text in variant, (text, variant)
    assert re.search(r"else\n\s*begin : vfb\d+\n\s*if \(", variant), variant
    canonical = emit_design(design)
    rng = random.Random(0)
    vectors = runner._gen_stimulus(design.get_module("t"), rng)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert runner._simulate(variant, "reference", vectors) == runner._simulate(canonical, "reference", vectors)
