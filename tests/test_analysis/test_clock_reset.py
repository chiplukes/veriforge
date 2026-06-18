"""Tests for clock/reset extraction analysis."""

from veriforge.analysis import ClockResetInfo, ClockSignal, ResetSignal, extract_clocks_resets
from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Module
from veriforge.model.expressions import BinaryOp, Identifier, Literal, UnaryOp
from veriforge.model.statements import IfStatement, NonblockingAssign, SensitivityEdge, SeqBlock
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _edge(name: str, edge: str = "posedge") -> SensitivityEdge:
    return SensitivityEdge(edge=edge, signal=Identifier(name))


def _nb_assign(lhs: str, rhs_val: int = 0) -> NonblockingAssign:
    return NonblockingAssign(lhs=Identifier(lhs), rhs=Literal(rhs_val))


def _if_reset(cond, then_stmts, else_stmts=None) -> IfStatement:
    then_body = SeqBlock(statements=then_stmts) if len(then_stmts) > 1 else then_stmts[0]
    else_body = None
    if else_stmts:
        else_body = SeqBlock(statements=else_stmts) if len(else_stmts) > 1 else else_stmts[0]
    return IfStatement(condition=cond, then_body=then_body, else_body=else_body)


def _always(sens_list, body_stmts) -> AlwaysBlock:
    body = SeqBlock(statements=body_stmts) if len(body_stmts) > 1 else body_stmts[0]
    return AlwaysBlock(
        sensitivity_list=sens_list,
        sensitivity_type=SensitivityType.SEQUENTIAL,
        body=body,
    )


def _module(name: str, always_blocks: list) -> Module:
    mod = Module(name=name)
    mod.always_blocks = always_blocks
    return mod


def _parse(src: str) -> Module:
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# Unit tests — model-level
# ---------------------------------------------------------------------------


