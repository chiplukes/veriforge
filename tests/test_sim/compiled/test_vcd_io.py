"""Compiled engine: VCD tracing and $dumpvars.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestPhase4VCD:
    """Test VCD recording with compiled engine."""

    def test_vcd_time_step_callback(self):
        """_on_time_step callback fires for each time step."""
        sim = Simulator(_make_counter(), engine="compiled")
        sim.drive("rst", Value(1, width=1))
        sim.drive("count", Value(0, width=8))
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)

        times_seen = []

        def record(sched):
            times_seen.append(sched.time)

        sim._sched._on_time_step = record
        sim.run(max_time=50)

        # Should have callbacks at t=0 and each clock edge
        assert len(times_seen) > 0
        assert times_seen[0] == 0

    def test_vcd_ctx_signals_access(self):
        """sched.ctx._signals provides dict-like access for VCD."""
        sim = Simulator(_make_adder(), engine="compiled")
        sim.drive("a", Value(3, width=8))
        sim.drive("b", Value(5, width=8))
        sim.run(max_time=0)

        sched = sim._sched
        # Access via ctx._signals (dict-like interface)
        signals = sched.ctx._signals
        assert "a" in signals
        assert "b" in signals
        assert "y" in signals
        assert signals["y"] == Value(8, width=8)

        # items() should work for VCD recording loop
        sig_dict = dict(signals.items())
        assert sig_dict["a"] == Value(3, width=8)
        assert sig_dict["b"] == Value(5, width=8)

    def test_vcd_recording(self):
        """Full VCD recording works with compiled engine."""
        import io  # noqa: PLC0415

        from veriforge.sim.vcd import VcdWriter  # noqa: PLC0415

        sim = Simulator(_make_counter(), engine="compiled")
        sim.drive("rst", Value(1, width=1))
        sim.drive("count", Value(0, width=8))
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)

        vcd_buf = io.StringIO()
        vcd_writer = VcdWriter(vcd_buf, timescale="1ns")

        for name in ["clk", "rst", "count"]:
            sig_val = sim._sched.ctx._signals[name]
            vcd_writer.add_signal(name, width=sig_val.width)

        vcd_writer.write_header()
        initial_vals = dict(sim._sched.ctx._signals.items())
        vcd_writer.write_initial(initial_vals)

        def _record_signals(sched):
            vcd_writer.set_time(sched.time)
            for name in sched.ctx._signals:
                vcd_writer.change(name, sched.ctx._signals[name])

        sim._sched._on_time_step = _record_signals
        sim.run(max_time=50)
        vcd_writer.finalize()

        vcd_text = vcd_buf.getvalue()
        assert "$timescale" in vcd_text
        assert "$var" in vcd_text
        assert "$dumpvars" in vcd_text


class TestCompiledDumpvars:
    """$dumpfile/$dumpvars support in the compiled engine."""

    def test_dumpvars_creates_vcd(self, tmp_path):
        """$dumpfile/$dumpvars in initial block creates VCD output."""
        vcd_file = tmp_path / "test.vcd"
        mod = _make_dumpvars_module(str(vcd_file))
        sim = Simulator(mod, engine="compiled")
        sim.drive("count", Value(0, width=8))

        # Schedule a clock-like event so run() has something to do
        sim._sched.schedule_at(10, ("clock_toggle", "count", Value(5, width=8)))
        sim.run(max_time=20)

        assert vcd_file.exists(), "VCD file should have been created"
        vcd_text = vcd_file.read_text()
        assert "$var" in vcd_text, "VCD should contain $var declarations"

    def test_dumpvars_records_changes(self, tmp_path):
        """VCD file records signal changes over time."""
        vcd_file = tmp_path / "changes.vcd"
        mod = _make_dumpvars_module(str(vcd_file))
        sim = Simulator(mod, engine="compiled")
        sim.drive("count", Value(0, width=8))

        sim._sched.schedule_at(5, ("clock_toggle", "count", Value(10, width=8)))
        sim._sched.schedule_at(15, ("clock_toggle", "count", Value(20, width=8)))
        sim.run(max_time=20)

        vcd_text = vcd_file.read_text()
        assert "$timescale" in vcd_text or "$var" in vcd_text, "VCD should have header"
        # Should have timestamp markers
        assert "#" in vcd_text, "VCD should contain timestamp markers"
