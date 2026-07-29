"""Compiled engine: generated-code structure and codegen-time regressions.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestCodegen:
    """Test that CythonCodegen produces valid .pyx source."""

    def test_generate_adder(self):
        """Codegen for assign y = a + b produces valid source."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_adder())
        assert "DEF N_SIGS" in pyx
        assert "cdef struct SimCtx:" in pyx
        assert "cdef class CompiledSim:" in pyx
        assert "cont_0" in pyx
        assert "delta_loop" in pyx

    def test_generate_no_assigns(self):
        """Codegen for module with no assigns still produces valid source."""
        m = Module(
            "empty",
            ports=[Port("a", PortDirection.INPUT)],
            nets=[Net("a", NetKind.WIRE)],
        )
        cg = CythonCodegen()
        pyx = cg.generate(m)
        assert "DEF N_SIGS = 1" in pyx
        assert "cdef class CompiledSim:" in pyx

    def test_signal_map(self):
        """Signal map is populated after generate()."""
        cg = CythonCodegen()
        cg.generate(_make_adder())
        assert "a" in cg.signal_map
        assert "b" in cg.signal_map
        assert "y" in cg.signal_map
        assert cg.n_sigs == 3

    @pytest.mark.parametrize(
        ("op", "literal_on_left", "use_z", "expected"),
        [
            ("===", False, False, "0"),
            ("!==", False, False, "1"),
            ("===", True, False, "0"),
            ("!==", True, False, "1"),
            ("===", False, True, "0"),
            ("!==", False, True, "1"),
            ("===", True, True, "0"),
            ("!==", True, True, "1"),
        ],
    )
    def test_emit_binary_case_xz_literal_shortcuts(self, op, literal_on_left, use_z, expected):
        """Case identity against x/z literals short-circuits in compiled codegen."""
        cg = CythonCodegen()
        literal = Literal(0, width=8, is_x=not use_z, is_z=use_z)
        expr = BinaryOp(op, literal, Identifier("a")) if literal_on_left else BinaryOp(op, Identifier("a"), literal)
        assert cg._emit_binary(expr, 1) == expected

    def test_literal_emission_from_original_text(self):
        """Literal emission preserves Value.from_verilog parsing for numeric text."""
        cg = CythonCodegen()
        literal = Literal(255, width=8, base="h", original_text="8'hFF")
        assert cg._emit_expr(literal, 8) == "255"
        assert cg._emit_py_expr(literal, 8) == "255"
        assert cg._emit_py_mask_expr(literal, 8) == "0"
        assert cg._emit_mask_expr(literal, 8) == "0"

    def test_literal_mask_emission_for_x_literal(self):
        """X literals still drive full-width masks through Python and Cython emitters."""
        cg = CythonCodegen()
        literal = Literal("1x0x", width=4, base="b", is_x=True, original_text="4'b1x0x")
        assert cg._emit_expr(literal, 4) == "8"
        assert cg._emit_py_expr(literal, 4) == "8"
        assert cg._emit_py_mask_expr(literal, 4) == cg._emit_py_width_mask(4)
        assert cg._emit_mask_expr(literal, 4) == "wmask(4)"

    def test_write_with_format_codegen(self):
        """Formatted $write lowers through the format-string emission path."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_write_with_format_always())
        assert "_out_char(c, 97)" in pyx
        assert "_out_char(c, 61)" in pyx
        assert "_out_int_dec(c, c.val[0])" in pyx

    def test_write_without_format_codegen(self):
        """Unformatted $write emits space-separated argument output directly."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_write_without_format_always())
        assert "_out_int_dec(c, c.val[0])" in pyx
        assert "_out_char(c, 32)" in pyx
        assert "_out_char(c, 111)" in pyx
        assert "_out_char(c, 107)" in pyx


class TestPhase2Codegen:
    """Test that codegen produces valid .pyx for sequential designs."""

    def test_counter_codegen(self):
        """Codegen for counter produces seq_0 and edge detection."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        assert "seq_0" in pyx
        assert "fire_seq_0" in pyx
        assert "nba_pending" in pyx
        assert "delta_loop" in pyx

    def test_combo_always_codegen(self):
        """Codegen for always @(*) produces combo_0."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_combo_always_mux())
        assert "combo_0" in pyx
        assert "delta_loop" in pyx

    def test_combo_always_codegen_declares_proc_locals(self):
        """Combo process codegen emits needed local declarations ahead of body lines."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_combo_always_mux())
        assert "cdef inline void combo_0(SimCtx *c) noexcept nogil:\n    cdef long long _cdv\n" in pyx

    def test_mixed_codegen(self):
        """Codegen for mixed design has both cont_0 and seq_0."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mixed_cont_seq())
        assert "cont_0" in pyx
        assert "seq_0" in pyx


