"""Tests for operator precedence, parametric memory dimensions, and string literal parsing.

Covers fixes applied to:
  - tree_to_model.py: Binary operator precedence (shunting-yard algorithm)
  - tree_to_model.py: StringLiteral parsing from Verilog string nodes
  - scheduler.py: _const_int() for parametric memory dimension evaluation
  - vm/compiler.py + compiled/codegen.py: _dim_depth() with parametric fallback

These bugs were discovered during DarkRISCV full-SoC simulation:
  - `i != 2**13/4` was parsed as `((i != 2) ** 13) / 4`
  - `reg [31:0] MEM [0:2**MLEN/4-1]` was not registered as memory
  - `$readmemh("file.mem", MEM)` filename parsed as Identifier, not StringLiteral
"""

import shutil

import pytest

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Identifier,
    Literal,
    Range,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)

from veriforge.model.nets import Net, NetKind
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    ForLoop,
    SeqBlock,
    SystemTaskCall,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

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


def _parse_module(source: str) -> Module:
    """Parse Verilog source and return the first Module."""
    vp = verilog_parser(start="module_declaration")
    tree = vp.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    assert isinstance(design, Design)
    assert len(design.modules) >= 1
    return design.modules[0]


# =====================================================================
# Operator Precedence Tests
# =====================================================================
# The Earley parser grammar has no operator precedence — all binary ops
# are at the same level.  A post-parse fixup (shunting-yard algorithm)
# restructures the BinaryOp tree according to Verilog precedence.


