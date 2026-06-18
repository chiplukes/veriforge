"""AXI-Stream frame helpers.

The frame class stores element-oriented payload data and can convert to or from
AXI beat arrays (`tdata`, `tkeep`, `tdest`, `tuser`, `tid`, `tlast`).
"""

from __future__ import annotations

from dataclasses import dataclass


BITS_PER_BYTE = 8


class ElementSizeError(ValueError):
    """Raised when an element does not fit within ``element_size_bits``."""


class BeatSizeError(ValueError):
    """Raised when a beat does not fit within ``elements_per_beat * element_size_bits``."""


def _validate_endian(endian: str) -> str:
    if endian not in {"little", "big"}:
        raise ValueError("endian must be 'little' or 'big'")
    return endian


def _coerce_bit_list(values: list[int] | list[bool], *, name: str, expected_len: int) -> list[int]:
    if len(values) != expected_len:
        raise AssertionError(f"{name} array must match length of data array")
    result: list[int] = []
    for value in values:
        if value in (0, 1, False, True):
            result.append(int(value))
        else:
            raise ValueError(f"{name} entries should be 1, 0, True, or False")
    return result


def _coerce_scalar_or_list(values: int | list[int], *, name: str, expected_len: int) -> list[int]:
    if isinstance(values, int):
        return [values for _ in range(expected_len)]
    if isinstance(values, list):
        if len(values) != expected_len:
            raise AssertionError(f"{name} array must match length of data array")
        if not all(isinstance(value, int) for value in values):
            raise ValueError(f"{name} should be an int or list of ints")
        return list(values)
    raise ValueError(f"{name} should be an int or list of ints")


