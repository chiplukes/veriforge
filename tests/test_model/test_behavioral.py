"""Tests for Phase 3: Behavioral — AlwaysBlock, InitialBlock, Statements.

Tests cover:
  - Always block extraction with various sensitivity lists
  - Initial block extraction
  - Statement types: blocking/nonblocking assign, if/else, case, loops, system tasks
  - Sensitivity classification (combinational, sequential)
  - Emitter round-trip for behavioral constructs
"""
# ruff: noqa: PLR2004

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, UnaryOp
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    EventControl,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    RepeatLoop,
    SeqBlock,
    SensitivityEdge,
    SystemTaskCall,
    WhileLoop,
)
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str) -> Module:
    """Helper: parse source and return the first Module."""
    tree = parser.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    assert isinstance(design, Design)
    assert len(design.modules) == 1
    return design.modules[0]


# ---- Always block extraction ----


class TestAlwaysCombinational:
    """Always blocks with combinational sensitivity."""

    def test_always_star(self, parser):
        source = """module top;
reg y;
wire a, b;
always @(*) begin
    y = a & b;
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.always_blocks) == 1
        ab = m.always_blocks[0]
        assert isinstance(ab, AlwaysBlock)
        assert ab.sensitivity_type == SensitivityType.COMBINATIONAL
        assert len(ab.sensitivity_list) == 0  # @(*) = empty list

    def test_always_star_body_is_seq_block(self, parser):
        source = """module top;
reg y;
wire a;
always @(*) begin
    y = a;
end
endmodule"""
        m = _parse_module(parser, source)
        ab = m.always_blocks[0]
        assert isinstance(ab.body, SeqBlock)
        assert len(ab.body.statements) == 1
        stmt = ab.body.statements[0]
        assert isinstance(stmt, BlockingAssign)
        assert isinstance(stmt.lhs, Identifier)
        assert stmt.lhs.name == "y"
        assert isinstance(stmt.rhs, Identifier)
        assert stmt.rhs.name == "a"

    def test_always_comb_and_expression(self, parser):
        source = """module top;
reg y;
wire a, b;
always @(*) begin
    y = a & b;
end
endmodule"""
        m = _parse_module(parser, source)
        ab = m.always_blocks[0]
        block = ab.body
        assert isinstance(block, SeqBlock)
        stmt = block.statements[0]
        assert isinstance(stmt, BlockingAssign)
        assert isinstance(stmt.rhs, BinaryOp)
        assert stmt.rhs.op == "&"


class TestAlwaysSequential:
    """Always blocks with edge-triggered sensitivity."""

    def test_posedge_clk(self, parser):
        source = """module top;
reg q;
wire clk, d;
always @(posedge clk) begin
    q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.always_blocks) == 1
        ab = m.always_blocks[0]
        assert ab.sensitivity_type == SensitivityType.SEQUENTIAL
        assert len(ab.sensitivity_list) == 1
        edge = ab.sensitivity_list[0]
        assert isinstance(edge, SensitivityEdge)
        assert edge.edge == "posedge"
        assert isinstance(edge.signal, Identifier)
        assert edge.signal.name == "clk"

    def test_posedge_body_nonblocking(self, parser):
        source = """module top;
reg q;
wire clk, d;
always @(posedge clk) begin
    q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        ab = m.always_blocks[0]
        block = ab.body
        assert isinstance(block, SeqBlock)
        stmt = block.statements[0]
        assert isinstance(stmt, NonblockingAssign)
        assert stmt.lhs.name == "q"
        assert stmt.rhs.name == "d"

    def test_posedge_negedge_reset(self, parser):
        source = """module top;
reg q;
wire clk, rst, d;
always @(posedge clk or negedge rst) begin
    if (!rst)
        q <= 1'b0;
    else
        q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        ab = m.always_blocks[0]
        assert ab.sensitivity_type == SensitivityType.SEQUENTIAL
        assert len(ab.sensitivity_list) == 2
        assert ab.sensitivity_list[0].edge == "posedge"
        assert ab.sensitivity_list[0].signal.name == "clk"
        assert ab.sensitivity_list[1].edge == "negedge"
        assert ab.sensitivity_list[1].signal.name == "rst"

    def test_reset_body_is_if_else(self, parser):
        source = """module top;
reg q;
wire clk, rst, d;
always @(posedge clk or negedge rst) begin
    if (!rst)
        q <= 1'b0;
    else
        q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        ab = m.always_blocks[0]
        block = ab.body
        assert isinstance(block, SeqBlock)
        stmt = block.statements[0]
        assert isinstance(stmt, IfStatement)
        assert isinstance(stmt.condition, UnaryOp)
        assert stmt.condition.op == "!"
        assert isinstance(stmt.then_body, NonblockingAssign)
        assert isinstance(stmt.else_body, NonblockingAssign)


class TestAlwaysNoSensitivity:
    """Always blocks without sensitivity list."""

    def test_bare_always_begin(self, parser):
        source = """module top;
