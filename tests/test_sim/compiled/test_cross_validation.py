"""Compiled engine: cross-engine (reference vs. compiled) validation.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestCompiledCrossValidation:
    """Run designs through all three engines and compare results."""

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]  # noqa: RUF012

    def _run_all(self, module_fn, setup_fn, signals_to_check):
        """Run module through all engines with same setup, compare signals."""
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(module_fn(), engine=eng)
            setup_fn(sim)
            sim.run(max_time=0)
            results[eng] = {name: sim.read(name) for name in signals_to_check}

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            for sig in signals_to_check:
                assert results[eng][sig] == ref[sig], (
                    f"{eng} disagrees with reference on '{sig}': {results[eng][sig]} != {ref[sig]}"
                )

    def test_and_gate_cross(self):
        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(0x0F, width=8))

        self._run_all(_make_and_gate, setup, ["y"])

    def test_adder_cross(self):
        def setup(s):
            s.drive("a", Value(37, width=8))
            s.drive("b", Value(19, width=8))

        self._run_all(_make_adder, setup, ["y"])

    def test_mux_sel0_cross(self):
        def setup(s):
            s.drive("sel", Value(0, width=1))
            s.drive("a", Value(111, width=8))
            s.drive("b", Value(222, width=8))

        self._run_all(_make_mux_continuous, setup, ["y"])

    def test_mux_sel1_cross(self):
        def setup(s):
            s.drive("sel", Value(1, width=1))
            s.drive("a", Value(111, width=8))
            s.drive("b", Value(222, width=8))

        self._run_all(_make_mux_continuous, setup, ["y"])

    def test_inverter_cross(self):
        def setup(s):
            s.drive("a", Value(0xAA, width=8))

        self._run_all(_make_inverter, setup, ["y"])

    def test_chain_cross(self):
        def setup(s):
            s.drive("a", Value(10, width=8))

        self._run_all(_make_chain, setup, ["b", "c"])

    def test_concat_cross(self):
        def setup(s):
            s.drive("a", Value(0xA, width=4))
            s.drive("b", Value(0x5, width=4))

        self._run_all(_make_concat_assign, setup, ["y"])

    def test_adder_sweep_cross(self):
        """Cross-validate adder across many input combinations."""
        for a_val in [0, 1, 127, 128, 255]:
            for b_val in [0, 1, 127, 128, 255]:

                def setup(s, av=a_val, bv=b_val):
                    s.drive("a", Value(av, width=8))
                    s.drive("b", Value(bv, width=8))

                self._run_all(_make_adder, setup, ["y"])


class TestPhase2CrossValidation:
    """Cross-validate sequential designs across vm and compiled engines.

    The reference engine's run_step() does not support single-step
    advancement (it runs the whole simulation at once), so we only
    compare vm vs compiled here.
    """

    ENGINES = ["vm", "vm-fast", "compiled"]  # noqa: RUF012

    def _run_clocked(self, module_fn, setup_fn, signals_to_check, max_steps=20, clock_period=10):
        """Run module through all engines with clock, compare signals after each step."""
        max_time = max_steps * clock_period + clock_period
        results = {}
        for eng in self.ENGINES:
            mod = module_fn()
            sim = Simulator(mod, engine=eng)
            setup_fn(sim)
            clk = Clock(sim.signal("clk"), period=clock_period)
            sim.fork(clk)
            sim._schedule_clock_events(clk, max_time)
            values = []
            for _ in range(max_steps):
                if not sim.run_step():
                    break
                values.append({name: sim.read(name) for name in signals_to_check})
            results[eng] = values

        # All engines must produce same number of steps
        ref_vals = results["vm"]
        for eng in ["compiled"]:
            eng_vals = results[eng]
            assert len(eng_vals) == len(ref_vals), f"{eng}: got {len(eng_vals)} steps, expected {len(ref_vals)}"
            for step_i, (ref_step, eng_step) in enumerate(zip(ref_vals, eng_vals, strict=True)):
                for sig in signals_to_check:
                    assert eng_step[sig] == ref_step[sig], (
                        f"{eng} step {step_i} disagrees on '{sig}': {eng_step[sig]} != {ref_step[sig]}"
                    )

    def test_counter_cross(self):
        """Counter: reset + count up, cross-validated."""

        def setup(s):
            s.drive("rst", Value(1, width=1))
            s.drive("count", Value(0, width=8))

        self._run_clocked(_make_counter, setup, ["count"], max_steps=6)

    def test_counter_reset_release_cross(self):
        """Counter: reset then release after 2 cycles."""

        def setup(s):
            s.drive("rst", Value(1, width=1))
            s.drive("count", Value(0, width=8))

        # First run with reset held (all engines should agree)
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_counter(), engine=eng)
            setup(sim)
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 200)
            # 2 full cycles with reset
            for _ in range(4):
                sim.run_step()
            # Release reset
            sim.drive("rst", Value(0, width=1))
            # 3 more full cycles
            vals = []
            for _ in range(6):
                sim.run_step()
                vals.append(sim.read("count"))
            results[eng] = vals

        ref = results["vm"]
        for eng in ["compiled"]:
            for i, (r, e) in enumerate(zip(ref, results[eng], strict=True)):
                assert e == r, f"{eng} step {i}: {e} != {r}"

    def test_counter_enable_cross(self):
        """Counter with enable, cross-validated."""

        def setup(s):
            s.drive("rst", Value(1, width=1))
            s.drive("en", Value(1, width=1))
            s.drive("count", Value(0, width=8))

        self._run_clocked(_make_counter_with_enable, setup, ["count"], max_steps=6)

    def test_shift_register_cross(self):
        """Shift register cross-validated."""

        def setup(s):
            s.drive("din", Value(1, width=1))
            s.drive("sr", Value(0, width=4))

        self._run_clocked(_make_shift_register, setup, ["sr"], max_steps=10)

    def test_fsm_cross(self):
        """FSM cross-validated."""

        def setup(s):
            s.drive("rst", Value(1, width=1))
            s.drive("go", Value(0, width=1))
            s.drive("state", Value(0, width=2))

        self._run_clocked(_make_fsm, setup, ["state"], max_steps=6)

    def test_fsm_go_cross(self):
        """FSM with go signal, cross-validated."""
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_fsm(), engine=eng)
            sim.drive("rst", Value(1, width=1))
            sim.drive("go", Value(0, width=1))
            sim.drive("state", Value(0, width=2))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 200)
            # Reset for 2 steps
            sim.run_step()
            sim.run_step()
            # Release reset
            sim.drive("rst", Value(0, width=1))
            sim.drive("go", Value(1, width=1))
            vals = []
            for _ in range(8):
                sim.run_step()
                vals.append(sim.read("state"))
            results[eng] = vals

        ref = results["vm"]
        for eng in ["compiled"]:
            for i, (r, e) in enumerate(zip(ref, results[eng], strict=True)):
                assert e == r, f"{eng} step {i}: {e} != {r}"

    def test_combo_always_mux_cross(self):
        """Combinational always @(*) mux cross-validated."""

        def setup(s):
            s.drive("sel", Value(1, width=1))
            s.drive("a", Value(42, width=8))
            s.drive("b", Value(99, width=8))

        # Use _run_all from Phase 1 pattern — run() works for all engines
        results = {}
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(_make_combo_always_mux(), engine=eng)
            setup(sim)
            sim.run(max_time=0)
            results[eng] = {"y": sim.read("y")}

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng]["y"] == ref["y"], f"{eng}: y={results[eng]['y']} != ref y={ref['y']}"

    def test_mixed_cont_seq_cross(self):
        """Mixed continuous + sequential cross-validated.

        Only check 'count' in step mode; the VM engine has a known issue
        where continuous assigns don't propagate through run_step() for
        testbench-driven inputs.
        """

        def setup(s):
            s.drive("rst", Value(1, width=1))
            s.drive("count", Value(0, width=8))

        self._run_clocked(_make_mixed_cont_seq, setup, ["count"], max_steps=6)


class TestPhase3CrossValidation:
    """Cross-validate Phase 3 designs between vm and compiled engines."""

    ENGINES = ["vm", "vm-fast", "compiled"]  # noqa: RUF012

    def _run_clocked(self, module_fn, setup_fn, signals_to_check, max_steps=20, clock_period=10):
        """Run module through all engines with clock, compare after each step."""
        max_time = max_steps * clock_period + clock_period
        results = {}
        for eng in self.ENGINES:
            mod = module_fn()
            sim = Simulator(mod, engine=eng)
            setup_fn(sim)
            clk = Clock(sim.signal("clk"), period=clock_period)
            sim.fork(clk)
            sim._schedule_clock_events(clk, max_time)
            values = []
            for _ in range(max_steps):
                if not sim.run_step():
                    break
                values.append({name: sim.read(name) for name in signals_to_check})
            results[eng] = values

        ref_vals = results["vm"]
        for eng in ["compiled"]:
            eng_vals = results[eng]
            assert len(eng_vals) == len(ref_vals), f"{eng}: got {len(eng_vals)} steps, expected {len(ref_vals)}"
            for step_i, (ref_step, eng_step) in enumerate(zip(ref_vals, eng_vals, strict=True)):
                for sig in signals_to_check:
                    assert eng_step[sig] == ref_step[sig], (
                        f"{eng} step {step_i} disagrees on '{sig}': {eng_step[sig]} != {ref_step[sig]}"
                    )

    @pytest.mark.parametrize(
        ("width", "sel_value", "a_value", "b_value"),
        [
            (65, 1, (1 << 64) | 0x12345678, 0x0FEDCBA987654321),
            (129, 0, (1 << 128) | (0x89ABCDEF01234567 << 16) | 0x55AA, (1 << 127) | (0x10203040 << 32) | 0xCAFEBABE),
        ],
    )
    def test_wide_combo_generic_ternary_signal_tree_cross_engine(self, width, sel_value, a_value, b_value):
        results = {}
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(_make_wide_combo_generic_ternary_signal_tree(width), engine=eng)
            sim.drive("sel", Value(sel_value, width=1))
            sim.drive("a", Value(a_value, width=width))
            sim.drive("b", Value(b_value, width=width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("width", "sel_value", "a_value", "b_value"),
        [
            (65, 1, (1 << 64) | 0x12345678, 0x0FEDCBA987654321),
            (129, 0, (1 << 128) | (0x89ABCDEF01234567 << 16) | 0x55AA, (1 << 127) | (0x10203040 << 32) | 0xCAFEBABE),
        ],
    )
    def test_wide_seq_generic_ternary_signal_tree_cross_engine(self, width, sel_value, a_value, b_value):
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(_make_wide_seq_generic_ternary_signal_tree(width), engine=eng)
            sim.drive("sel", Value(sel_value, width=1))
            sim.drive("a", Value(a_value, width=width))
            sim.drive("b", Value(b_value, width=width))
            sim.drive("q", Value(0, width=width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "dst_width", "sel", "a_value", "b_value"),
        [
            (65, 33, Value(0, width=1), (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA),
            (65, 33, Value(1, width=1, mask=1), (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA),
            (
                129,
                97,
                Value(0, width=1, mask=1),
                (1 << 128) | (0x89ABCDEF01234567 << 16) | 0x55AA,
                (1 << 127) | (0x10203040 << 32) | 0xCAFEBABE,
            ),
            (
                129,
                97,
                Value(1, width=1),
                Value((1 << 128) | (0x89ABCDEF01234567 << 16) | 0x55AA, width=129, mask=1 << 72),
                (1 << 127) | (0x10203040 << 32) | 0xCAFEBABE,
            ),
        ],
    )
    def test_wide_combo_generic_ternary_signal_tree_sizing_cross_engine(
        self, src_width, dst_width, sel, a_value, b_value
    ):
        results = {}
        a_input = a_value if isinstance(a_value, Value) else Value(a_value, width=src_width)
        b_input = b_value if isinstance(b_value, Value) else Value(b_value, width=src_width)
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(_make_wide_combo_generic_ternary_signal_tree(src_width, dst_width), engine=eng)
            sim.drive("sel", sel)
            sim.drive("a", a_input)
            sim.drive("b", b_input)
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "dst_width", "sel", "a_value", "b_value"),
        [
            (65, 33, Value(0, width=1), (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA),
            (65, 33, Value(1, width=1, mask=1), (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA),
            (
                129,
                97,
                Value(0, width=1, mask=1),
                (1 << 128) | (0x89ABCDEF01234567 << 16) | 0x55AA,
                (1 << 127) | (0x10203040 << 32) | 0xCAFEBABE,
            ),
            (
                129,
                97,
                Value(1, width=1),
                Value((1 << 128) | (0x89ABCDEF01234567 << 16) | 0x55AA, width=129, mask=1 << 72),
                (1 << 127) | (0x10203040 << 32) | 0xCAFEBABE,
            ),
        ],
    )
    def test_wide_seq_generic_ternary_signal_tree_sizing_cross_engine(
        self, src_width, dst_width, sel, a_value, b_value
    ):
        results = {}
        a_input = a_value if isinstance(a_value, Value) else Value(a_value, width=src_width)
        b_input = b_value if isinstance(b_value, Value) else Value(b_value, width=src_width)
        for eng in ["vm", "compiled"]:
            sim = Simulator(_make_wide_seq_generic_ternary_signal_tree(src_width, dst_width), engine=eng)
            sim.drive("sel", sel)
            sim.drive("a", a_input)
            sim.drive("b", b_input)
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("width", "sel_value", "sel_mask", "a_value", "b_value", "c_value"),
        [
            (65, 0, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (65, 1, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (
                129,
                0,
                1,
                (1 << 128) | (0x12345678 << 32) | 0x2468ACE0,
                (1 << 127) | (0x13579BDF << 16) | 0xAAAA,
                (1 << 126) | (0x11112222 << 32) | 0x00FF00FF,
            ),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Y)
    def test_wide_combo_generic_ternary_signal_tree_family_cross_engine(
        self, out_name, width, sel_value, sel_mask, a_value, b_value, c_value
    ):
        results = {}
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(_make_wide_generic_ternary_signal_tree_multi_by_mode(width, "c"), engine=eng)
            sim.drive("sel", Value(sel_value, width=1, mask=sel_mask))
            sim.drive("a", Value(a_value, width=width))
            sim.drive("b", Value(b_value, width=width))
            sim.drive("c", Value(c_value, width=width))
            sim.run(max_time=0)
            results[eng] = sim.read(out_name)

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("width", "sel_value", "sel_mask", "a_value", "b_value", "c_value"),
        [
            (65, 0, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (65, 1, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (
                129,
                0,
                1,
                (1 << 128) | (0x12345678 << 32) | 0x2468ACE0,
                (1 << 127) | (0x13579BDF << 16) | 0xAAAA,
                (1 << 126) | (0x11112222 << 32) | 0x00FF00FF,
            ),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Y)
    def test_wide_combo_signal_generic_ternary_tree_family_cross_engine(
        self, out_name, width, sel_value, sel_mask, a_value, b_value, c_value
    ):
        results = {}
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_ternary_signal_tree_multi_by_mode(width, "c", tree_on_left=False),
                engine=eng,
            )
            sim.drive("sel", Value(sel_value, width=1, mask=sel_mask))
            sim.drive("a", Value(a_value, width=width))
            sim.drive("b", Value(b_value, width=width))
            sim.drive("c", Value(c_value, width=width))
            sim.run(max_time=0)
            results[eng] = sim.read(out_name)

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("width", "sel_value", "sel_mask", "a_value", "b_value", "c_value"),
        [
            (65, 0, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (65, 1, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (
                129,
                0,
                1,
                (1 << 128) | (0x12345678 << 32) | 0x2468ACE0,
                (1 << 127) | (0x13579BDF << 16) | 0xAAAA,
                (1 << 126) | (0x11112222 << 32) | 0x00FF00FF,
            ),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Q)
    def test_wide_seq_generic_ternary_signal_tree_family_cross_engine(
        self, out_name, width, sel_value, sel_mask, a_value, b_value, c_value
    ):
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(_make_wide_generic_ternary_signal_tree_multi_by_mode(width, "s"), engine=eng)
            sim.drive("sel", Value(sel_value, width=1, mask=sel_mask))
            sim.drive("a", Value(a_value, width=width))
            sim.drive("b", Value(b_value, width=width))
            sim.drive("c", Value(c_value, width=width))
            sim.drive("q_and", Value(0, width=width))
            sim.drive("q_add", Value(0, width=width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(out_name)

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("width", "sel_value", "sel_mask", "a_value", "b_value", "c_value"),
        [
            (65, 0, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (65, 1, 0, (1 << 64) | 0x12345678, (1 << 63) | 0x00AA00AA, (1 << 62) | 0x00FF00FF),
            (
                129,
                0,
                1,
                (1 << 128) | (0x12345678 << 32) | 0x2468ACE0,
                (1 << 127) | (0x13579BDF << 16) | 0xAAAA,
                (1 << 126) | (0x11112222 << 32) | 0x00FF00FF,
            ),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Q)
    def test_wide_seq_signal_generic_ternary_tree_family_cross_engine(
        self, out_name, width, sel_value, sel_mask, a_value, b_value, c_value
    ):
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_ternary_signal_tree_multi_by_mode(width, "s", tree_on_left=False),
                engine=eng,
            )
            sim.drive("sel", Value(sel_value, width=1, mask=sel_mask))
            sim.drive("a", Value(a_value, width=width))
            sim.drive("b", Value(b_value, width=width))
            sim.drive("c", Value(c_value, width=width))
            sim.drive("q_and", Value(0, width=width))
            sim.drive("q_add", Value(0, width=width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(out_name)

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_combo_generic_concat_or_signal_tree_cross_engine(
        self, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(_make_wide_combo_generic_concat_or_signal_tree(hi_width, lo_width, dst_width), engine=eng)
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 33, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 65, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_combo_generic_concat_and_signal_tree, id="and"),
            pytest.param(_make_wide_combo_generic_concat_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_combo_generic_concat_add_signal_tree, id="add"),
            pytest.param(_make_wide_combo_generic_concat_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_combo_generic_concat_signal_tree_sizing_family_cross_engine(
        self, module_fn, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(module_fn(hi_width, lo_width, dst_width), engine=eng)
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_seq_generic_concat_or_signal_tree_cross_engine(
        self, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["vm", "compiled"]:
            sim = Simulator(_make_wide_seq_generic_concat_or_signal_tree(hi_width, lo_width, dst_width), engine=eng)
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_combo_generic_concat_and_signal_tree, id="and"),
            pytest.param(_make_wide_combo_generic_concat_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_combo_generic_concat_add_signal_tree, id="add"),
            pytest.param(_make_wide_combo_generic_concat_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_combo_generic_concat_signal_tree_family_cross_engine(
        self, module_fn, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(module_fn(hi_width, lo_width, dst_width), engine=eng)
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_seq_generic_concat_and_signal_tree, id="and"),
            pytest.param(_make_wide_seq_generic_concat_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_seq_generic_concat_add_signal_tree, id="add"),
            pytest.param(_make_wide_seq_generic_concat_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_seq_generic_concat_signal_tree_family_cross_engine(
        self, module_fn, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["vm", "compiled"]:
            sim = Simulator(module_fn(hi_width, lo_width, dst_width), engine=eng)
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 33, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 65, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_seq_generic_concat_and_signal_tree, id="and"),
            pytest.param(_make_wide_seq_generic_concat_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_seq_generic_concat_add_signal_tree, id="add"),
            pytest.param(_make_wide_seq_generic_concat_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_seq_generic_concat_signal_tree_sizing_family_cross_engine(
        self, module_fn, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["vm", "compiled"]:
            sim = Simulator(module_fn(hi_width, lo_width, dst_width), engine=eng)
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_combo_generic_replication_or_signal_tree_cross_engine(
        self, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_combo_generic_replication_or_signal_tree(src_width, count, dst_width), engine=eng
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 33, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 65, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_combo_generic_replication_and_signal_tree, id="and"),
            pytest.param(_make_wide_combo_generic_replication_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_combo_generic_replication_add_signal_tree, id="add"),
            pytest.param(_make_wide_combo_generic_replication_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_combo_generic_replication_signal_tree_sizing_family_cross_engine(
        self, module_fn, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(module_fn(src_width, count, dst_width), engine=eng)
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_seq_generic_replication_or_signal_tree_cross_engine(
        self, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["vm", "compiled"]:
            sim = Simulator(_make_wide_seq_generic_replication_or_signal_tree(src_width, count, dst_width), engine=eng)
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_combo_generic_replication_and_signal_tree, id="and"),
            pytest.param(_make_wide_combo_generic_replication_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_combo_generic_replication_add_signal_tree, id="add"),
            pytest.param(_make_wide_combo_generic_replication_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_combo_generic_replication_signal_tree_family_cross_engine(
        self, module_fn, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(module_fn(src_width, count, dst_width), engine=eng)
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_seq_generic_replication_and_signal_tree, id="and"),
            pytest.param(_make_wide_seq_generic_replication_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_seq_generic_replication_add_signal_tree, id="add"),
            pytest.param(_make_wide_seq_generic_replication_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_seq_generic_replication_signal_tree_family_cross_engine(
        self, module_fn, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["vm", "compiled"]:
            sim = Simulator(module_fn(src_width, count, dst_width), engine=eng)
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 33, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 65, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize(
        "module_fn",
        [
            pytest.param(_make_wide_seq_generic_replication_and_signal_tree, id="and"),
            pytest.param(_make_wide_seq_generic_replication_xor_signal_tree, id="xor"),
            pytest.param(_make_wide_seq_generic_replication_add_signal_tree, id="add"),
            pytest.param(_make_wide_seq_generic_replication_sub_signal_tree, id="sub"),
        ],
    )
    def test_wide_seq_generic_replication_signal_tree_sizing_family_cross_engine(
        self, module_fn, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["vm", "compiled"]:
            sim = Simulator(module_fn(src_width, count, dst_width), engine=eng)
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_combo_signal_generic_concat_or_tree_cross_engine(
        self, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_combo_generic_concat_or_signal_tree(hi_width, lo_width, dst_width, tree_on_left=False),
                engine=eng,
            )
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Y)
    def test_wide_combo_signal_generic_concat_tree_family_cross_engine(
        self, out_name, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_concat_signal_tree_multi_by_mode(
                    hi_width, lo_width, "c", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read(out_name)

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 33, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 65, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Y)
    def test_wide_combo_signal_generic_concat_tree_sizing_family_cross_engine(
        self, out_name, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_concat_signal_tree_multi_by_mode(
                    hi_width, lo_width, "c", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read(out_name)

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_seq_signal_generic_concat_or_tree_cross_engine(
        self, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_seq_generic_concat_or_signal_tree(hi_width, lo_width, dst_width, tree_on_left=False),
                engine=eng,
            )
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 65, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 129, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Q)
    def test_wide_seq_signal_generic_concat_tree_family_cross_engine(
        self, out_name, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_concat_signal_tree_multi_by_mode(
                    hi_width, lo_width, "s", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.drive("q_and", Value(0, width=dst_width))
            sim.drive("q_add", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(out_name)

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "dst_width", "hi_value", "lo_value", "c_value"),
        [
            (33, 32, 33, (1 << 32) | 0x12345678, 0x89ABCDEF, (1 << 64) | 0x00AA00AA),
            (65, 64, 65, (1 << 64) | 0x12345678, 0x89ABCDEF01234567, (1 << 128) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Q)
    def test_wide_seq_signal_generic_concat_tree_sizing_family_cross_engine(
        self, out_name, hi_width, lo_width, dst_width, hi_value, lo_value, c_value
    ):
        results = {}
        total_width = hi_width + lo_width
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_concat_signal_tree_multi_by_mode(
                    hi_width, lo_width, "s", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("hi", Value(hi_value, width=hi_width))
            sim.drive("lo", Value(lo_value, width=lo_width))
            sim.drive("c", Value(c_value, width=total_width))
            sim.drive("q_and", Value(0, width=dst_width))
            sim.drive("q_add", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(out_name)

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_combo_signal_generic_replication_or_tree_cross_engine(
        self, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_combo_generic_replication_or_signal_tree(src_width, count, dst_width, tree_on_left=False),
                engine=eng,
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read("y")

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Y)
    def test_wide_combo_signal_generic_replication_tree_family_cross_engine(
        self, out_name, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_replication_signal_tree_multi_by_mode(
                    src_width, count, "c", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read(out_name)

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 33, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 65, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Y)
    def test_wide_combo_signal_generic_replication_tree_sizing_family_cross_engine(
        self, out_name, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["reference", "vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_replication_signal_tree_multi_by_mode(
                    src_width, count, "c", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.run(max_time=0)
            results[eng] = sim.read(out_name)

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            assert results[eng] == ref, f"{eng}: {results[eng]} != {ref}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    def test_wide_seq_signal_generic_replication_or_tree_cross_engine(
        self, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_seq_generic_replication_or_signal_tree(src_width, count, dst_width, tree_on_left=False),
                engine=eng,
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.drive("q", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read("q")

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 66, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 130, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Q)
    def test_wide_seq_signal_generic_replication_tree_family_cross_engine(
        self, out_name, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_replication_signal_tree_multi_by_mode(
                    src_width, count, "s", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.drive("q_and", Value(0, width=dst_width))
            sim.drive("q_add", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(out_name)

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    @pytest.mark.parametrize(
        ("src_width", "count", "dst_width", "a_value", "b_value"),
        [
            (33, 2, 33, (1 << 32) | 0x12345678, (1 << 65) | 0x00AA00AA),
            (65, 2, 65, (1 << 64) | 0x12345678, (1 << 129) | (0x11112222 << 32) | 0x00FF00FF),
        ],
    )
    @pytest.mark.parametrize("out_name", _REPR_SINGLE_OPS_MULTI_Q)
    def test_wide_seq_signal_generic_replication_tree_sizing_family_cross_engine(
        self, out_name, src_width, count, dst_width, a_value, b_value
    ):
        results = {}
        total_width = src_width * count
        for eng in ["vm", "compiled"]:
            sim = Simulator(
                _make_wide_generic_replication_signal_tree_multi_by_mode(
                    src_width, count, "s", dst_width, tree_on_left=False
                ),
                engine=eng,
            )
            sim.drive("a", Value(a_value, width=src_width))
            sim.drive("b", Value(b_value, width=total_width))
            sim.drive("q_and", Value(0, width=dst_width))
            sim.drive("q_add", Value(0, width=dst_width))
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(out_name)

        assert results["compiled"] == results["vm"], f"compiled: {results['compiled']} != {results['vm']}"

    def test_concat_lhs_cross(self):
        """assign {hi, lo} = x; cross-validated."""

        def setup(s):
            s.drive("x", Value(0xA5, width=8))

        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_concat_lhs_cont(), engine=eng)
            setup(sim)
            sim.run(max_time=0)
            results[eng] = {"hi": sim.read("hi"), "lo": sim.read("lo")}

        ref = results["vm"]
        assert results["compiled"]["hi"] == ref["hi"]
        assert results["compiled"]["lo"] == ref["lo"]

    def test_concat_lhs_3way_cross(self):
        """assign {a, b, c} = x; cross-validated."""

        def setup(s):
            s.drive("x", Value(0b11101011, width=8))

        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_concat_lhs_3way_cont(), engine=eng)
            setup(sim)
            sim.run(max_time=0)
            results[eng] = {"a": sim.read("a"), "b": sim.read("b"), "c": sim.read("c")}

        ref = results["vm"]
        for sig in ["a", "b", "c"]:
            assert results["compiled"][sig] == ref[sig], (
                f"compiled disagrees on '{sig}': {results['compiled'][sig]} != {ref[sig]}"
            )

    def test_wide_concat_lhs_cross(self):
        """assign {a..j} = masked 132-bit x; cross-validated."""
        parts = [
            ("a", Value(0b01, width=2, mask=0b10)),
            ("b", Value(0x81234567, width=32, mask=1 << 30)),
            ("c", Value(0b101, width=3, mask=0b001)),
            ("d", Value(0xA4, width=8, mask=1 << 4)),
            ("e", Value(1, width=1, mask=1)),
            ("f", Value(0x11223344, width=32, mask=1 << 20)),
            ("g", Value(0xD, width=4)),
            ("h", Value(0xBEEF, width=16, mask=1 << 15)),
            ("i", Value(0x55667789, width=32, mask=1 << 0)),
            ("j", Value(0b10, width=2, mask=0b01)),
        ]
        x = _pack_concat_parts(parts)

        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_wide_concat_lhs_cont(), engine=eng)
            sim.drive("x", x)
            sim.run(max_time=0)
            results[eng] = {name: sim.read(name) for name, _ in parts}

        ref = results["vm"]
        for name, expected in parts:
            assert ref[name] == expected, f"vm disagrees on '{name}': {ref[name]} != {expected}"
            assert results["compiled"][name] == ref[name], (
                f"compiled disagrees on '{name}': {results['compiled'][name]} != {ref[name]}"
            )

    def test_bit_select_lhs_cross(self):
        """x[3] <= 1 cross-validated."""

        def setup(s):
            s.drive("x", Value(0, width=8))

        self._run_clocked(_make_bit_select_lhs, setup, ["x"], max_steps=4)

    def test_range_select_lhs_cross(self):
        """x[5:2] <= 0xA cross-validated."""

        def setup(s):
            s.drive("x", Value(0, width=8))

        self._run_clocked(_make_range_select_lhs, setup, ["x"], max_steps=4)

    def test_part_select_lhs_cross(self):
        """x[2 +: 4] <= 0xA cross-validated."""

        def setup(s):
            s.drive("x", Value(0, width=8))

        self._run_clocked(_make_part_select_lhs, setup, ["x"], max_steps=4)


class TestPhase4CrossValidation:
    """Cross-validate Phase 4 features across engines."""

    ENGINES = ["vm", "vm-fast", "compiled"]  # noqa: RUF012

    def _run_clocked(self, module_fn, setup_fn, signals_to_check, max_steps=20, clock_period=10):
        """Run module through all engines with clock, compare after each step."""
        max_time = max_steps * clock_period + clock_period
        results = {}
        for eng in self.ENGINES:
            mod = module_fn()
            sim = Simulator(mod, engine=eng)
            setup_fn(sim)
            clk = Clock(sim.signal("clk"), period=clock_period)
            sim.fork(clk)
            sim._schedule_clock_events(clk, max_time)
            values = []
            for _ in range(max_steps):
                if not sim.run_step():
                    break
                values.append({name: sim.read(name) for name in signals_to_check})
            results[eng] = values

        ref_vals = results["vm"]
        for eng in ["compiled"]:
            eng_vals = results[eng]
            assert len(eng_vals) == len(ref_vals), f"{eng}: got {len(eng_vals)} steps, expected {len(ref_vals)}"
            for step_i, (ref_step, eng_step) in enumerate(zip(ref_vals, eng_vals, strict=True)):
                for sig in signals_to_check:
                    assert eng_step[sig] == ref_step[sig], (
                        f"{eng} step {step_i} disagrees on '{sig}': {eng_step[sig]} != {ref_step[sig]}"
                    )

    def test_initial_simple_cross(self):
        """Simple initial block cross-validated."""
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_initial_simple(), engine=eng)
            sim.run(max_time=0)
            results[eng] = sim.read("count")

        assert results["compiled"] == results["vm"]

    def test_initial_delay_cross(self):
        """Initial block with #delay cross-validated."""
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_initial_with_delay(), engine=eng)
            sim.run(max_time=25)
            results[eng] = sim.read("count")

        assert results["compiled"] == results["vm"]

    def test_initial_display_cross(self):
        """$display in initial block cross-validated."""
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(_make_initial_with_display(), engine=eng)
            sim.run(max_time=0)
            results[eng] = {
                "x": sim.read("x"),
                "display_count": len(sim.display_output),
            }

        assert results["compiled"]["x"] == results["vm"]["x"]
        assert results["compiled"]["display_count"] == results["vm"]["display_count"]


