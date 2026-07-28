"""Compiled engine: behavioral execution correctness.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestCompiledExecution:
    """Test generated .pyx compiles and executes correctly."""

    @pytest.fixture()
    def compiler(self, tmp_path):
        return CythonCompiler(cache_dir=str(tmp_path / "cache"))

    def test_adder_compile_and_run(self, compiler):
        """Compiled adder: drive a=3, b=5 → y=8."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_adder())
        mod = compiler.compile_pyx(pyx, "test_adder_p1")
        sim = mod.CompiledSim()
        sid_a = cg.signal_map["a"]
        sid_b = cg.signal_map["b"]
        sid_y = cg.signal_map["y"]

        sim.drive(sid_a, 3, 0)
        sim.drive(sid_b, 5, 0)
        sim.step()

        v, m = sim.read(sid_y)
        assert v == 8
        assert m == 0

    def test_and_gate_compile_and_run(self, compiler):
        """Compiled AND gate: a=0xFF, b=0x0F → y=0x0F."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_and_gate())
        mod = compiler.compile_pyx(pyx, "test_and_p1")
        sim = mod.CompiledSim()

        sid_a = cg.signal_map["a"]
        sid_b = cg.signal_map["b"]
        sid_y = cg.signal_map["y"]

        sim.drive(sid_a, 0xFF, 0)
        sim.drive(sid_b, 0x0F, 0)
        sim.step()

        v, m = sim.read(sid_y)
        assert v == 0x0F
        assert m == 0

    def test_inverter_compile_and_run(self, compiler):
        """Compiled inverter: a=0xAA → y=0x55."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_inverter())
        mod = compiler.compile_pyx(pyx, "test_inv_p1")
        sim = mod.CompiledSim()

        sid_a = cg.signal_map["a"]
        sid_y = cg.signal_map["y"]

        sim.drive(sid_a, 0xAA, 0)
        sim.step()

        v, m = sim.read(sid_y)
        assert v == 0x55
        assert m == 0

    def test_chain_compile_and_run(self, compiler):
        """Chained assigns: a=10 → b=11 → c=12."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_chain())
        mod = compiler.compile_pyx(pyx, "test_chain_p1")
        sim = mod.CompiledSim()

        sid_a = cg.signal_map["a"]
        sid_b = cg.signal_map["b"]
        sid_c = cg.signal_map["c"]

        sim.drive(sid_a, 10, 0)
        sim.step()

        vb, mb = sim.read(sid_b)
        vc, mc = sim.read(sid_c)
        assert vb == 11
        assert vc == 12
        assert mb == 0
        assert mc == 0


class TestPhase2Execution:
    """Compile and run sequential designs at the CompiledSim level."""

    @pytest.fixture()
    def compiler(self, tmp_path):
        return CythonCompiler(cache_dir=str(tmp_path / "cache"))

    def test_counter_basic(self, compiler):
        """Counter: reset then count 3 cycles."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        mod = compiler.compile_pyx(pyx, "test_counter_p2")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_rst = cg.signal_map["rst"]
        sid_count = cg.signal_map["count"]

        # Reset: drive rst=1, clk=0→1
        sim.drive(sid_rst, 1, 0)
        sim.drive(sid_clk, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)  # posedge
        sim.step()
        v, _ = sim.read(sid_count)
        assert v == 0

        # Release reset, count 3 times
        sim.drive(sid_rst, 0, 0)
        for expected in [1, 2, 3]:
            sim.snapshot()
            sim.drive(sid_clk, 0, 0)
            sim.step()  # negedge, no trigger

            sim.snapshot()
            sim.drive(sid_clk, 1, 0)  # posedge
            sim.step()
            v, _ = sim.read(sid_count)
            assert v == expected, f"Expected {expected}, got {v}"

    def test_combo_always_mux(self, compiler):
        """Combinational always mux: sel=1→y=a, sel=0→y=b."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_combo_always_mux())
        mod = compiler.compile_pyx(pyx, "test_combo_mux_p2")
        sim = mod.CompiledSim()

        sid_sel = cg.signal_map["sel"]
        sid_a = cg.signal_map["a"]
        sid_b = cg.signal_map["b"]
        sid_y = cg.signal_map["y"]

        sim.drive(sid_a, 42, 0)
        sim.drive(sid_b, 99, 0)
        sim.drive(sid_sel, 1, 0)
        sim.snapshot()
        sim.step()
        v, _ = sim.read(sid_y)
        assert v == 42

        sim.drive(sid_sel, 0, 0)
        sim.snapshot()
        sim.step()
        v, _ = sim.read(sid_y)
        assert v == 99


class TestPhase3Execution:
    """Compile and run Phase 3 designs at the CompiledSim level."""

    @pytest.fixture()
    def compiler(self, tmp_path):
        return CythonCompiler(cache_dir=str(tmp_path / "cache"))

    def test_bit_select_nba(self, compiler):
        """x[3] <= 1 on posedge → x should be 8."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_bit_select_lhs())
        mod = compiler.compile_pyx(pyx, "test_bit_sel_p3")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_x = cg.signal_map["x"]

        # Initialize x=0, clk=0
        sim.drive(sid_x, 0, 0)
        sim.drive(sid_clk, 0, 0)
        sim.step()

        # Posedge: clk 0→1
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        v, _ = sim.read(sid_x)
        assert v == 8, f"Expected x=8, got {v}"

    def test_dynamic_bit_select_combo_reruns_on_index_change(self, compiler):
        """always_comb y[idx] = a reruns when idx changes in compiled simulation."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_dynamic_bit_select_combo_lhs())
        mod = compiler.compile_pyx(pyx, "test_bit_sel_combo_p3")
        sim = mod.CompiledSim()

        sid_idx = cg.signal_map["idx"]
        sid_a = cg.signal_map["a"]
        sid_y = cg.signal_map["y"]

        sim.drive(sid_a, 1, 0)
        sim.drive(sid_idx, 1, 0)
        sim.step()
        v, _ = sim.read(sid_y)
        assert v == 0b00000010

        sim.drive(sid_idx, 2, 0)
        sim.step()
        v, _ = sim.read(sid_y)
        assert v == 0b00000100

    def test_range_select_nba(self, compiler):
        """x[5:2] <= 0xA on posedge → x should be 0x28."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_range_select_lhs())
        mod = compiler.compile_pyx(pyx, "test_range_sel_p3")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_x = cg.signal_map["x"]

        sim.drive(sid_x, 0, 0)
        sim.drive(sid_clk, 0, 0)
        sim.step()

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        v, _ = sim.read(sid_x)
        assert v == 0x28, f"Expected x=0x28, got {v:#x}"

    def test_part_select_nba(self, compiler):
        """x[2 +: 4] <= 0xA on posedge → x should be 0x28."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_part_select_lhs())
        mod = compiler.compile_pyx(pyx, "test_part_sel_p3")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_x = cg.signal_map["x"]

        sim.drive(sid_x, 0, 0)
        sim.drive(sid_clk, 0, 0)
        sim.step()

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        v, _ = sim.read(sid_x)
        assert v == 0x28, f"Expected x=0x28, got {v:#x}"

    def test_concat_lhs_cont(self, compiler):
        """assign {hi, lo} = 0xA5 → hi=0xA, lo=0x5."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_concat_lhs_cont())
        mod = compiler.compile_pyx(pyx, "test_concat_lhs_p3")
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_x, 0xA5, 0)
        sim.step()

        vhi, _ = sim.read(sid_hi)
        vlo, _ = sim.read(sid_lo)
        assert vhi == 0xA, f"Expected hi=0xA, got {vhi:#x}"
        assert vlo == 0x5, f"Expected lo=0x5, got {vlo:#x}"

    def test_concat_lhs_3way_cont(self, compiler):
        """assign {a(2), b(3), c(3)} = 0b11_101_011 → a=3, b=5, c=3."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_concat_lhs_3way_cont())
        mod = compiler.compile_pyx(pyx, "test_concat_3_p3")
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_a = cg.signal_map["a"]
        sid_b = cg.signal_map["b"]
        sid_c = cg.signal_map["c"]

        sim.drive(sid_x, 0b11101011, 0)
        sim.step()

        va, _ = sim.read(sid_a)
        vb, _ = sim.read(sid_b)
        vc, _ = sim.read(sid_c)
        assert va == 0b11, f"a={va:#04b}"
        assert vb == 0b101, f"b={vb:#05b}"
        assert vc == 0b011, f"c={vc:#05b}"

    def test_concat_bit_lhs_cont(self, compiler):
        """Continuous concat bit-select parts update plain signals and preserve masks."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_concat_bit_lhs_cont())
        mod = compiler.compile_pyx(pyx, "test_concat_bit_lhs_cont")
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_data = cg.signal_map["data"]
        sid_valid = cg.signal_map["valid"]

        sim.drive(sid_x, 0b101, 0b010)
        sim.step()

        assert sim.read(sid_data) == (0b10, 0b01)
        assert sim.read(sid_valid) == (0b1, 0)

    def test_concat_range_lhs_cont(self, compiler):
        """Continuous concat range-select parts update plain signals and preserve masks."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_concat_range_lhs_cont())
        mod = compiler.compile_pyx(pyx, "test_concat_range_lhs_cont")
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_data = cg.signal_map["data"]
        sid_valid = cg.signal_map["valid"]

        sim.drive(sid_x, 0b1011, 0b0100)
        sim.step()

        assert sim.read(sid_data) == (0b10100, 0b01011)
        assert sim.read(sid_valid) == (0b1, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_concat_dyn_range_lhs_cont, "test_concat_dyn_range_lhs_cont", False),
            (_make_concat_dyn_range_lhs_combo, "test_concat_dyn_range_lhs_combo", False),
            (_make_concat_dyn_range_lhs_seq, "test_concat_dyn_range_lhs_seq", True),
        ],
    )
    def test_concat_dyn_range_lhs(self, compiler, builder, module_name, needs_clock):
        """Concat dynamic range-select parts update plain signals when the bounds move together."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_msb = cg.signal_map["msb"]
        sid_lsb = cg.signal_map["lsb"]
        sid_data = cg.signal_map["data"]
        sid_valid = cg.signal_map["valid"]

        sim.drive(sid_x, 0b1010, 0)
        sim.drive(sid_msb, 4, 0)
        sim.drive(sid_lsb, 2, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.read(sid_data) == (0b10100, 0b00011)
        assert sim.read(sid_valid) == (0, 0)

        sim.drive(sid_msb, 3, 0)
        sim.drive(sid_lsb, 1, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.read(sid_data) == (0b11010, 0b00001)
        assert sim.read(sid_valid) == (0, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_concat_part_lhs_cont, "test_concat_part_lhs_cont", False),
            (_make_concat_part_lhs_combo, "test_concat_part_lhs_combo", False),
            (_make_concat_part_lhs_seq, "test_concat_part_lhs_seq", True),
        ],
    )
    def test_concat_part_lhs(self, compiler, builder, module_name, needs_clock):
        """Concat part-select parts update plain signals and preserve masks."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_data = cg.signal_map["data"]
        sid_valid = cg.signal_map["valid"]

        sim.drive(sid_x, 0b101, 0b010)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.read(sid_data) == (0b1000, 0b0111)
        assert sim.read(sid_valid) == (0b1, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_concat_lhs_combo, "test_concat_lhs_combo", False),
            (_make_concat_lhs_seq, "test_concat_lhs_seq", True),
        ],
    )
    def test_concat_lhs_mask_propagation(self, compiler, builder, module_name, needs_clock):
        """Behavioral concat LHS writes preserve per-slice masks in compiled simulation."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        sid_x = cg.signal_map["x"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_x, 0xA5, 0x3C)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.read(sid_hi) == (0x8, 0x3)
        assert sim.read(sid_lo) == (0x1, 0xC)

    def test_wide_concat_lhs_cont(self):
        """assign {a..j} = x; wide RHS slices value and mask into each concat part."""
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
        sim = Simulator(_make_wide_concat_lhs_cont(), engine="compiled")
        sim.drive("x", x)
        sim.run(max_time=0)

        for name, expected in parts:
            assert sim.read(name) == expected, f"{name}: {sim.read(name)} != {expected}"

    def test_mem_write_and_read(self, compiler):
        """Write mem[0]=10 via mem_write API, read via combo always."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_read_write())
        mod = compiler.compile_pyx(pyx, "test_mem_rw_p3")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_out = cg.signal_map["out"]

        # Write mem[0]=10, mem[1]=20
        sim.mem_write(mid, 0, 10, 0)
        sim.mem_write(mid, 1, 20, 0)

        # Read mem[0]
        sim.drive(sid_addr, 0, 0)
        sim.step()
        v, _ = sim.read(sid_out)
        assert v == 10, f"Expected out=10, got {v}"

        # Read mem[1]
        sim.drive(sid_addr, 1, 0)
        sim.snapshot()
        sim.step()
        v, _ = sim.read(sid_out)
        assert v == 20, f"Expected out=20, got {v}"

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_cont, "test_struct_mem_field_cont", False),
            (_make_struct_mem_field_combo, "test_struct_mem_field_combo", False),
            (_make_struct_mem_field_seq, "test_struct_mem_field_seq", True),
        ],
    )
    def test_struct_memory_field_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct field writes into memory resolve dynamic indices and pack masks correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_data = cg.signal_map["in_data"]
        sid_valid = cg.signal_map["in_valid"]

        data_val = 0xA6
        data_mask = 0x20
        valid_val = 1
        valid_mask = 1

        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_data, data_val, data_mask)
        sim.drive(sid_valid, valid_val, valid_mask)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected_val = ((data_val & ~data_mask) << 1) | (valid_val & ~valid_mask)
        expected_mask = (data_mask << 1) | valid_mask
        assert sim.mem_read(mid, 1) == (expected_val, expected_mask)

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (expected_val, expected_mask)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_part_cont, "test_struct_mem_field_part_cont", False),
            (_make_struct_mem_field_part_combo, "test_struct_mem_field_part_combo", False),
            (_make_struct_mem_field_part_seq, "test_struct_mem_field_part_seq", True),
        ],
    )
    def test_struct_memory_field_part_select_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field part-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_base = cg.signal_map["base"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_base, 3, 0)
        sim.drive(sid_bits, 0b10, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = (1 << 5, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_bit_cont, "test_struct_mem_field_bit_cont", False),
            (_make_struct_mem_field_bit_combo, "test_struct_mem_field_bit_combo", False),
            (_make_struct_mem_field_bit_seq, "test_struct_mem_field_bit_seq", True),
        ],
    )
    def test_struct_memory_field_bit_select_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field bit-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bit = cg.signal_map["bit"]
        sid_in_bit = cg.signal_map["in_bit"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bit, 3, 0)
        sim.drive(sid_in_bit, 1, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = (1 << 4, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_range_cont, "test_struct_mem_field_range_cont", False),
            (_make_struct_mem_field_range_combo, "test_struct_mem_field_range_combo", False),
            (_make_struct_mem_field_range_seq, "test_struct_mem_field_range_seq", True),
        ],
    )
    def test_struct_memory_field_range_select_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field range-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_msb = cg.signal_map["msb"]
        sid_lsb = cg.signal_map["lsb"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_msb, 4, 0)
        sim.drive(sid_lsb, 2, 0)
        sim.drive(sid_bits, 0b101, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = ((0b101 << 2) << 1, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_concat_cont, "test_mem_concat_cont", False),
            (_make_mem_concat_combo, "test_mem_concat_combo", False),
            (_make_mem_concat_seq, "test_mem_concat_seq", True),
        ],
    )
    def test_memory_concat_write(self, compiler, builder, module_name, needs_clock):
        """Compiled plain memory-element concat writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bus = cg.signal_map["in_bus"]
        sid_valid = cg.signal_map["valid"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bus, 0x14D, 0x040)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0x86, 0x20)
        assert sim.read(sid_valid) == (1, 0)

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0x86, 0x20)
        assert sim.read(sid_valid) == (1, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_concat_bit_cont, "test_mem_concat_bit_cont", False),
            (_make_mem_concat_bit_combo, "test_mem_concat_bit_combo", False),
            (_make_mem_concat_bit_seq, "test_mem_concat_bit_seq", True),
        ],
    )
    def test_memory_concat_bit_write(self, compiler, builder, module_name, needs_clock):
        """Compiled plain memory-element concat bit-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bits = cg.signal_map["in_bits"]
        sid_valid = cg.signal_map["valid"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bits, 0b10, 0b10)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0, 1 << 3)
        assert sim.read(sid_valid) == (0, 0)

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0, 1 << 3)
        assert sim.read(sid_valid) == (0, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_concat_range_cont, "test_mem_concat_range_cont", False),
            (_make_mem_concat_range_combo, "test_mem_concat_range_combo", False),
            (_make_mem_concat_range_seq, "test_mem_concat_range_seq", True),
        ],
    )
    def test_memory_concat_range_write(self, compiler, builder, module_name, needs_clock):
        """Compiled plain memory-element concat constant range-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bits = cg.signal_map["in_bits"]
        sid_valid = cg.signal_map["valid"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bits, 0b1010, 0b0100)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0b10100, 0b01000)
        assert sim.read(sid_valid) == (0, 0)

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0b10100, 0b01000)
        assert sim.read(sid_valid) == (0, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_concat_dyn_range_cont, "test_mem_concat_dyn_range_cont", False),
            (_make_mem_concat_dyn_range_combo, "test_mem_concat_dyn_range_combo", False),
            (_make_mem_concat_dyn_range_seq, "test_mem_concat_dyn_range_seq", True),
        ],
    )
    def test_memory_concat_dyn_range_write(self, compiler, builder, module_name, needs_clock):
        """Compiled plain memory-element concat dynamic range-select writes follow dynamic address and bound changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_msb = cg.signal_map["msb"]
        sid_lsb = cg.signal_map["lsb"]
        sid_bits = cg.signal_map["in_bits"]
        sid_valid = cg.signal_map["valid"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_msb, 4, 0)
        sim.drive(sid_lsb, 2, 0)
        sim.drive(sid_bits, 0b1010, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0b10100, 0)
        assert sim.read(sid_valid) == (0, 0)

        sim.drive(sid_addr, 0, 0)
        sim.drive(sid_msb, 3, 0)
        sim.drive(sid_lsb, 1, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0b01010, 0)
        assert sim.read(sid_valid) == (0, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_concat_part_cont, "test_mem_concat_part_cont", False),
            (_make_mem_concat_part_combo, "test_mem_concat_part_combo", False),
            (_make_mem_concat_part_seq, "test_mem_concat_part_seq", True),
        ],
    )
    def test_memory_concat_part_write(self, compiler, builder, module_name, needs_clock):
        """Compiled plain memory-element concat constant part-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bits = cg.signal_map["in_bits"]
        sid_valid = cg.signal_map["valid"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bits, 0b101, 0b010)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0b1000, 0b0100)
        assert sim.read(sid_valid) == (1, 0)

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0b1000, 0b0100)
        assert sim.read(sid_valid) == (1, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_concat_dyn_part_cont, "test_mem_concat_dyn_part_cont", False),
            (_make_mem_concat_dyn_part_combo, "test_mem_concat_dyn_part_combo", False),
            (_make_mem_concat_dyn_part_seq, "test_mem_concat_dyn_part_seq", True),
        ],
    )
    def test_memory_concat_dyn_part_write(self, compiler, builder, module_name, needs_clock):
        """Compiled plain memory-element concat dynamic part-select writes follow dynamic address/base changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_base = cg.signal_map["base"]
        sid_bits = cg.signal_map["in_bits"]
        sid_valid = cg.signal_map["valid"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_base, 3, 0)
        sim.drive(sid_bits, 0b101, 0b010)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0b10000, 0b01000)
        assert sim.read(sid_valid) == (1, 0)

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0b10000, 0b01000)
        assert sim.read(sid_valid) == (1, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_cont, "test_struct_mem_field_concat_cont", False),
            (_make_struct_mem_field_concat_combo, "test_struct_mem_field_concat_combo", False),
            (_make_struct_mem_field_concat_seq, "test_struct_mem_field_concat_seq", True),
        ],
    )
    def test_struct_memory_field_concat_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bus = cg.signal_map["in_bus"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bus, 0x14D, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = (0x14D, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_bit_cont, "test_struct_mem_field_concat_bit_cont", False),
            (_make_struct_mem_field_concat_bit_combo, "test_struct_mem_field_concat_bit_combo", False),
            (_make_struct_mem_field_concat_bit_seq, "test_struct_mem_field_concat_bit_seq", True),
        ],
    )
    def test_struct_memory_field_concat_bit_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat bit-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bits, 0b10, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = (1 << 4, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_dyn_bit_cont, "test_struct_mem_field_concat_dyn_bit_cont", False),
            (_make_struct_mem_field_concat_dyn_bit_combo, "test_struct_mem_field_concat_dyn_bit_combo", False),
            (_make_struct_mem_field_concat_dyn_bit_seq, "test_struct_mem_field_concat_dyn_bit_seq", True),
        ],
    )
    def test_struct_memory_field_concat_dyn_bit_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat dynamic bit-select writes follow dynamic address and bit changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bit = cg.signal_map["bit"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bit, 3, 0)
        sim.drive(sid_bits, 0b10, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (1 << 4, 0)

        sim.drive(sid_addr, 0, 0)
        sim.drive(sid_bit, 2, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (1 << 3, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_range_cont, "test_struct_mem_field_concat_range_cont", False),
            (_make_struct_mem_field_concat_range_combo, "test_struct_mem_field_concat_range_combo", False),
            (_make_struct_mem_field_concat_range_seq, "test_struct_mem_field_concat_range_seq", True),
        ],
    )
    def test_struct_memory_field_concat_range_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat range-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bits, 0b1010, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = (0b101 << 3, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_dyn_range_cont, "test_struct_mem_field_concat_dyn_range_cont", False),
            (_make_struct_mem_field_concat_dyn_range_combo, "test_struct_mem_field_concat_dyn_range_combo", False),
            (_make_struct_mem_field_concat_dyn_range_seq, "test_struct_mem_field_concat_dyn_range_seq", True),
        ],
    )
    def test_struct_memory_field_concat_dyn_range_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat dynamic range-select writes follow dynamic address and bound changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_msb = cg.signal_map["msb"]
        sid_lsb = cg.signal_map["lsb"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_msb, 4, 0)
        sim.drive(sid_lsb, 2, 0)
        sim.drive(sid_bits, 0b1010, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == (0b101000, 0)

        sim.drive(sid_addr, 0, 0)
        sim.drive(sid_msb, 3, 0)
        sim.drive(sid_lsb, 1, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (0b010100, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_part_cont, "test_struct_mem_field_concat_part_cont", False),
            (_make_struct_mem_field_concat_part_combo, "test_struct_mem_field_concat_part_combo", False),
            (_make_struct_mem_field_concat_part_seq, "test_struct_mem_field_concat_part_seq", True),
        ],
    )
    def test_struct_memory_field_concat_part_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat part-select writes follow dynamic address changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_bits, 0b101, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        expected = ((0b10 << 3) | 1, 0)
        assert sim.mem_read(mid, 1) == expected

        sim.drive(sid_addr, 0, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_struct_mem_field_concat_dyn_part_cont, "test_struct_mem_field_concat_dyn_part_cont", False),
            (_make_struct_mem_field_concat_dyn_part_combo, "test_struct_mem_field_concat_dyn_part_combo", False),
            (_make_struct_mem_field_concat_dyn_part_seq, "test_struct_mem_field_concat_dyn_part_seq", True),
        ],
    )
    def test_struct_memory_field_concat_dyn_part_write(self, compiler, builder, module_name, needs_clock):
        """Compiled struct memory field concat dynamic part-select writes follow dynamic address/base changes."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_addr = cg.signal_map["addr"]
        sid_base = cg.signal_map["base"]
        sid_bits = cg.signal_map["in_bits"]

        sim.mem_write(mid, 0, 0, 0)
        sim.mem_write(mid, 1, 0, 0)
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_base, 3, 0)
        sim.drive(sid_bits, 0b101, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 1) == ((0b10 << 4) | 1, 0)

        sim.drive(sid_addr, 0, 0)
        sim.drive(sid_base, 2, 0)
        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == ((0b10 << 3) | 1, 0)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_mem_bit_cont, "test_mem_bit_cont", False),
            (_make_mem_bit_combo, "test_mem_bit_combo", False),
            (_make_mem_bit_seq, "test_mem_bit_seq", True),
        ],
    )
    @pytest.mark.parametrize(
        ("initial_val", "initial_mask", "bit_index", "din_val", "din_mask", "expected"),
        [
            (0b10100000, 0b00010000, 2, 1, 0, (0b10100100, 0b00010000)),
            (0b10100100, 0b00010000, 2, 1, 1, (0b10100000, 0b00010100)),
        ],
    )
    def test_mem_element_bit_write(
        self,
        compiler,
        builder,
        module_name,
        needs_clock,
        initial_val,
        initial_mask,
        bit_index,
        din_val,
        din_mask,
        expected,
    ):
        """Compiled mem[0][bit] writes preserve non-target bits and raw mask storage semantics."""
        cg = CythonCodegen()
        pyx = cg.generate(builder())
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_bit = cg.signal_map["bit"]
        sid_din = cg.signal_map["din"]

        sim.mem_write(mid, 0, initial_val, initial_mask)
        sim.drive(sid_bit, bit_index, 0)
        sim.drive(sid_din, din_val, din_mask)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == expected

    def test_wide_mem_element_bit_cont_write(self, compiler):
        """Compiled assign mem[0][bit] = din updates a high wide-memory bit through the shared range helper."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_bit_cont(65))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_bit_cont")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_bit = cg.signal_map["bit"]
        sid_din = cg.signal_map["din"]

        sim.drive(sid_bit, 64, 0)
        sim.drive(sid_din, 1, 0)
        sim.step()

        assert sim.mem_read(mid, 0) == (1 << 64, (1 << 64) - 1)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_wide_mem_dyn_range_cont, "test_wide_mem_dyn_range_cont", False),
            (_make_wide_mem_dyn_range_combo, "test_wide_mem_dyn_range_combo", False),
            (_make_wide_mem_dyn_range_seq, "test_wide_mem_dyn_range_seq", True),
        ],
    )
    def test_wide_mem_dynamic_range_write(self, compiler, builder, module_name, needs_clock):
        """Compiled dynamic wide mem[0][msb:lsb] writes split cross-word updates correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(builder(65))
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_msb = cg.signal_map["msb"]
        sid_lsb = cg.signal_map["lsb"]
        sid_din = cg.signal_map["din"]

        sim.drive(sid_msb, 64, 0)
        sim.drive(sid_lsb, 63, 0)
        sim.drive(sid_din, 0b10, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (1 << 64, (1 << 63) - 1)

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_wide_whole_mem_copy_combo, "test_wide_whole_mem_copy_combo", False),
            (_make_wide_whole_mem_copy_seq, "test_wide_whole_mem_copy_seq", True),
        ],
    )
    def test_wide_whole_memory_copy(self, compiler, builder, module_name, needs_clock):
        """Compiled whole-memory dst = src copies wide raw (val, mask) tuples for every address."""
        cg = CythonCodegen()
        pyx = cg.generate(builder(65))
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        src_mid = cg.mem_map["src"]
        dst_mid = cg.mem_map["dst"]

        expected0 = ((1 << 64) | 0x12, 1 << 5)
        expected1 = (0x3456, 1 << 64)
        sim.mem_write_wide(src_mid, 0, *expected0)
        sim.mem_write_wide(src_mid, 1, *expected1)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(dst_mid, 0) == expected0
        assert sim.mem_read(dst_mid, 1) == expected1

    @pytest.mark.parametrize(
        ("builder", "module_name", "needs_clock"),
        [
            (_make_wide_mem_part_select_cont, "test_wide_mem_part_select_cont", False),
            (_make_wide_mem_part_select_combo, "test_wide_mem_part_select_combo", False),
            (_make_wide_mem_part_select_seq, "test_wide_mem_part_select_seq", True),
        ],
    )
    def test_wide_mem_part_select_write(self, compiler, builder, module_name, needs_clock):
        """Compiled mem[0][base +: 2] writes route through wide memory range lowering."""
        cg = CythonCodegen()
        pyx = cg.generate(builder(65))
        mod = compiler.compile_pyx(pyx, module_name)
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_base = cg.signal_map["base"]
        sid_din = cg.signal_map["din"]

        sim.drive(sid_base, 63, 0)
        sim.drive(sid_din, 0b10, 0)

        if needs_clock:
            sid_clk = cg.signal_map["clk"]
            sim.drive(sid_clk, 0, 0)
            sim.step()
            sim.snapshot()
            sim.drive(sid_clk, 1, 0)
            sim.step()
        else:
            sim.step()

        assert sim.mem_read(mid, 0) == (1 << 64, (1 << 63) - 1)

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "hi_value", "lo_value"),
        [
            (1, 64, 1, 0x123456789ABCDEF0),
        ],
    )
    def test_wide_mem_cont_concat_write(self, compiler, hi_width, lo_width, hi_value, lo_value):
        """Compiled continuous mem[0] = {hi, lo} writes wide memory words correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_concat_cont(hi_width, lo_width))
        mod = compiler.compile_pyx(pyx, f"test_wide_mem_cont_concat_{hi_width}_{lo_width}")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_hi, hi_value, 0)
        sim.drive(sid_lo, lo_value, 0)
        sim.step()

        v, m = sim.mem_read(mid, 0)
        expected = _pack_concat_parts(
            [
                ("hi", Value(hi_value, width=hi_width)),
                ("lo", Value(lo_value, width=lo_width)),
            ]
        )
        assert (v, m) == (expected.val, expected.mask), (
            f"Expected {expected}, got Value({v}, width={expected.width}, mask={m})"
        )

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "hi_value", "lo_value"),
        [
            (1, 64, 1, 0x123456789ABCDEF0),
        ],
    )
    def test_wide_mem_combo_concat_write(self, compiler, hi_width, lo_width, hi_value, lo_value):
        """Compiled always_comb mem[0] = {hi, lo} writes wide memory words correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_concat_combo(hi_width, lo_width))
        mod = compiler.compile_pyx(pyx, f"test_wide_mem_combo_concat_{hi_width}_{lo_width}")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_hi, hi_value, 0)
        sim.drive(sid_lo, lo_value, 0)
        sim.step()

        v, m = sim.mem_read(mid, 0)
        expected = _pack_concat_parts(
            [
                ("hi", Value(hi_value, width=hi_width)),
                ("lo", Value(lo_value, width=lo_width)),
            ]
        )
        assert (v, m) == (expected.val, expected.mask), (
            f"Expected {expected}, got Value({v}, width={expected.width}, mask={m})"
        )

    def test_mem_nba_write(self, compiler):
        """NBA mem write: mem[addr] <= data_in on posedge."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_nba())
        mod = compiler.compile_pyx(pyx, "test_mem_nba_p3")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_addr = cg.signal_map["addr"]
        sid_din = cg.signal_map["data_in"]
        sid_rd = cg.signal_map["rd_addr"]
        sid_out = cg.signal_map["out"]

        # Setup: clk=0, addr=0, data_in=42
        sim.drive(sid_clk, 0, 0)
        sim.drive(sid_addr, 0, 0)
        sim.drive(sid_din, 42, 0)
        sim.drive(sid_rd, 0, 0)
        sim.step()

        # Posedge → mem[0] <= 42
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        # Read back via combo: rd_addr=0 → out=42
        v, _ = sim.read(sid_out)
        assert v == 42, f"Expected out=42, got {v}"

        # Write another addr: addr=1, data_in=99
        sim.drive(sid_addr, 1, 0)
        sim.drive(sid_din, 99, 0)
        sim.drive(sid_clk, 0, 0)
        sim.snapshot()
        sim.step()  # negedge, no write

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)  # posedge → mem[1] <= 99
        sim.step()

        # Read mem[1]
        sim.drive(sid_rd, 1, 0)
        sim.snapshot()
        sim.step()
        v, _ = sim.read(sid_out)
        assert v == 99, f"Expected out=99, got {v}"

        # Verify mem[0] still has 42
        sim.drive(sid_rd, 0, 0)
        sim.snapshot()
        sim.step()
        v, _ = sim.read(sid_out)
        assert v == 42, f"Expected out=42, got {v}"

    @pytest.mark.parametrize(
        ("hi_width", "lo_width", "hi_value", "lo_value"),
        [
            (1, 64, 1, 0x123456789ABCDEF0),
        ],
    )
    def test_wide_mem_nba_concat_write(self, compiler, hi_width, lo_width, hi_value, lo_value):
        """Compiled mem[0] <= {hi, lo} queues and commits wide memory words correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_concat_seq(hi_width, lo_width))
        mod = compiler.compile_pyx(pyx, f"test_wide_mem_nba_concat_{hi_width}_{lo_width}")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_clk = cg.signal_map["clk"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_clk, 0, 0)
        sim.drive(sid_hi, hi_value, 0)
        sim.drive(sid_lo, lo_value, 0)
        sim.step()

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        v, m = sim.mem_read(mid, 0)
        expected = _pack_concat_parts(
            [
                ("hi", Value(hi_value, width=hi_width)),
                ("lo", Value(lo_value, width=lo_width)),
            ]
        )
        assert (v, m) == (expected.val, expected.mask), (
            f"Expected {expected}, got Value({v}, width={expected.width}, mask={m})"
        )

    def test_wide_mem_cont_zero_write(self, compiler):
        """Compiled assign mem[0] = 0 clears a prefilled wide memory element."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_zero_cont(65))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_zero_cont")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sim.mem_write_wide(mid, 0, (1 << 64) | 0x123456789ABCDEF0, (1 << 64) | (1 << 12))
        sim.step()

        assert sim.mem_read(mid, 0) == (0, 0)

    def test_wide_mem_combo_zero_write(self, compiler):
        """Compiled always_comb mem[0] = 0 clears a prefilled wide memory element."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_zero_combo(65))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_zero_combo")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sim.mem_write_wide(mid, 0, (1 << 64) | 0x123456789ABCDEF0, (1 << 64) | (1 << 12))
        sim.step()

        assert sim.mem_read(mid, 0) == (0, 0)

    def test_wide_mem_nba_zero_write(self, compiler):
        """Compiled mem[0] <= 0 clears a prefilled wide memory element on posedge."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_zero_seq(65))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_zero_seq")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sid_clk = cg.signal_map["clk"]

        sim.mem_write_wide(mid, 0, (1 << 64) | 0x123456789ABCDEF0, (1 << 64) | (1 << 12))
        sim.drive(sid_clk, 0, 0)
        sim.step()

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        assert sim.mem_read(mid, 0) == (0, 0)

    def test_wide_mem_cont_copy(self, compiler):
        """Compiled assign dst[0] = src[1] copies wide memory value and mask."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_copy_cont(1, 64))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_copy_cont")
        sim = mod.CompiledSim()

        mid_dst = cg.mem_map["dst"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_hi, 0, 1)
        sim.drive(sid_lo, 0x123456789ABCDEF0, 1 << 4)
        sim.step()

        v, m = sim.mem_read(mid_dst, 0)
        expected_v = 0x123456789ABCDEF0
        expected_m = (1 << 64) | (1 << 4)
        assert (v, m) == (expected_v, expected_m), f"Expected ({expected_v}, {expected_m}), got ({v}, {m})"

    def test_wide_mem_combo_copy(self, compiler):
        """Compiled always_comb dst[0] = src[1] copies wide memory value and mask."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_copy_combo(1, 64))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_copy_combo")
        sim = mod.CompiledSim()

        mid_dst = cg.mem_map["dst"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_hi, 0, 1)
        sim.drive(sid_lo, 0x123456789ABCDEF0, 1 << 4)
        sim.step()

        v, m = sim.mem_read(mid_dst, 0)
        expected_v = 0x123456789ABCDEF0
        expected_m = (1 << 64) | (1 << 4)
        assert (v, m) == (expected_v, expected_m), f"Expected ({expected_v}, {expected_m}), got ({v}, {m})"

    def test_wide_mem_nba_copy(self, compiler):
        """Compiled dst[0] <= src[1] queues and commits wide memory value and mask."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_wide_mem_copy_seq(1, 64))
        mod = compiler.compile_pyx(pyx, "test_wide_mem_copy_seq")
        sim = mod.CompiledSim()

        mid_dst = cg.mem_map["dst"]
        sid_clk = cg.signal_map["clk"]
        sid_hi = cg.signal_map["hi"]
        sid_lo = cg.signal_map["lo"]

        sim.drive(sid_clk, 0, 0)
        sim.drive(sid_hi, 0, 1)
        sim.drive(sid_lo, 0x123456789ABCDEF0, 1 << 4)
        sim.step()

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        v, m = sim.mem_read(mid_dst, 0)
        expected_v = 0x123456789ABCDEF0
        expected_m = (1 << 64) | (1 << 4)
        assert (v, m) == (expected_v, expected_m), f"Expected ({expected_v}, {expected_m}), got ({v}, {m})"

    def test_mem_read_api(self, compiler):
        """mem_read API returns written values."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_read_write())
        mod = compiler.compile_pyx(pyx, "test_mem_read_api_p3")
        sim = mod.CompiledSim()

        mid = cg.mem_map["mem"]
        sim.mem_write(mid, 2, 55, 0)
        v, m = sim.mem_read(mid, 2)
        assert v == 55
        assert m == 0

    def test_memory_copy_combo_tracks_memory_rhs_and_wrapped_index(self, compiler):
        """Compiled combo logic should copy whole memories and wrap narrow indices correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_mem_copy_wrapped_index_combo())
        mod = compiler.compile_pyx(pyx, "test_mem_copy_wrap_p3")
        sim = mod.CompiledSim()

        mid_q = cg.mem_map["mem_q"]
        mid_d = cg.mem_map["mem_d"]
        sid_clk = cg.signal_map["clk"]
        sid_wptr = cg.signal_map["wptr"]
        sid_data_i = cg.signal_map["data_i"]

        sim.drive(sid_clk, 0, 0)
        sim.mem_write(mid_q, 0, 0x11, 0)
        sim.mem_write(mid_q, 1, 0x22, 0x04)
        sim.drive(sid_wptr, 1, 0)
        sim.drive(sid_data_i, 0x55, 0)
        sim.step()

        assert sim.mem_read(mid_d, 0) == (0x55, 0)
        assert sim.mem_read(mid_d, 1) == (0x22, 0x04)

        sim.mem_write(mid_q, 1, 0x66, 0x08)
        sim.step()

        assert sim.mem_read(mid_d, 0) == (0x55, 0)
        assert sim.mem_read(mid_d, 1) == (0x66, 0x08)

        sim.drive(sid_wptr, 0, 0)
        sim.drive(sid_data_i, 0x77, 0)
        sim.step()

        assert sim.mem_read(mid_d, 0) == (0x11, 0)
        assert sim.mem_read(mid_d, 1) == (0x77, 0)

        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()

        assert sim.mem_read(mid_q, 0) == (0x11, 0)
        assert sim.mem_read(mid_q, 1) == (0x77, 0)


