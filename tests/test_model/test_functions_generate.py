"""Tests for Phase 5: Functions, Tasks, and Generate Constructs.

Tests cover:
  - Function declaration extraction (simple, automatic, with return range/kind, ports)
  - Task declaration extraction (simple, automatic, with I/O ports)
  - Genvar declaration extraction
  - Generate-for loop extraction
  - Generate-if conditional extraction
  - Generate-case extraction
  - Emitter round-trip for all new constructs
  - to_dict() serialization
"""

# ruff: noqa: PLR2004

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal
from veriforge.model.functions import FunctionDecl, TaskDecl
from veriforge.model.generate import (
    GenerateBlock,
    GenerateCase,
    GenerateCaseItem,
    GenerateFor,
    GenerateIf,
    GenvarDecl,
)
from veriforge.model.ports import PortDirection
from veriforge.model.statements import BlockingAssign, IfStatement, SeqBlock
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str) -> Module:
    """Helper: parse source and return the first Module."""
    tree = parser.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    assert isinstance(design, Design)
    assert len(design.modules) == 1
    return design.modules[0]


# ---- Function declaration extraction ----


class TestFunctionBasic:
    """Basic function declaration parsing."""

    def test_simple_function(self, parser):
        """Parse a simple function with old-style port."""
        source = """module top;
function [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.functions) == 1
        fn = m.functions[0]
        assert isinstance(fn, FunctionDecl)
        assert fn.name == "add"
        assert fn.is_automatic is False

    def test_function_return_range(self, parser):
        """Function with explicit return range [7:0]."""
        source = """module top;
function [7:0] myfunc;
    input [7:0] a;
    begin
        myfunc = a;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.return_range is not None
        assert fn.return_kind is None

    def test_function_return_integer(self, parser):
        """Function with integer return type."""
        source = """module top;
function integer count;
    input [7:0] a;
    begin
        count = a;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.return_kind == "integer"

    def test_automatic_function(self, parser):
        """Automatic function declaration."""
        source = """module top;
