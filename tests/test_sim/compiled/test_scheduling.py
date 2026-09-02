"""Compiled engine: delta-cycle scheduling and dirty-marking.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestPhase5Scheduler:
    """Test batch_run through CompiledScheduler."""

    def test_scheduler_batch_run(self):
        """CompiledScheduler.batch_run runs the counter correctly."""
        sim = Simulator(_make_counter(), engine="compiled")
        sim.drive("rst", Value(1, width=1))
        sim.drive("clk", Value(0, width=1))
        sim._sched._sim.snapshot()
        sim.drive("clk", Value(1, width=1))
        sim._sched._sim.step()

        sim.drive("rst", Value(0, width=1))
        sim._sched._sim.snapshot()
        sim.drive("clk", Value(0, width=1))
        sim._sched._sim.step()

        sim.batch_run(5, "clk", clock_period=10)

        v = sim.read("count")
        assert v.val == 5
        assert sim.time == 50

    def test_simulator_batch_run_not_compiled(self):
        """batch_run raises NotImplementedError for non-compiled engines."""
        sim = Simulator(_make_counter(), engine="vm")
        with pytest.raises(NotImplementedError, match="compiled"):
            sim.batch_run(10, "clk")

    def test_scheduler_batch_run_bad_clock(self):
        """batch_run raises ValueError for unknown clock signal."""
        sim = Simulator(_make_counter(), engine="compiled")
        with pytest.raises(ValueError, match="nonexistent"):
            sim.batch_run(10, "nonexistent")


class TestDirtyMarkingRegression:
    """Bug regression: compiled engine unconditionally marked signals dirty."""

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]  # noqa: RUF012

    def test_stable_combo_cross(self):
        """Stable combinational logic should produce identical results across engines."""
        for eng in self.ENGINES:
            sim = Simulator(_make_stable_combo(), engine=eng)
            sim.drive("a", Value(1, width=1))
            sim.run(max_time=0)

            assert int(sim.read("y")) == 1, f"{eng}: y should be 1"
            assert int(sim.read("z")) == 1, f"{eng}: z should be 1"

            # Change input
            sim.drive("a", Value(0, width=1))
            sim.run(max_time=10)

            assert int(sim.read("y")) == 0, f"{eng}: y should be 0 after a=0"
            assert int(sim.read("z")) == 0, f"{eng}: z should be 0 after a=0"


def _parse(src: str):
    from veriforge.transforms.tree_to_model import tree_to_design
    from veriforge.verilog_parser import verilog_parser

    tree = verilog_parser(start="source_text").build_tree(src)
    design = tree_to_design(tree, source_file="test.v")
    return next(m for m in design.modules if m.name == "t")


class TestClockSignalReadWithinItsOwnTriggeredBody:
    """Bug regression: reading a posedge/negedge TRIGGER signal from within
    the very body it triggered gave its stale PRE-edge value in the compiled
    engine, instead of the value that actually caused the edge.

    Root cause: `_seq_body_to_sv_reads` (`sim/compiled/_gen_sections.py`)
    rewrites ordinary signal reads inside a seq process body to read from
    `sv[]`/`sm[]` (a snapshot taken BEFORE the edge, so other registers'
    NBA-race-free pre-edge values are visible) -- correct for every signal
    EXCEPT the process's own edge-trigger signal(s), whose value has, by
    definition, already genuinely transitioned to the new state by the time
    the triggered body runs. This exception already existed for negedge
    sensitivity signals (async reset inputs, where reading the stale
    pre-transition value inside `if (!rst_n)` would wrongly skip the reset
    branch) but was never extended to the ordinary POSEDGE clock case --
    confirmed directly: `always @(posedge clk) o <= clk;` gave `o <= 0`
    (sv[]'s stale pre-edge value) instead of the correct `o <= 1`, on
    `compiled` only (`reference`/`vm`/`vm-fast` use a separate old-value
    slot just for edge detection and never shared this gap).
    """

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]  # noqa: RUF012

    def test_bare_clk_read_in_triggering_block(self):
        mod = _parse("""
            module t (input clk, output reg [2:0] o);
              always @(posedge clk) begin
                o <= clk;
              end
            endmodule
        """)
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            assert int(sim.read("o")) == 1, f"{eng}: o should capture clk's new (1) value at its own posedge"

    def test_clk_read_inside_concat_in_triggering_block(self):
        """Same bug, but with the trigger signal buried inside a larger
        expression (a concat) rather than read bare -- confirms the fix
        isn't accidentally scoped to only the trivial single-identifier RHS
        shape.
        """
        mod = _parse("""
            module t (input clk, output reg [2:0] o);
              always @(posedge clk) begin
                o <= {2'b0, clk};
              end
            endmodule
        """)
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            assert int(sim.read("o")) == 1, f"{eng}: o should capture clk's new (1) value at its own posedge"

    def test_non_trigger_signal_still_uses_pre_edge_snapshot(self):
        """Guard against an overcorrection: an ORDINARY (non-trigger) signal
        toggled in lockstep with clk must still read its pre-edge snapshot
        value where that's the correct NBA-race-free semantics -- this bug's
        fix must not turn into "never use sv[] at all".
        """
        mod = _parse("""
            module t (input clk, input d, output reg [2:0] o);
              always @(posedge clk) begin
                o <= d;
              end
            endmodule
        """)
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            sim.drive("clk", Value(0, width=1))
            sim.drive("d", Value(1, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            assert int(sim.read("o")) == 1, f"{eng}: o should capture d's driven value at the posedge"


class TestContinuousAssignSnapshotConvergence:
    """Bug regression: the pre-edge snapshot (`sv[]`/`sm[]`) used for a
    sequential process's NBA reads was built from only ONE pass through all
    continuous-assign processes, in DECLARATION order -- not settled to a
    fixed point first. A multi-hop continuous-assign chain declared "out of
    order" relative to its own data dependencies (a later `assign` needed
    by an earlier one) left the snapshot one hop stale.

    Root cause: `refresh_data_snapshot()` and each of `batch_run()`'s three
    snapshot points (`sim/compiled/_gen_sections.py`) ran `cont_0()...
    cont_N()` exactly once before snapshotting -- correct only when
    declaration order happens to already match dependency order. Given
    `assign o10 = ~o12; assign o12 = {...};` (`o12` declared AFTER `o10`,
    which reads it), `o10`'s own process ran first in that single pass and
    computed from `o12`'s STALE value; that stale `o10` then got baked
    into the snapshot, so `r7 <= o10;` captured the wrong value at every
    single clock edge, forever. Confirmed by simply swapping the two
    `assign` statements, which made the divergence disappear entirely (see
    `notes/roadmap.md`, "mismatch_10096"). Fixed by settling continuous
    assigns to a genuine fixed point (bounded by the number of continuous
    processes, mirroring `delta_loop`'s own oscillation-detection scratch
    buffers) before every snapshot point, not just one blind pass.
    """

    ENGINES = ["reference", "compiled"]  # noqa: RUF012

    def test_out_of_order_continuous_assign_chain_settles_before_snapshot(self):
        """Original fuzzer-found module (task tracking: mismatch_10096),
        driven with the exact stimulus sequence that reproduced the
        divergence. Simplifying `o11`/`o12` down to their essential
        `o10 = ~o12; o12 = f(...)` declaration-order shape (verified
        separately to reproduce with hand-picked stimulus too) was tried
        first but didn't reproduce with a SHORT, simple drive sequence --
        this bug needs several clock cycles of real accumulated register
        state, not just one settle, so the full original module + its
        original reproducing vectors are used here instead of a further
        reduction, to keep the regression test's own fidelity to the
        confirmed failure exact.
        """
        mod = _parse("""
            module t (
                input [0:0] clk,
                input signed [15:0] i1,
                input signed [100:0] i2,
                input signed [6:0] i3,
                input [86:0] i4,
                input signed [7:0] i5,
                output signed [79:0] o10,
                output [79:0] o11,
                output signed [127:0] o12
            );
                reg [64:0] r6;
                reg [30:0] r7;
                reg [111:0] r8;
                reg [31:0] r9;
                assign o10 = ~o12;
                assign o11 = {r7[32'd4], i1 ? r9[32'd25] : {32'd3{i3}},
                              {~&{<<{clk, i2[32'd99:32'd23], o12}}, {{32'd2{r6}}, r7 - o12}}};
                assign o12 = {i1[32'd7] ? {{32'd2{clk}}, {<<32'd2{i2, i1}}, i2} : {32'd3{r9}},
                              ~|i3[32'd1], !i5};

                always @(posedge clk) begin
                    r6 <= -(-o11) ? r9[32'd28] % (o11 | 1'b1) ? o12[32'd13:32'd8] ? r7 : i1
                          : ~|r6[32'd59] : {{32'd3{o12}}, r9 * clk, {clk, o11[32'd28]}};
                    r7 <= o10;
                    r8 <= -{32'd3{i3}};
                    r9 <= r6;
                end
            endmodule
        """)
        # Exact reproducing stimulus (seed 0 of the grammar-driven fuzzer's
        # own `_gen_stimulus`, reduced to plain literals here).
        vectors = [
            {
                "clk": 1,
                "i1": Value(2653, width=16),
                "i2": Value(2329698900472816436872753636962, width=101),
                "i3": Value(38, width=7),
                "i4": Value(137977801063335231949971375, width=87),
                "i5": Value(0x80, width=8, mask=0x23),
            },
            {
                "clk": Value(0, width=1, mask=1),
                "i1": Value(16417, width=16),
                "i2": Value(2036573564372909422497018287890, width=101),
                "i3": Value(18, width=7),
                "i4": Value(139088488569565924033970427, width=87),
                "i5": Value(84, width=8),
            },
            {
                "clk": 0,
                "i1": Value(20722, width=16),
                "i2": Value(1423461208831624464189349369788, width=101),
                "i3": Value(110, width=7),
                "i4": Value(142125555556951916299783682, width=87),
                "i5": Value(0, width=8, mask=0xD7),
            },
            {
                "clk": 0,
                "i1": Value(51448, width=16),
                "i2": Value(2099037492882965679776570467653, width=101),
                "i3": Value(31, width=7),
                "i4": Value(9746353288704512556657387, width=87),
                "i5": Value(145, width=8),
            },
            {
                "clk": 1,
                "i1": Value(52637, width=16),
                "i2": Value(870972744167079373006347824579, width=101),
                "i3": Value(127, width=7),
                "i4": Value(85304464772387594979456785, width=87),
                "i5": Value(31, width=8),
            },
        ]
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(mod, engine=eng)
            for name, value in vectors[0].items():
                sim.drive(name, Value(0, width=(value.width if isinstance(value, Value) else 1)))
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            out = None
            for vec in vectors:
                for name, value in vec.items():
                    sim.drive(name, value)
                sim.settle()
                sim.drive("clk", Value(1, width=1))
                sim.settle()
                sim.drive("clk", Value(0, width=1))
                sim.settle()
                out = sim.read("o11")
            results[eng] = out
        assert results["compiled"] == results["reference"]


class TestBootstrapSettleFalsePosedge:
    """Bug regression: a clock that starts HIGH (its first-ever drive sets
    it straight to 1, never 0) made the compiled engine's bootstrap
    ``settle()`` call fire every `posedge clk` process one full edge early.

    Root cause: ``CompiledScheduler.drive_signal()`` snapshots ALL signals
    (``self._sim.snapshot()``) before the very first drive it ever sees, so
    edge detection has a baseline. When that very first drive is the clock
    itself being set to its starting level (1, for a clock generator that
    begins high), the snapshot captures the clock's PRE-drive value --
    whatever the freshly-constructed signal array defaults to (0) -- not a
    real "previous cycle" value, because nothing has run yet.
    ``_has_drive_snapshot`` then guards every later drive() this same cycle
    from re-snapshotting, and ``refresh_data_snapshot()`` deliberately
    leaves the clock's own snapshot slot untouched (needed so REAL edges
    detect correctly later) -- so this stale snapshot survives all the way
    into ``settle()``'s one-time ``mark_all_dirty()`` bootstrap pass. The
    delta loop it drives then sees ``c.val[clk]==1`` (real) against
    ``sv[clk]==0`` (stale pre-construction default) and reads that as a
    genuine 0->1 transition, firing every posedge-triggered process before
    the actual first clock edge ever happens.

    Confirmed via a minimal AXI-Stream-shaped skid buffer (a 1-deep
    register that self-fills when empty): its very first accepted beat was
    captured -- and later double-counted -- one cycle early under
    `compiled` only, corrupting a downstream beat counter by exactly one
    for the rest of the run (`tests/test_sim/test_axis_endpoints.py`
    covers the end-to-end symptom; this class isolates the scheduling
    mechanism itself).
    """

    def test_posedge_process_does_not_fire_on_first_settle_with_clock_starting_high(self):
        mod = _parse("""
            module t (input clk, output reg [7:0] cnt);
              initial cnt = 0;
              always @(posedge clk)
                cnt <= cnt + 1;
            endmodule
        """)
        sim = Simulator(mod, engine="compiled")
        # The clock's very first-ever drive sets it straight to 1 -- exactly
        # what a "starts high" clock generator does before any stepping.
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        assert int(sim.read("cnt")) == 0, "posedge process must not fire on the bootstrap settle() call"

        # A real subsequent edge (1 -> 0 -> 1) must still fire normally.
        sim.drive("clk", Value(0, width=1))
        sim.settle()
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        assert int(sim.read("cnt")) == 1, "the real first posedge should still increment cnt exactly once"

    def test_skid_buffer_does_not_double_capture_first_beat(self):
        """End-to-end shape of the originally-reported symptom: a 1-deep
        skid buffer that self-fills on its first (empty-buffer) cycle must
        not report its first captured beat twice.
        """
        mod = _parse("""
            module t (
                input clk,
                input s_valid,
                input [7:0] s_data,
                output reg m_valid,
                output reg [7:0] m_data,
                input m_ready
            );
                initial m_valid = 0;
                always @(posedge clk)
                    if (!m_valid || m_ready) begin
                        m_valid <= s_valid;
                        m_data  <= s_data;
                    end
            endmodule
        """)
        # Only the compiled engine has the bootstrap-settle bug this guards
        # against; other engines don't apply `initial` assignments via a
        # bare drive()/settle() pair the way `compiled`'s mark_all_dirty()
        # bootstrap does, so a fair cross-engine comparison isn't possible
        # at this low a level -- see `test_axis_endpoints.py` /
        # `test_bypass_survives_sustained_severe_output_backpressure`-style
        # bench tests for the cross-engine end-to-end coverage instead.
        sim = Simulator(mod, engine="compiled")
        sim.drive("s_valid", Value(1, width=1))
        sim.drive("s_data", Value(0x42, width=8))
        sim.drive("m_ready", Value(0, width=1))
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        assert int(sim.read("m_valid")) == 0, "buffer must still be empty before any real clock edge"

        sim.drive("clk", Value(0, width=1))
        sim.settle()
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        assert int(sim.read("m_valid")) == 1, "the real first posedge should capture the first beat"
        assert int(sim.read("m_data")) == 0x42

        # Held (m_ready=0): a second edge must NOT re-capture / advance.
        sim.drive("clk", Value(0, width=1))
        sim.settle()
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        assert int(sim.read("m_data")) == 0x42, "buffer must hold, not double-advance"