reg clk;
always begin
    #5 clk = ~clk;
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.always_blocks) == 1
        ab = m.always_blocks[0]
        # No sensitivity list → body is the seq_block directly
        assert isinstance(ab.body, SeqBlock)


# ---- Initial block extraction ----


class TestInitialBlock:
    """Initial block extraction."""

    def test_simple_initial(self, parser):
        source = """module top;
reg [7:0] mem;
initial begin
    mem = 8'h00;
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.initial_blocks) == 1
        ib = m.initial_blocks[0]
        assert isinstance(ib, InitialBlock)
        assert isinstance(ib.body, SeqBlock)
        assert len(ib.body.statements) == 1
        stmt = ib.body.statements[0]
        assert isinstance(stmt, BlockingAssign)
        assert stmt.lhs.name == "mem"

    def test_initial_with_system_tasks(self, parser):
        source = """module top;
wire [7:0] data;
initial begin
    $display("hello");
    $finish;
end
endmodule"""
        m = _parse_module(parser, source)
        ib = m.initial_blocks[0]
        block = ib.body
        assert isinstance(block, SeqBlock)
        assert len(block.statements) == 2
        assert isinstance(block.statements[0], SystemTaskCall)
        assert block.statements[0].task_name == "$display"
        assert len(block.statements[0].arguments) == 1
        assert isinstance(block.statements[1], SystemTaskCall)
        assert block.statements[1].task_name == "$finish"
        assert len(block.statements[1].arguments) == 0


# ---- Statement types ----


class TestBlockingAssign:
    """Blocking assignment extraction."""

    def test_simple_blocking(self, parser):
        source = """module top;
reg y;
wire a;
always @(*) begin
    y = a;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, BlockingAssign)
        assert isinstance(stmt.lhs, Identifier)
        assert isinstance(stmt.rhs, Identifier)


class TestNonblockingAssign:
    """Nonblocking assignment extraction."""

    def test_simple_nonblocking(self, parser):
        source = """module top;
reg q;
wire clk, d;
always @(posedge clk) begin
    q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, NonblockingAssign)


class TestIfStatement:
    """If/else statement extraction."""

    def test_if_without_else(self, parser):
        source = """module top;
reg y;
wire sel, a;
always @(*) begin
    if (sel)
        y = a;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, IfStatement)
        assert isinstance(stmt.condition, Identifier)
        assert stmt.condition.name == "sel"
        assert isinstance(stmt.then_body, BlockingAssign)
        assert stmt.else_body is None

    def test_if_else(self, parser):
        source = """module top;
reg y;
wire sel, a;
always @(*) begin
    if (sel)
        y = a;
    else
        y = 1'b0;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, IfStatement)
        assert isinstance(stmt.then_body, BlockingAssign)
        assert isinstance(stmt.else_body, BlockingAssign)

    def test_nested_if_else(self, parser):
        source = """module top;
reg [1:0] y;
wire [1:0] sel;
always @(*) begin
    if (sel[0])
        y = 2'b01;
    else if (sel[1])
        y = 2'b10;
    else
        y = 2'b00;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, IfStatement)
        # The else branch should contain another IfStatement
        assert isinstance(stmt.else_body, IfStatement)


class TestCaseStatement:
    """Case statement extraction."""

    def test_basic_case(self, parser):
        source = """module top;
reg [1:0] out;
wire [1:0] sel;
always @(*) begin
    case (sel)
        2'b00: out = 2'b01;
        2'b01: out = 2'b10;
        default: out = 2'b00;
    endcase
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, CaseStatement)
        assert stmt.case_type == "case"
        assert isinstance(stmt.expression, Identifier)
        assert stmt.expression.name == "sel"
        assert len(stmt.items) == 3

        # First item
        assert isinstance(stmt.items[0], CaseItem)
        assert not stmt.items[0].is_default
        assert len(stmt.items[0].values) == 1
        assert isinstance(stmt.items[0].body, BlockingAssign)

        # Default item
        assert stmt.items[2].is_default

    def test_casex(self, parser):
        source = """module top;
reg y;
wire [1:0] sel;
always @(*) begin
    casex (sel)
        2'b1x: y = 1'b1;
        default: y = 1'b0;
    endcase
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.always_blocks[0].body.statements[0]
        assert isinstance(stmt, CaseStatement)
        assert stmt.case_type == "casex"


