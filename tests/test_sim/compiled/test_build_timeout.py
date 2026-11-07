"""Build deadlines use elapsed wall time and can accommodate large designs."""

import subprocess

import pytest

from veriforge.sim.compiled import compiler


@pytest.mark.parametrize("value, expected", [(None, 3600), ("3600", 3600), ("7200", 7200), ("0", 0), ("0.25", 0.25)])
def test_build_timeout_configuration(monkeypatch, value, expected):
    monkeypatch.delenv("VERIFORGE_COMPILE_TIMEOUT", raising=False)
    monkeypatch.delenv("VERILOG_TOOLS_COMPILE_TIMEOUT", raising=False)
    if value is not None:
        monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", value)
    assert compiler._build_timeout() == expected


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "no", ""])
def test_invalid_timeout_does_not_start_build(monkeypatch, tmp_path, value):
    pytest.importorskip("Cython")
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", value)

    def unexpected_process(*args, **kwargs):
        pytest.fail("invalid timeout launched a compiler process")

    monkeypatch.setattr(compiler.subprocess, "Popen", unexpected_process)
    with pytest.raises(ValueError, match="VERIFORGE_COMPILE_TIMEOUT"):
        compiler.CythonCompiler()._run_build(str(tmp_path))


def _fake_build(monkeypatch, finish_after):
    pytest.importorskip("Cython")
    clock = [100.0]
    processes = []

    class Build:
        pid = 12345
        returncode = 0

        def __init__(self, *args, **kwargs):
            self.started = clock[0]
            self.killed = False
            self.waited = False
            self.polls = []
            processes.append(self)

        def communicate(self, timeout):
            assert timeout > 0
            self.polls.append(timeout)
            remaining = finish_after - (clock[0] - self.started)
            if remaining <= timeout:
                clock[0] += remaining
                return "build output", ""
            clock[0] += timeout
            raise subprocess.TimeoutExpired("build", timeout)

        def kill(self):
            self.killed = True

        def wait(self):
            self.waited = True

    monkeypatch.setattr(compiler.subprocess, "Popen", Build)
    monkeypatch.setattr(compiler.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("VERIFORGE_QUIET_BUILD", "1")
    return clock, processes


@pytest.mark.parametrize("limit, duration", [("3600", 2100), ("7200", 4000), ("0", 8000)])
def test_long_build_completes_without_real_wait(monkeypatch, tmp_path, limit, duration):
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", limit)
    clock, processes = _fake_build(monkeypatch, duration)
    compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 100 + duration
    assert not processes[0].killed


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_deadline_kills_build_and_reports_configuration(monkeypatch, tmp_path, system):
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", "10.5")
    clock, processes = _fake_build(monkeypatch, 100)
    monkeypatch.setattr(compiler.platform, "system", lambda: system)
    killed = []
    monkeypatch.setattr(compiler.subprocess, "run", lambda command, **kwargs: killed.append(command))
    with pytest.raises(RuntimeError, match=r"after 10\.5 s.*VERIFORGE_COMPILE_TIMEOUT"):
        compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 110.5
    assert processes[0].polls == [5, 5, 0.5]
    assert processes[0].killed and processes[0].waited
    assert bool(killed) == (system == "Windows")
    if killed:
        assert killed == [["taskkill", "/F", "/T", "/PID", "12345"]]


def test_timeout_policy_does_not_invalidate_binary_cache(monkeypatch):
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", "600")
    original = compiler._cache_key("same generated source")
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", "7200")
    assert compiler._cache_key("same generated source") == original


def test_deadline_counts_scheduler_delay(monkeypatch, tmp_path):
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", "10")
    monkeypatch.setattr(compiler.platform, "system", lambda: "Linux")
    clock, processes = _fake_build(monkeypatch, 100)
    process_type = compiler.subprocess.Popen

    def delayed_communication(self, timeout):
        clock[0] += 11
        raise subprocess.TimeoutExpired("build", timeout)

    monkeypatch.setattr(process_type, "communicate", delayed_communication)
    with pytest.raises(RuntimeError, match="after 10 s"):
        compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 111
    assert processes[0].killed and processes[0].waited
