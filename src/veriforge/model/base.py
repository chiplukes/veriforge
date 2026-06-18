"""Base classes for the Verilog semantic model.

All model objects inherit from VerilogNode, which provides source location
tracking, comment association, parent navigation, and serialization support.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Iterator, TypeVar

if TYPE_CHECKING:
    from lark import Tree

T = TypeVar("T", bound="VerilogNode")


class SourceLocation:  # cm:3a6e9c
    """Position in original Verilog source code."""

    __slots__ = ("column", "end_column", "end_line", "file", "line")

    def __init__(
        self,
        file: str | None = None,
        line: int = 0,
        column: int = 0,
        end_line: int = 0,
        end_column: int = 0,
    ):
        self.file = file
        self.line = line
        self.column = column
        self.end_line = end_line
        self.end_column = end_column

    def __repr__(self) -> str:
        if self.file:
            return f"SourceLocation({self.file}:{self.line}:{self.column})"
        return f"SourceLocation({self.line}:{self.column})"

    def __str__(self) -> str:
        if self.file:
            return f"{self.file}:{self.line}:{self.column}"
        return f"{self.line}:{self.column}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SourceLocation):
            return NotImplemented
        return (
            self.file == other.file
            and self.line == other.line
            and self.column == other.column
            and self.end_line == other.end_line
            and self.end_column == other.end_column
        )

    def to_dict(self) -> dict:
        d: dict = {"line": self.line, "column": self.column}
        if self.end_line:
            d["end_line"] = self.end_line
        if self.end_column:
            d["end_column"] = self.end_column
        if self.file:
            d["file"] = self.file
        return d

    @classmethod
    def from_meta(cls, meta, file: str | None = None) -> SourceLocation:
        """Create from a Lark tree node's .meta attribute."""
        return cls(
            file=file,
            line=getattr(meta, "line", 0),
            column=getattr(meta, "column", 0),
            end_line=getattr(meta, "end_line", 0),
            end_column=getattr(meta, "end_column", 0),
        )


class Comment:
    """A source code comment preserved from parsing."""

    __slots__ = ("kind", "loc", "position", "text")

    def __init__(
        self,
        text: str,
        loc: SourceLocation,
        kind: str = "line",  # "line" or "block"
        position: str = "leading",  # "leading", "trailing", "inline"
    ):
        self.text = text
        self.loc = loc
        self.kind = kind
        self.position = position

    def __repr__(self) -> str:
        return f"Comment({self.kind}, {self.position!r}, {self.text!r})"

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "kind": self.kind,
            "position": self.position,
            "loc": self.loc.to_dict(),
        }


class VerilogNode:  # cm:d4f6b3
    """Base class for all semantic model objects.

    Uses __slots__ for memory efficiency and Cython compatibility.
    """

    __slots__ = ("_parse_tree", "attributes", "comments", "loc", "parent")

    def __init__(
        self,
        loc: SourceLocation | None = None,
        comments: list[Comment] | None = None,
        parent: VerilogNode | None = None,
        _parse_tree: Tree | None = None,
    ):
        self.loc = loc or SourceLocation()
        self.comments = comments or []
        self.attributes: dict[str, str | None] = {}
        self.parent = parent
        self._parse_tree = _parse_tree

    def __deepcopy__(self, memo: dict) -> VerilogNode:
        """Deep-copy without following ``parent`` back-references.

        ``parent`` points up to the containing Module, so a naïve
        ``copy.deepcopy`` would pull in the entire module graph.
        We sever the link here and leave ``parent`` as *None* on the copy.
        """
        cls = type(self)
        result = cls.__new__(cls)
        memo[id(self)] = result
        # Walk all __slots__ in the MRO, copying everything except parent/_parse_tree
        for klass in cls.__mro__:
            for slot in getattr(klass, "__slots__", ()):
                if slot in ("parent", "_parse_tree"):
                    object.__setattr__(result, slot, None)
                    continue
                try:
                    val = getattr(self, slot)
                except AttributeError:
                    continue
                object.__setattr__(result, slot, copy.deepcopy(val, memo))
        return result

    def _child_nodes(self) -> list[VerilogNode]:
        """Override in subclasses to return direct child nodes for traversal."""
        return []

    def walk(self) -> Iterator[VerilogNode]:
        """Depth-first traversal of this node and all descendants."""
        yield self
        for child in self._child_nodes():
            yield from child.walk()

    def find(self, node_type: type[T]) -> Iterator[T]:
        """Find all descendants (including self) of a given type."""
        for node in self.walk():
            if isinstance(node, node_type):
                yield node

    def root(self) -> VerilogNode:
        """Walk up to the top-level node (Design)."""
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def to_dict(self) -> dict:
        """Serialize to dictionary. Override in subclasses."""
        d: dict = {"type": type(self).__name__}
        if self.loc and self.loc.line:
            d["loc"] = self.loc.to_dict()
        if self.comments:
            d["comments"] = [c.to_dict() for c in self.comments]
        if self.attributes:
            d["attributes"] = dict(self.attributes)
        return d
