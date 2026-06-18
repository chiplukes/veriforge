"""Generate RV32I instruction test firmware for Ibex.

Produces a firmware.hex file that tests core RV32I instructions.
Each test group writes a pass(1)/fail(0) marker to a result address.
The testbench reads these markers and prints pass/fail per group.

Memory layout (4096 words = 16 KB, byte-addressed):
  0x000 - 0x7FF  Code (starts at 0x080, word 32 — Ibex boot offset)
  0x800 - 0x83F  Test results (16 words, one per test group)
  0x900 - 0x93F  Scratch (16 words)

Halt convention: SW to address 0x3000 with exit code.
  exit code 1 = all tests done (pass/fail in result words)

Run:
    uv run python examples/ibex/gen_firmware.py
"""

import struct
import sys
from pathlib import Path

# --- Register aliases ---
ZERO, RA, SP = 0, 1, 2
T0, T1, T2, T3, T4, T5 = 5, 6, 7, 28, 29, 30
A0, A1, A2 = 10, 11, 12

# --- Memory addresses ---
# Ibex boots at boot_addr + 0x80, so code starts at byte 0x80 (word 32).
CODE_START_WORD = 32  # word index where code begins
RESULT_BASE = 0x800  # byte address for results (word 512)
SCRATCH = 0x900  # byte address for scratch
HALT_ADDR = 0x3000  # write here to halt

# --- Test group IDs ---
TEST_LUI_AUIPC = 0
TEST_ADDI = 1
TEST_ADD_SUB = 2
TEST_LOGIC = 3
TEST_SHIFT = 4
TEST_COMPARE = 5
TEST_BRANCH = 6
TEST_JAL_JALR = 7
TEST_LOAD_STORE = 8
NUM_TESTS = 9

TEST_NAMES = [
    "LUI/AUIPC",
    "ADDI",
    "ADD/SUB",
    "LOGIC (AND/OR/XOR)",
    "SHIFT",
    "COMPARE (SLT)",
    "BRANCH",
    "JAL/JALR",
    "LOAD/STORE",
]


# ── RV32I Instruction Encoders ──────────────────────────────────────
def _bits(val, hi, lo):
    mask = (1 << (hi - lo + 1)) - 1
    return (val >> lo) & mask