function automatic [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.is_automatic is True

    def test_function_ports(self, parser):
        """Function should extract ports from tf_input_declaration."""
        source = """module top;
function [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert len(fn.ports) == 2
        assert fn.ports[0].name == "a"
        assert fn.ports[0].direction == PortDirection.INPUT
        assert fn.ports[1].name == "b"

    def test_function_body(self, parser):
        """Function body should be a statement."""
        source = """module top;
function [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.body is not None
        assert isinstance(fn.body, SeqBlock)

    def test_function_no_return_type(self, parser):
        """Function with no explicit return type (defaults to 1-bit)."""
        source = """module top;
function myfunc;
    input a;
    begin
        myfunc = a;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.name == "myfunc"
        # No explicit return type
        assert fn.return_range is None
        assert fn.return_kind is None

    def test_multiple_functions(self, parser):
        """Module with multiple functions."""
        source = """module top;
function [7:0] func1;
    input [7:0] a;
    begin
        func1 = a;
    end
endfunction
function [3:0] func2;
    input [3:0] b;
    begin
        func2 = b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.functions) == 2
        assert m.functions[0].name == "func1"
        assert m.functions[1].name == "func2"


class TestFunctionAnsiStyle:
    """ANSI-style function declarations with (port_list)."""

    def test_ansi_function(self, parser):
        """ANSI-style function with port list in parentheses."""
        source = """module top;
function [7:0] add (input [7:0] a, input [7:0] b);
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.name == "add"
        assert len(fn.ports) == 2
        assert fn.ports[0].name == "a"
        assert fn.ports[1].name == "b"

    def test_sv_function_ports_locals_and_return(self, parser):
        """SV ANSI-style function ports, local declarations, and return statements should be preserved."""
        source = """module top;
function automatic logic pick(logic [1:0] mode, logic flag);
    logic tmp = flag;
    if (mode[0]) tmp = ~tmp;
    return tmp;
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        assert fn.name == "pick"
        assert [p.name for p in fn.ports] == ["mode", "flag"]
        assert [p.data_type for p in fn.ports] == ["logic", "logic"]
        assert [v.name for v in fn.locals] == ["tmp"]
        assert isinstance(fn.body, SeqBlock)
        assert len(fn.body.statements) == 3
        assert isinstance(fn.body.statements[0], BlockingAssign)
        assert fn.body.statements[0].lhs.name == "tmp"
        assert isinstance(fn.body.statements[1], IfStatement)
        assert isinstance(fn.body.statements[2], BlockingAssign)
        assert fn.body.statements[2].lhs.name == "pick"


# ---- Task declaration extraction ----


class TestTaskBasic:
    """Basic task declaration parsing."""

    def test_simple_task(self, parser):
        """Parse a simple task."""
        source = """module top;
task my_task;
    input [7:0] data;
    begin
        $display("data = %h", data);
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.tasks) == 1
        tk = m.tasks[0]
        assert isinstance(tk, TaskDecl)
        assert tk.name == "my_task"
        assert tk.is_automatic is False

    def test_automatic_task(self, parser):
        """Automatic task declaration."""
        source = """module top;
task automatic my_task;
    input [7:0] data;
    begin
        $display("data = %h", data);
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        tk = m.tasks[0]
        assert tk.is_automatic is True

    def test_task_ports(self, parser):
        """Task with input and output ports."""
        source = """module top;
task add_task;
    input [7:0] a;
    input [7:0] b;
    output [7:0] result;
    begin
        result = a + b;
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        tk = m.tasks[0]
        assert len(tk.ports) == 3
        assert tk.ports[0].name == "a"
        assert tk.ports[0].direction == PortDirection.INPUT
        assert tk.ports[2].name == "result"
        assert tk.ports[2].direction == PortDirection.OUTPUT

    def test_task_body(self, parser):
        """Task body should be a statement."""
        source = """module top;
task my_task;
    input a;
    begin
        $display("hello");
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        tk = m.tasks[0]
        assert tk.body is not None

    def test_task_ansi_style(self, parser):
        """ANSI-style task with port list in parentheses."""
        source = """module top;
task my_task (input [7:0] a, output [7:0] b);
    begin
        b = a;
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        tk = m.tasks[0]
        assert tk.name == "my_task"
        assert len(tk.ports) == 2


# ---- Genvar declaration ----


class TestGenvarDecl:
    """Genvar declaration parsing."""

    def test_single_genvar(self, parser):
        """Parse single genvar declaration."""
        source = """module top;
genvar i;
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.generate_blocks) >= 1
        gv = None
        for g in m.generate_blocks:
            if isinstance(g, GenvarDecl):
                gv = g
                break
        assert gv is not None
        assert gv.names == ["i"]

    def test_multiple_genvars(self, parser):
        """Parse multiple genvars in one declaration."""
        source = """module top;
genvar i, j, k;
endmodule"""
        m = _parse_module(parser, source)
        gv = None
        for g in m.generate_blocks:
            if isinstance(g, GenvarDecl):
                gv = g
                break
        assert gv is not None
        assert gv.names == ["i", "j", "k"]


# ---- Generate-for loop ----


class TestGenerateFor:
    """Generate-for loop extraction."""

    def test_simple_generate_for(self, parser):
        """Parse a simple generate-for loop."""
        source = """module top;
genvar i;
generate
    for (i = 0; i < 4; i = i + 1) begin : gen_blk
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_fors = [g for g in m.generate_blocks if isinstance(g, GenerateFor)]
        assert len(gen_fors) == 1
        gf = gen_fors[0]
        assert gf.genvar == "i"
        assert isinstance(gf.init_value, Literal)
        assert isinstance(gf.condition, BinaryOp)
        assert isinstance(gf.update, BinaryOp)
        assert isinstance(gf.body, GenerateBlock)
        assert gf.body.name == "gen_blk"

    def test_generate_for_with_instances(self, parser):
        """Generate-for containing module instances."""
        source = """module top;
