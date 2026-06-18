"""Generate RV32I instruction test firmware for SERV bit-serial RISC-V CPU.

Produces a firmware.hex file that tests all RV32I base instructions.
Each test group writes a pass(1)/fail(0) marker to a result address.
The testbench reads these markers and prints pass/fail per group.

Memory layout (2048 words = 8192 bytes, byte-addressed):
  0x000 - 0x5FF  Code (384 words max)
  0x600 - 0x63F  Test results (16 words, one per test group)
  0x700 - 0x73F  Scratch (16 words)
  0x7FC          Done flag (word 511, set to 1 when all tests complete)

Run:
    uv run python examples/serv/gen_firmware.py
"""

import struct
import sys
from pathlib import Path

# --- Register aliases ---
ZERO, RA, SP = 0, 1, 2
T0, T1, T2, T3, T4, T5 = 5, 6, 7, 28, 29, 30
A0, A1, A2 = 10, 11, 12

# --- Result memory addresses ---
RESULT_BASE = 0x600  # Word 384
SCRATCH = 0x700  # Word 448
DONE_ADDR = 0x7FC  # Word 511 — set to 1 when tests complete

# --- Test group IDs ---
TEST_LUI_AUIPC = 0
TEST_JAL_JALR = 1
TEST_BRANCH = 2
TEST_LOAD_STORE = 3
TEST_ALU_IMM = 4
TEST_ALU_REG = 5
TEST_SHIFT = 6
TEST_COMPARE = 7
TEST_LOGICAL = 8
TEST_MEMORY_BYTE = 9
TEST_MEMORY_HALF = 10
NUM_TESTS = 11

TEST_NAMES = [
    "LUI/AUIPC",
    "JAL/JALR",
    "BRANCH",
    "LOAD/STORE (word)",
    "ALU immediate",
    "ALU register",
    "SHIFT",
    "COMPARE (SLT)",
    "LOGICAL",
    "LOAD/STORE (byte)",
    "LOAD/STORE (half)",
]


# ── RV32I Instruction Encoders ──────────────────────────────────────
def _bits(val, hi, lo):
    """Extract bits [hi:lo] from val."""
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


def LH(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 1, rd, 0x03)


def LHU(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 5, rd, 0x03)


def LB(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 0, rd, 0x03)


def LBU(rd, rs1, offset):
    return i_type(offset & 0xFFF, rs1, 4, rd, 0x03)


def SW(rs2, rs1, offset):
    return s_type(offset & 0xFFF, rs2, rs1, 2)


def SH(rs2, rs1, offset):
    return s_type(offset & 0xFFF, rs2, rs1, 1)


def SB(rs2, rs1, offset):
    return s_type(offset & 0xFFF, rs2, rs1, 0)


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
    return i_type((0x400 | (shamt & 0x1F)), rs1, 5, rd, 0x13)


def ADD(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 0, rd)


def SUB(rd, rs1, rs2):
    return r_type(0x20, rs2, rs1, 0, rd)


def SLL(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 1, rd)


def SLT(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 2, rd)


def SLTU(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 3, rd)


def XOR(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 4, rd)


def SRL(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 5, rd)


def SRA(rd, rs1, rs2):
    return r_type(0x20, rs2, rs1, 5, rd)


def OR(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 6, rd)


def AND(rd, rs1, rs2):
    return r_type(0, rs2, rs1, 7, rd)


def NOP():
    return ADDI(0, 0, 0)


def LI(rd, imm):
    """Load immediate (up to 12-bit signed)."""
    return ADDI(rd, ZERO, imm & 0xFFF)


def MV(rd, rs1):
    return ADDI(rd, rs1, 0)


# ── Program builder ─────────────────────────────────────────────────
class Program:
    def __init__(self):
        self.code: list[int] = []
        self.labels: dict[str, int] = {}

    def label(self, name):
        self.labels[name] = len(self.code) * 4

    def emit(self, *insns):
        for insn in insns:
            self.code.append(insn & 0xFFFFFFFF)

    def pc(self):
        return len(self.code) * 4

    def offset_to(self, label_name):
        return self.labels[label_name] - self.pc()

    def write_hex(self, path, total_words=2048):
        """Write Verilog $readmemh compatible hex file."""
        mem = [0] * total_words
        for i, word in enumerate(self.code):
            if i >= total_words:
                raise ValueError(f"Program too large: {len(self.code)} words > {total_words}")
            mem[i] = word
        with open(path, "w") as f:
            for i, word in enumerate(mem):
                f.write(f"{word:08x}\n")
        return len(self.code)