class TestLoopStatements:
    """Loop statement extraction."""

    def test_for_loop(self, parser):
        source = """module top;
integer i;
reg [7:0] mem [0:3];
initial begin
    for (i = 0; i < 4; i = i + 1)
        mem[i] = 8'h00;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.initial_blocks[0].body.statements[0]
        assert isinstance(stmt, ForLoop)
        assert isinstance(stmt.init, BlockingAssign)
        assert isinstance(stmt.init.lhs, Identifier)
        assert stmt.init.lhs.name == "i"
        assert isinstance(stmt.condition, BinaryOp)
        assert stmt.condition.op == "<"
        assert isinstance(stmt.update, BlockingAssign)
        assert isinstance(stmt.body, BlockingAssign)

    def test_while_loop(self, parser):
        source = """module top;
integer count;
initial begin
    count = 0;
    while (count < 10)
        count = count + 1;
end
endmodule"""
        m = _parse_module(parser, source)
        stmts = m.initial_blocks[0].body.statements
        # Find the while loop
        while_stmt = None
        for s in stmts:
            if isinstance(s, WhileLoop):
                while_stmt = s
                break
        assert while_stmt is not None
        assert isinstance(while_stmt.condition, BinaryOp)
        assert while_stmt.condition.op == "<"
        assert isinstance(while_stmt.body, BlockingAssign)

    def test_forever_loop(self, parser):
        source = """module top;
reg clk;
initial begin
    clk = 0;
    forever #5 clk = ~clk;
end
endmodule"""
        m = _parse_module(parser, source)
        stmts = m.initial_blocks[0].body.statements
        forever = None
        for s in stmts:
            if isinstance(s, ForeverLoop):
                forever = s
                break
        assert forever is not None
        assert forever.body is not None

    def test_repeat_loop(self, parser):
        source = """module top;
reg clk;
initial begin
    clk = 0;
    repeat (10) #5 clk = ~clk;
end
endmodule"""
        m = _parse_module(parser, source)
        stmts = m.initial_blocks[0].body.statements
        repeat = None
        for s in stmts:
            if isinstance(s, RepeatLoop):
                repeat = s
                break
        assert repeat is not None
        assert isinstance(repeat.count, Literal)


class TestDelayControl:
    """Delay control extraction."""

    def test_delay_in_initial(self, parser):
        source = """module top;
reg clk;
initial begin
    clk = 0;
    #5 clk = 1;
end
endmodule"""
        m = _parse_module(parser, source)
        stmts = m.initial_blocks[0].body.statements
        delay_stmt = None
        for s in stmts:
            if isinstance(s, DelayControl):
                delay_stmt = s
                break
        assert delay_stmt is not None
        assert isinstance(delay_stmt.delay, Literal)
        assert delay_stmt.delay.value == 5
        assert isinstance(delay_stmt.body, BlockingAssign)


class TestEventControl:
    """Event control statement extraction."""

    def test_event_control_in_always(self, parser):
        source = """module top;
reg y;
wire clk, d;
always begin
    @(posedge clk) y <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        ab = m.always_blocks[0]
        block = ab.body
        assert isinstance(block, SeqBlock)
        stmt = block.statements[0]
        assert isinstance(stmt, EventControl)
        assert len(stmt.events) == 1
        assert stmt.events[0].edge == "posedge"
        assert isinstance(stmt.body, NonblockingAssign)


class TestSystemTaskCall:
    """System task call extraction."""

    def test_display_with_args(self, parser):
        source = """module top;
initial begin
    $display("hello");
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.initial_blocks[0].body.statements[0]
        assert isinstance(stmt, SystemTaskCall)
        assert stmt.task_name == "$display"
        assert len(stmt.arguments) == 1

    def test_finish_no_args(self, parser):
        source = """module top;
initial begin
    $finish;
end
endmodule"""
        m = _parse_module(parser, source)
        stmt = m.initial_blocks[0].body.statements[0]
        assert isinstance(stmt, SystemTaskCall)
        assert stmt.task_name == "$finish"
        assert len(stmt.arguments) == 0


# ---- Multiple blocks ----


class TestMultipleBlocks:
    """Multiple always/initial blocks in one module."""

    def test_two_always_blocks(self, parser):
        source = """module top;
reg a, b;
wire clk, d1, d2;
always @(posedge clk) begin
    a <= d1;
