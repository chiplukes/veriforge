"""Tests for user-defined function and task simulation.

Covers:
  - Function call in always block  (reference, VM, compiled)
  - Function with explicit return width
  - Function with integer return kind
  - Task with input-only ports
  - Task with output ports (value copy-back)
  - Function called from continuous assign
  - Nested function calls
"""

import pytest

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import (
    BinaryOp,
    FunctionCall,
    Identifier,
    Literal,
    Range,
)
from veriforge.model.functions import FunctionDecl, TaskDecl
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    DelayControl,
    SeqBlock,
    TaskEnable,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value


R8 = Range(Literal(7, width=32), Literal(0, width=32))


# ── Helper builders ─────────────────────────────────────────────────


def _module_with_function_in_always() -> Module:
    """Module with a function called inside an always @(*) block.

    function [7:0] add_one;
      input [7:0] x;
      add_one = x + 1;
    endfunction

    always @(*) y = add_one(a);
    """
    func = FunctionDecl(
        "add_one",
        return_range=R8,
        ports=[Port("x", PortDirection.INPUT, width=R8)],
        body=BlockingAssign(
            Identifier("add_one"),
            BinaryOp("+", Identifier("x"), Literal(1, width=8)),
        ),
    )
    m = Module(
        "dut",
        ports=[
            Port("a", PortDirection.INPUT, width=R8),
            Port("y", PortDirection.OUTPUT, width=R8),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=R8),
        ],
    )
    m.variables.append(Variable("y", VariableKind.REG, width=R8))
    m.functions.append(func)
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("y"),
                FunctionCall("add_one", [Identifier("a")]),
            ),
        )
    )
    return m


def _module_with_function_in_continuous() -> Module:
    """Module with function called from continuous assign.

    function [7:0] double; input [7:0] x; double = x << 1; endfunction
    assign y = double(a);
    """
    func = FunctionDecl(
        "double",
        return_range=R8,
        ports=[Port("x", PortDirection.INPUT, width=R8)],
        body=BlockingAssign(
            Identifier("double"),
            BinaryOp("<<", Identifier("x"), Literal(1, width=8)),
        ),
    )
    m = Module(
        "dut",
        ports=[
            Port("a", PortDirection.INPUT, width=R8),
            Port("y", PortDirection.OUTPUT, width=R8),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=R8),
            Net("y", NetKind.WIRE, width=R8),
        ],
    )
    m.functions.append(func)
    m.continuous_assigns.append(
        ContinuousAssign(
            Identifier("y"),
            FunctionCall("double", [Identifier("a")]),
        )
    )
    return m


def _module_with_integer_function() -> Module:
    """Module with integer-returning function.

    function integer count_bits; input [7:0] val;
      count_bits = val & 4'hf;
    endfunction

    always @(*) y = count_bits(a);
    """
    func = FunctionDecl(
        "count_bits",
        return_kind="integer",
        ports=[Port("val", PortDirection.INPUT, width=R8)],
        body=BlockingAssign(
            Identifier("count_bits"),
            BinaryOp("&", Identifier("val"), Literal(0xF, width=32)),
        ),
    )
    m = Module(
        "dut",
        ports=[
            Port("a", PortDirection.INPUT, width=R8),
            Port("y", PortDirection.OUTPUT, width=R8),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=R8),
        ],
    )
    m.variables.append(Variable("y", VariableKind.REG, width=R8))
    m.functions.append(func)
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("y"),
                FunctionCall("count_bits", [Identifier("a")]),
            ),
        )
    )
    return m


def _module_with_task_input_only() -> Module:
    """Module with task that has input-only ports.

    task set_val;
      input [7:0] v;
      y = v;
    endtask

    initial begin
      #10 set_val(42);
    end
    """
    task = TaskDecl(
        "set_val",
        ports=[Port("v", PortDirection.INPUT, width=R8)],
        body=BlockingAssign(Identifier("y"), Identifier("v")),
    )
    m = Module(
        "dut",
        ports=[
            Port("y", PortDirection.OUTPUT, width=R8),
        ],
    )
    m.variables.append(Variable("y", VariableKind.REG, width=R8))
    m.tasks.append(task)
    m.initial_blocks.append(
        InitialBlock(
            body=SeqBlock(
                statements=[
                    DelayControl(Literal(10, width=32)),
                    TaskEnable("set_val", [Literal(42, width=8)]),
                ]
            )
        )
    )
    return m


def _module_with_task_output() -> Module:
    """Module with task that has an output port.

    task compute;
      input [7:0] a_in;
      output [7:0] result;
      result = a_in + 10;
    endtask

    initial begin
      #10 compute(5, y);
    end
    """
    task = TaskDecl(
        "compute",
        ports=[
            Port("a_in", PortDirection.INPUT, width=R8),
            Port("result", PortDirection.OUTPUT, width=R8),
        ],
        body=BlockingAssign(
            Identifier("result"),
            BinaryOp("+", Identifier("a_in"), Literal(10, width=8)),
        ),
    )
    m = Module(
        "dut",
        ports=[
            Port("y", PortDirection.OUTPUT, width=R8),
        ],
    )
    m.variables.append(Variable("y", VariableKind.REG, width=R8))
    m.tasks.append(task)
    m.initial_blocks.append(
        InitialBlock(
            body=SeqBlock(
                statements=[
                    DelayControl(Literal(10, width=32)),
                    TaskEnable("compute", [Literal(5, width=8), Identifier("y")]),
                ]
            )
        )
    )
    return m


