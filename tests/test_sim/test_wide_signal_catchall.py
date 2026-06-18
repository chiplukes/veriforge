"""File-backed SystemVerilog catch-all fixture for wide and narrow signal behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value

ENGINES = ["reference", "vm"]

FIXTURE_PATH = Path(__file__).resolve().parent / "verilog" / "wide_signal_catchall.sv"

MASK_16 = (1 << 16) - 1
MASK_32 = (1 << 32) - 1
MASK_64 = (1 << 64) - 1
MASK_65 = (1 << 65) - 1
MASK_128 = (1 << 128) - 1
MASK_130 = (1 << 130) - 1

INIT_MEM = {
    0: (0x112233445566778899AABBCCDDEEFF00, 1, 0),
    1: (0xFFEEDDCCBBAA99887766554433221100, 0, 1),
}

OUTPUT_WIDTHS = {
    "narrow_logic": 16,
    "narrow_concat": 16,
    "narrow_repl": 32,
    "narrow_eq": 1,
    "wide_mux": 130,
    "wide_slice_mux": 65,
    "wide_concat_mix": 130,
    "wide_replication": 130,
    "wide_mask_merge": 130,
    "wide_add": 130,
    "wide_sub": 130,
    "wide_shl": 130,
    "wide_shr": 130,
    "wide_ashr": 130,
    "wide_tree_mix": 130,
    "wide_tree_truth": 1,
    "wide_plus_slice": 65,
    "wide_minus_slice": 65,
    "wide_selected_bit": 1,
    "wide_eq": 1,
    "wide_neq": 1,
    "wide_any": 1,
    "wide_all": 1,
    "wide_parity": 1,
    "struct_mix": 130,
    "mem_word": 130,
    "mem_slice": 65,
    "seq_stage": 130,
    "seq_mem_word": 130,
}

SCENARIOS = [
    pytest.param(
        {
            "sel": 1,
            "mem_sel": 0,
            "plus_base": 24,
            "minus_base": 104,
            "bit_index": 9,
            "shamt": 3,
            "narrow_a": 0x1234,
            "narrow_b": 0x00F5,
            "wide_a": (0b10 << 128) | 0x123456789ABCDEF0011223344556677,
            "wide_b": (0b11 << 128) | 0x0FEDCBA9876543218899AABBCCDDEEFF,
            "wide_mask": ((1 << 65) - 1) << 17,
        },
        id="sel-a-mem0",
    ),
    pytest.param(
        {
            "sel": 0,
            "mem_sel": 1,
            "plus_base": 8,
            "minus_base": 120,
            "bit_index": 70,
            "shamt": 5,
            "narrow_a": 0xF0AA,
            "narrow_b": 0x0F0F,
            "wide_a": (0b01 << 128) | 0xFEDCBA98765432100123456789ABCDEF,
            "wide_b": (0b10 << 128) | 0x13579BDF2468ACE0F0E1D2C3B4A59687,
            "wide_mask": int("155555555555555555555555555555555", 16) & MASK_130,
        },
        id="sel-b-mem1",
    ),
]


def _pack_bus(data: int, valid: int, last: int) -> int:
    return ((data & MASK_128) << 2) | ((valid & 1) << 1) | (last & 1)


def _part_plus(value: int, base: int, width: int) -> int:
    return (value >> base) & ((1 << width) - 1)


def _part_minus(value: int, msb: int, width: int) -> int:
    return (value >> (msb - width + 1)) & ((1 << width) - 1)


def _signed_shift_right(value: int, shift: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    signed_value = value - (1 << width) if value & sign_bit else value
    return (signed_value >> shift) & ((1 << width) - 1)


def _repeat_pair(sel: int, count: int) -> int:
    pair = ((sel & 1) << 1) | ((~sel) & 1)
    out = 0
    for _ in range(count):
        out = (out << 2) | pair
    return out


def _parity(value: int) -> int:
    return value.bit_count() & 1


def _load_design(tmp_path: Path):
    return parse_files([str(FIXTURE_PATH)], preprocess=True, cache_dir=str(tmp_path / "pcache"))


def _drive_inputs(sim: Simulator, scenario: dict[str, int]) -> None:
    sim.drive("clk", Value(0, width=1))
    sim.drive("sel", Value(scenario["sel"], width=1))
    sim.drive("mem_sel", Value(scenario["mem_sel"], width=1))
    sim.drive("plus_base", Value(scenario["plus_base"], width=8))
    sim.drive("minus_base", Value(scenario["minus_base"], width=8))
    sim.drive("bit_index", Value(scenario["bit_index"], width=8))
    sim.drive("shamt", Value(scenario["shamt"], width=4))
    sim.drive("narrow_a", Value(scenario["narrow_a"], width=16))
    sim.drive("narrow_b", Value(scenario["narrow_b"], width=16))
    sim.drive("wide_a", Value(scenario["wide_a"], width=130))
    sim.drive("wide_b", Value(scenario["wide_b"], width=130))
    sim.drive("wide_mask", Value(scenario["wide_mask"], width=130))


def _expected_snapshots(
    scenario: dict[str, int],
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    sel = scenario["sel"]
    mem_sel = scenario["mem_sel"]
    plus_base = scenario["plus_base"]
    minus_base = scenario["minus_base"]
    bit_index = scenario["bit_index"]
    shamt = scenario["shamt"]
    narrow_a = scenario["narrow_a"]
    narrow_b = scenario["narrow_b"]
    wide_a = scenario["wide_a"] & MASK_130
    wide_b = scenario["wide_b"] & MASK_130
    wide_mask = scenario["wide_mask"] & MASK_130

    init_data, init_valid, init_last = INIT_MEM[mem_sel]
    pkt_a_data = (wide_a >> 2) & MASK_128
    pkt_a_valid = (wide_a >> 1) & 1
    wide_mux = wide_a if sel else wide_b
    wide_concat_mix = (((wide_a >> 65) & MASK_65) << 65) | (wide_b & MASK_65)
    wide_plus_slice = _part_plus(wide_a, plus_base, 65)
    wide_selected_bit = (wide_a >> bit_index) & 1

    combo_initial = {
        "narrow_logic": ((narrow_a & narrow_b) ^ (narrow_a | 0x00F0)) & MASK_16,
        "narrow_concat": (((narrow_a >> 8) & 0xFF) << 8) | (narrow_b & 0xFF),
        "narrow_repl": (((narrow_a & 0xFF) << 8) | (narrow_b & 0xFF)) * 0x00010001 & MASK_32,
        "narrow_eq": int(narrow_a == narrow_b),
        "wide_mux": wide_mux,
        "wide_slice_mux": ((wide_a if sel else wide_b) >> 64) & MASK_65,
        "wide_concat_mix": wide_concat_mix,
        "wide_replication": _repeat_pair(sel, 65) & MASK_130,
        "wide_mask_merge": ((wide_a & wide_mask) | (wide_b & (~wide_mask & MASK_130))) & MASK_130,
        "wide_add": (wide_a + wide_b) & MASK_130,
        "wide_sub": (wide_a - wide_b) & MASK_130,
        "wide_shl": (wide_a << shamt) & MASK_130,
        "wide_shr": (wide_b >> shamt) & MASK_130,
        "wide_ashr": _signed_shift_right(wide_b, shamt, 130),
        "wide_tree_mix": ((((wide_a if sel else wide_b) ^ wide_mask) + wide_concat_mix) & MASK_130),
        "wide_tree_truth": int((((wide_a if sel else wide_b) & wide_mask) & MASK_130) != 0),
        "wide_plus_slice": wide_plus_slice,
        "wide_minus_slice": _part_minus(wide_b, minus_base, 65),
        "wide_selected_bit": wide_selected_bit,
        "wide_eq": int(((wide_a >> 64) & MASK_65) == ((wide_b >> 64) & MASK_65)),
        "wide_neq": int((wide_a & MASK_65) != (wide_b & MASK_65)),
        "wide_any": int(((wide_a >> 64) & MASK_65) != 0),
        "wide_all": int((wide_b & MASK_65) == MASK_65),
        "wide_parity": _parity(wide_a & MASK_65),
        "struct_mix": (
            (((pkt_a_data >> 64) & MASK_64) << 66) | ((init_data & MASK_64) << 2) | (pkt_a_valid << 1) | init_last
        ),
        "mem_word": _pack_bus(init_data, init_valid, init_last),
        "mem_slice": _part_plus(init_data, plus_base, 65),
    }

    updated_data = (((wide_mux >> 66) & MASK_64) << 64) | (wide_plus_slice & MASK_64)
    updated_valid = sel
    updated_last = wide_selected_bit
    updated_mem_word = _pack_bus(updated_data, updated_valid, updated_last)

    combo_after_first_clock = dict(combo_initial)
    combo_after_first_clock.update(
        {
            "struct_mix": (
                (((pkt_a_data >> 64) & MASK_64) << 66)
                | ((updated_data & MASK_64) << 2)
                | (pkt_a_valid << 1)
                | updated_last
            ),
            "mem_word": updated_mem_word,
            "mem_slice": _part_plus(updated_data, plus_base, 65),
        }
    )

    seq_after_first_clock = {
        "seq_stage": combo_initial["wide_mask_merge"] if sel else combo_initial["wide_concat_mix"],
        "seq_mem_word": combo_initial["mem_word"],
    }
    seq_after_second_clock = {
        "seq_stage": combo_initial["wide_mask_merge"] if sel else combo_initial["wide_concat_mix"],
        "seq_mem_word": updated_mem_word,
    }

    return combo_initial, combo_after_first_clock, seq_after_first_clock, seq_after_second_clock


def _assert_outputs(sim: Simulator, expected: dict[str, int]) -> None:
    for name, value in expected.items():
        assert sim.read(name) == Value(value, width=OUTPUT_WIDTHS[name]), name


def test_wide_signal_catchall_parses(tmp_path):
    design = _load_design(tmp_path)
    assert design.get_module("wide_signal_catchall") is not None


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_wide_signal_catchall_cross_engine(engine, scenario, tmp_path):
    design = _load_design(tmp_path)
    top = design.get_module("wide_signal_catchall")
    sim = Simulator(top, engine=engine, design=design)

    _drive_inputs(sim, scenario)
    combo_initial, combo_after_first_clock, seq_after_first_clock, seq_after_second_clock = _expected_snapshots(
        scenario
    )

    sim.run(max_time=0)
    _assert_outputs(sim, combo_initial)

    clk = Clock(sim.signal("clk"), period=10)
    sim.fork(clk)
    sim._schedule_clock_events(clk, 80)

    for _ in range(2):
        sim.run_step()

    _assert_outputs(sim, combo_after_first_clock)
    _assert_outputs(sim, seq_after_first_clock)

    for _ in range(2):
        sim.run_step()

    _assert_outputs(sim, combo_after_first_clock)
    _assert_outputs(sim, seq_after_second_clock)
