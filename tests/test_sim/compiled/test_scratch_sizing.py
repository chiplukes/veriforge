"""Wide scratch arrays: sized by values, zeroed compactly.

A ``$readmemh(file, mem)`` on a 4096x8 RAM measured the memory as one
32768-bit value, so every wide statement in the module used 512-word
scratch arrays and zeroed the unused words one generated line each. In
the full ``axis_pix_correction2`` IP (256 such RAMs) that was 63% of a
2.9M-line .pyx and most of a ~35-minute build.
"""

from __future__ import annotations

import re

import pytest

from veriforge.project import parse_file
from veriforge.sim.compiled._gen_sections import _compact_scratch_zero_stores
from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from ..engines import ENGINES

_ROM_WORDS = 4096


def _design(tmp_path):
    rom = tmp_path / "rom.mem"
    rom.write_text("\n".join(f"{(i * 37) & 0xFF:02x}" for i in range(_ROM_WORDS)) + "\n")
    path = tmp_path / "dut.sv"
    path.write_text(
        "module t(input [11:0] a, input [7:0] b, output [7:0] q, output [129:0] w);\n"
        "  reg [7:0] rom [0:4095];\n"
        f'  initial $readmemh("{rom.as_posix()}", rom);\n'
        "  assign q = rom[a];\n"
        "  assign w = {b, 58'd0, rom[a], 56'd0} | {122'd0, b};\n"
        "endmodule\n"
    )
    return parse_file(path)


def test_readmem_memory_does_not_size_scratch(tmp_path):
    pyx = CythonCodegen().generate(_design(tmp_path).get_module("t"))
    sizes = {int(n) for n in re.findall(r"cdef unsigned long long _sc\d+_v\[(\d+)\]", pyx)}
    assert sizes == {3}  # the 130-bit statement, not 4096 * 8 bits
    assert "DEF MAX_VALUE_WORDS = 16" in pyx


@pytest.mark.parametrize("engine", ENGINES)
def test_readmem_rom_and_wide_statement_values(engine, tmp_path):
    sim = Simulator(_design(tmp_path).get_module("t"), engine=engine)
    got = []
    for a, b in ((0, 0x5A), (1, 0xFF), (4095, 0x01)):
        sim.drive("a", a)
        sim.drive("b", b)
        sim.run(max_time=sim.time + 1)
        got.append((sim.read("q"), sim.read("w")))
    want = []
    for a, b in ((0, 0x5A), (1, 0xFF), (4095, 0x01)):
        r = (a * 37) & 0xFF
        want.append((Value(r, width=8), Value((b << 122) | (r << 56) | b, width=130)))
    assert got == want


def test_compact_merges_contiguous_runs_per_array():
    lines = [
        "    x = 1",
        *(f"    _sc0_v[{k}] = 0" for k in range(1, 7)),
        *(f"    _sc0_m[{k}] = 0" for k in range(1, 7)),
        "    _sc1_v[2] = 0",
        "    _sc1_v[3] = 0",
        "    y = 2",
    ]
    assert _compact_scratch_zero_stores(lines) == [
        "    x = 1",
        "    memset(&_sc0_v[1], 0, 6 * sizeof(unsigned long long))",
        "    memset(&_sc0_m[1], 0, 6 * sizeof(unsigned long long))",
        "    _sc1_v[2] = 0",
        "    _sc1_v[3] = 0",
        "    y = 2",
    ]


def test_compact_interleaved_pairs_gaps_and_indent_boundaries():
    lines = [
        *(s for k in range(2, 8) for s in (f"        _sc2_v[{k}] = 0", f"        _sc2_m[{k}] = 0")),
        "        _sc2_v[10] = 0",
        "    _sc2_v[8] = 0",  # another block: not merged with the run above
        "    _sc2_v[9] = 0",
    ]
    assert _compact_scratch_zero_stores(lines) == [
        "        memset(&_sc2_v[2], 0, 6 * sizeof(unsigned long long))",
        "        _sc2_v[10] = 0",
        "        memset(&_sc2_m[2], 0, 6 * sizeof(unsigned long long))",
        "    _sc2_v[8] = 0",
        "    _sc2_v[9] = 0",
    ]


def test_compact_leaves_other_stores_alone():
    lines = ["    _sc0_v[0] = x", "    _sc0_v[1] = 0", "    _sc0_m[1] = _word_mask64(3)", "    _sc0_v[2] = 0"]
    assert _compact_scratch_zero_stores(lines) == lines
