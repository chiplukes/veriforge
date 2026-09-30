"""Standalone reproducer for Cython recursion in a wide native testbench wrapper.

Run with ``uv run pytest -q --run-slow tests/test_sim/test_compiled_wide_wrapper_recursion.py``.
Both the small control and 256-lane case compile and check the captured beat.
"""

import pytest

from veriforge.sim.bench import AXIStreamSinkLowering, AXIStreamSourceLowering, Testbench, compile_native
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


LANES = 256
BITS_PER_LANE = 8


def _wide_axis_adder_design(lanes: int):
    """Build original RTL: one byte adder instance per AXI-Stream data lane."""
    width = lanes * BITS_PER_LANE
    lane_wires = "\n".join(f"wire [7:0] lane_{i};" for i in range(lanes))
    lane_instances = "\n".join(
        f"lane_adder u_{i}(.a(m_axis_tdata[{8 * i + 7}:{8 * i}]), .y(lane_{i}));" for i in range(lanes)
    )
    assembled_data = ", ".join(f"lane_{i}" for i in reversed(range(lanes)))
    source = f"""
        module lane_adder(input wire [7:0] a, output wire [7:0] y);
            assign y = a + 8'd1;
        endmodule

        module axis_wide_add(
            input wire clk, input wire rst_n,
            input wire m_axis_tvalid, output wire m_axis_tready,
            input wire [{width - 1}:0] m_axis_tdata, input wire m_axis_tlast,
            output wire s_axis_tvalid, input wire s_axis_tready,
            output wire [{width - 1}:0] s_axis_tdata, output wire s_axis_tlast
        );
            assign m_axis_tready = s_axis_tready;
            assign s_axis_tvalid = m_axis_tvalid;
            assign s_axis_tlast = m_axis_tlast;
            {lane_wires}
            {lane_instances}
            assign s_axis_tdata = {{{assembled_data}}};
        endmodule
    """
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    return design.modules[-1], design


def _run_native_wrapper(lanes: int):
    dut, design = _wide_axis_adder_design(lanes)
    lowered = compile_native(
        Testbench(dut, design=design),
        lowerings={
            "m_axis": AXIStreamSourceLowering([0], data_width=lanes * BITS_PER_LANE),
            "s_axis": AXIStreamSinkLowering(1, data_width=lanes * BITS_PER_LANE),
        },
    )
    # compile_native currently includes only the wrapper and DUT; make the
    # instantiated leaf module available when Simulator flattens the wrapper.
    lowered.design.modules.extend(module for module in design.modules if module.name != dut.name)
    return lowered.run("compiled", max_time=200)


def _assert_captured_adder_beat(captures: dict[str, int], lanes: int):
    expected = sum(1 << (BITS_PER_LANE * i) for i in range(lanes))
    assert captures["s_axis_cap_0"] == expected
    assert captures["s_axis_snk_done"] == 1


@pytest.mark.slow
def test_compiled_native_wrapper_small_control():
    """Check that the generated stream and expected data work at small scale."""
    _assert_captured_adder_beat(_run_native_wrapper(4), 4)


@pytest.mark.slow
def test_compiled_native_wrapper_with_256_byte_lanes():
    """Compile a lowered AXI-Stream bench wrapping 256 instantiated adders."""
    _assert_captured_adder_beat(_run_native_wrapper(LANES), LANES)
