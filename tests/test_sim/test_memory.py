"""Tests for memory array support across all simulation engines.

Covers:
  - Memory array registration during elaboration
  - Memory element read (BitSelect on memory)
  - Memory element write (blocking and non-blocking)
  - $readmemh / $readmemb system tasks
  - Out-of-bounds access returns x
  - Dimension preservation in hierarchy flattening
"""

import shutil

import pytest

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Identifier,
    Literal,
    Range,
    RangeSelect,
    StringLiteral,
)
from veriforge.model.instances import Instance, PortConnection
from veriforge.model.nets import Net, NetKind
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    NonblockingAssign,
    SeqBlock,
    SensitivityEdge,
    SystemTaskCall,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.sim.scheduler import Scheduler
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines():
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        try:
            import Cython  # noqa: F401, PLC0415

            engines.append("compiled")
        except ImportError:
            pass
    return engines


ENGINES = _engines()


# ── Helpers ──────────────────────────────────────────────────────────


def _W(hi, lo=0):
    """Shorthand for Range(Literal(hi), Literal(lo))."""
    return Range(Literal(hi, width=32), Literal(lo, width=32))


def _make_mem_module() -> Module:
    """Module with reg [7:0] mem [0:3], addr input, out output.

    always @(*) out = mem[addr];
    """
    m = Module(
        "mem_test",
        ports=[
            Port("addr", PortDirection.INPUT, width=_W(1)),
            Port("out", PortDirection.OUTPUT, width=_W(7)),
        ],
        nets=[
            Net("addr", NetKind.WIRE, width=_W(1)),
        ],
        variables=[
            Variable("out", VariableKind.REG, width=_W(7)),
            Variable("mem", VariableKind.REG, width=_W(7), dimensions=[_W(0, 3)]),
        ],
    )
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("out"),
                BitSelect(Identifier("mem"), Identifier("addr")),
            ),
        ),
    )
    return m


def _make_mem_write_module() -> Module:
    """Module that writes to mem[addr] = data in an initial block, reads via combo."""
    m = Module(
        "mem_write_test",
        ports=[
            Port("addr", PortDirection.INPUT, width=_W(1)),
            Port("data", PortDirection.INPUT, width=_W(7)),
            Port("out", PortDirection.OUTPUT, width=_W(7)),
        ],
        nets=[
            Net("addr", NetKind.WIRE, width=_W(1)),
            Net("data", NetKind.WIRE, width=_W(7)),
        ],
        variables=[
            Variable("out", VariableKind.REG, width=_W(7)),
            Variable("mem", VariableKind.REG, width=_W(7), dimensions=[_W(0, 3)]),
        ],
    )
    # initial begin mem[0]=8'hAA; mem[1]=8'hBB; mem[2]=8'hCC; mem[3]=8'hDD; end
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(BitSelect(Identifier("mem"), Literal(0, width=32)), Literal(0xAA, width=8)),
                    BlockingAssign(BitSelect(Identifier("mem"), Literal(1, width=32)), Literal(0xBB, width=8)),
                    BlockingAssign(BitSelect(Identifier("mem"), Literal(2, width=32)), Literal(0xCC, width=8)),
                    BlockingAssign(BitSelect(Identifier("mem"), Literal(3, width=32)), Literal(0xDD, width=8)),
                ]
            )
        )
    )
    # always @(*) out = mem[addr];
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("out"),
                BitSelect(Identifier("mem"), Identifier("addr")),
            ),
        ),
    )
    return m


def _make_mem_range_write_native_module() -> Module:
    """Module with native blocking byte-lane writes to mem[0] in an initial block."""
    m = Module(
        "mem_range_native",
        ports=[],
        nets=[],
        variables=[
            Variable("out", VariableKind.REG, width=_W(31)),
            Variable("mem", VariableKind.REG, width=_W(31), dimensions=[_W(0, 3)]),
        ],
    )
    mem0 = BitSelect(Identifier("mem"), Literal(0, width=32))
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(
                        RangeSelect(mem0, Literal(7, width=32), Literal(0, width=32)),
                        Literal(0x11, width=8),
                    ),
                    BlockingAssign(
                        RangeSelect(mem0, Literal(15, width=32), Literal(8, width=32)),
                        Literal(0x22, width=8),
                    ),
                    BlockingAssign(
                        RangeSelect(mem0, Literal(23, width=32), Literal(16, width=32)),
                        Literal(0x33, width=8),
                    ),
                    BlockingAssign(
                        RangeSelect(mem0, Literal(31, width=32), Literal(24, width=32)),
                        Literal(0x44, width=8),
                    ),
                    BlockingAssign(Identifier("out"), BitSelect(Identifier("mem"), Literal(0, width=32))),
                ]
            )
        )
    )
    return m


def _make_nonzero_base_mem_select_module() -> Module:
    """Module that reads and writes memory elements declared with a non-zero packed LSB."""
    m = Module(
        "mem_nonzero_base_select",
        ports=[],
        nets=[],
        variables=[
            Variable("out_bit", VariableKind.REG, width=_W(0)),
            Variable("out_low", VariableKind.REG, width=_W(7)),
            Variable("out_high", VariableKind.REG, width=_W(7)),
            Variable("mem", VariableKind.REG, width=_W(33, 2), dimensions=[_W(0, 1)]),
        ],
    )
    mem0 = BitSelect(Identifier("mem"), Literal(0, width=32))
    m.initial_blocks.append(
        InitialBlock(
            SeqBlock(
                [
                    BlockingAssign(BitSelect(mem0, Literal(2, width=32)), Literal(1, width=1)),
                    BlockingAssign(
                        RangeSelect(mem0, Literal(9, width=32), Literal(2, width=32)),
                        Literal(0xA5, width=8),
                    ),
                    BlockingAssign(
                        RangeSelect(mem0, Literal(33, width=32), Literal(26, width=32)),
                        Literal(0xD2, width=8),
                    ),
                ]
            )
        )
    )
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=SeqBlock(
                [
                    BlockingAssign(Identifier("out_bit"), BitSelect(mem0, Literal(2, width=32))),
                    BlockingAssign(
                        Identifier("out_low"),
                        RangeSelect(mem0, Literal(9, width=32), Literal(2, width=32)),
                    ),
                    BlockingAssign(
                        Identifier("out_high"),
                        RangeSelect(mem0, Literal(33, width=32), Literal(26, width=32)),
                    ),
                ]
            ),
        )
    )
    return m


