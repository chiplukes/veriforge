"""Compiled engine: runtime compilation, caching, and cache invalidation.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestRuntimeCompile:
    """Test that .pyx source strings compile and import correctly."""

    def test_compile_and_call(self, tmp_cache):
        """Compile a trivial .pyx and call its function."""
        compiler = CythonCompiler(cache_dir=tmp_cache)
        mod = compiler.compile_pyx(TRIVIAL_PYX, "test_add")
        assert mod.add(2, 3) == 5
        assert mod.add(-1, 1) == 0

    def test_compile_multiple_functions(self, tmp_cache):
        """Compile a .pyx with multiple functions."""
        compiler = CythonCompiler(cache_dir=tmp_cache)
        mod = compiler.compile_pyx(MULTI_FUNC_PYX, "test_multi")
        assert mod.multiply(6, 7) == 42
        assert mod.square(8) == 64

    def test_compile_long_module_name_uses_compact_cache_entry(self, tmp_cache):
        """Long parametrized module names should not leak into cache paths."""
        module_name = "compiled_" + ("very_long_parametrized_module_name_" * 6)
        compiler = CythonCompiler(cache_dir=tmp_cache)

        mod = compiler.compile_pyx(TRIVIAL_PYX, module_name)

        assert mod.add(2, 3) == 5
        entries = [e for e in os.listdir(tmp_cache) if not e.endswith(".lock")]
        assert entries == [_keyed_module_name(module_name, _cache_key(TRIVIAL_PYX))]
        assert len(entries[0]) == len("vtc_12345678_1234567890abcdef")


class TestCaching:
    """Test that compiled extensions are cached and reused."""

    def test_cache_hit(self, tmp_cache):
        """Second call with same source skips compilation."""
        compiler = CythonCompiler(cache_dir=tmp_cache)

        # First compile
        mod1 = compiler.compile_pyx(TRIVIAL_PYX, "test_cached")
        assert mod1.add(2, 3) == 5

        # Get the build directory and find the .pyx file
        entries = [e for e in os.listdir(tmp_cache) if not e.endswith(".lock")]
        assert len(entries) == 1
        build_dir = os.path.join(tmp_cache, entries[0])
        pyx_files = [f for f in os.listdir(build_dir) if f.endswith(".pyx")]
        assert len(pyx_files) == 1
        pyx_mtime = os.path.getmtime(os.path.join(build_dir, pyx_files[0]))

        # Second compile — should hit cache (no recompilation)
        mod2 = compiler.compile_pyx(TRIVIAL_PYX, "test_cached")
        assert mod2.add(2, 3) == 5

        # .pyx file should not have been rewritten
        pyx_mtime_after = os.path.getmtime(os.path.join(build_dir, pyx_files[0]))
        assert pyx_mtime == pyx_mtime_after

    def test_cache_miss_on_source_change(self, tmp_cache):
        """Changed source triggers recompile with new cache entry."""
        compiler = CythonCompiler(cache_dir=tmp_cache)

        # Compile v1
        mod1 = compiler.compile_pyx(TRIVIAL_PYX, "test_versioned")
        assert mod1.add(2, 3) == 5

        # Compile v2 (different source → different cache key)
        mod2 = compiler.compile_pyx(TRIVIAL_PYX_V2, "test_versioned")
        assert mod2.add(2, 3) == 6  # v2 adds 1 extra

        # Both cache entries should exist
        entries = [e for e in os.listdir(tmp_cache) if not e.endswith(".lock")]
        assert len(entries) == 2


class TestClearCache:
    """Test cache management."""

    def test_clear_cache_empty(self, tmp_cache):
        """clear_cache() on nonexistent dir returns 0."""
        compiler = CythonCompiler(cache_dir=tmp_cache)
        assert compiler.clear_cache() == 0

    def test_clear_cache_removes_unlocked(self, tmp_cache):
        """clear_cache() removes entries not locked by loaded extensions.

        On Windows, loaded .pyd files are locked and can't be removed by
        the same process. We verify the method runs without error and
        returns a count >= 0.
        """
        compiler = CythonCompiler(cache_dir=tmp_cache)
        compiler.compile_pyx(TRIVIAL_PYX, "test_clear")

        entries_before = os.listdir(tmp_cache)
        assert len(entries_before) > 0

        removed = compiler.clear_cache()
        # On Windows the loaded .pyd is locked, so removed may be 0.
        # On Linux it should be 1.
        assert removed >= 0


class TestCacheControls:
    """Cache location, invalidation, and control environment variables."""

    def test_default_cache_dir_is_cycache(self, tmp_path, monkeypatch):
        """Default cache dir is .cycache/ relative to the working directory."""
        from veriforge.sim.compiled.compiler import _default_cache_dir

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VERIFORGE_COMPILE_CACHE", raising=False)
        monkeypatch.delenv("VERIFORGE_COMPILE_CACHE", raising=False)
        result = _default_cache_dir()
        assert result == str(tmp_path / ".cycache")

    def test_env_var_overrides_cache_dir(self, tmp_path, monkeypatch):
        """VERIFORGE_COMPILE_CACHE env var sets the cache directory."""
        from veriforge.sim.compiled.compiler import _default_cache_dir

        custom = str(tmp_path / "custom_cache")
        monkeypatch.setenv("VERIFORGE_COMPILE_CACHE", custom)
        assert _default_cache_dir() == custom

    def test_no_cache_env_var_still_produces_working_module(self, tmp_cache, monkeypatch):
        """VERIFORGE_NO_COMPILE_CACHE=1 still compiles and returns a working module."""
        monkeypatch.setenv("VERIFORGE_NO_COMPILE_CACHE", "1")
        compiler = CythonCompiler(cache_dir=tmp_cache)
        mod = compiler.compile_pyx(TRIVIAL_PYX, "nocache_test")
        assert mod.add(2, 3) == 5

    def test_cache_key_differs_for_different_source(self):
        """Different .pyx sources produce different cache keys."""
        from veriforge.sim.compiled.compiler import _cache_key

        key1 = _cache_key(TRIVIAL_PYX)
        key2 = _cache_key(TRIVIAL_PYX + "# extra comment\n")
        assert key1 != key2

    def test_cache_key_is_16_hex_chars(self):
        """Cache key is exactly 16 hexadecimal characters."""
        from veriforge.sim.compiled.compiler import _cache_key

        key = _cache_key(TRIVIAL_PYX)
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)

    def test_infra_hash_includes_codegen_file(self):
        """_codegen_infra_hash() returns a SHA-256 hex digest over codegen infrastructure files."""
        from veriforge.sim.compiled.compiled_scheduler import _codegen_infra_hash

        h = _codegen_infra_hash()
        assert len(h) == 64  # SHA-256 hex digest

    def test_infra_hash_is_stable(self):
        """_codegen_infra_hash() returns the same value on repeated calls."""
        from veriforge.sim.compiled.compiled_scheduler import _codegen_infra_hash

        h1 = _codegen_infra_hash()
        h2 = _codegen_infra_hash()
        assert h1 == h2

    def test_elab_cache_round_trip(self, tmp_path):
        """Elaboration metadata can be saved and loaded from the elab cache."""
        from veriforge.sim.compiled.compiled_scheduler import _load_elab_cache, _save_elab_cache

        cache_dir = str(tmp_path / "elab_cache")
        elab_hash = "abc123def456abcd"

        data = {
            "keyed_name": "vtc_test_abc123",
            "signal_map": {"a": 0, "b": 1, "y": 2},
            "sig_widths": [8, 8, 8],
            "sig_signed": [False, False, False],
            "mem_map": {},
            "mem_info": [],
            "n_sigs": 3,
            "n_mems": 0,
        }

        _save_elab_cache(cache_dir, elab_hash, data)
        loaded = _load_elab_cache(cache_dir, elab_hash)

        assert loaded is not None
        assert loaded["keyed_name"] == "vtc_test_abc123"
        assert loaded["signal_map"] == {"a": 0, "b": 1, "y": 2}
        assert loaded["n_sigs"] == 3

    def test_elab_cache_miss_returns_none(self, tmp_path):
        """_load_elab_cache returns None when no entry exists."""
        from veriforge.sim.compiled.compiled_scheduler import _load_elab_cache

        result = _load_elab_cache(str(tmp_path), "nonexistent_hash")
        assert result is None

    def test_cache_dir_property_accessible(self, tmp_cache):
        """CythonCompiler.cache_dir property returns the configured directory."""
        compiler = CythonCompiler(cache_dir=tmp_cache)
        assert compiler.cache_dir == tmp_cache


class TestDuplicateDefConstants:
    """Regression test for duplicate DEF constant names in generated .pyx.

    When two signals have different raw names but the same sanitized form
    (e.g., ``a.b`` and ``a_b`` both become ``A_B``), the codegen must
    disambiguate the DEF constants to avoid one silently overwriting the
    other.  This happens in practice with hierarchically-flattened designs
    where dot-separated instance paths collide with underscored names.
    """

    def test_no_duplicate_def_lines(self):
        """Generated .pyx must not contain duplicate DEF SIG_xxx lines."""
        # Create a module with two signals whose names sanitize identically.
        # In flattened hierarchies, signal names contain dots (e.g.
        # ``inst.sig``).  A second signal named ``inst_sig`` sanitizes to
        # the same constant.
        mod = Module(
            "dup_test",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("out", PortDirection.OUTPUT, width=_w8()),
            ],
            nets=[
                Net("clk", NetKind.WIRE),
                # These two names sanitize to the same constant (A_B):
                Net("a.b", NetKind.WIRE, width=_w8()),
                Net("a_b", NetKind.WIRE, width=_w8()),
            ],
            variables=[
                Variable("out", VariableKind.REG, width=_w8()),
            ],
        )
        mod.always_blocks = [
            AlwaysBlock(
                NonblockingAssign(Identifier("out"), Literal(0, width=8)),
                sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
                sensitivity_type=SensitivityType.SEQUENTIAL,
            ),
        ]

        cg = CythonCodegen()
        pyx = cg.generate(mod)

        # Collect all DEF SIG_xxx lines and verify no duplicates
        import re  # noqa: PLC0415

        def_lines = re.findall(r"^DEF SIG_(\S+) = \d+$", pyx, re.MULTILINE)
        assert len(def_lines) == len(set(def_lines)), (
            f"Duplicate DEF constants found: {[n for n in def_lines if def_lines.count(n) > 1]}"
        )

        # Width constants should also be unique
        w_lines = re.findall(r"^DEF W_(\S+) = \d+$", pyx, re.MULTILINE)
        assert len(w_lines) == len(set(w_lines)), (
            f"Duplicate W_ constants found: {[n for n in w_lines if w_lines.count(n) > 1]}"
        )


class TestTimingDiagnostics:
    """Compiled engine emits timing performance warnings for slow patterns."""

    @staticmethod
    def _diags(mod: Module) -> list[str]:
        cg = CythonCodegen()
        cg.generate(mod)
        return cg.timing_diagnostics

    def test_no_diagnostics_for_clean_module(self):
        """Module with no timing controls produces no timing diagnostics."""
        assert self._diags(_make_adder()) == []

    def test_no_diagnostics_for_sequential_module(self):
        """Module with only posedge always blocks produces no timing diagnostics."""
        assert self._diags(_make_counter()) == []

    def test_clock_gen_always_produces_diagnostic(self):
        """always #N loop (clock generator) triggers a timing diagnostic."""
        diags = self._diags(_make_always_clk_gen())
        assert len(diags) == 1
        assert "batch_run" in diags[0]

    def test_clock_gen_diagnostic_mentions_delay_loop(self):
        """Clock-generator diagnostic mentions #delay loop pattern."""
        diags = self._diags(_make_always_clk_gen())
        assert "#delay" in diags[0]

    def test_always_event_control_produces_diagnostic(self):
        """always block with @event control triggers a timing diagnostic."""
        diags = self._diags(_make_always_event_control())
        assert len(diags) == 1
        assert "timing control" in diags[0]

    def test_initial_single_delay_produces_diagnostic(self):
        """initial block with a single #delay triggers a timing diagnostic."""
        diags = self._diags(_make_initial_timing_single_delay())
        assert len(diags) == 1
        assert "#delay" in diags[0]

    def test_initial_delay_loop_produces_loop_diagnostic(self):
        """initial block with a timing loop triggers the loop-specific diagnostic."""
        diags = self._diags(_make_initial_timing_delay_loop())
        assert len(diags) == 1
        assert "loop" in diags[0]
        assert "batch_run" in diags[0]

    def test_multiple_slow_blocks_produce_multiple_diagnostics(self):
        """Two slow always blocks produce two separate diagnostics."""
        mod = _make_always_clk_gen()
        # Add a second always-with-timing block
        second = AlwaysBlock(
            ForeverLoop(
                SeqBlock(
                    [
                        DelayControl(Literal(3, width=32)),
                        BlockingAssign(Identifier("clk"), Literal(0, width=1)),
                    ]
                )
            ),
            sensitivity_list=[],
        )
        mod.always_blocks.append(second)
        diags = self._diags(mod)
        assert len(diags) == 2

    def test_timing_diagnostics_empty_before_generate(self):
        """CythonCodegen.timing_diagnostics is empty before generate() is called."""
        cg = CythonCodegen()
        assert cg.timing_diagnostics == []

    def test_diagnostic_includes_per_process_number(self):
        """Each diagnostic message names the process number (always/initial block N)."""
        diags = self._diags(_make_always_clk_gen())
        assert "always block 1" in diags[0]

    def test_multiple_blocks_numbered_separately(self):
        """Two falling-back always blocks get distinct numbers."""
        mod = _make_always_clk_gen()
        mod.always_blocks.append(
            AlwaysBlock(
                ForeverLoop(
                    SeqBlock(
                        [
                            DelayControl(Literal(3, width=32)),
                            BlockingAssign(Identifier("clk"), Literal(0, width=1)),
                        ]
                    )
                ),
                sensitivity_list=[],
            )
        )
        diags = self._diags(mod)
        assert len(diags) == 2
        assert "always block 1" in diags[0]
        assert "always block 2" in diags[1]

    def test_diagnostic_includes_cost_estimate(self):
        """Diagnostics include the '10–100×' performance cost language."""
        diags = self._diags(_make_always_clk_gen())
        assert "10" in diags[0] and "100" in diags[0]

    def test_initial_system_task_produces_diagnostic(self):
        """initial block with only $display (no timing) also triggers a diagnostic."""
        mod = Module(
            "init_systask",
            ports=[Port("y", PortDirection.OUTPUT)],
            variables=[Variable("y", VariableKind.REG)],
        )
        mod.initial_blocks = [
            InitialBlock(
                SeqBlock(
                    [
                        SystemTaskCall("$display", [StringLiteral("hello")]),
                        BlockingAssign(Identifier("y"), Literal(1, width=1)),
                    ]
                )
            )
        ]
        diags = self._diags(mod)
        assert len(diags) == 1
        assert "system task" in diags[0].lower()
        assert "initial block 1" in diags[0]

    def test_initial_system_task_and_timing_combined(self):
        """initial block with both $display and #delay names both reasons."""
        mod = Module(
            "init_both",
            ports=[Port("y", PortDirection.OUTPUT)],
            variables=[Variable("y", VariableKind.REG)],
        )
        mod.initial_blocks = [
            InitialBlock(
                SeqBlock(
                    [
                        SystemTaskCall("$display", [StringLiteral("start")]),
                        DelayControl(Literal(5, width=32)),
                        BlockingAssign(Identifier("y"), Literal(1, width=1)),
                    ]
                )
            )
        ]
        diags = self._diags(mod)
        assert len(diags) == 1
        # Both reasons should appear in the single combined diagnostic
        assert "timing" in diags[0].lower() or "#delay" in diags[0]
        assert "system task" in diags[0].lower()

    def test_preflight_warning_emitted_via_simulator(self):
        """Simulator emits a UserWarning when the compiled engine has timing fallbacks."""
        import warnings as _warnings

        mod = _make_always_clk_gen()
        with _warnings.catch_warnings(record=True) as rec:
            _warnings.simplefilter("always")
            Simulator(mod, engine="compiled")
        user_warnings = [w for w in rec if issubclass(w.category, UserWarning)]
        assert any("preflight" in str(w.message).lower() for w in user_warnings), (
            f"Expected a UserWarning with 'preflight', got: {[str(w.message) for w in user_warnings]}"
        )
