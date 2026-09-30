"""Ibex controller regression with stimulus driven from Python.

The larger Ibex compatibility suite intentionally exercises timed Verilog
testbenches. This test keeps the DUT itself as the simulation top so the
compiled engine can run its clocked logic without a timing fallback.
"""

from pathlib import Path

import pytest

from veriforge.model.ports import PortDirection
from veriforge.project import parse_files
from veriforge.sim.testbench import Simulator

from .engines import ENGINES


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_fast_irq_with_python_stimulus(engine: str, tmp_path: Path) -> None:
    rtl_dir = Path(__file__).resolve().parents[2] / "examples" / "ibex" / "rtl"
    design = parse_files(
        [str(rtl_dir / "ibex_pkg.sv"), str(rtl_dir / "ibex_controller.sv")],
        preprocess=True,
        include_paths=[str(rtl_dir)],
        defines={"SYNTHESIS": ""},
        cache_dir=str(tmp_path / "pcache"),
    )
    dut = design.get_module("ibex_controller")
    assert dut is not None
    sim = Simulator(dut, engine=engine, design=design)

    # Give every DUT input a known value, then apply the same essential
    # conditions as the timed-Verilog fast-IRQ regression.
    for port in dut.ports:
        if port.direction == PortDirection.INPUT:
            sim.drive(port.name, 0)
    sim.drive("instr_exec_i", 1)
    sim.drive("ready_wb_i", 1)
    sim.drive("csr_mstatus_mie_i", 1)
    sim.drive("priv_mode_i", 3)  # PRIV_LVL_M
    sim.settle()

    def tick() -> tuple[int, int, int, int, int]:
        if engine == "compiled":
            assert sim.run_cycles(1, clock_name="clk_i", clock_period=2) == 1
        else:
            sim.drive("clk_i", 1)
            sim.settle()
        observed = tuple(
            int(sim.read(name))
            for name in (
                "exc_cause_o.irq_ext",
                "exc_cause_o.irq_int",
                "exc_cause_o.lower_cause",
                "pc_set_o",
                "csr_save_cause_o",
            )
        )
        if engine != "compiled":
            sim.drive("clk_i", 0)
            sim.settle()
        return observed

    tick()  # clock the controller while reset is asserted
    sim.drive("rst_ni", 1)
    sim.drive("irq_pending_i", 1)
    sim.drive("irqs_i", 1 << 3)  # irq_fast[3]
    sim.settle()

    assert (1, 0, 19, 1, 1) in [tick() for _ in range(5)]
    if engine == "compiled":
        assert sim.engine_report()["fallback_processes"] == 0
