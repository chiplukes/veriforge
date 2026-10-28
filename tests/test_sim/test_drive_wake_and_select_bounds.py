"""Testbench drives waking ``always @*``, and dynamic selects outside their target.

Each of these was found by the weekly slow suite
(``tests/test_sim/compiled/test_external_io_slow.py``, skipped by default)
after the IEEE time-0 change (``always @*`` waits for its first change):

- reference: a drive of a memory element (``m[0]``) never woke an
  ``always @*`` reading the memory -- before the first run, before a later
  ``run()``, or before ``settle()``;
- reference: a drive before the first run that reached a block only
  through a continuous assign (``assign s = i; always @* o = s;``) didn't
  wake it; vm had the same gap for a continuous assign into a memory element;
- reference: an ``always @*`` writing then reading a struct field of a
  memory element (``src[1].data[3]``) re-triggered itself forever;
- compiled: a dynamic bit or part-select write outside a >64-bit memory
  element (or with an x index) indexed past the memory arrays (a segfault);
  a bit write past bit 63 of a 64-bit reg wrapped to bit 0;
- vm-fast: a dynamic part-select write past a reg's top bit wrapped too.

Expected values for the selects come from Icarus (``iverilog -g2012``):
bits outside the target are not written, and an x index writes nothing.
"""

from __future__ import annotations

import pytest

from veriforge.model.design import Design
from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES


def _parse(tmp_path, source: str) -> Design:
    path = tmp_path / "dut.sv"
    path.write_text(source)
    return parse_file(path)


def _sim(design: Design, engine: str) -> Simulator:
    return Simulator(design.get_module("t"), engine=engine, design=design)