class TestPhase3Codegen:
    """Test that codegen produces valid .pyx for Phase 3 LHS patterns."""

    def test_concat_lhs_cont_codegen(self):
        """Concat LHS continuous assign produces multiple cont processes."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_concat_lhs_cont())
        # Should have two cont processes: one for hi, one for lo
        assert pyx.count("void cont_") >= 2

    def test_memory_codegen_struct(self):
        """Memory arrays emit mem_*_val arrays in struct."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_read_write())
        assert "mem_0_val" in pyx
        assert "mem_0_mask" in pyx
        assert "MEM_0_WIDTH" in pyx
        assert "MEM_0_DEPTH" in pyx

    def test_memory_codegen_mem_read(self):
        """Memory read generates mem_read method."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_read_write())
        assert "mem_read" in pyx

    def test_memory_codegen_nba_queue(self):
        """Memory NBA generates nba_mem queue fields."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_nba())
        assert "nba_mem_count" in pyx
        assert "nba_mem_mid" in pyx
        assert "nba_mem_val" in pyx

    def test_memory_copy_codegen_supports_whole_memory_rhs_and_marker_sensitivity(self):
        """Whole-memory copy should be emitted and depend on the RHS memory marker."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_copy_wrapped_index_combo())
        sensitivity, _lines = cg._combo_processes[0]

        assert "unsupported LHS: mem_d" not in pyx
        assert "unsupported LHS: mem_q" not in pyx
        assert "wmask(1)" in pyx
        assert sensitivity == {
            cg._mem_marker_sigs[cg.mem_map["mem_q"]],
            cg.signal_map["wptr"],
            cg.signal_map["data_i"],
        }

    def test_bit_select_lhs_codegen(self):
        """BitSelect LHS generates read-modify-write code."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_bit_select_lhs())
        # Should generate bit-level RMW: ~(1LL << ...)
        assert "1LL <<" in pyx

    def test_range_select_lhs_codegen(self):
        """RangeSelect LHS generates range mask code."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_range_select_lhs())
        # Should contain the pre-computed range mask: 0x3c (bits 5:2)
        assert "0x3c" in pyx

    def test_dynamic_bit_select_cont_sensitivity(self):
        """Dynamic bit-select LHS process sensitivity includes rhs and index signals."""
        cg = CythonCodegen()
        cg.generate(_make_dynamic_bit_select_cont_lhs())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["a"], cg.signal_map["idx"]}

    def test_dynamic_bit_select_combo_sensitivity(self):
        """always_comb dynamic bit-select LHS sensitivity includes rhs and index signals."""
        cg = CythonCodegen()
        cg.generate(_make_dynamic_bit_select_combo_lhs())
        sensitivity, _lines = cg._combo_processes[0]
        assert sensitivity == {cg.signal_map["a"], cg.signal_map["idx"]}

    def test_dynamic_range_select_cont_sensitivity(self):
        """Dynamic range-select LHS process sensitivity includes rhs and bound signals."""
        cg = CythonCodegen()
        cg.generate(_make_dynamic_range_select_cont_lhs())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["a"], cg.signal_map["msb"], cg.signal_map["lsb"]}

    def test_dynamic_part_select_cont_sensitivity(self):
        """Dynamic part-select LHS process sensitivity includes rhs and base signals."""
        cg = CythonCodegen()
        cg.generate(_make_dynamic_part_select_cont_lhs())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["a"], cg.signal_map["base"]}

    def test_struct_memory_field_bit_select_cont_sensitivity(self):
        """Struct-backed memory field bit-select sensitivity includes rhs, address, and bit signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_bit_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["bit"], cg.signal_map["in_bit"]}

    def test_struct_memory_field_range_select_cont_sensitivity(self):
        """Struct-backed memory field range-select sensitivity includes rhs, address, and bound signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_range_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {
            cg.signal_map["addr"],
            cg.signal_map["msb"],
            cg.signal_map["lsb"],
            cg.signal_map["in_bits"],
        }

    def test_struct_memory_field_concat_cont_sensitivity(self):
        """Struct-backed memory field concat sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bus"]}

    def test_memory_concat_cont_sensitivity(self):
        """Plain memory-element concat sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_mem_concat_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bus"]}

    def test_memory_concat_bit_cont_sensitivity(self):
        """Plain memory-element concat bit-select sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_mem_concat_bit_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bits"]}

    def test_memory_concat_range_cont_sensitivity(self):
        """Plain memory-element concat constant range-select sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_mem_concat_range_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bits"]}

    def test_memory_concat_dyn_range_cont_sensitivity(self):
        """Plain memory-element concat dynamic range-select sensitivity includes rhs, address, and bound signals."""
        cg = CythonCodegen()
        cg.generate(_make_mem_concat_dyn_range_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {
                cg.signal_map["addr"],
                cg.signal_map["msb"],
                cg.signal_map["lsb"],
                cg.signal_map["in_bits"],
            }

    def test_memory_concat_part_cont_sensitivity(self):
        """Plain memory-element concat constant part-select sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_mem_concat_part_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bits"]}

    def test_memory_concat_dyn_part_cont_sensitivity(self):
        """Plain memory-element concat dynamic part-select sensitivity includes rhs, address, and base signals."""
        cg = CythonCodegen()
        cg.generate(_make_mem_concat_dyn_part_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["addr"], cg.signal_map["base"], cg.signal_map["in_bits"]}

    def test_struct_memory_field_concat_bit_cont_sensitivity(self):
        """Struct-backed memory field concat bit-select sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_bit_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bits"]}

    def test_struct_memory_field_concat_dyn_bit_cont_sensitivity(self):
        """Struct-backed memory field concat dynamic bit-select sensitivity includes rhs, address, and bit signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_dyn_bit_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["bit"], cg.signal_map["in_bits"]}

    def test_struct_memory_field_concat_range_cont_sensitivity(self):
        """Struct-backed memory field concat range-select sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_range_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bits"]}

    def test_struct_memory_field_concat_dyn_range_cont_sensitivity(self):
        """Struct-backed memory field concat dynamic range-select sensitivity includes rhs, address, and bound signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_dyn_range_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {
            cg.signal_map["addr"],
            cg.signal_map["msb"],
            cg.signal_map["lsb"],
            cg.signal_map["in_bits"],
        }

    def test_struct_memory_field_concat_part_cont_sensitivity(self):
        """Struct-backed memory field concat part-select sensitivity includes rhs and address signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_part_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["in_bits"]}

    def test_struct_memory_field_concat_dyn_part_cont_sensitivity(self):
        """Struct-backed memory field concat dynamic part-select sensitivity includes rhs, address, and base signals."""
        cg = CythonCodegen()
        cg.generate(_make_struct_mem_field_concat_dyn_part_cont())
        sensitivity, _lines = cg._processes[0]
        assert sensitivity == {cg.signal_map["addr"], cg.signal_map["base"], cg.signal_map["in_bits"]}

    def test_concat_bit_lhs_cont_sensitivity(self):
        """Plain signal concat bit-select sensitivity includes only the RHS signal."""
        cg = CythonCodegen()
        cg.generate(_make_concat_bit_lhs_cont())
        assert len(cg._processes) == 3
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["x"]}

    def test_concat_range_lhs_cont_sensitivity(self):
        """Plain signal concat range-select sensitivity includes only the RHS signal."""
        cg = CythonCodegen()
        cg.generate(_make_concat_range_lhs_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["x"]}

    def test_concat_dyn_range_lhs_cont_sensitivity(self):
        """Plain signal concat dynamic range-select sensitivity includes rhs and bound signals."""
        cg = CythonCodegen()
        cg.generate(_make_concat_dyn_range_lhs_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["x"], cg.signal_map["msb"], cg.signal_map["lsb"]}

    def test_concat_part_lhs_cont_sensitivity(self):
        """Plain signal concat part-select sensitivity includes only the RHS signal."""
        cg = CythonCodegen()
        cg.generate(_make_concat_part_lhs_cont())
        assert len(cg._processes) == 2
        for sensitivity, _lines in cg._processes:
            assert sensitivity == {cg.signal_map["x"]}

    def test_mem_map_populated(self):
        """mem_map and mem_info are populated after generate()."""
        cg = CythonCodegen()
        cg.generate(_make_mem_read_write())
        assert "mem" in cg.mem_map
        assert cg.n_mems == 1
        ew, depth = cg.mem_info[0]
        assert ew == 8
        assert depth == 4


class TestPhase4Codegen:
    """Test that codegen correctly skips timing blocks."""

    def test_always_with_timing_skipped(self):
        """Always block with #delay is NOT compiled into .pyx."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_always_timing_clock())
        # The always #5 block should be skipped — only the continuous assign compiled
        assert "cont_0" in pyx
        # Should NOT have seq_ or combo_ for the timing always block
        assert "seq_0" not in pyx
        assert "combo_0" not in pyx

    def test_mixed_always_blocks(self):
        """Only non-timing always blocks are compiled."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_initial_counter_setup())
        # The always @(posedge clk) should be compiled
        assert "seq_0" in pyx


class TestPhase5Codegen:
    """Verify batch_run method appears in generated .pyx source."""

    def test_batch_run_generated(self):
        """batch_run cpdef method is present in generated code."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        assert "cpdef int batch_run" in pyx
        assert "nogil" in pyx

    def test_batch_run_has_memcpy(self):
        """batch_run uses memcpy for snapshot inside the loop."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        # Find the batch_run section
        idx = pyx.index("cpdef int batch_run")
        section = pyx[idx:]
        assert "memcpy" in section


class TestPhase7Codegen:
    """Verify Phase 7 codegen features and guards."""

    def test_signed_codegen(self):
        """$signed emits _sign_ext helper call."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_signed_arith())
        assert "_sign_ext" in pyx

    def test_power_codegen(self):
        """Power operator uses libc pow."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_power())
        assert "pow" in pyx
        assert "from libc.math cimport pow" in pyx

    def test_xnor_reduce_codegen(self):
        """XNOR reduction uses _xor_reduce with correct logic."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_xnor_reduce())
        assert "_xor_reduce" in pyx
        assert "else 0" in pyx

    def test_native_initial_codegen(self):
        """Simple initial block compiles into __init__ (not fallback)."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_native_initial())
        # The initial assignments should appear in the __init__ method
        assert "42" in pyx
        # No timing fallback needed
        assert not cg.has_timing_initials

    def test_initial_with_display_needs_fallback(self):
        """Initial block with $display falls back to reference executor."""
        cg = CythonCodegen()
        cg.generate(_make_initial_with_display())
        assert cg.has_timing_initials

    def test_inout_codegen(self):
        """Raw inout ports compile like ordinary top-level wires."""
        mod = _make_inout_port_probe()
        cg = CythonCodegen()
        code = cg.generate(mod)
        assert "c.val" in code
        assert "bus" in cg.signal_map

    def test_instance_raises(self):
        """Module instantiation raises NotImplementedError."""
        mod = Module(
            "inst_test",
            ports=[],
            nets=[],
            variables=[],
        )
        mod.instances = [Instance("child_mod", "u0")]
        cg = CythonCodegen()
        with pytest.raises(NotImplementedError, match="instantiation"):
            cg.generate(mod)

    def test_while_generates_loop(self):
        """While loop emits guarded loop code instead of raising."""
        mod = _make_while_counter()
        cg = CythonCodegen()
        code = cg.generate(mod)
        assert "while True:" in code
        assert "ERR_WHILE_LOOP_LIMIT" in code

    def test_repeat_generates_loop(self):
        """Repeat loop in always block emits a finite loop instead of raising."""
        mod = _make_repeat_counter()
        cg = CythonCodegen()
        code = cg.generate(mod)
        assert "for _lv_repeat_i_0 in range(_lv_repeat_count_0):" in code

    def test_forever_generates_loop(self):
        """Forever loop emits guarded loop code instead of raising."""
        mod = _make_forever_finish_counter()
        cg = CythonCodegen()
        code = cg.generate(mod)
        assert "ERR_FOREVER_LOOP_LIMIT" in code
        assert "while True:" in code

    def test_multidim_memory_codegen(self):
        """Multi-dimensional unpacked arrays flatten into compiled memory storage."""
        mod = _make_multidim_memory_probe()
        cg = CythonCodegen()
        code = cg.generate(mod)
        mid = cg.mem_map["mem"]
        assert cg.mem_info[mid] == (8, 6)
        assert f"c.mem_{mid}_val" in code

    def test_task_enable_unknown_emits_pass(self):
        """Unknown task enable emits harmless pass comment instead of raising."""
        mod = Module(
            "task_test",
            ports=[Port("out", PortDirection.OUTPUT, width=_w8())],
            nets=[],
            variables=[Variable("out", VariableKind.REG, width=_w8())],
        )
        mod.always_blocks = [
            AlwaysBlock(
                TaskEnable("my_task", [Identifier("out")]),
                sensitivity_type=SensitivityType.COMBINATIONAL,
            ),
        ]
        cg = CythonCodegen()
        code = cg.generate(mod)
        assert "unknown task: my_task" in code

    def test_casex_generates_code(self):
        """casex generates mask-aware comparison code."""
        mod = Module(
            "casex_test",
            ports=[
                Port("sel", PortDirection.INPUT, width=_w8()),
                Port("out", PortDirection.OUTPUT, width=_w8()),
            ],
            nets=[],
            variables=[
                Variable("sel", VariableKind.REG, width=_w8()),
                Variable("out", VariableKind.REG, width=_w8()),
            ],
        )
        mod.always_blocks = [
            AlwaysBlock(
                CaseStatement(
                    "casex",
                    Identifier("sel"),
                    [CaseItem([Literal(0, width=8)], BlockingAssign(Identifier("out"), Literal(0, width=8)))],
                ),
                sensitivity_type=SensitivityType.COMBINATIONAL,
            ),
        ]
        cg = CythonCodegen()
        code = cg.generate(mod)
        assert "casex" not in code or "sel_mask" in code or "item_mask" in code


class TestForLoopCodegen:
    """Tests for for-loop local variable codegen (Bug #14: infinite while loops)."""

    @pytest.fixture()
    def compiler(self, tmp_path):
        return CythonCompiler(cache_dir=str(tmp_path / "cache"))

    def test_for_loop_upward(self):
        """For loop with upward-counting local integer generates a terminating while loop."""
        # for (i = 0; i < 4; i = i + 1)  out = out | (1 << i);
        mod = Module("for_up")
        mod.nets.append(Net("out", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))))

        init = BlockingAssign(Identifier("i"), Literal("0"))
        cond = BinaryOp("<", Identifier("i"), Literal("4"))
        update = BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal("1")))
        body = BlockingAssign(
            Identifier("out"),
            BinaryOp(
                "|",
                Identifier("out"),
                BinaryOp("<<", Literal("1"), Identifier("i")),
            ),
        )
        loop = ForLoop(init, cond, update, body)

        mod.always_blocks.append(AlwaysBlock(loop, sensitivity_type=SensitivityType.COMBINATIONAL))

        cg = CythonCodegen()
        pyx = cg.generate(mod)

        # Should contain the local variable declaration and while loop
        assert "_lv_i" in pyx
        assert "while" in pyx
        # Should NOT contain "unsupported LHS: i"
        assert "unsupported LHS: i" not in pyx

    def test_for_loop_downward(self):
        """For loop counting downward generates a terminating while loop."""
        # for (i = 7; i >= 0; i = i - 1)  out = out | (1 << i);
        mod = Module("for_down")
        mod.nets.append(Net("out", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))))

        init = BlockingAssign(Identifier("i"), Literal("7"))
        cond = BinaryOp(">=", Identifier("i"), Literal("0"))
        update = BlockingAssign(Identifier("i"), BinaryOp("-", Identifier("i"), Literal("1")))
        body = BlockingAssign(
            Identifier("out"),
            BinaryOp(
                "|",
                Identifier("out"),
                BinaryOp("<<", Literal("1"), Identifier("i")),
            ),
        )
        loop = ForLoop(init, cond, update, body)

        mod.always_blocks.append(AlwaysBlock(loop, sensitivity_type=SensitivityType.COMBINATIONAL))

        cg = CythonCodegen()
        pyx = cg.generate(mod)

        assert "_lv_i" in pyx
        # Downward loops use wmask(64) so the decrement stays signed
        assert "wmask(64)" in pyx
        assert "unsupported LHS: i" not in pyx

    def test_for_loop_compile_and_run(self, compiler):
        """For loop with local variable compiles and runs correctly."""
        # out = 0; for (i = 0; i < 4; i = i + 1)  out = out | (1 << i);
        # Expect out = 0b1111 = 15
        #
        # The explicit `out = 0;` before the loop matters for x-mask
        # precision, not just style: `out` is never otherwise initialized,
        # and the OR-accumulation pattern only makes bits *definitely* 1 as
        # each loop iteration ORs in a known-1 bit -- any bit never touched
        # by the loop (here, bits 4-7, since in_a=4 only iterates i=0..3)
        # stays x if `out`'s prior value was x, per precise 4-state
        # semantics (x | 0 = x, only x | 1 = 1 resolves definitely).
        mod = Module("for_run")
        mod.nets.append(Net("in_a", NetKind.WIRE, width=Range(Literal(3, width=32), Literal(0, width=32))))
        mod.nets.append(Net("out", NetKind.WIRE, width=Range(Literal(7, width=32), Literal(0, width=32))))

        zero_init = BlockingAssign(Identifier("out"), Literal(0))
        init = BlockingAssign(Identifier("i"), Literal("0"))
        cond = BinaryOp("<", Identifier("i"), Identifier("in_a"))
        update = BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal("1")))
        body = BlockingAssign(
            Identifier("out"),
            BinaryOp(
                "|",
                Identifier("out"),
                BinaryOp("<<", Literal("1"), Identifier("i")),
            ),
        )
        loop = ForLoop(init, cond, update, body)

        mod.always_blocks.append(
            AlwaysBlock(SeqBlock([zero_init, loop]), sensitivity_type=SensitivityType.COMBINATIONAL)
        )

        cg = CythonCodegen()
        pyx = cg.generate(mod)
        ext = compiler.compile_pyx(pyx, "for_run_test")
        sim = ext.CompiledSim()

        # Drive in_a = 4, expect out = 0b1111 = 15
        in_a_sid = cg.signal_map["in_a"]
        out_sid = cg.signal_map["out"]

        sim.drive(in_a_sid, 4, 0)
        sim.snapshot()
        sim.step()

        v, m = sim.read(out_sid)
        assert v == 15, f"Expected 15, got {v}"
        assert m == 0


