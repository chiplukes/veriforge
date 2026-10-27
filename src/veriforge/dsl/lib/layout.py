"""Named fields of a packed word: build-time slices and concatenations.

A ``PackedLayout`` names the fields of a wide word (a FIFO entry, a command
or descriptor word, a header beat), first field lowest. In the DSL it gives
each field's slice of a word and packs one expression per field into a word;
in a testbench the same layout packs and unpacks Python ints, so stimulus and
checks use the field names the RTL uses::

    from veriforge.dsl.lib import PackedLayout

    DESC = PackedLayout(("port", 2), ("slice", 3), ("words", 5), ("last", 1), ("local", 20))

    word = m.wire(DESC.pack(port=port, slice=slc, words=nwords, last=is_last, local=addr))
    entry = m.wire(DESC.span("port", "last").of(fifo_dout))   # {last, words, slice, port}
    local = m.wire(DESC["local"].of(fifo_dout))

    DESC.pack_int(port=1, slice=0, words=4, last=1, local=0x100)   # -> int
    DESC.unpack_int(value)                                        # -> {"port": 1, ...}

Nothing here declares a signal or adds logic: ``of()`` is a bit or range
select and ``pack()`` is a concatenation, emitted as written by hand.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from ..builder import Expr, Signal, cat, lit


@dataclass(frozen=True)
class Field:
    """``width`` bits of a word from bit ``lsb``."""

    name: str
    lsb: int
    width: int

    @property
    def msb(self) -> int:
        return self.lsb + self.width - 1

    def of(self, word: Signal | Expr) -> Expr:
        """The field's bits of ``word``: ``word[lsb]`` for one bit, else ``word[msb:lsb]``
        (unsigned, as any select)."""
        return word[self.lsb] if self.width == 1 else word[self.msb : self.lsb]

    def get(self, word: int) -> int:
        """The field of a Python int word."""
        return (word >> self.lsb) & ((1 << self.width) - 1)


class PackedLayout:
    """Fields of a packed word from ``(name, width)`` pairs, first field lowest.

    Names are unique and widths at least 1 (a field that may be empty in some
    configuration is usually kept one bit wide and driven 0, so its neighbors
    keep their positions)."""

    def __init__(self, *fields: tuple[str, int]) -> None:
        if not fields:
            raise ValueError("PackedLayout needs at least one field")
        placed: dict[str, Field] = {}
        lsb = 0
        for name, width in fields:
            if name in placed:
                raise ValueError(f"PackedLayout: field {name!r} given twice")
            if not isinstance(width, int) or isinstance(width, bool) or width < 1:
                raise ValueError(f"PackedLayout: field {name!r} has width {width!r}; widths are ints >= 1")
            placed[name] = Field(name, lsb, width)
            lsb += width
        self._fields = placed
        self.width = lsb

    def __getitem__(self, name: str) -> Field:
        try:
            return self._fields[name]
        except KeyError:
            raise KeyError(f"no field {name!r}; fields are {list(self._fields)}") from None

    def __iter__(self) -> Iterator[Field]:
        return iter(self._fields.values())

    def __len__(self) -> int:
        return len(self._fields)

    def __contains__(self, name: object) -> bool:
        return name in self._fields

    @property
    def names(self) -> list[str]:
        """Field names, first (lowest) field first."""
        return list(self._fields)

    def __repr__(self) -> str:
        inner = ", ".join(f"({f.name!r}, {f.width})" for f in self)
        return f"PackedLayout({inner})"

    def span(self, first: str, last: str) -> Field:
        """The contiguous bits from field ``first`` up to and including field ``last``, as
        one Field (``first`` must not be above ``last``)."""
        lo, hi = self[first], self[last]
        if lo.lsb > hi.lsb:
            raise ValueError(f"span({first!r}, {last!r}): {first!r} is above {last!r}; give the lower field first")
        return Field(f"{first}..{last}", lo.lsb, hi.msb - lo.lsb + 1)

    def _parts(self, parts: Mapping | None, kwargs: dict, where: str) -> dict:
        given = {**(parts or {}), **kwargs}
        missing = [n for n in self._fields if n not in given]
        extra = [n for n in given if n not in self._fields]
        if missing or extra:
            raise ValueError(f"{where}: missing fields {missing}, unknown fields {extra}; fields are {self.names}")
        return given

    def pack(self, parts: Mapping[str, Signal | Expr | int] | None = None, /, **kwargs: Signal | Expr | int) -> Expr:
        """One part per field, by name (a mapping, keywords, or both), concatenated with the
        first field lowest: ``{last_field, ..., first_field}``.

        A part is an expression already the field's width (a Signal's declared width is
        checked) or an int, which becomes a sized literal of the field's width."""
        given = self._parts(parts, kwargs, "pack()")
        sized = []
        for f in reversed(list(self)):
            part = given[f.name]
            if isinstance(part, bool) or isinstance(part, int):
                part = int(part)
                if not 0 <= part < (1 << f.width):
                    raise ValueError(f"pack(): {f.name}={part} does not fit in {f.width} bits")
                part = lit(f"{f.width}'d{part}")
            elif isinstance(part, Signal) and isinstance(part._width, int) and part._width != f.width:
                raise ValueError(f"pack(): {f.name} is {f.width} bits, but {part._name} is {part._width}")
            sized.append(part)
        return cat(*sized)

    def pack_int(self, values: Mapping[str, int] | None = None, /, **kwargs: int) -> int:
        """The Python int word holding one value per field (each must fit its field)."""
        given = self._parts(values, kwargs, "pack_int()")
        word = 0
        for f in self:
            value = given[f.name]
            if not 0 <= value < (1 << f.width):
                raise ValueError(f"pack_int(): {f.name}={value} does not fit in {f.width} bits")
            word |= value << f.lsb
        return word

    def unpack_int(self, word: int) -> dict[str, int]:
        """Each field's value in a Python int word, by name (bits above the layout must be 0)."""
        if not 0 <= word < (1 << self.width):
            raise ValueError(f"unpack_int(): {word:#x} is not a {self.width}-bit word")
        return {f.name: f.get(word) for f in self}
