"""Targeted simulator tests for constructs found in the DarkRISCV design.

Each test isolates a single Verilog construct or pattern observed in the
DarkRISCV SoC files and verifies it across all three simulation engines
(reference, VM, compiled).  The intent is to find and fix simulator bugs
in isolation before attempting the full-SoC simulation.

Constructs covered (mapped to DarkRISCV source):
  1. $display with format strings          (darkram.v, darksocv.v)
  2. $write system task                    (darkuart.v)
  3. Reduction operators in simulation     (darkuart.v, darkio.v)
  4. Reg initial values (reg X = val)      (darksimv.v, darkpll.v)
  5. Nested ternary mux                    (darkriscv.v decode logic)
  6. Concatenation in continuous assigns   (darkuart.v, darkriscv.v)
  7. casex in full simulation              (darkio.v, darkuart.v)
  8. Localparam in simulation              (various)
  9. Countdown register                    (darkpll.v IRES pattern)
 10. Initial block with for loop           (darkram.v memory init)
 11. While-loop clock generator            (darksimv.v)
 12. Multi-level hierarchy reset chain     (darkpll->darksocv pattern)
 13. $signed() + arithmetic right shift    (darkriscv.v SRA instruction)
 14. Case equality === / !==               (darkriscv.v invalid trap)
 15. $readmemh memory init                 (darkram.v firmware load)
 16. $finish simulation control            (darksimv.v)
 17. Variable-amount shift operators       (darkriscv.v ALU)
 18. negedge sensitivity                   (darksocv.v reset logic)
 19. Integer variables in always blocks    (darksimv.v perf counters)
 20. $dumpfile / $dumpvars VCD output      (darksimv.v)
 21. Signed comparison operators           (darkriscv.v BLT/BGE)
 22. $fflush system task                   (darkuart.v console flush)
 23. Computed real #delay                  (darksimv.v clock gen)
 24. inout ports with z-values             (bidirectional bus)
 25. Memory read-modify-write byte enable  (darkram.v byte writes)
"""

import shutil

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    FunctionCall,
    Identifier,
    Literal,
    Range,
    RangeSelect,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.instances import Instance, PortConnection
from veriforge.model.nets import Net, NetKind
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    ForLoop,
    NonblockingAssign,
    SensitivityEdge,
    SeqBlock,
    SystemTaskCall,
    WhileLoop,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _w(n: int) -> Range:
    """Create a width range [n-1:0]."""
    return Range(Literal(n - 1), Literal(0))


def _engines():
    """Return list of available engine names."""
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        try:
            import Cython  # noqa: F401, PLC0415

            engines.append("compiled")
        except ImportError:
            pass
    return engines


ENGINES = _engines()


# =====================================================================
# 1. $display with format strings
# =====================================================================
# Pattern from darkram.v:
#   $display("dpram: unified BRAM w/ %0dx32-bit", 2**`MLEN/4);
# The reference engine's _format_display must handle StringLiteral
# as a format string, not evaluate it to a numeric Value.