class TestAsyncReset:
    """Async reset: edge-triggered reset signal in sensitivity list."""

    def test_posedge_clk_negedge_rst_n(self):
        """Standard async active-low reset: @(posedge clk or negedge rst_n)."""
        blk = _always(
            [_edge("clk", "posedge"), _edge("rst_n", "negedge")],
            [
                _if_reset(
                    UnaryOp("!", Identifier("rst_n")),
                    [_nb_assign("q", 0)],
                    [_nb_assign("q", 1)],
                )
            ],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert len(info.clocks) == 1
        assert info.clocks[0].name == "clk"
        assert info.clocks[0].edge == "posedge"
        assert len(info.resets) == 1
        assert info.resets[0].name == "rst_n"
        assert info.resets[0].style == "async"
        assert info.resets[0].active_low is True
        assert info.resets[0].edge == "negedge"
        assert info.resets[0].clock == "clk"

    def test_posedge_clk_posedge_rst(self):
        """Async active-high reset: @(posedge clk or posedge rst)."""
        blk = _always(
            [_edge("clk", "posedge"), _edge("rst", "posedge")],
            [
                _if_reset(
                    Identifier("rst"),
                    [_nb_assign("q", 0)],
                    [_nb_assign("q", 1)],
                )
            ],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert len(info.clocks) == 1
        assert info.clocks[0].name == "clk"
        assert len(info.resets) == 1
        assert info.resets[0].name == "rst"
        assert info.resets[0].active_low is False
        assert info.resets[0].style == "async"
        assert info.resets[0].edge == "posedge"

    def test_tilde_negation(self):
        """Active-low with ~ instead of !: if (~rst_n)."""
        blk = _always(
            [_edge("clk"), _edge("rst_n", "negedge")],
            [
                _if_reset(
                    UnaryOp("~", Identifier("rst_n")),
                    [_nb_assign("q", 0)],
                    [_nb_assign("q", 1)],
                )
            ],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert info.resets[0].active_low is True

    def test_equality_check_zero(self):
        """Active-low via equality: if (rst_n == 1'b0)."""
        blk = _always(
            [_edge("clk"), _edge("rst_n", "negedge")],
            [
                _if_reset(
                    BinaryOp("==", Identifier("rst_n"), Literal(0, width=1, base="b")),
                    [_nb_assign("q", 0)],
                    [_nb_assign("q", 1)],
                )
            ],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert info.resets[0].active_low is True
        assert info.resets[0].name == "rst_n"


class TestSyncReset:
    """Sync reset: clock-only sensitivity, reset checked in body."""

    def test_sync_active_high(self):
        """@(posedge clk) if (rst) ... else ..."""
        blk = _always(
            [_edge("clk")],
            [
                _if_reset(
                    Identifier("rst"),
                    [_nb_assign("q", 0)],
                    [_nb_assign("q", 1)],
                )
            ],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert len(info.clocks) == 1
        assert info.clocks[0].name == "clk"
        assert len(info.resets) == 1
        assert info.resets[0].name == "rst"
        assert info.resets[0].style == "sync"
        assert info.resets[0].active_low is False
        assert info.resets[0].edge is None
        assert info.resets[0].clock == "clk"

    def test_sync_active_low(self):
        """@(posedge clk) if (!rst_n) ... else ..."""
        blk = _always(
            [_edge("clk")],
            [
                _if_reset(
                    UnaryOp("!", Identifier("rst_n")),
                    [_nb_assign("q", 0)],
                    [_nb_assign("q", 1)],
                )
            ],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert info.resets[0].style == "sync"
        assert info.resets[0].active_low is True


class TestNoReset:
    """Always blocks without resets."""

    def test_clock_only(self):
        """@(posedge clk) q <= d; — no reset."""
        blk = _always(
            [_edge("clk")],
            [_nb_assign("q", 1)],
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert len(info.clocks) == 1
        assert info.clocks[0].name == "clk"
        assert len(info.resets) == 0

    def test_combinational_ignored(self):
        """Combinational blocks are skipped entirely."""
        blk = AlwaysBlock(
            sensitivity_list=[],
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=_nb_assign("y", 0),
        )
        info = extract_clocks_resets(_module("dut", [blk]))
        assert len(info.clocks) == 0
        assert len(info.resets) == 0


class TestMultipleDomains:
    """Modules with multiple clock domains."""

    def test_two_clocks(self):
        """Two always blocks with different clocks."""
        blk1 = _always([_edge("clk_a")], [_nb_assign("q1")])
        blk2 = _always([_edge("clk_b")], [_nb_assign("q2")])
        info = extract_clocks_resets(_module("dut", [blk1, blk2]))
        assert len(info.clocks) == 2
        names = info.clock_names()
        assert names == ["clk_a", "clk_b"]

    def test_shared_clock_merged(self):
        """Two blocks on the same clock merge into one ClockSignal."""
        blk1 = _always([_edge("clk")], [_nb_assign("q1")])
        blk2 = _always([_edge("clk")], [_nb_assign("q2")])
        info = extract_clocks_resets(_module("dut", [blk1, blk2]))
        assert len(info.clocks) == 1
        assert info.clocks[0].name == "clk"
        assert len(info.clocks[0].always_blocks) == 2

    def test_domain_map(self):
        """domain_map() groups blocks by clock."""
        blk1 = _always([_edge("clk_a")], [_nb_assign("q1")])
        blk2 = _always([_edge("clk_b")], [_nb_assign("q2")])
        blk3 = _always([_edge("clk_a")], [_nb_assign("q3")])
        info = extract_clocks_resets(_module("dut", [blk1, blk2, blk3]))
        dm = info.domain_map()
        assert len(dm["clk_a"]) == 2
        assert len(dm["clk_b"]) == 1


class TestClockResetInfoHelpers:
    """Test ClockResetInfo convenience methods."""

    def test_clock_names(self):
        info = ClockResetInfo(
            clocks=[ClockSignal("z_clk", "posedge"), ClockSignal("a_clk", "posedge")],
        )
        assert info.clock_names() == ["a_clk", "z_clk"]

    def test_reset_names(self):
        info = ClockResetInfo(
            resets=[
                ResetSignal("rst_b", style="async", active_low=False),
                ResetSignal("rst_a", style="sync", active_low=True),
            ],
        )
        assert info.reset_names() == ["rst_a", "rst_b"]

    def test_empty_module(self):
        info = extract_clocks_resets(_module("empty", []))
        assert info.clocks == []
        assert info.resets == []
        assert info.clock_names() == []
        assert info.reset_names() == []
        assert info.domain_map() == {}


class TestNegedgeClock:
    """Negedge clock should be recorded properly."""

    def test_negedge_clock(self):
        blk = _always([_edge("clk", "negedge")], [_nb_assign("q")])
        info = extract_clocks_resets(_module("dut", [blk]))
        assert info.clocks[0].edge == "negedge"


# ---------------------------------------------------------------------------
# Integration tests — parse from Verilog source
# ---------------------------------------------------------------------------


class TestParseAsyncReset:
    """Parse Verilog and extract async reset info."""

    def test_standard_async_reset(self):
        src = """\
module dff_ar (
    input clk, rst_n, d,
    output reg q
);
    always @(posedge clk or negedge rst_n)
        if (!rst_n)
            q <= 1'b0;
        else
            q <= d;
endmodule
"""
        mod = _parse(src)
        info = extract_clocks_resets(mod)
        assert info.clock_names() == ["clk"]
        assert info.reset_names() == ["rst_n"]
        assert info.resets[0].style == "async"
        assert info.resets[0].active_low is True
        assert info.resets[0].edge == "negedge"
        assert info.resets[0].clock == "clk"

    def test_async_active_high(self):
        src = """\
module dff_ar_hi (
    input clk, rst, d,
    output reg q
);
    always @(posedge clk or posedge rst)
        if (rst)
            q <= 0;
        else
            q <= d;
endmodule
"""
        mod = _parse(src)
        info = extract_clocks_resets(mod)
        assert info.resets[0].active_low is False
        assert info.resets[0].style == "async"
        assert info.resets[0].edge == "posedge"


class TestParseSyncReset:
    """Parse Verilog and extract sync reset info."""

    def test_standard_sync_reset(self):
        src = """\
module dff_sr (
    input clk, rst, d,
    output reg q
);
    always @(posedge clk)
        if (rst)
            q <= 0;
        else
            q <= d;
endmodule
"""
        mod = _parse(src)
        info = extract_clocks_resets(mod)
        assert info.clock_names() == ["clk"]
        assert info.reset_names() == ["rst"]
        assert info.resets[0].style == "sync"
        assert info.resets[0].active_low is False
        assert info.resets[0].clock == "clk"


class TestParseMultiDomain:
    """Parse Verilog with multiple clock domains."""

    def test_dual_clock(self):
        src = """\
module dual_clk (
    input clk_a, clk_b, d,
    output reg q1, q2
);
    always @(posedge clk_a)
        q1 <= d;
    always @(posedge clk_b)
        q2 <= d;
endmodule
"""
        mod = _parse(src)
        info = extract_clocks_resets(mod)
        assert info.clock_names() == ["clk_a", "clk_b"]
        assert len(info.resets) == 0


class TestParseNoSequential:
    """Combinational-only modules should yield empty results."""

    def test_combinational(self):
        src = """\
module comb_only (
    input a, b,
    output y
);
    assign y = a & b;
endmodule
"""
        mod = _parse(src)
        info = extract_clocks_resets(mod)
        assert info.clocks == []
        assert info.resets == []


class TestDesignLevel:
    """Test extract_clocks_resets_from_design."""

    def test_design_extraction(self):
        from veriforge.analysis import extract_clocks_resets_from_design
        from veriforge.model.design import Design

        m1 = _module("mod_a", [_always([_edge("clk")], [_nb_assign("q")])])
        m2 = _module("mod_b", [])
        design = Design(modules=[m1, m2])
        result = extract_clocks_resets_from_design(design)
        assert "mod_a" in result
        assert "mod_b" in result
        assert len(result["mod_a"].clocks) == 1
        assert len(result["mod_b"].clocks) == 0
