"""Build options select the requested binary even with a warm cache."""

import json
import hashlib
from pathlib import Path

import pytest

from veriforge.project import parse_file
from veriforge.sim.compiled.compiled_scheduler import _compute_elab_hash
from veriforge.sim.compiled.compiler import CythonCompiler, _cache_key, _cache_key_from_source_hash
from veriforge.sim.testbench import Simulator


def test_effective_split_configuration_changes_both_cache_keys(tmp_path, monkeypatch):
    path = tmp_path / "top.sv"
    path.write_text("module top(output y); assign y = 1'b1; endmodule")
    module = parse_file(path).modules[0]
    keys = []
    for value in ("0", "1", "true"):
        monkeypatch.setenv("VERIFORGE_COMPILE_SPLIT", value)
        keys.append((_compute_elab_hash(module, [str(path)]), _cache_key("same source")))
    assert keys[0][0] != keys[1][0]
    assert keys[0][1] != keys[1][1]
    assert keys[1] == keys[2]


def test_streaming_and_string_compilation_share_binary_identity():
    source = "def add(a, b):\n    return a + b\n"
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert _cache_key(source) == _cache_key_from_source_hash(digest)


def test_elaboration_key_includes_delta_limit_and_toolchain(tmp_path, monkeypatch):
    path = tmp_path / "top.sv"
    path.write_text("module top; endmodule")
    module = parse_file(path).modules[0]
    baseline = _compute_elab_hash(module, [str(path)], delta_limit=10)
    assert baseline != _compute_elab_hash(module, [str(path)], delta_limit=20)
    monkeypatch.setenv("CFLAGS", "-O1")
    assert baseline != _compute_elab_hash(module, [str(path)], delta_limit=10)


def test_disabled_cache_bypasses_warm_elaboration(tmp_path, monkeypatch):
    path = tmp_path / "top.sv"
    path.write_text("module top(input a, output y); assign y = ~a; endmodule")
    design = parse_file(path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("VERIFORGE_COMPILE_CACHE", str(cache))
    builds = []
    original = CythonCompiler._run_build

    def record_build(self, *args, **kwargs):
        builds.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CythonCompiler, "_run_build", record_build)
    for disabled, expected_builds in ((False, 1), (False, 1), (True, 2)):
        monkeypatch.setenv("VERIFORGE_NO_COMPILE_CACHE", "1" if disabled else "0")
        sim = Simulator(design.modules[0], engine="compiled", design=design)
        sim.drive("a", 0)
        sim.settle()
        assert int(sim.read("y")) == 1
        assert len(builds) == expected_builds
    assert len(list(cache.glob("_elab_*.json"))) == 1


def test_warm_elaboration_respects_new_delta_limit(tmp_path, monkeypatch):
    path = tmp_path / "top.sv"
    path.write_text("module top(input a, output y); assign y = a; endmodule")
    design = parse_file(path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("VERIFORGE_COMPILE_CACHE", str(cache))
    for limit in (20, 40):
        sim = Simulator(design.modules[0], engine="compiled", design=design, delta_limit=limit)
        sim.drive("a", 1)
        sim.settle()
        assert int(sim.read("y")) == 1
    entries = [json.loads(path.read_text()) for path in cache.glob("_elab_*.json")]
    assert len(entries) == 2
    assert len({entry["keyed_name"] for entry in entries}) == 2