def _make_readmemh_module(filename: str) -> Module:
    """Module with $readmemh in initial block."""
    m = Module(
        "readmemh_test",
        ports=[
            Port("addr", PortDirection.INPUT, width=_W(1)),
            Port("out", PortDirection.OUTPUT, width=_W(7)),
        ],
        nets=[
            Net("addr", NetKind.WIRE, width=_W(1)),
        ],
        variables=[
            Variable("out", VariableKind.REG, width=_W(7)),
            Variable("mem", VariableKind.REG, width=_W(7), dimensions=[_W(0, 3)]),
        ],
    )
    m.initial_blocks.append(
        InitialBlock(
            SystemTaskCall("$readmemh", [StringLiteral(filename), Identifier("mem")]),
        )
    )
    m.always_blocks.append(
        AlwaysBlock(
            sensitivity_type=SensitivityType.COMBINATIONAL,
            body=BlockingAssign(
                Identifier("out"),
                BitSelect(Identifier("mem"), Identifier("addr")),
            ),
        ),
    )
    return m


# ── Memory Registration ─────────────────────────────────────────────


class TestMemoryRegistration:
    """Memory arrays are registered during elaboration."""

    def test_memory_registered(self):
        """Variables with dimensions are registered as memories."""
        sched = Scheduler()
        sched.elaborate(_make_mem_module())
        assert "mem" in sched.ctx._memory_names
        assert "mem" in sched.ctx._memories

    def test_memory_depth(self):
        """Memory has correct depth (4 elements for [0:3])."""
        sched = Scheduler()
        sched.elaborate(_make_mem_module())
        mem_data, elem_w = sched.ctx._memories["mem"]
        assert len(mem_data) == 4
        assert elem_w == 8

    def test_memory_initialized_to_x(self):
        """Memory elements start as x."""
        sched = Scheduler()
        sched.elaborate(_make_mem_module())
        mem_data, _ = sched.ctx._memories["mem"]
        for elem in mem_data:
            assert not elem.is_defined

    def test_non_memory_not_registered(self):
        """Variables without dimensions are NOT registered as memories."""
        sched = Scheduler()
        sched.elaborate(_make_mem_module())
        assert "out" not in sched.ctx._memory_names
        assert "out" in sched.ctx._signals


# ── Memory Read ──────────────────────────────────────────────────────


class TestMemoryRead:
    """Reading memory elements via BitSelect."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_read_initialized_memory(self, engine):
        """Can read memory elements after blocking write."""
        m = _make_mem_write_module()
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("addr", Value(0, width=2)), max_time=0)
        assert sim.read("out") == 0xAA

    @pytest.mark.parametrize("engine", ENGINES)
    def test_read_all_elements(self, engine):
        """Each memory element holds its own value."""
        m = _make_mem_write_module()
        expected = {0: 0xAA, 1: 0xBB, 2: 0xCC, 3: 0xDD}
        for addr, exp_val in expected.items():
            sim = Simulator(m, engine=engine)
            sim.run(lambda s, a=addr: s.drive("addr", Value(a, width=2)), max_time=0)
            assert sim.read("out") == exp_val, f"mem[{addr}] expected {exp_val:#x}"


# ── Memory Write ─────────────────────────────────────────────────────


class TestMemoryWrite:
    """Writing to memory elements via BitSelect."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_blocking_write(self, engine):
        """Blocking assign to mem[i] updates the element."""
        m = _make_mem_write_module()
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("addr", Value(2, width=2)), max_time=0)
        assert sim.read("out") == 0xCC

    def test_nba_write(self):
        """Non-blocking assign to mem[i] updates after NBA phase (reference only)."""
        # NBA-to-memory propagation with combo readback is reference-engine only
        m = Module(
            "nba_mem_test",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("out", PortDirection.OUTPUT, width=_W(7)),
            ],
            nets=[Net("clk", NetKind.WIRE)],
            variables=[
                Variable("out", VariableKind.REG, width=_W(7)),
                Variable("mem", VariableKind.REG, width=_W(7), dimensions=[_W(0, 3)]),
            ],
        )
        # initial begin mem[0] <= 8'h42; end
        m.initial_blocks.append(
            InitialBlock(
                NonblockingAssign(
                    BitSelect(Identifier("mem"), Literal(0, width=32)),
                    Literal(0x42, width=8),
                ),
            )
        )
        # always @(*) out = mem[0];
        m.always_blocks.append(
            AlwaysBlock(
                sensitivity_type=SensitivityType.COMBINATIONAL,
                body=BlockingAssign(
                    Identifier("out"),
                    BitSelect(Identifier("mem"), Literal(0, width=32)),
                ),
            ),
        )
        sim = Simulator(m, engine="reference")
        sim.run(lambda s: None, max_time=0)
        assert sim.read("out") == 0x42


# ── $readmemh / $readmemb ───────────────────────────────────────────