class TestPhase4CounterCross:
    """Counter with initial block cross-validation (originally in Phase 4)."""

    ENGINES = ["vm", "vm-fast", "compiled"]  # noqa: RUF012

    def test_initial_counter_cross(self):
        """Counter with initial block setup, cross-validated via run()."""
        results = {}
        for eng in self.ENGINES:
            mod = _make_initial_counter_setup()
            sim = Simulator(mod, engine=eng)
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim.run(max_time=100)
            results[eng] = {
                "count": sim.read("count"),
                "rst": sim.read("rst"),
            }

        assert results["compiled"]["count"] == results["vm"]["count"]
        assert results["compiled"]["rst"] == results["vm"]["rst"]


class TestPhase7Cross:
    """Cross-validation: compiled vs VM for Phase 7 features."""

    ENGINES = ("vm", "vm-fast", "compiled")

    def test_signed_shift_cross(self):
        """$signed arithmetic right shift matches across engines."""
        for eng in self.ENGINES:
            sim = Simulator(_make_signed_arith(), engine=eng)
            sim.drive("a", Value(0xF0, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 0xFC, f"{eng}: $signed(0xF0) >>> 2 should be 0xFC"

    def test_power_cross(self):
        """Power operator matches across engines."""
        for eng in self.ENGINES:
            sim = Simulator(_make_power(), engine=eng)
            sim.drive("a", Value(3, width=16))
            sim.drive("b", Value(4, width=16))
            sim.run(max_time=0)
            assert sim.read("y") == 81, f"{eng}: 3**4 should be 81"

    def test_xnor_reduce_cross(self):
        """XNOR reduction matches across engines."""
        for eng in self.ENGINES:
            sim = Simulator(_make_xnor_reduce(), engine=eng)
            sim.drive("a", Value(0xFF, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 1, f"{eng}: ~^0xFF should be 1"
            sim.drive("a", Value(0x01, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 0, f"{eng}: ~^0x01 should be 0"

    def test_repeat_loop_cross(self):
        """Combinational repeat loop matches across engines."""
        for eng in ("reference", "vm", "compiled"):
            sim = Simulator(_make_repeat_counter(), engine=eng)
            sim.drive("count", Value(5, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 5, f"{eng}: repeat(5) should increment y to 5"
            sim.drive("count", Value(2, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 2, f"{eng}: repeat(2) should increment y to 2"

    def test_while_loop_cross(self):
        """Combinational while loop matches across engines."""
        for eng in ("reference", "vm", "compiled"):
            sim = Simulator(_make_while_counter(), engine=eng)
            sim.drive("count", Value(6, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 6, f"{eng}: while loop should increment y to 6"
            sim.drive("count", Value(3, width=8))
            sim.run(max_time=0)
            assert sim.read("y") == 3, f"{eng}: while loop should increment y to 3"

    def test_forever_loop_cross(self):
        """Forever loop with $finish matches across engines."""
        from veriforge.sim.executor import StopExecution  # noqa: PLC0415
        from veriforge.sim.vm.interpreter import StopSimulation  # noqa: PLC0415

        for eng in ("reference", "vm", "compiled"):
            sim = Simulator(_make_forever_finish_counter(), engine=eng)
            sim.drive("count", Value(5, width=8))
            try:
                sim.run(max_time=1)
            except (StopExecution, StopSimulation):
                pass
            assert sim.read("y") == 5, f"{eng}: forever/$finish loop should stop with y == 5"

    def test_inout_port_cross(self):
        """Single-module raw inout port behavior matches across engines."""
        for eng in ("reference", "vm", "compiled"):
            sim = Simulator(_make_inout_port_probe(), engine=eng)
            sim.drive("drive_val", Value(0x42, width=8))
            sim.drive("drive_en", Value(1, width=1))
            sim.run(max_time=0)
            assert sim.read("out") == Value(0x42, width=8), f"{eng}: enabled drive should propagate to out"

            sim.drive("drive_val", Value(0xFF, width=8))
            sim.drive("drive_en", Value(0, width=1))
            sim.run(max_time=0)
            assert sim.read("out") == Value(0, width=8), f"{eng}: disabled drive should leave out at zero"


class TestWideUnifiedBehavioralCrossVal:
    """Phase 1 behavioral cross-validation: compiled vs vm for wide operators.

    Each test compiles a module and runs it with both engines, verifying
    the recursive wide emitter produces correct simulation output.
    """

    _W65 = (1 << 64) | 0x1234_5678_9ABC_DEF0
    _W65b = (1 << 63) | 0x0FED_CBA9_8765_4321
    _W129 = (1 << 128) | (0xDEAD_BEEF_CAFE_BABE << 32) | 0x1234_5678

    @staticmethod
    def _combo(mod: Module, drives: dict, output: str = "y") -> tuple:
        """Run mod with vm and compiled, return (vm_result, compiled_result)."""
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(mod, engine=eng)
            for name, val in drives.items():
                sim.drive(name, val)
            sim.run(max_time=0)
            results[eng] = sim.read(output)
        return results["vm"], results["compiled"]

    @staticmethod
    def _seq(mod: Module, drives: dict, output: str = "q") -> tuple:
        """Run clocked mod one cycle (posedge), return (vm_result, compiled_result)."""
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(mod, engine=eng)
            for name, val in drives.items():
                sim.drive(name, val)
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(output)
        return results["vm"], results["compiled"]

    # ── Continuous-assign operators ───────────────────────────────────────

    @pytest.mark.parametrize("width", [65, 129])
    def test_cont_add_cross_engine(self, width):
        a = self._W65 if width == 65 else self._W129
        b = self._W65b if width == 65 else (self._W129 >> 3)
        vm, comp = self._combo(
            _make_wide_cont_add(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value((a + b) & mask, width=width)

    @pytest.mark.parametrize(
        "op,out_name,py_op",
        [
            ("&", "y_and", lambda a, b: a & b),
            ("|", "y_or", lambda a, b: a | b),
            ("^", "y_xor", lambda a, b: a ^ b),
        ],
    )
    def test_cont_bitwise_cross_engine(self, op, out_name, py_op):
        width = 65
        a, b = self._W65, self._W65b
        vm, comp = self._combo(
            _make_wide_cont_multi_bitwise(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
            output=out_name,
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value(py_op(a, b) & mask, width=width)

    def test_cont_not_cross_engine(self):
        width = 65
        a = self._W65
        vm, comp = self._combo(
            _make_wide_cont_not(width),
            {"a": Value(a, width=width)},
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value((~a) & mask, width=width)

    @pytest.mark.parametrize("width", [65, 129])
    def test_cont_equality_cross_engine(self, width):
        a = self._W65 if width == 65 else self._W129
        b = self._W65b if width == 65 else self._W129  # b == a for 129-bit
        vm_ne, comp_ne = self._combo(
            _make_wide_equality(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
        )
        assert comp_ne == vm_ne
        # Equal case
        vm_eq, comp_eq = self._combo(
            _make_wide_equality(width),
            {"a": Value(a, width=width), "b": Value(a, width=width)},
        )
        assert comp_eq == vm_eq
        assert vm_eq == Value(1, width=1)

    @pytest.mark.parametrize("width", [65, 129])
    def test_cont_lt_cross_engine(self, width):
        a = self._W65 if width == 65 else self._W129
        b = self._W65b if width == 65 else (self._W129 >> 1)
        vm, comp = self._combo(
            _make_wide_cont_lt(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
        )
        assert comp == vm
        assert vm == Value(1 if a < b else 0, width=1)

    # ── Always-block operators (blocking assign) ──────────────────────────

    @pytest.mark.parametrize("width", [65, 129])
    def test_combo_add_cross_engine(self, width):
        a = self._W65 if width == 65 else self._W129
        b = self._W65b if width == 65 else (self._W129 >> 3)
        vm, comp = self._combo(
            _make_wide_combo_add(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value((a + b) & mask, width=width)

    @pytest.mark.parametrize("op,out_name", [("&", "y_and"), ("|", "y_or"), ("^", "y_xor")])
    def test_combo_bitwise_cross_engine(self, op, out_name):
        width = 65
        a, b = self._W65, self._W65b
        vm, comp = self._combo(
            _make_wide_combo_multi_bitwise(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
            output=out_name,
        )
        assert comp == vm

    def test_combo_sub_cross_engine(self):
        width = 65
        a, b = self._W65, self._W65b
        vm, comp = self._combo(
            _make_wide_combo_sub(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value((a - b) & mask, width=width)

    # ── Sequential operators (NBA) ────────────────────────────────────────

    @pytest.mark.parametrize("width", [65, 129])
    def test_seq_add_cross_engine(self, width):
        a = self._W65 if width == 65 else self._W129
        b = self._W65b if width == 65 else (self._W129 >> 3)
        vm, comp = self._seq(
            _make_wide_seq_add(width),
            {"a": Value(a, width=width), "b": Value(b, width=width), "q": Value(0, width=width)},
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value((a + b) & mask, width=width)

    @pytest.mark.parametrize("op,out_name", [("&", "q_and"), ("|", "q_or"), ("^", "q_xor")])
    def test_seq_bitwise_cross_engine(self, op, out_name):
        width = 65
        a, b = self._W65, self._W65b
        vm, comp = self._seq(
            _make_wide_seq_multi_bitwise(width),
            {
                "a": Value(a, width=width),
                "b": Value(b, width=width),
                "q_and": Value(0, width=width),
                "q_or": Value(0, width=width),
                "q_xor": Value(0, width=width),
            },
            output=out_name,
        )
        assert comp == vm

    # ── Phase 2.1: struct field reads as wide subexpressions ─────────────

    @pytest.mark.parametrize(
        "op,out_name,py_op",
        [
            ("&", "y_and", lambda a, b: a & b),
            ("|", "y_or", lambda a, b: a | b),
            ("^", "y_xor", lambda a, b: a ^ b),
        ],
    )
    def test_cont_struct_field_binop_cross_engine(self, op, out_name, py_op):
        """Struct signal field reads in a compound expr: assign y = bus_a.data <op> bus_b.data."""
        data_width = 65
        total_width = data_width + 1  # valid at bit 0, data at bits [65:1]
        data_a, data_b = self._W65, self._W65b
        packed_a = (data_a << 1) | 1  # valid=1
        packed_b = (data_b << 1) | 0  # valid=0
        vm, comp = self._combo(
            _make_wide_struct_field_multi_binop(),
            {
                "in_a": Value(packed_a, width=total_width),
                "in_b": Value(packed_b, width=total_width),
            },
            output=out_name,
        )
        assert comp == vm
        mask = (1 << data_width) - 1
        assert vm == Value(py_op(data_a, data_b) & mask, width=data_width)

    @pytest.mark.parametrize(
        "op,out_name,py_op",
        [
            ("&", "y_and", lambda a, b: a & b),
            ("|", "y_or", lambda a, b: a | b),
            ("^", "y_xor", lambda a, b: a ^ b),
        ],
    )
    def test_cont_struct_mem_field_binop_cross_engine(self, op, out_name, py_op):
        """Memory element struct field reads in a compound expr: assign y = mem[0].data <op> mem[1].data."""
        data_width = 65
        total_width = data_width + 1  # valid at bit 0, data at bits [65:1]
        data_a, data_b = self._W65, self._W65b
        packed_a = (data_a << 1) | 1  # valid=1
        packed_b = (data_b << 1) | 0  # valid=0
        vm, comp = self._combo(
            _make_wide_struct_mem_field_multi_binop(),
            {
                "in_a": Value(packed_a, width=total_width),
                "in_b": Value(packed_b, width=total_width),
            },
            output=out_name,
        )
        assert comp == vm
        mask = (1 << data_width) - 1
        assert vm == Value(py_op(data_a, data_b) & mask, width=data_width)

    # ── Phase 3: multiply, divide, modulo, signed comparisons ─────────────

    @pytest.mark.parametrize("width", [65, 129])
    def test_cont_mul_cross_engine(self, width):
        a = self._W65 if width == 65 else self._W129
        b = self._W65b if width == 65 else (self._W129 >> 3)
        vm, comp = self._combo(
            _make_wide_cont_mul(width),
            {"a": Value(a, width=width), "b": Value(b, width=width)},
        )
        assert comp == vm
        mask = (1 << width) - 1
        assert vm == Value((a * b) & mask, width=width)

    @pytest.mark.parametrize("op,py_op", [("/", lambda a, b: a // b), ("%", lambda a, b: a % b)])
    def test_cont_divmod_cross_engine(self, op, py_op):
        width = 65
        a_val = self._W65b  # positive 65-bit: bit64=0, about 9.2e18
        b_val = 0xDEAD_BEEF_CAFE  # about 2.4e13 (fits in 44 bits)
        mask = (1 << width) - 1
        vm, comp = self._combo(
            _make_wide_cont_divmod(width, op),
            {"a": Value(a_val, width=width), "b": Value(b_val, width=width)},
        )
        assert comp == vm
        assert vm == Value(py_op(a_val, b_val) & mask, width=width)

    @pytest.mark.parametrize("width", [65, 129])
    def test_cont_div_zero_divisor_returns_x(self, width):
        """Per Verilog spec: division by zero returns X (all bits unknown)."""
        a_val = self._W65 if width == 65 else self._W129
        vm, comp = self._combo(
            _make_wide_cont_divmod(width, "/"),
            {"a": Value(a_val, width=width), "b": Value(0, width=width)},
        )
        assert comp == vm
        assert vm == Value.x(width), f"divide by zero: expected all-X, got {vm}"

    @pytest.mark.parametrize("width", [65, 129])
    def test_cont_mod_zero_divisor_returns_x(self, width):
        """Per Verilog spec: modulo by zero returns X (all bits unknown)."""
        a_val = self._W65b if width == 65 else (self._W129 >> 3)
        vm, comp = self._combo(
            _make_wide_cont_divmod(width, "%"),
            {"a": Value(a_val, width=width), "b": Value(0, width=width)},
        )
        assert comp == vm
        assert vm == Value.x(width), f"modulo by zero: expected all-X, got {vm}"

    @pytest.mark.parametrize(
        "op,out_name,py_op",
        [
            ("<", "y_lt", lambda a, b: int(a < b)),
            ("<=", "y_le", lambda a, b: int(a <= b)),
            (">", "y_gt", lambda a, b: int(a > b)),
            (">=", "y_ge", lambda a, b: int(a >= b)),
        ],
    )
    def test_cont_signed_cmp_cross_engine(self, op, out_name, py_op):
        """Signed comparison: _W65 has sign bit set (negative), _W65b does not (positive)."""
        width = 65
        a_val = self._W65  # bit 64 = 1 → negative as signed 65-bit
        b_val = self._W65b  # bit 64 = 0 → positive as signed 65-bit
        sign_bit = 1 << (width - 1)

        def to_signed(v: int) -> int:
            return v - (1 << width) if v >= sign_bit else v

        sa, sb = to_signed(a_val), to_signed(b_val)
        vm, comp = self._combo(
            _make_wide_cont_multi_signed_cmp(width),
            {"a": Value(a_val, width=width), "b": Value(b_val, width=width)},
            output=out_name,
        )
        assert comp == vm
        assert vm == Value(py_op(sa, sb), width=1)


class TestWideDynamicPhase4:
    """Phase 4 behavioral cross-validation: dynamic shift amounts and runtime slice selects.

    Verifies that compiled engine matches vm engine when shift amounts and part-select
    bases are signals rather than compile-time constants.
    """

    _W65 = (1 << 64) | 0x1234_5678_9ABC_DEF0
    _W65b = (1 << 63) | 0x0FED_CBA9_8765_4321
    _W129 = (1 << 128) | (0xDEAD_BEEF_CAFE_BABE << 32) | 0x1234_5678

    @staticmethod
    def _combo(mod: Module, drives: dict, output: str = "y") -> tuple:
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(mod, engine=eng)
            for name, val in drives.items():
                sim.drive(name, val)
            sim.run(max_time=0)
            results[eng] = sim.read(output)
        return results["vm"], results["compiled"]

    @staticmethod
    def _seq(mod: Module, drives: dict, output: str = "q") -> tuple:
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(mod, engine=eng)
            for name, val in drives.items():
                sim.drive(name, val)
            clk = Clock(sim.signal("clk"), period=10)
            sim.fork(clk)
            sim._schedule_clock_events(clk, 40)
            for _ in range(2):
                sim.run_step()
            results[eng] = sim.read(output)
        return results["vm"], results["compiled"]

    # ── 4.1 Dynamic shift amount ──────────────────────────────────────────

    @pytest.mark.parametrize(
        "op,shamt",
        [
            (">>", 0),
            (">>", 4),
            (">>", 33),
            (">>", 64),
            ("<<", 0),
            ("<<", 4),
            ("<<", 33),
            ("<<", 64),
            (">>>", 4),
            (">>>", 33),
        ],
    )
    def test_combo_dyn_shift_cross_engine(self, op, shamt):
        width = 65
        a_val = self._W65
        vm, comp = self._combo(
            _make_wide_combo_dyn_shift(width, op),
            {"a": Value(a_val, width=width), "shamt": Value(shamt, width=7)},
        )
        assert comp == vm

    @pytest.mark.parametrize("op,shamt", [(">>", 4), ("<<", 8), (">>>", 16)])
    def test_seq_dyn_shift_cross_engine(self, op, shamt):
        width = 65
        a_val = self._W65
        vm, comp = self._seq(
            _make_wide_seq_dyn_shift(width, op),
            {"a": Value(a_val, width=width), "shamt": Value(shamt, width=7), "q": Value(0, width=width)},
        )
        assert comp == vm

    # ── 4.2 Dynamic part-select (wide LHS, dynamic base) ─────────────────

    @pytest.mark.parametrize("base", [0, 4, 32, 64])
    def test_combo_dyn_part_select_wide_lhs_cross_engine(self, base):
        """y = a[base +: 65] where a is 129-bit: wide result from dynamic PartSelect."""
        src_width, slice_width = 129, 65
        a_val = self._W129
        vm, comp = self._combo(
            _make_wide_combo_dyn_part_select(src_width, slice_width),
            {"a": Value(a_val, width=src_width), "base": Value(base, width=7)},
        )
        assert comp == vm
        expected = (a_val >> base) & ((1 << slice_width) - 1)
        assert vm == Value(expected, width=slice_width)

    @pytest.mark.parametrize("base", [0, 4, 32, 64])
    def test_combo_dyn_part_select_as_subexpr_cross_engine(self, base):
        """y = (a[base +: 65]) & b — dynamic part-select inside compound expression."""
        src_width, slice_width = 129, 65
        a_val, b_val = self._W129, self._W65
        vm, comp = self._combo(
            _make_wide_combo_dyn_part_select_subexpr(src_width, slice_width),
            {
                "a": Value(a_val, width=src_width),
                "b": Value(b_val, width=slice_width),
                "base": Value(base, width=7),
            },
        )
        assert comp == vm
        slice_val = (a_val >> base) & ((1 << slice_width) - 1)
        assert vm == Value(slice_val & b_val & ((1 << slice_width) - 1), width=slice_width)

    # ── 4.3 Dynamic range-select ──────────────────────────────────────────

    @pytest.mark.parametrize("lsb", [0, 4, 32, 64])
    def test_combo_dyn_range_select_wide_lhs_cross_engine(self, lsb):
        """y = a[lsb+64:lsb] where a is 129-bit: dynamic range-select, wide result."""
        src_width, slice_width = 129, 65
        a_val = self._W129
        vm, comp = self._combo(
            _make_wide_combo_dyn_range_select(src_width, slice_width),
            {"a": Value(a_val, width=src_width), "lsb": Value(lsb, width=7)},
        )
        assert comp == vm
        expected = (a_val >> lsb) & ((1 << slice_width) - 1)
        assert vm == Value(expected, width=slice_width)


class TestWideStructFieldSignals:
    """Cross-engine tests for reading/writing individual wide (>64-bit) packed struct fields.

    The module under test has a packed struct ``wide_bus_t { logic [64:0] data; logic valid; }``
    (66 bits total).  Ports: in_data (65-bit), in_valid (1-bit), out_bus (66-bit),
    out_data (65-bit), out_valid (1-bit).  The expected relationships are:
      out_data  == in_data
      out_valid == in_valid
      out_bus   == (in_data << 1) | in_valid   (packed: data occupies bits [65:1])
    """

    _DATA_VAL = (1 << 64) | 0x1234_5678_9ABC_DEF0  # 65-bit value, bit 64 set
    _DATA_WIDTH = 65
    _TOTAL_WIDTH = 66  # data + valid

    @staticmethod
    def _run_combo(module_fn, in_data: int, in_valid: int, engine: str) -> dict:
        sim = Simulator(module_fn(), engine=engine)
        sim.drive("in_data", Value(in_data, width=TestWideStructFieldSignals._DATA_WIDTH))
        sim.drive("in_valid", Value(in_valid, width=1))
        sim.run(max_time=0)
        return {
            "out_data": sim.read("out_data"),
            "out_bus": sim.read("out_bus"),
            "out_valid": sim.read("out_valid"),
        }

    @staticmethod
    def _run_seq(module_fn, in_data: int, in_valid: int, engine: str) -> dict:
        sim = Simulator(module_fn(), engine=engine)
        sim.drive("in_data", Value(in_data, width=TestWideStructFieldSignals._DATA_WIDTH))
        sim.drive("in_valid", Value(in_valid, width=1))
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim._schedule_clock_events(clk, 40)
        for _ in range(2):
            sim.run_step()
        return {
            "out_data": sim.read("out_data"),
            "out_bus": sim.read("out_bus"),
            "out_valid": sim.read("out_valid"),
        }

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_cont_field_write_reads_correct_data(self, engine):
        """Continuous assign to struct field: out_data == in_data."""
        result = self._run_combo(_make_wide_struct_signal_field_read_cont, self._DATA_VAL, 1, engine)
        assert result["out_data"] == Value(self._DATA_VAL, width=self._DATA_WIDTH), (
            f"{engine}: out_data={result['out_data']}"
        )

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_cont_field_write_reads_correct_valid(self, engine):
        """Continuous assign to struct field: out_valid == in_valid."""
        result = self._run_combo(_make_wide_struct_signal_field_read_cont, self._DATA_VAL, 1, engine)
        assert result["out_valid"] == Value(1, width=1), f"{engine}: out_valid={result['out_valid']}"

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_cont_field_write_packed_bus_correct(self, engine):
        """Continuous assign: out_bus == {bus.data, bus.valid} packed correctly."""
        expected_bus = (self._DATA_VAL << 1) | 1
        result = self._run_combo(_make_wide_struct_signal_field_read_cont, self._DATA_VAL, 1, engine)
        assert result["out_bus"] == Value(expected_bus, width=self._TOTAL_WIDTH), (
            f"{engine}: out_bus={result['out_bus']}, expected 0x{expected_bus:x}"
        )

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_combo_field_write_reads_correct_data(self, engine):
        """Combo always block to struct field: out_data == in_data."""
        result = self._run_combo(_make_wide_struct_signal_field_read_combo, self._DATA_VAL, 0, engine)
        assert result["out_data"] == Value(self._DATA_VAL, width=self._DATA_WIDTH), (
            f"{engine}: out_data={result['out_data']}"
        )

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_combo_field_write_packed_bus_correct(self, engine):
        """Combo always block: out_bus == {bus.data, bus.valid} packed correctly."""
        expected_bus = self._DATA_VAL << 1  # valid=0
        result = self._run_combo(_make_wide_struct_signal_field_read_combo, self._DATA_VAL, 0, engine)
        assert result["out_bus"] == Value(expected_bus, width=self._TOTAL_WIDTH), (
            f"{engine}: out_bus={result['out_bus']}, expected 0x{expected_bus:x}"
        )

    @pytest.mark.parametrize("engine", ["vm", "compiled"])
    def test_seq_field_write_reads_correct_data(self, engine):
        """Sequential (posedge) NBA to struct field: out_data == in_data after one cycle.

        Reference engine excluded: does not support incremental multi-cycle run_step().
        """
        result = self._run_seq(_make_wide_struct_signal_field_read_seq, self._DATA_VAL, 1, engine)
        assert result["out_data"] == Value(self._DATA_VAL, width=self._DATA_WIDTH), (
            f"{engine}: out_data={result['out_data']}"
        )

    @pytest.mark.parametrize("engine", ["vm", "compiled"])
    def test_seq_field_write_packed_bus_correct(self, engine):
        """Sequential (posedge) NBA: out_bus == {bus.data, bus.valid} packed correctly.

        Reference engine excluded: does not support incremental multi-cycle run_step().
        """
        expected_bus = (self._DATA_VAL << 1) | 1
        result = self._run_seq(_make_wide_struct_signal_field_read_seq, self._DATA_VAL, 1, engine)
        assert result["out_bus"] == Value(expected_bus, width=self._TOTAL_WIDTH), (
            f"{engine}: out_bus={result['out_bus']}, expected 0x{expected_bus:x}"
        )


class TestSignedDeclarationsCrossEngine:
    """Cross-engine validation: declared-signed signals produce correct signed results."""

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]

    def _run_all(self, module_fn, setup_fn, signals_to_check):
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(module_fn(), engine=eng)
            setup_fn(sim)
            sim.run(max_time=0)
            results[eng] = {name: sim.read(name) for name in signals_to_check}

        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            for sig in signals_to_check:
                assert results[eng][sig] == ref[sig], (
                    f"{eng} disagrees with reference on '{sig}': {results[eng][sig]} != {ref[sig]}"
                )

    # ── Signed comparison ─────────────────────────────────────────

    def test_signed_comparison_lt(self):
        """0xFF (-1) < 1 → true for signed, false for unsigned."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(1, width=8))

        self._run_all(_make_signed_cmp_module, setup, ["lt", "gt"])

    def test_signed_comparison_both_negative(self):
        """0x80 (-128) < 0xFF (-1) → true."""

        def setup(s):
            s.drive("a", Value(0x80, width=8))
            s.drive("b", Value(0xFF, width=8))

        self._run_all(_make_signed_cmp_module, setup, ["lt", "gt"])

    def test_signed_comparison_positive(self):
        """127 < 1 → false for both signed and unsigned."""

        def setup(s):
            s.drive("a", Value(127, width=8))
            s.drive("b", Value(1, width=8))

        self._run_all(_make_signed_cmp_module, setup, ["lt", "gt"])

    # ── Mixed signed/unsigned comparison ──────────────────────────

    def test_mixed_signed_unsigned_cmp(self):
        """0xFF signed vs 1 unsigned → unsigned compare (0xFF=255 > 1 → lt=0)."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(1, width=8))

        self._run_all(_make_mixed_signed_unsigned_cmp_module, setup, ["lt"])

    # ── Unsigned comparison (sanity check) ────────────────────────

    def test_unsigned_comparison(self):
        """0xFF (255) < 1 → false for unsigned."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(1, width=8))

        self._run_all(_make_unsigned_cmp_module, setup, ["lt", "gt"])

    # ── Signed widening ───────────────────────────────────────────

    def test_signed_widening_negative(self):
        """8-bit signed -1 (0xFF) → 16-bit should be 0xFFFF (sign-extend)."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))

        self._run_all(_make_signed_widen_module, setup, ["w"])

    def test_signed_widening_negative_128(self):
        """8-bit signed -128 (0x80) → 16-bit should be 0xFF80."""

        def setup(s):
            s.drive("a", Value(0x80, width=8))

        self._run_all(_make_signed_widen_module, setup, ["w"])

    def test_signed_widening_positive(self):
        """8-bit signed 127 (0x7F) → 16-bit should be 0x007F (no sign-ext)."""

        def setup(s):
            s.drive("a", Value(127, width=8))

        self._run_all(_make_signed_widen_module, setup, ["w"])

    # ── Signed arithmetic with widening ───────────────────────────

    def test_signed_arith_widen_negative(self):
        """a=-1, b=-1 → sum should be sign-extended -2 (0xFFFE)."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(0xFF, width=8))

        self._run_all(_make_signed_arith_widen_module, setup, ["sum"])

    def test_signed_arith_widen_positive(self):
        """a=127, b=127 → sum should be 254 (0x00FE)."""

        def setup(s):
            s.drive("a", Value(127, width=8))
            s.drive("b", Value(127, width=8))

        self._run_all(_make_signed_arith_widen_module, setup, ["sum"])

    def test_signed_arith_widen_mixed(self):
        """a=-128, b=1 → sum should be -127 (0xFF81)."""

        def setup(s):
            s.drive("a", Value(0x80, width=8))
            s.drive("b", Value(1, width=8))

        self._run_all(_make_signed_arith_widen_module, setup, ["sum"])

    # ── Signed arithmetic shift right ─────────────────────────────

    def test_signed_ashr_negative(self):
        """0xF0 (-16) >>> 2 → -4 (0xFC)."""

        def setup(s):
            s.drive("a", Value(0xF0, width=8))

        self._run_all(_make_signed_ashr_module, setup, ["y"])

    def test_signed_ashr_positive(self):
        """0x0F (15) >>> 2 → 3 (0x03)."""

        def setup(s):
            s.drive("a", Value(0x0F, width=8))

        self._run_all(_make_signed_ashr_module, setup, ["y"])

    # ── Signed multiplication ─────────────────────────────────────

    def test_signed_mul_neg_neg(self):
        """(-1) * (-1) = 1 → 0x0001."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(0xFF, width=8))

        self._run_all(_make_signed_mul_module, setup, ["prod"])

    def test_signed_mul_neg_pos(self):
        """(-1) * 1 = -1 → 0xFFFF."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))
            s.drive("b", Value(1, width=8))

        self._run_all(_make_signed_mul_module, setup, ["prod"])

    def test_signed_mul_pos_pos(self):
        """10 * 10 = 100 → 0x0064."""

        def setup(s):
            s.drive("a", Value(10, width=8))
            s.drive("b", Value(10, width=8))

        self._run_all(_make_signed_mul_module, setup, ["prod"])

    def test_signed_mul_neg128_2(self):
        """(-128) * 2 = -256 → 0xFF00."""

        def setup(s):
            s.drive("a", Value(0x80, width=8))
            s.drive("b", Value(2, width=8))

        self._run_all(_make_signed_mul_module, setup, ["prod"])

    # ── Signed division / modulus ─────────────────────────────────

    def test_signed_div_neg_pos(self):
        """-10 / 3 = -3 (0xFD),  -10 % 3 = -1 (0xFF)."""

        def setup(s):
            s.drive("a", Value(0xF6, width=8))  # -10
            s.drive("b", Value(3, width=8))

        self._run_all(_make_signed_div_module, setup, ["quot", "rem"])

    def test_signed_div_pos_neg(self):
        """10 / -3 = -3 (0xFD),  10 % -3 = 1 (0x01)."""

        def setup(s):
            s.drive("a", Value(10, width=8))
            s.drive("b", Value(0xFD, width=8))  # -3

        self._run_all(_make_signed_div_module, setup, ["quot", "rem"])

    def test_signed_div_neg_neg(self):
        """-10 / -3 = 3 (0x03),  -10 % -3 = -1 (0xFF)."""

        def setup(s):
            s.drive("a", Value(0xF6, width=8))  # -10
            s.drive("b", Value(0xFD, width=8))  # -3

        self._run_all(_make_signed_div_module, setup, ["quot", "rem"])

    # ── Unary negation ────────────────────────────────────────────

    def test_signed_uneg_positive(self):
        """-5 → -5 = 0xFFFB (sign-extended to 16-bit)."""

        def setup(s):
            s.drive("a", Value(5, width=8))

        self._run_all(_make_signed_uneg_module, setup, ["y"])

    def test_signed_uneg_negative(self):
        """-(-1) → 1 = 0x0001."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))  # -1

        self._run_all(_make_signed_uneg_module, setup, ["y"])

    def test_signed_uneg_zero(self):
        """-0 → 0."""

        def setup(s):
            s.drive("a", Value(0, width=8))

        self._run_all(_make_signed_uneg_module, setup, ["y"])

    # ── $signed() continuous assign to wider target ────────────────

    def test_signed_call_cont_assign_widening(self):
        """$signed(a) continuous assign: 0xFF → 0xFFFF (sign-extend)."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))

        self._run_all(_make_signed_call_cont_assign_widen_module, setup, ["w"])

    def test_signed_call_cont_assign_no_extend(self):
        """$signed(a) continuous assign: 0x7F → 0x007F (no sign-ext)."""

        def setup(s):
            s.drive("a", Value(0x7F, width=8))

        self._run_all(_make_signed_call_cont_assign_widen_module, setup, ["w"])

    # ── $unsigned() continuous assign cancels sign extension ───────

    def test_unsigned_call_cont_assign_widening(self):
        """$unsigned(signed_wire) continuous assign: 0xFF → 0x00FF (zero-extend)."""

        def setup(s):
            s.drive("a", Value(0xFF, width=8))

        self._run_all(_make_unsigned_call_cont_assign_widen_module, setup, ["w"])


class TestDslSignedWidening:
    """DSL-level cross-engine tests for signed/unsigned wire widening via continuous assign."""

    ENGINES = ["reference", "vm", "vm-fast", "compiled"]

    def _run(self, module_fn, drives: dict, expected: Value):
        results = {}
        for eng in self.ENGINES:
            mod = module_fn() if callable(module_fn) else module_fn
            sim = Simulator(mod, engine=eng)
            for name, val in drives.items():
                sim.drive(name, val)
            sim.settle()
            results[eng] = sim.read("w")
        ref = results["reference"]
        for eng in self.ENGINES:
            assert results[eng] == ref, f"{eng} != reference: {results[eng]} != {ref}"
        assert ref == expected, f"expected {expected}, got {ref}"

    def test_signed_wire_ca_widening(self):
        """DSL: signed wire → wider unsigned output via continuous assign signs-extends."""
        m = _make_dsl_signed_wire_widen_module()
        self._run(m, {"a": Value(0xFF, width=8)}, Value(0xFFFF, width=16))

    def test_signed_wire_ca_widening_positive(self):
        """DSL: signed wire 0x7F → wider unsigned output no extension needed."""
        m = _make_dsl_signed_wire_widen_module()
        self._run(m, {"a": Value(0x7F, width=8)}, Value(0x007F, width=16))

    def test_unsigned_wire_ca_widening(self):
        """DSL: unsigned wire → wider output zero-extends (default)."""
        m = _make_dsl_unsigned_wire_widen_module()
        self._run(m, {"a": Value(0xFF, width=8)}, Value(0x00FF, width=16))

    def test_signed_wire_unsigned_cast_ca(self):
        """DSL: unsigned(signed_wire) → wider output zero-extends."""
        m = _make_dsl_signed_wire_unsigned_cast_module()
        self._run(m, {"a": Value(0xFF, width=8)}, Value(0x00FF, width=16))

    def test_unsigned_wire_signed_cast_ca(self):
        """DSL: signed(unsigned_wire) → wider output sign-extends."""
        m = _make_dsl_unsigned_wire_signed_cast_module()
        self._run(m, {"a": Value(0xFF, width=8)}, Value(0xFFFF, width=16))


class TestSignedAssignmentCrossEngine:
    """Cross-engine validation: signed narrowing assignments in processes
    (always blocks) sign-extend correctly, matching continuous-assign behavior."""

    ENGINES = ["reference", "vm", "compiled"]

    def _run(self, module_fn, drives: dict, signals_to_check: list):
        # `clk`, when present, needs a REAL 0->1 transition (not just a
        # single "drive it straight to 1, then settle()" call) to fire an
        # `always @(posedge clk)` process per standard Verilog semantics --
        # a bare drive-to-1 with no prior observed value only *happens* to
        # fire on `compiled` because of a bootstrap-settle bug (see
        # `tests/test_sim/compiled/test_scheduling.py`'s
        # `TestBootstrapSettleFalsePosedge`), now fixed. Driving clk low
        # first, settling, then driving it high makes the edge explicit and
        # unambiguous on every engine, matching the sibling clock tests
        # in `test_scheduling.py`.
        non_clk_drives = {name: val for name, val in drives.items() if name != "clk"}
        has_clk = "clk" in drives
        results = {}
        for eng in self.ENGINES:
            sim = Simulator(module_fn(), engine=eng)
            for name, val in non_clk_drives.items():
                sim.drive(name, val)
            if has_clk:
                sim.drive("clk", Value(0, width=1))
                sim.settle()
                sim.drive("clk", Value(1, width=1))
            sim.settle()
            results[eng] = {name: sim.read(name) for name in signals_to_check}
        ref = results["reference"]
        for eng in ["vm", "compiled"]:
            for sig in signals_to_check:
                assert results[eng][sig] == ref[sig], (
                    f"{eng} disagrees with reference on '{sig}': {results[eng][sig]} != {ref[sig]}"
                )

    # ── NBA: signed wire → wider register ──────────────────────────

    def test_signed_nba_wider_register(self):
        """signed 16-bit 0xFFE3 → signed 32-bit reg: 0xFFFFFFE3 (-29)."""
        self._run(
            _make_signed_nba_widen_module,
            {"a": Value(0xFFE3, width=16), "clk": Value(1)},
            ["o"],
        )

    def test_signed_nba_positive_no_extend(self):
        """signed 16-bit 0x007F → signed 32-bit reg: 0x0000007F (127)."""
        self._run(
            _make_signed_nba_widen_module,
            {"a": Value(0x007F, width=16), "clk": Value(1)},
            ["o"],
        )

    def test_unsigned_nba_no_signed_extend(self):
        """unsigned 16-bit 0xFFE3 → unsigned 32-bit reg: 0x0000FFE3."""
        self._run(
            _make_unsigned_nba_widen_module,
            {"a": Value(0xFFE3, width=16), "clk": Value(1)},
            ["o"],
        )

    # ── CA: range-select on signed signal → wider wire ─────────────

    def test_signed_range_select_ca_wider(self):
        """signed 16-bit[7:0] (0xE3=-29) → signed 32-bit: 0xFFFFFFE3."""
        self._run(
            _make_signed_range_select_ca_module,
            {"a": Value(0xFFE3, width=16)},
            ["o"],
        )

    def test_signed_range_select_ca_positive(self):
        """signed 16-bit[7:0] (0x7F=127) → signed 32-bit: 0x0000007F."""
        self._run(
            _make_signed_range_select_ca_module,
            {"a": Value(0x007F, width=16)},
            ["o"],
        )

    # ── CA: part-select-like (range) on signed signal → wider wire ──

    def test_signed_range_upper_ca_wider(self):
        """signed 16-bit[15:8] (0xFF=-1) → signed 32-bit: 0xFFFFFFFF."""
        self._run(
            _make_signed_range_upper_ca_module,
            {"a": Value(0xFF00, width=16)},
            ["o"],
        )

    # ── NBA: $signed() in process → wider register ─────────────────

    def test_signed_call_nba_wider(self):
        """$signed(16-bit 0xFFE3) → signed 32-bit reg: 0xFFFFFFE3."""
        self._run(
            _make_signed_call_nba_widen_module,
            {"a": Value(0xFFE3, width=16), "clk": Value(1)},
            ["o"],
        )

    # ── NBA: signed wire same width (no sign-ext needed) ───────────

    def test_signed_nba_same_width(self):
        """signed 16-bit → signed 16-bit reg: no sign extension needed."""
        self._run(
            _make_signed_nba_same_width_module,
            {"a": Value(0xFFE3, width=16), "clk": Value(1)},
            ["o"],
        )

    # ── Signed comparison in process ────────────────────────────────

    def test_signed_comparison_in_process(self):
        """(-1 < 1) → lt=1 when both signed in always block."""
        self._run(
            _make_signed_cmp_process_module,
            {"a": Value(0xFF, width=8), "b": Value(1, width=8)},
            ["lt"],
        )


class TestTernaryChain32bit:
    """Cross-engine correctness for 32-bit ternary chains (mirrors gfwx-fpga pattern)."""

    def _run_cross(self, mod, drives: dict, expected_val: int, width: int = 32):
        """Drive inputs, settle, and assert compiled matches vm-fast."""
        from veriforge.sim.testbench import Simulator

        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            for name, val in drives.items():
                if isinstance(val, Value):
                    sim.drive(name, val)
                else:
                    sim.drive(name, Value(val, width=1) if name.startswith("sel") else Value(val, width=width))
            sim.settle()
            results[engine] = sim.read("result")

        assert results["compiled"] == results["vm-fast"], (
            f"compiled={results['compiled']!r} != vm-fast={results['vm-fast']!r}"
        )
        assert results["compiled"] == Value(expected_val, width=width), (
            f"compiled={results['compiled']!r}, expected Value({expected_val:#010x}, width={width})"
        )

    def test_chain_3_select_first(self):
        mod = _make_ternary_chain_32bit_module(3)
        self._run_cross(
            mod,
            {"sel0": 1, "sel1": 0, "sel2": 0, "d0": 0x11111111, "d1": 0x22222222, "d2": 0x33333333, "d3": 0x44444444},
            0x11111111,
        )

    def test_chain_3_select_last(self):
        mod = _make_ternary_chain_32bit_module(3)
        self._run_cross(
            mod,
            {"sel0": 0, "sel1": 0, "sel2": 0, "d0": 0x11111111, "d1": 0x22222222, "d2": 0x33333333, "d3": 0x44444444},
            0x44444444,
        )

    def test_chain_3_select_middle(self):
        mod = _make_ternary_chain_32bit_module(3)
        self._run_cross(
            mod,
            {"sel0": 0, "sel1": 0, "sel2": 1, "d0": 0x11111111, "d1": 0x22222222, "d2": 0x33333333, "d3": 0x44444444},
            0x33333333,
        )

    def test_chain_24_select_first(self):
        """24-deep chain (gfwx-fpga depth): selects d0."""
        k = 24
        mod = _make_ternary_chain_32bit_module(k, name="ternary_norm_sum_like")
        drives = {f"sel{i}": (1 if i == 0 else 0) for i in range(k)}
        drives.update({f"d{i}": 0x0880014C + i for i in range(k + 1)})
        self._run_cross(mod, drives, 0x0880014C)

    def test_chain_24_select_last(self):
        """24-deep chain: all selectors 0, selects d24 (innermost fallthrough)."""
        k = 24
        mod = _make_ternary_chain_32bit_module(k, name="ternary_norm_sum_last")
        drives = {f"sel{i}": 0 for i in range(k)}
        drives.update({f"d{i}": 0x0880014C + i for i in range(k + 1)})
        # d24 = 0x0880014C + 24 = 0x08800164
        self._run_cross(mod, drives, 0x0880014C + k)

    def test_chain_24_each_level(self):
        """24-deep chain: test each selector selecting its branch."""
        k = 24
        mod = _make_ternary_chain_32bit_module(k, name="ternary_norm_sum_levels")
        base_val = 0x10000000
        for chosen in range(k):
            drives = {f"sel{i}": (1 if i == chosen else 0) for i in range(k)}
            drives.update({f"d{i}": base_val + i for i in range(k + 1)})
            self._run_cross(mod, drives, base_val + chosen)

    def test_chain_wide_branch_select_narrow(self):
        """Ternary chain with wide-signal fallthrough, sel=1 (picks narrow data)."""
        mod = _make_ternary_chain_wide_branch_module(3)
        from veriforge.sim.testbench import Simulator

        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            sim.drive("sel0", Value(1, width=1))
            sim.drive("sel1", Value(0, width=1))
            sim.drive("sel2", Value(0, width=1))
            sim.drive("d0", Value(0x12345678, width=32))
            sim.drive("d1", Value(0xABCDEF01, width=32))
            sim.drive("d2", Value(0xDEADBEEF, width=32))
            sim.drive("w", Value(0x0880014C, width=128))
            sim.settle()
            results[engine] = sim.read("result")

        assert results["compiled"] == results["vm-fast"], (
            f"wide-branch: compiled={results['compiled']!r} != vm-fast={results['vm-fast']!r}"
        )
        assert results["compiled"] == Value(0x12345678, width=32)

    def test_chain_wide_branch_select_wide(self):
        """Ternary chain with wide-signal fallthrough, all sel=0 (picks w[31:0])."""
        mod = _make_ternary_chain_wide_branch_module(3)
        from veriforge.sim.testbench import Simulator

        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            sim.drive("sel0", Value(0, width=1))
            sim.drive("sel1", Value(0, width=1))
            sim.drive("sel2", Value(0, width=1))
            sim.drive("d0", Value(0x12345678, width=32))
            sim.drive("d1", Value(0xABCDEF01, width=32))
            sim.drive("d2", Value(0xDEADBEEF, width=32))
            sim.drive("w", Value(0x0880014C, width=128))
            sim.settle()
            results[engine] = sim.read("result")

        assert results["compiled"] == results["vm-fast"], (
            f"wide-branch fallthrough: compiled={results['compiled']!r} != vm-fast={results['vm-fast']!r}"
        )
        assert results["compiled"] == Value(0x0880014C, width=32)


class TestOrOfTernaries:
    """Cross-engine correctness for |/& chains of TernaryOps."""

    def _run_cross(self, mod, drives: dict, expected_val: int, width: int = 32):
        from veriforge.sim.testbench import Simulator

        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            for name, val in drives.items():
                w = 1 if name.startswith("sel") else width
                sim.drive(name, Value(val, width=w))
            sim.settle()
            results[engine] = sim.read("result")

        assert results["compiled"] == results["vm-fast"], (
            f"compiled={results['compiled']!r} != vm-fast={results['vm-fast']!r}"
        )
        assert results["compiled"] == Value(expected_val, width=width), (
            f"compiled={results['compiled']!r}, expected Value({expected_val:#010x})"
        )

    def test_or_of_2_ternaries_pick_first(self):
        mod = _make_or_of_ternaries_module(2)
        # sel0=1→a0, sel1=1→a1; result = a0 | a1
        self._run_cross(
            mod,
            {"sel0": 1, "sel1": 1, "a0": 0x0F0F0F0F, "b0": 0xF0F0F0F0, "a1": 0x00FF00FF, "b1": 0xFF00FF00},
            0x0F0F0F0F | 0x00FF00FF,
        )

    def test_or_of_2_ternaries_pick_second(self):
        mod = _make_or_of_ternaries_module(2)
        # sel0=0→b0, sel1=0→b1; result = b0 | b1
        self._run_cross(
            mod,
            {"sel0": 0, "sel1": 0, "a0": 0x0F0F0F0F, "b0": 0xF0F0F0F0, "a1": 0x00FF00FF, "b1": 0xFF00FF00},
            0xF0F0F0F0 | 0xFF00FF00,
        )

    def test_or_of_4_ternaries(self):
        mod = _make_or_of_ternaries_module(4)
        drives = {}
        for i in range(4):
            drives[f"sel{i}"] = 1
            drives[f"a{i}"] = 1 << (i * 8)
            drives[f"b{i}"] = 0xFF << (i * 8)
        # all sel=1 → OR of all a_i
        expected = sum(1 << (i * 8) for i in range(4))
        self._run_cross(mod, drives, expected)

    def test_or_chain(self):
        mod = _make_or_chain_of_sels_module()
        self._run_cross(
            mod,
            {"a": 0x00FF0000, "b": 0x0000FF00, "c": 0x000000FF, "d": 0x00000000},
            0x00FF0000 | 0x0000FF00 | 0x000000FF,
        )

    def test_or_of_ternaries_max_line_length(self):
        """Or-chain of TernaryOps: mask hoisting keeps line length O(k)."""
        for k in [5, 10, 20]:
            mod = _make_or_of_ternaries_module(k)
            cg = CythonCodegen()
            pyx = cg.generate(mod)
            max_len = max(len(line) for line in pyx.split("\n"))
            assert max_len < 800, f"k={k}: max line length {max_len} exceeds 800"
