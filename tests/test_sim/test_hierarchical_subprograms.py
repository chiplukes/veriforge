"""Functions and tasks retain instance identity through shared elaboration."""

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


def _sim(parser, source, engine):
    design = tree_to_design(parser.build_tree(source))
    return Simulator(design.get_module("top"), design=design, engine=engine)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("root", ["", "top."])
def test_same_function_name_in_two_instances(parser, engine, root):
    source = f"""module leaf #(parameter ADD=1)();
      function automatic [7:0] f(input [7:0] x); f=x+ADD; endfunction
    endmodule
    module top(input [7:0] a, output [7:0] y0,y1);
      leaf #(.ADD(1)) u0(); leaf #(.ADD(9)) u1();
      assign y0={root}u0.f(a); assign y1={root}u1.f(a);
    endmodule"""
    sim = _sim(parser, source, engine)
    for data in (5, 9):
        sim.drive("a", data)
        sim.settle()
        assert sim.read("y0") == Value(data + 1, width=8)
        assert sim.read("y1") == Value(data + 9, width=8)


@pytest.mark.parametrize("engine", ENGINES)
def test_same_task_name_in_two_instances(parser, engine):
    source = """module leaf();
      reg [7:0] r;
      task set_value(input [7:0] x); r=x; endtask
    endmodule
    module top(output [7:0] y0,y1);
      leaf u0(); leaf u1();
      initial begin u0.set_value(3); u1.set_value(5); end
      assign y0=u0.r; assign y1=u1.r;
    endmodule"""
    sim = _sim(parser, source, engine)
    sim.run(max_time=0)
    assert sim.read("y0") == Value(3, width=8)
    assert sim.read("y1") == Value(5, width=8)


def _nested_source(width, layout):
    leaf = f"""module leaf #(parameter W={width}, MASK=1)
      (input [W-1:0] d, output [W-1:0] local_result);
      reg [W-1:0] r;
      function automatic [W-1:0] f(input [W-1:0] x); f=x^MASK; endfunction
      function automatic [W-1:0] wrapper(input [W-1:0] x); wrapper=f(x); endfunction
      task store(input [W-1:0] x, output [W-1:0] echo);
        if (x[0]) r=f(x); else r=f(x);
        echo=r;
      endtask
      task call_store(input [W-1:0] x, output [W-1:0] echo);
        store(x,echo);
      endtask
      assign local_result=wrapper(d);
    endmodule\n"""
    if layout == "nested":
        leaf += """module mid #(parameter W=8, MASK=1)(input [W-1:0] d, output [W-1:0] local_result);
          leaf #(.W(W),.MASK(MASK)) u(.d(d),.local_result(local_result));
        endmodule\n"""
        instances = f"mid #(.W({width}),.MASK(1)) a(.d(d),.local_result(la)); mid #(.W({width}),.MASK(9)) b(.d(d),.local_result(lb));"
        a, b = "a.u", "b.u"
    elif layout == "generated":
        instances = f"""localparam COUNT=2;
          for (genvar i=0; i<COUNT; i=i+1) begin : g
            leaf #(.W({width}),.MASK(1+8*i)) u(.d(d),.local_result());
          end
          assign la=g[0].u.local_result; assign lb=g[1].u.local_result;
        """
        a, b = "g[COUNT-2].u", "g[COUNT-1].u"
    else:
        instances = f"leaf #(.W({width}),.MASK(1)) a(.d(d),.local_result(la)); leaf #(.W({width}),.MASK(9)) b(.d(d),.local_result(lb));"
        a, b = "a", "b"
    return (
        leaf
        + f"""module top(input clk, input [{width - 1}:0] d,
      output [{width - 1}:0] ya,yb,la,lb,ra,rb, output reg [{width - 1}:0] ta,tb);
      {instances}
      assign ya={a}.f(d); assign yb={b}.wrapper(d);
      assign ra={a}.r; assign rb={b}.r;
      always @(posedge clk) begin {a}.call_store(d,ta); {b}.call_store(d,tb); end
    endmodule"""
    )


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("layout", ["direct", "nested", "generated"])
def test_internal_and_hierarchical_calls_stay_instance_local(parser, engine, width, layout):
    sim = _sim(parser, _nested_source(width, layout), engine)
    sim.drive("clk", 0)
    sim.drive("d", 0)
    sim.settle()
    for data, mask in ((5, 0), (1 << (width - 1), 0), (0, 1 << (width - 1)), (7, 0)):
        sim.drive("d", Value(data, mask=mask, width=width))
        sim.drive("clk", 1)
        sim.settle()
        for suffix, delta in (("a", 1), ("b", 9)):
            expected = Value(data ^ delta, mask=mask, width=width)
            for prefix in ("y", "l", "r", "t"):
                assert sim.read(prefix + suffix) == expected, (engine, layout, prefix + suffix)
        sim.drive("clk", 0)
        sim.settle()


@pytest.mark.parametrize("engine", ENGINES)
def test_backend_callable_names_do_not_alias(parser, engine):
    source = """module leaf();
      function [7:0] f(input [7:0] x); f=x+1; endfunction
    endmodule
    module top(input [7:0] d, output [7:0] y0,y1,y2);
      leaf u0();
      function [7:0] u0_f(input [7:0] x); u0_f=x+30; endfunction
      function [7:0] _vf_hier_75302e66(input [7:0] x); _vf_hier_75302e66=x+20; endfunction
      assign y0=u0.f(d); assign y1=u0_f(d); assign y2=_vf_hier_75302e66(d);
    endmodule"""
    sim = _sim(parser, source, engine)
    sim.drive("d", 5)
    sim.settle()
    assert [int(sim.read(n)) for n in ("y0", "y1", "y2")] == [6, 35, 25]


@pytest.mark.parametrize("engine", ENGINES)
def test_repeated_task_calls_do_not_reuse_a_previous_function_result(parser, engine):
    source = """module leaf();
      reg [7:0] r;
      function [7:0] f(input [7:0] x); f=x+1; endfunction
      task store(input [7:0] x); r=f(x); endtask
    endmodule
    module top(output reg [7:0] y0,y1);
      leaf u();
      initial begin
        u.store(2); y0=u.r;
        u.store(7); y1=u.r;
      end
    endmodule"""
    sim = _sim(parser, source, engine)
    sim.run(max_time=0)
    assert sim.read("y0") == Value(3, width=8)
    assert sim.read("y1") == Value(8, width=8)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kind", ["function", "task"])
def test_unresolved_qualified_calls_fail_explicitly(parser, engine, kind):
    call = "assign y=u.missing(8'd5);" if kind == "function" else "initial u.missing(8'd5);"
    source = f"module leaf(); endmodule\nmodule top(output [7:0] y); leaf u(); {call} endmodule"
    with pytest.raises(ValueError, match=f"Unresolved hierarchical {kind} 'u.missing'"):
        _sim(parser, source, engine)