class TestReadmem:
    """$readmemh and $readmemb system tasks."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_loads_data(self, tmp_path, engine):
        """$readmemh loads hex values into memory."""
        hex_file = tmp_path / "data.hex"
        hex_file.write_text("0A\n14\n1E\n28\n")
        m = _make_readmemh_module(str(hex_file))
        expected = {0: 0x0A, 1: 0x14, 2: 0x1E, 3: 0x28}
        for addr, exp_val in expected.items():
            sim = Simulator(m, engine=engine)
            sim.run(lambda s, a=addr: s.drive("addr", Value(a, width=2)), max_time=0)
            assert sim.read("out") == exp_val, f"mem[{addr}] expected {exp_val:#x}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_with_address_spec(self, tmp_path, engine):
        """$readmemh handles @addr address specifications."""
        hex_file = tmp_path / "data.hex"
        hex_file.write_text("@02\nFF\nEE\n")
        m = _make_readmemh_module(str(hex_file))
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("addr", Value(2, width=2)), max_time=0)
        assert sim.read("out") == 0xFF

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_with_comments(self, tmp_path, engine):
        """$readmemh skips comment lines."""
        hex_file = tmp_path / "data.hex"
        hex_file.write_text("// header comment\n0A\n// middle\n14\n1E\n28\n")
        m = _make_readmemh_module(str(hex_file))
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("addr", Value(0, width=2)), max_time=0)
        assert sim.read("out") == 0x0A

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemb(self, tmp_path, engine):
        """$readmemb loads binary values into memory."""
        bin_file = tmp_path / "data.bin"
        bin_file.write_text("00001010\n00010100\n00011110\n00101000\n")
        m = Module(
            "readmemb_test",
            ports=[
                Port("addr", PortDirection.INPUT, width=_W(1)),
                Port("out", PortDirection.OUTPUT, width=_W(7)),
            ],
            nets=[Net("addr", NetKind.WIRE, width=_W(1))],
            variables=[
                Variable("out", VariableKind.REG, width=_W(7)),
                Variable("mem", VariableKind.REG, width=_W(7), dimensions=[_W(0, 3)]),
            ],
        )
        m.initial_blocks.append(
            InitialBlock(
                SystemTaskCall("$readmemb", [StringLiteral(str(bin_file)), Identifier("mem")]),
            )
        )
        m.always_blocks.append(
            AlwaysBlock(
                sensitivity_type=SensitivityType.COMBINATIONAL,
                body=BlockingAssign(
                    Identifier("out"),
                    BitSelect(Identifier("mem"), Identifier("addr")),
                ),
            ),
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: s.drive("addr", Value(0, width=2)), max_time=0)
        assert sim.read("out") == 0x0A

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_missing_file(self, engine):
        """$readmemh with missing file raises FileNotFoundError."""
        m = _make_readmemh_module("nonexistent_file.hex")
        sim = Simulator(m, engine=engine)
        with pytest.raises(FileNotFoundError):
            sim.run(lambda s: None, max_time=0)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_readmemh_multiple_tokens_per_line(self, tmp_path, engine):
        """$readmemh handles multiple space-separated tokens per line."""
        hex_file = tmp_path / "data.hex"
        hex_file.write_text("0A 14\n1E 28\n")
        m = _make_readmemh_module(str(hex_file))
        expected = {0: 0x0A, 1: 0x14, 2: 0x1E, 3: 0x28}
        for addr, exp_val in expected.items():
            sim = Simulator(m, engine=engine)
            sim.run(lambda s, a=addr: s.drive("addr", Value(a, width=2)), max_time=0)
            assert sim.read("out") == exp_val


# ── Out-of-bounds ────────────────────────────────────────────────────


class TestMemoryBounds:
    """Out-of-bounds memory access returns x."""

    def test_read_out_of_bounds(self):
        """Reading beyond memory depth returns x."""
        from veriforge.sim.evaluator import EvalContext, ExpressionEvaluator

        ctx = EvalContext()
        ctx._memories["mem"] = ([Value(0xAA, width=8)], 8)
        ctx._memory_names.add("mem")
        ev = ExpressionEvaluator()
        result = ev.eval(BitSelect(Identifier("mem"), Literal(5, width=32)), ctx)
        assert not result.is_defined
        assert result.width == 8


# ── Dimension Preservation ───────────────────────────────────────────


class TestDimensionPreservation:
    """Dimensions are preserved during hierarchy flattening."""

    def test_prefixed_variable_keeps_dimensions(self):
        """_create_prefixed_signals preserves dimensions on variables."""
        from veriforge.sim.elaborate import _create_prefixed_signals

        sub = Module(
            "sub",
            variables=[
                Variable("mem", VariableKind.REG, width=_W(7), dimensions=[_W(0, 255)]),
            ],
        )
        flat = Module("flat")
        _create_prefixed_signals(flat, sub, "inst")
        assert len(flat.variables) == 1
        v = flat.variables[0]
        assert v.name == "inst.mem"
        assert len(v.dimensions) == 1


# ── $dumpfile / $dumpvars ────────────────────────────────────────────


class TestDumpVcd:
    """$dumpfile / $dumpvars produce VCD output across all engines."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_dumpfile_dumpvars_creates_file(self, engine, tmp_path):
        """$dumpfile + $dumpvars generate a VCD file with signal traces."""
        vcd_path = str(tmp_path / "out.vcd")
        m = Module(
            "vcd_test",
            ports=[
                Port("clk", PortDirection.INPUT),
                Port("out", PortDirection.OUTPUT, width=_W(7)),
            ],
            nets=[Net("clk", NetKind.WIRE)],
            variables=[Variable("out", VariableKind.REG, width=_W(7))],
        )
        m.initial_blocks.append(
            InitialBlock(
                SeqBlock(
                    [
                        SystemTaskCall("$dumpfile", [StringLiteral(vcd_path)]),
                        SystemTaskCall("$dumpvars", []),
                        BlockingAssign(Identifier("out"), Literal(42, width=8)),
                    ]
                )
            )
        )
        sim = Simulator(m, engine=engine)
        sim.run(lambda s: None, max_time=0)
        import os

        assert os.path.exists(vcd_path)
        content = open(vcd_path).read()
        assert "$timescale" in content
        assert "$enddefinitions" in content
        assert "$dumpvars" in content

    @pytest.mark.parametrize("engine", ENGINES)
    def test_dumpvars_default_filename(self, engine, tmp_path):
        """$dumpvars without $dumpfile uses 'dump.vcd' as default."""
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            m = Module("vcd_default_test")
            m.initial_blocks.append(InitialBlock(SystemTaskCall("$dumpvars", [])))
            sim = Simulator(m, engine=engine)
            sim.run(lambda s: None, max_time=0)
            assert os.path.exists(tmp_path / "dump.vcd")
        finally:
            os.chdir(old_cwd)


# ── Memory partial-range writes ──────────────────────────────────────


class TestMemoryRangeWrite:
    """Memory byte-lane writes: memory[addr][msb:lsb] <= value."""

    _BLOCKING_VERILOG = """\
module mem_range_blocking;
    reg [31:0] mem [0:3];
    reg [31:0] out;

    initial begin
        mem[0][ 7: 0] = 8'h11;
        mem[0][15: 8] = 8'h22;
        mem[0][23:16] = 8'h33;
        mem[0][31:24] = 8'h44;
        out = mem[0];
        $display("BLOCKING out=%0h", out);
        $finish;
    end
endmodule
"""

    _NBA_VERILOG = """\
module mem_range_nba;
    reg clk = 0;
    reg [31:0] mem [0:3];
    reg [31:0] out;
    reg [31:0] wdata;
    reg done = 0;

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (!done) begin
            wdata = 32'hAABBCCDD;
            mem[1][ 7: 0] <= wdata[ 7: 0];
            mem[1][15: 8] <= wdata[15: 8];
            mem[1][23:16] <= wdata[23:16];
            mem[1][31:24] <= wdata[31:24];
            done <= 1;
        end
    end

    always @(posedge clk) begin
        if (done)
            $display("NBA out=%0h", mem[1]);
    end
endmodule
"""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_byte_lane_blocking_native(self, engine):
        """Native blocking byte-lane writes to memory elements work across engines."""
        sim = Simulator(_make_mem_range_write_native_module(), engine=engine)
        sim.run(max_time=0)
        assert sim.read("out") == Value(0x44332211, width=32)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_nonzero_packed_base_selects(self, engine):
        """Memory element bit/range selects honor the declared packed LSB."""
        sim = Simulator(_make_nonzero_base_mem_select_module(), engine=engine)
        sim.run(max_time=0)
        assert sim.read("out_bit") == Value(1, width=1)
        assert sim.read("out_low") == Value(0xA5, width=8)
        assert sim.read("out_high") == Value(0xD2, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_byte_lane_blocking(self, engine, tmp_path):
        """Blocking byte-lane writes to memory elements."""
        from veriforge.project import parse_files

        src = tmp_path / "test.v"
        src.write_text(self._BLOCKING_VERILOG)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("mem_range_blocking")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=100)
        lines = sim.display_output
        assert any("BLOCKING out=44332211" in l for l in lines), f"Blocking byte-lane write failed: {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_memory_byte_lane_nba(self, engine, tmp_path):
        """Non-blocking byte-lane writes to memory elements."""
        from veriforge.project import parse_files

        src = tmp_path / "test.v"
        src.write_text(self._NBA_VERILOG)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("mem_range_nba")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=200)
        lines = sim.display_output
        assert any("NBA out=aabbccdd" in l for l in lines), f"NBA byte-lane write failed: {lines}"


