"""Memory synchronization across VM timing-control coroutine boundaries."""

from copy import deepcopy
from functools import lru_cache

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


@lru_cache(maxsize=8)
def _module(source):
    return tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]


def _run(source, engine, max_time=95):
    sim = Simulator(deepcopy(_module(source)), engine=engine)
    sim.run(max_time=max_time)
    return sim


def _state(sim, *names):
    return tuple((sim.read(name).val, sim.read(name).mask) for name in names)


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_timed_clock_skips_unrelated_memory_sync_and_preserves_native_writes():
    source = """module timed_clock(output reg clk, output reg [7:0] q, output [7:0] memout);
        reg [7:0] mem [0:31];
        assign memout=mem[0];
        initial begin clk=0; q=0; mem[0]=0; end
        always #5 clk=~clk;
        always @(posedge clk) begin q<=q+1; mem[0]<=q+1; end
    endmodule"""
    native = _run(source, "vm-fast")
    assert any(names.skip_memory_sync for names in native._sched._coro_sync_names.values())
    assert _state(native, "q", "memout", "clk") == _state(_run(source, "vm"), "q", "memout", "clk")
    assert _state(native, "q", "memout", "clk") == _state(_run(source, "reference"), "q", "memout", "clk")
    assert native.read("memout").val == 10


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_timed_memory_access_retains_full_sync():
    source = """module timed_mem(output reg clk, output [7:0] memout);
        reg [7:0] mem [0:31];
        assign memout=mem[0];
        initial begin clk=0; mem[0]=0; end
        always #5 begin mem[0]=mem[0]+1; clk=~clk; end
    endmodule"""
    native = _run(source, "vm-fast")
    assert all(not names.skip_memory_sync for names in native._sched._coro_sync_names.values())
    assert _state(native, "memout", "clk") == _state(_run(source, "vm"), "memout", "clk")
    assert _state(native, "memout", "clk") == _state(_run(source, "reference"), "memout", "clk")
    assert native.read("memout").val == 19


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_write_only_timed_memory_access_retains_full_sync():
    source = """module timed_mem_write(output reg clk, output [7:0] memout);
        reg [7:0] mem [0:31];
        assign memout=mem[0];
        initial begin clk=0; mem[0]=0; end
        always #5 begin mem[0]=7; clk=~clk; end
    endmodule"""
    native = _run(source, "vm-fast")
    assert all(not names.skip_memory_sync for names in native._sched._coro_sync_names.values())
    assert _state(native, "memout", "clk") == _state(_run(source, "vm"), "memout", "clk")
    assert _state(native, "memout", "clk") == _state(_run(source, "reference"), "memout", "clk")
    assert native.read("memout").val == 7


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_write_only_timed_signal_is_in_targeted_sync_set():
    source = """module timed_signal(output reg [7:0] q);
        reg [7:0] mem [0:31];
        initial q=0;
        always #5 q=7;
    endmodule"""
    native = _run(source, "vm-fast")
    assert any("q" in names for names in native._sched._coro_sync_names.values())
    assert native.read("q").val == 7
    assert _state(native, "q") == _state(_run(source, "vm"), "q")
    assert _state(native, "q") == _state(_run(source, "reference"), "q")


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_timed_system_task_uses_full_sync():
    source = """module timed_task(output reg clk);
        reg [7:0] mem [0:31];
        initial clk=0;
        always #5 begin $display("tick"); clk=~clk; end
    endmodule"""
    native = _run(source, "vm-fast", max_time=15)
    assert list(native._sched._coro_sync_names.values()) == [None]
    assert _state(native, "clk") == _state(_run(source, "reference", max_time=15), "clk")


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_timed_initial_memory_write_still_syncs():
    source = """module timed_initial(output reg clk, output reg [7:0] q);
        reg [7:0] mem [0:31];
        initial begin clk=0; q=0; mem[0]=3; #20 mem[0]=7; end
        always #5 clk=~clk;
        always @(posedge clk) q<=mem[0];
    endmodule"""
    native = _run(source, "vm-fast")
    assert _state(native, "q", "clk") == _state(_run(source, "vm"), "q", "clk")
    assert _state(native, "q", "clk") == _state(_run(source, "reference"), "q", "clk")
    assert native.read("q").val == 7
