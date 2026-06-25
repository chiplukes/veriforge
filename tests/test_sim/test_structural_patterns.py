"""Tests for DarkRISCV-style structural patterns with hierarchy and simulation.

Each test class exercises a specific Verilog pattern found in the DarkRISCV
SoC, but isolated into a small, self-contained module hierarchy.  This lets
us validate that the simulator handles these constructs correctly without
needing the full 8-module SoC.

Patterns covered:
  1. Multi-bit register initialization  (reg [1:0] X = 0)
  2. Ternary-chain continuous assigns with registered selectors
  3. State machine with ternary NBA     (bridge XSTATE pattern)
  4. Continuous assign dependency chains (wire A = f(B); wire B = f(C); ...)
  5. REQ/ACK handshake across modules   (darkram/darkio DTACK pattern)
  6. Combinational HLT-like feedback    (wire HLT = (REQ ? !ACK : 0))
  7. Bus mux with address decode        (darksocv peripheral selection)
  8. Part-select in comparisons         (addr[7:6] == 2'd1)
  9. Pipeline with stall across hierarchy
 10. Multiple always blocks per module
"""

import shutil

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.design import Design
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

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


def _parse_design(source: str) -> Design:
    """Parse Verilog source containing one or more modules, return Design."""
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    return design


def _parse_and_sim(source: str, engine: str, top_name: str | None = None) -> Simulator:
    """Parse multi-module source and return a ready-to-run Simulator.

    If *top_name* is None, uses the last module in the source (convention:
    testbench is defined last).
    """
    design = _parse_design(source)
    link_instances(design)
    resolve_port_connections(design)
    if top_name:
        tops = [m for m in design.modules if m.name == top_name]
        assert tops, f"Module '{top_name}' not found in {[m.name for m in design.modules]}"
        top = tops[0]
    else:
        top = design.modules[-1]
    return Simulator(top, engine=engine, design=design)


# =====================================================================
# 1. Multi-bit Register Initialization
# =====================================================================
# DarkRISCV's bridge has `reg [1:0] XSTATE = 0;` which must initialise
# to 2'b00, not 1'bx.