class TestConcatLhsNba:
    """Concat LHS NBA: multiple sub-parts targeting the same signal must chain."""

    _VERILOG = """\
module concat_lhs_nba;
    reg clk = 0;
    reg [31:0] imm;
    reg done = 0;

    always #5 clk = ~clk;

    // 4 sub-parts target 'imm' via concat NBA (total 32 bits)
    always @(posedge clk) begin
        if (!done) begin
            {imm[31:24], imm[23:16], imm[15:8], imm[7:0]} <= 32'hDEADBEEF;
            done <= 1;
        end
    end

    always @(posedge clk) begin
        if (done)
            $display("CONCAT_NBA imm=%08h", imm);
    end
endmodule
"""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_lhs_nba(self, engine, tmp_path):
        """Concat LHS NBA sub-parts should chain, not clobber each other."""
        from veriforge.project import parse_files

        src = tmp_path / "test.v"
        src.write_text(self._VERILOG)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("concat_lhs_nba")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=200)
        lines = sim.display_output
        assert any("concat_nba imm=deadbeef" in l.lower() for l in lines), f"Concat LHS NBA failed ({engine}): {lines}"

    _VERILOG_MIXED = """\
module concat_lhs_nba_mixed;
    reg clk = 0;
    reg [7:0] sig;
    reg done = 0;

    always #5 clk = ~clk;

    // Mix bit-selects and range-selects in concat LHS NBA
    always @(posedge clk) begin
        if (!done) begin
            {sig[7], sig[6:4], sig[3], sig[2:0]} <= 8'hA5;
            done <= 1;
        end
    end

    always @(posedge clk) begin
        if (done)
            $display("MIXED sig=%02h", sig);
    end
endmodule
"""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_concat_lhs_nba_mixed(self, engine, tmp_path):
        """Concat LHS NBA with mixed bit-select and range-select sub-parts."""
        from veriforge.project import parse_files

        src = tmp_path / "test.v"
        src.write_text(self._VERILOG_MIXED)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("concat_lhs_nba_mixed")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=200)
        lines = sim.display_output
        assert any("mixed sig=a5" in l.lower() for l in lines), f"Concat LHS NBA mixed failed ({engine}): {lines}"


class TestStructArrayFieldReads:
    """Packed-struct procedural field reads from local unpacked arrays."""

    _VERILOG = """\
module struct_array_field_reads;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    item_t items [1:0];
    logic [7:0] out_data;
    logic [3:0] out_tag;

    initial begin
        items[0] = 12'hA53;
        items[1] = 12'h5AC;
        out_data = items[0].data;
        out_tag = items[1].tag;
        #1;
        $display("STRUCT_ARRAY out_data=%0h out_tag=%0h", out_data, out_tag);
    end
    endmodule
"""

    _VERILOG_COMBO = """\
module struct_array_field_reads_combo;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    item_t items [1:0];
    logic [7:0] out_data;
    logic [3:0] out_tag;

    always @(*) begin
        out_data = items[0].data;
        out_tag = items[1].tag;
    end

    initial begin
        items[0] = 12'hA53;
        items[1] = 12'h5AC;
        #1;
        $display("STRUCT_ARRAY_COMBO out_data=%0h out_tag=%0h", out_data, out_tag);
    end
endmodule
"""

    _VERILOG_DYNAMIC = """\
module struct_array_field_reads_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    item_t items [1:0];
    logic idx;
    logic [7:0] out_data;
    logic [3:0] out_tag;

    initial begin
        items[0] = 12'hA53;
        items[1] = 12'h5AC;
        idx = 1'b1;
        out_data = items[idx].data;
        idx = 1'b0;
        out_tag = items[idx].tag;
        #1;
        $display("STRUCT_ARRAY_DYNAMIC out_data=%0h out_tag=%0h", out_data, out_tag);
    end
endmodule
"""

    _VERILOG_DYNAMIC_COMBO = """\
module struct_array_field_reads_dynamic_combo;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    item_t items [1:0];
    logic idx;
    logic [7:0] out_data;
    logic [3:0] out_tag;

    always @(*) begin
        out_data = items[idx].data;
        out_tag = items[idx].tag;
    end

    initial begin
        items[0] = 12'hA53;
        items[1] = 12'h5AC;
        idx = 1'b1;
        #1;
        $display("STRUCT_ARRAY_DYNAMIC_COMBO1 out_data=%0h out_tag=%0h", out_data, out_tag);
        idx = 1'b0;
        #1;
        $display("STRUCT_ARRAY_DYNAMIC_COMBO2 out_data=%0h out_tag=%0h", out_data, out_tag);
    end
endmodule
"""

    _VERILOG_FIELD_WRITES = """\
module struct_array_field_writes;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    item_t items [1:0];
    logic [11:0] out0;
    logic [11:0] out1;

    initial begin
        items[0] = 12'h000;
        items[1] = 12'h000;
        items[0].data = 8'hA5;
        items[0].tag = 4'h3;
        items[1].data = 8'h5A;
        items[1].tag = 4'hC;
        out0 = items[0];
        out1 = items[1];
        #1;
        $display("STRUCT_ARRAY_FIELD_WRITE out0=%0h out1=%0h", out0, out1);
    end
endmodule
"""

    _VERILOG_DYNAMIC_FIELD_WRITES = """\
module struct_array_field_writes_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } item_t;

    item_t items [1:0];
    integer idx;
    logic [11:0] out0;
    logic [11:0] out1;

    initial begin
        items[0] = 12'h000;
        items[1] = 12'h000;
        idx = 1;
        items[idx].data = 8'h5A;
        idx = 0;
        items[idx].tag = 4'h3;
        items[idx].data = 8'hA5;
        idx = 1;
        items[idx].tag = 4'hC;
        out0 = items[0];
        out1 = items[1];
        #1;
        $display("STRUCT_ARRAY_DYNAMIC_FIELD_WRITE out0=%0h out1=%0h", out0, out1);
    end
endmodule
"""

    _VERILOG_NESTED_DYNAMIC_FIELD_WRITES = """\
module struct_array_nested_field_writes_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    integer idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        idx = 1;
        items[idx].payload.data = 8'h5A;
        items[idx].kind = 2'h2;
        idx = 0;
        items[idx].payload.tag = 4'h3;
        items[idx].payload.data = 8'hA5;
        items[idx].kind = 2'h1;
        idx = 1;
        items[idx].payload.tag = 4'hC;
        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_DYNAMIC out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_DYNAMIC_COMBO = """\