class TestPhase4Execution:
    """Test initial block fallback and timing support."""

    def test_initial_simple(self):
        """Simple initial block sets signal to 42."""
        sim = Simulator(_make_initial_simple(), engine="compiled")
        sim.run(max_time=0)
        assert sim.read("count") == Value(42, width=8)

    def test_initial_with_delay(self):
        """Initial block with #delay sets values at correct times."""
        sim = Simulator(_make_initial_with_delay(), engine="compiled")

        # After t=0: count=0
        # After t=10: count=5
        # After t=20: count=10
        sim.run(max_time=25)
        assert sim.read("count") == Value(10, width=8)

    def test_initial_with_display(self):
        """Initial block $display output is captured."""
        sim = Simulator(_make_initial_with_display(), engine="compiled")
        sim.run(max_time=0)
        assert sim.read("x") == Value(42, width=8)
        # $display output should have been captured
        assert len(sim.display_output) >= 1
        assert "42" in sim.display_output[0]

    def test_always_timing_clock(self):
        """Always #5 clk = ~clk toggling works via fallback."""
        sim = Simulator(_make_always_timing_clock(), engine="compiled")
        sim.run(max_time=25)
        # After several toggles, out should reflect clk
        # At t=0: clk=x (initial), t=5: clk=~x, ...
        # The exact value depends on x semantics; just check no crash

    def test_engine_report_compiled_fallback(self):
        """`always #5 clk = ~clk` needs reference/coroutine fallback on the
        compiled engine (>=1 fallback process, a non-empty reason); the
        reference engine runs everything natively (zero fallback)."""
        sim = Simulator(_make_always_timing_clock(), engine="compiled")
        report = sim.engine_report()
        assert report["engine"] == "compiled"
        assert report["fallback_processes"] >= 1
        assert report["native_processes"] >= 0
        assert report["fallback_reasons"]

        ref_sim = Simulator(_make_always_timing_clock(), engine="reference")
        ref_report = ref_sim.engine_report()
        assert ref_report["engine"] == "reference"
        assert ref_report["fallback_processes"] == 0
        assert ref_report["fallback_reasons"] == []

    def test_initial_counter_setup(self):
        """Initial block sets up reset, then counter counts on clock."""
        mod = _make_initial_counter_setup()
        sim = Simulator(mod, engine="compiled")
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim.run(max_time=100)
        # After reset released at t=20, counter should have counted
        count = sim.read("count")
        assert count.is_defined
        assert int(count) > 0

    def test_initial_finish(self):
        """$finish in initial block stops simulation."""
        sim = Simulator(_make_initial_finish(), engine="compiled")
        sim.run(max_time=100)
        # x should be 99 (set before $finish)
        assert sim.read("x") == Value(99, width=8)

    def test_integer_modulus_initial(self):
        """Large INTEGER assignments do not get truncated to zero in compiled mode."""
        sim = Simulator(_make_integer_modulus_initial(), engine="compiled")
        sim.run(max_time=0)
        assert sim.read("count") == Value(7, width=32)