class TestDisplayFormatStrings:
    """$display with Verilog format strings (%d, %x, %b, %0d, etc.)."""

    def _make_display_module(self, fmt_str: str, *args) -> Module:
        """Build a module with: initial $display(fmt_str, args...);"""
        display_args = [StringLiteral(fmt_str)]
        display_args.extend(args)
        mod = Module(
            "display_test",
            variables=[Variable("dummy", VariableKind.REG, width=_w(8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", display_args),
            ),
        ]
        return mod

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_plain_string(self, engine):
        """$display("hello world") should output 'hello world'."""
        mod = self._make_display_module("hello world")
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "hello world"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_decimal(self, engine):
        """$display("val=%d", sig) with sig=42 -> 'val=42'."""
        mod = Module(
            "display_test",
            variables=[Variable("val", VariableKind.REG, width=_w(8), initial_value=Literal(42, width=8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("val=%d"), Identifier("val")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "val=42"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_hex(self, engine):
        """$display("addr=%x", sig) with sig=0xAB -> 'addr=ab'."""
        mod = Module(
            "display_test",
            variables=[Variable("addr", VariableKind.REG, width=_w(8), initial_value=Literal(0xAB, width=8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("addr=%x"), Identifier("addr")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "addr=ab"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_binary(self, engine):
        """$display("bits=%b", sig) with sig=5 -> 'bits=101'."""
        mod = Module(
            "display_test",
            variables=[Variable("bits", VariableKind.REG, width=_w(8), initial_value=Literal(5, width=8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("bits=%b"), Identifier("bits")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "bits=101"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_zero_pad(self, engine):
        """$display("%0d items", val) -- %0d suppresses leading spaces."""
        mod = Module(
            "display_test",
            variables=[Variable("count", VariableKind.REG, width=_w(16), initial_value=Literal(2048, width=16))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("%0dx32-bit"), Identifier("count")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "2048x32-bit"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_multiple_args(self, engine):
        """$display("a=%d b=%x", a, b) with multiple format specifiers."""
        mod = Module(
            "display_test",
            variables=[
                Variable("a", VariableKind.REG, width=_w(8), initial_value=Literal(10, width=8)),
                Variable("b", VariableKind.REG, width=_w(8), initial_value=Literal(255, width=8)),
            ],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("a=%d b=%x"), Identifier("a"), Identifier("b")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "a=10 b=ff"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_width_hex(self, engine):
        """$display("%08x", val) with val=0xAB -> '000000ab' (zero-padded 8-wide hex)."""
        mod = Module(
            "display_test",
            variables=[Variable("val", VariableKind.REG, width=_w(32), initial_value=Literal(0xAB, width=32))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("addr=0x%08x"), Identifier("val")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "addr=0x000000ab"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_width_decimal(self, engine):
        """$display("%4d", val) with val=42 -> '  42' (space-padded 4-wide decimal)."""
        mod = Module(
            "display_test",
            variables=[Variable("val", VariableKind.REG, width=_w(8), initial_value=Literal(42, width=8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$display", [StringLiteral("val=%4d"), Identifier("val")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "val=  42"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_display_format_mixed_widths(self, engine):
        """$display("0x%08x: 0x%08x", a, b) — PicoRV32-style format."""
        mod = Module(
            "display_test",
            variables=[
                Variable("a", VariableKind.REG, width=_w(32), initial_value=Literal(0x1000, width=32)),
                Variable("b", VariableKind.REG, width=_w(32), initial_value=Literal(0x3FC00093, width=32)),
            ],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall(
                    "$display",
                    [StringLiteral("ifetch 0x%08x: 0x%08x"), Identifier("a"), Identifier("b")],
                ),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "ifetch 0x00001000: 0x3fc00093"


# =====================================================================
# 2. $write system task
# =====================================================================
# Pattern from darkuart.v:
#   $write("%c", DATAI[15:8]);


class TestWriteSystemTask:
    """$write works like $display in our simulator (no newline distinction)."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_write_basic(self, engine):
        """$write("hello") produces output."""
        mod = Module(
            "write_test",
            variables=[Variable("dummy", VariableKind.REG, width=_w(8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$write", [StringLiteral("hello")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "hello"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_write_format_char(self, engine):
        """$write("%c", val) outputs a character for ASCII code."""
        mod = Module(
            "write_test",
            variables=[Variable("ch", VariableKind.REG, width=_w(8), initial_value=Literal(65, width=8))],
        )
        mod.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$write", [StringLiteral("%c"), Identifier("ch")]),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert len(sim.display_output) == 1
        assert sim.display_output[0] == "A"


# =====================================================================
# 3. Reduction operators in simulation
# =====================================================================
# Pattern from darkio.v:  assign IRQ = |BOARD_IRQ;
# Pattern from darksimv.v:  .XRES(|RES)
# These exist in test_evaluator but not in full-simulation tests.


class TestReductionOperators:
    """Reduction operators (&, |, ^, ~&, ~|, ~^) in continuous assigns."""

    def _make_reduction_module(self, op: str) -> Module:
        """module m(input [7:0] a, output y); assign y = <op>a; endmodule"""
        return Module(
            "reduction_test",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("y", PortDirection.OUTPUT),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(8)),
                Net("y", NetKind.WIRE),
            ],
            continuous_assigns=[
                ContinuousAssign(Identifier("y"), UnaryOp(op, Identifier("a"))),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_or_all_zero(self, engine):
        """|8'h00 == 0."""
        mod = self._make_reduction_module("|")
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0x00, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_or_nonzero(self, engine):
        """|8'h01 == 1."""
        mod = self._make_reduction_module("|")
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0x01, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_and_all_ones(self, engine):
        """&8'hFF == 1."""
        mod = self._make_reduction_module("&")
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0xFF, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_and_not_all_ones(self, engine):
        """&8'hFE == 0."""
        mod = self._make_reduction_module("&")
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0xFE, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_xor(self, engine):
        """^8'hFF == 0 (even number of 1s after XOR)."""
        mod = self._make_reduction_module("^")
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0xFF, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_xor_odd(self, engine):
        """^8'h07 == 1 (three 1-bits -> odd)."""
        mod = self._make_reduction_module("^")
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0x07, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_or_on_1bit(self, engine):
        """|1'b1 == 1 (darksimv.v pattern: .XRES(|RES) where RES is 1-bit)."""
        mod = Module(
            "reduction_1bit",
            ports=[
                Port("a", PortDirection.INPUT),
                Port("y", PortDirection.OUTPUT),
            ],
            nets=[
                Net("a", NetKind.WIRE),
                Net("y", NetKind.WIRE),
            ],
            continuous_assigns=[
                ContinuousAssign(Identifier("y"), UnaryOp("|", Identifier("a"))),
            ],
        )
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(1, width=1))
        sim.run(max_time=0)
        assert sim.read("y") == 1


# =====================================================================
# 4. Reg initial values
# =====================================================================
# Pattern from darksimv.v:  reg CLK = 0;  reg RES = 1;
# Pattern from darkpll.v:   reg [7:0] IRES = -1;  (i.e. 8'hFF)


class TestRegInitialValues:
    """Registers with initial values should start at those values."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_init_zero(self, engine):
        """reg CLK = 0 -> starts at 0."""
        mod = Module(
            "init_test",
            variables=[Variable("CLK", VariableKind.REG, initial_value=Literal(0))],
        )
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert sim.read("CLK") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_init_one(self, engine):
        """reg RES = 1 -> starts at 1."""
        mod = Module(
            "init_test",
            variables=[Variable("RES", VariableKind.REG, initial_value=Literal(1))],
        )
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert sim.read("RES") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_init_minus_one_8bit(self, engine):
        """reg [7:0] IRES = -1 -> starts at 8'hFF (255)."""
        mod = Module(
            "init_test",
            variables=[
                Variable(
                    "IRES",
                    VariableKind.REG,
                    width=_w(8),
                    initial_value=UnaryOp("-", Literal(1)),
                ),
            ],
        )
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert sim.read("IRES") == Value(0xFF, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reg_init_no_value_is_x(self, engine):
        """reg with no initial value starts as x."""
        mod = Module(
            "init_test",
            variables=[Variable("data", VariableKind.REG, width=_w(8))],
        )
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        val = sim.read("data")
        assert not val.is_defined


# =====================================================================
# 5. Nested ternary mux
# =====================================================================
# Pattern from darkriscv.v instruction decode:
#   assign result = sel[1] ? (sel[0] ? d : c) : (sel[0] ? b : a);
# This creates a 4-to-1 mux from nested ternary operators.


class TestNestedTernaryMux:
    """Nested ternary operators forming a mux tree."""

    def _make_mux4(self) -> Module:
        """4-to-1 mux using nested ternary:
        assign y = sel[1] ? (sel[0] ? d : c) : (sel[0] ? b : a);
        """
        sel1 = BitSelect(Identifier("sel"), Literal(1))
        sel0 = BitSelect(Identifier("sel"), Literal(0))
        return Module(
            "mux4",
            ports=[
                Port("sel", PortDirection.INPUT, width=_w(2)),
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("b", PortDirection.INPUT, width=_w(8)),
                Port("c", PortDirection.INPUT, width=_w(8)),
                Port("d", PortDirection.INPUT, width=_w(8)),
                Port("y", PortDirection.OUTPUT, width=_w(8)),
            ],
            nets=[
                Net("sel", NetKind.WIRE, width=_w(2)),
                Net("a", NetKind.WIRE, width=_w(8)),
                Net("b", NetKind.WIRE, width=_w(8)),
                Net("c", NetKind.WIRE, width=_w(8)),
                Net("d", NetKind.WIRE, width=_w(8)),
                Net("y", NetKind.WIRE, width=_w(8)),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    TernaryOp(
                        sel1,
                        TernaryOp(sel0, Identifier("d"), Identifier("c")),
                        TernaryOp(sel0, Identifier("b"), Identifier("a")),
                    ),
                ),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    @pytest.mark.parametrize(
        "sel,expected_src",
        [(0, "a"), (1, "b"), (2, "c"), (3, "d")],
        ids=["sel=0->a", "sel=1->b", "sel=2->c", "sel=3->d"],
    )
    def test_mux4_selects(self, engine, sel, expected_src):
        """4-to-1 mux selects correct input for each sel value."""
        mod = self._make_mux4()
        sim = Simulator(mod, engine=engine)
        sim.drive("sel", Value(sel, width=2))
        values = {"a": 0x10, "b": 0x20, "c": 0x30, "d": 0x40}
        for name, val in values.items():
            sim.drive(name, Value(val, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == Value(values[expected_src], width=8)


# =====================================================================
# 6. Concatenation in continuous assigns
# =====================================================================
# Pattern from darkuart.v:
#   wire [7:0] UART_STATE = { 6'd0, UART_RREQ!=UART_RACK, UART_XREQ!=UART_XACK };


class TestConcatContinuousAssign:
    """Concatenation in continuous assign expressions."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_zero_pad(self, engine):
        """assign y = {6'd0, a, b} packs bits correctly."""
        mod = Module(
            "concat_test",
            ports=[
                Port("a", PortDirection.INPUT),
                Port("b", PortDirection.INPUT),
                Port("y", PortDirection.OUTPUT, width=_w(8)),
            ],
            nets=[
                Net("a", NetKind.WIRE),
                Net("b", NetKind.WIRE),
                Net("y", NetKind.WIRE, width=_w(8)),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    Concatenation([Literal(0, width=6), Identifier("a"), Identifier("b")]),
                ),
            ],
        )
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(1, width=1))
        sim.drive("b", Value(0, width=1))
        sim.run(max_time=0)
        # {6'b000000, 1'b1, 1'b0} = 8'b00000010 = 2
        assert sim.read("y") == Value(2, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_wide_fields(self, engine):
        """assign y = {a, b} with 8-bit fields -> 16-bit result."""
        mod = Module(
            "concat_wide",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("b", PortDirection.INPUT, width=_w(8)),
                Port("y", PortDirection.OUTPUT, width=_w(16)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(8)),
                Net("b", NetKind.WIRE, width=_w(8)),
                Net("y", NetKind.WIRE, width=_w(16)),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    Concatenation([Identifier("a"), Identifier("b")]),
                ),
            ],
        )
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(0xAB, width=8))
        sim.drive("b", Value(0xCD, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == Value(0xABCD, width=16)


# =====================================================================
# 7. casex in full simulation
# =====================================================================
# Pattern from darkio.v:
#   casex(XADDR[4:0])
#     5'b1xxxx: LED_logic;
#     5'b01xxx: OPORT_logic;
#     ...
#   endcase


class TestCasexSimulation:
    """casex statement in a full always-block simulation."""

    def _make_casex_decoder(self) -> Module:
        """Address decoder using casex:
        always @(*) begin
            casex(addr[4:0])
                5'b1xxxx: out = 8'd1;   // top bit set
                5'b01xxx: out = 8'd2;   // bit 3 set
                5'b001xx: out = 8'd3;   // bit 2 set
                default:  out = 8'd0;
            endcase
        end
        """
        # Use original_text so Value.from_verilog preserves per-bit x masks
        lit_1xxxx = Literal("1xxxx", width=5, base="b", is_x=True, original_text="5'b1xxxx")
        lit_01xxx = Literal("01xxx", width=5, base="b", is_x=True, original_text="5'b01xxx")
        lit_001xx = Literal("001xx", width=5, base="b", is_x=True, original_text="5'b001xx")

        mod = Module(
            "casex_decoder",
            ports=[
                Port("addr", PortDirection.INPUT, width=_w(5)),
                Port("out", PortDirection.OUTPUT, width=_w(8)),
            ],
            nets=[Net("addr", NetKind.WIRE, width=_w(5))],
            variables=[Variable("out", VariableKind.REG, width=_w(8))],
        )
        mod.always_blocks = [
            AlwaysBlock(
                CaseStatement(
                    "casex",
                    Identifier("addr"),
                    [
                        CaseItem(
                            [lit_1xxxx],
                            BlockingAssign(Identifier("out"), Literal(1, width=8)),
                        ),
                        CaseItem(
                            [lit_01xxx],
                            BlockingAssign(Identifier("out"), Literal(2, width=8)),
                        ),
                        CaseItem(
                            [lit_001xx],
                            BlockingAssign(Identifier("out"), Literal(3, width=8)),
                        ),
                        CaseItem(
                            None,
                            BlockingAssign(Identifier("out"), Literal(0, width=8)),
                            is_default=True,
                        ),
                    ],
                ),
                sensitivity_type=SensitivityType.COMBINATIONAL,
            ),
        ]
        return mod

    @pytest.mark.parametrize(
        "engine",
        ENGINES,
        ids=ENGINES,
    )
    @pytest.mark.parametrize(
        "addr,expected",
        [
            (0b10000, 1),  # top bit set -> match first
            (0b11111, 1),  # top bit set (other bits ignored for casex)
            (0b01000, 2),  # bit 3 pattern
            (0b01111, 2),  # bit 3 pattern with don't cares
            (0b00100, 3),  # bit 2 pattern
            (0b00000, 0),  # default
            (0b00011, 0),  # default
        ],
        ids=["top-bit", "top-all-ones", "bit3", "bit3-dontcare", "bit2", "default-zero", "default-low"],
    )
    def test_casex_decode(self, engine, addr, expected):
        mod = self._make_casex_decoder()
        sim = Simulator(mod, engine=engine)
        sim.drive("addr", Value(addr, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(expected, width=8)


# =====================================================================
# 8. Localparam in simulation
# =====================================================================
# Pattern: localparam WIDTH = 8; used in expressions.


class TestLocalparamSimulation:
    """Localparam (constant) used in expressions evaluates correctly."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_localparam_in_assign(self, engine):
        """assign y = a + WIDTH where localparam WIDTH = 5."""
        mod = Module(
            "localparam_test",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("y", PortDirection.OUTPUT, width=_w(8)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(8)),
                Net("y", NetKind.WIRE, width=_w(8)),
            ],
            parameters=[
                Parameter("WIDTH", default_value=Literal(5, width=32), is_local=True),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("y"),
                    BinaryOp("+", Identifier("a"), Identifier("WIDTH")),
                ),
            ],
        )
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(10, width=8))
        sim.run(max_time=0)
        assert sim.read("y") == Value(15, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_parameter_in_comparison(self, engine):
        """always @(*) out = (a == MAGIC) ? 1 : 0; with parameter MAGIC = 42."""
        mod = Module(
            "param_cmp_test",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("out", PortDirection.OUTPUT),
            ],
            nets=[Net("a", NetKind.WIRE, width=_w(8))],
            variables=[Variable("out", VariableKind.REG)],
            parameters=[
                Parameter("MAGIC", default_value=Literal(42, width=8), is_local=True),
            ],
        )
        mod.always_blocks = [
            AlwaysBlock(
                BlockingAssign(
                    Identifier("out"),
                    TernaryOp(
                        BinaryOp("==", Identifier("a"), Identifier("MAGIC")),
                        Literal(1, width=1),
                        Literal(0, width=1),
                    ),
                ),
                sensitivity_type=SensitivityType.COMBINATIONAL,
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.drive("a", Value(42, width=8))
        sim.run(max_time=0)
        assert sim.read("out") == 1

        sim2 = Simulator(mod, engine=engine)
        sim2.drive("a", Value(99, width=8))
        sim2.run(max_time=0)
        assert sim2.read("out") == 0


# =====================================================================
# 9. Countdown register
# =====================================================================
# Pattern from darkpll.v:
#   reg [7:0] IRES = -1;
#   always @(posedge XCLK)
#       IRES <= XRES==1 ? -1 : IRES[7] ? IRES-1 : 0;
# IRES starts at 0xFF, counts down to 0x80, then goes to 0.


class TestCountdownRegister:
    """Countdown register pattern from darkpll.v (IRES)."""

    def _make_countdown(self) -> Module:
        """Build the IRES countdown logic.

        reg [7:0] cnt = 8'hFF;
        always @(posedge clk)
            cnt <= rst ? 8'hFF : cnt[7] ? cnt - 1 : 0;
        """
        mod = Module(
            "countdown",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("rst", PortDirection.INPUT),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("rst", NetKind.WIRE),
            ],
            variables=[
                Variable(
                    "cnt",
                    VariableKind.REG,
                    width=_w(8),
                    initial_value=UnaryOp("-", Literal(1)),
                ),
            ],
        )
        mod.always_blocks = [
            AlwaysBlock(
                NonblockingAssign(
                    Identifier("cnt"),
                    TernaryOp(
                        Identifier("rst"),
                        Literal(0xFF, width=8),
                        TernaryOp(
                            BitSelect(Identifier("cnt"), Literal(7)),
                            BinaryOp("-", Identifier("cnt"), Literal(1, width=8)),
                            Literal(0, width=8),
                        ),
                    ),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        return mod

    @pytest.mark.parametrize("engine", ENGINES)
    def test_countdown_initial_value(self, engine):
        """cnt starts at 0xFF."""
        mod = self._make_countdown()
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        assert sim.read("cnt") == Value(0xFF, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_countdown_decrements(self, engine):
        """After a few clock edges with rst=0, cnt decrements."""
        mod = self._make_countdown()
        sim = Simulator(mod, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("rst", Value(0, width=1))
        # Clock posedges at t=0, t=10, t=20, ...
        # Run past 2 posedges (t=0 and t=10): 0xFF -> 0xFE -> 0xFD
        sim.run(max_time=15)
        cnt_val = sim.read("cnt")
        # After 2 posedges, cnt should be 0xFD
        assert cnt_val == Value(0xFD, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_countdown_reaches_zero(self, engine):
        """After 128 clock cycles, cnt should reach 0 (0x80->0 transition)."""
        mod = self._make_countdown()
        sim = Simulator(mod, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("rst", Value(0, width=1))
        # 128 posedges: 0xFF->0xFE->...->0x80->0x00
        # Each period is 10 time units, 128 cycles = 1280 time units
        sim.run(max_time=1285)
        cnt_val = sim.read("cnt")
        assert cnt_val == Value(0, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_countdown_stays_zero(self, engine):
        """Once cnt reaches 0, it stays at 0 (cnt[7]=0 -> 0 branch)."""
        mod = self._make_countdown()
        sim = Simulator(mod, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("rst", Value(0, width=1))
        # Run well past the countdown
        sim.run(max_time=1500)
        assert sim.read("cnt") == Value(0, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_countdown_reset_reloads(self, engine):
        """Asserting rst reloads cnt to 0xFF."""
        mod = self._make_countdown()
        sim = Simulator(mod, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("rst", Value(0, width=1))
        sim.run(max_time=50)  # count down a few
        sim.drive("rst", Value(1, width=1))
        sim.run(max_time=65)  # 1 more posedge with rst=1
        assert sim.read("cnt") == Value(0xFF, width=8)


# =====================================================================
# 10. Initial block with for loop
# =====================================================================
# Pattern from darkram.v:
#   integer i;
#   initial begin
#       for(i=0; i!=4; i=i+1) MEM[i] = 32'd0;
#   end


class TestInitialForLoop:
    """Initial block containing a for loop to initialize memory."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_for_loop_inits_memory(self, engine):
        """for(i=0; i!=4; i=i+1) MEM[i] = 0; zeros out memory."""
        mod = Module(
            "mem_init",
            variables=[
                Variable("MEM", VariableKind.REG, width=_w(8), dimensions=[Range(Literal(3), Literal(0))]),
                Variable("i", VariableKind.INTEGER),
            ],
        )
        mod.initial_blocks = [
            InitialBlock(
                ForLoop(
                    init=BlockingAssign(Identifier("i"), Literal(0)),
                    condition=BinaryOp("!=", Identifier("i"), Literal(4)),
                    update=BlockingAssign(
                        Identifier("i"),
                        BinaryOp("+", Identifier("i"), Literal(1)),
                    ),
                    body=BlockingAssign(
                        BitSelect(Identifier("MEM"), Identifier("i")),
                        Literal(0, width=8),
                    ),
                ),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        for addr in range(4):
            val = sim.read(f"MEM[{addr}]")
            assert val == Value(0, width=8), f"MEM[{addr}] = {val}, expected 0"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_for_loop_inits_with_pattern(self, engine):
        """for(i=0; i!=4; i=i+1) MEM[i] = i; stores index values."""
        mod = Module(
            "mem_pattern",
            variables=[
                Variable("MEM", VariableKind.REG, width=_w(8), dimensions=[Range(Literal(3), Literal(0))]),
                Variable("i", VariableKind.INTEGER),
            ],
        )
        mod.initial_blocks = [
            InitialBlock(
                ForLoop(
                    init=BlockingAssign(Identifier("i"), Literal(0)),
                    condition=BinaryOp("!=", Identifier("i"), Literal(4)),
                    update=BlockingAssign(
                        Identifier("i"),
                        BinaryOp("+", Identifier("i"), Literal(1)),
                    ),
                    body=BlockingAssign(
                        BitSelect(Identifier("MEM"), Identifier("i")),
                        Identifier("i"),
                    ),
                ),
            ),
        ]
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=0)
        for addr in range(4):
            val = sim.read(f"MEM[{addr}]")
            assert val == Value(addr, width=8), f"MEM[{addr}] = {val}, expected {addr}"


# =====================================================================
# 11. While-loop clock generator
# =====================================================================
# Pattern from darksimv.v:
#   initial while(1) #5 CLK = !CLK;


class TestWhileLoopClock:
    """Clock generation via initial while(1) #delay CLK = !CLK."""

    def _make_while_clock(self, period_half: int = 5) -> Module:
        """initial while(1) #<half> CLK = !CLK;"""
        mod = Module(
            "clk_gen",
            variables=[Variable("CLK", VariableKind.REG, initial_value=Literal(0))],
        )
        mod.initial_blocks = [
            InitialBlock(
                WhileLoop(
                    condition=Literal(1),
                    body=SeqBlock(
                        [
                            DelayControl(Literal(period_half)),
                            BlockingAssign(Identifier("CLK"), UnaryOp("!", Identifier("CLK"))),
                        ]
                    ),
                ),
            ),
        ]
        return mod

    @pytest.mark.parametrize("engine", ENGINES)
    def test_clock_toggles(self, engine):
        """CLK should toggle every 5 time units."""
        mod = self._make_while_clock(5)
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=25)
        # At t=25: CLK started 0, toggled at t=5,10,15,20,25
        # t=5->1, t=10->0, t=15->1, t=20->0, t=25->1
        assert sim.read("CLK") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_clock_period(self, engine):
        """After full period (10 units), CLK returns to starting value."""
        mod = self._make_while_clock(5)
        sim = Simulator(mod, engine=engine)
        sim.run(max_time=10)
        # t=5->1, t=10->0 (back to start)
        assert sim.read("CLK") == 0


# =====================================================================
# 12. Multi-level hierarchy with reset chain
# =====================================================================
# Pattern from DarkRISCV: darkpll produces RES which propagates through
# darksocv to other modules via port connections and continuous assigns.


class TestResetChainHierarchy:
    """Multi-level hierarchy propagating a reset signal."""

    def _make_reset_chain(self) -> tuple[Module, Design]:
        """Build a 2-level hierarchy with reset propagation.

        module pll(input clk, input rst_in, output rst_out);
            reg [3:0] cnt = 4'hF;
            always @(posedge clk)
                cnt <= rst_in ? 4'hF : cnt[3] ? cnt - 1 : 0;
            assign rst_out = cnt[3];
        endmodule

        module top(input clk, input ext_rst, output internal_rst);
            pll u_pll (.clk(clk), .rst_in(ext_rst), .rst_out(internal_rst));
        endmodule
        """
        pll = Module(
            "pll",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("rst_in", PortDirection.INPUT),
                Port("rst_out", PortDirection.OUTPUT),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("rst_in", NetKind.WIRE),
                Net("rst_out", NetKind.WIRE),
            ],
            variables=[
                Variable(
                    "cnt",
                    VariableKind.REG,
                    width=_w(4),
                    initial_value=Literal(0xF, width=4),
                ),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("rst_out"),
                    BitSelect(Identifier("cnt"), Literal(3)),
                ),
            ],
        )
        pll.always_blocks = [
            AlwaysBlock(
                NonblockingAssign(
                    Identifier("cnt"),
                    TernaryOp(
                        Identifier("rst_in"),
                        Literal(0xF, width=4),
                        TernaryOp(
                            BitSelect(Identifier("cnt"), Literal(3)),
                            BinaryOp("-", Identifier("cnt"), Literal(1, width=4)),
                            Literal(0, width=4),
                        ),
                    ),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]

        top = Module(
            "top",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("ext_rst", PortDirection.INPUT),
                Port("internal_rst", PortDirection.OUTPUT),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("ext_rst", NetKind.WIRE),
                Net("internal_rst", NetKind.WIRE),
            ],
            instances=[
                Instance(
                    "pll",
                    "u_pll",
                    port_connections=[
                        PortConnection(port_name="clk", expression=Identifier("clk"), is_named=True),
                        PortConnection(port_name="rst_in", expression=Identifier("ext_rst"), is_named=True),
                        PortConnection(port_name="rst_out", expression=Identifier("internal_rst"), is_named=True),
                    ],
                ),
            ],
        )

        design = Design(modules=[top, pll])
        link_instances(design)
        resolve_port_connections(design)
        return top, design

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reset_starts_high(self, engine):
        """internal_rst should start high (cnt starts at 0xF, cnt[3]=1)."""
        top, design = self._make_reset_chain()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("ext_rst", Value(0, width=1))
        sim.run(max_time=0)
        assert sim.read("internal_rst") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reset_deasserts_after_countdown(self, engine):
        """internal_rst should go low after cnt counts from 0xF to 0x7."""
        top, design = self._make_reset_chain()
        sim = Simulator(top, engine=engine, design=design)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("ext_rst", Value(0, width=1))
        # cnt: F->E->D->C->B->A->9->8->0 (8 posedges: cnt[3] goes 0 when cnt reaches 7->0)
        # Need 8 clock edges: 8 * 10 = 80 time units
        sim.run(max_time=85)
        assert sim.read("internal_rst") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reset_chain_hierarchy_signal_access(self, engine):
        """Can read internal pll counter via hierarchical name."""
        top, design = self._make_reset_chain()
        sim = Simulator(top, engine=engine, design=design)
        sim.drive("ext_rst", Value(0, width=1))
        sim.run(max_time=0)
        cnt = sim.read("u_pll.cnt")
        assert cnt == Value(0xF, width=4)


# =====================================================================
# 13. $signed() + arithmetic right shift (>>>)
# =====================================================================
# Pattern from darkriscv.v — SRA instruction:
#   assign sra = $signed(a) >>> b[4:0];
# Verifies sign-extension during right shift.


class TestSignedArithShift:
    """$signed() and >>> arithmetic right shift."""

    def _make_module(self) -> Module:
        """Build module with: assign out = $signed(a) >>> shamt;

        module sra_test(input [31:0] a, input [4:0] shamt, output [31:0] out);
            assign out = $signed(a) >>> shamt;
        endmodule
        """
        return Module(
            "sra_test",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(32)),
                Port("shamt", PortDirection.INPUT, width=_w(5)),
                Port("out", PortDirection.OUTPUT, width=_w(32)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(32)),
                Net("shamt", NetKind.WIRE, width=_w(5)),
                Net("out", NetKind.WIRE, width=_w(32)),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("out"),
                    BinaryOp(
                        ">>>",
                        FunctionCall("$signed", [Identifier("a")], is_system=True),
                        Identifier("shamt"),
                    ),
                ),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_positive_number_shift(self, engine):
        """Positive number: $signed(0x40) >>> 2 = 0x10."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0x40, width=32))
        sim.drive("shamt", Value(2, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x10, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_negative_number_shift(self, engine):
        """Negative: $signed(0x80000000) >>> 4 = 0xF8000000 (sign-extended)."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0x80000000, width=32))
        sim.drive("shamt", Value(4, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0xF8000000, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_negative_shift_one(self, engine):
        """$signed(0xFFFFFFFF) >>> 1 = 0xFFFFFFFF (all-ones stays all-ones)."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xFFFFFFFF, width=32))
        sim.drive("shamt", Value(1, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0xFFFFFFFF, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_zero_shift_amount(self, engine):
        """$signed(0xDEADBEEF) >>> 0 = 0xDEADBEEF (no shift)."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xDEADBEEF, width=32))
        sim.drive("shamt", Value(0, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0xDEADBEEF, width=32)


# =====================================================================
# 14. Case equality === / !== operators
# =====================================================================
# Pattern from darkriscv.v — invalid instruction detection:
#   XLUI === 1 (with potential x values)
# Verifies identity comparison that considers x/z bits.


class TestCaseEquality:
    """=== and !== case equality operators."""

    def _make_module(self, op: str) -> Module:
        """Build module: assign out = (a <op> b);

        module ceq_test(input [7:0] a, b, output out);
            assign out = (a === b);   // or !==
        endmodule
        """
        return Module(
            f"ceq_test_{op.replace('=', 'e').replace('!', 'n')}",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("b", PortDirection.INPUT, width=_w(8)),
                Port("out", PortDirection.OUTPUT),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(8)),
                Net("b", NetKind.WIRE, width=_w(8)),
                Net("out", NetKind.WIRE),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("out"),
                    BinaryOp(op, Identifier("a"), Identifier("b")),
                ),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_case_eq_matching_values(self, engine):
        """a === b when both are 0xAB => 1."""
        m = self._make_module("===")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xAB, width=8))
        sim.drive("b", Value(0xAB, width=8))
        sim.run(max_time=0)
        assert sim.read("out") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_case_eq_different_values(self, engine):
        """a === b when a=0xAB, b=0xCD => 0."""
        m = self._make_module("===")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xAB, width=8))
        sim.drive("b", Value(0xCD, width=8))
        sim.run(max_time=0)
        assert sim.read("out") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_case_ne_different(self, engine):
        """a !== b when a != b => 1."""
        m = self._make_module("!==")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xAB, width=8))
        sim.drive("b", Value(0xCD, width=8))
        sim.run(max_time=0)
        assert sim.read("out") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_case_ne_same(self, engine):
        """a !== b when a == b => 0."""
        m = self._make_module("!==")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0x42, width=8))
        sim.drive("b", Value(0x42, width=8))
        sim.run(max_time=0)
        assert sim.read("out") == 0


# =====================================================================
# 15. $readmemh memory initialization
# =====================================================================
# Pattern from darkram.v:
#   reg [31:0] MEM [0:2**MLEN/4-1];
#   initial $readmemh("darksocv.mem", MEM);
# Verifies memory loading from hex file.


class TestReadmemhInit:
    """$readmemh system task in initial block."""

    def _make_module(self, filename: str) -> Module:
        """Build module that loads memory with $readmemh.

        module readmem_test;
            reg [31:0] mem [0:3];
            reg [31:0] out;
            initial $readmemh("file.hex", mem);
            // out driven by testbench read of mem[N]
        endmodule
        """
        m = Module(
            "readmem_test",
            nets=[],
            variables=[
                Variable("mem", VariableKind.REG, width=_w(32), dimensions=[Range(Literal(0), Literal(3))]),
                Variable("out", VariableKind.REG, width=_w(32)),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                SystemTaskCall("$readmemh", [StringLiteral(filename), Identifier("mem")]),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_basic(self, engine, tmp_path):
        """$readmemh loads hex values into memory array."""
        hex_file = tmp_path / "test.hex"
        hex_file.write_text("DEADBEEF\n12345678\n00000000\nFFFFFFFF\n")
        m = self._make_module(str(hex_file))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("mem[0]") == Value(0xDEADBEEF, width=32)
        assert sim.read("mem[1]") == Value(0x12345678, width=32)
        assert sim.read("mem[2]") == Value(0x00000000, width=32)
        assert sim.read("mem[3]") == Value(0xFFFFFFFF, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_with_address(self, engine, tmp_path):
        """$readmemh handles @addr specifications."""
        hex_file = tmp_path / "test_addr.hex"
        hex_file.write_text("@2\nAAAAAAAA\nBBBBBBBB\n")
        m = self._make_module(str(hex_file))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("mem[2]") == Value(0xAAAAAAAA, width=32)
        assert sim.read("mem[3]") == Value(0xBBBBBBBB, width=32)


# =====================================================================
# 16. $finish simulation control
# =====================================================================
# Pattern from darksimv.v:
#   if(... && FINISH_O) $finish;
# Verifies that $finish terminates simulation early.


class TestFinishSimControl:
    """$finish system task terminates simulation."""

    def _make_module(self) -> Module:
        """Build module with $finish at t=55.

        module finish_test;
            reg [7:0] counter = 0;
            initial while(1) begin #10 counter = counter + 1; end
            initial #55 $finish;
        endmodule
        """
        m = Module(
            "finish_test",
            nets=[],
            variables=[
                Variable("counter", VariableKind.REG, width=_w(8), initial_value=Literal(0, width=8)),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                WhileLoop(
                    condition=Literal(1),
                    body=SeqBlock(
                        [
                            DelayControl(Literal(10)),
                            BlockingAssign(
                                Identifier("counter"),
                                BinaryOp("+", Identifier("counter"), Literal(1, width=8)),
                            ),
                        ]
                    ),
                ),
            ),
            InitialBlock(
                SeqBlock(
                    [
                        DelayControl(Literal(55)),
                        SystemTaskCall("$finish"),
                    ]
                ),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_finish_stops_simulation(self, engine):
        """$finish at t=55 should stop sim; counter should be 5 (increments at t=10,20,30,40,50)."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=200)  # Would run much longer without $finish
        assert sim.read("counter") == Value(5, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_finish_time(self, engine):
        """Simulation time should be 55 when $finish fires."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=200)
        assert sim.time == 55


# =====================================================================
# 17. Variable-amount shift operators
# =====================================================================
# Pattern from darkriscv.v ALU:
#   assign sll = a << b[4:0];
#   assign srl = a >> b[4:0];
# Verifies shifts with non-constant amounts.


class TestVariableShift:
    """Shift operators with variable shift amounts."""

    def _make_module(self, op: str) -> Module:
        """Build module: assign out = a <op> shamt;

        module shift_test(input [31:0] a, input [4:0] shamt, output [31:0] out);
            assign out = a << shamt;  // or >> or >>>
        endmodule
        """
        return Module(
            f"shift_{op.replace('>', 'r').replace('<', 'l')}",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(32)),
                Port("shamt", PortDirection.INPUT, width=_w(5)),
                Port("out", PortDirection.OUTPUT, width=_w(32)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(32)),
                Net("shamt", NetKind.WIRE, width=_w(5)),
                Net("out", NetKind.WIRE, width=_w(32)),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("out"),
                    BinaryOp(op, Identifier("a"), Identifier("shamt")),
                ),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_sll_by_variable(self, engine):
        """1 << 8 = 0x100."""
        m = self._make_module("<<")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(1, width=32))
        sim.drive("shamt", Value(8, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x100, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_sll_high_bits_truncate(self, engine):
        """0x80000001 << 1 = 0x00000002 (MSB falls off 32 bits)."""
        m = self._make_module("<<")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0x80000001, width=32))
        sim.drive("shamt", Value(1, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x00000002, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_srl_by_variable(self, engine):
        """0x100 >> 4 = 0x10."""
        m = self._make_module(">>")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0x100, width=32))
        sim.drive("shamt", Value(4, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x10, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_srl_zero_fill(self, engine):
        """0xFF000000 >> 8 = 0x00FF0000 (zero-filled)."""
        m = self._make_module(">>")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xFF000000, width=32))
        sim.drive("shamt", Value(8, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x00FF0000, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_shift_by_zero(self, engine):
        """Any value << 0 is unchanged."""
        m = self._make_module("<<")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xCAFEBABE, width=32))
        sim.drive("shamt", Value(0, width=5))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0xCAFEBABE, width=32)


# =====================================================================
# 18. negedge sensitivity
# =====================================================================
# Pattern from darksocv.v:
#   always @(negedge rst_n) ...
# Verifies that negedge triggers on falling edge.


class TestNegedgeSensitivity:
    """negedge-triggered always blocks."""

    def _make_module(self) -> Module:
        """Build module with negedge-triggered flip-flop and clock gen.

        module negedge_test;
            reg clk = 1;
            reg d = 0;
            reg q;
            always @(negedge clk)
                q <= d;
            // Clock gen: toggle every 5 time units -> negedges at t=5, t=15, ...
            initial while(1) #5 clk = !clk;
        endmodule
        """
        m = Module(
            "negedge_test",
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, initial_value=Literal(1)),
                Variable("d", VariableKind.REG, initial_value=Literal(0)),
                Variable("q", VariableKind.REG),
            ],
        )
        m.always_blocks = [
            AlwaysBlock(
                NonblockingAssign(Identifier("q"), Identifier("d")),
                sensitivity_list=[SensitivityEdge("negedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        m.initial_blocks = [
            InitialBlock(
                WhileLoop(
                    condition=Literal(1),
                    body=SeqBlock(
                        [
                            DelayControl(Literal(5)),
                            BlockingAssign(Identifier("clk"), UnaryOp("!", Identifier("clk"))),
                        ]
                    ),
                ),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_negedge_captures_on_falling(self, engine):
        """q should latch d=0 on first negedge at t=5."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        # clk starts 1, d starts 0. Negedge at t=5 -> q captures d=0
        sim.run(max_time=6)
        assert sim.read("q") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_posedge_does_not_trigger(self, engine):
        """After posedge at t=10, q should still hold the value from the last negedge."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        # negedge at t=5 (q=0), posedge at t=10 (no trigger)
        sim.run(max_time=11)
        assert sim.read("q") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_negedge_captures_updated_d(self, engine):
        """Module with d driven to 1 via initial block, captured on 2nd negedge.

        module negedge_test2;
            reg clk = 1;
            reg d = 0;
            reg q;
            always @(negedge clk) q <= d;
            initial while(1) #5 clk = !clk;
            initial #8 d = 1;  // d changes to 1 before 2nd negedge at t=15
        endmodule
        """
        m = Module(
            "negedge_test2",
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, initial_value=Literal(1)),
                Variable("d", VariableKind.REG, initial_value=Literal(0)),
                Variable("q", VariableKind.REG),
            ],
        )
        m.always_blocks = [
            AlwaysBlock(
                NonblockingAssign(Identifier("q"), Identifier("d")),
                sensitivity_list=[SensitivityEdge("negedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        m.initial_blocks = [
            InitialBlock(
                WhileLoop(
                    condition=Literal(1),
                    body=SeqBlock(
                        [
                            DelayControl(Literal(5)),
                            BlockingAssign(Identifier("clk"), UnaryOp("!", Identifier("clk"))),
                        ]
                    ),
                ),
            ),
            InitialBlock(
                SeqBlock(
                    [
                        DelayControl(Literal(8)),
                        BlockingAssign(Identifier("d"), Literal(1)),
                    ]
                ),
            ),
        ]
        sim = Simulator(m, engine=engine)
        # negedge at t=5: q=0 (d=0)
        # d changes to 1 at t=8
        # negedge at t=15: q=1 (d=1)
        sim.run(max_time=16)
        assert sim.read("q") == 1


# =====================================================================
# 19. Integer variables in always blocks
# =====================================================================
# Pattern from darksimv.v:
#   integer cycles;
#   always @(posedge clk) cycles = cycles + 1;
# Verifies 32-bit integer arithmetic in always blocks.


class TestIntegerVariable:
    """Integer variables (32-bit) in always blocks."""

    def _make_module(self) -> Module:
        """Build module with integer counter.

        module int_test(input clk);
            integer cycles;
            initial cycles = 0;
            always @(posedge clk) cycles = cycles + 1;
        endmodule
        """
        m = Module(
            "int_test",
            ports=[Port("clk", PortDirection.INPUT)],
            nets=[Net("clk", NetKind.WIRE)],
            variables=[
                Variable("cycles", VariableKind.INTEGER),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                BlockingAssign(Identifier("cycles"), Literal(0, width=32)),
            ),
        ]
        m.always_blocks = [
            AlwaysBlock(
                BlockingAssign(
                    Identifier("cycles"),
                    BinaryOp("+", Identifier("cycles"), Literal(1, width=32)),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_integer_increments(self, engine):
        """Integer counter increments on each posedge."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        # 3 posedges at t=0, t=10, t=20
        sim.run(max_time=25)
        assert sim.read("cycles") == Value(3, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_integer_width_is_32(self, engine):
        """Integer variable should be 32 bits wide."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        val = sim.read("cycles")
        assert val.width == 32


# =====================================================================
# 20. $dumpfile / $dumpvars VCD output
# =====================================================================
# Pattern from darksimv.v:
#   initial begin
#       $dumpfile("darksocv.vcd");
#       $dumpvars(0, darksocv);
#   end
# Verifies VCD file creation.


class TestDumpfileVCD:
    """$dumpfile and $dumpvars create VCD output."""

    def _make_module(self, filename: str) -> Module:
        """Build module with $dumpfile/$dumpvars.

        module vcd_test(input clk);
            reg [7:0] cnt = 0;
            always @(posedge clk) cnt <= cnt + 1;
            initial begin
                $dumpfile("output.vcd");
                $dumpvars;
            end
        endmodule
        """
        m = Module(
            "vcd_test",
            ports=[Port("clk", PortDirection.INPUT)],
            nets=[Net("clk", NetKind.WIRE)],
            variables=[
                Variable("cnt", VariableKind.REG, width=_w(8), initial_value=Literal(0, width=8)),
            ],
        )
        m.always_blocks = [
            AlwaysBlock(
                NonblockingAssign(
                    Identifier("cnt"),
                    BinaryOp("+", Identifier("cnt"), Literal(1, width=8)),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        m.initial_blocks = [
            InitialBlock(
                SeqBlock(
                    [
                        SystemTaskCall("$dumpfile", [StringLiteral(filename)]),
                        SystemTaskCall("$dumpvars"),
                    ]
                ),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", [e for e in ENGINES if e != "vm"])
    def test_vcd_file_created(self, engine, tmp_path):
        """$dumpfile/$dumpvars should create a VCD file."""
        vcd_path = tmp_path / "test_dump.vcd"
        m = self._make_module(str(vcd_path))
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=25)
        assert vcd_path.exists(), "VCD file should be created"

    @pytest.mark.parametrize("engine", [e for e in ENGINES if e != "vm"])
    def test_vcd_file_has_content(self, engine, tmp_path):
        """VCD file should contain signal definitions and time steps."""
        vcd_path = tmp_path / "test_content.vcd"
        m = self._make_module(str(vcd_path))
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=25)
        content = vcd_path.read_text()
        assert "$var" in content, "VCD should contain $var definitions"
        assert "$end" in content, "VCD should contain $end markers"


# =====================================================================
# 21. Signed comparison operators
# =====================================================================
# Pattern from darkriscv.v — BLT/BGE branches:
#   $signed(a) < $signed(b)
# Verifies signed comparison with negative two's-complement values.


class TestSignedComparison:
    """Signed comparison using $signed() wrapper."""

    def _make_module(self, op: str) -> Module:
        """Build module: assign out = ($signed(a) <op> $signed(b));

        module scmp_test(input [31:0] a, b, output out);
            assign out = ($signed(a) < $signed(b));
        endmodule
        """
        return Module(
            f"scmp_{op.replace('<', 'lt').replace('>', 'gt').replace('=', 'e')}",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(32)),
                Port("b", PortDirection.INPUT, width=_w(32)),
                Port("out", PortDirection.OUTPUT),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(32)),
                Net("b", NetKind.WIRE, width=_w(32)),
                Net("out", NetKind.WIRE),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("out"),
                    BinaryOp(
                        op,
                        FunctionCall("$signed", [Identifier("a")], is_system=True),
                        FunctionCall("$signed", [Identifier("b")], is_system=True),
                    ),
                ),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signed_lt_negative_vs_positive(self, engine):
        """-1 < 1 should be true (0xFFFFFFFF as signed is -1)."""
        m = self._make_module("<")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xFFFFFFFF, width=32))  # -1
        sim.drive("b", Value(1, width=32))  # 1
        sim.run(max_time=0)
        assert sim.read("out") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signed_lt_positive_vs_negative(self, engine):
        """1 < -1 should be false."""
        m = self._make_module("<")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(1, width=32))
        sim.drive("b", Value(0xFFFFFFFF, width=32))
        sim.run(max_time=0)
        assert sim.read("out") == 0

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signed_gte_equal(self, engine):
        """$signed(-5) >= $signed(-5) should be true."""
        m = self._make_module(">=")
        sim = Simulator(m, engine=engine)
        minus5 = (1 << 32) - 5  # 0xFFFFFFFB
        sim.drive("a", Value(minus5, width=32))
        sim.drive("b", Value(minus5, width=32))
        sim.run(max_time=0)
        assert sim.read("out") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_signed_lt_both_negative(self, engine):
        """-10 < -5 should be true."""
        m = self._make_module("<")
        sim = Simulator(m, engine=engine)
        minus10 = (1 << 32) - 10  # 0xFFFFFFF6
        minus5 = (1 << 32) - 5  # 0xFFFFFFFB
        sim.drive("a", Value(minus10, width=32))
        sim.drive("b", Value(minus5, width=32))
        sim.run(max_time=0)
        assert sim.read("out") == 1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_unsigned_lt_would_differ(self, engine):
        """Without $signed, 0xFFFFFFFF > 1 (unsigned). With $signed, -1 < 1."""
        m = self._make_module("<")
        sim = Simulator(m, engine=engine)
        sim.drive("a", Value(0xFFFFFFFF, width=32))
        sim.drive("b", Value(1, width=32))
        sim.run(max_time=0)
        assert sim.read("out") == 1  # Signed: -1 < 1


# =====================================================================
# 22. $fflush system task
# =====================================================================
# Pattern from darkuart.v:
#   $fflush();          // flush stdout
#   $fflush(32'h8000_0001);  // flush file descriptor
# Verifies $fflush is accepted without crashing.


class TestFflushSystemTask:
    """$fflush system task (no-op in simulation)."""

    def _make_module(self) -> Module:
        """Build module with $fflush in initial block.

        module fflush_test;
            reg [7:0] data = 8'hAB;
            initial begin
                $fflush();
                $fflush(32'h8000_0001);
            end
        endmodule
        """
        m = Module(
            "fflush_test",
            nets=[],
            variables=[
                Variable("data", VariableKind.REG, width=_w(8), initial_value=Literal(0xAB, width=8)),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                SeqBlock(
                    [
                        SystemTaskCall("$fflush"),
                        SystemTaskCall("$fflush", [Literal(0x80000001, width=32)]),
                    ]
                ),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_fflush_no_crash(self, engine):
        """$fflush should be silently accepted on all engines."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        # $fflush is a no-op; just verify the sim ran without error
        assert sim.read("data") == Value(0xAB, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_fflush_in_always(self, engine):
        """$fflush inside a clocked always block doesn't crash.

        module fflush_always;
            reg clk = 0;
            reg [7:0] cnt = 0;
            initial while(1) #5 clk = !clk;
            always @(posedge clk) begin
                cnt <= cnt + 1;
                $fflush();
            end
        endmodule
        """
        m = Module(
            "fflush_always",
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, initial_value=Literal(0)),
                Variable("cnt", VariableKind.REG, width=_w(8), initial_value=Literal(0, width=8)),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                WhileLoop(
                    condition=Literal(1),
                    body=SeqBlock(
                        [
                            DelayControl(Literal(5)),
                            BlockingAssign(Identifier("clk"), UnaryOp("!", Identifier("clk"))),
                        ]
                    ),
                ),
            ),
        ]
        m.always_blocks = [
            AlwaysBlock(
                SeqBlock(
                    [
                        NonblockingAssign(
                            Identifier("cnt"),
                            BinaryOp("+", Identifier("cnt"), Literal(1, width=8)),
                        ),
                        SystemTaskCall("$fflush"),
                    ]
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        sim = Simulator(m, engine=engine)
        sim.run(max_time=25)
        assert sim.read("cnt") == Value(3, width=8)  # posedges at t=5, t=15, t=25


# =====================================================================
# 23. Computed real #delay
# =====================================================================
# Pattern from darksimv.v:
#   while(1) #(500e6/`BOARD_CK) CLK = !CLK;
# Verifies float-valued and expression-based delays.


class TestComputedRealDelay:
    """Non-constant and real-valued #delay expressions."""

    def _make_module(self, delay_expr) -> Module:
        """Build module with clock generator using given delay expression.

        module delay_test;
            reg clk = 0;
            initial while(1) #<delay> clk = !clk;
        endmodule
        """
        m = Module(
            "delay_test",
            nets=[],
            variables=[
                Variable("clk", VariableKind.REG, initial_value=Literal(0)),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                WhileLoop(
                    condition=Literal(1),
                    body=SeqBlock(
                        [
                            DelayControl(delay_expr),
                            BlockingAssign(Identifier("clk"), UnaryOp("!", Identifier("clk"))),
                        ]
                    ),
                ),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_integer_expression_delay(self, engine):
        """#(100/10) = #10 — clock toggles every 10 time units."""
        m = self._make_module(BinaryOp("/", Literal(100, width=32), Literal(10, width=32)))
        sim = Simulator(m, engine=engine)
        # clk starts 0. Toggles at t=10, t=20, t=30
        sim.run(max_time=25)
        assert sim.read("clk") == 0  # toggled at t=10 (1), t=20 (0)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_real_literal_delay(self, engine):
        """#(10.5) truncated to #10 — standard Verilog behavior."""
        m = self._make_module(Literal(10.5))
        sim = Simulator(m, engine=engine)
        # Toggle at t=10, t=20
        sim.run(max_time=15)
        assert sim.read("clk") == 1  # toggled once at t=10

    @pytest.mark.parametrize("engine", ENGINES)
    def test_large_computed_delay(self, engine):
        """Large expression: #(1000/50) = #20."""
        m = self._make_module(BinaryOp("/", Literal(1000, width=32), Literal(50, width=32)))
        sim = Simulator(m, engine=engine)
        sim.run(max_time=45)
        assert sim.read("clk") == 0  # toggled at t=20 (1), t=40 (0)


# =====================================================================
# 24. inout ports with bidirectional wiring
# =====================================================================
# Verifies inout ports create bidirectional continuous assigns
# in hierarchy flattening.


class TestInoutPorts:
    """inout port direction — single module, no hierarchy.

    Verifies that PortDirection.INOUT is accepted and that a continuous
    assign can drive an inout wire like any other net.

    module inout_test(inout [7:0] bus, input [7:0] drive_val,
                      input drive_en, output [7:0] out);
        assign bus = drive_en ? drive_val : 8'b0;
        assign out = bus;
    endmodule
    """

    def _make_module(self) -> Module:
        return Module(
            "inout_test",
            ports=[
                Port("bus", PortDirection.INOUT, width=_w(8)),
                Port("drive_val", PortDirection.INPUT, width=_w(8)),
                Port("drive_en", PortDirection.INPUT),
                Port("out", PortDirection.OUTPUT, width=_w(8)),
            ],
            nets=[
                Net("bus", NetKind.WIRE, width=_w(8)),
                Net("drive_val", NetKind.WIRE, width=_w(8)),
                Net("drive_en", NetKind.WIRE),
                Net("out", NetKind.WIRE, width=_w(8)),
            ],
            continuous_assigns=[
                ContinuousAssign(
                    Identifier("bus"),
                    TernaryOp(
                        Identifier("drive_en"),
                        Identifier("drive_val"),
                        Literal(0, width=8),
                    ),
                ),
                ContinuousAssign(Identifier("out"), Identifier("bus")),
            ],
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_inout_drive_propagates(self, engine):
        """drive_en=1 → bus gets drive_val → out mirrors bus."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.drive("drive_val", Value(0x42, width=8))
        sim.drive("drive_en", Value(1, width=1))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x42, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_inout_no_drive_zero(self, engine):
        """drive_en=0 → bus gets 0 → out is 0."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        sim.drive("drive_val", Value(0xFF, width=8))
        sim.drive("drive_en", Value(0, width=1))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0, width=8)


# =====================================================================
# 25. Memory read-modify-write (byte enable pattern)
# =====================================================================
# Pattern from darkram.v:
#   MEM[addr][31:24] <= data[7:0];  // byte-lane write
# Native nested part-select on LHS is not yet supported, so this tests
# the read-modify-write workaround:
#   temp = MEM[addr]; temp[MSB:LSB] = val; MEM[addr] = temp;


class TestMemoryReadModifyWrite:
    """Memory byte-enable via read-modify-write pattern."""

    def _make_module(self) -> Module:
        """Build module with memory and byte-lane write via RMW.

        module rmw_test(input clk, input [1:0] addr, input [7:0] wdata,
                        input [1:0] byte_sel, input we);
            reg [31:0] mem [0:3];
            reg [31:0] temp;
            integer i;
            initial for(i=0; i<4; i=i+1) mem[i] = 0;

            always @(posedge clk) begin
                if (we) begin
                    temp = mem[addr];
                    temp[15:8] = wdata;  // always write to byte lane 1
                    mem[addr] = temp;
                end
            end
        endmodule
        """
        m = Module(
            "rmw_test",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("addr", PortDirection.INPUT, width=_w(2)),
                Port("wdata", PortDirection.INPUT, width=_w(8)),
                Port("we", PortDirection.INPUT),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                Net("addr", NetKind.WIRE, width=_w(2)),
                Net("wdata", NetKind.WIRE, width=_w(8)),
                Net("we", NetKind.WIRE),
            ],
            variables=[
                Variable("mem", VariableKind.REG, width=_w(32), dimensions=[Range(Literal(0), Literal(3))]),
                Variable("temp", VariableKind.REG, width=_w(32)),
                Variable("i", VariableKind.INTEGER),
            ],
        )
        # initial for(i=0; i<4; i=i+1) mem[i] = 0;
        m.initial_blocks = [
            InitialBlock(
                ForLoop(
                    init=BlockingAssign(Identifier("i"), Literal(0, width=32)),
                    condition=BinaryOp("<", Identifier("i"), Literal(4, width=32)),
                    update=BlockingAssign(
                        Identifier("i"),
                        BinaryOp("+", Identifier("i"), Literal(1, width=32)),
                    ),
                    body=BlockingAssign(
                        BitSelect(Identifier("mem"), Identifier("i")),
                        Literal(0, width=32),
                    ),
                ),
            ),
        ]
        # always @(posedge clk) begin
        #     if (we) begin
        #         temp = mem[addr];
        #         temp[15:8] = wdata;
        #         mem[addr] = temp;
        #     end
        # end
        from veriforge.model.statements import IfStatement  # noqa: PLC0415

        m.always_blocks = [
            AlwaysBlock(
                IfStatement(
                    Identifier("we"),
                    SeqBlock(
                        [
                            BlockingAssign(
                                Identifier("temp"),
                                BitSelect(Identifier("mem"), Identifier("addr")),
                            ),
                            BlockingAssign(
                                RangeSelect(Identifier("temp"), Literal(15), Literal(8)),
                                Identifier("wdata"),
                            ),
                            BlockingAssign(
                                BitSelect(Identifier("mem"), Identifier("addr")),
                                Identifier("temp"),
                            ),
                        ]
                    ),
                ),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_byte_write_to_zero_mem(self, engine):
        """Write 0xAB to byte lane 1 of mem[0] -> mem[0] = 0x0000AB00."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.drive("addr", Value(0, width=2))
        sim.drive("wdata", Value(0xAB, width=8))
        sim.drive("we", Value(1, width=1))
        sim.run(max_time=5)  # 1 posedge at t=0
        assert sim.read("mem[0]") == Value(0x0000AB00, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_byte_write_preserves_other_bytes(self, engine):
        """Writing byte lane 1 preserves other byte lanes."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        # First write 0xFF to byte lane 1 of mem[1]
        sim.drive("addr", Value(1, width=2))
        sim.drive("wdata", Value(0xFF, width=8))
        sim.drive("we", Value(1, width=1))
        sim.run(max_time=5)  # posedge at t=0
        assert sim.read("mem[1]") == Value(0x0000FF00, width=32)
        # Now write 0x42 — should overwrite byte 1, keep others
        sim.drive("wdata", Value(0x42, width=8))
        sim.run(max_time=15)  # posedge at t=10
        assert sim.read("mem[1]") == Value(0x00004200, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_different_addresses(self, engine):
        """Writes to different addresses are independent."""
        m = self._make_module()
        sim = Simulator(m, engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        # Write to mem[0]
        sim.drive("addr", Value(0, width=2))
        sim.drive("wdata", Value(0x11, width=8))
        sim.drive("we", Value(1, width=1))
        sim.run(max_time=5)
        # Write to mem[2]
        sim.drive("addr", Value(2, width=2))
        sim.drive("wdata", Value(0x22, width=8))
        sim.run(max_time=15)
        assert sim.read("mem[0]") == Value(0x00001100, width=32)
        assert sim.read("mem[2]") == Value(0x00002200, width=32)