module struct_array_nested_field_reads_dynamic_combo;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    logic idx;
    logic [7:0] out_data;
    logic [3:0] out_tag;
    logic [1:0] out_kind;

    always @(*) begin
        out_data = items[idx].payload.data;
        out_tag = items[idx].payload.tag;
        out_kind = items[idx].kind;
    end

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;
        idx = 1'b1;
        #1;
        $display("STRUCT_ARRAY_NESTED_COMBO1 out_data=%0h out_tag=%0h out_kind=%0h", out_data, out_tag, out_kind);
        idx = 1'b0;
        #1;
        $display("STRUCT_ARRAY_NESTED_COMBO2 out_data=%0h out_tag=%0h out_kind=%0h", out_data, out_tag, out_kind);
    end
endmodule
"""

    _VERILOG_NESTED_DYNAMIC_NBA = """\
module struct_array_nested_field_writes_dynamic_nba;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    integer idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        idx = 1;
        items[idx].payload.data <= 8'h5A;
        items[idx].kind <= 2'h2;
        idx = 0;
        items[idx].payload.tag <= 4'h3;
        items[idx].payload.data <= 8'hA5;
        items[idx].kind <= 2'h1;
        idx = 1;
        items[idx].payload.tag <= 4'hC;
        #1;
        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_NBA out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_PAYLOAD_WRITE_DYNAMIC = """\
module struct_array_nested_payload_write_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    payload_t payload;
    integer idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].kind = 2'h1;
        items[1].kind = 2'h2;

        payload.data = 8'h5A;
        payload.tag = 4'hC;
        idx = 1;
        items[idx].payload = payload;

        payload.data = 8'hA5;
        payload.tag = 4'h3;
        idx = 0;
        items[idx].payload = payload;

        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_WRITE out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_PAYLOAD_WRITE_DYNAMIC_NBA = """\
module struct_array_nested_payload_write_dynamic_nba;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    payload_t payload;
    integer idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].kind = 2'h1;
        items[1].kind = 2'h2;

        payload.data = 8'h5A;
        payload.tag = 4'hC;
        idx = 1;
        items[idx].payload <= payload;

        payload.data = 8'hA5;
        payload.tag = 4'h3;
        idx = 0;
        items[idx].payload <= payload;

        #1;
        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_WRITE_NBA out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_PAYLOAD_READ_DYNAMIC = """\
module struct_array_nested_payload_read_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    payload_t out_payload;
    integer idx;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;

        idx = 1;
        out_payload = items[idx].payload;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_READ1 out_data=%0h out_tag=%0h",
            out_payload.data,
            out_payload.tag
        );

        idx = 0;
        out_payload = items[idx].payload;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_READ2 out_data=%0h out_tag=%0h",
            out_payload.data,
            out_payload.tag
        );
    end
endmodule
"""

    _VERILOG_NESTED_PAYLOAD_READ_DYNAMIC_COMBO = """\
module struct_array_nested_payload_read_dynamic_combo;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    payload_t out_payload;
    logic idx;

    always @(*) begin
        out_payload = items[idx].payload;
    end

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;

        idx = 1'b1;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_COMBO1 out_data=%0h out_tag=%0h",
            out_payload.data,
            out_payload.tag
        );

        idx = 1'b0;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_COMBO2 out_data=%0h out_tag=%0h",
            out_payload.data,
            out_payload.tag
        );
    end
endmodule
"""

    _VERILOG_NESTED_PAYLOAD_COPY_DYNAMIC = """\
module struct_array_nested_payload_copy_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    integer src_idx;
    integer dst_idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;

        src_idx = 1;
        dst_idx = 0;
        items[dst_idx].payload = items[src_idx].payload;

        items[1].payload.data = 8'hC3;
        items[1].payload.tag = 4'h6;

        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_COPY out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_PAYLOAD_COPY_DYNAMIC_NBA = """\
module struct_array_nested_payload_copy_dynamic_nba;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    integer src_idx;
    integer dst_idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;

        src_idx = 1;
        dst_idx = 0;
        items[dst_idx].payload <= items[src_idx].payload;

        items[1].payload.data = 8'hC3;
        items[1].payload.tag = 4'h6;

        #1;
        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_PAYLOAD_COPY_NBA out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_ELEMENT_SWAP_NBA = """\
module struct_array_nested_element_swap_dynamic_nba;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    integer src_idx;
    integer dst_idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;
        src_idx = 1;
        dst_idx = 0;
        items[dst_idx] <= items[src_idx];
        src_idx = 0;
        dst_idx = 1;
        items[dst_idx] <= items[src_idx];
        #1;
        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_SWAP_NBA out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_ELEMENT_COPY_DYNAMIC = """\
