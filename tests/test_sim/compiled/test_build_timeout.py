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


def _fake_build(monkeypatch, *durations):
    """Fake build processes; the i-th one launched takes ``durations[i]`` seconds."""
    pytest.importorskip("Cython")
    clock = [100.0]
    processes = []

    class Build:
        pid = 12345
        returncode = 0

        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            self.finish_after = durations[len(processes)]
            self.started = clock[0]
            self.killed = False
            self.waited = False
            self.polls = []
            processes.append(self)

        def communicate(self, timeout):
            assert timeout > 0
            self.polls.append(timeout)
            remaining = self.finish_after - (clock[0] - self.started)
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


@pytest.mark.parametrize("limit, cython, cc", [("3600", 300, 1800), ("7200", 1000, 3000), ("0", 2000, 6000)])
def test_long_build_completes_without_real_wait(monkeypatch, tmp_path, limit, cython, cc):
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", limit)
    clock, processes = _fake_build(monkeypatch, cython, cc)
    compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 100 + cython + cc
    assert [process.cmd[1:3] for process in processes] == [["setup.py", "cythonize"], ["setup.py", "build_ext"]]
    assert not any(process.killed for process in processes)


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_deadline_kills_build_and_reports_configuration(monkeypatch, tmp_path, system):
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", "10.5")
    clock, processes = _fake_build(monkeypatch, 100, 0)
    monkeypatch.setattr(compiler.platform, "system", lambda: system)
    killed = []
    monkeypatch.setattr(compiler.subprocess, "run", lambda command, **kwargs: killed.append(command))
    with pytest.raises(RuntimeError, match=r"after 10\.5 s.*VERIFORGE_COMPILE_TIMEOUT"):
        compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 110.5
    assert processes[0].polls == [5, 5, 0.5]
    assert processes[0].killed and processes[0].waited
    assert len(processes) == 1
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
    clock, processes = _fake_build(monkeypatch, 100, 0)
    process_type = compiler.subprocess.Popen

    def delayed_communication(self, timeout):
        clock[0] += 11
        raise subprocess.TimeoutExpired("build", timeout)

    monkeypatch.setattr(process_type, "communicate", delayed_communication)
    with pytest.raises(RuntimeError, match="after 10 s"):
        compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 111
    assert processes[0].killed and processes[0].waited


def test_deadline_spans_both_build_steps(monkeypatch, tmp_path):
    """Cython's time counts against the deadline the C compiler step gets."""
    monkeypatch.setenv("VERIFORGE_COMPILE_TIMEOUT", "10.5")
    monkeypatch.setattr(compiler.platform, "system", lambda: "Linux")
    clock, processes = _fake_build(monkeypatch, 6, 100)
    with pytest.raises(RuntimeError, match=r"after 10\.5 s \(C compiler step\)"):
        compiler.CythonCompiler()._run_build(str(tmp_path))
    assert clock[0] == 110.5
    assert processes[1].polls == [4.5]
    assert not processes[0].killed and processes[1].killed
