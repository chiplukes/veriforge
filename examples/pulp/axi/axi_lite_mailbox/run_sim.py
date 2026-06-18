"""Run the adapted AXI-Lite mailbox example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_lite_mailbox/run_sim.py
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.endpoints import AXILiteMaster, AXILiteResponseError
from veriforge.sim.example_runner import available_engines
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_DIR = SCRIPT_DIR / "rtl"
TB_FILE = SCRIPT_DIR / "tb" / "axi_lite_mailbox_tb.sv"
FILES = [
    str(RTL_DIR / "axi_lite_mailbox.sv"),
    str(TB_FILE),
]
MAX_TIME = 420
ENGINES = available_engines()
BASE0 = 0x000
BASE1 = 0x100
REG_MBOXW = 0x00
REG_MBOXR = 0x04
REG_STATUS = 0x08
REG_ERROR = 0x0C
REG_WIRQT = 0x10
REG_RIRQT = 0x14
REG_IRQS = 0x18
REG_IRQEN = 0x1C
REG_IRQP = 0x20
MAIL_P0_TO_P1 = 0xAABBCCDD
MAIL_P1_TO_P0 = 0x11223344
IRQ_ERROR = 0x4


def _read_int(sim: Simulator, signal_name: str) -> int:
    raw = sim.read(signal_name)
    try:
        return int(raw)
    except Exception as exc:
        raise RuntimeError(f"{signal_name} is not fully resolved: {raw}") from exc


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = _read_int(sim, signal_name)
    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected:#x}, got {actual:#x}")


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=sim.time)
    else:
        step_eval_now(sim)


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("axi_lite_mailbox_exec_tb")
    if top is None:
        raise RuntimeError("Top module 'axi_lite_mailbox_exec_tb' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), MAX_TIME)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_axi_lite_master(sim: Simulator, prefix: str, *, timeout_cycles: int = 8) -> AXILiteMaster:
    return AXILiteMaster(sim, prefix, default_timeout_cycles=timeout_cycles)


def _expect_value(actual: int, expected: int, message: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected:#x}, got {actual:#x}")


def _expect_read_error(master: AXILiteMaster, addr: int) -> None:
    try:
        master.read(addr)
    except AXILiteResponseError as exc:
        if "expected 0x0, got 0x2" not in str(exc):
            raise RuntimeError(f"unexpected AXI-Lite error text: {exc}") from exc
        return
    raise RuntimeError("expected AXI-Lite read response error")


def _exercise_mailbox(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    port0 = _make_axi_lite_master(sim, "slv0")
    port1 = _make_axi_lite_master(sim, "slv1")
    _settle_drives(sim, engine)

    _expect_value(port0.read(BASE0 + REG_STATUS), 0x1, "port0 reset status mismatch")
    _expect_value(port1.read(BASE1 + REG_STATUS), 0x1, "port1 reset status mismatch")
    _expect_value(port0.write(BASE0 + REG_WIRQT, 0x1), 0x0, "port0 WIRQT write response mismatch")
    _expect_value(port0.write(BASE0 + REG_RIRQT, 0x1), 0x0, "port0 RIRQT write response mismatch")
    _expect_value(port1.write(BASE1 + REG_WIRQT, 0x1), 0x0, "port1 WIRQT write response mismatch")
    _expect_value(port1.write(BASE1 + REG_RIRQT, 0x1), 0x0, "port1 RIRQT write response mismatch")

    _expect_value(port0.write(BASE0 + REG_MBOXW, MAIL_P0_TO_P1), 0x0, "port0 mailbox write response mismatch")
    _expect_value(port0.read(BASE0 + REG_STATUS), 0x1, "port0 post-write status mismatch")
    _expect_value(port1.read(BASE1 + REG_STATUS), 0x0, "port1 post-receive status mismatch")
    _expect_value(port1.read(BASE1 + REG_MBOXR), MAIL_P0_TO_P1, "port1 mailbox read mismatch")
    _expect_value(port1.read(BASE1 + REG_STATUS), 0x1, "port1 post-read status mismatch")

    _expect_value(port1.write(BASE1 + REG_MBOXW, MAIL_P1_TO_P0), 0x0, "port1 mailbox write response mismatch")
    _expect_value(port0.read(BASE0 + REG_MBOXR), MAIL_P1_TO_P0, "port0 mailbox read mismatch")

    _expect_value(port1.write(BASE1 + REG_IRQEN, IRQ_ERROR), 0x0, "port1 IRQEN write response mismatch")
    _expect_read_error(port1, BASE1 + REG_MBOXR)
    _expect(sim, "irq1", 1, "port1 irq should assert for enabled error pending")
    _expect_value(port1.read(BASE1 + REG_IRQP), IRQ_ERROR, "port1 IRQP mismatch")
    _expect_value(port1.read(BASE1 + REG_ERROR), 0x1, "port1 ERROR register mismatch")
    _expect_value(port1.write(BASE1 + REG_IRQS, IRQ_ERROR), 0x0, "port1 IRQS acknowledge response mismatch")
    _expect_value(port1.read(BASE1 + REG_IRQP), 0x0, "port1 IRQP clear mismatch")
    _expect(sim, "irq1", 0, "port1 irq should clear after acknowledge")


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_lite_mailbox_pcache")
    if design.get_module("axi_lite_mailbox") is None:
        raise RuntimeError("Imported typed module 'axi_lite_mailbox' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _exercise_mailbox(design, engine)
    except Exception:
        print("axi_lite_mailbox run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_lite_mailbox passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
