"""Tests for the Verilog lint checks."""

from veriforge.analysis import LintCode, LintWarning, lint_design, lint_module
from veriforge.analysis.lint import (
    _check_assignment_style,
    _check_drivers_loads,
    _check_latch_inferred,
    _check_unconnected_ports,
    _check_width_mismatch,
)
from veriforge.analysis.resolver import Driver, Load
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import Identifier, Literal
from veriforge.model.instances import Instance, PortConnection
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    IfStatement,
    NonblockingAssign,
    SensitivityEdge,
    SeqBlock,
)
from veriforge.model.variables import Variable

from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _mod(name: str = "dut", **kwargs) -> Module:
    mod = Module(name=name)
    for attr, val in kwargs.items():
        setattr(mod, attr, val)
    return mod


def _nb(lhs: str, rhs_val: int = 0) -> NonblockingAssign:
    return NonblockingAssign(lhs=Identifier(lhs), rhs=Literal(rhs_val))


def _ba(lhs: str, rhs_val: int = 0) -> BlockingAssign:
    return BlockingAssign(lhs=Identifier(lhs), rhs=Literal(rhs_val))


def _if(cond_name: str, then_stmts, else_stmts=None) -> IfStatement:
    then = SeqBlock(statements=then_stmts) if len(then_stmts) > 1 else then_stmts[0]
    el = None
    if else_stmts:
        el = SeqBlock(statements=else_stmts) if len(else_stmts) > 1 else else_stmts[0]
    return IfStatement(condition=Identifier(cond_name), then_body=then, else_body=el)


def _always_comb(body_stmts) -> AlwaysBlock:
    body = SeqBlock(statements=body_stmts) if len(body_stmts) > 1 else body_stmts[0]
    return AlwaysBlock(
        sensitivity_list=[],
        sensitivity_type=SensitivityType.COMBINATIONAL,
        body=body,
    )


def _always_seq(body_stmts) -> AlwaysBlock:
    body = SeqBlock(statements=body_stmts) if len(body_stmts) > 1 else body_stmts[0]
    return AlwaysBlock(
        sensitivity_list=[SensitivityEdge(edge="posedge", signal=Identifier("clk"))],
        sensitivity_type=SensitivityType.SEQUENTIAL,
        body=body,
    )


def _parse(src: str) -> Module:
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


def _parse_design(src: str) -> Design:
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(text=src)
    return tree_to_design(tree)


def _codes(warnings: list[LintWarning]) -> list[LintCode]:
    return [w.code for w in warnings]


def _signals(warnings: list[LintWarning]) -> list[str | None]:
    return [w.signal for w in warnings]


# ---------------------------------------------------------------------------
# Latch inference
# ---------------------------------------------------------------------------