class TestDriveWakesAlwaysStar:
    _MEM = (
        "module t(output reg [7:0] y); reg [7:0] m [0:1]; reg [7:0] d [0:1];\n"
        "always @* y = m[0]; always @* d = m; endmodule\n"
    )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_element_drive(self, engine, tmp_path):
        sim = _sim(_parse(tmp_path, self._MEM), engine)
        got = []
        sim.drive("m[0]", Value(7, width=8))
        sim.run(max_time=0)
        got.append((sim.read("y"), sim.read("d[0]")))
        sim.drive("m[0]", Value(9, width=8))
        sim.run(max_time=1)
        got.append((sim.read("y"), sim.read("d[0]")))
        sim.drive("m[0]", Value(4, width=8))
        sim.settle()
        got.append((sim.read("y"), sim.read("d[0]")))
        assert got == [(Value(n, width=8), Value(n, width=8)) for n in (7, 9, 4)]

    @pytest.mark.parametrize(
        "source",
        [
            "module t(input [7:0] i, output reg [7:0] o); wire [7:0] s; assign s = i; always @* o = s; endmodule",
            "module t(input [7:0] i, output reg [7:0] o); wire [7:0] s, s2; assign s = i; assign s2 = s + 1;"
            " always @* o = s2 - 1; endmodule",
            "module t(input [7:0] i, output [7:0] o); reg [7:0] m [0:1]; reg [7:0] d [0:1];"
            " assign m[1] = i; assign o = d[0]; always @* d[0] = m[1]; endmodule",
        ],
        ids=["wire", "two-assigns", "memory-element"],
    )
    @pytest.mark.parametrize("engine", ENGINES)
    def test_drive_through_continuous_assign_before_first_run(self, engine, source, tmp_path):
        sim = _sim(_parse(tmp_path, source), engine)
        sim.drive("i", Value(7, width=8))
        sim.run(max_time=0)
        assert sim.read("o") == Value(7, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_constant_assign_does_not_wake(self, engine, tmp_path):
        # The rule the fixes keep: a continuous assign's starting value isn't
        # a change (Icarus counts it; see known_issues.md).
        design = _parse(
            tmp_path, "module t(output reg [7:0] o); wire [7:0] c; assign c = 3; always @* o = c; endmodule"
        )
        sim = _sim(design, engine)
        sim.run(max_time=0)
        assert sim.read("o").is_x

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_field_of_memory_written_and_read_settles(self, engine, tmp_path):
        design = _parse(
            tmp_path,
            "module t(input in_bit, output out_bit);\n"
            "  typedef struct packed { logic [7:0] data; logic valid; } bus_t;\n"
            "  bus_t src [0:1];\n  bus_t dst [0:1];\n"
            "  assign out_bit = dst[0].data[3];\n"
            "  always @* begin\n    src[1].data[3] = in_bit;\n    dst[0].data[3] = src[1].data[3];\n  end\n"
            "endmodule\n",
        )
        sim = _sim(design, engine)
        sim.drive("in_bit", 1)
        sim.run(max_time=0)
        assert sim.read("out_bit") == Value(1, width=1)


def _select_source(width: int, kind: str, sel: str, form: str) -> str:
    target = "mem[0]" if kind == "mem" else "r"
    decl = f"reg [{width - 1}:0] mem [0:0];" if kind == "mem" else f"reg [{width - 1}:0] r;"
    lhs = f"{target}[bi]" if sel == "bit" else f"{target}[bi {sel} 3]"
    rhs = "din[0]" if sel == "bit" else "din"
    body = f"always @* {lhs} = {rhs};" if form == "combo" else f"always @(posedge clk) {lhs} <= {rhs};"
    return (
        f"module t(input clk, input [7:0] bi, input [2:0] din, output [{width - 1}:0] q);\n"
        f"  {decl}\n  initial {target} = 0;\n  {body}\n  assign q = {target};\nendmodule\n"
    )


def _expected(width: int, sel: str, index: int) -> int:
    bits = [index] if sel == "bit" else list(range(index, index + 3) if sel == "+:" else range(index - 2, index + 1))
    return sum(1 << b for b in bits if 0 <= b < width)


@pytest.mark.parametrize("form", ["combo", "nba"])
@pytest.mark.parametrize("sel", ["bit", "+:", "-:"])
@pytest.mark.parametrize("kind", ["mem", "reg"])
@pytest.mark.parametrize("width", [8, 64, 65])
@pytest.mark.parametrize("engine", ENGINES)
def test_dynamic_select_write_outside_target(engine, width, kind, sel, form, tmp_path):
    design = _parse(tmp_path, _select_source(width, kind, sel, form))
    got, want = [], []
    for index in sorted({0, 1, width - 2, width - 1, width, width + 1, 200}):
        sim = _sim(design, engine)
        sim.drive("clk", 0)
        sim.run(max_time=1)
        sim.drive("bi", index)
        sim.drive("din", 7)
        sim.run(max_time=2)
        sim.drive("clk", 1)
        sim.run(max_time=3)
        got.append((index, sim.read("q")))
        want.append((index, Value(_expected(width, sel, index), width=width)))
    assert got == want


@pytest.mark.parametrize("sel", ["[bi]", "[bi +: 2]", "[bi -: 2]"])
@pytest.mark.parametrize("engine", ENGINES)
def test_wide_memory_continuous_select_with_x_then_driven_index(engine, sel, tmp_path):
    # Elaboration settles `assign` while `bi` is still x: compiled indexed
    # past the memory arrays there (a segfault).
    rhs_w = 1 if sel == "[bi]" else 2
    design = _parse(
        tmp_path,
        f"module t(input [6:0] bi, input [{rhs_w - 1}:0] din);\n"
        f"  reg [64:0] mem [0:0];\n  assign mem[0]{sel} = din;\nendmodule\n",
    )
    sim = _sim(design, engine)
    sim.drive("bi", 64)
    sim.drive("din", (1 << rhs_w) - 1)
    sim.run(max_time=0)
    written = {"[bi]": 1 << 64, "[bi +: 2]": 1 << 64, "[bi -: 2]": 3 << 63}[sel]
    assert sim.read("mem[0]") == Value(written, width=65, mask=((1 << 65) - 1) & ~written)
