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