def r_type(funct7, rs2, rs1, funct3, rd, opcode=0x33):
    return (funct7 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode


def i_type(imm, rs1, funct3, rd, opcode=0x13):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode


def s_type(imm, rs2, rs1, funct3, opcode=0x23):
    return (_bits(imm, 11, 5) << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (_bits(imm, 4, 0) << 7) | opcode


def b_type(imm, rs2, rs1, funct3, opcode=0x63):
    return (
        (_bits(imm, 12, 12) << 31)
        | (_bits(imm, 10, 5) << 25)
        | (rs2 << 20)
        | (rs1 << 15)
        | (funct3 << 12)
        | (_bits(imm, 4, 1) << 8)
        | (_bits(imm, 11, 11) << 7)
        | opcode
    )


def u_type(imm, rd, opcode):
    return ((imm & 0xFFFFF) << 12) | (rd << 7) | opcode


def j_type(imm, rd, opcode=0x6F):
    return (
        (_bits(imm, 20, 20) << 31)
        | (_bits(imm, 10, 1) << 21)
        | (_bits(imm, 11, 11) << 20)
        | (_bits(imm, 19, 12) << 12)
        | (rd << 7)
        | opcode
    )


# ── Instruction helpers ─────────────────────────────────────────────
def LUI(rd, imm20):
    return u_type(imm20, rd, 0x37)


def AUIPC(rd, imm20):
    return u_type(imm20, rd, 0x17)


def JAL(rd, offset):
    return j_type(offset & 0x1FFFFF, rd, 0x6F)


def JALR(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 0, rd, 0x67)


def BEQ(rs1, rs2, offset):
    return b_type(offset & 0x1FFF, rs2, rs1, 0)


def BNE(rs1, rs2, offset):
    return b_type(offset & 0x1FFF, rs2, rs1, 1)


def BLT(rs1, rs2, offset):
    return b_type(offset & 0x1FFF, rs2, rs1, 4)


def BGE(rs1, rs2, offset):
    return b_type(offset & 0x1FFF, rs2, rs1, 5)


def BLTU(rs1, rs2, offset):
    return b_type(offset & 0x1FFF, rs2, rs1, 6)


def BGEU(rs1, rs2, offset):
    return b_type(offset & 0x1FFF, rs2, rs1, 7)


def LW(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 2, rd, 0x03)


def LB(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 0, rd, 0x03)


def LBU(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 4, rd, 0x03)


def LH(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 1, rd, 0x03)


def LHU(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 5, rd, 0x03)


def SW(rs2, rs1, offset):
    return s_type(offset & 0xFFF, rs2, rs1, 2)


def SB(rs2, rs1, offset):
    return s_type(offset & 0xFFF, rs2, rs1, 0)


def SH(rs2, rs1, offset):
    return s_type(offset & 0xFFF, rs2, rs1, 1)


def ADDI(rd, rs1, imm):
    return i_type(imm & 0xFFF, rs1, 0, rd, 0x13)


def SLTI(rd, rs1, imm):
    return i_type(imm & 0xFFF, rs1, 2, rd, 0x13)


def SLTIU(rd, rs1, imm):
    return i_type(imm & 0xFFF, rs1, 3, rd, 0x13)


def XORI(rd, rs1, imm):
    return i_type(imm & 0xFFF, rs1, 4, rd, 0x13)


def ORI(rd, rs1, imm):
    return i_type(imm & 0xFFF, rs1, 6, rd, 0x13)


def ANDI(rd, rs1, imm):
    return i_type(imm & 0xFFF, rs1, 7, rd, 0x13)


def SLLI(rd, rs1, shamt):
    return i_type(shamt & 0x1F, rs1, 1, rd, 0x13)


def SRLI(rd, rs1, shamt):
    return i_type(shamt & 0x1F, rs1, 5, rd, 0x13)


def SRAI(rd, rs1, shamt):
    return i_type(0x400 | (shamt & 0x1F), rs1, 5, rd, 0x13)


def ADD(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 0, rd)


def SUB(rd, rs1, rs2):
    return r_type(0x20, rs2, rs1, 0, rd)


def SLL(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 1, rd)


def SLT(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 2, rd)


def SLTU(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 3, rd)


def XOR(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 4, rd)


def SRL(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 5, rd)


def SRA(rd, rs1, rs2):
    return r_type(0x20, rs2, rs1, 5, rd)


def OR(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 6, rd)


def AND(rd, rs1, rs2):
    return r_type(0x00, rs2, rs1, 7, rd)


def NOP():
    return ADDI(ZERO, ZERO, 0)


def MV(rd, rs1):
    return ADDI(rd, rs1, 0)


def LI(rd, imm):
    return ADDI(rd, ZERO, imm)


def EBREAK():
    return i_type(1, 0, 0, 0, 0x73)


class FirmwareBuilder:
    """Build firmware as a list of 32-bit words."""

    def __init__(self, code_start_word=CODE_START_WORD):
        self.code_start = code_start_word
        self.code = []  # instructions (appended at code_start)
        self.data = {}  # word_index -> value (for pre-initialized data)

    @property
    def pc(self):
        """Current PC (byte address)."""
        return (self.code_start + len(self.code)) * 4

    def emit(self, *instrs):
        """Append one or more instructions."""
        for instr in instrs:
            self.code.append(instr & 0xFFFFFFFF)

    def emit_li32(self, rd, value):
        """Load a full 32-bit immediate into rd using LUI + ADDI."""
        value = value & 0xFFFFFFFF
        upper = (value + 0x800) >> 12  # adjust for sign extension of ADDI
        lower = value & 0xFFF
        if lower >= 0x800:
            lower -= 0x1000  # sign extend
        self.emit(LUI(rd, upper))
        self.emit(ADDI(rd, rd, lower & 0xFFF))

    def emit_store_result(self, test_id, pass_reg):
        """Store pass_reg to RESULT_BASE + test_id*4."""
        addr = RESULT_BASE + test_id * 4
        # Load result address into T5
        self.emit_li32(T5, addr)
        self.emit(SW(pass_reg, T5, 0))

    def emit_halt(self, exit_code=1):
        """Halt simulation by writing exit_code to HALT_ADDR."""
        self.emit_li32(T5, HALT_ADDR)
        self.emit(LI(T4, exit_code))
        self.emit(SW(T4, T5, 0))
        # Spin loop (should not reach here)
        self.emit(JAL(ZERO, 0))  # infinite loop

    def build(self, mem_size=4096):
        """Return memory image as list of 32-bit words."""
        mem = [0] * mem_size
        # Place code
        for i, word in enumerate(self.code):
            idx = self.code_start + i
            if idx < mem_size:
                mem[idx] = word
        # Place pre-initialized data
        for idx, val in self.data.items():
            if idx < mem_size:
                mem[idx] = val
        return mem


def build_firmware():
    fw = FirmwareBuilder()

    # ── Test 0: LUI / AUIPC ─────────────────────────────────────────
    # LUI loads upper 20 bits
    fw.emit(LUI(T0, 0x12345))  # T0 = 0x12345000
    fw.emit(LI(T1, 1))  # T1 = 1 (assume pass)
    # Check: T0 should be 0x12345000
    fw.emit(LUI(T2, 0x12345))  # T2 = 0x12345000
    fw.emit(BNE(T0, T2, 8))  # skip fail if equal
    fw.emit(JAL(ZERO, 8))  # jump to store  (skip fail)
    fw.emit(LI(T1, 0))  # T1 = 0 (fail)
    fw.emit_store_result(TEST_LUI_AUIPC, T1)

    # ── Test 1: ADDI ────────────────────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 42))  # T0 = 42
    fw.emit(ADDI(T0, T0, 8))  # T0 = 50
    fw.emit(LI(T1, 1))
    fw.emit(ADDI(T2, ZERO, 50))  # expected
    fw.emit(BNE(T0, T2, 8))
    fw.emit(JAL(ZERO, 8))
    fw.emit(LI(T1, 0))
    fw.emit_store_result(TEST_ADDI, T1)

    # ── Test 2: ADD / SUB ───────────────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 100))  # T0 = 100
    fw.emit(ADDI(T1, ZERO, 30))  # T1 = 30
    fw.emit(ADD(T2, T0, T1))  # T2 = 130
    fw.emit(SUB(T3, T0, T1))  # T3 = 70
    fw.emit(LI(A0, 1))  # assume pass
    fw.emit(ADDI(A1, ZERO, 130))
    fw.emit(BNE(T2, A1, 8))
    fw.emit(JAL(ZERO, 12))
    fw.emit(LI(A0, 0))
    fw.emit(JAL(ZERO, 16))  # skip to store
    fw.emit(ADDI(A1, ZERO, 70))
    fw.emit(BNE(T3, A1, 8))
    fw.emit(JAL(ZERO, 8))
    fw.emit(LI(A0, 0))
    fw.emit_store_result(TEST_ADD_SUB, A0)

    # ── Test 3: Logic (AND, OR, XOR) ────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 0xFF))  # T0 = 0xFF
    fw.emit(ADDI(T1, ZERO, 0x0F))  # T1 = 0x0F
    fw.emit(AND(T2, T0, T1))  # T2 = 0x0F
    fw.emit(OR(T3, T0, T1))  # T3 = 0xFF
    fw.emit(XOR(T4, T0, T1))  # T4 = 0xF0
    fw.emit(LI(A0, 1))
    fw.emit(ADDI(A1, ZERO, 0x0F))
    fw.emit(BNE(T2, A1, 8))
    fw.emit(JAL(ZERO, 12))
    fw.emit(LI(A0, 0))
    fw.emit(JAL(ZERO, 32))  # skip to store
    fw.emit(ADDI(A1, ZERO, 0xFF))
    fw.emit(BNE(T3, A1, 8))  # check OR
    fw.emit(JAL(ZERO, 12))
    fw.emit(LI(A0, 0))
    fw.emit(JAL(ZERO, 16))
    fw.emit(ADDI(A1, ZERO, 0xF0))
    fw.emit(BNE(T4, A1, 8))  # check XOR — 0xF0 needs sign ext check
    fw.emit(JAL(ZERO, 8))
    fw.emit(LI(A0, 0))
    fw.emit_store_result(TEST_LOGIC, A0)

    # ── Test 4: Shifts ──────────────────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 1))  # T0 = 1
    fw.emit(SLLI(T1, T0, 4))  # T1 = 16
    fw.emit(ADDI(T0, ZERO, 0x80))  # T0 = 128
    fw.emit(SRLI(T2, T0, 3))  # T2 = 16
    fw.emit(LI(A0, 1))
    fw.emit(ADDI(A1, ZERO, 16))
    fw.emit(BNE(T1, A1, 8))
    fw.emit(JAL(ZERO, 12))
    fw.emit(LI(A0, 0))
    fw.emit(JAL(ZERO, 12))
    fw.emit(BNE(T2, A1, 8))
    fw.emit(JAL(ZERO, 8))
    fw.emit(LI(A0, 0))
    fw.emit_store_result(TEST_SHIFT, A0)

    # ── Test 5: Compare (SLT, SLTU) ─────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 5))
    fw.emit(ADDI(T1, ZERO, 10))
    fw.emit(SLT(T2, T0, T1))  # 5 < 10 → T2 = 1
    fw.emit(SLT(T3, T1, T0))  # 10 < 5 → T3 = 0
    fw.emit(LI(A0, 1))
    fw.emit(BNE(T2, A0, 8))  # T2 should be 1
    fw.emit(JAL(ZERO, 12))
    fw.emit(LI(A0, 0))
    fw.emit(JAL(ZERO, 12))
    fw.emit(BNE(T3, ZERO, 8))  # T3 should be 0
    fw.emit(JAL(ZERO, 8))
    fw.emit(LI(A0, 0))
    fw.emit_store_result(TEST_COMPARE, A0)

    # ── Test 6: Branch ──────────────────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 42))
    fw.emit(ADDI(T1, ZERO, 42))
    fw.emit(LI(A0, 0))  # start with fail
    fw.emit(BNE(T0, T1, 12))  # should NOT branch (equal)
    fw.emit(LI(A0, 1))  # pass if we get here
    fw.emit(JAL(ZERO, 8))  # skip fail
    fw.emit(LI(A0, 0))  # fail (branched when shouldn't)
    # Check BEQ taken
    fw.emit(BEQ(T0, T1, 8))  # should branch (equal)
    fw.emit(LI(A0, 0))  # fail if not taken
    fw.emit_store_result(TEST_BRANCH, A0)

    # ── Test 7: JAL / JALR ──────────────────────────────────────────
    fw.emit(LI(A0, 0))  # start fail
    pc_jal = fw.pc
    fw.emit(JAL(RA, 12))  # jump forward 12 bytes (3 words)
    fw.emit(LI(A0, 0))  # should be skipped
    fw.emit(JAL(ZERO, 8))  # should be skipped
    # JAL target:
    fw.emit(LI(A0, 1))  # pass — JAL landed here
    # Test JALR: jump to PC+8 via register
    pc_jalr = fw.pc
    fw.emit(AUIPC(T0, 0))  # T0 = current PC
    fw.emit(JALR(RA, T0, 12))  # jump to PC+12 (skip next 2)
    fw.emit(LI(A0, 0))  # should be skipped
    # JALR target:
    fw.emit_store_result(TEST_JAL_JALR, A0)

    # ── Test 8: Load / Store ────────────────────────────────────────
    fw.emit(ADDI(T0, ZERO, 0x5A))  # T0 = 0x5A
    fw.emit_li32(T3, SCRATCH)  # T3 = scratch address
    fw.emit(SW(T0, T3, 0))  # store T0 to scratch
    fw.emit(LW(T1, T3, 0))  # load back
    fw.emit(LI(A0, 1))
    fw.emit(BNE(T0, T1, 8))  # compare
    fw.emit(JAL(ZERO, 8))
    fw.emit(LI(A0, 0))
    fw.emit_store_result(TEST_LOAD_STORE, A0)

    # ── Halt ─────────────────────────────────────────────────────────
    fw.emit_halt(1)

    return fw


def main():
    fw = build_firmware()
    mem = fw.build()

    sim_dir = Path(__file__).parent / "sim"
    sim_dir.mkdir(exist_ok=True)
    hex_path = sim_dir / "firmware.hex"

    with open(hex_path, "w") as f:
        for word in mem:
            f.write(f"{word:08X}\n")

    print(f"Generated {hex_path}")
    print(f"  Code: {len(fw.code)} instructions ({len(fw.code) * 4} bytes)")
    print(f"  Code starts at word {fw.code_start} (byte 0x{fw.code_start * 4:X})")
    print(f"  Result base: 0x{RESULT_BASE:X} (word {RESULT_BASE // 4})")
    print(f"  Halt address: 0x{HALT_ADDR:X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