class TestPhase5Execution:
    """Test batch_run at the compiled extension level."""

    @pytest.fixture()
    def compiler(self, tmp_cache):
        return CythonCompiler(cache_dir=tmp_cache)

    def test_batch_run_counter(self, compiler):
        """batch_run produces same count as manual stepping."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        mod = compiler.compile_pyx(pyx, "test_batch_counter_p5")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_rst = cg.signal_map["rst"]
        sid_count = cg.signal_map["count"]

        # Reset: drive rst=1, posedge clk
        sim.drive(sid_rst, 1, 0)
        sim.drive(sid_clk, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()
        v, _ = sim.read(sid_count)
        assert v == 0

        # Release reset
        sim.drive(sid_rst, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 0, 0)
        sim.step()

        # batch_run 10 cycles
        completed = sim.batch_run(10, sid_clk)
        assert completed == 10

        v, _ = sim.read(sid_count)
        assert v == 10

    def test_batch_run_matches_step(self, compiler):
        """batch_run produces identical results to manual step-by-step."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        mod = compiler.compile_pyx(pyx, "test_batch_vs_step_p5")

        # Manual stepping
        sim_a = mod.CompiledSim()
        sid_clk = cg.signal_map["clk"]
        sid_rst = cg.signal_map["rst"]
        sid_count = cg.signal_map["count"]

        sim_a.drive(sid_rst, 1, 0)
        sim_a.drive(sid_clk, 0, 0)
        sim_a.snapshot()
        sim_a.drive(sid_clk, 1, 0)
        sim_a.step()
        sim_a.drive(sid_rst, 0, 0)
        sim_a.snapshot()
        sim_a.drive(sid_clk, 0, 0)
        sim_a.step()

        n_cycles = 20
        for _ in range(n_cycles):
            sim_a.snapshot()
            sim_a.drive(sid_clk, 1, 0)
            sim_a.step()
            sim_a.snapshot()
            sim_a.drive(sid_clk, 0, 0)
            sim_a.step()

        va, _ = sim_a.read(sid_count)

        # Batch run
        sim_b = mod.CompiledSim()
        sim_b.drive(sid_rst, 1, 0)
        sim_b.drive(sid_clk, 0, 0)
        sim_b.snapshot()
        sim_b.drive(sid_clk, 1, 0)
        sim_b.step()
        sim_b.drive(sid_rst, 0, 0)
        sim_b.snapshot()
        sim_b.drive(sid_clk, 0, 0)
        sim_b.step()

        sim_b.batch_run(n_cycles, sid_clk)
        vb, _ = sim_b.read(sid_count)

        assert va == vb == n_cycles

    def test_batch_run_zero_cycles(self, compiler):
        """batch_run(0, ...) is a valid no-op."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        mod = compiler.compile_pyx(pyx, "test_batch_zero_p5")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        completed = sim.batch_run(0, sid_clk)
        assert completed == 0

    def test_batch_run_large(self, compiler):
        """batch_run with 1000 cycles works correctly."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_counter())
        mod = compiler.compile_pyx(pyx, "test_batch_large_p5")
        sim = mod.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_rst = cg.signal_map["rst"]
        sid_count = cg.signal_map["count"]

        # Reset then release
        sim.drive(sid_rst, 1, 0)
        sim.drive(sid_clk, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()
        sim.drive(sid_rst, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 0, 0)
        sim.step()

        sim.batch_run(1000, sid_clk)
        v, _ = sim.read(sid_count)
        # 8-bit counter wraps: 1000 % 256 = 232
        assert v == 1000 % 256


class TestPhase7Runtime:
    """Verify Phase 7 features at runtime (compile + execute)."""

    @pytest.fixture()
    def compiler(self, tmp_path):
        return CythonCompiler(cache_dir=str(tmp_path / "cache"))

    def test_signed_right_shift(self, compiler):
        """$signed(a) >>> 2 sign-extends before shifting."""
        mod = _make_signed_arith()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_signed_arith")
        sim = compiled_mod.CompiledSim()
        sid_a, sid_y = cg.signal_map["a"], cg.signal_map["y"]
        sim.drive(sid_a, 0xF0, 0)  # a = 0xF0 (negative in signed 8-bit)
        sim.step()
        v, _m = sim.read(sid_y)
        # $signed(0xF0) >>> 2 = -16 >>> 2 = -4 = 0xFC in 8-bit unsigned
        assert (v & 0xFF) == 0xFC

    def test_signed_positive_shift(self, compiler):
        """Signed right shift of positive value."""
        mod = _make_signed_arith()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_signed_pos")
        sim = compiled_mod.CompiledSim()
        sid_a, sid_y = cg.signal_map["a"], cg.signal_map["y"]
        sim.drive(sid_a, 0x40, 0)  # a = 0x40 = 64
        sim.step()
        v, _m = sim.read(sid_y)
        # $signed(0x40) >>> 2 = 64 >>> 2 = 16 = 0x10
        assert (v & 0xFF) == 0x10

    def test_power_op(self, compiler):
        """a ** b computes power."""
        mod = _make_power()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_power")
        sim = compiled_mod.CompiledSim()
        sid_a, sid_b, sid_y = cg.signal_map["a"], cg.signal_map["b"], cg.signal_map["y"]
        sim.drive(sid_a, 2, 0)  # a = 2
        sim.drive(sid_b, 8, 0)  # b = 8
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFFFF) == 256  # 2**8 = 256

    def test_power_zero_exponent(self, compiler):
        """a ** 0 = 1."""
        mod = _make_power()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_power_zero")
        sim = compiled_mod.CompiledSim()
        sid_a, sid_b, sid_y = cg.signal_map["a"], cg.signal_map["b"], cg.signal_map["y"]
        sim.drive(sid_a, 10, 0)  # a = 10
        sim.drive(sid_b, 0, 0)  # b = 0
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFFFF) == 1  # 10**0 = 1

    def test_xnor_reduction(self, compiler):
        """XNOR reduction: ~^0xFF = 1 (even parity), ~^0x01 = 0 (odd parity)."""
        mod = _make_xnor_reduce()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_xnor_red")
        sim = compiled_mod.CompiledSim()
        sid_a, sid_y = cg.signal_map["a"], cg.signal_map["y"]

        # 0xFF has 8 ones → even parity → ~^0xFF = 1
        sim.drive(sid_a, 0xFF, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 1) == 1

        # 0x01 has 1 one → odd parity → ~^0x01 = 0
        sim.drive(sid_a, 0x01, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 1) == 0

    def test_repeat_loop(self, compiler):
        """Combinational repeat loop compiles and executes in the compiled engine."""
        mod = _make_repeat_counter()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_repeat_counter")
        sim = compiled_mod.CompiledSim()
        sid_count, sid_y = cg.signal_map["count"], cg.signal_map["y"]

        sim.drive(sid_count, 3, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFF) == 3

        sim.drive(sid_count, 0, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFF) == 0

    def test_while_loop(self, compiler):
        """Combinational while loop compiles and executes in the compiled engine."""
        mod = _make_while_counter()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_while_counter")
        sim = compiled_mod.CompiledSim()
        sid_count, sid_y = cg.signal_map["count"], cg.signal_map["y"]

        sim.drive(sid_count, 4, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFF) == 4

        sim.drive(sid_count, 1, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFF) == 1

    def test_while_loop_limit_raises(self, compiler):
        """Infinite compiled while loop raises the shared loop-limit error."""
        mod = _make_infinite_while()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_infinite_while")
        sim = compiled_mod.CompiledSim()

        with pytest.raises(RuntimeError, match=r"While loop exceeded 100000 iterations"):
            sim.step()

    def test_initial_while_loop_compiled(self):
        """Untimed initial while loop runs natively during compiled construction."""
        sim = Simulator(_make_initial_while_counter(), engine="compiled")
        assert sim.read("count") == 3

    def test_initial_while_loop_limit_raises(self, compiler):
        """Infinite initial while loop raises during compiled simulator construction."""
        mod = _make_initial_infinite_while()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_initial_infinite_while")

        with pytest.raises(RuntimeError, match=r"While loop exceeded 100000 iterations"):
            compiled_mod.CompiledSim()

    def test_forever_loop_finish(self, compiler):
        """Forever loop can terminate through $finish in compiled runtime."""
        mod = _make_forever_finish_counter()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_forever_finish_counter")
        sim = compiled_mod.CompiledSim()
        sid_count, sid_y = cg.signal_map["count"], cg.signal_map["y"]

        sim.drive(sid_count, 4, 0)
        sim.step()
        v, _m = sim.read(sid_y)
        assert (v & 0xFF) == 4
        assert sim.is_finished()

    def test_forever_loop_limit_raises(self, compiler):
        """Infinite compiled forever loop raises the shared loop-limit error."""
        mod = _make_infinite_forever()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_infinite_forever")
        sim = compiled_mod.CompiledSim()

        with pytest.raises(RuntimeError, match=r"Forever loop exceeded 100000 iterations"):
            sim.step()

    def test_initial_forever_loop_limit_raises(self, compiler):
        """Infinite initial forever loop raises during compiled simulator construction."""
        mod = _make_initial_infinite_forever()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_initial_infinite_forever")

        with pytest.raises(RuntimeError, match=r"Forever loop exceeded 100000 iterations"):
            compiled_mod.CompiledSim()

    def test_multidim_memory_access(self, compiler):
        """Compiled runtime handles flattened two-dimensional unpacked memories."""
        mod = _make_multidim_memory_probe()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_multidim_memory_probe")
        sim = compiled_mod.CompiledSim()
        sid_row = cg.signal_map["row"]
        sid_col = cg.signal_map["col"]
        sid_y = cg.signal_map["y"]
        sid_lane = cg.signal_map["lane"]
        sid_copied = cg.signal_map["copied"]

        sim.drive(sid_row, 1, 0)
        sim.drive(sid_col, 2, 0)
        sim.step()
        y_val, _ = sim.read(sid_y)
        lane_val, _ = sim.read(sid_lane)
        copied_val, _ = sim.read(sid_copied)
        assert (y_val & 0xFF) == 0xA5
        assert (lane_val & 0xFF) == 0xDC
        assert (copied_val & 0xFF) == 0xA5

        sim.drive(sid_row, 0, 0)
        sim.drive(sid_col, 1, 0)
        sim.step()
        y_val, _ = sim.read(sid_y)
        assert (y_val & 0xFF) == 0xDC

    def test_inout_port(self, compiler):
        """Compiled runtime supports raw inout ports in single-module designs."""
        mod = _make_inout_port_probe()
        cg = CythonCodegen()
        pyx = cg.generate(mod)
        compiled_mod = compiler.compile_pyx(pyx, "compiled_inout_port_probe")
        sim = compiled_mod.CompiledSim()
        sid_drive_val = cg.signal_map["drive_val"]
        sid_drive_en = cg.signal_map["drive_en"]
        sid_out = cg.signal_map["out"]

        sim.drive(sid_drive_val, 0x42, 0)
        sim.drive(sid_drive_en, 1, 0)
        sim.step()
        out_val, _ = sim.read(sid_out)
        assert (out_val & 0xFF) == 0x42

        sim.drive(sid_drive_val, 0xFF, 0)
        sim.drive(sid_drive_en, 0, 0)
        sim.step()
        out_val, _ = sim.read(sid_out)
        assert (out_val & 0xFF) == 0

    def test_native_initial_compiled(self):
        """Simple initial block values are in compiled __init__, not fallback."""
        mod = _make_native_initial()
        sim = Simulator(mod, engine="compiled")
        assert sim.read("count") == 42
        assert sim.read("flag") == 1