genvar i;
generate
    for (i = 0; i < 4; i = i + 1) begin : gen_inst
        buf u_buf (out[i], in[i]);
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_fors = [g for g in m.generate_blocks if isinstance(g, GenerateFor)]
        assert len(gen_fors) == 1
        gf = gen_fors[0]
        assert gf.body.name == "gen_inst"

    def test_generate_for_body_items(self, parser):
        """Generate-for body should contain module items."""
        source = """module top;
genvar i;
generate
    for (i = 0; i < 4; i = i + 1) begin : gen_blk
        wire [7:0] data;
        assign data = 8'hFF;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_fors = [g for g in m.generate_blocks if isinstance(g, GenerateFor)]
        assert len(gen_fors) == 1
        gf = gen_fors[0]
        assert len(gf.body.items) >= 1

    def test_generate_for_condition_parts(self, parser):
        """Verify init, condition, update expressions are extracted."""
        source = """module top;
genvar i;
generate
    for (i = 0; i < 8; i = i + 1) begin : gen_blk
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gf = next(g for g in m.generate_blocks if isinstance(g, GenerateFor))
        # init_value should be 0
        assert isinstance(gf.init_value, Literal)
        # condition should be i < 8
        assert isinstance(gf.condition, BinaryOp)
        assert gf.condition.op == "<"
        # update should be i + 1
        assert isinstance(gf.update, BinaryOp)
        assert gf.update.op == "+"


# ---- Generate-if ----


class TestGenerateIf:
    """Generate-if conditional extraction."""

    def test_simple_generate_if(self, parser):
        """Parse a simple generate-if."""
        source = """module top;
parameter MODE = 1;
generate
    if (MODE == 1) begin : gen_true
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_ifs = [g for g in m.generate_blocks if isinstance(g, GenerateIf)]
        assert len(gen_ifs) == 1
        gi = gen_ifs[0]
        assert isinstance(gi.condition, BinaryOp)
        assert gi.then_body is not None
        assert isinstance(gi.then_body, GenerateBlock)
        assert gi.then_body.name == "gen_true"

    def test_generate_if_else(self, parser):
        """Parse generate-if with else branch."""
        source = """module top;
parameter MODE = 0;
generate
    if (MODE == 1) begin : gen_true
        wire x;
    end else begin : gen_false
        wire y;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_ifs = [g for g in m.generate_blocks if isinstance(g, GenerateIf)]
        assert len(gen_ifs) == 1
        gi = gen_ifs[0]
        assert gi.then_body is not None
        assert gi.then_body.name == "gen_true"
        assert gi.else_body is not None
        assert gi.else_body.name == "gen_false"

    def test_generate_if_no_else(self, parser):
        """Generate-if without else should have else_body=None."""
        source = """module top;
parameter MODE = 1;
generate
    if (MODE) begin : gen_blk
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_ifs = [g for g in m.generate_blocks if isinstance(g, GenerateIf)]
        gi = gen_ifs[0]
        assert gi.else_body is None


# ---- Generate-case ----


class TestGenerateCase:
    """Generate-case extraction."""

    def test_simple_generate_case(self, parser):
        """Parse a generate-case construct."""
        source = """module top;
parameter MODE = 0;
generate
    case (MODE)
        0: begin : gen_mode0
            wire x;
        end
        1: begin : gen_mode1
            wire y;
        end
        default: begin : gen_default
            wire z;
        end
    endcase
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gen_cases = [g for g in m.generate_blocks if isinstance(g, GenerateCase)]
        assert len(gen_cases) == 1
        gc = gen_cases[0]
        assert isinstance(gc.expression, Identifier)
        assert len(gc.items) == 3

    def test_generate_case_items(self, parser):
        """Verify case item details."""
        source = """module top;
parameter MODE = 0;
generate
    case (MODE)
        0: begin : gen_mode0
            wire x;
        end
        default: begin : gen_def
            wire y;
        end
    endcase
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gc = next(g for g in m.generate_blocks if isinstance(g, GenerateCase))
        assert len(gc.items) == 2
        # First item: value 0
        item0 = gc.items[0]
        assert isinstance(item0, GenerateCaseItem)
        assert item0.is_default is False
        assert len(item0.values) == 1
        # Second item: default
        item1 = gc.items[1]
        assert item1.is_default is True


# ---- to_dict serialization ----


class TestSerialization:
    """Test to_dict() for new model classes."""

    def test_function_to_dict(self, parser):
        source = """module top;