def store_result(prog, test_id, pass_reg):
    """Store pass_reg to result area for test_id."""
    addr = RESULT_BASE + test_id * 4
    prog.emit(LI(T5, addr))
    prog.emit(SW(pass_reg, T5, 0))


def set_pass(prog, test_id):
    """Mark test as passed: store 1."""
    prog.emit(LI(T4, 1))
    store_result(prog, test_id, T4)


def assert_eq(prog, ra, rb):
    """If ra != rb, branch to self (hang)."""
    prog.emit(BNE(ra, rb, 0))


def build_program():
    """Build the complete RV32I test program."""
    p = Program()

    # Initialize result area to 0 (fail)
    p.emit(LI(T0, RESULT_BASE))
    for i in range(NUM_TESTS):
        p.emit(SW(ZERO, T0, i * 4))

    # ================================================================
    # TEST 0: LUI / AUIPC
    # ================================================================
    p.label("test_lui_auipc")
    p.emit(LUI(T1, 0x12345))
    p.emit(SRLI(T2, T1, 20))
    p.emit(LI(T3, 0x123))
    assert_eq(p, T2, T3)
    p.emit(SRLI(T2, T1, 12))
    p.emit(ANDI(T2, T2, 0xFF))
    p.emit(LI(T3, 0x45))
    assert_eq(p, T2, T3)
    p.emit(ANDI(T2, T1, 0x7FF))
    assert_eq(p, T2, ZERO)

    pc_auipc = p.pc()
    p.emit(AUIPC(T1, 0))
    p.emit(LI(T2, pc_auipc))
    assert_eq(p, T1, T2)

    set_pass(p, TEST_LUI_AUIPC)

    # ================================================================
    # TEST 1: JAL / JALR
    # ================================================================
    p.label("test_jal_jalr")
    pc_jal = p.pc()
    p.emit(JAL(RA, 8))
    p.emit(JAL(ZERO, 0))  # HANG if not skipped
    p.emit(LI(T1, pc_jal + 4))
    assert_eq(p, RA, T1)

    p.emit(AUIPC(T1, 0))
    p.emit(ADDI(T1, T1, 16))
    p.emit(JALR(RA, T1, 0))
    p.emit(JAL(ZERO, 0))  # HANG if not skipped

    set_pass(p, TEST_JAL_JALR)

    # ================================================================
    # TEST 2: BRANCH instructions
    # ================================================================
    p.label("test_branch")
    p.emit(LI(T0, 5))
    p.emit(LI(T1, 10))
    p.emit(LI(T2, 5))
    p.emit(LI(T3, -1))

    p.emit(BEQ(T0, T2, 8))
    p.emit(JAL(ZERO, 0))
    p.emit(BNE(T0, T1, 8))
    p.emit(JAL(ZERO, 0))
    p.emit(BLT(T0, T1, 8))
    p.emit(JAL(ZERO, 0))
    p.emit(BGE(T1, T0, 8))
    p.emit(JAL(ZERO, 0))
    p.emit(BLTU(T0, T1, 8))
    p.emit(JAL(ZERO, 0))
    p.emit(BGEU(T3, T1, 8))
    p.emit(JAL(ZERO, 0))
    p.emit(BLT(T3, T0, 8))
    p.emit(JAL(ZERO, 0))

    set_pass(p, TEST_BRANCH)

    # ================================================================
    # TEST 3: LOAD/STORE (word)
    # ================================================================
    p.label("test_load_store")
    p.emit(LI(T0, SCRATCH))

    p.emit(LI(T1, 0x42))
    p.emit(SW(T1, T0, 0))
    p.emit(LW(T2, T0, 0))
    assert_eq(p, T1, T2)

    p.emit(LI(T1, 0x123))
    p.emit(SW(T1, T0, 4))
    p.emit(LW(T2, T0, 4))
    assert_eq(p, T1, T2)

    p.emit(LI(T1, 0x42))
    p.emit(LW(T2, T0, 0))
    assert_eq(p, T1, T2)

    set_pass(p, TEST_LOAD_STORE)

    # ================================================================
    # TEST 4: ALU immediate
    # ================================================================
    p.label("test_alu_imm")
    p.emit(LI(T0, 100))
    p.emit(ADDI(T1, T0, 23))
    p.emit(LI(T2, 123))
    assert_eq(p, T1, T2)

    p.emit(ADDI(T1, T0, -50))
    p.emit(LI(T2, 50))
    assert_eq(p, T1, T2)

    p.emit(LI(T0, 0x0F0))
    p.emit(ORI(T1, T0, 0x00F))
    p.emit(LI(T2, 0xFF))
    assert_eq(p, T1, T2)

    p.emit(LI(T0, 0x1FF))
    p.emit(ANDI(T1, T0, 0x0F0))
    p.emit(LI(T2, 0xF0))
    assert_eq(p, T1, T2)

    p.emit(LI(T0, 0xFF))
    p.emit(XORI(T1, T0, 0x0F))
    p.emit(LI(T2, 0xF0))
    assert_eq(p, T1, T2)

    set_pass(p, TEST_ALU_IMM)

    # ================================================================
    # TEST 5: ALU register
    # ================================================================
    p.label("test_alu_reg")
    p.emit(LI(T0, 30))
    p.emit(LI(T1, 12))

    p.emit(ADD(T2, T0, T1))
    p.emit(LI(T3, 42))
    assert_eq(p, T2, T3)

    p.emit(SUB(T2, T0, T1))
    p.emit(LI(T3, 18))
    assert_eq(p, T2, T3)

    p.emit(LI(T0, 100))
    p.emit(LI(T1, -25))
    p.emit(ADD(T2, T0, T1))
    p.emit(LI(T3, 75))
    assert_eq(p, T2, T3)

    p.emit(LI(T0, 0x0F0))
    p.emit(LI(T1, 0x00F))
    p.emit(OR(T2, T0, T1))
    p.emit(LI(T3, 0xFF))
    assert_eq(p, T2, T3)

    p.emit(LI(T0, 0x1FF))
    p.emit(LI(T1, 0x0F0))
    p.emit(AND(T2, T0, T1))
    p.emit(LI(T3, 0xF0))
    assert_eq(p, T2, T3)

    p.emit(LI(T0, 0xFF))
    p.emit(LI(T1, 0x0F))
    p.emit(XOR(T2, T0, T1))
    p.emit(LI(T3, 0xF0))
    assert_eq(p, T2, T3)

    set_pass(p, TEST_ALU_REG)

    # ================================================================
    # TEST 6: SHIFT
    # ================================================================
    p.label("test_shift")
    p.emit(LI(T0, 1))
    p.emit(SLLI(T1, T0, 4))
    p.emit(LI(T2, 16))
    assert_eq(p, T1, T2)

    p.emit(LI(T0, 16))
    p.emit(SRLI(T1, T0, 2))
    p.emit(LI(T2, 4))
    assert_eq(p, T1, T2)

    p.emit(LI(T0, -16))
    p.emit(SRAI(T1, T0, 2))
    p.emit(LI(T2, -4))
    assert_eq(p, T1, T2)

    p.emit(LI(T0, 1))
    p.emit(LI(T1, 8))
    p.emit(SLL(T2, T0, T1))
    p.emit(LI(T3, 256))
    assert_eq(p, T2, T3)

    p.emit(LI(T0, 256))
    p.emit(LI(T1, 4))
    p.emit(SRL(T2, T0, T1))
    p.emit(LI(T3, 16))
    assert_eq(p, T2, T3)

    p.emit(LI(T0, -128))
    p.emit(LI(T1, 3))
    p.emit(SRA(T2, T0, T1))
    p.emit(LI(T3, -16))
    assert_eq(p, T2, T3)

    set_pass(p, TEST_SHIFT)

    # ================================================================
    # TEST 7: COMPARE (SLT, SLTU, SLTI, SLTIU)
    # ================================================================
    p.label("test_compare")
    p.emit(LI(T0, 5))
    p.emit(LI(T1, 10))
    p.emit(LI(T3, -1))

    p.emit(SLT(T2, T0, T1))
    p.emit(LI(T4, 1))
    assert_eq(p, T2, T4)

    p.emit(SLT(T2, T1, T0))
    assert_eq(p, T2, ZERO)

    p.emit(SLT(T2, T3, T0))
    p.emit(LI(T4, 1))
    assert_eq(p, T2, T4)

    p.emit(SLTU(T2, T0, T3))
    p.emit(LI(T4, 1))
    assert_eq(p, T2, T4)

    p.emit(SLTI(T2, T0, 10))
    p.emit(LI(T4, 1))
    assert_eq(p, T2, T4)

    p.emit(SLTIU(T2, T0, 10))
    p.emit(LI(T4, 1))
    assert_eq(p, T2, T4)

    set_pass(p, TEST_COMPARE)

    # ================================================================
    # TEST 8: LOGICAL (LUI-based large values)
    # ================================================================
    p.label("test_logical")
    p.emit(LUI(T0, 0xAAAAA))
    p.emit(LUI(T1, 0x55555))

    p.emit(OR(T2, T0, T1))
    p.emit(LUI(T3, 0xFFFFF))
    assert_eq(p, T2, T3)

    p.emit(AND(T2, T0, T1))
    assert_eq(p, T2, ZERO)

    p.emit(XOR(T2, T0, T1))
    p.emit(LUI(T3, 0xFFFFF))
    assert_eq(p, T2, T3)

    p.emit(XORI(T2, T0, -1))
    p.emit(LUI(T3, 0x55556))
    p.emit(ADDI(T3, T3, -1))
    assert_eq(p, T2, T3)

    set_pass(p, TEST_LOGICAL)

    # ================================================================
    # TEST 9: LOAD/STORE byte (SB, LB, LBU)
    # ================================================================
    p.label("test_memory_byte")
    p.emit(LI(T0, SCRATCH))
    p.emit(SW(ZERO, T0, 0))

    p.emit(LI(T1, 0xAB))
    p.emit(SB(T1, T0, 0))

    p.emit(LBU(T2, T0, 0))
    p.emit(LI(T3, 0xAB))
    assert_eq(p, T2, T3)

    p.emit(LB(T2, T0, 0))
    p.emit(LI(T3, -85))
    assert_eq(p, T2, T3)

    p.emit(LI(T1, 0x34))
    p.emit(SB(T1, T0, 1))
    p.emit(LBU(T2, T0, 1))
    p.emit(LI(T3, 0x34))
    assert_eq(p, T2, T3)

    p.emit(LW(T2, T0, 0))
    p.emit(LUI(T3, 0))
    p.emit(LI(T3, 0x34))
    p.emit(SLLI(T3, T3, 8))
    p.emit(ORI(T3, T3, 0xAB))
    assert_eq(p, T2, T3)

    set_pass(p, TEST_MEMORY_BYTE)

    # ================================================================
    # TEST 10: LOAD/STORE halfword (SH, LH, LHU)
    # ================================================================
    p.label("test_memory_half")
    p.emit(LI(T0, SCRATCH))
    p.emit(SW(ZERO, T0, 8))

    p.emit(LI(T1, 0x12))
    p.emit(SLLI(T1, T1, 8))
    p.emit(ORI(T1, T1, 0x34))

    p.emit(SH(T1, T0, 8))
    p.emit(LHU(T2, T0, 8))

    p.emit(LI(T3, 0x12))
    p.emit(SLLI(T3, T3, 8))
    p.emit(ORI(T3, T3, 0x34))
    assert_eq(p, T2, T3)

    p.emit(LI(T1, 0x87))
    p.emit(SLLI(T1, T1, 8))
    p.emit(ORI(T1, T1, 0x65))
    p.emit(SH(T1, T0, 10))

    p.emit(LH(T2, T0, 10))
    p.emit(SLT(T3, T2, ZERO))
    p.emit(LI(T4, 1))
    assert_eq(p, T3, T4)

    p.emit(LHU(T2, T0, 10))
    p.emit(SLT(T3, T2, ZERO))
    assert_eq(p, T3, ZERO)

    set_pass(p, TEST_MEMORY_HALF)

    # ================================================================
    # DONE: set done flag and enter infinite loop
    # ================================================================
    p.label("done")
    # Write 1 to done flag address (word 511 = 0x7FC)
    p.emit(LI(T0, DONE_ADDR))
    p.emit(LI(T1, 1))
    p.emit(SW(T1, T0, 0))
    # Infinite loop (JAL x0, 0 = jump to self)
    p.emit(JAL(ZERO, 0))

    # Verify program fits
    code_words = len(p.code)
    max_code_words = RESULT_BASE // 4
    if code_words > max_code_words:
        print(f"ERROR: Program {code_words} words exceeds code area {max_code_words} words", file=sys.stderr)
        sys.exit(1)

    return p


def main():
    p = build_program()
    out_path = Path(__file__).parent / "sim" / "firmware.hex"
    n_words = p.write_hex(out_path)
    print(f"Generated {out_path}")
    print(f"  {n_words} instruction words ({n_words * 4} bytes)")
    print(f"  {NUM_TESTS} test groups: {', '.join(TEST_NAMES)}")
    print(f"  Results at 0x{RESULT_BASE:03x} - 0x{RESULT_BASE + NUM_TESTS * 4:03x}")
    print(f"  Done flag at 0x{DONE_ADDR:03x}")

    print("\nLabels:")
    for name, addr in sorted(p.labels.items(), key=lambda x: x[1]):
        print(f"  0x{addr:03x}: {name}")

    # Verify encodings
    assert NOP() == 0x00000013, f"NOP: {NOP():#010x}"
    assert ADDI(1, 0, 1020) == 0x3FC00093, f"ADDI: {ADDI(1, 0, 1020):#010x}"
    print("\nEncoding verification: OK")


if __name__ == "__main__":
    main()