class TestOperatorPrecedence:
    """Verify binary operator precedence in parsed expressions."""

    def _get_assign_expr(self, m: Module) -> BinaryOp:
        """Extract the RHS expression from the first continuous assign."""
        assert len(m.continuous_assigns) >= 1
        return m.continuous_assigns[0].rhs

    def test_power_binds_tighter_than_multiply(self):
        """2**N * 4  →  (2**N) * 4, not 2**(N*4)."""
        m = _parse_module("""
        module t(input [7:0] n, output [31:0] y);
            assign y = 2 ** n * 4;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "*"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "**"

    def test_power_binds_tighter_than_divide(self):
        """2**13 / 4  →  (2**13) / 4."""
        m = _parse_module("""
        module t(output [31:0] y);
            assign y = 2 ** 13 / 4;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "/"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "**"

    def test_multiply_binds_tighter_than_add(self):
        """a + b * c  →  a + (b*c)."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output [7:0] y);
            assign y = a + b * c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "+"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.op == "*"

    def test_add_binds_tighter_than_comparison(self):
        """a + 1 < b  →  (a+1) < b."""
        m = _parse_module("""
        module t(input [7:0] a, b, output y);
            assign y = a + 1 < b;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "<"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "+"

    def test_comparison_binds_tighter_than_equality(self):
        """a < b == c  →  (a<b) == c."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output y);
            assign y = a < b == c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "=="
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "<"

    def test_equality_binds_tighter_than_bitwise_and(self):
        """a == b & c  →  (a==b) & c."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output y);
            assign y = a == b & c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "&"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "=="

    def test_bitwise_and_binds_tighter_than_bitwise_xor(self):
        """a & b ^ c  →  (a&b) ^ c."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output y);
            assign y = a & b ^ c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "^"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "&"

    def test_bitwise_xor_binds_tighter_than_bitwise_or(self):
        """a ^ b | c  →  (a^b) | c."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output y);
            assign y = a ^ b | c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "|"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "^"

    def test_bitwise_or_binds_tighter_than_logical_and(self):
        """a | b && c  →  (a|b) && c."""
        m = _parse_module("""
        module t(input a, b, c, output y);
            assign y = a | b && c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "&&"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "|"

    def test_logical_and_binds_tighter_than_logical_or(self):
        """a && b || c  →  (a&&b) || c."""
        m = _parse_module("""
        module t(input a, b, c, output y);
            assign y = a && b || c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "||"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "&&"

    def test_shift_binds_tighter_than_comparison(self):
        """a << 2 > b  →  (a<<2) > b."""
        m = _parse_module("""
        module t(input [7:0] a, b, output y);
            assign y = a << 2 > b;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == ">"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "<<"

    def test_not_equal_with_power_and_divide(self):
        """i != 2**13/4  →  i != ((2**13) / 4)  —  the DarkRISCV bug."""
        m = _parse_module("""
        module t(input [31:0] i, output y);
            assign y = i != 2 ** 13 / 4;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "!="
        # RHS should be: (2**13) / 4
        rhs = expr.right
        assert isinstance(rhs, BinaryOp)
        assert rhs.op == "/"
        assert isinstance(rhs.left, BinaryOp)
        assert rhs.left.op == "**"

    def test_three_way_add(self):
        """a + b + c is left-assoc: (a+b) + c."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output [7:0] y);
            assign y = a + b + c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "+"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "+"

    def test_power_is_right_associative(self):
        """2**3**4 is right-assoc: 2**(3**4)."""
        m = _parse_module("""
        module t(output [31:0] y);
            assign y = 2 ** 3 ** 4;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "**"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.op == "**"

    def test_parentheses_override_precedence(self):
        """(a + b) * c  →  parens respected."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, output [7:0] y);
            assign y = (a + b) * c;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "*"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "+"

    def test_mixed_precedence_chain(self):
        """a + b * c - d  →  (a + (b*c)) - d."""
        m = _parse_module("""
        module t(input [7:0] a, b, c, d, output [7:0] y);
            assign y = a + b * c - d;
        endmodule
        """)
        expr = self._get_assign_expr(m)
        assert isinstance(expr, BinaryOp)
        assert expr.op == "-"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "+"
        assert isinstance(expr.left.right, BinaryOp)
        assert expr.left.right.op == "*"


# =====================================================================
# Operator Precedence in Constant Expressions
# =====================================================================
# constant_expression also needs precedence fixup (used in dimensions,
# parameters, generate blocks).


class TestConstExprPrecedence:
    """Verify precedence in constant_expression contexts."""

    def test_parameter_dimension_power_divide(self):
        """parameter DEPTH = 2**MLEN/4  →  (2**MLEN)/4."""
        m = _parse_module("""
        module t;
            parameter MLEN = 13;
            parameter DEPTH = 2 ** MLEN / 4;
        endmodule
        """)
        depth_param = None
        for p in m.parameters:
            if p.name == "DEPTH":
                depth_param = p
                break
        assert depth_param is not None
        expr = depth_param.default_value
        assert isinstance(expr, BinaryOp)
        assert expr.op == "/"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "**"

    def test_dimension_expression(self):
        """reg [31:0] mem [0:2**13/4-1]  →  dimension upper = (2**13)/4 - 1."""
        m = _parse_module("""
        module t;
            reg [31:0] mem [0:2**13/4-1];
        endmodule
        """)
        assert len(m.variables) >= 1
        mem_var = m.variables[0]
        assert mem_var.name == "mem"
        assert mem_var.dimensions is not None
        dim = mem_var.dimensions[0]
        # lsb (upper bound) should be: ((2**13)/4) - 1
        lsb_expr = dim.lsb
        assert isinstance(lsb_expr, BinaryOp)
        assert lsb_expr.op == "-"
        assert isinstance(lsb_expr.left, BinaryOp)
        assert lsb_expr.left.op == "/"
        assert isinstance(lsb_expr.left.left, BinaryOp)
        assert lsb_expr.left.left.op == "**"

    def test_parameter_ternary_with_binary_condition(self):
        """parameter X = A > B ? B : A  →  TernaryOp(A>B, B, A), not BinaryOp.

        Regression test: the Earley parser may resolve 'A > B ? B : A' as
        A > (B ? B : A).  The transformer must emit TernaryOp regardless of
        which parse tree shape it receives.
        """
        m = _parse_module("""
        module t;
            parameter A = 8;
            parameter B = 4;
            parameter X = A > B ? B : A;
        endmodule
        """)
        x_param = next((p for p in m.parameters if p.name == "X"), None)
        assert x_param is not None
        expr = x_param.default_value
        assert isinstance(expr, TernaryOp), f"Expected TernaryOp, got {type(expr).__name__}: {expr}"
        assert isinstance(expr.condition, BinaryOp)
        assert expr.condition.op == ">"


# =====================================================================
# Operator Precedence in Simulation
# =====================================================================
# Verify that correctly-parsed expressions produce correct simulation
# results when constant-evaluated.


class TestPrecedenceSimulation:
    """Verify precedence is correct in simulation results."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_power_divide_assign(self, engine):
        """assign y = 2**8 / 4  →  256/4 = 64."""
        m = _parse_module("""
        module t(output [31:0] y);
            assign y = 2 ** 8 / 4;
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(64, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_add_multiply_precedence(self, engine):
        """assign y = 3 + 4 * 5  →  3+20 = 23, not 35."""
        m = _parse_module("""
        module t(output [31:0] y);
            assign y = 3 + 4 * 5;
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(23, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_not_equal_power_divide(self, engine):
        """The DarkRISCV for-loop condition: 0 != 2**4/4  →  0 != 4  →  1."""
        m = _parse_module("""
        module t(output [31:0] y);
            assign y = 0 != 2 ** 4 / 4;
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(1, width=32)


# =====================================================================
# StringLiteral Parsing Tests
# =====================================================================
# Verify that Verilog string literals in system task arguments are
# parsed as StringLiteral objects, not Identifier objects.


class TestStringLiteralParsing:
    """Verify strings are parsed as StringLiteral in the model."""

    def test_display_string_argument(self):
        """$display("hello") — first arg is StringLiteral."""
        m = _parse_module("""
        module t;
            initial $display("hello world");
        endmodule
        """)
        assert len(m.initial_blocks) == 1
        stmt = m.initial_blocks[0].body
        assert isinstance(stmt, SystemTaskCall)
        assert stmt.task_name == "$display"
        assert len(stmt.arguments) >= 1
        assert isinstance(stmt.arguments[0], StringLiteral)
        assert stmt.arguments[0].value == "hello world"

    def test_readmemh_filename_string(self):
        """$readmemh("file.mem", mem) — filename is StringLiteral."""
        m = _parse_module("""
        module t;
            reg [7:0] mem [0:3];
            initial $readmemh("test.hex", mem);
        endmodule
        """)
        assert len(m.initial_blocks) == 1
        stmt = m.initial_blocks[0].body
        assert isinstance(stmt, SystemTaskCall)
        assert stmt.task_name == "$readmemh"
        assert len(stmt.arguments) >= 2
        assert isinstance(stmt.arguments[0], StringLiteral)
        assert stmt.arguments[0].value == "test.hex"
        assert isinstance(stmt.arguments[1], Identifier)
        assert stmt.arguments[1].name == "mem"

    def test_dumpfile_string_argument(self):
        """$dumpfile("output.vcd") — StringLiteral."""
        m = _parse_module("""
        module t;
            initial $dumpfile("output.vcd");
        endmodule
        """)
        stmt = m.initial_blocks[0].body
        assert isinstance(stmt, SystemTaskCall)
        assert isinstance(stmt.arguments[0], StringLiteral)
        assert stmt.arguments[0].value == "output.vcd"

    def test_string_in_constant_expression(self):
        """String used as parameter value."""
        m = _parse_module("""
        module t;
            parameter MSG = "hello";
        endmodule
        """)
        assert len(m.parameters) >= 1
        param = m.parameters[0]
        assert isinstance(param.default_value, StringLiteral)
        assert param.default_value.value == "hello"


# =====================================================================
# Parametric Memory Dimension Tests
# =====================================================================
# Verify that memory arrays with computed dimensions (involving
# parameters) are properly registered and accessible.


class TestParametricMemoryDimensions:
    """Verify parametric memory array dimensions are correctly evaluated."""

    def _make_param_mem_module(self, depth_expr, depth_val: int) -> Module:
        """Build module with parametric memory.

        module t;
            parameter DEPTH = <depth_val>;
            reg [7:0] mem [0:DEPTH-1];
            reg [7:0] out;
            initial begin
                mem[0] = 8'hAA;
                mem[DEPTH-1] = 8'hBB;
            end
        endmodule
        """
        m = Module(
            "param_mem_test",
            parameters=[Parameter("DEPTH", default_value=Literal(depth_val, width=32))],
            variables=[
                Variable(
                    "mem",
                    VariableKind.REG,
                    width=_w(8),
                    dimensions=[Range(Literal(0), BinaryOp("-", Identifier("DEPTH"), Literal(1)))],
                ),
                Variable("out", VariableKind.REG, width=_w(8)),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(BitSelect(Identifier("mem"), Literal(0)), Literal(0xAA, width=8)),
                        BlockingAssign(
                            BitSelect(Identifier("mem"), BinaryOp("-", Identifier("DEPTH"), Literal(1))),
                            Literal(0xBB, width=8),
                        ),
                    ]
                ),
            ),
        ]
        return m

    @pytest.mark.parametrize("engine", ENGINES)
    def test_param_depth_8(self, engine):
        """Memory with DEPTH=8 parameter — first and last element written."""
        m = self._make_param_mem_module(
            BinaryOp("-", Identifier("DEPTH"), Literal(1)),
            depth_val=8,
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("mem[0]") == Value(0xAA, width=8)
        assert sim.read("mem[7]") == Value(0xBB, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_param_depth_16(self, engine):
        """Memory with DEPTH=16 — verifies larger parametric dimension."""
        m = self._make_param_mem_module(
            BinaryOp("-", Identifier("DEPTH"), Literal(1)),
            depth_val=16,
        )
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("mem[0]") == Value(0xAA, width=8)
        assert sim.read("mem[15]") == Value(0xBB, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_power_expression_dimension(self, engine):
        """Memory with dimension [0:2**3-1] = 8 elements."""
        m = Module(
            "power_mem_test",
            variables=[
                Variable(
                    "mem",
                    VariableKind.REG,
                    width=_w(8),
                    dimensions=[Range(Literal(0), BinaryOp("-", BinaryOp("**", Literal(2), Literal(3)), Literal(1)))],
                ),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                SeqBlock(
                    [
                        BlockingAssign(BitSelect(Identifier("mem"), Literal(0)), Literal(0x11, width=8)),
                        BlockingAssign(BitSelect(Identifier("mem"), Literal(7)), Literal(0x77, width=8)),
                    ]
                ),
            ),
        ]
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("mem[0]") == Value(0x11, width=8)
        assert sim.read("mem[7]") == Value(0x77, width=8)


# =============================================================================
# $readmemh with StringLiteral (end-to-end)
# =====================================================================
# Verify that parsing Verilog source with $readmemh produces a
# StringLiteral filename and the simulator can actually load the file.


class TestReadmemhParsed:
    """End-to-end: parse $readmemh from Verilog source and simulate."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_from_parsed_source(self, engine, tmp_path):
        """Parse Verilog with $readmemh and simulate — memory loaded."""
        hex_file = tmp_path / "test.hex"
        hex_file.write_text("DE\nAD\nBE\nEF\n")
        # Use forward slashes for cross-platform path in Verilog source
        path_str = str(hex_file).replace("\\", "/")
        m = _parse_module(f"""
        module t;
            reg [7:0] mem [0:3];
            initial $readmemh("{path_str}", mem);
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("mem[0]") == Value(0xDE, width=8)
        assert sim.read("mem[1]") == Value(0xAD, width=8)
        assert sim.read("mem[2]") == Value(0xBE, width=8)
        assert sim.read("mem[3]") == Value(0xEF, width=8)


# =====================================================================
# For loop with parametric bound (end-to-end simulation)
# =====================================================================
# The DarkRISCV pattern: for(i=0; i!=2**MLEN/4; i=i+1) MEM[i] = 0;
# Requires both correct parsing (precedence) and correct simulation.


class TestForLoopParametricBound:
    """For loop with computed bound in initial block."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_for_loop_power_divide_bound(self, engine):
        """for(i=0; i!=2**3; i=i+1) mem[i]=i  →  8 iterations."""
        m = Module(
            "forloop_test",
            variables=[
                Variable("mem", VariableKind.REG, width=_w(8), dimensions=[Range(Literal(0), Literal(7))]),
                Variable("i", VariableKind.INTEGER),
            ],
        )
        m.initial_blocks = [
            InitialBlock(
                ForLoop(
                    init=BlockingAssign(Identifier("i"), Literal(0)),
                    condition=BinaryOp("!=", Identifier("i"), BinaryOp("**", Literal(2), Literal(3))),
                    update=BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal(1))),
                    body=BlockingAssign(BitSelect(Identifier("mem"), Identifier("i")), Identifier("i")),
                ),
            ),
        ]
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        for i in range(8):
            assert sim.read(f"mem[{i}]") == Value(i, width=8)


# =====================================================================
# Popcount-style always_comb for-loop with delayed testbench
# =====================================================================


class TestAlwaysCombForLoopWithDelay:
    """Regression: delayed testbenches must preserve loop assignment widths."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_popcount_style_always_comb(self, engine):
        """always_comb accumulator with for(int i=...) works through #delay path."""
        m = _parse_module("""
        module t;
            logic [4:0] data;
            logic [3:0] out;

            always_comb begin
                out = 0;
                for (int i = 0; i < 5; i = i + 1) begin
                    out = out + data[i];
                end
            end

            initial begin
                data = 5'b10110;
                #1;
                if (out != 4'd3)
                    $display("FAIL out=%0d", out);
                else
                    $display("PASS out=%0d", out);
                $finish;
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=2)
        assert sim.read("out") == Value(3, width=4)
        assert any("PASS out=3" in line for line in sim.display_output)


class TestProceduralBlockLocalDecls:
    """Regression: unnamed procedural blocks may declare local temporaries."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_always_comb_local_logic_decl(self, engine):
        """Local logic declared inside unnamed begin/end should execute cross-engine."""
        m = _parse_module("""
        module t;
            logic a;
            logic y;

            always_comb begin
                logic tmp;
                tmp = ~a;
                y = tmp;
            end

            initial begin
                a = 1'b0;
                #1;
                if (y != 1'b1)
                    $display("FAIL y=%0d", y);
                a = 1'b1;
                #1;
                if (y != 1'b0)
                    $display("FAIL y=%0d", y);
                else
                    $display("PASS y=%0d", y);
                $finish;
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=3)
        assert sim.read("y") == Value(0, width=1)
        assert any("PASS y=0" in line for line in sim.display_output)


class TestInsideRangeSyntax:
    """Regression: inside range items should parse and execute cross-engine."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_inside_range_in_always_comb(self, engine):
        m = _parse_module("""
        module t;
            logic [2:0] x;
            logic hit;

            always_comb begin
                if (x inside {[1:3]})
                    hit = 1'b1;
                else
                    hit = 1'b0;
            end

            initial begin
                x = 3'd0;
                #1;
                if (hit != 1'b0)
                    $display("FAIL x=%0d hit=%0d", x, hit);
                x = 3'd2;
                #1;
                if (hit != 1'b1)
                    $display("FAIL x=%0d hit=%0d", x, hit);
                x = 3'd4;
                #1;
                if (hit != 1'b0)
                    $display("FAIL x=%0d hit=%0d", x, hit);
                else
                    $display("PASS hit=%0d", hit);
                $finish;
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=4)
        assert sim.read("hit") == Value(0, width=1)
        assert any("PASS hit=0" in line for line in sim.display_output)


# =====================================================================
# Net Declaration Assignment (wire foo = expr;)
# =====================================================================
# In Verilog, `wire foo = bar;` is a net-declaration-assignment that
# implicitly creates an `assign foo = bar;` continuous assignment.
# This is one of the most common Verilog constructs.  Previously the
# parser discarded the initializer expression, leaving the wire
# undriven (stuck at x).


class TestNetDeclAssignmentParsing:
    """Verify that `wire x = expr;` produces both a Net and a ContinuousAssign."""

    def test_simple_wire_assign(self):
        """wire y = a & b; should create a net and a continuous assign."""
        m = _parse_module("""
        module t(input a, input b, output y);
            wire y = a & b;
        endmodule
        """)
        # Net should exist
        net_names = [n.name for n in m.nets]
        assert "y" in net_names
        # Continuous assign should exist for y
        ca_lhs_names = [ca.lhs.name for ca in m.continuous_assigns if hasattr(ca.lhs, "name")]
        assert "y" in ca_lhs_names

    def test_wire_assign_rhs_is_expression(self):
        """The RHS of wire x = expr; should be a proper expression tree."""
        m = _parse_module("""
        module t(input a, input b);
            wire y = a | b;
        endmodule
        """)
        ca = next(ca for ca in m.continuous_assigns if hasattr(ca.lhs, "name") and ca.lhs.name == "y")
        assert isinstance(ca.rhs, BinaryOp)
        assert ca.rhs.op == "|"

    def test_wire_ternary_assign(self):
        """wire y = sel ? a : b; — ternary in net declaration."""
        m = _parse_module("""
        module t(input sel, input a, input b);
            wire y = sel ? a : b;
        endmodule
        """)
        ca = next(ca for ca in m.continuous_assigns if hasattr(ca.lhs, "name") and ca.lhs.name == "y")
        assert isinstance(ca.rhs, TernaryOp)

    def test_wire_unary_assign(self):
        """wire y = ~a; — unary in net declaration."""
        m = _parse_module("""
        module t(input a);
            wire y = ~a;
        endmodule
        """)
        ca = next(ca for ca in m.continuous_assigns if hasattr(ca.lhs, "name") and ca.lhs.name == "y")
        assert isinstance(ca.rhs, UnaryOp)
        assert ca.rhs.op == "~"

    def test_multiple_net_decl_assignments(self):
        """wire a = x, b = y; — multiple assignments in one declaration."""
        m = _parse_module("""
        module t(input x, input y);
            wire a = x, b = y;
        endmodule
        """)
        net_names = [n.name for n in m.nets]
        assert "a" in net_names
        assert "b" in net_names
        ca_names = {ca.lhs.name for ca in m.continuous_assigns if hasattr(ca.lhs, "name")}
        assert "a" in ca_names
        assert "b" in ca_names

    def test_wide_wire_assign(self):
        """wire [7:0] y = a + b; — ranged net with assignment."""
        m = _parse_module("""
        module t(input [7:0] a, input [7:0] b);
            wire [7:0] y = a + b;
        endmodule
        """)
        net = next(n for n in m.nets if n.name == "y")
        assert net.width is not None
        ca = next(ca for ca in m.continuous_assigns if hasattr(ca.lhs, "name") and ca.lhs.name == "y")
        assert isinstance(ca.rhs, BinaryOp)
        assert ca.rhs.op == "+"

    def test_wire_assign_coexists_with_explicit_assign(self):
        """Both implicit and explicit assigns in the same module."""
        m = _parse_module("""
        module t(input a, input b, input c);
            wire x = a & b;
            assign y = x | c;
            wire y;
        endmodule
        """)
        ca_names = {ca.lhs.name for ca in m.continuous_assigns if hasattr(ca.lhs, "name")}
        assert "x" in ca_names
        assert "y" in ca_names


class TestNetDeclAssignmentSimulation:
    """Verify that `wire x = expr;` drives correct values in simulation."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wire_and_gate(self, engine):
        """wire y = a & b; with a=1, b=1 → y=1."""
        m = _parse_module("""
        module t;
            reg a, b;
            wire y = a & b;
            initial begin
                a = 1;
                b = 1;
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wire_ternary_mux(self, engine):
        """wire y = sel ? a : b; — mux via net declaration."""
        m = _parse_module("""
        module t;
            reg sel;
            reg [7:0] a, b;
            wire [7:0] y = sel ? a : b;
            initial begin
                a = 8'hAA;
                b = 8'h55;
                sel = 0;
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(0x55, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wire_reduction_or(self, engine):
        """wire y = |data; — reduction OR via net declaration."""
        m = _parse_module("""
        module t;
            reg [3:0] data;
            wire y = |data;
            initial data = 4'b0000;
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_chained_wire_assigns(self, engine):
        """wire b = a; wire c = b; — chain of net declaration assigns."""
        m = _parse_module("""
        module t;
            reg [7:0] a;
            wire [7:0] b = a;
            wire [7:0] c = b;
            initial a = 8'h42;
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("b") == Value(0x42, width=8)
        assert sim.read("c") == Value(0x42, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wire_complex_expression(self, engine):
        """wire y = (a + b) >> 1; — complex expression in net decl."""
        m = _parse_module("""
        module t;
            reg [7:0] a, b;
            wire [7:0] y = (a + b) >> 1;
            initial begin
                a = 8'd10;
                b = 8'd20;
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("y") == Value(15, width=8)


class TestSignedExtension:
    """$signed() should sign-extend when assigned to a wider target."""

    @pytest.mark.parametrize("engine", ["reference", "vm"])
    def test_signed_nba_wider_target(self, engine):
        """$signed(21-bit) assigned to 32-bit concat LHS should sign-extend."""
        m = _parse_module("""
        module t;
            reg clk = 0;
            reg [31:0] imm;
            reg done = 0;
            always #5 clk = ~clk;
            always @(posedge clk) begin
                if (!done) begin
                    // 21'h1FFFF4 = -12 signed; should sign-extend to 32'hFFFFFFF4
                    imm <= $signed(21'h1FFFF4);
                    done <= 1;
                end
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=100)
        assert sim.read("imm") == Value(0xFFFFFFF4, width=32)

    @pytest.mark.parametrize("engine", ["reference", "vm"])
    def test_signed_blocking_wider_target(self, engine):
        """$signed(blocking assign) sign-extends to wider target."""
        m = _parse_module("""
        module t;
            reg [31:0] result;
            initial begin
                result = $signed(8'hFF);  // -1 in 8-bit → 32'hFFFFFFFF
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("result") == Value(0xFFFFFFFF, width=32)

    @pytest.mark.parametrize("engine", ["reference", "vm"])
    def test_signed_positive_no_extend(self, engine):
        """$signed(positive value) should not set upper bits."""
        m = _parse_module("""
        module t;
            reg [31:0] result;
            initial begin
                result = $signed(8'h7F);  // +127 in 8-bit → 32'h0000007F
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=0)
        assert sim.read("result") == Value(0x7F, width=32)

    @pytest.mark.parametrize("engine", ["reference", "vm"])
    def test_signed_concat_lhs_nba(self, engine):
        """$signed + simple concat LHS NBA sign-extends correctly.

        {imm[31:8], imm[7:0]} <= $signed(8'hFF)
        RHS is 8 bits, LHS concat is 32 bits → sign-extend 8→32.
        Result: imm = 0xFFFFFFFF (-1 sign-extended).
        """
        m = _parse_module("""
        module t;
            reg clk = 0;
            reg [31:0] imm;
            reg done = 0;
            always #5 clk = ~clk;
            always @(posedge clk) begin
                if (!done) begin
                    {imm[31:8], imm[7:0]} <= $signed(8'hFF);
                    done <= 1;
                end
            end
        endmodule
        """)
        sim = Simulator(m, engine=engine)
        sim.run(max_time=100)
        assert sim.read("imm") == Value(0xFFFFFFFF, width=32)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