class TestMultiBitRegInit:
    """Verify reg initializers set the correct width and value."""

    SRC = """\
    module reg_init_tb;
        reg [1:0] STATE2 = 0;
        reg [3:0] CNT4 = 4'hA;
        reg [7:0] WIDE8 = 8'd200;
        reg [15:0] BIG16 = 16'hCAFE;
        reg FLAG = 1'b1;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_2bit_reg_init_zero(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "reg_init_tb")
        sim.run(max_time=0)
        assert sim.read("STATE2") == Value(0, width=2)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_4bit_reg_init_hex(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "reg_init_tb")
        sim.run(max_time=0)
        assert sim.read("CNT4") == Value(0xA, width=4)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_8bit_reg_init_decimal(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "reg_init_tb")
        sim.run(max_time=0)
        assert sim.read("WIDE8") == Value(200, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_16bit_reg_init(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "reg_init_tb")
        sim.run(max_time=0)
        assert sim.read("BIG16") == Value(0xCAFE, width=16)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_1bit_reg_init(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "reg_init_tb")
        sim.run(max_time=0)
        assert sim.read("FLAG") == Value(1, width=1)


# =====================================================================
# 2. Ternary Chain Continuous Assign with Registered Selector
# =====================================================================
# DarkRISCV bridge uses patterns like:
#   assign XXADDR = XSTATE==2 ? XADDR : XSTATE==1 ? YADDR : 32'd0;
# where XSTATE changes over time.


class TestTernaryChainWithRegSelector:
    """Ternary mux driven by a registered selector that changes on clock."""

    SRC = """\
    module ternary_chain_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg [1:0] SEL = 0;
        wire [7:0] A = 8'd10;
        wire [7:0] B = 8'd20;
        wire [7:0] C = 8'd30;
        wire [7:0] D = 8'd40;

        wire [7:0] Y = SEL == 2'd0 ? A :
                       SEL == 2'd1 ? B :
                       SEL == 2'd2 ? C : D;

        always @(posedge CLK) begin
            SEL <= SEL + 1;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_sel0_gives_A(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("Y") == Value(10, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_sel_advances_through_all(self, engine):
        """After each posedge SEL increments and Y selects the next value."""
        sim = _parse_and_sim(self.SRC, engine)
        # t=0: SEL=0→A=10; t=5(posedge): SEL→1→B=20; t=15: SEL→2→C=30; t=25: SEL→3→D=40
        times_and_expected = [(0, 10), (5, 20), (15, 30), (25, 40)]
        for t, exp in times_and_expected:
            sim.run(max_time=t)
            val = sim.read("Y")
            assert val == Value(exp, width=8), f"t={t}: expected {exp}, got {val}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_sel_wraps_around(self, engine):
        """SEL is 2-bit so after 3 it wraps to 0."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=35)  # 4 posedges: t=5,15,25,35 → SEL wraps to 0
        assert sim.read("Y") == Value(10, width=8)


# =====================================================================
# 3. State Machine with Ternary NBA
# =====================================================================
# The darkbridge XSTATE pattern:
#   XSTATE <= RES ? 0 :
#             XSTATE==0 && DREQ ? 2 :
#             XSTATE==2 && DACK ? 0 : XSTATE;


class TestStateMachineTernaryNBA:
    """State machine using nested ternary in non-blocking assign."""

    SRC = """\
    module state_machine(
        input CLK, input RES,
        input DREQ, input DACK,
        output reg [1:0] STATE
    );
        always @(posedge CLK) begin
            STATE <= RES        ? 2'd0 :
                     STATE == 0 && DREQ ? 2'd2 :
                     STATE == 2 && DACK ? 2'd0 :
                     STATE;
        end
    endmodule

    module sm_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg RES = 1;
        reg DREQ = 0;
        reg DACK = 0;
        wire [1:0] STATE;

        state_machine u_sm(
            .CLK(CLK), .RES(RES),
            .DREQ(DREQ), .DACK(DACK),
            .STATE(STATE)
        );

        initial begin
            #20 RES = 0;
            #10 DREQ = 1;
            #10 DREQ = 0;
            #20 DACK = 1;
            #10 DACK = 0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_starts_in_reset(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "sm_tb")
        sim.run(max_time=15)  # After posedge at t=5,15 with RES=1 → STATE=0
        assert sim.read("u_sm.STATE") == Value(0, width=2)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_exits_reset(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "sm_tb")
        sim.run(max_time=25)  # RES=0 at t=20, posedge at t=25
        assert sim.read("u_sm.STATE") == Value(0, width=2)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_dreq_transitions_to_2(self, engine):
        """After RES=0 and DREQ=1 (t=30), posedge at t=35 → STATE=2."""
        sim = _parse_and_sim(self.SRC, engine, "sm_tb")
        sim.run(max_time=35)
        assert sim.read("u_sm.STATE") == Value(2, width=2)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_dack_transitions_back_to_0(self, engine):
        """DACK=1 at t=60, posedge at t=65 → STATE returns to 0."""
        sim = _parse_and_sim(self.SRC, engine, "sm_tb")
        sim.run(max_time=65)
        assert sim.read("u_sm.STATE") == Value(0, width=2)


# =====================================================================
# 4. Continuous Assign Dependency Chain
# =====================================================================
# Many DarkRISCV signals form long chains:
#   wire A = IN;  wire B = f(A);  wire C = f(B);  wire D = f(C);
# The simulator must propagate through the entire chain in one delta.


class TestContinuousAssignChain:
    """Long chain of continuous assigns that must all resolve in one step."""

    SRC = """\
    module chain_tb;
        reg [7:0] IN = 8'd1;
        wire [7:0] A = IN;
        wire [7:0] B = A + 8'd1;
        wire [7:0] C = B + 8'd1;
        wire [7:0] D = C + 8'd1;
        wire [7:0] E = D + 8'd1;
        wire [7:0] F = E + 8'd1;
        wire [7:0] OUT = F + 8'd1;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_chain_propagates(self, engine):
        """IN=1, each step adds 1, so OUT = 1+6 = 7."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("OUT") == Value(7, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_chain_intermediate_values(self, engine):
        """Check all intermediate values."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        for name, expected in [("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5), ("F", 6), ("OUT", 7)]:
            assert sim.read(name) == Value(expected, width=8), f"{name} wrong"


# =====================================================================
# 5. REQ/ACK Handshake Across Module Boundary
# =====================================================================
# DarkRISCV's darkram/darkio use a DTACK counter for request acknowledge:
#   DTACK <= DTACK ? DTACK-1 : (REQ && RD) ? 1 : 0;
#   assign ACK = DTACK == 1;
# The core waits for ACK before proceeding.


class TestReqAckHandshake:
    """Cross-module request/acknowledge with DTACK countdown."""

    SRC = """\
    module responder(
        input CLK, input RES,
        input REQ, input RD,
        output ACK
    );
        reg [1:0] DTACK = 0;
        always @(posedge CLK)
            DTACK <= RES ? 2'd0 :
                     DTACK != 0 ? DTACK - 2'd1 :
                     (REQ && RD) ? 2'd1 : 2'd0;
        assign ACK = DTACK == 2'd1;
    endmodule

    module handshake_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg RES = 1;
        reg REQ = 0;
        wire ACK;

        responder u_resp(
            .CLK(CLK), .RES(RES),
            .REQ(REQ), .RD(1'b1),
            .ACK(ACK)
        );

        initial begin
            #22 RES = 0;
            #11 REQ = 1;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ack_deasserted_during_reset(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "handshake_tb")
        sim.run(max_time=20)
        assert sim.read("u_resp.ACK") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ack_deasserted_before_req(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "handshake_tb")
        sim.run(max_time=30)
        assert sim.read("u_resp.ACK") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ack_asserts_one_cycle_after_req(self, engine):
        """REQ=1 at t=33, posedge at t=35 sets DTACK=1, ACK=1."""
        sim = _parse_and_sim(self.SRC, engine, "handshake_tb")
        sim.run(max_time=35)
        assert sim.read("u_resp.ACK") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ack_deasserts_next_cycle(self, engine):
        """DTACK decrements to 0, ACK goes back to 0."""
        sim = _parse_and_sim(self.SRC, engine, "handshake_tb")
        sim.run(max_time=45)
        assert sim.read("u_resp.ACK") == Value(0, width=1)


# =====================================================================
# 6. Combinational HLT-like Feedback
# =====================================================================
# wire HLT = (DREQ ? !DACK : 1'b0) || (IREQ ? !IACK : 1'b0);
# HLT asserts when a request is pending without acknowledge.


class TestCombinationalHLT:
    """HLT pattern — combinational feedback from REQ/ACK signals."""

    SRC = """\
    module hlt_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg DREQ = 0;
        reg DACK = 0;
        reg IREQ = 0;
        reg IACK = 0;

        wire HLT = (DREQ ? !DACK : 1'b0) || (IREQ ? !IACK : 1'b0);

        initial begin
            #12 DREQ = 1;
            #10 DACK = 1;
            #6  DACK = 0; DREQ = 0;
            #4  IREQ = 1;
            #10 IACK = 1;
            #10 IREQ = 0; IACK = 0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hlt_low_initially(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=10)
        assert sim.read("HLT") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hlt_high_when_dreq_no_dack(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=20)
        assert sim.read("HLT") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hlt_low_when_dack(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=30)
        assert sim.read("HLT") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hlt_high_when_ireq_no_iack(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=38)
        assert sim.read("HLT") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hlt_low_after_iack(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=48)
        assert sim.read("HLT") == Value(0, width=1)


# =====================================================================
# 7. Bus Mux with Address Decode
# =====================================================================
# darksocv.v uses address bits to select peripherals:
#   assign REQ0 = XDREQ && ADDR[7:6]==0;
#   assign REQ1 = XDREQ && ADDR[7:6]==1;
# And multiplexes acknowledge/data back:
#   wire ACK = ADDR[7:6]==0 ? ACK0 :
#              ADDR[7:6]==1 ? ACK1 : 1'b0;


class TestBusMuxAddressDecode:
    """Peripheral selection via address bits, with muxed ACK."""

    SRC = """\
    module peripheral(
        input CLK, input REQ,
        output reg [7:0] DATA,
        output ACK
    );
        parameter ID = 0;
        reg [1:0] DTACK = 0;
        always @(posedge CLK)
            DTACK <= DTACK != 0 ? DTACK - 2'd1 : REQ ? 2'd1 : 2'd0;
        assign ACK = DTACK == 2'd1;
        always @(posedge CLK)
            if (REQ) DATA <= ID;
    endmodule

    module bus_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg [7:0] ADDR = 0;
        reg DREQ = 0;

        wire REQ0 = DREQ && ADDR[7:6] == 2'd0;
        wire REQ1 = DREQ && ADDR[7:6] == 2'd1;
        wire REQ2 = DREQ && ADDR[7:6] == 2'd2;

        wire [7:0] DATA0, DATA1, DATA2;
        wire ACK0, ACK1, ACK2;

        peripheral #(.ID(8'd10)) p0(.CLK(CLK), .REQ(REQ0), .DATA(DATA0), .ACK(ACK0));
        peripheral #(.ID(8'd20)) p1(.CLK(CLK), .REQ(REQ1), .DATA(DATA1), .ACK(ACK1));
        peripheral #(.ID(8'd30)) p2(.CLK(CLK), .REQ(REQ2), .DATA(DATA2), .ACK(ACK2));

        wire ACK_MUX = ADDR[7:6] == 2'd0 ? ACK0 :
                       ADDR[7:6] == 2'd1 ? ACK1 :
                       ADDR[7:6] == 2'd2 ? ACK2 : 1'b0;

        wire [7:0] DATA_MUX = ADDR[7:6] == 2'd0 ? DATA0 :
                              ADDR[7:6] == 2'd1 ? DATA1 :
                              ADDR[7:6] == 2'd2 ? DATA2 : 8'd0;

        initial begin
            #3  ADDR = 8'h40; DREQ = 1;
            #40 DREQ = 0;
            #5  ADDR = 8'h80; DREQ = 1;
            #30 DREQ = 0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_addr_decode_selects_peripheral_1(self, engine):
        """ADDR=0x40 → ADDR[7:6]=1 → REQ1=1, peripheral #1 selected."""
        sim = _parse_and_sim(self.SRC, engine, "bus_tb")
        sim.run(max_time=3)
        assert sim.read("REQ1") == Value(1, width=1)
        assert sim.read("REQ0") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ack_mux_returns_correct_ack(self, engine):
        """ADDR/DREQ set at t=3, posedge at t=5 sets DTACK=1, ACK_MUX=1."""
        sim = _parse_and_sim(self.SRC, engine, "bus_tb")
        sim.run(max_time=8)  # after posedge at t=5
        assert sim.read("ACK_MUX") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_data_mux_returns_peripheral_id(self, engine):
        """DATA_MUX shows peripheral 1's ID (20) after request."""
        sim = _parse_and_sim(self.SRC, engine, "bus_tb")
        sim.run(max_time=8)
        assert sim.read("DATA_MUX") == Value(20, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_second_peripheral_via_addr_change(self, engine):
        """After changing ADDR to 0x80 (peripheral 2), DATA_MUX shows ID=30."""
        sim = _parse_and_sim(self.SRC, engine, "bus_tb")
        sim.run(max_time=58)  # ADDR=0x80 at t=48, posedge at t=55
        assert sim.read("DATA_MUX") == Value(30, width=8)


# =====================================================================
# 8. Part-Select in Comparisons
# =====================================================================
# DarkRISCV frequently uses things like:
#   ADDR[31:30] == 2'd1
#   opcode[6:2] == 5'b01100


class TestPartSelectComparison:
    """Part-select expressions used in comparison and assignment."""

    SRC = """\
    module partsel_tb;
        reg [31:0] ADDR = 32'h40000000;
        wire [1:0] CS = ADDR[31:30];
        wire IS_IO = CS == 2'd1;
        wire IS_RAM = ADDR[31:30] == 2'd0;

        reg [31:0] INSTR = 32'b00000000000000000000001000110011;
        wire [6:0] OPCODE = INSTR[6:0];
        wire [2:0] FUNCT3 = INSTR[14:12];
        wire [4:0] RD = INSTR[11:7];
        wire IS_RTYPE = OPCODE == 7'b0110011;
        wire IS_ADD = IS_RTYPE && FUNCT3 == 3'b000;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_cs_is_1_for_io(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("CS") == Value(1, width=2)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_is_io_true(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("IS_IO") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_is_ram_false(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("IS_RAM") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_opcode_extraction(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("OPCODE") == Value(0b0110011, width=7)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_rd_extraction(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("RD") == Value(4, width=5)  # bits 11:7 = 00100

    @pytest.mark.parametrize("engine", ENGINES)
    def test_compound_decode(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("IS_ADD") == Value(1, width=1)


# =====================================================================
# 9. Pipeline with Stall Across Hierarchy
# =====================================================================
# DarkRISCV has a pipeline where HLT gates register updates:
#   always @(posedge CLK) if(!HLT) PC <= NXPC;
# HLT comes from a submodule.


class TestPipelineWithStall:
    """Two-stage pipeline where a submodule can stall the pipe."""

    SRC = """\
    module stall_ctrl(
        input CLK, input RES,
        input STALL_REQ,
        output STALL
    );
        reg [1:0] CNT = 0;
        always @(posedge CLK)
            CNT <= RES ? 2'd0 :
                   STALL_REQ && CNT == 0 ? 2'd2 :
                   CNT != 0 ? CNT - 2'd1 : 2'd0;
        assign STALL = CNT != 2'd0;
    endmodule

    module pipeline_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg RES = 1;
        reg STALL_REQ = 0;

        wire STALL;
        stall_ctrl u_stall(
            .CLK(CLK), .RES(RES),
            .STALL_REQ(STALL_REQ),
            .STALL(STALL)
        );

        reg [7:0] PC = 0;
        reg [7:0] NXPC = 0;
        wire [7:0] NXPC2 = NXPC + 8'd4;

        always @(posedge CLK) begin
            if (RES) begin
                PC <= 0;
                NXPC <= 0;
            end else if (!STALL) begin
                PC <= NXPC;
                NXPC <= NXPC2;
            end
        end

        initial begin
            #25 RES = 0;
            #30 STALL_REQ = 1;
            #10 STALL_REQ = 0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_pc_advances_normally(self, engine):
        """After reset, PC increments by 4 each cycle."""
        sim = _parse_and_sim(self.SRC, engine, "pipeline_tb")
        sim.run(max_time=45)  # 2 clock cycles after reset
        pc = sim.read("PC")
        assert pc.val >= 4, f"PC should advance after reset, got {pc}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_stall_freezes_pc(self, engine):
        """When STALL is asserted, PC stops advancing."""
        sim = _parse_and_sim(self.SRC, engine, "pipeline_tb")
        sim.run(max_time=55)  # first stall posedge
        pc_before = sim.read("PC")
        sim.run(max_time=65)  # next posedge, still stalled
        pc_after = sim.read("PC")
        assert pc_before == pc_after, f"PC changed during stall: {pc_before} -> {pc_after}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_pc_resumes_after_stall(self, engine):
        """After CNT counts down to 0, STALL deasserts, PC advances again."""
        sim = _parse_and_sim(self.SRC, engine, "pipeline_tb")
        sim.run(max_time=55)
        pc_stalled = sim.read("PC")
        sim.run(max_time=85)  # several cycles after stall ends
        pc_resumed = sim.read("PC")
        assert pc_resumed.val > pc_stalled.val, (
            f"PC didn't resume after stall: stalled={pc_stalled}, resumed={pc_resumed}"
        )


# =====================================================================
# 10. Multiple Always Blocks per Module
# =====================================================================
# DarkRISCV modules have many always blocks updating different
# registers on the same clock edge.


class TestMultipleAlwaysBlocks:
    """Multiple always blocks in one module, each updating different regs."""

    SRC = """\
    module multi_always_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg [7:0] A = 0;
        reg [7:0] B = 0;
        reg [7:0] C = 0;

        always @(posedge CLK) A <= A + 8'd1;
        always @(posedge CLK) B <= B + 8'd2;
        always @(posedge CLK) C <= A + B;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_all_three_update(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=5)  # after 1st posedge
        assert sim.read("A") == Value(1, width=8)
        assert sim.read("B") == Value(2, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_c_uses_old_values(self, engine):
        """C reads A and B from BEFORE this posedge (NBA semantics)."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=5)  # posedge at t=5: C = A(old=0) + B(old=0) = 0
        assert sim.read("C") == Value(0, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_c_reflects_previous_cycle_values(self, engine):
        """After 2nd posedge: C = A(1) + B(2) = 3."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=15)
        assert sim.read("C") == Value(3, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_values_after_several_cycles(self, engine):
        """After 4 posedges: A=4, B=8, C = A(3)+B(6) = 9."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=35)
        assert sim.read("A") == Value(4, width=8)
        assert sim.read("B") == Value(8, width=8)
        assert sim.read("C") == Value(9, width=8)


# =====================================================================
# 11. Full REQ/ACK with HLT Pipeline Stall (Integration)
# =====================================================================
# Combines patterns 5, 6, 9: a pipeline that sends data requests
# to a responder, and HLT stalls the pipe until ACK.


class TestReqAckPipelineStall:
    """Pipeline stalls on data request until responder acknowledges."""

    SRC = """\
    module mem_responder(
        input CLK, input RES,
        input REQ, input RD,
        output ACK,
        output reg [7:0] RDATA
    );
        reg [1:0] DTACK = 0;
        always @(posedge CLK)
            DTACK <= RES ? 2'd0 :
                     DTACK != 0 ? DTACK - 2'd1 :
                     (REQ && RD) ? 2'd1 : 2'd0;
        assign ACK = DTACK == 2'd1;
        always @(posedge CLK)
            if (REQ && RD && DTACK == 0) RDATA <= 8'hAB;
    endmodule

    module cpu_core(
        input CLK, input RES,
        output reg DREQ,
        output DRD,
        input DACK,
        input [7:0] DATAI,
        output reg [7:0] PC,
        output reg [7:0] RESULT
    );
        assign DRD = 1'b1;
        wire HLT = DREQ ? !DACK : 1'b0;

        always @(posedge CLK) begin
            if (RES) begin
                PC <= 0;
                DREQ <= 0;
                RESULT <= 0;
            end else if (!HLT) begin
                PC <= PC + 8'd4;
                if (PC == 8'd8) begin
                    DREQ <= 1;
                end else begin
                    DREQ <= 0;
                end
                if (DACK) RESULT <= DATAI;
            end
        end
    endmodule

    module cpu_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg RES = 1;

        wire DREQ, DRD, DACK;
        wire [7:0] DATAI;
        wire [7:0] PC;
        wire [7:0] RESULT;

        cpu_core u_cpu(
            .CLK(CLK), .RES(RES),
            .DREQ(DREQ), .DRD(DRD), .DACK(DACK),
            .DATAI(DATAI), .PC(PC), .RESULT(RESULT)
        );

        mem_responder u_mem(
            .CLK(CLK), .RES(RES),
            .REQ(DREQ), .RD(DRD),
            .ACK(DACK), .RDATA(DATAI)
        );

        initial #15 RES = 0;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_pc_advances_before_dreq(self, engine):
        """PC should increment normally before DREQ asserts."""
        sim = _parse_and_sim(self.SRC, engine, "cpu_tb")
        sim.run(max_time=35)  # a couple cycles after reset
        pc = sim.read("u_cpu.PC")
        assert pc.val >= 4, f"PC should advance, got {pc}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hlt_asserts_on_dreq(self, engine):
        """When CPU drives DREQ, HLT goes high (ACK not yet asserted)."""
        sim = _parse_and_sim(self.SRC, engine, "cpu_tb")
        # Run until DREQ asserts — PC=8 triggers DREQ
        # After reset (t=15), PC increments: t=25→PC=4, t=35→PC=8, t=45→DREQ=1
        sim.run(max_time=100)
        dreq = sim.read("u_cpu.DREQ")
        # At some point DREQ was high; verify the pipeline didn't run away
        pc = sim.read("u_cpu.PC")
        assert pc.val <= 80, f"PC should not advance wildly, got {pc}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ack_eventually_resumes_pipeline(self, engine):
        """After ACK, HLT deasserts and PC continues."""
        sim = _parse_and_sim(self.SRC, engine, "cpu_tb")
        sim.run(max_time=200)
        pc = sim.read("u_cpu.PC")
        # PC should have advanced past the stall point
        assert pc.val > 8, f"PC should resume after ACK, got {pc}"


# =====================================================================
# 12. Deep Hierarchy (3 levels)
# =====================================================================
# top → bridge → core, signals pass through intermediate module.


class TestDeepHierarchy:
    """Three-level hierarchy with signal propagation through ports."""

    SRC = """\
    module leaf(input CLK, input [7:0] DIN, output reg [7:0] DOUT);
        always @(posedge CLK) DOUT <= DIN + 8'd1;
    endmodule

    module middle(input CLK, input [7:0] IN, output [7:0] OUT);
        leaf u_leaf(.CLK(CLK), .DIN(IN), .DOUT(OUT));
    endmodule

    module deep_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg [7:0] DATA = 8'd42;
        wire [7:0] RESULT;
        middle u_mid(.CLK(CLK), .IN(DATA), .OUT(RESULT));
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_data_flows_through_3_levels(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "deep_tb")
        sim.run(max_time=10)
        assert sim.read("RESULT") == Value(43, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_internal_signal_accessible(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "deep_tb")
        sim.run(max_time=10)
        assert sim.read("u_mid.u_leaf.DOUT") == Value(43, width=8)


# =====================================================================
# 13. Ternary Output Mux with Nested Continuous Assigns
# =====================================================================
# DarkRISCV has patterns where multiple continuous assign outputs
# are selected by a state register:
#   assign XXDREQ = XSTATE==2 ? XDREQ : XSTATE==1 ? YDREQ : 0;
#   assign XXADDR = XSTATE==2 ? XADDR : XSTATE==1 ? YADDR : 0;
# Both driven by the same state register.


class TestTernaryOutputMux:
    """Multiple output muxes driven by the same state register."""

    SRC = """\
    module output_mux(
        input CLK, input RES,
        input DATA_REQ, input INSTR_REQ,
        input [7:0] DATA_ADDR, input [7:0] INSTR_ADDR,
        output [7:0] BUS_ADDR,
        output BUS_REQ
    );
        reg [1:0] STATE = 0;
        always @(posedge CLK)
            STATE <= RES ? 2'd0 :
                     STATE == 0 && DATA_REQ  ? 2'd2 :
                     STATE == 0 && INSTR_REQ ? 2'd1 :
                     2'd0;

        assign BUS_REQ  = STATE == 2'd2 ? DATA_REQ  :
                          STATE == 2'd1 ? INSTR_REQ : 1'b0;
        assign BUS_ADDR = STATE == 2'd2 ? DATA_ADDR  :
                          STATE == 2'd1 ? INSTR_ADDR : 8'd0;
    endmodule

    module output_mux_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg RES = 1;
        reg DREQ = 0;
        reg IREQ = 0;
        reg [7:0] DADDR = 8'hAA;
        reg [7:0] IADDR = 8'h55;
        wire [7:0] BUS_ADDR;
        wire BUS_REQ;

        output_mux u_mux(
            .CLK(CLK), .RES(RES),
            .DATA_REQ(DREQ), .INSTR_REQ(IREQ),
            .DATA_ADDR(DADDR), .INSTR_ADDR(IADDR),
            .BUS_ADDR(BUS_ADDR), .BUS_REQ(BUS_REQ)
        );

        initial begin
            #12 RES = 0;
            #11 DREQ = 1;
            #20 DREQ = 0;
            #10 IREQ = 1;
            #20 IREQ = 0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_idle_outputs_zero(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "output_mux_tb")
        sim.run(max_time=20)
        assert sim.read("BUS_REQ") == Value(0, width=1)
        assert sim.read("BUS_ADDR") == Value(0, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_data_req_selects_data_addr(self, engine):
        """DREQ=1 at t=23, posedge at t=25: STATE→2, BUS_ADDR=DADDR."""
        sim = _parse_and_sim(self.SRC, engine, "output_mux_tb")
        sim.run(max_time=28)
        assert sim.read("u_mux.STATE") == Value(2, width=2)
        assert sim.read("BUS_ADDR") == Value(0xAA, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_instr_req_selects_instr_addr(self, engine):
        """IREQ=1 at t=53, posedge at t=55: STATE→1, BUS_ADDR=IADDR."""
        sim = _parse_and_sim(self.SRC, engine, "output_mux_tb")
        sim.run(max_time=58)
        assert sim.read("u_mux.STATE") == Value(1, width=2)
        assert sim.read("BUS_ADDR") == Value(0x55, width=8)


# =====================================================================
# 14. Conditional Assignment with Bit Operations
# =====================================================================
# DarkRISCV uses patterns like:
#   wire SCC = FLUSH ? 0 : XLUI||XAUIPC||XJAL;   (OR of decoded signals)
#   wire LCC = FLUSH ? 0 : XLCC;


class TestConditionalBitOps:
    """Conditional mux of OR-combined decode signals (like DarkRISCV SCC/LCC)."""

    SRC = """\
    module decode_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg [6:0] OPCODE = 7'b0110011;
        reg FLUSH = 1;

        wire IS_LUI   = OPCODE == 7'b0110111;
        wire IS_AUIPC = OPCODE == 7'b0010111;
        wire IS_JAL   = OPCODE == 7'b1101111;
        wire IS_RTYPE = OPCODE == 7'b0110011;

        wire SCC = FLUSH ? 1'b0 : IS_LUI || IS_AUIPC || IS_JAL;
        wire LCC = FLUSH ? 1'b0 : IS_RTYPE;

        initial begin
            #15 FLUSH = 0;
            #20 OPCODE = 7'b0110111;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_flush_suppresses_decode(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=10)
        assert sim.read("SCC") == Value(0, width=1)
        assert sim.read("LCC") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_rtype_sets_lcc(self, engine):
        """After FLUSH=0, OPCODE is R-type, LCC should be 1."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=20)
        assert sim.read("LCC") == Value(1, width=1)
        assert sim.read("SCC") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_lui_sets_scc(self, engine):
        """After OPCODE changes to LUI, SCC should be 1."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=40)
        assert sim.read("SCC") == Value(1, width=1)
        assert sim.read("LCC") == Value(0, width=1)


# =====================================================================
# 15. Interconnected Continuous Assigns (Combinational Cloud)
# =====================================================================
# DarkRISCV has ~57 continuous assigns that form a dependency graph.
# Some are interdependent: A depends on B which depends on C which
# depends on D.  The simulator must converge all of them.


class TestCombinationalCloud:
    """Multiple interdependent continuous assigns forming a dependency DAG."""

    SRC = """\
    module cloud_tb;
        reg [7:0] X = 8'd5;
        reg [7:0] Y = 8'd3;

        wire [7:0] SUM    = X + Y;
        wire [7:0] DIFF   = X - Y;
        wire       GT     = X > Y;
        wire [7:0] MAX    = GT ? X : Y;
        wire [7:0] MIN    = GT ? Y : X;
        wire [7:0] RANGE  = MAX - MIN;
        wire [7:0] AVG    = SUM >> 1;
        wire       EQ     = RANGE == 8'd0;
        wire [7:0] RESULT = EQ ? AVG : RANGE + AVG;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_all_assigns_resolve(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("SUM") == Value(8, width=8)
        assert sim.read("DIFF") == Value(2, width=8)
        assert sim.read("GT") == Value(1, width=1)
        assert sim.read("MAX") == Value(5, width=8)
        assert sim.read("MIN") == Value(3, width=8)
        assert sim.read("RANGE") == Value(2, width=8)
        assert sim.read("AVG") == Value(4, width=8)
        assert sim.read("EQ") == Value(0, width=1)
        assert sim.read("RESULT") == Value(6, width=8)  # 2+4

    @pytest.mark.parametrize("engine", ENGINES)
    def test_equal_inputs(self, engine):
        """When X==Y, EQ=1, RESULT=AVG."""
        sim = _parse_and_sim(
            """\
        module cloud_eq_tb;
            reg [7:0] X = 8'd10;
            reg [7:0] Y = 8'd10;
            wire [7:0] SUM    = X + Y;
            wire [7:0] DIFF   = X - Y;
            wire       GT     = X > Y;
            wire [7:0] MAX    = GT ? X : Y;
            wire [7:0] MIN    = GT ? Y : X;
            wire [7:0] RANGE  = MAX - MIN;
            wire [7:0] AVG    = SUM >> 1;
            wire       EQ     = RANGE == 8'd0;
            wire [7:0] RESULT = EQ ? AVG : RANGE + AVG;
        endmodule
        """,
            engine,
        )
        sim.run(max_time=0)
        assert sim.read("EQ") == Value(1, width=1)
        assert sim.read("RESULT") == Value(10, width=8)


# =====================================================================
# 16. Hierarchical Parameter Override
# =====================================================================
# DarkRISCV uses parameter overrides for module configuration:
#   peripheral #(.ID(1)) p1 (...);


class TestHierarchicalParamOverride:
    """Parameter overrides propagate correctly through hierarchy."""

    SRC = """\
    module counter(input CLK, input RES, output reg [7:0] CNT);
        parameter STEP = 1;
        always @(posedge CLK)
            CNT <= RES ? 8'd0 : CNT + STEP;
    endmodule

    module param_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg RES = 1;
        wire [7:0] CNT1, CNT2, CNT3;

        counter #(.STEP(1))  u1(.CLK(CLK), .RES(RES), .CNT(CNT1));
        counter #(.STEP(2))  u2(.CLK(CLK), .RES(RES), .CNT(CNT2));
        counter #(.STEP(10)) u3(.CLK(CLK), .RES(RES), .CNT(CNT3));

        initial #22 RES = 0;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_different_step_values(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "param_tb")
        sim.run(max_time=35)  # 2 posedges after reset (t=25, t=35)
        assert sim.read("CNT1") == Value(2, width=8)
        assert sim.read("CNT2") == Value(4, width=8)
        assert sim.read("CNT3") == Value(20, width=8)


# =====================================================================
# 17. Registered Output from Submodule Drives Continuous Assign
# =====================================================================
# Pattern: submodule has registered output, parent has continuous
# assign that transforms it.  This tests the cross-boundary propagation.


class TestRegOutputDrivesContinuousAssign:
    """Registered submodule output feeds into parent's continuous assign."""

    SRC = """\
    module shifter(
        input CLK,
        input [7:0] DIN,
        output reg [7:0] DOUT
    );
        always @(posedge CLK) DOUT <= DIN << 1;
    endmodule

    module shift_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;
        reg [7:0] INPUT = 8'd3;
        wire [7:0] SHIFTED;

        shifter u_sh(.CLK(CLK), .DIN(INPUT), .DOUT(SHIFTED));

        wire [7:0] MASKED = SHIFTED & 8'h0F;
        wire       NONZERO = |MASKED;
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_shifted_value(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "shift_tb")
        sim.run(max_time=10)
        assert sim.read("SHIFTED") == Value(6, width=8)  # 3<<1

    @pytest.mark.parametrize("engine", ENGINES)
    def test_masked_value(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "shift_tb")
        sim.run(max_time=10)
        assert sim.read("MASKED") == Value(6, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_reduction_feeds_from_masked(self, engine):
        sim = _parse_and_sim(self.SRC, engine, "shift_tb")
        sim.run(max_time=10)
        assert sim.read("NONZERO") == Value(1, width=1)


# =====================================================================
# 18. Bidirectional Continuous Assign Convergence
# =====================================================================
# Two parallel continuous assigns that both depend on the same input
# register, and a third that combines them.  Tests order-independence.


class TestParallelContinuousAssigns:
    """Parallel continuous assigns from the same source, combined downstream."""

    SRC = """\
    module parallel_tb;
        reg [7:0] IN = 8'd100;

        wire [7:0] PATH_A = IN + 8'd10;
        wire [7:0] PATH_B = IN - 8'd10;
        wire [7:0] COMBINED = PATH_A + PATH_B;
        wire       CHECK = COMBINED == (IN + IN);
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_parallel_paths_converge(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("PATH_A") == Value(110, width=8)
        assert sim.read("PATH_B") == Value(90, width=8)
        assert sim.read("COMBINED") == Value(200, width=8)
        assert sim.read("CHECK") == Value(1, width=1)


# =====================================================================
# 19. BitSelect LHS Continuous Assign Propagation
# =====================================================================
# Regression test for the bug where `assign VEC[i] = expr` did not
# propagate changes through the dirty set to downstream CAs that
# read VEC.   (DarkRISCV darksocv XDREQMUX pattern)


class TestBitSelectLHSPropagation:
    """assign VEC[i] = expr; assign OUT = VEC[i]; must propagate."""

    SRC = """\
    module bitsel_lhs_tb;
        reg SEL = 1'b0;
        reg [1:0] ADDR = 2'd1;

        wire [3:0] REQMUX;
        assign REQMUX[0] = SEL && ADDR == 2'd0;
        assign REQMUX[1] = SEL && ADDR == 2'd1;
        assign REQMUX[2] = SEL && ADDR == 2'd2;
        assign REQMUX[3] = SEL && ADDR == 2'd3;

        wire OUT = REQMUX[1];

        initial begin
            #10 SEL = 1'b1;
            #10 SEL = 1'b0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_bitsel_propagates_at_t0(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=0)
        assert sim.read("OUT") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_bitsel_propagates_on_change(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=15)
        # SEL=1, ADDR=1 → REQMUX[1]=1, OUT=1
        assert sim.read("REQMUX") == Value(0b0010, width=4)
        assert sim.read("OUT") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_bitsel_propagates_deassert(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=25)
        # SEL=0 → REQMUX=0, OUT=0
        assert sim.read("REQMUX") == Value(0, width=4)
        assert sim.read("OUT") == Value(0, width=1)


# =====================================================================
# 20. BitSelect Port Connection Through Hierarchy
# =====================================================================
# Tests the DarkRISCV pattern: parent assigns VEC[i], connects it
# as a port to a child module.  Changes must propagate across hierarchy.


class TestBitSelectPortConnection:
    """Port connection with packed bit-select must propagate across modules."""

    SRC = """\
    module child(input DREQ, output DACK);
        assign DACK = DREQ;
    endmodule

    module parent_tb;
        reg EN = 1'b0;
        reg [1:0] SEL = 2'd1;

        wire [3:0] REQMUX;
        assign REQMUX[0] = EN && SEL == 2'd0;
        assign REQMUX[1] = EN && SEL == 2'd1;
        assign REQMUX[2] = EN && SEL == 2'd2;
        assign REQMUX[3] = EN && SEL == 2'd3;

        wire ACK1;
        child c1(.DREQ(REQMUX[1]), .DACK(ACK1));

        initial begin
            #10 EN = 1'b1;
            #10 EN = 1'b0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_port_bitsel_propagates(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=15)
        assert sim.read("REQMUX") == Value(0b0010, width=4)
        assert sim.read("c1.DREQ") == Value(1, width=1)
        assert sim.read("ACK1") == Value(1, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_port_bitsel_deassert(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=25)
        assert sim.read("REQMUX") == Value(0, width=4)
        assert sim.read("c1.DREQ") == Value(0, width=1)
        assert sim.read("ACK1") == Value(0, width=1)


# =====================================================================
# 21. Unpacked Array Output Port Connection
# =====================================================================
# Tests `wire ACKMUX [0:3]; child c(.DACK(ACKMUX[1]));`
# where child drives output through the unpacked array element.


class TestUnpackedArrayOutputPort:
    """Output port connected to unpacked array element must propagate."""

    SRC = """\
    module ack_gen(input CLK, input REQ, output reg ACK);
        always @(posedge CLK)
            ACK <= REQ;
    endmodule

    module unpacked_tb;
        reg CLK = 0;
        reg EN = 0;
        reg [1:0] SEL = 2'd1;

        wire [3:0] REQMUX;
        assign REQMUX[0] = EN && SEL == 2'd0;
        assign REQMUX[1] = EN && SEL == 2'd1;
        assign REQMUX[2] = EN && SEL == 2'd2;
        assign REQMUX[3] = EN && SEL == 2'd3;

        wire ACKMUX [0:3];

        ack_gen g0(.CLK(CLK), .REQ(REQMUX[0]), .ACK(ACKMUX[0]));
        ack_gen g1(.CLK(CLK), .REQ(REQMUX[1]), .ACK(ACKMUX[1]));

        wire RESULT = ACKMUX[1];

        initial begin
            #5 EN = 1;
            #5 CLK = 1;   // t=10 posedge
            #5 CLK = 0;   // t=15
            #5 CLK = 1;   // t=20 posedge — ACK latches
            #5 CLK = 0;   // t=25
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_unpacked_output_propagates(self, engine):
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=25)
        assert sim.read("RESULT") == Value(1, width=1)


class TestAttributeOnStatement:
    """Test (* parallel_case, full_case *) attribute on case statement.

    Verilog allows ``(* attr *)`` before a statement.  The parse tree
    places an ``attribute_instance`` child inside the ``statement`` node,
    before the actual semantic node (e.g. ``case_statement``).  The model
    transformer must skip attribute_instance when unwrapping statements.
    """

    SRC = """\
    module attr_case_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        localparam STATE_A = 8'b10000000;
        localparam STATE_B = 8'b01000000;
        localparam STATE_C = 8'b00100000;

        reg [7:0] state;
        reg [7:0] result;
        reg resetn;

        always @(posedge CLK) begin
            if (!resetn) begin
                state <= STATE_B;
                result <= 0;
            end else
            (* parallel_case, full_case *)
            case (state)
                STATE_A: result <= 8'd1;
                STATE_B: result <= 8'd2;
                STATE_C: result <= 8'd3;
            endcase
        end

        initial begin
            resetn = 0;
            #20 resetn = 1;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_case_with_attribute_matches(self, engine):
        """Case body after (* attr *) must execute correctly."""
        sim = _parse_and_sim(self.SRC, engine, "attr_case_tb")
        sim.run(max_time=50)
        # state is STATE_B (64), case should set result = 2
        assert sim.read("result") == Value(2, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_attribute_on_case_in_else(self, engine):
        """Attribute before case in else-branch must not drop the else body."""
        design = _parse_design(self.SRC)
        mod = design.get_module("attr_case_tb")
        # Find the always @(posedge CLK) block with the if-else
        from veriforge.model.statements import CaseStatement, IfStatement

        for ab in mod.always_blocks:
            body = ab.body
            # Walk into SeqBlock if needed
            if hasattr(body, "statements"):
                for s in body.statements:
                    if isinstance(s, IfStatement):
                        assert s.else_body is not None, "else body lost due to attribute_instance"
                        assert isinstance(s.else_body, CaseStatement), (
                            f"else body should be CaseStatement, got {type(s.else_body).__name__}"
                        )
                        return
        pytest.fail("Could not find IfStatement in always block")


class TestCaseOneHotPriority:
    """Test ``case (1'b1)`` priority pattern used in PicoRV32.

    This pattern checks each case item expression and takes the first
    branch whose expression matches 1'b1 (i.e. is true).  It's a common
    Verilog idiom for priority encoding.
    """

    SRC = """\
    module case_1b1_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg A, B, C;
        reg [7:0] result;

        always @(posedge CLK) begin
            (* parallel_case *)
            case (1'b1)
                A: result <= 8'd1;
                B: result <= 8'd2;
                C: result <= 8'd3;
                default: result <= 8'd0;
            endcase
        end

        initial begin
            A = 0; B = 0; C = 0;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_no_match_default(self, engine):
        """When no expression is true, default fires."""
        sim = _parse_and_sim(self.SRC, engine, "case_1b1_tb")
        sim.run(max_time=15)
        assert sim.read("result") == Value(0, width=8)

    SRC_B_ONLY = """\
    module case_1b1_b_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg A = 0, B = 1, C = 0;
        reg [7:0] result;

        always @(posedge CLK) begin
            (* parallel_case *)
            case (1'b1)
                A: result <= 8'd1;
                B: result <= 8'd2;
                C: result <= 8'd3;
                default: result <= 8'd0;
            endcase
        end
    endmodule
    """

    SRC_AB = """\
    module case_1b1_ab_tb;
        reg CLK = 0;
        initial while(1) #5 CLK = ~CLK;

        reg A = 1, B = 1, C = 0;
        reg [7:0] result;

        always @(posedge CLK) begin
            (* parallel_case *)
            case (1'b1)
                A: result <= 8'd1;
                B: result <= 8'd2;
                C: result <= 8'd3;
                default: result <= 8'd0;
            endcase
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_single_match(self, engine):
        """When B=1 only, result should be 2."""
        sim = _parse_and_sim(self.SRC_B_ONLY, engine, "case_1b1_b_tb")
        sim.run(max_time=15)
        assert sim.read("result") == Value(2, width=8)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_priority_first_wins(self, engine):
        """When A=1 and B=1 both, A wins (priority)."""
        sim = _parse_and_sim(self.SRC_AB, engine, "case_1b1_ab_tb")
        sim.run(max_time=15)
        assert sim.read("result") == Value(1, width=8)


# =====================================================================
# Parametric range widths (localparam-dependent signal widths)
# =====================================================================


class TestParametricRangeWidth:
    """Signals declared with localparam-dependent widths like [BITS-1:0]."""

    SRC = """\
    module param_width_tb;
        parameter WIDTH_SEL = 1;
        localparam integer BITS = (WIDTH_SEL ? 8 : 4);
        reg [BITS-1:0] data;
        reg [BITS-1:0] result;
        reg clk = 0;

        always #5 clk = ~clk;

        initial begin
            data = 8'hA5;
        end

        always @(posedge clk) begin
            result <= data;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_parametric_width(self, engine):
        """Localparam ternary width: reg [BITS-1:0] should be 8 bits."""
        sim = _parse_and_sim(self.SRC, engine, "param_width_tb")
        sim.run(max_time=15)
        val = sim.read("data")
        assert val.width == 8, f"Expected width 8, got {val.width}"
        assert val == Value(0xA5, width=8)

    SRC_REGFILE = """\
    module regfile_tb;
        parameter USE_WIDE = 1;
        localparam integer INDEX_BITS = (USE_WIDE ? 5 : 4);
        localparam integer REGFILE_SIZE = (USE_WIDE ? 32 : 16);

        reg [31:0] regs [0:REGFILE_SIZE-1];
        reg [INDEX_BITS-1:0] wr_addr;
        reg [31:0] wr_data;
        reg clk = 0;

        always #5 clk = ~clk;

        initial begin
            wr_addr = 5'd1;
            wr_data = 32'h0000_03FC;
        end

        always @(posedge clk) begin
            regs[wr_addr] <= wr_data;
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_regfile_parametric_index(self, engine):
        """Register file with parametric index width and memory size."""
        sim = _parse_and_sim(self.SRC_REGFILE, engine, "regfile_tb")
        sim.run(max_time=15)
        # wr_addr should be 5 bits wide
        addr_val = sim.read("wr_addr")
        assert addr_val.width == 5, f"Expected wr_addr width 5, got {addr_val.width}"
        assert addr_val == Value(1, width=5)

    SRC_HIERARCHICAL = """\
    module sub_mod(input clk);
        parameter ENABLE_WIDE = 1;
        localparam integer IDX_BITS = (ENABLE_WIDE ? 5 : 4);
        localparam integer MEM_SIZE = (ENABLE_WIDE ? 32 : 16);
        reg [IDX_BITS-1:0] addr;
        reg [31:0] mem [0:MEM_SIZE-1];
        initial begin
            addr = 5'd7;
            mem[7] = 32'hDEAD_BEEF;
        end
    endmodule

    module hier_tb;
        reg clk = 0;
        always #5 clk = ~clk;
        sub_mod uut(.clk(clk));
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_hierarchical_parametric_width(self, engine):
        """Parametric widths work when resolved through hierarchy flattening."""
        sim = _parse_and_sim(self.SRC_HIERARCHICAL, engine, "hier_tb")
        sim.run(max_time=15)
        addr_val = sim.read("uut.addr")
        assert addr_val.width == 5, f"Expected uut.addr width 5, got {addr_val.width}"
        assert addr_val == Value(7, width=5)


# =====================================================================
# 24. Non-zero-base range declarations
# =====================================================================
# Signals declared with non-zero LSB (e.g. logic [31:1] addr) must have
# correct bit/range select semantics.  The Verilog index space starts at
# the declared LSB, not at 0.  Bug discovered in Ibex fetch FIFO where
# addr[31:1] on a [31:1] signal shifted right by 1 instead of being a
# full-range select.


class TestNonZeroBaseRange:
    """Verify bit/range selects on non-zero-base signals."""

    SRC = """\
    module nzb_tb;
        reg clk;
        initial clk = 0;
        initial while(1) #5 clk = ~clk;

        // Declared with LSB=1 (31 bits, indices 1..31)
        reg [31:1] addr_q;
        wire [31:1] addr_next;
        wire [31:0] full_addr;
        wire addr_bit1;

        // full_addr = {addr_q, 1'b0} — reads full value, no indexing issue
        assign full_addr = {addr_q, 1'b0};

        // addr_next = addr_q[31:1] + 2 — the [31:1] select must be a no-op
        assign addr_next = addr_q[31:1] + 31'd2;

        // addr_bit1 = addr_q[1] — must select the LSB (internal bit 0)
        assign addr_bit1 = addr_q[1];

        always @(posedge clk) begin
            addr_q <= addr_next;
        end

        initial begin
            addr_q = 31'd64;  // 0x40 in [31:1] domain → full_addr = 0x80
            #25;              // let 2 posedges pass
        end
    endmodule
    """

    @pytest.mark.parametrize("engine", ENGINES)
    def test_full_range_select_noop(self, engine):
        """addr_q[31:1] on a [31:1] signal returns the full value unchanged."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=2)  # before first posedge at t=5
        # Initial state: addr_q = 64, full_addr = 128 (0x80)
        assert sim.read("full_addr") == Value(0x80, width=32)
        # addr_next = addr_q[31:1] + 2 = 64 + 2 = 66
        assert sim.read("addr_next") == Value(66, width=31)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_bit_select_with_base(self, engine):
        """addr_q[1] selects the LSB of a [31:1] signal."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=2)  # before first posedge
        # addr_q = 64 = 0b1000000, bit 1 (LSB of [31:1]) = 0
        assert sim.read("addr_bit1") == Value(0, width=1)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_increment_through_register(self, engine):
        """After a posedge, addr_q increments correctly via addr_next."""
        sim = _parse_and_sim(self.SRC, engine)
        sim.run(max_time=8)  # after first posedge at t=5
        # After first posedge: addr_q = addr_next = 64+2 = 66, full_addr = 132 = 0x84
        assert sim.read("full_addr") == Value(0x84, width=32)


# =====================================================================
# Regression: LHS descending part-select direction (-:) in always @(*)
# =====================================================================
# The Lark Earley parser does not propagate start_pos/end_pos correctly
# for range_expression nodes inside variable_lvalue (LHS of blocking
# assign).  The old _extract_part_select_direction used those byte offsets
# and silently fell back to "+:" for every "-:" LHS part-select, leaving
# x-bits in the written register.  This test exercises that path.


class TestLhsDescendingPartSelect:
    """LHS -: part-select in always @(*) must produce correct bit ranges."""

    SRC = """\
module lhs_partsel (
    input  [39:0] in,
    output reg [19:0] out
);
    integer i;
    always @(*)
        for (i = 0; i < 2; i = i + 1)
            out[i*10+9-:10] = in[i*20+19-:20];
endmodule
"""

    def _sim(self, engine, tmp_path):
        """Parse SRC from a real temp file so _extract_part_select_direction
        can read it back to recover the direction token."""
        vfile = tmp_path / "lhs_partsel.v"
        vfile.write_text(self.SRC)
        vp = verilog_parser(start="source_text")
        tree = vp.build_tree(vfile)
        design = tree_to_design(tree, source_file=str(vfile))
        link_instances(design)
        resolve_port_connections(design)
        return Simulator(design.modules[0], engine=engine, design=design)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_no_x_bits(self, engine, tmp_path):
        """After driving input=0 and settling, 'out' must have no x/z bits."""
        sim = self._sim(engine, tmp_path)
        sim.drive("in", 0)
        sim.settle()
        v = sim.read("out")
        assert v.mask == 0, f"x/z bits in 'out': mask=0x{v.mask:x}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_correct_value(self, engine, tmp_path):
        """Verify bit placement: out[9:0]=in[9:0], out[19:10]=in[29:20].

        The RHS -: select extracts 20 bits; the 10-bit LHS slot keeps the
        10 LSBs of that value (standard Verilog truncation).
          i=0: out[9:0]   = truncate(in[19:0],  10) = in[9:0]
          i=1: out[19:10] = truncate(in[39:20], 10) = in[29:20]
        """
        sim = self._sim(engine, tmp_path)
        # Drive bits 9:0 = 0x155, bits 29:20 = 0x2AA, rest 0
        in_val = (0x2AA << 20) | 0x155
        sim.drive("in", in_val)
        sim.settle()
        v = sim.read("out")
        assert v.mask == 0, f"x/z bits: mask=0x{v.mask:x}"
        lo = in_val & 0x3FF          # in[9:0]   → out[9:0]
        hi = (in_val >> 20) & 0x3FF  # in[29:20] → out[19:10]
        expected = (hi << 10) | lo
        assert int(v) == expected, f"out=0x{int(v):x}, expected=0x{expected:x}"