def _module_with_nested_functions() -> Module:
    """Module with nested function calls.

    function [7:0] inc; input [7:0] x; inc = x + 1; endfunction
    function [7:0] double_inc; input [7:0] x; double_inc = inc(x) + inc(x); endfunction
    always @(*) y = double_inc(a);
    """
    func_inc = FunctionDecl(
        "inc",
        return_range=R8,
        ports=[Port("x", PortDirection.INPUT, width=R8)],
        body=BlockingAssign(
            Identifier("inc"),
            BinaryOp("+", Identifier("x"), Literal(1, width=8)),
        ),
    )
    func_double_inc = FunctionDecl(
        "double_inc",
        return_range=R8,
        ports=[Port("x", PortDirection.INPUT, width=R8)],
        body=BlockingAssign(
            Identifier("double_inc"),
            BinaryOp(
                "+",
                FunctionCall("inc", [Identifier("x")]),
                FunctionCall("inc", [Identifier("x")]),
            ),
        ),
    )
    m = Module(
        "dut",
        ports=[
            Port("a", PortDirection.INPUT, width=R8),
            Port("y", PortDirection.OUTPUT, width=R8),
        ],
        nets=[
            Net("a", NetKind.WIRE, width=R8),
        ],
    )
    m.variables.append(Variable("y", VariableKind.REG, width=R8))
    m.functions.extend([func_inc, func_double_inc])
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("y"),
                FunctionCall("double_inc", [Identifier("a")]),
            ),
        )
    )
    return m


# ── Reference engine tests ──────────────────────────────────────────


class TestFunctionRef:
    def test_function_in_always(self):
        m = _module_with_function_in_always()
        sim = Simulator(m, engine="reference")

        def test(s):
            s.drive("a", Value(10, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 11

    def test_function_in_continuous(self):
        m = _module_with_function_in_continuous()
        sim = Simulator(m, engine="reference")

        def test(s):
            s.drive("a", Value(5, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 10

    def test_integer_function(self):
        m = _module_with_integer_function()
        sim = Simulator(m, engine="reference")

        def test(s):
            s.drive("a", Value(0xAB, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 0x0B  # 0xAB & 0x0F

    def test_nested_function_calls(self):
        m = _module_with_nested_functions()
        sim = Simulator(m, engine="reference")

        def test(s):
            s.drive("a", Value(3, width=8))

        sim.run(test, max_time=0)
        # double_inc(3) = inc(3) + inc(3) = 4 + 4 = 8
        assert sim.read("y") == 8


class TestTaskRef:
    def test_task_input_only(self):
        m = _module_with_task_input_only()
        sim = Simulator(m, engine="reference")
        sim.run(max_time=20)
        assert sim.read("y") == 42

    def test_task_output(self):
        m = _module_with_task_output()
        sim = Simulator(m, engine="reference")
        sim.run(max_time=20)
        # compute(5, y) → result = 5 + 10 = 15 → y = 15
        assert sim.read("y") == 15


# ── VM engine tests ──────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestFunctionVM:
    def test_function_in_always(self, engine):
        m = _module_with_function_in_always()
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("a", Value(10, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 11

    def test_function_in_continuous(self, engine):
        m = _module_with_function_in_continuous()
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("a", Value(5, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 10

    def test_integer_function(self, engine):
        m = _module_with_integer_function()
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("a", Value(0xAB, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 0x0B

    def test_nested_function_calls(self, engine):
        m = _module_with_nested_functions()
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("a", Value(3, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 8


@pytest.mark.parametrize("engine", ["vm", "vm-fast"])
class TestTaskVM:
    def test_task_input_only(self, engine):
        m = _module_with_task_input_only()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=20)
        assert sim.read("y") == 42

    def test_task_output(self, engine):
        m = _module_with_task_output()
        sim = Simulator(m, engine=engine)
        sim.run(max_time=20)
        assert sim.read("y") == 15


# ── Compiled engine tests ───────────────────────────────────────────


class TestFunctionCompiled:
    def test_function_in_always(self):
        m = _module_with_function_in_always()
        sim = Simulator(m, engine="compiled")

        def test(s):
            s.drive("a", Value(10, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 11

    def test_function_in_continuous(self):
        m = _module_with_function_in_continuous()
        sim = Simulator(m, engine="compiled")

        def test(s):
            s.drive("a", Value(5, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 10

    def test_integer_function(self):
        m = _module_with_integer_function()
        sim = Simulator(m, engine="compiled")

        def test(s):
            s.drive("a", Value(0xAB, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 0x0B


class TestTaskCompiled:
    def test_task_input_only(self):
        m = _module_with_task_input_only()
        sim = Simulator(m, engine="compiled")
        sim.run(max_time=20)
        assert sim.read("y") == 42

    def test_task_output(self):
        m = _module_with_task_output()
        sim = Simulator(m, engine="compiled")
        sim.run(max_time=20)
        assert sim.read("y") == 15


# ── Cross-engine validation ─────────────────────────────────────────


class TestCrossEngine:
    """Verify all engines agree on function/task results."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_function_add_one(self, engine):
        m = _module_with_function_in_always()
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("a", Value(99, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 100

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_function_double(self, engine):
        m = _module_with_function_in_continuous()
        sim = Simulator(m, engine=engine)

        def test(s):
            s.drive("a", Value(33, width=8))

        sim.run(test, max_time=0)
        assert sim.read("y") == 66
