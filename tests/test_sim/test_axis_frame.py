from __future__ import annotations

import pytest

from veriforge.sim.endpoints import AXIStreamFrame, BeatSizeError, ElementSizeError


def test_frame_init_from_bytes_defaults() -> None:
    frame = AXIStreamFrame(data=bytearray([0x10, 0x20, 0x30, 0x40]))

    assert frame.data == [0x10, 0x20, 0x30, 0x40]
    assert frame.keep == [1, 1, 1, 1]
    assert frame.dest == [0, 0, 0, 0]
    assert frame.tid == [0, 0, 0, 0]
    assert frame.user == [0, 0, 0, 0]
    assert frame.last == [0, 0, 0, 1]


def test_frame_init_from_bytes_requires_byte_multiple_element_size() -> None:
    with pytest.raises(ElementSizeError):
        AXIStreamFrame(data=bytearray([0x10, 0x20]), element_size_bits=9)


def test_frame_init_from_list_with_metadata() -> None:
    frame = AXIStreamFrame(data=[0x1, 0x2, 0x3], dest=7, tid=5, user=9, element_size_bits=4)

    assert frame.data == [0x1, 0x2, 0x3]
    assert frame.dest == [7, 7, 7]
    assert frame.tid == [5, 5, 5]
    assert frame.user == [9, 9, 9]


def test_frame_init_rejects_element_overflow() -> None:
    with pytest.raises(ElementSizeError):
        AXIStreamFrame(data=[0x10], element_size_bits=4)


def test_frame_to_beats_little_endian() -> None:
    frame = AXIStreamFrame(data=[0, 1, 2], dest=1, tid=3, user=5, elements_per_beat=2, endian="little")

    tdata, tkeep, tdest, tuser, tid, tlast = frame.to_beats()

    assert tdata == [0x0100, 0x0002]
    assert tkeep == [0b11, 0b01]
    assert tdest == [1, 1]
    assert tuser == [5, 5]
    assert tid == [3, 3]
    assert tlast == [0, 1]


def test_frame_to_beats_big_endian() -> None:
    frame = AXIStreamFrame(data=[0, 1, 2, 3], dest=1, tid=3, user=5, elements_per_beat=2, endian="big")

    tdata, tkeep, tdest, tuser, tid, tlast = frame.to_beats()

    assert tdata == [0x0001, 0x0203]
    assert tkeep == [0b11, 0b11]
    assert tdest == [1, 1]
    assert tuser == [5, 5]
    assert tid == [3, 3]
    assert tlast == [0, 1]


def test_frame_to_beats_rejects_mixed_metadata_within_beat() -> None:
    frame = AXIStreamFrame(data=[0, 1, 2], dest=[4, 5, 5], elements_per_beat=2)

    with pytest.raises(ValueError):
        frame.to_beats()


def test_frame_from_beats_little_endian_discards_leading_zero_keep() -> None:
    frame = AXIStreamFrame(elements_per_beat=2, endian="little")
    frame.from_beats(tdata=[0x1100, 0x3322, 0x5544, 0x7766], tkeep=[0b10, 0b11, 0b11, 0b01])

    assert frame.data == [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
    assert frame.keep == [1, 1, 1, 1, 1, 1]
    assert frame.dest == [0, 0, 0, 0, 0, 0]
    assert frame.user == [0, 0, 0, 0, 0, 0]
    assert frame.tid == [0, 0, 0, 0, 0, 0]
    assert frame.last == [0, 0, 0, 0, 0, 1]


def test_frame_from_beats_capture_leading() -> None:
    frame = AXIStreamFrame(elements_per_beat=2, endian="little")
    frame.from_beats(tdata=[0x1100, 0x3322, 0x5544, 0x7766], tkeep=[0b10, 0b11, 0b11, 0b01], capture_leading=True)

    assert frame.data == [0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
    assert frame.keep == [0, 1, 1, 1, 1, 1, 1]
    assert frame.last == [0, 0, 0, 0, 0, 0, 1]


def test_frame_from_beats_big_endian() -> None:
    frame = AXIStreamFrame(elements_per_beat=2, endian="big")
    frame.from_beats(tdata=[0x1100, 0x3322, 0x5544, 0x7766], tkeep=[0b01, 0b11, 0b11, 0b10])

    assert frame.data == [0x00, 0x33, 0x22, 0x55, 0x44, 0x77]
    assert frame.keep == [1, 1, 1, 1, 1, 1]
    assert frame.last == [0, 0, 0, 0, 0, 1]


def test_frame_from_beats_rejects_oversized_beat() -> None:
    frame = AXIStreamFrame(elements_per_beat=2, element_size_bits=8)
    with pytest.raises(BeatSizeError):
        frame.from_beats(tdata=[0x1_0000])


def test_frame_to_bytes_and_copy_roundtrip() -> None:
    original = AXIStreamFrame(data=bytearray([1, 2, 3]))
    copied = AXIStreamFrame(original)

    assert copied == original
    assert copied.to_bytes() == bytearray([1, 2, 3])


def test_frame_to_elements_uses_keep_mask() -> None:
    frame = AXIStreamFrame(elements_per_beat=2, endian="little")
    frame.from_beats(tdata=[0x1100, 0x3322], tkeep=[0b10, 0b01], capture_leading=True)

    assert frame.to_elements("little") == [0x11, 0x22]