function [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        fn = m.functions[0]
        d = fn.to_dict()
        assert d["type"] == "FunctionDecl"
        assert d["name"] == "add"
        assert d["is_automatic"] is False
        assert "return_range" in d
        assert "ports" in d
        assert len(d["ports"]) == 2

    def test_task_to_dict(self, parser):
        source = """module top;
task my_task;
    input [7:0] data;
    begin
        $display("data");
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        tk = m.tasks[0]
        d = tk.to_dict()
        assert d["type"] == "TaskDecl"
        assert d["name"] == "my_task"
        assert "ports" in d

    def test_genvar_to_dict(self, parser):
        source = """module top;
genvar i, j;
endmodule"""
        m = _parse_module(parser, source)
        gv = next(g for g in m.generate_blocks if isinstance(g, GenvarDecl))
        d = gv.to_dict()
        assert d["type"] == "GenvarDecl"
        assert d["names"] == ["i", "j"]

    def test_generate_for_to_dict(self, parser):
        source = """module top;
genvar i;
generate
    for (i = 0; i < 4; i = i + 1) begin : gen_blk
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gf = next(g for g in m.generate_blocks if isinstance(g, GenerateFor))
        d = gf.to_dict()
        assert d["type"] == "GenerateFor"
        assert d["genvar"] == "i"
        assert "body" in d
        assert "condition" in d

    def test_generate_if_to_dict(self, parser):
        source = """module top;
parameter MODE = 1;
generate
    if (MODE == 1) begin : gen_true
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        gi = next(g for g in m.generate_blocks if isinstance(g, GenerateIf))
        d = gi.to_dict()
        assert d["type"] == "GenerateIf"
        assert "condition" in d
        assert "then_body" in d

    def test_module_to_dict_includes_new_fields(self, parser):
        """Module.to_dict() should include functions, tasks, generate_blocks."""
        source = """module top;
function [7:0] myfunc;
    input [7:0] a;
    begin
        myfunc = a;
    end
endfunction
task mytask;
    input b;
    begin
        $display("b");
    end
endtask
genvar i;
endmodule"""
        m = _parse_module(parser, source)
        d = m.to_dict()
        assert "functions" in d
        assert len(d["functions"]) == 1
        assert "tasks" in d
        assert len(d["tasks"]) == 1
        assert "generate_blocks" in d
        assert len(d["generate_blocks"]) >= 1


# ---- Emitter round-trip ----


class TestEmitterRoundTrip:
    """Test emit → re-parse round-trip for new constructs."""

    def test_function_emit(self, parser):
        """Emit a parsed function and verify structure."""
        source = """module top;
function [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m)
        assert "function" in emitted
        assert "endfunction" in emitted
        assert "add" in emitted

    def test_function_roundtrip(self, parser):
        """Parse, emit, re-parse a function — structure should match."""
        source = """module top;
function [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1)
        m2 = _parse_module(parser, emitted)
        assert len(m2.functions) == 1
        assert m2.functions[0].name == "add"
        assert len(m2.functions[0].ports) == len(m1.functions[0].ports)

    def test_task_emit(self, parser):
        """Emit a parsed task and verify keywords present."""
        source = """module top;
task my_task;
    input [7:0] data;
    begin
        $display("hello");
    end
endtask
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m)
        assert "task" in emitted
        assert "endtask" in emitted
        assert "my_task" in emitted

    def test_task_roundtrip(self, parser):
        """Parse, emit, re-parse a task."""
        source = """module top;
task my_task;
    input [7:0] data;
    begin
        $display("hello");
    end
endtask
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1)
        m2 = _parse_module(parser, emitted)
        assert len(m2.tasks) == 1
        assert m2.tasks[0].name == "my_task"

    def test_genvar_emit(self, parser):
        """Emit genvar declaration."""
        source = """module top;
