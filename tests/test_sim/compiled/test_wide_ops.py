"""Compiled engine: wide (>64-bit) signal operations.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestWideUnifiedPhase0Codegen:
    """Phase 0: verify _gen_wide_primitives, _gen_wide_adapters, and scratch
    declaration emission without Cython compilation."""

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _narrow_module() -> Module:
        """Module with only narrow (<= 64-bit) signals."""
        return Module(
            "narrow_only",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(8)),
                Port("y", PortDirection.OUTPUT, width=_w(8)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(8)),
                Net("y", NetKind.WIRE, width=_w(8)),
            ],
            continuous_assigns=[ContinuousAssign(Identifier("y"), Identifier("a"))],
        )

    @staticmethod
    def _wide_module(width: int = 128) -> Module:
        """Module with a wide (> 64-bit) signal."""
        return _make_wide_passthrough(width)

    # ── _module_has_wide_state / _module_max_wide_words ──────────────────────

    def test_narrow_module_has_no_wide_state(self):
        cg = CythonCodegen()
        cg.generate(self._narrow_module())
        assert not cg._module_has_wide_state()

    def test_wide_module_has_wide_state(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module(128))
        assert cg._module_has_wide_state()

    def test_max_wide_words_narrow(self):
        cg = CythonCodegen()
        cg.generate(self._narrow_module())
        assert cg._module_max_wide_words() == 1

    def test_max_wide_words_128bit(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module(128))
        assert cg._module_max_wide_words() == 2

    def test_max_wide_words_192bit(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module(192))
        assert cg._module_max_wide_words() == 3

    def test_max_wide_words_65bit(self):
        """65-bit signal needs exactly 2 words."""
        cg = CythonCodegen()
        cg.generate(self._wide_module(65))
        assert cg._module_max_wide_words() == 2

    # ── _gen_wide_primitives ─────────────────────────────────────────────────

    def test_primitives_empty_for_narrow_module(self):
        cg = CythonCodegen()
        cg.generate(self._narrow_module())
        assert cg._gen_wide_primitives() == ""

    def test_primitives_present_for_wide_module(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_primitives()
        assert "Wide-value primitives" in code

    def test_primitives_contain_all_required_functions(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_primitives()
        expected = [
            "wide_copy",
            "wide_and",
            "wide_or",
            "wide_xor",
            "wide_not",
            "wide_neg",
            "wide_add",
            "wide_sub",
            "wide_shl",
            "wide_shr",
            "wide_ashr",
            "wide_slice_extract",
            "wide_cmp_eq",
            "wide_cmp_ne",
            "wide_reduce_or",
            "wide_reduce_and",
            "wide_reduce_xor",
            "wide_mux",
            "wide_logical_truth",
        ]
        for fn in expected:
            assert fn in code, f"Missing primitive: {fn}"

    def test_primitives_use_nogil_noexcept(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_primitives()
        assert "noexcept nogil" in code

    def test_primitives_use_unsigned_long_long_pointers(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_primitives()
        assert "unsigned long long *dv" in code
        assert "unsigned long long *dm" in code

    # ── _gen_wide_adapters ────────────────────────────────────────────────────

    def test_adapters_empty_for_narrow_module(self):
        cg = CythonCodegen()
        cg.generate(self._narrow_module())
        assert cg._gen_wide_adapters() == ""

    def test_adapters_present_for_wide_module(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_adapters()
        assert "wide_load_signal" in code
        assert "wide_store_signal" in code
        assert "wide_stage_signal" in code

    def test_adapters_load_reads_wide_val(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_adapters()
        assert "c.wide_val" in code
        assert "c.wide_mask" in code

    def test_adapters_store_sets_dirty_flag(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_adapters()
        assert "c.dirty[sid]" in code

    def test_adapters_stage_sets_nba_flags(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        code = cg._gen_wide_adapters()
        assert "c.nba_dirty[sid]" in code
        assert "c.nba_pending" in code

    # ── Sections list wires up primitives and adapters ────────────────────────

    def test_generate_wide_module_contains_primitives(self):
        cg = CythonCodegen()
        pyx = cg.generate(self._wide_module())
        assert "wide_copy" in pyx
        assert "wide_add" in pyx
        assert "wide_load_signal" in pyx
        assert "wide_store_signal" in pyx

    def test_generate_narrow_module_omits_primitives(self):
        cg = CythonCodegen()
        pyx = cg.generate(self._narrow_module())
        assert "wide_copy" not in pyx
        assert "wide_load_signal" not in pyx

    # ── Scratch allocator ─────────────────────────────────────────────────────

    def test_alloc_scratch_increments(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        cg._reset_scratch()
        s0 = cg._alloc_scratch()
        s1 = cg._alloc_scratch()
        assert s0 == 0
        assert s1 == 1
        assert cg._scratch_peak == 2

    def test_free_scratch_decrements(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        cg._reset_scratch()
        s0 = cg._alloc_scratch()
        s1 = cg._alloc_scratch()
        cg._free_scratch(s1)
        assert cg._scratch_slot_count == 1
        s2 = cg._alloc_scratch()
        assert s2 == 1
        assert cg._scratch_peak == 2

    def test_reset_scratch_clears_count(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        cg._alloc_scratch()
        cg._alloc_scratch()
        cg._reset_scratch()
        assert cg._scratch_slot_count == 0

    def test_peak_preserved_after_free(self):
        cg = CythonCodegen()
        cg.generate(self._wide_module())
        cg._reset_scratch()
        cg._alloc_scratch()
        cg._alloc_scratch()
        cg._alloc_scratch()
        cg._free_scratch(2)
        assert cg._scratch_peak == 3

    # ── Scratch declarations in process functions ─────────────────────────────

    def test_no_scratch_decls_without_sc_refs(self):
        """Process body without _sc* refs must not emit scratch declarations."""
        cg = CythonCodegen()
        pyx = cg.generate(self._wide_module())
        assert "cdef unsigned long long _sc0_v" not in pyx

    def test_scratch_decls_emitted_when_body_references_sc_slots(self):
        """Manually inject _sc0_v/_sc0_m into a body and check declarations appear."""
        from veriforge.sim.compiled.codegen import CythonCodegen as _CG

        cg = _CG()
        cg.generate(self._wide_module(128))
        max_words = cg._module_max_wide_words()

        cg._processes = [
            (
                [],
                [
                    "    wide_load_signal(c, 0, _sc0_v, _sc0_m, 2)",
                    "    wide_store_signal(c, 1, _sc0_v, _sc0_m, 2)",
                ],
            )
        ]
        cg._combo_processes = []
        cg._seq_processes = []

        pyx_body = cg._gen_process_functions()
        assert f"cdef unsigned long long _sc0_v[{max_words}]" in pyx_body
        assert f"cdef unsigned long long _sc0_m[{max_words}]" in pyx_body

    def test_scratch_decls_cover_all_slots_up_to_max(self):
        """If body references _sc2_v, declarations 0, 1, and 2 are all emitted."""
        from veriforge.sim.compiled.codegen import CythonCodegen as _CG

        cg = _CG()
        cg.generate(self._wide_module(128))
        max_words = cg._module_max_wide_words()

        cg._processes = [
            (
                [],
                [
                    "    wide_load_signal(c, 0, _sc0_v, _sc0_m, 2)",
                    "    wide_add(_sc2_v, _sc2_m, _sc0_v, _sc0_m, _sc1_v, _sc1_m, 2, 128)",
                    "    wide_store_signal(c, 1, _sc2_v, _sc2_m, 2)",
                ],
            )
        ]
        cg._combo_processes = []
        cg._seq_processes = []

        pyx_body = cg._gen_process_functions()
        for i in range(3):
            assert f"cdef unsigned long long _sc{i}_v[{max_words}]" in pyx_body
            assert f"cdef unsigned long long _sc{i}_m[{max_words}]" in pyx_body


class TestNarrowSignalsWideIntermediates:
    """Compiled engine: all declared signals ≤ 64 bits but intermediate
    expressions (shift, multiply, concat) exceed 64 bits, triggering the
    wide codegen path.  Verifies that wide helper functions are correctly
    emitted and N_WIDE_WORDS accommodates the intermediate widths."""

    ENGINES = ("reference", "vm-fast", "compiled")

    def _run(self, *build_fns):
        for build_fn in build_fns:
            m = build_fn()
            ref_val = None
            for engine in self.ENGINES:
                from veriforge.sim.testbench import Simulator

                sim = Simulator(m, engine=engine)
                sim.settle()
                for name, val in _DRIVE_NARROW_WIDE_INTERMEDIATES.get(m.name, {}).items():
                    sim.drive(name, val)
                sim.settle()
                got = sim.read("out")
                if ref_val is None:
                    ref_val = got
                assert got == ref_val, f"{m.name}: engine={engine} got={got!r} ref={ref_val!r}"

    # ── Shift-left into OR (the original bug pattern) ──────────────

    def test_shl_or_pack(self):
        self._run(_make_narrow_shl_or_pack_module)

    # ── Narrow multiply → wider result ─────────────────────────────

    def test_mul_narrow_to_wide(self):
        self._run(_make_narrow_mul_widen_module)

    # ── Narrow concat → wider result ───────────────────────────────

    def test_concat_narrow_to_wide(self):
        self._run(_make_narrow_concat_widen_module)

    # ── Direct shift → wider result ────────────────────────────────

    def test_direct_shift_to_wide(self):
        self._run(_make_narrow_direct_shift_module)

    # ── Shift + OR with different widths ───────────────────────────

    def test_shl_or_different_widths(self):
        self._run(_make_narrow_shl_or_diff_widths_module)


class TestWideUnifiedPhase1Emitter:
    """Phase 1: _emit_wide_expr_to_scratch and _emit_wide_lhs_write_new.

    All tests are pure codegen (no Cython compilation) and therefore fast.
    """

    @staticmethod
    def _cg_wide(width: int = 128) -> "CythonCodegen":
        cg = CythonCodegen()
        cg.generate(_make_wide_passthrough(width))
        return cg

    @staticmethod
    def _cg_two(width: int = 128) -> "CythonCodegen":
        """Module with two wide signals a and b → y (a op b)."""
        mod = Module(
            f"two_wide_{width}",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(width)),
                Port("b", PortDirection.INPUT, width=_w(width)),
                Port("y", PortDirection.OUTPUT, width=_w(width)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(width)),
                Net("b", NetKind.WIRE, width=_w(width)),
                Net("y", NetKind.WIRE, width=_w(width)),
            ],
            continuous_assigns=[ContinuousAssign(Identifier("y"), Identifier("a"))],
        )
        cg = CythonCodegen()
        cg.generate(mod)
        return cg

    # ── Helper: call _emit_wide_expr_to_scratch directly ─────────────────

    @staticmethod
    def _scratch(cg: "CythonCodegen", expr, dst_width: int = 128) -> list[str] | None:
        n_words = cg._module_max_wide_words()
        cg._reset_scratch()
        slot = cg._alloc_scratch()
        return cg._emit_wide_expr_to_scratch(expr, slot, n_words, dst_width, indent=1)

    # ── Identifier → wide_load_signal ────────────────────────────────────

    def test_identifier_emits_wide_load_signal(self):
        cg = self._cg_wide(128)
        sid = cg.signal_map["a"]
        lines = self._scratch(cg, Identifier("a"))
        assert lines is not None
        joined = "\n".join(lines)
        assert f"wide_load_signal(c, {sid}, _sc0_v, _sc0_m, 2)" in joined

    def test_unknown_identifier_returns_none(self):
        cg = self._cg_wide(128)
        lines = self._scratch(cg, Identifier("nonexistent_signal"))
        assert lines is None

    # ── Literal ──────────────────────────────────────────────────────────

    def test_zero_literal_emits_zeros(self):
        cg = self._cg_wide(128)
        lines = self._scratch(cg, Literal(0, width=128))
        assert lines is not None
        joined = "\n".join(lines)
        assert "_sc0_v[0] = 0" in joined
        assert "_sc0_v[1] = 0" in joined
        assert "_sc0_m[0] = 0" in joined

    def test_wide_literal_emits_correct_words(self):
        cg = self._cg_wide(128)
        val = (0xDEAD_BEEF << 64) | 0xCAFE_BABE
        lines = self._scratch(cg, Literal(val, width=128, original_text=f"128'h{val:032x}"))
        assert lines is not None
        joined = "\n".join(lines)
        assert "0xcafebabe" in joined.lower() or "0xCAFEBABE" in joined or "cafebabe" in joined.lower()

    # ── UnaryOp ──────────────────────────────────────────────────────────

    def test_bitwise_not_emits_wide_not(self):
        cg = self._cg_wide(128)
        expr = UnaryOp("~", Identifier("a"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_not(_sc0_v, _sc0_m, _sc1_v, _sc1_m, 2, 128)" in joined
        assert "wide_load_signal" in joined

    def test_negate_emits_wide_neg(self):
        cg = self._cg_wide(128)
        expr = UnaryOp("-", Identifier("a"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_neg(_sc0_v, _sc0_m, _sc1_v, _sc1_m, 2, 128)" in joined

    def test_logical_not_emits_reduce_or(self):
        """! on a wide operand: reduce-or then invert to produce a 1-bit result."""
        cg = self._cg_wide(128)
        expr = UnaryOp("!", Identifier("a"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_reduce_or" in joined

    def test_logical_not_result_is_masked_to_one_bit(self):
        """! result word 0 is masked to 1 bit; higher words are zeroed."""
        cg = self._cg_wide(128)
        expr = UnaryOp("!", Identifier("a"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "& 1ULL" in joined

    # ── BinaryOp — bitwise/arithmetic ────────────────────────────────────

    @pytest.mark.parametrize(
        "op,prim",
        [
            ("&", "wide_and"),
            ("|", "wide_or"),
            ("^", "wide_xor"),
            ("+", "wide_add"),
            ("-", "wide_sub"),
        ],
    )
    def test_binary_op_emits_correct_primitive(self, op, prim):
        cg = self._cg_two(128)
        expr = BinaryOp(op, Identifier("a"), Identifier("b"))
        lines = self._scratch(cg, expr)
        assert lines is not None, f"Expected lines for op '{op}'"
        joined = "\n".join(lines)
        assert prim in joined
        assert "_sc0_v" in joined
        assert "_sc1_v" in joined
        assert "_sc2_v" in joined

    def test_binary_uses_three_scratch_slots(self):
        cg = self._cg_two(128)
        cg._reset_scratch()
        slot = cg._alloc_scratch()
        cg._emit_wide_expr_to_scratch(BinaryOp("&", Identifier("a"), Identifier("b")), slot, 2, 128, indent=1)
        assert cg._scratch_peak >= 3  # dst + left + right

    # ── BinaryOp — shifts ─────────────────────────────────────────────────

    def test_shl_emits_wide_shl(self):
        cg = self._cg_wide(128)
        expr = BinaryOp("<<", Identifier("a"), Literal(4, width=8))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_shl" in joined
        assert "<int>(" in joined

    def test_shr_emits_wide_shr(self):
        cg = self._cg_wide(128)
        expr = BinaryOp(">>", Identifier("a"), Literal(1, width=8))
        lines = self._scratch(cg, expr)
        assert lines is not None
        assert "wide_shr" in "\n".join(lines)

    def test_ashr_emits_wide_ashr_with_src_width(self):
        cg = self._cg_wide(128)
        expr = BinaryOp(">>>", Identifier("a"), Literal(3, width=8))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_ashr" in joined
        # ashr must pass src_width before dst_width
        assert "128, 128" in joined  # src_width=128, dst_width=128

    # ── TernaryOp ────────────────────────────────────────────────────────

    def test_ternary_emits_wide_mux(self):
        cg = self._cg_two(128)
        expr = TernaryOp(Identifier("a"), Identifier("a"), Identifier("b"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_mux" in joined
        assert "cond_v" not in joined  # mux takes scalar scalars inline

    def test_ternary_uses_four_scratch_slots(self):
        cg = self._cg_two(128)
        cg._reset_scratch()
        slot = cg._alloc_scratch()
        cg._emit_wide_expr_to_scratch(
            TernaryOp(Identifier("a"), Identifier("a"), Identifier("b")), slot, 2, 128, indent=1
        )
        assert cg._scratch_peak >= 3  # dst + true + false

    # ── RangeSelect ──────────────────────────────────────────────────────

    def test_range_select_emits_wide_slice_extract(self):
        cg = self._cg_wide(128)
        expr = RangeSelect(Identifier("a"), Literal(127, width=32), Literal(64, width=32))
        lines = self._scratch(cg, expr, dst_width=64)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_slice_extract" in joined
        assert "64" in joined  # lsb
        assert "64" in joined  # width (127-64+1=64)

    def test_dynamic_range_select_emits_slice_extract(self):
        """Dynamic range selects now handled via runtime lsb/width expressions."""
        cg = self._cg_two(128)
        expr = RangeSelect(Identifier("a"), Identifier("b"), Literal(0, width=32))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_slice_extract" in joined
        assert "<int>" in joined  # runtime cast for dynamic bound

    # ── _emit_wide_lhs_write_new integration ─────────────────────────────

    def test_write_new_returns_none_for_narrow_dst(self):
        cg = CythonCodegen()
        cg.generate(_make_adder())
        sid = cg.signal_map["y"]
        result = cg._emit_wide_lhs_write_new(sid, Identifier("a"), indent=1, is_nba=False)
        assert result is None

    def test_write_new_emits_wide_store_for_blocking(self):
        cg = self._cg_wide(128)
        sid = cg.signal_map["y"]
        lines = cg._emit_wide_lhs_write_new(sid, Identifier("a"), indent=1, is_nba=False)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_store_signal" in joined
        assert f"c, {sid}" in joined

    def test_write_new_emits_wide_stage_for_nba(self):
        cg = self._cg_wide(128)
        sid = cg.signal_map["y"]
        lines = cg._emit_wide_lhs_write_new(sid, Identifier("a"), indent=1, is_nba=True)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_stage_signal" in joined

    def test_write_new_unknown_rhs_returns_none(self):
        """RHS with unresolvable FunctionCall → None (not yet handled)."""
        cg = self._cg_two(128)
        sid = cg.signal_map["y"]
        # Arbitrary FunctionCall (not $signed/$unsigned) is not handled
        func_call = FunctionCall("$clog2", [Identifier("a")])
        result = cg._emit_wide_lhs_write_new(sid, func_call, indent=1, is_nba=False)
        assert result is None

    # ── generate() now routes wide assignments through new emitter ────────

    def test_generate_wide_passthrough_uses_new_emitter(self):
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_passthrough(128))
        # New emitter emits wide_store_signal; old emitter emits _whole_assign_signal
        assert "wide_load_signal" in pyx
        assert "wide_store_signal" in pyx

    def test_generate_wide_literal_uses_new_emitter(self):
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_literal(128, 0xDEAD_BEEF_CAFE_BABE_1234_5678_9ABC_DEF0))
        assert "wide_store_signal" in pyx


class TestWideUnifiedPhase1Extended:
    """Phase 1.4: PartSelect, Reductions, Concatenation, Replication — pure codegen."""

    @staticmethod
    def _cg(width: int = 128) -> "CythonCodegen":
        cg = CythonCodegen()
        cg.generate(_make_wide_passthrough(width))
        return cg

    @staticmethod
    def _cg2(width: int = 128) -> "CythonCodegen":
        mod = Module(
            f"two_wide_{width}",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(width)),
                Port("b", PortDirection.INPUT, width=_w(width)),
                Port("y", PortDirection.OUTPUT, width=_w(width)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(width)),
                Net("b", NetKind.WIRE, width=_w(width)),
                Net("y", NetKind.WIRE, width=_w(width)),
            ],
            continuous_assigns=[ContinuousAssign(Identifier("y"), Identifier("a"))],
        )
        cg = CythonCodegen()
        cg.generate(mod)
        return cg

    @staticmethod
    def _scratch(cg: "CythonCodegen", expr, dst_width: int = 128) -> list[str] | None:
        n_words = cg._module_max_wide_words()
        cg._reset_scratch()
        slot = cg._alloc_scratch()
        return cg._emit_wide_expr_to_scratch(expr, slot, n_words, dst_width, indent=1)

    # ── PartSelect ────────────────────────────────────────────────────────

    def test_partselect_ascending_emits_slice_extract(self):
        cg = self._cg(128)
        expr = PartSelect(Identifier("a"), Literal(64, width=32), Literal(32, width=32), direction="+:")
        lines = self._scratch(cg, expr, dst_width=32)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_slice_extract" in joined

    def test_partselect_descending_emits_slice_extract(self):
        cg = self._cg(128)
        # a[95 -: 32]  → lsb = 95 - 32 + 1 = 64
        expr = PartSelect(Identifier("a"), Literal(95, width=32), Literal(32, width=32), direction="-:")
        lines = self._scratch(cg, expr, dst_width=32)
        assert lines is not None
        assert "wide_slice_extract" in "\n".join(lines)

    def test_partselect_dynamic_base_emits_slice_extract(self):
        """Dynamic base PartSelect now handled via runtime lsb expression."""
        cg = self._cg2(128)
        expr = PartSelect(Identifier("a"), Identifier("b"), Literal(32, width=32), direction="+:")
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_slice_extract" in joined
        assert "<int>" in joined  # runtime cast for dynamic base

    # ── Reduction operators ───────────────────────────────────────────────

    @pytest.mark.parametrize(
        "op,prim",
        [
            ("|", "wide_reduce_or"),
            ("&", "wide_reduce_and"),
            ("^", "wide_reduce_xor"),
        ],
    )
    def test_reduction_emits_correct_primitive(self, op, prim):
        cg = self._cg(128)
        expr = UnaryOp(op, Identifier("a"))
        lines = self._scratch(cg, expr, dst_width=1)
        assert lines is not None, f"Expected lines for reduction '{op}'"
        joined = "\n".join(lines)
        assert prim in joined

    def test_reduction_zeros_upper_words(self):
        """Reduction result is 1-bit — upper scratch words must be zeroed."""
        cg = self._cg(128)
        expr = UnaryOp("|", Identifier("a"))
        lines = self._scratch(cg, expr, dst_width=1)
        assert lines is not None
        joined = "\n".join(lines)
        assert "_sc0_v[1] = 0" in joined
        assert "_sc0_m[1] = 0" in joined

    def test_inverted_reduction_nand_flips_bit(self):
        cg = self._cg(128)
        expr = UnaryOp("~&", Identifier("a"))
        lines = self._scratch(cg, expr, dst_width=1)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_reduce_and" in joined
        assert "~_sc0_v[0]" in joined

    # ── Concatenation ─────────────────────────────────────────────────────

    def test_concat_two_wide_signals_emits_shift_or(self):
        cg = self._cg2(128)
        # {a[63:0], b[63:0]} — two 64-bit parts making 128 bits
        part_a = RangeSelect(Identifier("a"), Literal(63, width=32), Literal(0, width=32))
        part_b = RangeSelect(Identifier("b"), Literal(63, width=32), Literal(0, width=32))
        expr = Concatenation([part_a, part_b])
        lines = self._scratch(cg, expr, dst_width=128)
        assert lines is not None
        joined = "\n".join(lines)
        assert "wide_or" in joined
        assert "wide_shl" in joined

    def test_concat_zeros_slot_first(self):
        cg = self._cg(128)
        expr = Concatenation(
            [
                RangeSelect(Identifier("a"), Literal(127, width=32), Literal(64, width=32)),
                RangeSelect(Identifier("a"), Literal(63, width=32), Literal(0, width=32)),
            ]
        )
        lines = self._scratch(cg, expr, dst_width=128)
        assert lines is not None
        joined = "\n".join(lines)
        # First lines must zero the destination slot
        assert "_sc0_v[0] = 0" in joined
        assert "_sc0_m[0] = 0" in joined

    def test_concat_lsb_part_has_no_shift(self):
        """The LSB (last Verilog part) is ORed in with no shift — no wide_shl before first wide_or."""
        cg = self._cg(128)
        part_hi = RangeSelect(Identifier("a"), Literal(127, width=32), Literal(64, width=32))
        part_lo = RangeSelect(Identifier("a"), Literal(63, width=32), Literal(0, width=32))
        expr = Concatenation([part_hi, part_lo])
        lines = self._scratch(cg, expr, dst_width=128)
        assert lines is not None
        joined = "\n".join(lines)
        # wide_or should appear before any wide_shl (LSB part is zero-offset)
        first_or = joined.find("wide_or")
        first_shl = joined.find("wide_shl")
        assert first_or != -1
        assert first_shl != -1
        assert first_or < first_shl

    def test_concat_unknown_part_returns_none(self):
        """A concat part not supported by the emitter causes full None return."""
        cg = self._cg(128)
        # Arbitrary FunctionCall (not $signed/$unsigned) is not handled
        func_call = FunctionCall("$clog2", [Identifier("a")])
        expr = Concatenation([func_call, Identifier("a")])
        lines = self._scratch(cg, expr, dst_width=128)
        assert lines is None

    # ── Replication ───────────────────────────────────────────────────────

    def test_replication_emits_wide_replicate(self):
        cg = self._cg(128)
        expr = Replication(Literal(2, width=32), Identifier("a"))
        lines = self._scratch(cg, expr, dst_width=256)
        assert lines is not None
        assert "wide_replicate" in "\n".join(lines)

    def test_replication_zero_count_returns_none(self):
        cg = self._cg(128)
        expr = Replication(Literal(0, width=32), Identifier("a"))
        lines = self._scratch(cg, expr, dst_width=128)
        assert lines is None

    # ── Primitives list contains wide_replicate ───────────────────────────

    def test_primitives_contain_wide_replicate(self):
        cg = self._cg(128)
        code = cg._gen_wide_primitives()
        assert "wide_replicate" in code


class TestWideUnifiedPhase1Comparisons:
    """Phase 1.4 item 8-9: comparison operators — pure codegen (no Cython compile)."""

    @staticmethod
    def _cg2(width: int = 128) -> "CythonCodegen":
        """Module with two wide inputs `a`, `b` and one wide output `y`."""
        mod = Module(
            f"cmp_wide_{width}",
            ports=[
                Port("a", PortDirection.INPUT, width=_w(width)),
                Port("b", PortDirection.INPUT, width=_w(width)),
                Port("y", PortDirection.OUTPUT, width=_w(width)),
            ],
            nets=[
                Net("a", NetKind.WIRE, width=_w(width)),
                Net("b", NetKind.WIRE, width=_w(width)),
                Net("y", NetKind.WIRE, width=_w(width)),
            ],
            continuous_assigns=[ContinuousAssign(Identifier("y"), Identifier("a"))],
        )
        cg = CythonCodegen()
        cg.generate(mod)
        return cg

    @staticmethod
    def _scratch(cg: "CythonCodegen", expr, dst_width: int = 1) -> list[str] | None:
        n_words = cg._module_max_wide_words()
        cg._reset_scratch()
        slot = cg._alloc_scratch()
        return cg._emit_wide_expr_to_scratch(expr, slot, n_words, dst_width, indent=1)

    # ── Equality / inequality ─────────────────────────────────────────────

    @pytest.mark.parametrize(
        "op,prim",
        [
            ("==", "wide_cmp_eq"),
            ("===", "wide_cmp_eq"),
            ("!=", "wide_cmp_ne"),
            ("!==", "wide_cmp_ne"),
        ],
    )
    def test_equality_emits_correct_primitive(self, op, prim):
        cg = self._cg2(128)
        expr = BinaryOp(op, Identifier("a"), Identifier("b"))
        lines = self._scratch(cg, expr)
        assert lines is not None, f"Expected lines for op '{op}'"
        assert prim in "\n".join(lines)

    # ── Ordered comparisons ───────────────────────────────────────────────

    @pytest.mark.parametrize(
        "op,prim",
        [
            ("<", "wide_cmp_lt"),
            ("<=", "wide_cmp_le"),
            (">", "wide_cmp_lt"),  # swapped operands
            (">=", "wide_cmp_le"),  # swapped operands
        ],
    )
    def test_ordered_cmp_emits_correct_primitive(self, op, prim):
        cg = self._cg2(128)
        expr = BinaryOp(op, Identifier("a"), Identifier("b"))
        lines = self._scratch(cg, expr)
        assert lines is not None, f"Expected lines for op '{op}'"
        assert prim in "\n".join(lines)

    def test_gt_swaps_operands(self):
        """a > b  ≡  wide_cmp_lt(b, a): second arg slot should come before first."""
        cg = self._cg2(128)
        expr = BinaryOp(">", Identifier("a"), Identifier("b"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        # a is loaded first into _sc1_v (lslot), b into _sc2_v (rslot).
        # For >, we swap: primitive called as (dst, _sc2, _sc1) i.e. (b, a).
        cmp_line = next(l for l in lines if "wide_cmp_lt" in l)
        # Both slots should appear; b_slot (_sc2) should appear before a_slot (_sc1) in the call.
        assert "_sc2_v" in cmp_line
        assert "_sc1_v" in cmp_line
        assert cmp_line.index("_sc2_v") < cmp_line.index("_sc1_v")

    def test_comparison_zeros_upper_words(self):
        """Comparison result is 1-bit — upper scratch words must be zeroed."""
        cg = self._cg2(128)
        expr = BinaryOp("==", Identifier("a"), Identifier("b"))
        lines = self._scratch(cg, expr)
        assert lines is not None
        joined = "\n".join(lines)
        assert "_sc0_v[1] = 0" in joined
        assert "_sc0_m[1] = 0" in joined

    # ── Primitives presence ───────────────────────────────────────────────

    def test_primitives_contain_wide_cmp_lt(self):
        cg = self._cg2(128)
        code = cg._gen_wide_primitives()
        assert "wide_cmp_lt" in code

    def test_primitives_contain_wide_cmp_le(self):
        cg = self._cg2(128)
        code = cg._gen_wide_primitives()
        assert "wide_cmp_le" in code


class TestWideLogicalOps:
    """Logical operators on wide (>64-bit) signals must read all words correctly.

    The critical regression: if only the high word is nonzero (e.g. a=2**64),
    the old scalar path read c.val[sid]==0 and returned wrong results.
    """

    ENGINES = ["vm", "vm-fast", "compiled"]  # noqa: RUF012
    W = 65  # 65-bit operands: one bit beyond the first word

    # a value with only the 65th bit set — low 64 bits are zero
    HIGH_BIT = 1 << 64

    @staticmethod
    def _run(mod, inputs):
        results = {}
        for eng in ["vm", "compiled"]:
            sim = Simulator(mod, engine=eng)
            for name, val in inputs.items():
                sim.drive(name, val)
            sim.run(max_time=0)
            results[eng] = sim.read("y")
        return results["vm"], results["compiled"]

    # ── logical NOT ──────────────────────────────────────────────────────────

    def test_logical_not_zero_input(self):
        """!0 == 1 for wide zero."""
        vm, comp = self._run(_make_wide_logical_not(self.W), {"a": Value(0, width=self.W)})
        assert comp == vm
        assert vm == Value(1, width=1)

    def test_logical_not_low_bit_set(self):
        """!1 == 0 (nonzero in low word)."""
        vm, comp = self._run(_make_wide_logical_not(self.W), {"a": Value(1, width=self.W)})
        assert comp == vm
        assert vm == Value(0, width=1)

    def test_logical_not_high_bit_only(self):
        """!a == 0 when only the 65th bit is set — scalar path bug regression."""
        vm, comp = self._run(
            _make_wide_logical_not(self.W),
            {"a": Value(self.HIGH_BIT, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(0, width=1)

    # ── logical AND ──────────────────────────────────────────────────────────

    def test_logical_and_both_nonzero(self):
        """a && b == 1 when both operands are nonzero (value in high word)."""
        vm, comp = self._run(
            _make_wide_logical_and(self.W),
            {"a": Value(self.HIGH_BIT, width=self.W), "b": Value(1, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(1, width=1)

    def test_logical_and_one_zero(self):
        """a && b == 0 when a is zero (even though b is nonzero in high word)."""
        vm, comp = self._run(
            _make_wide_logical_and(self.W),
            {"a": Value(0, width=self.W), "b": Value(self.HIGH_BIT, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(0, width=1)

    def test_logical_and_both_high_bits(self):
        """a && b == 1 when both have only the high bit set."""
        vm, comp = self._run(
            _make_wide_logical_and(self.W),
            {"a": Value(self.HIGH_BIT, width=self.W), "b": Value(self.HIGH_BIT, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(1, width=1)

    # ── logical OR ───────────────────────────────────────────────────────────

    def test_logical_or_both_zero(self):
        """a || b == 0 when both operands are zero."""
        vm, comp = self._run(
            _make_wide_logical_or(self.W),
            {"a": Value(0, width=self.W), "b": Value(0, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(0, width=1)

    def test_logical_or_one_high_bit(self):
        """a || b == 1 when only 'a' has the 65th bit set — scalar path bug regression."""
        vm, comp = self._run(
            _make_wide_logical_or(self.W),
            {"a": Value(self.HIGH_BIT, width=self.W), "b": Value(0, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(1, width=1)

    def test_logical_or_only_b_nonzero(self):
        """a || b == 1 when only 'b' is nonzero in high word."""
        vm, comp = self._run(
            _make_wide_logical_or(self.W),
            {"a": Value(0, width=self.W), "b": Value(self.HIGH_BIT, width=self.W)},
        )
        assert comp == vm
        assert vm == Value(1, width=1)


class TestWideEdgeDetection:
    """Compiled engine rejects posedge/negedge on signals wider than 64 bits."""

    @staticmethod
    def _make_wide_posedge_module(sig_width: int) -> Module:
        """always @(posedge wide_clk) q <= 1; — posedge on a wide signal."""
        mod = Module(
            f"wide_posedge_{sig_width}",
            ports=[
                Port("wide_clk", PortDirection.INPUT, width=_w(sig_width)),
                Port("q", PortDirection.OUTPUT),
            ],
            nets=[Net("wide_clk", NetKind.WIRE, width=_w(sig_width))],
            variables=[Variable("q", VariableKind.REG)],
        )
        mod.always_blocks = [
            AlwaysBlock(
                BlockingAssign(Identifier("q"), Literal(1, width=1)),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("wide_clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            )
        ]
        return mod

    @pytest.mark.parametrize("sig_width", [65, 129])
    def test_posedge_wide_signal_raises(self, sig_width):
        """Compiled engine raises NotImplementedError for posedge on a wide signal."""
        with pytest.raises(NotImplementedError, match="posedge.*wide_clk"):
            Simulator(self._make_wide_posedge_module(sig_width), engine="compiled")

    @pytest.mark.parametrize("sig_width", [65, 129])
    def test_negedge_wide_signal_raises(self, sig_width):
        """Compiled engine raises NotImplementedError for negedge on a wide signal."""
        mod = self._make_wide_posedge_module(sig_width)
        mod.always_blocks[0].sensitivity_list[0] = SensitivityEdge("negedge", Identifier("wide_clk"))
        with pytest.raises(NotImplementedError, match="negedge.*wide_clk"):
            Simulator(mod, engine="compiled")

    @pytest.mark.parametrize("sig_width", [65, 129])
    @pytest.mark.parametrize("engine", ["vm", "vm-fast"])
    def test_posedge_wide_signal_ok_in_vm(self, sig_width, engine):
        """VM engines do not raise for posedge on a wide signal (no compiled restriction)."""
        Simulator(self._make_wide_posedge_module(sig_width), engine=engine)


class TestSignedWideShiftNarrow:
    """Regression: $signed(wide128) >>> N → narrow[31:0] must use full 128-bit path.

    Before the fix, _emit_wide_expr_to_scratch returned None for FunctionCall nodes,
    causing the B1 early-return in _emit_wide_py_bits_lines to route the assign to the
    CCA fallthrough, which only sees the low 64 bits via c.val[sid].
    """

    def _run_cross(self, shift_amt: int, w_val: int, expected_lo32: int):
        results = {}
        for engine in ("vm-fast", "compiled"):
            mod = _make_signed_wide_shift_module(shift_amt)
            sim = Simulator(mod, engine=engine)
            sim.drive("w", Value(w_val, width=128))
            sim.settle()
            results[engine] = sim.read("result")

        assert results["vm-fast"] == Value(expected_lo32 & 0xFFFFFFFF, width=32), (
            f"vm-fast sanity fail: shift={shift_amt}: got {results['vm-fast']!r}"
        )
        assert results["compiled"] == results["vm-fast"], (
            f"shift={shift_amt}: compiled={results['compiled']!r} != vm-fast={results['vm-fast']!r}"
        )

    def test_shift64_result_from_word1(self):
        """Shift=64: result comes from bits [95:64] of w (above low 64)."""
        # w[95:64] = 0xDEADBEEF, w[63:0] = 0 — positive (bit 127 clear)
        w = 0xDEADBEEF << 64
        self._run_cross(64, w, 0xDEADBEEF)

    def test_shift64_sign_extended_negative(self):
        """Shift=64: negative wide value sign-extends into result."""
        # w = 0xFFFFFFFFFFFFFFFF_0000000000000000 → signed(w) = -2^64 → >>> 64 = -1
        w = ((1 << 64) - 1) << 64
        self._run_cross(64, w, 0xFFFFFFFF)

    def test_shift96_result_from_word2(self):
        """Shift=96: result comes from bits [127:96] of w."""
        w = 0x12345678 << 96
        self._run_cross(96, w, 0x12345678)

    def test_shift64_golden_value(self):
        """Shift=64: the golden 0x0880014C value from the gfwx correctness test."""
        w = 0x0880014C << 64
        self._run_cross(64, w, 0x0880014C)

    def test_shift32_from_low_word(self):
        """Shift=32: result from low word bits [63:32] — both paths should agree."""
        # w = 0x...CAFEBABE_00000000 — bits [63:32] should be in result
        w = 0xCAFEBABE << 32
        self._run_cross(32, w, 0xCAFEBABE)
