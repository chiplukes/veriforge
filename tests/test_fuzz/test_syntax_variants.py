"""Fuzz the parser with alternative spellings of the same design.

The emitter prints one spelling of each construct, so the fuzz runner never
fed the parser others: a function's old-style scalar `input j;` was taken
for a local variable (the argument never bound), and an old-style function's
or task's local declarations were dropped. Here each fuzz design is emitted
again with an `EmitStyle` (old-style ports, `@*`, `,`-separated sensitivity
lists, redundant parentheses, re-spelled literals, positional instance
connections): parsed and re-emitted
canonically it must match the canonical emission, and it must simulate the
same.
"""

from __future__ import annotations

import contextlib
import io
import random

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
    )
    with emit_style(style):
        variant = emit_design(design)
    assert emit_design(_parse(variant)) == emit_design(_parse(canonical)), style
    style.literal_rng = random.Random(seed)
    style.positional_connections = rng.random() < 0.6
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
