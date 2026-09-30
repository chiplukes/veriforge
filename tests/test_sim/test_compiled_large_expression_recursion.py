"""Cython translation regressions for expressions that grow with RTL size."""

import shutil
import subprocess

import pytest

from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import Identifier, Literal
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import NonblockingAssign, SensitivityEdge
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


pytestmark = pytest.mark.slow


def _translate(module: Module, tmp_path):
    pyx = tmp_path / "large.pyx"
    CythonCodegen().generate_to_file(module, str(pyx))
    cython = shutil.which("cython")
    assert cython is not None
    result = subprocess.run(
        [cython, "-3", str(pyx), "-o", str(tmp_path / "large.c")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


def _parse(source: str) -> Module:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    return design.modules[0]


@pytest.mark.parametrize("operation", ["negate", "shift"])
def test_32768_bit_word_checks_translate(operation, tmp_path):
    """Wide unary and shift operators must not emit 512-term expressions."""
    if operation == "negate":
        source = "module wide(input wire [32767:0] a, output wire [32767:0] y); assign y = -a; endmodule"
    else:
        source = (
            "module wide(input wire [127:0] a, input wire [32767:0] amount, "
            "output wire [127:0] y); assign y = a << amount; endmodule"
        )
    _translate(_parse(source), tmp_path)


@pytest.mark.parametrize("case_kind", ["narrow_values", "wide_values", "wide_words"])
def test_large_case_comparisons_translate(case_kind, tmp_path):
    """Large value lists and very wide selectors compile as shallow checks."""
    if case_kind == "wide_words":
        width = 32768
        values = "32768'd0"
    else:
        width = 128 if case_kind == "wide_values" else 16
        values = ", ".join(f"{width}'d{i}" for i in range(512))
    source = (
        f"module wide(input wire [{width - 1}:0] sel, output reg y); "
        f"always @* begin case (sel) {values}: y = 1'b1; "
        "default: y = 1'b0; endcase end endmodule"
    )
    _translate(_parse(source), tmp_path)


def test_512_edge_process_translates(tmp_path):
    """One process with many clock edges must not emit a deep OR tree."""
    count = 512
    module = Module(
        "many_edges",
        ports=[*(Port(f"e{i}", PortDirection.INPUT) for i in range(count)), Port("q", PortDirection.OUTPUT)],
        variables=[Variable("q", VariableKind.REG)],
    )
    module.always_blocks = [
        AlwaysBlock(
            NonblockingAssign(Identifier("q"), Literal(1, width=1)),
            sensitivity_list=[SensitivityEdge("posedge", Identifier(f"e{i}")) for i in range(count)],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        )
    ]
    _translate(module, tmp_path)


def test_large_case_dispatch_matches_reference():
    """The bounded narrow checks and wide word matcher preserve case results."""
    values = ", ".join(f"16'd{i}" for i in range(8))
    source = f"""
        module case_functional(
            input wire [15:0] narrow_sel, input wire [127:0] wide_sel,
            output reg [1:0] narrow_hit, output reg [1:0] wide_hit,
            output reg wildcard_hit
        );
            always @* begin
                case (narrow_sel)
                    {values}: narrow_hit = 2'd1;
                    16'd9: narrow_hit = 2'd2;
                    default: narrow_hit = 2'd0;
                endcase
            end
            always @* begin
                case (wide_sel)
                    128'd7, 128'd8: wide_hit = 2'd1;
                    128'd9: wide_hit = 2'd2;
                    default: wide_hit = 2'd0;
                endcase
            end
            always @* begin
                casex (wide_sel)
                    128'hx7: wildcard_hit = 1'b1;
                    default: wildcard_hit = 1'b0;
                endcase
            end
        endmodule
    """
    module = _parse(source)
    samples = [
        (Value(7, width=16), Value(7, width=128), (1, 1, 1)),
        (Value(9, width=16), Value(9, width=128), (2, 2, 0)),
        (Value(10, width=16), Value(10, width=128), (0, 0, 0)),
        (Value(7, width=16), Value(7, width=128, mask=1 << 100), (1, 0, 1)),
    ]
    for engine in ("reference", "compiled"):
        sim = Simulator(module, engine=engine)
        for narrow, wide, expected in samples:
            sim.drive("narrow_sel", narrow)
            sim.drive("wide_sel", wide)
            sim.run(max_time=0)
            actual = (
                int(sim.read("narrow_hit")),
                int(sim.read("wide_hit")),
                int(sim.read("wildcard_hit")),
            )
            assert actual == expected, (engine, narrow, wide, actual)
