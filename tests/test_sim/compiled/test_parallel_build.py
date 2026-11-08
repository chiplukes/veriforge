"""Actual source jobs overlap within one extension and obey resource caps."""

from threading import Lock
import time

import pytest
from setuptools._distutils.ccompiler import new_compiler

from veriforge.sim.compiled import _build
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design


def test_source_jobs_overlap_and_restore_compiler_after_error(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIFORGE_COMPILE_JOBS", "2")
    monkeypatch.setenv("VERIFORGE_COMPILE_MEMORY_MB", "1024")
    monkeypatch.setenv("VERIFORGE_COMPILE_WORKER_MEMORY_MB", "128")
    monkeypatch.setattr(_build, "_available_memory", lambda: 8 * 1024**3)
    monkeypatch.setattr(_build.os, "cpu_count", lambda: 8)

    class ProbeCompiler(type(new_compiler())):
        compiler_type = "unix"
        executables = {}
        src_extensions = [".c"]
        obj_extension = ".o"

        def __init__(self):
            super().__init__(force=True)
            self.active = 0
            self.peak = 0
            self.lock = Lock()
            self.fail = False

        def _compile(self, obj, src, *args):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(0.03)
                if self.fail and src.endswith("0.c"):
                    raise RuntimeError("failed source job")
            finally:
                with self.lock:
                    self.active -= 1

    sources = []
    for i in range(5):
        path = tmp_path / f"{i}.c"
        path.write_text("int f(void) { return 0; }")
        sources.append(str(path))
    compiler = ProbeCompiler()
    original = compiler._compile
    objects = _build.compile_objects(compiler, sources, output_dir=str(tmp_path / "obj"))
    assert len(objects) == 5
    assert compiler.peak == 2
    assert compiler._compile == original
    compiler.fail = True
    with pytest.raises(RuntimeError, match="failed source job"):
        _build.compile_objects(compiler, sources, output_dir=str(tmp_path / "obj"))
    assert compiler._compile == original
    assert compiler.active == 0


def test_memory_budget_caps_workers(monkeypatch):
    monkeypatch.setattr(_build, "_available_memory", lambda: 4 * 1024**3)
    monkeypatch.setenv("VERIFORGE_COMPILE_JOBS", "8")
    monkeypatch.setenv("VERIFORGE_COMPILE_MEMORY_MB", "1024")
    monkeypatch.setenv("VERIFORGE_COMPILE_WORKER_MEMORY_MB", "1024")
    assert _build.build_worker_limit(9) == 1


def test_custom_compiler_loop_keeps_its_own_behavior(monkeypatch):
    monkeypatch.setattr(_build, "build_worker_limit", lambda count: 4)

    class CustomCompiler:
        compiler_type = "unix"

        def compile(self, sources):
            return ["custom", *sources]

    assert _build.compile_objects(CustomCompiler(), ["a.c", "b.c"]) == ["custom", "a.c", "b.c"]


@pytest.mark.parametrize("split", ["0", "1"])
def test_multi_source_extension_matches_unsplit_wide_results(parser, monkeypatch, split):
    monkeypatch.setenv("VERIFORGE_COMPILE_SPLIT", split)
    monkeypatch.setenv("VERIFORGE_COMPILE_JOBS", "2")
    monkeypatch.setenv("VERIFORGE_COMPILE_MEMORY_MB", "512")
    monkeypatch.setenv("VERIFORGE_COMPILE_WORKER_MEMORY_MB", "128")
    source = """module top(input [255:0] a, output [255:0] y);
  for (genvar i=0; i<256; i=i+1) begin : g
    assign y[i] = ~a[i];
  end
endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine="compiled")
    data = (1 << 255) | (1 << 64) | 0x1234
    sim.drive("a", Value(data, width=256))
    sim.settle()
    assert int(sim.read("y")) == data ^ ((1 << 256) - 1)
    sim.drive("a", Value(data, mask=1 << 130, width=256))
    sim.settle()
    assert sim.read("y").mask == 1 << 130


def _msvc_compiler(monkeypatch, *, host="win-amd64", native_tools=True):
    """setuptools' real MSVC compiler, with the Visual Studio lookup stubbed so it initializes anywhere.

    Returns the compiler, the commands it would run, and the vcvarsall
    specs it asked for (``amd64`` is the 64-bit-hosted toolset).
    """
    msvc = pytest.importorskip("setuptools._distutils.compilers.C.msvc")
    from setuptools._distutils.errors import DistutilsPlatformError

    specs = []

    def vc_env(spec):
        specs.append(spec)
        if spec == "amd64" and not native_tools:
            raise DistutilsPlatformError("Error executing vcvarsall.bat amd64")
        return {"path": "", "include": "", "lib": ""}

    monkeypatch.setattr(msvc, "_get_vc_env", vc_env)
    monkeypatch.setattr(msvc, "_find_exe", lambda exe, paths: exe)
    monkeypatch.setattr(msvc, "get_host_platform", lambda: host)
    compiler = msvc.Compiler()
    commands = []
    compiler.spawn = commands.append
    return compiler, commands, specs


def _compile_and_link(compiler, tmp_path):
    source = tmp_path / "m.c"
    source.write_text("int f(void) { return 0; }")
    objects = compiler.compile([str(source)], output_dir=str(tmp_path / "t"))
    compiler.link_shared_object(objects, str(tmp_path / "m.pyd"))


def test_msvc_flags_for_generated_extensions(tmp_path, monkeypatch):
    """setuptools compiles with ``/GL`` and links with ``/LTCG``: all code
    generation waits for the link, which holds the whole program's
    intermediate code in memory. A generated extension is one translation
    unit, so there is no cross-module inlining to gain. ``/bigobj``: a large
    generated file has more sections than the default object format holds
    (MSVC error C1128; axis_pix_correction2 needed it, 2026-11-07)."""
    compiler, commands, _specs = _msvc_compiler(monkeypatch)
    _build.configure_msvc(compiler, "win-amd64")
    _compile_and_link(compiler, tmp_path)
    cl, link = commands
    assert cl[0] == "cl.exe" and "/O2" in cl and "/bigobj" in cl and "/GL" not in cl
    assert link[0] == "link.exe" and "/DLL" in link and "/LTCG" not in link


def test_msvc_uses_the_64_bit_hosted_toolset(monkeypatch):
    """setuptools always picks the 32-bit-hosted cross compiler
    (``x86_amd64``), whose cl.exe ran out of heap (C1060) on the full
    axis_pix_correction2 IP (2026-11-07)."""
    compiler, _commands, specs = _msvc_compiler(monkeypatch)
    _build.configure_msvc(compiler, "win-amd64")
    assert specs == ["amd64"] and compiler.initialized


def test_msvc_falls_back_without_64_bit_hosted_tools(monkeypatch):
    compiler, _commands, specs = _msvc_compiler(monkeypatch, native_tools=False)
    _build.configure_msvc(compiler, "win-amd64")
    assert specs == ["amd64", "x86_amd64"] and compiler.initialized
    assert "/bigobj" in compiler.compile_options and "/GL" not in compiler.compile_options


@pytest.mark.parametrize("host, target, spec", [("win-amd64", "win32", "x86"), ("win-arm64", "win-arm64", "arm64")])
def test_msvc_other_targets_keep_the_setuptools_toolset(monkeypatch, host, target, spec):
    compiler, _commands, specs = _msvc_compiler(monkeypatch, host=host)
    _build.configure_msvc(compiler, target)
    assert specs == [spec]


def test_msvc_debug_links_drop_ltcg_too(monkeypatch):
    compiler, _commands, _specs = _msvc_compiler(monkeypatch)
    compiler.initialize("win-amd64")
    assert "/GL" in compiler.compile_options and "/LTCG" in compiler.ldflags_shared_debug
    _build.configure_msvc(compiler)
    assert all("/LTCG" not in flags for flags in compiler._ldflags.values())
    assert "/GL" not in compiler.compile_options
    assert compiler.compile_options.count("/bigobj") == 1 and "/bigobj" in compiler.compile_options_debug


def test_msvc_configuration_leaves_other_compilers_alone():
    compiler = new_compiler(compiler="unix")
    before = list(compiler.compiler_so)
    _build.configure_msvc(compiler)
    assert compiler.compiler_so == before


def test_split_workers_with_python_integer_code_load(parser, monkeypatch, tmp_path):
    """Split workers are linked into the main extension, not imported, so
    their Cython module state was never initialized. Worker code using
    Python integers (a wide `?:` with a `'0` fill falls back to them) then
    dereferenced a NULL cached constant: axis_pix_correction2 segfaulted on
    load with VERIFORGE_COMPILE_SPLIT=1 (2026-11-07)."""
    from veriforge.sim.compiled.codegen import CythonCodegen
    from veriforge.sim.elaborate import flatten_module

    monkeypatch.setenv("VERIFORGE_COMPILE_SPLIT", "1")
    source = """module t(input s, input [127:0] x, output [219:0] y);
  genvar i;
  for (i = 0; i < 220; i = i + 1) begin : g
    wire [127:0] w = s ? (x ^ i) : '0;
    assign y[i] = ^w;
  end
endmodule"""
    design = tree_to_design(parser.build_tree(source))
    paths, _digest = CythonCodegen().generate_to_files(flatten_module(design.modules[0], design), str(tmp_path))
    workers = [path for path in paths if "worker" in path and path.endswith(".pyx")]
    assert workers and any("_py_bits(c," in open(path, encoding="utf-8").read() for path in workers)
    results = {}
    for engine in ("reference", "compiled"):
        sim = Simulator(design.modules[0], engine=engine)
        for s, x in ((1, (1 << 127) | 0x1234), (0, 5), (1, (1 << 200) - 1 >> 73)):
            sim.drive("s", s)
            sim.drive("x", Value(x, width=128))
            sim.settle()
            results.setdefault(engine, []).append(sim.read("y"))
    assert results["compiled"] == results["reference"]
