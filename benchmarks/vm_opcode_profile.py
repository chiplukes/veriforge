#!/usr/bin/env python3
"""Report compiled and executed VM opcode mixes.

Run: uv run python benchmarks/vm_opcode_profile.py --cycles 100
Executed counts use the Python VM's instruction loop and are intended to guide
native VM work. Scheduler differences can change how often a process executes.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from collections import Counter
from copy import deepcopy

from benchmark import BENCH_DUT, Clock, Simulator, Value
from vm_propagation import _chain, _memory, _parse

from veriforge.sim.vm.compiler import Compiler
from veriforge.sim.vm.interpreter import Interpreter
from veriforge.sim.vm.opcodes import Op


def _instruction_line():
    source, first_line = inspect.getsourcelines(Interpreter.execute)
    return next(first_line + i for i, line in enumerate(source) if line.strip() == "pc += 1")


def _executed_counts(sim, cycles, *, reset):
    counts = Counter()
    instruction_line = _instruction_line()

    def local_trace(frame, event, _arg):
        if event == "line" and frame.f_lineno == instruction_line:
            counts[Op(frame.f_locals["op"]).name] += 1
        return local_trace

    def global_trace(frame, event, _arg):
        if event == "call" and frame.f_code is Interpreter.execute.__code__:
            return local_trace
        return None

    def reset_stimulus(s):
        s.drive("rst", Value(1, width=1))
        s._sched.schedule_at(20, ("clock_toggle", "rst", Value(0, width=1)))

    sim.fork(Clock(sim.signal("clk"), period=10))
    sys.settrace(global_trace)
    try:
        sim.run(reset_stimulus if reset else None, max_time=cycles * 10 - 5)
    finally:
        sys.settrace(None)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()
    if min(args.cycles, args.top) < 1:
        parser.error("cycles and top must be positive")

    workloads = (
        ("bench-dut", _parse(BENCH_DUT), True),
        ("reconvergent-chain-16", _chain(16, reconvergent=True)[0], False),
        ("memory", _memory()[0], False),
    )
    for name, module, reset in workloads:
        compiler = Compiler()
        compiler.compile_module(deepcopy(module))
        static = Counter(Op(op).name for proc in compiler.processes for op, _, _ in proc.program)
        sim = Simulator(deepcopy(module), engine="vm")
        executed = _executed_counts(sim, args.cycles, reset=reset)
        print(f"{name}: {sum(static.values())} compiled, {sum(executed.values())} executed instructions")
        print("  compiled:", static.most_common(args.top))
        print("  executed:", executed.most_common(args.top))


if __name__ == "__main__":
    main()