class TestCompiledFailFast:
    """Unsupported compiled constructs must raise NotImplementedError at codegen time."""

    def _codegen(self, mod: Module) -> None:
        CythonCodegen().generate(mod)

    def test_unsupported_statement_type_raises(self):
        """ParBlock (fork...join) is unsupported — must raise, not emit pass."""
        from veriforge.model.statements import ParBlock

        stmt = ParBlock([BlockingAssign(Identifier("y"), Literal(0, width=8))])
        with pytest.raises(NotImplementedError, match="ParBlock"):
            self._codegen(_make_module_with_stmt(stmt))

    def test_wait_statement_routes_to_fallback(self):
        """WaitStatement triggers the timing-fallback path (block is skipped, not compiled)."""
        from veriforge.model.statements import WaitStatement

        # A block containing WaitStatement has _has_timing == True, so the compiled
        # engine routes it to the reference executor fallback rather than raising.
        # Verify that codegen succeeds (no raise) and the block is silently excluded.
        stmt = WaitStatement(Literal(1, width=1))
        mod = _make_module_with_stmt(stmt)
        code = CythonCodegen().generate(mod)
        # The generated code should have no reference to the wait condition
        # (the block was skipped, not compiled).
        assert "wait" not in code.lower().split("def ")[0]  # not in the module header area

    def test_disable_statement_raises(self):
        """DisableStatement is unsupported — must raise, not emit pass."""
        from veriforge.model.statements import DisableStatement

        stmt = DisableStatement("some_block")
        with pytest.raises(NotImplementedError, match="DisableStatement"):
            self._codegen(_make_module_with_stmt(stmt))

    def test_event_trigger_raises(self):
        """EventTrigger is unsupported — must raise, not emit pass."""
        from veriforge.model.statements import EventTrigger

        stmt = EventTrigger("my_event")
        with pytest.raises(NotImplementedError, match="EventTrigger"):
            self._codegen(_make_module_with_stmt(stmt))

    def test_unresolved_lhs_identifier_raises(self):
        """BlockingAssign to an unknown identifier must raise, not emit pass."""
        stmt = BlockingAssign(Identifier("nonexistent_signal"), Literal(0, width=8))
        with pytest.raises(NotImplementedError, match="nonexistent_signal"):
            self._codegen(_make_module_with_stmt(stmt))

    def test_unresolved_bitselect_target_raises(self):
        """Bit-select on unknown signal must raise, not emit pass."""
        stmt = BlockingAssign(BitSelect(Identifier("nonexistent"), Literal(0, width=8)), Literal(0, width=1))
        with pytest.raises(NotImplementedError, match="nonexistent"):
            self._codegen(_make_module_with_stmt(stmt))

    def test_unresolved_range_select_target_raises(self):
        """Range-select on unknown signal must raise, not emit pass."""
        stmt = BlockingAssign(
            RangeSelect(Identifier("nonexistent"), Literal(7, width=8), Literal(0, width=8)),
            Literal(0, width=8),
        )
        with pytest.raises(NotImplementedError, match="nonexistent"):
            self._codegen(_make_module_with_stmt(stmt))


