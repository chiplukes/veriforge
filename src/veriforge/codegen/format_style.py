"""Configurable formatting style for Verilog source emission.

Provides :class:`FormatStyle` with presets for common brace-placement
conventions and options for port alignment and column-limit wrapping.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FormatStyle:
    """Controls formatting decisions for Verilog code emission.

    Attributes:
        indent_width: Number of spaces per indentation level.
        begin_end_style: Placement of ``begin``/``end`` relative to control
            keywords.

            * ``"knr"`` — ``begin`` on the same line as the control keyword.
            * ``"allman"`` — ``begin`` on the next line, indented one level
              (same column as statements and ``end``).
            * ``"gnu"`` — ``begin`` on the next line at the same indentation
              as the control keyword.

        end_else_same_line: When *True*, emit ``end else`` on a single line
            (K&R convention).  When *False*, ``end`` and ``else`` appear on
            separate lines.
        align_ports: Align port signal names vertically in module header.
        column_limit: Target line length.  Instance port connections and
            parameter bindings that exceed this width wrap to multiple lines.
            Set to ``0`` to disable.
    """

    indent_width: int = 4
    begin_end_style: str = "knr"
    end_else_same_line: bool = True
    align_ports: bool = False
    column_limit: int = 100

    # -- Preset constructors ------------------------------------------------

    @classmethod
    def knr(cls, **overrides: object) -> FormatStyle:
        """K&R: ``begin`` on same line as control keyword."""
        defaults: dict = dict(begin_end_style="knr", end_else_same_line=True)
        defaults.update(overrides)
        return cls(**defaults)

    @classmethod
    def allman(cls, **overrides: object) -> FormatStyle:
        """Allman: ``begin`` on next line, indented to match contents/``end``."""
        defaults: dict = dict(begin_end_style="allman", end_else_same_line=False)
        defaults.update(overrides)
        return cls(**defaults)

    @classmethod
    def gnu(cls, **overrides: object) -> FormatStyle:
        """GNU: ``begin`` on next line at control-keyword indent."""
        defaults: dict = dict(begin_end_style="gnu", end_else_same_line=False)
        defaults.update(overrides)
        return cls(**defaults)
