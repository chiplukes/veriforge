"""Callback-driven inputs must settle before the next simulation time step."""

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.trace import register_time_step_callback
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


CALLBACK_GRANT = """
module callback_grant(input clk, input req, input ack, output reg grant = 0);
    wire req_valid;
    assign req_valid = req;
    always @(posedge clk) begin
        if (grant) begin
            if (ack) grant <= 0;
        end else if (req_valid) begin
            grant <= 1;
        end
    end
endmodule
"""


@pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast"])
@pytest.mark.parametrize("stepped", [False, True])
def test_callback_drive_settles_continuous_logic_at_current_time(engine, stepped):
    module = tree_to_design(verilog_parser(start="source_text").build_tree(CALLBACK_GRANT)).modules[0]
    sim = Simulator(module, engine=engine)
    sim.drive("req", 1)
    sim.drive("ack", 1)
    sim.settle()
    sim.schedule_clock(Clock(sim.signal("clk"), period=10), 20)

    def drop_request(sched):
        if sched.time == 15:
            sim.drive("req", 0)

    with register_time_step_callback(sim._sched, drop_request):
        if stepped:
            while sim.time < 15:
                assert sim.run_step()
        else:
            sim.run(max_time=15)

    assert sim.time == 15
    assert int(sim.read("req_valid")) == 0
    assert int(sim.read("grant")) == 0
