"""Tests for the packed-word field layout helper (dsl.lib.layout)."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl import Module
from veriforge.dsl.lib import Field, PackedLayout
from veriforge.sim import Simulator

DESC = PackedLayout(("port", 2), ("slice", 3), ("words", 5), ("last", 1), ("local", 20))


class TestLayout:
    def test_positions_first_field_lowest(self):
        assert DESC.width == 31
        assert [(f.name, f.lsb, f.width) for f in DESC] == [
            ("port", 0, 2),
            ("slice", 2, 3),
            ("words", 5, 5),
            ("last", 10, 1),
            ("local", 11, 20),
        ]
        assert DESC["local"].msb == 30
        assert DESC.names == ["port", "slice", "words", "last", "local"]
        assert len(DESC) == 5 and "last" in DESC and "nope" not in DESC

    def test_span_covers_contiguous_fields(self):
        assert DESC.span("port", "last") == Field("port..last", 0, 11)
        assert DESC.span("words", "words") == Field("words..words", 5, 5)
        with pytest.raises(ValueError, match="give the lower field first"):
            DESC.span("last", "port")

    @pytest.mark.parametrize(
        ("fields", "match"),
        [
            ((), "at least one field"),
            ((("a", 1), ("a", 2)), "given twice"),
            ((("a", 0),), "widths are ints >= 1"),
            ((("a", True),), "widths are ints >= 1"),
        ],
    )
    def test_refused_layouts(self, fields, match):
        with pytest.raises(ValueError, match=match):
            PackedLayout(*fields)

    def test_unknown_field(self):
        with pytest.raises(KeyError, match="no field 'nope'"):
            DESC["nope"]


class TestInts:
    def test_pack_unpack_round_trip(self):
        values = {"port": 3, "slice": 5, "words": 17, "last": 1, "local": 0xABCDE}
        word = DESC.pack_int(**values)
        assert word == 3 | 5 << 2 | 17 << 5 | 1 << 10 | 0xABCDE << 11
        assert DESC.unpack_int(word) == values
        assert DESC.pack_int(values) == word  # a mapping works too
        assert DESC["words"].get(word) == 17

    def test_pack_int_checks(self):
        with pytest.raises(ValueError, match="missing fields \\['local'\\]"):
            DESC.pack_int(port=0, slice=0, words=0, last=0)
        with pytest.raises(ValueError, match="unknown fields \\['extra'\\]"):
            DESC.pack_int(port=0, slice=0, words=0, last=0, local=0, extra=1)
        with pytest.raises(ValueError, match="words=32 does not fit in 5 bits"):
            DESC.pack_int(port=0, slice=0, words=32, last=0, local=0)
        with pytest.raises(ValueError, match="not a 31-bit word"):
            DESC.unpack_int(1 << 31)


def _desc_module() -> Module:
    m = Module("desc_pack")
    port = m.input(width=2)
    slc = m.input(width=3)
    words = m.input(width=5)
    last = m.input()
    addr = m.input(width=20)
    word = m.output(width=DESC.width)
    entry = m.output(width=11)
    local_out = m.output(width=20)
    last_out = m.output()
    word.assign = DESC.pack(port=port, slice=slc, words=words, last=last, local=addr)
    entry.assign = DESC.span("port", "last").of(word)
    local_out.assign = DESC["local"].of(word)
    last_out.assign = DESC["last"].of(word)
    return m


class TestDsl:
    def test_emits_concatenation_and_selects(self):
        v = emit_module(_desc_module().build())
        assert "assign word = {addr, last, words, slc, port};" in v
        assert "assign entry = word[10:0];" in v
        assert "assign local_out = word[30:11];" in v
        assert "assign last_out = word[10];" in v  # one bit: a bit select, not word[10:10]

    def test_simulates_like_pack_int(self):
        values = {"port": 2, "slice": 6, "words": 9, "last": 1, "local": 0x12345}
        sim = Simulator(_desc_module().build())
        sim.drive("port", values["port"])
        sim.drive("slc", values["slice"])
        sim.drive("words", values["words"])
        sim.drive("last", values["last"])
        sim.drive("addr", values["local"])
        sim.run(max_time=0)
        word = sim.read("word").val
        assert word == DESC.pack_int(**values)
        assert sim.read("entry").val == DESC.span("port", "last").get(word)
        assert sim.read("local_out").val == values["local"]

    def test_int_parts_become_sized_literals(self):
        m = Module("hdr")
        x = m.input(width=4)
        out = m.output(width=7)
        out.assign = PackedLayout(("x", 4), ("pad", 2), ("flag", 1)).pack(x=x, pad=0, flag=1)
        assert "assign out = {1'd1, 2'd0, x};" in emit_module(m.build())

    def test_pack_checks(self):
        m = Module("bad")
        narrow = m.input(width=4)
        layout = PackedLayout(("a", 5), ("b", 1))
        with pytest.raises(ValueError, match="a is 5 bits, but narrow is 4"):
            layout.pack(a=narrow, b=0)
        with pytest.raises(ValueError, match="b=2 does not fit in 1 bits"):
            layout.pack(a=0, b=2)
        with pytest.raises(ValueError, match="missing fields \\['b'\\]"):
            layout.pack(a=0)