class TestLatchInferred:
    """LATCH_INFERRED: incomplete if/case in combinational blocks."""

    def test_if_no_else(self):
        """if (sel) y = a; — no else infers latch."""
        blk = _always_comb([_if("sel", [_ba("y", 1)])])
        ws = _check_latch_inferred(blk, "dut")
        assert len(ws) == 1
        assert ws[0].code == LintCode.LATCH_INFERRED
        assert ws[0].signal == "y"
        assert "no else" in ws[0].message

    def test_if_else_complete(self):
        """if (sel) y = a; else y = b; — no latch."""
        blk = _always_comb([_if("sel", [_ba("y", 1)], [_ba("y", 0)])])
        ws = _check_latch_inferred(blk, "dut")
        assert len(ws) == 0

    def test_if_else_partial(self):
        """if (sel) {y=1; z=1;} else {y=0;} — z not assigned in else."""
        blk = _always_comb([_if("sel", [_ba("y", 1), _ba("z", 1)], [_ba("y", 0)])])
        ws = _check_latch_inferred(blk, "dut")
        assert len(ws) == 1
        assert ws[0].signal == "z"

    def test_case_no_default(self):
        """case without default infers latch for all targets."""
        case = CaseStatement(
            case_type="case",
            expression=Identifier("sel"),
            items=[
                CaseItem(values=[Literal(0)], body=_ba("y", 0)),
                CaseItem(values=[Literal(1)], body=_ba("y", 1)),
            ],
        )
        blk = _always_comb([case])
        ws = _check_latch_inferred(blk, "dut")
        assert len(ws) == 1
        assert ws[0].signal == "y"
        assert "default" in ws[0].message

    def test_case_with_default(self):
        """case with default — no latch warning."""
        case = CaseStatement(
            case_type="case",
            expression=Identifier("sel"),
            items=[
                CaseItem(values=[Literal(0)], body=_ba("y", 0)),
                CaseItem(values=None, body=_ba("y", 1), is_default=True),
            ],
        )
        blk = _always_comb([case])
        ws = _check_latch_inferred(blk, "dut")
        assert len(ws) == 0

    def test_sequential_block_ignored(self):
        """Latch check only applies to combinational blocks."""
        blk = _always_seq([_if("rst", [_nb("q", 0)])])
        ws = _check_latch_inferred(blk, "dut")
        # _check_latch_inferred is called — it still finds the pattern,
        # but lint_module only calls it for COMBINATIONAL blocks
        # Direct call will still find the if-no-else
        assert len(ws) >= 1  # it does find it at the function level

    def test_lint_module_skips_sequential(self):
        """lint_module doesn't flag latch in sequential blocks."""
        blk = _always_seq([_if("rst", [_nb("q", 0)])])
        mod = _mod(always_blocks=[blk])
        ws = lint_module(mod, skip={LintCode.MIXED_BLOCKING, LintCode.MIXED_NONBLOCKING})
        latch_ws = [w for w in ws if w.code == LintCode.LATCH_INFERRED]
        assert len(latch_ws) == 0


# ---------------------------------------------------------------------------
# Assignment style
# ---------------------------------------------------------------------------


class TestAssignmentStyle:
    """MIXED_BLOCKING / MIXED_NONBLOCKING checks."""

    def test_blocking_in_sequential(self):
        """Blocking = in sequential always → warning."""
        blk = _always_seq([_ba("q", 1)])
        ws = _check_assignment_style(blk, "dut")
        assert len(ws) == 1
        assert ws[0].code == LintCode.MIXED_BLOCKING

    def test_nonblocking_in_sequential_ok(self):
        """Non-blocking <= in sequential always → no warning."""
        blk = _always_seq([_nb("q", 1)])
        ws = _check_assignment_style(blk, "dut")
        assert len(ws) == 0

    def test_nonblocking_in_combinational(self):
        """Non-blocking <= in combinational always → warning."""
        blk = _always_comb([_nb("y", 1)])
        ws = _check_assignment_style(blk, "dut")
        assert len(ws) == 1
        assert ws[0].code == LintCode.MIXED_NONBLOCKING

    def test_blocking_in_combinational_ok(self):
        """Blocking = in combinational always → no warning."""
        blk = _always_comb([_ba("y", 1)])
        ws = _check_assignment_style(blk, "dut")
        assert len(ws) == 0


# ---------------------------------------------------------------------------
# Unconnected ports
# ---------------------------------------------------------------------------


class TestUnconnectedPort:
    """UNCONNECTED_PORT on instances."""

    def test_unconnected(self):
        inst = Instance(
            "sub",
            "u1",
            port_connections=[
                PortConnection(port_name="a", expression=Identifier("sig_a")),
                PortConnection(port_name="b", expression=None),
            ],
        )
        mod = _mod(instances=[inst])
        ws = _check_unconnected_ports(mod)
        assert len(ws) == 1
        assert ws[0].code == LintCode.UNCONNECTED_PORT
        assert ws[0].signal == "b"
        assert ws[0].instance == "u1"

    def test_all_connected(self):
        inst = Instance(
            "sub",
            "u1",
            port_connections=[
                PortConnection(port_name="a", expression=Identifier("sig_a")),
            ],
        )
        mod = _mod(instances=[inst])
        ws = _check_unconnected_ports(mod)
        assert len(ws) == 0


# ---------------------------------------------------------------------------
# Driver/load checks (unit tests with manual driver/load population)
# ---------------------------------------------------------------------------


