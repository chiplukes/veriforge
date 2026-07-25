from __future__ import annotations

from pathlib import Path
from typing import Any

from lark import Lark
from lark.reconstruct import Reconstructor

from .preprocessor import strip_parser_blocking_directives


class VerilogParser:
    def __init__(
        self,
        transformer: Any = None,
        parser: str = "earley",
        start: str | None = None,
        debug: bool = False,
    ) -> None:
        if transformer:
            raise ValueError(
                "Transformer is only compatible with the LALR parser, which does not support the Verilog EBNF grammar."
            )

        with open(Path(__file__).parent.absolute() / "lark_file" / "verilog.lark") as f:
            if transformer:
                self.parser = Lark(
                    f,
                    parser=parser,
                    transformer=transformer,
                    propagate_positions=True,
                    start=start,
                )
            else:
                self.parser = Lark(
                    f,
                    parser=parser,
                    propagate_positions=True,
                    start=start,
                    debug=debug,
                    keep_all_tokens=False,
                    maybe_placeholders=False,
                )

    def build_tree(self, text: str | Path | None = None) -> Any:
        """Build AST from *text* (string or file path)."""
        if isinstance(text, Path):
            with open(file=text, mode="r") as f:
                netlist = f.read()
        elif isinstance(text, str):
            netlist = text
        else:
            raise TypeError(f"input to parser expects str or Path, got {type(text).__name__!r}")

        netlist = strip_parser_blocking_directives(netlist)
        return self.parser.parse(netlist)

    def reconstruct(self, tree: Any = None) -> str:
        """Reconstruct Verilog source text from a parse tree."""
        new_verilog = Reconstructor(self.parser).reconstruct(tree)
        return new_verilog


verilog_parser = VerilogParser
"""Alias preserved for backward compatibility with external callers."""