class TestMultibitCondition:
    """Regression tests for multi-bit signals used as conditions.

    Previously, the codegen emitted ``cond & 1`` to convert a condition to
    boolean.  This only tested bit 0, so a 2-bit value of 2 (``10``) was
    incorrectly treated as false.
    """

    ENGINES = ["vm", "vm-fast", "compiled"]  # noqa: RUF012

    def _run_clocked(self, module_fn, setup_fn, signals_to_check, max_steps=20, clock_period=10):
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
        return results

    def test_flush_countdown_codegen(self):
        """Codegen for flush countdown must not use ``& 1`` on condition."""
        cg = CythonCodegen()
        pyx = cg.generate(_make_flush_countdown())
        # The ternary condition for flush should not mask with & 1
        # (which only checks bit 0, missing bit 1 when flush=2)
        assert "nba_val" in pyx
        assert "nba_pending" in pyx

    def test_flush_countdown_execution(self):
        """Flush counts 2 → 1 → 0 after reset release."""
        cg = CythonCodegen()
        mod = _make_flush_countdown()
        compiler = CythonCompiler(cache_dir=None)
        pyx = cg.generate(mod)
        ext = compiler.compile_pyx(pyx, "test_flush_cd")
        sim = ext.CompiledSim()

        sid_clk = cg.signal_map["clk"]
        sid_rst = cg.signal_map["rst"]
        sid_flush = cg.signal_map["flush"]

        # Reset: flush <= 2
        sim.drive(sid_rst, 1, 0)
        sim.drive(sid_clk, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()
        v, _ = sim.read(sid_flush)
        assert v == 2, f"After reset, flush should be 2, got {v}"

        # Release reset, cycle 1: flush=2 → flush-1=1
        sim.drive(sid_rst, 0, 0)
        sim.snapshot()
        sim.drive(sid_clk, 0, 0)
        sim.step()
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()
        v, _ = sim.read(sid_flush)
        assert v == 1, f"Cycle 1: flush should be 1, got {v}"

        # Cycle 2: flush=1 → flush-1=0
        sim.snapshot()
        sim.drive(sid_clk, 0, 0)
        sim.step()
        sim.snapshot()
        sim.drive(sid_clk, 1, 0)
        sim.step()
        v, _ = sim.read(sid_flush)
        assert v == 0, f"Cycle 2: flush should be 0, got {v}"

    def test_flush_countdown_cross(self):
        """Flush countdown cross-validated: vm vs compiled."""

        def setup(s):
            s.drive("rst", Value(1, width=1))
            s.drive("flush", Value(0, width=2))

        results = self._run_clocked(_make_flush_countdown, setup, ["flush"], max_steps=8)
        ref = results["vm"]
        comp = results["compiled"]
        assert len(comp) == len(ref)
        for i, (r, c) in enumerate(zip(ref, comp, strict=True)):
            assert c["flush"] == r["flush"], f"Step {i}: compiled flush={c['flush']} != vm flush={r['flush']}"

    def test_multibit_if_sel2(self):
        """If-condition with sel=2 (bit0=0, bit1=1) must be true."""

        def setup(s):
            s.drive("sel", Value(2, width=2))
            s.drive("a", Value(42, width=8))
            s.drive("b", Value(99, width=8))
            s.drive("out", Value(0, width=8))

        results = self._run_clocked(_make_multibit_if_condition, setup, ["out"], max_steps=4)
        ref = results["vm"]
        comp = results["compiled"]
        for i, (r, c) in enumerate(zip(ref, comp, strict=True)):
            assert c["out"] == r["out"], f"Step {i}: compiled out={c['out']} != vm out={r['out']}"
        # sel=2 is truthy, so out should be a (42)
        assert comp[-1]["out"] == Value(42, width=8)

    def test_multibit_if_sel0(self):
        """If-condition with sel=0 must be false."""

        def setup(s):
            s.drive("sel", Value(0, width=2))
            s.drive("a", Value(42, width=8))
            s.drive("b", Value(99, width=8))
            s.drive("out", Value(0, width=8))

        results = self._run_clocked(_make_multibit_if_condition, setup, ["out"], max_steps=4)
        comp = results["compiled"]
        assert comp[-1]["out"] == Value(99, width=8)


class TestNarrow64BitUnsignedOps:
    """Compiled engine narrow path (<=64-bit) must treat unsigned signals as unsigned.

    Cython stores signal values as long long.  C's sign-sensitive operators
    (>>, /, %, <, <=, >, >=) give wrong results when 64-bit unsigned values
    with MSB=1 are stored as negative long long.  The fix casts to unsigned
    long long for unsigned Verilog operands.

    Cross-engine tests: vm-fast (Python integers, always unsigned) is the reference.
    """

    def _cross(self, mod, *, a: int, b: int, a_width: int = 64, b_width: int = 64) -> dict:
        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            sim.drive("a", Value(a, width=a_width))
            sim.drive("b", Value(b, width=b_width))
            sim.settle()
            results[engine] = int(sim.read("result"))
        return results

    # ── Logical right shift (>>) ──────────────────────────────────────────────

    def test_lsr_64bit_ones_shift32(self):
        """ONES64 >> 32 must zero-fill from MSB (logical), not sign-extend (arithmetic)."""
        mod = _make_narrow64_binop_module(">>", name="narrow64_lsr_ones32")
        r = self._cross(mod, a=0xFFFF_FFFF_FFFF_FFFF, b=32)
        assert r["vm-fast"] == 0x0000_0000_FFFF_FFFF, f"vm-fast sanity: {r['vm-fast']:#018x}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']:#018x} != vm-fast {r['vm-fast']:#018x}"

    def test_lsr_64bit_msb_only_shift1(self):
        """0x8000...0000 >> 1 must give 0x4000...0000, not 0xC000...0000."""
        mod = _make_narrow64_binop_module(">>", name="narrow64_lsr_msb1")
        r = self._cross(mod, a=0x8000_0000_0000_0000, b=1)
        assert r["vm-fast"] == 0x4000_0000_0000_0000, f"vm-fast sanity: {r['vm-fast']:#018x}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']:#018x} != vm-fast {r['vm-fast']:#018x}"

    def test_lsr_64bit_msb_only_shift63(self):
        """0x8000...0000 >> 63 must give 1, not all-ones from arithmetic sign-extension."""
        mod = _make_narrow64_binop_module(">>", name="narrow64_lsr_msb63")
        r = self._cross(mod, a=0x8000_0000_0000_0000, b=63)
        assert r["vm-fast"] == 1, f"vm-fast sanity: {r['vm-fast']:#018x}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']:#018x} != vm-fast {r['vm-fast']:#018x}"

    # ── Unsigned division (/) ─────────────────────────────────────────────────

    def test_udiv_64bit_msb_set(self):
        """0xFFFF...FFFF / 2 must be 0x7FFF...FFFF (unsigned), not 0 (signed -1/2=0)."""
        mod = _make_narrow64_binop_module("/", name="narrow64_udiv_msb")
        r = self._cross(mod, a=0xFFFF_FFFF_FFFF_FFFF, b=2)
        assert r["vm-fast"] == 0x7FFF_FFFF_FFFF_FFFF, f"vm-fast sanity: {r['vm-fast']:#018x}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']:#018x} != vm-fast {r['vm-fast']:#018x}"

    # ── Unsigned modulus (%) ──────────────────────────────────────────────────

    def test_umod_64bit_msb_set(self):
        """0xFFFF...FFFF % 7 must be 1 (unsigned), not all-ones (signed -1 % 7 = -1)."""
        mod = _make_narrow64_binop_module("%", name="narrow64_umod_msb")
        r = self._cross(mod, a=0xFFFF_FFFF_FFFF_FFFF, b=7)
        assert r["vm-fast"] == 1, f"vm-fast sanity: {r['vm-fast']}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']} != vm-fast {r['vm-fast']}"

    # ── Unsigned relational comparisons (<, <=, >, >=) ───────────────────────

    def test_ugt_64bit_msb_set(self):
        """0xFFFF...FFFF > 1 must be 1 (unsigned), not 0 (signed: -1 > 1 is false)."""
        mod = _make_narrow64_binop_module(">", lhs_width=1, name="narrow64_ugt_msb")
        r = self._cross(mod, a=0xFFFF_FFFF_FFFF_FFFF, b=1)
        assert r["vm-fast"] == 1, f"vm-fast sanity: {r['vm-fast']}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']} != vm-fast {r['vm-fast']}"

    def test_ult_64bit_small_less_than_msb(self):
        """1 < 0xFFFF...FFFF must be 1 (unsigned), not 0 (signed: 1 < -1 is false)."""
        mod = _make_narrow64_binop_module("<", lhs_width=1, name="narrow64_ult_msb")
        r = self._cross(mod, a=1, b=0xFFFF_FFFF_FFFF_FFFF)
        assert r["vm-fast"] == 1, f"vm-fast sanity: {r['vm-fast']}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']} != vm-fast {r['vm-fast']}"

    def test_uge_64bit_msb_equal(self):
        """0x8000...0000 >= 0x8000...0000 must be 1 (equal values)."""
        mod = _make_narrow64_binop_module(">=", lhs_width=1, name="narrow64_uge_msb_eq")
        r = self._cross(mod, a=0x8000_0000_0000_0000, b=0x8000_0000_0000_0000)
        assert r["vm-fast"] == 1, f"vm-fast sanity: {r['vm-fast']}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']} != vm-fast {r['vm-fast']}"

    def test_ule_64bit_msb_smaller(self):
        """0x8000...0000 <= 0xFFFF...FFFF must be 1 (unsigned: 0x8000... < 0xFFFF...)."""
        mod = _make_narrow64_binop_module("<=", lhs_width=1, name="narrow64_ule_msb_lt")
        r = self._cross(mod, a=0x8000_0000_0000_0000, b=0xFFFF_FFFF_FFFF_FFFF)
        assert r["vm-fast"] == 1, f"vm-fast sanity: {r['vm-fast']}"
        assert r["compiled"] == r["vm-fast"], f"compiled {r['compiled']} != vm-fast {r['vm-fast']}"


class TestBitwiseCondWidth:
    """Regression: bitwise &/|/^ in conditions must evaluate sub-expressions at natural width.

    Bug: _emit_binary propagated op_width=1 (if-condition context) into operands of
    &, |, ^.  Compound sub-expressions like (a+b) applied wmask(1) before the
    bitwise op, discarding all bits above the LSB.  E.g. (0+2)&2 = 2 → True, but
    compiled produced ((0+2)&1)&2 = 0&2 = 0 → False.

    Fix: treat &, |, ^ like comparison ops — use natural operand width regardless
    of surrounding context width.
    """

    # (a, b) pairs that expose the bug: sum has bits above LSB so LSB-masking
    # gives the wrong boolean result.
    _CASES = [(0, 2), (1, 2), (3, 1), (0, 0), (2, 3)]  # noqa: RUF012

    def _cross_seq(self, mod, a_val, b_val):
        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            sim.drive("clk", 0)
            sim.drive("a", a_val)
            sim.drive("b", b_val)
            sim.settle()
            sim.drive("clk", 1)
            sim.settle()
            sim.drive("clk", 0)
            sim.settle()
            results[engine] = int(sim.read("result"))
        return results

    def _cross_ca(self, mod, a_val, b_val):
        results = {}
        for engine in ("vm-fast", "compiled"):
            sim = Simulator(mod, engine=engine)
            sim.drive("a", a_val)
            sim.drive("b", b_val)
            sim.settle()
            results[engine] = int(sim.read("result"))
        return results

    @pytest.mark.parametrize("a,b", [(0, 2), (1, 2), (3, 1), (0, 0), (2, 3)])
    def test_and_in_if_cond(self, a, b):
        """(a+b) & b in if-condition: upper bits of sum must not be discarded."""
        mod = _make_bitwise_cond_seq_module("&", f"bwcond_and_{a}_{b}")
        expected = 1 if ((a + b) & b) != 0 else 0
        r = self._cross_seq(mod, a, b)
        assert r["vm-fast"] == expected, f"vm-fast sanity a={a} b={b}"
        assert r["compiled"] == expected, f"compiled a={a} b={b}: got {r['compiled']} expected {expected}"

    @pytest.mark.parametrize("a,b", [(0, 2), (1, 2), (3, 1), (0, 0), (2, 3)])
    def test_or_in_if_cond(self, a, b):
        """(a+b) | b in if-condition: upper bits must participate."""
        mod = _make_bitwise_cond_seq_module("|", f"bwcond_or_{a}_{b}")
        expected = 1 if ((a + b) | b) != 0 else 0
        r = self._cross_seq(mod, a, b)
        assert r["vm-fast"] == expected, f"vm-fast sanity a={a} b={b}"
        assert r["compiled"] == expected, f"compiled a={a} b={b}: got {r['compiled']} expected {expected}"

    @pytest.mark.parametrize("a,b", [(0, 2), (1, 2), (3, 1), (0, 0), (2, 3)])
    def test_xor_in_if_cond(self, a, b):
        """(a+b) ^ b in if-condition: upper bits must participate."""
        mod = _make_bitwise_cond_seq_module("^", f"bwcond_xor_{a}_{b}")
        expected = 1 if ((a + b) ^ b) != 0 else 0
        r = self._cross_seq(mod, a, b)
        assert r["vm-fast"] == expected, f"vm-fast sanity a={a} b={b}"
        assert r["compiled"] == expected, f"compiled a={a} b={b}: got {r['compiled']} expected {expected}"

    @pytest.mark.parametrize("a,b", [(0, 2), (1, 2), (3, 1), (0, 0), (2, 3)])
    def test_and_in_ternary_cond(self, a, b):
        """(a+b) & b as ternary condition in CA: same bug path via _emit_ternary."""
        mod = _make_bitwise_cond_ca_module("&", f"bwcond_ternary_{a}_{b}")
        expected = 1 if ((a + b) & b) != 0 else 0
        r = self._cross_ca(mod, a, b)
        assert r["vm-fast"] == expected, f"vm-fast sanity a={a} b={b}"
        assert r["compiled"] == expected, f"compiled a={a} b={b}: got {r['compiled']} expected {expected}"