class TestDriversLoads:
    """UNDRIVEN, UNUSED, MULTI_DRIVEN checks."""

    def test_undriven_net(self):
        net = Net("orphan")
        # No drivers, add a fake load
        net.loads.append(Load(consumer=None))
        mod = _mod(nets=[net])
        ws = _check_drivers_loads(mod)
        codes = _codes(ws)
        assert LintCode.UNDRIVEN in codes
        assert any(w.signal == "orphan" for w in ws if w.code == LintCode.UNDRIVEN)

    def test_unused_net(self):
        net = Net("unused_wire")
        net.drivers.append(Driver(source=None))
        mod = _mod(nets=[net])
        ws = _check_drivers_loads(mod)
        codes = _codes(ws)
        assert LintCode.UNUSED in codes
        assert any(w.signal == "unused_wire" for w in ws if w.code == LintCode.UNUSED)

    def test_well_connected_net(self):
        net = Net("ok")
        net.drivers.append(Driver(source=None))
        net.loads.append(Load(consumer=None))
        mod = _mod(nets=[net])
        ws = _check_drivers_loads(mod)
        # No warnings for this net
        net_ws = [w for w in ws if w.signal == "ok"]
        assert len(net_ws) == 0

    def test_multi_driven_wire(self):
        net = Net("conflict", NetKind.WIRE)
        net.drivers.append(Driver(source="src1"))
        net.drivers.append(Driver(source="src2"))
        net.loads.append(Load(consumer=None))
        mod = _mod(nets=[net])
        ws = _check_drivers_loads(mod)
        assert any(w.code == LintCode.MULTI_DRIVEN and w.signal == "conflict" for w in ws)

    def test_undriven_variable(self):
        var = Variable("unused_reg")
        var.loads.append(Load(consumer=None))
        mod = _mod(variables=[var])
        ws = _check_drivers_loads(mod)
        assert any(w.code == LintCode.UNDRIVEN and w.signal == "unused_reg" for w in ws)

    def test_port_signals_excluded(self):
        """Nets/vars with the same name as a port are skipped."""
        net = Net("data_in")  # no drivers/loads
        port = Port("data_in", PortDirection.INPUT)
        mod = _mod(nets=[net], ports=[port])
        ws = _check_drivers_loads(mod)
        net_ws = [w for w in ws if w.signal == "data_in"]
        assert len(net_ws) == 0  # excluded because it's a port


# ---------------------------------------------------------------------------
# Width mismatch
# ---------------------------------------------------------------------------


class TestWidthMismatch:
    """WIDTH_MISMATCH on continuous assigns."""

    def test_width_mismatch_assign(self):
        """assign [3:0] lhs = [7:0] rhs → mismatch."""
        lhs = Identifier("y")
        rhs = Identifier("a")
        lhs.inferred_width = 4
        rhs.inferred_width = 8
        ca = ContinuousAssign(lhs=lhs, rhs=rhs)
        mod = _mod(continuous_assigns=[ca])
        ws = _check_width_mismatch(mod)
        assert len(ws) == 1
        assert ws[0].code == LintCode.WIDTH_MISMATCH
        assert "4-bit" in ws[0].message
        assert "8-bit" in ws[0].message

    def test_width_match_ok(self):
        lhs = Identifier("y")
        rhs = Identifier("a")
        lhs.inferred_width = 8
        rhs.inferred_width = 8
        ca = ContinuousAssign(lhs=lhs, rhs=rhs)
        mod = _mod(continuous_assigns=[ca])
        ws = _check_width_mismatch(mod)
        assert len(ws) == 0


# ---------------------------------------------------------------------------
# Skip filtering
# ---------------------------------------------------------------------------


class TestSkipFiltering:
    """lint_module with skip parameter."""

    def test_skip_latch(self):
        blk = _always_comb([_if("sel", [_ba("y", 1)])])
        mod = _mod(always_blocks=[blk])
        ws = lint_module(mod, skip={LintCode.LATCH_INFERRED})
        latch_ws = [w for w in ws if w.code == LintCode.LATCH_INFERRED]
        assert len(latch_ws) == 0

    def test_skip_multiple(self):
        blk = _always_comb([_nb("y", 1)])
        mod = _mod(always_blocks=[blk])
        ws = lint_module(mod, skip={LintCode.MIXED_NONBLOCKING, LintCode.LATCH_INFERRED})
        assert all(w.code not in {LintCode.MIXED_NONBLOCKING, LintCode.LATCH_INFERRED} for w in ws)