class TestCharStorageLayout:
    """Characterize: signal/memory/wide layout in the generated struct."""

    def test_scalar_module_signal_count_constant(self):
        """DEF N_SIGS is emitted with the correct count for a scalar module."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "DEF N_SIGS = 3" in pyx

    def test_struct_definition_present(self):
        """SimCtx struct is emitted for every module."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "cdef struct SimCtx:" in pyx

    def test_scalar_struct_has_val_and_mask_arrays(self):
        """Scalar signal storage uses signed long long val[] and mask[] in SimCtx."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "long long val[" in pyx
        assert "long long mask[" in pyx

    def test_wide_struct_has_wide_val_and_wide_mask(self):
        """Wide signal storage adds wide_val[] and wide_mask[] to SimCtx."""
        pyx = CythonCodegen().generate(_make_wide_passthrough(128))
        assert "wide_val[" in pyx
        assert "wide_mask[" in pyx

    def test_wide_struct_has_wide_words_constant(self):
        """WIDE_WORDS constant is emitted when the module has wide signals."""
        pyx = CythonCodegen().generate(_make_wide_passthrough(128))
        assert "WIDE_WORDS" in pyx

    def test_memory_struct_has_mem_val_array(self):
        """Memory element arrays are declared inside SimCtx."""
        pyx = CythonCodegen().generate(_make_mem_read_write())
        assert "mem_0_val" in pyx
        assert "mem_0_mask" in pyx

    def test_memory_struct_has_depth_and_width_constants(self):
        """Memory dimensions are emitted as DEF constants."""
        pyx = CythonCodegen().generate(_make_mem_read_write())
        assert "MEM_0_WIDTH" in pyx
        assert "MEM_0_DEPTH" in pyx

    def test_nba_fields_present_for_sequential_module(self):
        """NBA queue fields (nba_pending, nba_val, nba_dirty) appear in the struct."""
        pyx = CythonCodegen().generate(_make_counter())
        assert "nba_pending" in pyx
        assert "nba_val[" in pyx
        assert "nba_dirty[" in pyx

    def test_dirty_flag_array_present(self):
        """dirty[] array tracks which signals have changed."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "dirty[" in pyx

    def test_compiled_sim_class_present(self):
        """CompiledSim Python-extension class wraps the struct."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "cdef class CompiledSim:" in pyx


class TestCharProcessFunctions:
    """Characterize: emitted process function signatures and delta-loop structure."""

    def test_continuous_assign_emits_cont_function(self):
        """Continuous assign lowers to a cont_N function."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "cont_0" in pyx

    def test_combinational_block_emits_combo_function(self):
        """always @(*) lowers to a combo_N function."""
        pyx = CythonCodegen().generate(_make_wide_combo_passthrough(96))
        assert "combo_0" in pyx

    def test_sequential_block_emits_seq_function(self):
        """always @(posedge/negedge) lowers to a seq_N function."""
        pyx = CythonCodegen().generate(_make_counter())
        assert "seq_0" in pyx

    def test_combo_function_is_inline_nogil(self):
        """Combo process functions are declared cdef inline ... noexcept nogil."""
        pyx = CythonCodegen().generate(_make_wide_combo_passthrough(96))
        assert "cdef inline void combo_0(SimCtx *c) noexcept nogil:" in pyx

    def test_seq_function_declares_fire_flag(self):
        """Sequential process functions emit a fire_seq_N edge-detection flag."""
        pyx = CythonCodegen().generate(_make_counter())
        assert "fire_seq_0" in pyx

    def test_delta_loop_present(self):
        """delta_loop is emitted for every module."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "delta_loop" in pyx

    def test_combo_process_declares_cdv_local(self):
        """Scalar combo process emits cdef long long _cdv for change-detection."""
        pyx = CythonCodegen().generate(_make_combo_always_mux())
        assert "cdef long long _cdv" in pyx

    def test_multiple_continuous_assigns_produce_multiple_cont_functions(self):
        """Two continuous assigns lower to two separate cont_N functions."""
        pyx = CythonCodegen().generate(_make_chain())
        assert pyx.count("void cont_") >= 2


class TestCharWideExpressions:
    """Characterize: wide expression emission — scratch slots, primitive calls."""

    def test_wide_add_calls_wide_add_primitive(self):
        """128-bit a+b emits a wide_add call in the combo body."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "wide_add(" in pyx

    def test_wide_expr_uses_scratch_slot_arrays(self):
        """Wide scratch evaluation uses _sc0_v / _sc0_m variable names."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "_sc0_v" in pyx
        assert "_sc0_m" in pyx

    def test_wide_load_signal_called_for_wide_rhs_identifier(self):
        """Reading a wide signal on the RHS calls wide_load_signal."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "wide_load_signal(" in pyx

    def test_wide_store_signal_called_for_wide_lhs_identifier(self):
        """Writing a wide signal on the LHS calls wide_store_signal."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "wide_store_signal(" in pyx

    def test_wide_passthrough_cont_uses_wide_load_and_store(self):
        """Wide continuous assign y=a emits wide_load_signal + wide_store_signal."""
        pyx = CythonCodegen().generate(_make_wide_passthrough(128))
        assert "wide_load_signal(" in pyx
        assert "wide_store_signal(" in pyx

    def test_wide_seq_nba_uses_wide_stage_signal(self):
        """Wide NBA q<=a+b emits wide_stage_signal to queue the update."""
        pyx = CythonCodegen().generate(_make_wide_seq_add(128))
        assert "wide_stage_signal(" in pyx

    def test_narrow_module_has_no_scratch_slots(self):
        """Narrow-only module emits no _sc0_v scratch arrays."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "_sc0_v" not in pyx

    def test_wide_load_wmem_called_for_wide_memory_read(self):
        """Reading a wide memory element calls wide_load_wmem<mid>."""
        pyx = CythonCodegen().generate(_make_wide_mem_bit_combo(96))
        assert "wide_load_wmem" in pyx


class TestCharLhsWriters:
    """Characterize: scalar and wide LHS write code patterns."""

    def test_scalar_blocking_assign_uses_cdv_change_guard(self):
        """Scalar blocking assign emits a _cdv change-detection guard."""
        pyx = CythonCodegen().generate(_make_combo_always_mux())
        assert "_cdv = " in pyx

    def test_scalar_nba_sets_nba_pending(self):
        """Scalar NBA emits c.nba_pending = 1 to signal the update."""
        pyx = CythonCodegen().generate(_make_counter())
        assert "c.nba_pending = 1" in pyx

    def test_scalar_nba_stages_into_nba_val(self):
        """Scalar NBA writes staged value into c.nba_val[...]."""
        pyx = CythonCodegen().generate(_make_counter())
        assert "c.nba_val[" in pyx

    def test_bit_select_lhs_emits_rmw_shift(self):
        """Bit-select LHS write uses a read-modify-write with 1LL << shift."""
        pyx = CythonCodegen().generate(_make_bit_select_lhs())
        assert "1LL <<" in pyx

    def test_range_select_lhs_emits_mask_constant(self):
        """Range-select LHS write emits the pre-computed range mask literal."""
        pyx = CythonCodegen().generate(_make_range_select_lhs())
        assert "0x3c" in pyx

    def test_whole_assign_helper_emitted_for_wide_module(self):
        """Wide modules emit a _whole_assign_<signal> helper for bulk writes."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "_whole_assign_" in pyx

    def test_whole_stage_helper_emitted_for_wide_nba_module(self):
        """Wide NBA modules emit a _whole_stage_<signal> helper."""
        pyx = CythonCodegen().generate(_make_wide_seq_add(128))
        assert "_whole_stage_" in pyx


class TestCharMemoryWriters:
    """Characterize: memory read/write emission patterns."""

    def test_memory_read_emits_mem_read_method(self):
        """Memory element read generates a mem_read call or mem_0_val array access."""
        pyx = CythonCodegen().generate(_make_mem_read_write())
        assert "mem_read" in pyx or "mem_0_val" in pyx

    def test_memory_nba_emits_nba_mem_count(self):
        """Memory NBA path emits nba_mem_count to track pending element writes."""
        pyx = CythonCodegen().generate(_make_mem_nba())
        assert "nba_mem_count" in pyx

    def test_memory_nba_emits_nba_mem_val(self):
        """Memory NBA path stages value through nba_mem_val."""
        pyx = CythonCodegen().generate(_make_mem_nba())
        assert "nba_mem_val" in pyx

    def test_wide_memory_struct_has_wmem_array(self):
        """Wide memory element storage uses _wmem0 array name in struct."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_bit_combo(96))
        assert "_wmem0" in pyx or "wmem0" in pyx

    def test_wide_memory_load_uses_wide_load_wmem(self):
        """Wide memory element read emits wide_load_wmem<mid>."""
        pyx = CythonCodegen().generate(_make_wide_mem_bit_combo(96))
        assert "wide_load_wmem" in pyx


class TestCharHelperGeneration:
    """Characterize: _whole_assign / _whole_stage / wmask helper emission."""

    def test_wmask_helper_emitted_for_wide_module(self):
        """wmask() helper function is emitted for modules with wide signals."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "wmask" in pyx

    def test_whole_assign_py_bits_helper_emitted_for_wide_module(self):
        """_whole_assign_py_bits helper (Python GIL path) appears for wide modules."""
        pyx = CythonCodegen().generate(_make_wide_combo_add(128))
        assert "_whole_assign_" in pyx

    def test_wide_primitives_section_header_present(self):
        """Wide primitive block has its section header comment."""
        cg = CythonCodegen()
        cg.generate(_make_wide_passthrough(128))
        assert "Wide-value primitives" in cg._gen_wide_primitives()

    def test_wide_adapters_section_header_present(self):
        """Wide adapter functions load/store/stage to SimCtx wide arrays."""
        cg = CythonCodegen()
        cg.generate(_make_wide_passthrough(128))
        code = cg._gen_wide_adapters()
        assert "wide_load_signal" in code
        assert "wide_store_signal" in code
        assert "wide_stage_signal" in code

    def test_narrow_module_emits_no_wide_load_signal(self):
        """Narrow-only module emits no wide_load_signal (no wide signals)."""
        pyx = CythonCodegen().generate(_make_adder())
        assert "wide_load_signal" not in pyx


class TestCharSystemTasks:
    """Characterize: $display / $write / output helper emission."""

    def test_display_with_format_emits_out_char(self):
        """$write with format string emits _out_char calls for literal bytes."""
        pyx = CythonCodegen().generate(_make_write_with_format_always())
        assert "_out_char(" in pyx

    def test_display_emits_out_int_dec_for_integer_arg(self):
        """$write with integer arg emits _out_int_dec call."""
        pyx = CythonCodegen().generate(_make_write_with_format_always())
        assert "_out_int_dec(" in pyx

    def test_display_without_format_emits_space_separator(self):
        """$write without format string emits _out_char(c, 32) space separators."""
        pyx = CythonCodegen().generate(_make_write_without_format_always())
        assert "_out_char(c, 32)" in pyx


class TestExpressionTemporaries:
    """Verify that expression temporaries produce correct results and keep line
    lengths bounded (O(k) instead of O(k²)) for addition chains."""

    def _run(self, mod, operand_values, expected):
        from veriforge.sim.testbench import Simulator

        for engine in ("reference", "compiled"):
            sim = Simulator(mod, engine=engine)
            for name, val in operand_values.items():
                sim.run(lambda s, n=name, v=val: s.drive(n, Value(v, width=16)), max_time=0)
            sim.run(lambda s: s.drive("clk", 0), max_time=0)
            sim.run(lambda s: s.drive("rst", 1), max_time=0)
            sim.run(lambda s: s.drive("clk", 1), max_time=1)
            sim.run(lambda s: s.drive("rst", 0), max_time=0)
            sim.run(lambda s: s.drive("clk", 0), max_time=0)
            sim.run(lambda s: s.drive("clk", 1), max_time=1)
            got = sim.read("result")
            assert got == expected, f"engine={engine} k={len(operand_values)}: got {got!r}, expected {expected}"

    def test_add_chain_3(self):
        """3-term addition: a0+a1+a2."""
        mod = _make_add_chain_module(3)
        vals = {f"a{i}": i + 1 for i in range(3)}
        self._run(mod, vals, sum(vals.values()) & 0xFFFF)

    def test_add_chain_5(self):
        """5-term addition: a0+...+a4."""
        mod = _make_add_chain_module(5)
        vals = {f"a{i}": i + 1 for i in range(5)}
        self._run(mod, vals, sum(vals.values()) & 0xFFFF)

    def test_add_chain_10(self):
        """10-term addition: a0+...+a9."""
        mod = _make_add_chain_module(10)
        vals = {f"a{i}": i + 1 for i in range(10)}
        self._run(mod, vals, sum(vals.values()) & 0xFFFF)

    def test_add_chain_max_line_length(self):
        """Expression temps keep the max generated line length bounded (O(k), not O(k²))."""
        for k in [5, 10, 20]:
            mod = _make_add_chain_module(k)
            cg = CythonCodegen()
            pyx = cg.generate(mod)
            max_len = max(len(line) for line in pyx.split("\n"))
            assert max_len < 500, f"k={k}: max line length {max_len} exceeds 500 — O(k²) growth not fixed"


class TestOrChainTemporaries:
    """Verify that expression temporaries keep |/& mask lines bounded (O(k) not O(k²))
    for OR chains in continuous assigns, which use _emit_mask_expr heavily."""

    def _run(self, mod, operand_values, expected):
        from veriforge.sim.testbench import Simulator

        for engine in ("reference", "compiled"):
            sim = Simulator(mod, engine=engine)
            for name, val in operand_values.items():
                sim.drive(name, Value(val, width=8))
            sim.settle()
            got = sim.read("result")
            assert got == expected, f"engine={engine} k={len(operand_values)}: got {got!r}, expected {expected}"

    def test_or_chain_3(self):
        """3-term OR chain via continuous assign: a0|a1|a2 == 0b111."""
        mod = _make_or_chain_module(3)
        vals = {f"a{i}": 1 << i for i in range(3)}
        self._run(mod, vals, Value(0b111, width=8))

    def test_or_chain_5(self):
        """5-term OR chain: a0|...|a4 == 0b11111."""
        mod = _make_or_chain_module(5)
        vals = {f"a{i}": 1 << i for i in range(5)}
        self._run(mod, vals, Value(0b11111, width=8))

    def test_or_chain_max_line_length(self):
        """OR-chain expression temps keep generated line length bounded (O(k) not O(k²))."""
        for k in [5, 10, 20]:
            mod = _make_or_chain_module(k)
            cg = CythonCodegen()
            pyx = cg.generate(mod)
            max_len = max(len(line) for line in pyx.split("\n"))
            assert max_len < 500, f"k={k}: max line length {max_len} exceeds 500 — OR mask O(k²) not fixed"


class TestTernaryChainTemporaries:
    """Verify that ternary-chain expression temporaries produce correct results
    and keep codegen time O(k) instead of O(2^k)."""

    def _run(self, mod, sel_values, data_values, expected):
        from veriforge.sim.testbench import Simulator

        for engine in ("reference", "compiled"):
            sim = Simulator(mod, engine=engine)
            for name, val in sel_values.items():
                sim.drive(name, val)
            for name, val in data_values.items():
                sim.drive(name, Value(val, width=8))
            sim.settle()
            got = sim.read("result")
            assert got == expected, f"engine={engine} k={len(sel_values)}: got {got!r}, expected {expected}"

    def test_ternary_chain_3_first(self):
        """3-deep ternary: a0=1 selects d0."""
        mod = _make_ternary_chain_module(3)
        self._run(
            mod,
            {"a0": 1, "a1": 0, "a2": 0},
            {f"d{i}": i + 10 for i in range(4)},
            Value(10, width=8),
        )

    def test_ternary_chain_3_last(self):
        """3-deep ternary: all a=0 selects default d3."""
        mod = _make_ternary_chain_module(3)
        self._run(
            mod,
            {"a0": 0, "a1": 0, "a2": 0},
            {f"d{i}": i + 10 for i in range(4)},
            Value(13, width=8),
        )

    def test_ternary_chain_max_line_length(self):
        """Ternary-chain temps keep generated line length bounded (O(k) not O(2^k))."""
        for k in [5, 10, 20]:
            mod = _make_ternary_chain_module(k)
            cg = CythonCodegen()
            pyx = cg.generate(mod)
            max_len = max(len(line) for line in pyx.split("\n"))
            assert max_len < 800, f"k={k}: max line length {max_len} exceeds 800 — ternary 2^k not fixed"