end
always @(posedge clk) begin
    b <= d2;
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.always_blocks) == 2

    def test_always_and_initial(self, parser):
        source = """module top;
reg q;
wire clk, d;
initial begin
    q = 0;
end
always @(posedge clk) begin
    q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.initial_blocks) == 1
        assert len(m.always_blocks) == 1


# ---- Emitter round-trip ----


class TestBehavioralEmitter:
    """Test that emitted behavioral code re-parses correctly."""

    def test_always_comb_roundtrip(self, parser):
        source = """module top;
reg y;
wire a, b;
always @(*) begin
    y = a & b;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)

        # Should contain key keywords
        assert "always @(*)" in emitted
        assert "begin" in emitted
        assert "end" in emitted
        assert "y = a & b;" in emitted

    def test_always_seq_roundtrip(self, parser):
        source = """module top;
reg q;
wire clk, d;
always @(posedge clk) begin
    q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert "always @(posedge clk)" in emitted
        assert "q <= d;" in emitted

    def test_initial_roundtrip(self, parser):
        source = """module top;
reg [7:0] mem;
initial begin
    mem = 8'h00;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert "initial" in emitted
        assert "begin" in emitted
        assert "mem = 8'h00;" in emitted

    def test_if_else_roundtrip(self, parser):
        source = """module top;
reg q;
wire clk, rst, d;
always @(posedge clk or negedge rst) begin
    if (!rst)
        q <= 1'b0;
    else
        q <= d;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert "if (!rst)" in emitted
        assert "else" in emitted

    def test_case_roundtrip(self, parser):
        source = """module top;
reg [1:0] out;
wire [1:0] sel;
always @(*) begin
    case (sel)
        2'b00: out = 2'b01;
        default: out = 2'b00;
    endcase
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert "case (sel)" in emitted
        assert "endcase" in emitted
        assert "default:" in emitted

    def test_for_loop_roundtrip(self, parser):
        source = """module top;
integer i;
reg [7:0] mem [0:3];
initial begin
    for (i = 0; i < 4; i = i + 1)
        mem[i] = 8'h00;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert "for (" in emitted
        assert "i = 0" in emitted

    def test_system_task_roundtrip(self, parser):
        source = """module top;
initial begin
    $display("hello");
    $finish;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert '$display("hello");' in emitted
        assert "$finish;" in emitted

    def test_delay_roundtrip(self, parser):
        source = """module top;
reg clk;
initial begin
    clk = 0;
    #5 clk = 1;
end
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m, emit_comments=False)
        assert "#5" in emitted

    def test_reparse_emitted(self, parser):
        """Parse → emit → re-parse should produce equivalent model."""
        source = """module top;
reg y;
wire a, b;
always @(*) begin
    y = a & b;
end
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1, emit_comments=False)

        # Re-parse the emitted output
        tree2 = parser.build_tree(emitted)
        design2 = tree_to_design(tree2, source_file="test2.v")
        m2 = design2.modules[0]

        assert m2.name == m1.name
        assert len(m2.always_blocks) == len(m1.always_blocks)
        assert m2.always_blocks[0].sensitivity_type == m1.always_blocks[0].sensitivity_type

    def test_reparse_sequential(self, parser):
        """Parse → emit → re-parse for sequential always block."""
        source = """module top;
reg q;
wire clk, d;
always @(posedge clk) begin
    q <= d;
end
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1, emit_comments=False)

        tree2 = parser.build_tree(emitted)
        design2 = tree_to_design(tree2, source_file="test2.v")
        m2 = design2.modules[0]

        assert len(m2.always_blocks) == 1
        ab2 = m2.always_blocks[0]
        assert ab2.sensitivity_type == SensitivityType.SEQUENTIAL
        assert len(ab2.sensitivity_list) == 1
        assert ab2.sensitivity_list[0].edge == "posedge"


# ---- to_dict tests ----


class TestBehavioralToDict:
    """Verify to_dict() serialization for behavioral constructs."""

    def test_always_block_to_dict(self, parser):
        source = """module top;
reg y;
wire a;
always @(*) begin
    y = a;
end
endmodule"""
        m = _parse_module(parser, source)
        d = m.to_dict()
        assert "always_blocks" in d
        assert len(d["always_blocks"]) == 1
        ab = d["always_blocks"][0]
        assert ab["type"] == "AlwaysBlock"
        assert ab["sensitivity_type"] == "combinational"
        assert "body" in ab

    def test_initial_block_to_dict(self, parser):
        source = """module top;
reg x;
initial begin
    x = 0;
end
endmodule"""
        m = _parse_module(parser, source)
        d = m.to_dict()
        assert "initial_blocks" in d
        assert len(d["initial_blocks"]) == 1
        ib = d["initial_blocks"][0]
        assert ib["type"] == "InitialBlock"
        assert "body" in ib