module struct_array_nested_element_copy_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    integer src_idx;
    integer dst_idx;
    logic [7:0] out0_data;
    logic [3:0] out0_tag;
    logic [1:0] out0_kind;
    logic [7:0] out1_data;
    logic [3:0] out1_tag;
    logic [1:0] out1_kind;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;
        src_idx = 1;
        dst_idx = 0;
        items[dst_idx] = items[src_idx];
        items[1].payload.data = 8'hA5;
        items[1].payload.tag = 4'h3;
        items[1].kind = 2'h1;
        out0_data = items[0].payload.data;
        out0_tag = items[0].payload.tag;
        out0_kind = items[0].kind;
        out1_data = items[1].payload.data;
        out1_tag = items[1].payload.tag;
        out1_kind = items[1].kind;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_COPY out0_data=%0h out0_tag=%0h out0_kind=%0h out1_data=%0h out1_tag=%0h out1_kind=%0h",
            out0_data,
            out0_tag,
            out0_kind,
            out1_data,
            out1_tag,
            out1_kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_ELEMENT_READ_DYNAMIC = """\
module struct_array_nested_element_read_dynamic;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    item_t out_item;
    integer idx;

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;
        idx = 1;
        out_item = items[idx];
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_READ1 out_data=%0h out_tag=%0h out_kind=%0h",
            out_item.payload.data,
            out_item.payload.tag,
            out_item.kind
        );
        idx = 0;
        out_item = items[idx];
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_READ2 out_data=%0h out_tag=%0h out_kind=%0h",
            out_item.payload.data,
            out_item.payload.tag,
            out_item.kind
        );
    end
endmodule
"""

    _VERILOG_NESTED_ELEMENT_READ_DYNAMIC_COMBO = """\
module struct_array_nested_element_read_dynamic_combo;
    typedef struct packed {
        logic [7:0] data;
        logic [3:0] tag;
    } payload_t;

    typedef struct packed {
        payload_t payload;
        logic [1:0] kind;
    } item_t;

    item_t items [1:0];
    item_t out_item;
    logic idx;

    always @(*) begin
        out_item = items[idx];
    end

    initial begin
        items[0] = '0;
        items[1] = '0;
        items[0].payload.data = 8'hA5;
        items[0].payload.tag = 4'h3;
        items[0].kind = 2'h1;
        items[1].payload.data = 8'h5A;
        items[1].payload.tag = 4'hC;
        items[1].kind = 2'h2;
        idx = 1'b1;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_READ_COMBO1 out_data=%0h out_tag=%0h out_kind=%0h",
            out_item.payload.data,
            out_item.payload.tag,
            out_item.kind
        );
        idx = 1'b0;
        #1;
        $display(
            "STRUCT_ARRAY_NESTED_READ_COMBO2 out_data=%0h out_tag=%0h out_kind=%0h",
            out_item.payload.data,
            out_item.payload.tag,
            out_item.kind
        );
    end
endmodule
"""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_field_reads(self, engine, tmp_path):
        """Packed-struct array element field reads should resolve across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test.sv"
        src.write_text(self._VERILOG)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_field_reads")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array out_data=a5 out_tag=c" in l.lower() for l in lines), (
            f"Struct array field read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_field_reads_combo(self, engine, tmp_path):
        """Packed-struct array element reads should also drive combinational logic."""
        from veriforge.project import parse_files

        src = tmp_path / "test_combo.sv"
        src.write_text(self._VERILOG_COMBO)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_field_reads_combo")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_combo out_data=a5 out_tag=c" in l.lower() for l in lines), (
            f"Struct array combo read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_field_reads_dynamic(self, engine, tmp_path):
        """Packed-struct array field reads should support a simple dynamic index."""
        from veriforge.project import parse_files

        src = tmp_path / "test_dynamic.sv"
        src.write_text(self._VERILOG_DYNAMIC)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_field_reads_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_dynamic out_data=5a out_tag=3" in l.lower() for l in lines), (
            f"Struct array dynamic read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_field_reads_dynamic_combo(self, engine, tmp_path):
        """Dynamic indexed packed-struct reads should re-fire combinationally when the index changes."""
        from veriforge.project import parse_files

        src = tmp_path / "test_dynamic_combo.sv"
        src.write_text(self._VERILOG_DYNAMIC_COMBO)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_field_reads_dynamic_combo")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_dynamic_combo1 out_data=5a out_tag=c" in l.lower() for l in lines), (
            f"Struct array dynamic combo first read failed ({engine}): {lines}"
        )
        assert any("struct_array_dynamic_combo2 out_data=a5 out_tag=3" in l.lower() for l in lines), (
            f"Struct array dynamic combo second read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_field_writes(self, engine, tmp_path):
        """Packed-struct array field writes should update memory-backed elements across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_field_writes.sv"
        src.write_text(self._VERILOG_FIELD_WRITES)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_field_writes")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_field_write out0=a53 out1=5ac" in l.lower() for l in lines), (
            f"Struct array field write failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_field_writes_dynamic(self, engine, tmp_path):
        """Dynamic indexed packed-struct field writes should update memory-backed elements across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_dynamic_field_writes.sv"
        src.write_text(self._VERILOG_DYNAMIC_FIELD_WRITES)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_field_writes_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_dynamic_field_write out0=a53 out1=5ac" in line.lower() for line in lines), (
            f"Struct array dynamic field write failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_field_writes_dynamic(self, engine, tmp_path):
        """Dynamic indexed nested packed-struct field writes should update memory-backed elements across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_dynamic_field_writes.sv"
        src.write_text(self._VERILOG_NESTED_DYNAMIC_FIELD_WRITES)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_field_writes_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_dynamic out0_data=a5 out0_tag=3 out0_kind=1 out1_data=5a out1_tag=c out1_kind=2"
            in line.lower()
            for line in lines
        ), f"Struct array nested dynamic field write failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_field_reads_dynamic_combo(self, engine, tmp_path):
        """Dynamic indexed nested packed-struct reads should re-fire combinationally across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_dynamic_combo.sv"
        src.write_text(self._VERILOG_NESTED_DYNAMIC_COMBO)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_field_reads_dynamic_combo")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_nested_combo1 out_data=5a out_tag=c out_kind=2" in line.lower() for line in lines), (
            f"Struct array nested dynamic combo first read failed ({engine}): {lines}"
        )
        assert any("struct_array_nested_combo2 out_data=a5 out_tag=3 out_kind=1" in line.lower() for line in lines), (
            f"Struct array nested dynamic combo second read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_field_writes_dynamic_nba(self, engine, tmp_path):
        """Dynamic indexed nested packed-struct NBA writes should accumulate across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_dynamic_nba.sv"
        src.write_text(self._VERILOG_NESTED_DYNAMIC_NBA)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_field_writes_dynamic_nba")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_nba out0_data=a5 out0_tag=3 out0_kind=1 out1_data=5a out1_tag=c out1_kind=2"
            in line.lower()
            for line in lines
        ), f"Struct array nested dynamic NBA write failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_payload_write_dynamic(self, engine, tmp_path):
        """Dynamic indexed nested sub-struct writes should update only the targeted payload slice."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_payload_write_dynamic.sv"
        src.write_text(self._VERILOG_NESTED_PAYLOAD_WRITE_DYNAMIC)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_payload_write_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_payload_write out0_data=a5 out0_tag=3 out0_kind=1 out1_data=5a out1_tag=c out1_kind=2"
            in line.lower()
            for line in lines
        ), f"Struct array nested payload write failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_payload_write_dynamic_nba(self, engine, tmp_path):
        """Dynamic indexed nested sub-struct NBA writes should preserve sibling fields and queued RHS values."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_payload_write_dynamic_nba.sv"
        src.write_text(self._VERILOG_NESTED_PAYLOAD_WRITE_DYNAMIC_NBA)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_payload_write_dynamic_nba")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_payload_write_nba out0_data=a5 out0_tag=3 out0_kind=1 out1_data=5a out1_tag=c out1_kind=2"
            in line.lower()
            for line in lines
        ), f"Struct array nested payload NBA write failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_payload_read_dynamic(self, engine, tmp_path):
        """Dynamic indexed nested sub-struct reads should preserve the full payload slice."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_payload_read_dynamic.sv"
        src.write_text(self._VERILOG_NESTED_PAYLOAD_READ_DYNAMIC)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_payload_read_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_nested_payload_read1 out_data=5a out_tag=c" in line.lower() for line in lines), (
            f"Struct array nested payload first read failed ({engine}): {lines}"
        )
        assert any("struct_array_nested_payload_read2 out_data=a5 out_tag=3" in line.lower() for line in lines), (
            f"Struct array nested payload second read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_payload_read_dynamic_combo(self, engine, tmp_path):
        """Dynamic indexed nested sub-struct combinational reads should re-fire across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_payload_read_dynamic_combo.sv"
        src.write_text(self._VERILOG_NESTED_PAYLOAD_READ_DYNAMIC_COMBO)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_payload_read_dynamic_combo")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_nested_payload_combo1 out_data=5a out_tag=c" in line.lower() for line in lines), (
            f"Struct array nested payload combo first read failed ({engine}): {lines}"
        )
        assert any("struct_array_nested_payload_combo2 out_data=a5 out_tag=3" in line.lower() for line in lines), (
            f"Struct array nested payload combo second read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_payload_copy_dynamic(self, engine, tmp_path):
        """Dynamic nested sub-struct copies should preserve sibling fields and copied payload values."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_payload_copy_dynamic.sv"
        src.write_text(self._VERILOG_NESTED_PAYLOAD_COPY_DYNAMIC)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_payload_copy_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_payload_copy out0_data=5a out0_tag=c out0_kind=1 out1_data=c3 out1_tag=6 out1_kind=2"
            in line.lower()
            for line in lines
        ), f"Struct array nested payload copy failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_payload_copy_dynamic_nba(self, engine, tmp_path):
        """Dynamic nested sub-struct NBA copies should preserve queued payload values and sibling fields."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_payload_copy_dynamic_nba.sv"
        src.write_text(self._VERILOG_NESTED_PAYLOAD_COPY_DYNAMIC_NBA)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_payload_copy_dynamic_nba")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_payload_copy_nba out0_data=5a out0_tag=c out0_kind=1 out1_data=c3 out1_tag=6 out1_kind=2"
            in line.lower()
            for line in lines
        ), f"Struct array nested payload NBA copy failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_element_swap_dynamic_nba(self, engine, tmp_path):
        """Dynamic whole-element NBA swaps in nested packed-struct arrays should preserve full packed values."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_element_swap_dynamic_nba.sv"
        src.write_text(self._VERILOG_NESTED_ELEMENT_SWAP_NBA)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_element_swap_dynamic_nba")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_swap_nba out0_data=5a out0_tag=c out0_kind=2 out1_data=a5 out1_tag=3 out1_kind=1"
            in line.lower()
            for line in lines
        ), f"Struct array nested dynamic NBA swap failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_element_copy_dynamic(self, engine, tmp_path):
        """Dynamic whole-element copies in nested packed-struct arrays should preserve copied values."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_element_copy_dynamic.sv"
        src.write_text(self._VERILOG_NESTED_ELEMENT_COPY_DYNAMIC)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_element_copy_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_copy out0_data=5a out0_tag=c out0_kind=2 out1_data=a5 out1_tag=3 out1_kind=1"
            in line.lower()
            for line in lines
        ), f"Struct array nested dynamic copy failed ({engine}): {lines}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_element_read_dynamic(self, engine, tmp_path):
        """Dynamic whole-element reads in nested packed-struct arrays should preserve the full packed value."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_element_read_dynamic.sv"
        src.write_text(self._VERILOG_NESTED_ELEMENT_READ_DYNAMIC)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_element_read_dynamic")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any("struct_array_nested_read1 out_data=5a out_tag=c out_kind=2" in line.lower() for line in lines), (
            f"Struct array nested dynamic whole-element first read failed ({engine}): {lines}"
        )
        assert any("struct_array_nested_read2 out_data=a5 out_tag=3 out_kind=1" in line.lower() for line in lines), (
            f"Struct array nested dynamic whole-element second read failed ({engine}): {lines}"
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_struct_array_nested_element_read_dynamic_combo(self, engine, tmp_path):
        """Dynamic combinational whole-element reads in nested packed-struct arrays should re-fire across engines."""
        from veriforge.project import parse_files

        src = tmp_path / "test_nested_element_read_dynamic_combo.sv"
        src.write_text(self._VERILOG_NESTED_ELEMENT_READ_DYNAMIC_COMBO)
        design = parse_files([str(src)], preprocess=True)
        top = design.get_module("struct_array_nested_element_read_dynamic_combo")
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=20)
        lines = sim.display_output
        assert any(
            "struct_array_nested_read_combo1 out_data=5a out_tag=c out_kind=2" in line.lower() for line in lines
        ), f"Struct array nested dynamic whole-element combo first read failed ({engine}): {lines}"
        assert any(
            "struct_array_nested_read_combo2 out_data=a5 out_tag=3 out_kind=1" in line.lower() for line in lines
        ), f"Struct array nested dynamic whole-element combo second read failed ({engine}): {lines}"


# ── Bit-select on instance-connected wire ────────────────────────────
#
# Regression for a bug where mem[port[N:0]] (RangeSelect as memory index)
# returned X in the compiled engine when the indexed signal was wired via
# a port connection rather than driven directly.  Root cause: the compiled
# engine's data snapshot (sv[]) was not refreshed with post-drive / post-CA
# values before seq process bodies ran.  Fixed in veriforge commit 627a245.
#
# Both standalone and flattened scenarios are tested across all engines so
# that any regression in NBA snapshot handling is caught immediately.


def _build_bram_dsl(depth: int = 4):
    """Build a simple BRAM module using the DSL (matches the real context_buffer pattern).

    The always block uses RangeSelect as the memory index:
      rd_data <= mem[rd_addr[addr_width-1:0]]
      mem[wr_addr[addr_width-1:0]] <= wr_data

    This is the exact pattern that triggered the compiled-engine NBA snapshot bug.
    """
    from veriforge.dsl import Module as DslModule, posedge  # noqa: PLC0415

    addr_width = (depth - 1).bit_length()
    m = DslModule("bram_dut")
    clk = m.input("clk")
    rst = m.input("rst")
    wr_en = m.input("wr_en")
    wr_addr = m.input("wr_addr", width=addr_width)
    wr_data = m.input("wr_data", width=32)
    rd_addr = m.input("rd_addr", width=addr_width)
    rd_data = m.output_reg("rd_data", width=32)
    mem = m.reg("mem", width=32, depth=depth)

    with m.always(posedge(clk)):
        with m.if_(rst):
            rd_data <<= 0
        with m.else_():
            # RangeSelect as memory index — the pattern that triggered the bug
            rd_data <<= mem[rd_addr[addr_width - 1 : 0]]
            with m.if_(wr_en):
                mem[wr_addr[addr_width - 1 : 0]] <<= wr_data

    return m.build()


def _build_bram_wrapper_dsl(depth: int = 4):
    """Wrapper that instantiates bram_dut 1:1, adding a hierarchy level."""
    from veriforge.dsl import Module as DslModule  # noqa: PLC0415

    addr_width = (depth - 1).bit_length()
    m = DslModule("bram_wrapper")
    clk = m.input("clk")
    rst = m.input("rst")
    wr_en = m.input("wr_en")
    wr_addr = m.input("wr_addr", width=addr_width)
    wr_data = m.input("wr_data", width=32)
    rd_addr = m.input("rd_addr", width=addr_width)
    rd_data = m.output("rd_data", width=32)

    m.instance(
        "bram_dut",
        "u_bram",
        ports={
            "clk": clk,
            "rst": rst,
            "wr_en": wr_en,
            "wr_addr": wr_addr,
            "wr_data": wr_data,
            "rd_addr": rd_addr,
            "rd_data": rd_data,
        },
    )
    return m.build()


def _bram_write_read(sim, wr_addr_val: int, wr_data_val: int, rd_addr_val: int):
    """Drive a clock-edge write followed by a clock-edge read; return rd_data."""

    def step():
        sim.drive("clk", 1)
        sim.settle()
        sim.drive("clk", 0)
        sim.settle()

    # Reset
    sim.drive("clk", 0)
    sim.drive("rst", 1)
    sim.drive("wr_en", 0)
    sim.drive("wr_addr", 0)
    sim.drive("wr_data", 0)
    sim.drive("rd_addr", 0)
    step()
    sim.drive("rst", 0)
    step()

    # Write
    sim.drive("wr_en", 1)
    sim.drive("wr_addr", wr_addr_val)
    sim.drive("wr_data", wr_data_val)
    sim.drive("rd_addr", 0)
    step()

    # Read back
    sim.drive("wr_en", 0)
    sim.drive("rd_addr", rd_addr_val)
    step()

    return sim.read("rd_data")


# ── load_memory / dump_memory / memory_names ─────────────────────────


_compiled_only = pytest.mark.skipif(
    "compiled" not in ENGINES,
    reason="compiled engine not available (no C compiler or Cython)",
)


@_compiled_only
class TestLoadDumpMemory:
    """Simulator.load_memory / dump_memory / memory_names (compiled engine only)."""

    def test_load_then_dump_roundtrip(self):
        """Values written via load_memory are readable via dump_memory."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        payload = [0x11, 0x22, 0x33, 0x44]
        sim.load_memory("mem", payload)
        result = sim.dump_memory("mem", 4)
        assert result == payload

    def test_load_affects_sim_read(self):
        """load_memory sets values that are visible to in-sim combinational logic."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        sim.load_memory("mem", [0xAA, 0xBB, 0xCC, 0xDD])
        sim.drive("addr", 2)
        sim.settle()
        assert int(sim.read("out")) == 0xCC

    def test_partial_load(self):
        """load_memory with fewer elements than depth only touches written addresses."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        sim.load_memory("mem", [0x01, 0x02])
        result = sim.dump_memory("mem", 2)
        assert result == [0x01, 0x02]

    def test_value_masking(self):
        """Values wider than element width are silently truncated to element width."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        sim.load_memory("mem", [0x1FF])  # 9-bit value into 8-bit memory
        result = sim.dump_memory("mem", 1)
        assert result == [0xFF]

    def test_memory_names_property(self):
        """memory_names returns the flat name of every DSL memory."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        names = sim.memory_names
        assert "mem" in names

    def test_memory_names_non_compiled_returns_empty(self):
        """memory_names returns [] for non-compiled engines."""
        m = _make_mem_module()
        sim = Simulator(m, engine="reference")
        assert sim.memory_names == []

    def test_load_memory_wrong_engine_raises(self):
        """load_memory raises NotImplementedError for non-compiled engines."""
        m = _make_mem_module()
        sim = Simulator(m, engine="reference")
        with pytest.raises(NotImplementedError):
            sim.load_memory("mem", [1, 2, 3])

    def test_dump_memory_wrong_engine_raises(self):
        """dump_memory raises NotImplementedError for non-compiled engines."""
        m = _make_mem_module()
        sim = Simulator(m, engine="reference")
        with pytest.raises(NotImplementedError):
            sim.dump_memory("mem", 4)

    def test_load_memory_unknown_name_raises(self):
        """load_memory raises ValueError for an unknown memory name."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        with pytest.raises(ValueError, match="Unknown memory"):
            sim.load_memory("no_such_mem", [1, 2])

    def test_dump_memory_unknown_name_raises(self):
        """dump_memory raises ValueError for an unknown memory name."""
        m = _make_mem_module()
        sim = Simulator(m, engine="compiled")
        with pytest.raises(ValueError, match="Unknown memory"):
            sim.dump_memory("no_such_mem", 4)

    def test_load_overrides_initial_block_values(self):
        """load_memory called after elaboration overrides values set by initial blocks."""
        m = _make_mem_write_module()  # initial block sets mem={AA,BB,CC,DD}
        sim = Simulator(m, engine="compiled")
        # Run initial blocks so the default values land in the compiled arrays
        sim.run(max_time=0)
        # Now override with new values
        sim.load_memory("mem", [0x01, 0x02, 0x03, 0x04])
        result = sim.dump_memory("mem", 4)
        assert result == [0x01, 0x02, 0x03, 0x04]


@pytest.mark.parametrize("engine", ENGINES)
def test_bram_range_select_index_standalone(engine):
    """mem[rd_addr[N:0]] write-then-read works in standalone mode across all engines.

    Regression for the compiled engine's NBA snapshot bug (veriforge commit 627a245):
    signals driven before the clock edge must be visible in seq process bodies.
    """
    dut = _build_bram_dsl(depth=4)
    sim = Simulator(dut, engine=engine)
    result = _bram_write_read(sim, wr_addr_val=2, wr_data_val=0xBEEF, rd_addr_val=2)
    assert int(result) == 0xBEEF, f"[{engine}] standalone mem[rd_addr[N:0]]: expected 0xBEEF, got {result!r}"


@pytest.mark.parametrize("engine", ENGINES)
def test_bram_range_select_index_flattened(engine):
    """mem[rd_addr[N:0]] write-then-read works in flattened (hierarchical) mode across all engines.

    Regression for the compiled engine's NBA snapshot bug: in the flattened module,
    rd_addr is driven via a port-connection CA (assign u_bram.rd_addr = rd_addr).
    The seq process must see this CA-propagated value, not a stale snapshot.
    """
    inner = _build_bram_dsl(depth=4)
    outer = _build_bram_wrapper_dsl(depth=4)
    design = Design(modules=[outer, inner])
    sim = Simulator(outer, engine=engine, design=design)
    result = _bram_write_read(sim, wr_addr_val=2, wr_data_val=0xBEEF, rd_addr_val=2)
    assert int(result) == 0xBEEF, f"[{engine}] flattened mem[rd_addr[N:0]]: expected 0xBEEF, got {result!r}"