genvar i, j;
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m)
        assert "genvar" in emitted
        assert "i" in emitted

    def test_generate_for_emit(self, parser):
        """Emit a generate-for loop."""
        source = """module top;
genvar i;
generate
    for (i = 0; i < 4; i = i + 1) begin : gen_blk
        wire x;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m)
        assert "for" in emitted
        assert "gen_blk" in emitted
        assert "begin" in emitted

    def test_generate_for_roundtrip(self, parser):
        """Parse, emit, re-parse generate-for."""
        source = """module top;
genvar i;
generate
    for (i = 0; i < 4; i = i + 1) begin : gen_blk
        wire x;
    end
endgenerate
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1)
        m2 = _parse_module(parser, emitted)
        gen_fors = [g for g in m2.generate_blocks if isinstance(g, GenerateFor)]
        assert len(gen_fors) == 1
        assert gen_fors[0].genvar == "i"

    def test_generate_if_emit(self, parser):
        """Emit a generate-if."""
        source = """module top;
parameter MODE = 1;
generate
    if (MODE == 1) begin : gen_true
        wire x;
    end else begin : gen_false
        wire y;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m)
        assert "if" in emitted
        assert "else" in emitted
        assert "gen_true" in emitted
        assert "gen_false" in emitted

    def test_generate_case_emit(self, parser):
        """Emit a generate-case."""
        source = """module top;
parameter MODE = 0;
generate
    case (MODE)
        0: begin : gen_mode0
            wire x;
        end
        default: begin : gen_def
            wire y;
        end
    endcase
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        emitted = emit_module(m)
        assert "case" in emitted
        assert "endcase" in emitted
        assert "gen_mode0" in emitted

    def test_automatic_function_roundtrip(self, parser):
        """Automatic function should survive round-trip."""
        source = """module top;
function automatic [7:0] add;
    input [7:0] a;
    input [7:0] b;
    begin
        add = a + b;
    end
endfunction
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1)
        assert "automatic" in emitted
        m2 = _parse_module(parser, emitted)
        assert m2.functions[0].is_automatic is True

    def test_integer_function_roundtrip(self, parser):
        """Function with integer return type should survive round-trip."""
        source = """module top;
function integer count;
    input [7:0] a;
    begin
        count = a;
    end
endfunction
endmodule"""
        m1 = _parse_module(parser, source)
        emitted = emit_module(m1)
        assert "integer" in emitted
        m2 = _parse_module(parser, emitted)
        assert m2.functions[0].return_kind == "integer"


# ---- Mixed constructs ----


class TestMixedConstructs:
    """Test modules with multiple types of Phase 5 constructs."""

    def test_function_and_always(self, parser):
        """Module with both function and always block."""
        source = """module top;
reg [7:0] result;
wire [7:0] a, b;
function [7:0] add;
    input [7:0] x;
    input [7:0] y;
    begin
        add = x + y;
    end
endfunction
always @(*) begin
    result = add(a, b);
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.functions) == 1
        assert len(m.always_blocks) == 1

    def test_task_and_initial(self, parser):
        """Module with both task and initial block."""
        source = """module top;
reg [7:0] data;
task set_data;
    input [7:0] val;
    begin
        data = val;
    end
endtask
initial begin
    set_data(8'hFF);
end
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.tasks) == 1
        assert len(m.initial_blocks) == 1

    def test_generate_with_parameters(self, parser):
        """Generate constructs alongside parameters."""
        source = """module top;
parameter N = 4;
genvar i;
generate
    for (i = 0; i < N; i = i + 1) begin : gen_blk
        wire data;
    end
endgenerate
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.parameters) >= 1
        gen_fors = [g for g in m.generate_blocks if isinstance(g, GenerateFor)]
        assert len(gen_fors) == 1