@dataclass(slots=True)
class AXIStreamFrame:
    """Element-oriented AXI-Stream transaction container."""

    data: list[int]
    keep: list[int]
    dest: list[int]
    tid: list[int]
    user: list[int]
    last: list[int]
    elements_per_beat: int = 1
    element_size_bits: int = 8
    repr_items: int = -1
    allow_trailing: bool = False
    endian: str = "little"

    def __init__(  # noqa: PLR0913
        self,
        data: AXIStreamFrame | bytes | bytearray | list[int] | None = None,
        keep: list[int] | list[bool] | None = None,
        dest: int | list[int] = 0,
        tid: int | list[int] = 0,
        user: int | list[int] = 0,
        last: list[int] | list[bool] | None = None,
        *,
        elements_per_beat: int = 1,
        element_size_bits: int = 8,
        repr_items: int = -1,
        allow_trailing: bool = False,
        endian: str = "little",
    ) -> None:
        if elements_per_beat <= 0:
            raise ValueError("elements_per_beat must be positive")
        if element_size_bits <= 0:
            raise ValueError("element_size_bits must be positive")

        self.elements_per_beat = elements_per_beat
        self.element_size_bits = element_size_bits
        self.repr_items = repr_items
        self.allow_trailing = allow_trailing
        self.endian = _validate_endian(endian)
        self.data = []
        self.keep = []
        self.dest = []
        self.tid = []
        self.user = []
        self.last = []

        if isinstance(data, AXIStreamFrame):
            self.elements_per_beat = data.elements_per_beat
            self.element_size_bits = data.element_size_bits
            self.repr_items = data.repr_items
            self.allow_trailing = data.allow_trailing
            self.endian = data.endian
            self.data = list(data.data)
            self.keep = list(data.keep)
            self.dest = list(data.dest)
            self.tid = list(data.tid)
            self.user = list(data.user)
            self.last = list(data.last)
            return

        self.data = self._coerce_data(data)
        expected_len = len(self.data)

        if keep is None:
            self.keep = [1 for _ in range(expected_len)]
        elif isinstance(keep, list):
            self.keep = _coerce_bit_list(keep, name="keep", expected_len=expected_len)
        else:
            raise ValueError("keep should be None or a list of ints/bools")

        self.dest = _coerce_scalar_or_list(dest, name="dest", expected_len=expected_len)
        self.tid = _coerce_scalar_or_list(tid, name="tid", expected_len=expected_len)
        self.user = _coerce_scalar_or_list(user, name="user", expected_len=expected_len)

        if last is None:
            self.last = [0 for _ in range(expected_len)]
            if self.last:
                self.last[-1] = 1
        elif isinstance(last, list):
            self.last = _coerce_bit_list(last, name="last", expected_len=expected_len)
        else:
            raise ValueError("last should be None or a list of ints/bools")

    @property
    def element_max(self) -> int:
        return (1 << self.element_size_bits) - 1

    @property
    def beat_max(self) -> int:
        return (1 << (self.elements_per_beat * self.element_size_bits)) - 1

    def _coerce_data(self, data: bytes | bytearray | list[int] | None) -> list[int]:
        if data is None:
            return []
        if isinstance(data, (bytes, bytearray)):
            if self.element_size_bits % BITS_PER_BYTE != 0:
                raise ElementSizeError("data is bytes-like, but element_size_bits is not a multiple of 8")
            element_size_bytes = self.element_size_bits // BITS_PER_BYTE
            result = []
            for index in range(0, len(data), element_size_bytes):
                chunk = data[index : index + element_size_bytes]
                result.append(int.from_bytes(chunk, byteorder="little", signed=False))
            return result
        if isinstance(data, list):
            result = []
            for value in data:
                if not isinstance(value, int):
                    raise ValueError("data list must contain only ints")
                if value < 0 or value > self.element_max:
                    raise ElementSizeError(f"{value:#x} in data exceeds element_size_bits({self.element_size_bits})")
                result.append(value)
            return result
        raise ValueError("data is not an AXIStreamFrame, bytes-like object, list of ints, or None")

    def clear(self) -> None:
        self.data.clear()
        self.keep.clear()
        self.dest.clear()
        self.tid.clear()
        self.user.clear()
        self.last.clear()

    def to_beats(self) -> tuple[list[int], list[int], list[int], list[int], list[int], list[int]]:
        """Pack element arrays into beat arrays."""
        tdata: list[int] = []
        tkeep: list[int] = []
        tdest: list[int] = []
        tuser: list[int] = []
        tid: list[int] = []
        tlast: list[int] = []

        data_word = 0
        keep_word = 0
        beat_dest: set[int] = set()
        beat_tid: set[int] = set()
        beat_user: set[int] = set()
        beat_index = 0

        for index, value in enumerate(self.data):
            if self.endian == "little":
                shift = beat_index * self.element_size_bits
                keep_shift = beat_index
            else:
                shift = (self.elements_per_beat - beat_index - 1) * self.element_size_bits
                keep_shift = self.elements_per_beat - beat_index - 1

            data_word |= value << shift
            keep_word |= self.keep[index] << keep_shift
            beat_dest.add(self.dest[index])
            beat_tid.add(self.tid[index])
            beat_user.add(self.user[index])
            beat_index += 1

            if beat_index >= self.elements_per_beat or index == len(self.data) - 1:
                if len(beat_dest) != 1:
                    raise ValueError("dest must match for all elements within a beat")
                if len(beat_tid) != 1:
                    raise ValueError("tid must match for all elements within a beat")
                if len(beat_user) != 1:
                    raise ValueError("user must match for all elements within a beat")

                tdata.append(data_word)
                tkeep.append(keep_word)
                tdest.append(next(iter(beat_dest)))
                tid.append(next(iter(beat_tid)))
                tuser.append(next(iter(beat_user)))
                tlast.append(self.last[index])

                data_word = 0
                keep_word = 0
                beat_dest.clear()
                beat_tid.clear()
                beat_user.clear()
                beat_index = 0

        return tdata, tkeep, tdest, tuser, tid, tlast

    def from_beats(  # noqa: PLR0913, PLR0912
        self,
        *,
        tdata: list[int],
        tkeep: list[int] | None = None,
        tdest: list[int] | None = None,
        tuser: list[int] | None = None,
        tid: list[int] | None = None,
        tlast: list[int] | None = None,
        capture_leading: bool = False,
        capture_trailing: bool = False,
    ) -> None:
        """Unpack beat arrays into element arrays."""
        if capture_trailing:
            raise NotImplementedError("capture_trailing is not implemented")
        if not isinstance(tdata, list):
            raise ValueError("tdata must be defined and must be a list")
        if tkeep is None:
            tkeep = [(1 << self.elements_per_beat) - 1 for _ in tdata]
        if len(tkeep) != len(tdata):
            raise AssertionError("length of tkeep and tdata arrays must be the same")
        for name, values in (("tdest", tdest), ("tuser", tuser), ("tid", tid), ("tlast", tlast)):
            if values is not None and len(values) != len(tdata):
                raise AssertionError(f"length of {name} and tdata arrays must be the same")

        self.clear()
        mask = self.element_max

        for beat_number, beat in enumerate(tdata):
            if beat < 0 or beat > self.beat_max:
                raise BeatSizeError(f"{beat:#x} in tdata exceeds beat width")

            set_last = False
            for element_index in range(self.elements_per_beat):
                if self.endian == "little":
                    keep_value = (tkeep[beat_number] >> element_index) & 0x1
                    shift = element_index * self.element_size_bits
                else:
                    keep_value = (tkeep[beat_number] >> (self.elements_per_beat - element_index - 1)) & 0x1
                    shift = (self.elements_per_beat - element_index - 1) * self.element_size_bits

                include = bool(keep_value) or (beat_number == 0 and capture_leading)
                if include:
                    self.data.append((beat >> shift) & mask)
                    self.keep.append(keep_value)
                    self.dest.append(tdest[beat_number] if tdest is not None else 0)
                    self.user.append(tuser[beat_number] if tuser is not None else 0)
                    self.tid.append(tid[beat_number] if tid is not None else 0)
                    self.last.append(0)
                    if tlast is not None and tlast[beat_number]:
                        set_last = True

            if set_last and self.last:
                self.last[-1] = 1

        if tlast is None and self.last:
            self.last[-1] = 1

    def to_bytes(self) -> bytearray:
        if self.element_size_bits != BITS_PER_BYTE:
            raise ElementSizeError("to_bytes requires element_size_bits == 8")
        return bytearray(self.data)

    def to_elements(self, endian: str = "little") -> list[int]:
        endian = _validate_endian(endian)
        mask = self.element_max
        elements: list[int] = []
        for index, beat in enumerate(self.data):
            for element_index in range(self.elements_per_beat):
                if endian == "little":
                    keep_value = (self.keep[index] >> element_index) & 0x1
                    shift = element_index * self.element_size_bits
                else:
                    keep_value = (self.keep[index] >> (self.elements_per_beat - element_index - 1)) & 0x1
                    shift = (self.elements_per_beat - element_index - 1) * self.element_size_bits
                if keep_value:
                    elements.append((beat >> shift) & mask)
        return elements

    def __iter__(self):
        return iter(self.data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AXIStreamFrame):
            raise TypeError("Objects being compared must be AXIStreamFrame instances")

        def _length(lhs: list[int], rhs: list[int]) -> int:
            if self.allow_trailing or other.allow_trailing:
                return min(len(lhs), len(rhs))
            return len(lhs)

        keep_len = _length(self.keep, other.keep)
        dest_len = _length(self.dest, other.dest)
        tid_len = _length(self.tid, other.tid)
        user_len = _length(self.user, other.user)
        last_len = _length(self.last, other.last)
        data_len = _length(self.data, other.data)

        return (
            self.keep[:keep_len] == other.keep[:keep_len]
            and self.dest[:dest_len] == other.dest[:dest_len]
            and self.tid[:tid_len] == other.tid[:tid_len]
            and self.user[:user_len] == other.user[:user_len]
            and self.last[:last_len] == other.last[:last_len]
            and self.data[:data_len] == other.data[:data_len]
        )

    def __repr__(self) -> str:
        def _short(values: list[int]) -> list[int] | str:
            if self.repr_items < 0 or len(values) <= self.repr_items:
                return values
            if self.repr_items == 0:
                return "..."
            return values[: self.repr_items]

        return (
            "AXIStreamFrame("
            f"data={_short(self.data)}, keep={_short(self.keep)}, dest={_short(self.dest)}, "
            f"tid={_short(self.tid)}, user={_short(self.user)}, last={_short(self.last)}, "
            f"elements_per_beat={self.elements_per_beat}, element_size_bits={self.element_size_bits}, "
            f"endian={self.endian!r})"
        )
