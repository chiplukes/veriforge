"""Cancel stalled CDC transfers from either clock domain, then resume traffic.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_2phase_clearable/bench/cdc_2phase_clearable_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, StreamProxy, Testbench

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
RTL_FILES = [
    RTL_DIR / "sync.sv",
    RTL_DIR / "cdc_4phase_ctrl.sv",
    RTL_DIR / "cdc_reset_ctrlr.sv",
    RTL_DIR / "cdc_2phase_clearable.sv",
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("cdc_2phase_clearable")
    if dut is None:
        raise RuntimeError("Top module 'cdc_2phase_clearable' not found")
    overrides = PlannerOverrides(clock_periods={"src_clk_i": 10, "dst_clk_i": 14})
    return Testbench(dut, engine=engine, design=design, overrides=overrides)


def _wait_until(bench: Testbench, predicate, *, timeout: int, message: str) -> None:
    for _ in range(timeout):
        if predicate():
            return
        bench.step()
    raise AssertionError(message)


def _cancel_stalled_beat(
    bench: Testbench, source: StreamProxy, sink: StreamProxy, *, clear_side: str, payload: int
) -> None:
    sink.pause = True
    source.put(payload)
    source.wait_drain(timeout=100)
    _wait_until(
        bench,
        lambda: int(bench.sim.read("dst_valid_o")) == 1,
        timeout=100,
        message=f"{clear_side}-clear transfer never reached the destination",
    )
    if int(bench.sim.read("dst_data_o")) != payload or int(bench.sim.read("src_ready_o")) != 0:
        raise AssertionError(f"{clear_side}-clear transfer was not held at the stalled destination")

    clear_port = f"{clear_side}_clear_i"
    bench.sim.drive(clear_port, 1)
    bench.step(domain=f"{clear_side}_clk_i")
    bench.sim.drive(clear_port, 0)
    bench.sim.settle()
    _wait_until(
        bench,
        lambda: int(bench.sim.read(f"{clear_side}_clear_pending_o")) == 1,
        timeout=100,
        message=f"{clear_side} clear did not start",
    )
    other_side = "dst" if clear_side == "src" else "src"
    _wait_until(
        bench,
        lambda: int(bench.sim.read(f"{other_side}_clear_pending_o")) == 1,
        timeout=100,
        message=f"{clear_side} clear did not reach the other domain",
    )
    _wait_until(
        bench,
        lambda: (
            int(bench.sim.read("src_clear_pending_o")) == 0
            and int(bench.sim.read("dst_clear_pending_o")) == 0
            and int(bench.sim.read("src_ready_o")) == 1
            and int(bench.sim.read("dst_valid_o")) == 0
        ),
        timeout=200,
        message=f"{clear_side} clear did not withdraw the stalled transfer",
    )
    if sink.pending():
        raise AssertionError(f"{clear_side}-cancelled transfer reached the sink")


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("src_clear_i", 0)
        bench.sim.drive("dst_clear_i", 0)
        bench.reset_all()
        source = bench.iface("src")
        sink = bench.iface("dst")
        for clear_side, cancelled, recovered in (("src", 0x11, 0x22), ("dst", 0x33, 0x44)):
            _cancel_stalled_beat(bench, source, sink, clear_side=clear_side, payload=cancelled)
            source.put(recovered)
            sink.pause = False
            sink.expect(recovered, timeout=100)
            source.wait_drain(timeout=100)
            if sink.pending():
                raise AssertionError(f"unexpected transfer after {clear_side} clear recovery")

    print(f"cdc_2phase_clearable passed: both clear domains cancelled stalled beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