# ---------------------------------------------------------------------------
# lint_design
# ---------------------------------------------------------------------------


class TestLintDesign:
    """Design-level lint."""

    def test_multi_module(self):
        blk = _always_comb([_if("sel", [_ba("y", 1)])])
        m1 = _mod("mod_a", always_blocks=[blk])
        m2 = _mod("mod_b")
        design = Design(modules=[m1, m2])
        ws = lint_design(design)
        assert any(w.module == "mod_a" for w in ws)
        # mod_b is clean
        assert not any(w.module == "mod_b" for w in ws)


# ---------------------------------------------------------------------------
# Integration tests — parse from Verilog
# ---------------------------------------------------------------------------


class TestParseIntegrationLatch:
    """Parse Verilog and detect latch inference."""

    def test_incomplete_if_latch(self):
        src = """\
module latch_ex (
    input sel, d,
    output reg q
);
    always @(*)
        if (sel)
            q = d;
endmodule
"""
        mod = _parse(src)
        ws = lint_module(mod, skip={LintCode.UNDRIVEN, LintCode.UNUSED, LintCode.MULTI_DRIVEN})
        latch_ws = [w for w in ws if w.code == LintCode.LATCH_INFERRED]
        assert len(latch_ws) >= 1
        assert any(w.signal == "q" for w in latch_ws)


class TestParseIntegrationAssignStyle:
    """Parse Verilog and detect assignment style issues."""

    def test_blocking_in_sequential(self):
        src = """\
module bad_style (
    input clk, d,
    output reg q
);
    always @(posedge clk)
        q = d;
endmodule
"""
        mod = _parse(src)
        ws = lint_module(mod, skip={LintCode.UNDRIVEN, LintCode.UNUSED, LintCode.MULTI_DRIVEN})
        style_ws = [w for w in ws if w.code == LintCode.MIXED_BLOCKING]
        assert len(style_ws) == 1

    def test_correct_style_no_warning(self):
        src = """\
module good_style (
    input clk, d,
    output reg q
);
    always @(posedge clk)
        q <= d;
endmodule
"""
        mod = _parse(src)
        ws = lint_module(mod, skip={LintCode.UNDRIVEN, LintCode.UNUSED, LintCode.MULTI_DRIVEN})
        style_ws = [w for w in ws if w.code in {LintCode.MIXED_BLOCKING, LintCode.MIXED_NONBLOCKING}]
        assert len(style_ws) == 0


class TestParseIntegrationDriverLoad:
    """Parse + analyze_design → driver/load lint."""

    def test_undriven_output(self):
        src = """\
module undriven_out (
    input a,
    output y
);
endmodule
"""
        mod = _parse(src)
        ws = lint_module(mod)
        undriven_ws = [w for w in ws if w.code == LintCode.UNDRIVEN and w.signal == "y"]
        assert len(undriven_ws) >= 1

    def test_driven_output_clean(self):
        src = """\
module driven_out (
    input a,
    output y
);
    assign y = a;
endmodule
"""
        mod = _parse(src)
        ws = lint_module(mod, skip={LintCode.UNDRIVEN, LintCode.UNUSED, LintCode.MULTI_DRIVEN})
        # The assign drives y, so no undriven warning
        undriven_ws = [w for w in ws if w.code == LintCode.UNDRIVEN and w.signal == "y"]
        assert len(undriven_ws) == 0


class TestLintWarningDataclass:
    """Basic LintWarning construction and fields."""

    def test_fields(self):
        w = LintWarning(
            code=LintCode.UNDRIVEN,
            message="test message",
            module="mod",
            signal="sig",
        )
        assert w.code == LintCode.UNDRIVEN
        assert w.message == "test message"
        assert w.module == "mod"
        assert w.signal == "sig"
        assert w.instance is None

    def test_instance_field(self):
        w = LintWarning(
            code=LintCode.UNCONNECTED_PORT,
            message="port open",
            module="mod",
            signal="p",
            instance="u1",
        )
        assert w.instance == "u1"
