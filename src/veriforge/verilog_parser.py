# # Import the simple submodule
# from . import simple_submodule

from pathlib import Path
from lark import Lark
from lark.reconstruct import Reconstructor

from .preprocessor import strip_parser_blocking_directives


class verilog_parser(object):  # cm:1c7a4e
    def __init__(self, transformer=None, parser="earley", start=None, debug=False):
        if transformer:
            raise Exception(
                "At this time the transformer only works with LALR parser, which is not compatible with the Verilog EBNF if the verilog.lark file.  The language syntax needs to be changed so that there is no ambiguity between rules given that LALR only looks ahead by one token."
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

    def build_tree(self, text=None):  # cm:d5f3b8
        """
        builds AST from text
        """

        if isinstance(text, Path):
            with open(file=text, mode="r") as f:
                fin = f.readlines()
            netlist = "".join(fin)
        elif isinstance(text, str):
            netlist = text
        else:
            raise Exception("input to parser should be a string or path object")

        netlist = strip_parser_blocking_directives(netlist)

        # c = parser.parse_interactive(netlist)
        c = self.parser.parse(netlist)
        return c

    def reconstruct(self, tree=None):
        """
        reconstructs text from AST
        """
        new_verilog = Reconstructor(self.parser).reconstruct(tree)
        return new_verilog
