"""SystemVerilog enum, struct, union, and typedef model classes.

Represents ``typedef enum``, ``typedef struct``, and ``typedef union``
declarations commonly used for FSM states, bus interfaces, and data
packing in synthesisable SystemVerilog.

Example Verilog::

    typedef enum logic [1:0] {
        IDLE  = 2'b00,
        RUN   = 2'b01,
        DONE  = 2'b10
    } state_t;

    typedef struct packed {
        logic [7:0] data;
        logic       valid;
    } bus_t;

    typedef union packed {
        logic [31:0] word;
        logic [7:0]  byte_val;
    } word_t;
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .expressions import Expression, Literal, Range


class EnumMember(VerilogNode):
    """A single member of an enum declaration: ``IDLE = 2'b00``."""

    __slots__ = ("name", "value")

    def __init__(
        self,
        name: str,
        *,
        value: Expression | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.value: Expression | None = value

    def __repr__(self) -> str:
        if self.value is not None:
            return f"EnumMember({self.name!r}, value={self.value!r})"
        return f"EnumMember({self.name!r})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        if self.value is not None:
            d["value"] = self.value.to_dict()
        return d


class EnumType(VerilogNode):
    """An enum type specifier (may be anonymous or part of a typedef).

    Example: ``enum logic [1:0] { IDLE, RUN, DONE }``
    """

    __slots__ = ("base_type", "members", "signed", "width")

    def __init__(
        self,
        members: list[EnumMember],
        *,
        base_type: str | None = None,
        width: Range | None = None,
        signed: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.members: list[EnumMember] = members
        self.base_type: str | None = base_type  # "logic", "bit", "reg", "int", etc.
        self.width: Range | None = width
        self.signed: bool = signed

    def _child_nodes(self) -> list[VerilogNode]:
        children: list[VerilogNode] = list(self.members)
        if self.width is not None:
            children.append(self.width)  # type: ignore[arg-type]
        return children

    def __repr__(self) -> str:
        return f"EnumType(base={self.base_type!r}, members={len(self.members)})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.base_type:
            d["base_type"] = self.base_type
        if self.signed:
            d["signed"] = True
        if self.width is not None:
            d["width"] = self.width.to_dict()
        d["members"] = [m.to_dict() for m in self.members]
        return d


class StructField(VerilogNode):
    """A single field within a struct or union: ``logic [7:0] data;``."""

    __slots__ = ("data_type", "name", "signed", "width")

    def __init__(
        self,
        name: str,
        data_type: str,
        *,
        width: Range | None = None,
        signed: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.data_type: str = data_type  # "logic", "bit", "reg", "int", etc.
        self.width: Range | None = width
        self.signed: bool = signed

    def _child_nodes(self) -> list[VerilogNode]:
        if self.width is not None:
            return [self.width]  # type: ignore[list-item]
        return []

    def __repr__(self) -> str:
        parts = [f"StructField({self.name!r}, {self.data_type!r}"]
        if self.width is not None:
            parts.append(f", width={self.width!r}")
        if self.signed:
            parts.append(", signed=True")
        parts.append(")")
        return "".join(parts)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        d["data_type"] = self.data_type
        if self.width is not None:
            d["width"] = self.width.to_dict()
        if self.signed:
            d["signed"] = True
        return d


class StructType(VerilogNode):
    """A struct type specifier (may be anonymous or part of a typedef).

    Example: ``struct packed { logic [7:0] data; logic valid; }``
    """

    __slots__ = ("fields", "packed", "signed")

    def __init__(
        self,
        fields: list[StructField],
        *,
        packed: bool = False,
        signed: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.fields: list[StructField] = fields
        self.packed: bool = packed
        self.signed: bool = signed

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.fields)

    def __repr__(self) -> str:
        qualifiers = []
        if self.packed:
            qualifiers.append("packed")
        if self.signed:
            qualifiers.append("signed")
        q = " " + " ".join(qualifiers) if qualifiers else ""
        return f"StructType({q.strip()}, fields={len(self.fields)})"

    def total_width(self) -> int:
        """Compute the total bit-width of the struct.

        For packed structs, this is the sum of all field widths.
        """
        return sum(_field_width(f) for f in self.fields)

    def compute_layout(self) -> dict[str, tuple[int, int]]:
        """Compute field layout for a packed struct.

        Returns dict mapping field_name -> (bit_offset, bit_width).
        Fields are packed MSB-first: the first declared field occupies
        the highest bits, the last field occupies the lowest bits.
        """
        layout: dict[str, tuple[int, int]] = {}
        offset = 0
        for field in reversed(self.fields):
            w = _field_width(field)
            layout[field.name] = (offset, w)
            offset += w
        return layout

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.packed:
            d["packed"] = True
        if self.signed:
            d["signed"] = True
        d["fields"] = [f.to_dict() for f in self.fields]
        return d


class UnionType(VerilogNode):
    """A union type specifier (may be anonymous or part of a typedef).

    Example: ``union packed { logic [31:0] word; logic [7:0] byte_val; }``
    """

    __slots__ = ("fields", "packed", "signed")

    def __init__(
        self,
        fields: list[StructField],
        *,
        packed: bool = False,
        signed: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.fields: list[StructField] = fields
        self.packed: bool = packed
        self.signed: bool = signed

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.fields)

    def __repr__(self) -> str:
        qualifiers = []
        if self.packed:
            qualifiers.append("packed")
        if self.signed:
            qualifiers.append("signed")
        q = " " + " ".join(qualifiers) if qualifiers else ""
        return f"UnionType({q.strip()}, fields={len(self.fields)})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.packed:
            d["packed"] = True
        if self.signed:
            d["signed"] = True
        d["fields"] = [f.to_dict() for f in self.fields]
        return d


class TypedefDecl(VerilogNode):
    """A typedef declaration: ``typedef <type> <name>;``

    Supports ``typedef enum ... <name>;``, ``typedef struct ... <name>;``,
    ``typedef union ... <name>;``, and ``typedef <base_type> <name>;``
    (type alias).
    """

    __slots__ = ("enum_type", "name", "struct_type", "type_ref", "union_type", "width")

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        *,
        enum_type: EnumType | None = None,
        struct_type: StructType | None = None,
        union_type: UnionType | None = None,
        type_ref: str | None = None,
        width: Range | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.enum_type: EnumType | None = enum_type
        self.struct_type: StructType | None = struct_type
        self.union_type: UnionType | None = union_type
        self.type_ref: str | None = type_ref  # e.g. "logic", "bit [7:0]"
        self.width: Range | None = width  # Range from typedef base type (e.g. [3:0])

    def _child_nodes(self) -> list[VerilogNode]:
        if self.enum_type is not None:
            return [self.enum_type]
        if self.struct_type is not None:
            return [self.struct_type]
        if self.union_type is not None:
            return [self.union_type]
        return []

    def __repr__(self) -> str:
        if self.enum_type:
            return f"TypedefDecl({self.name!r}, enum={self.enum_type!r})"
        if self.struct_type:
            return f"TypedefDecl({self.name!r}, struct={self.struct_type!r})"
        if self.union_type:
            return f"TypedefDecl({self.name!r}, union={self.union_type!r})"
        return f"TypedefDecl({self.name!r}, type_ref={self.type_ref!r})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        if self.enum_type is not None:
            d["enum_type"] = self.enum_type.to_dict()
        if self.struct_type is not None:
            d["struct_type"] = self.struct_type.to_dict()
        if self.union_type is not None:
            d["union_type"] = self.union_type.to_dict()
        if self.type_ref is not None:
            d["type_ref"] = self.type_ref
        return d


# ── Helpers ────────────────────────────────────────────────────────────


def _field_width(field: StructField) -> int:
    """Compute the bit-width of a struct/union field."""
    if field.width is not None:
        try:
            if isinstance(field.width.msb, Literal) and isinstance(field.width.lsb, Literal):
                return int(field.width.msb.value) - int(field.width.lsb.value) + 1
        except (TypeError, ValueError):
            pass
    return 1
