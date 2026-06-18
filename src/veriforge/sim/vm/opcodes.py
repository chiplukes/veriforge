"""Bytecode instruction definitions for the VM simulation engine.

Defines the Op enum (opcode values) used by the compiler and interpreter.
Each instruction is a 3-tuple: (opcode, arg1, arg2).
"""

from __future__ import annotations

from enum import IntEnum, auto


class Op(IntEnum):  # cm:5b3d7f
    """Bytecode opcodes for the VM simulation engine."""

    # ── Data movement ────────────────────────────────────────────────
    LOAD_SIG = auto()  # Push signal value: (LOAD_SIG, sig_id, 0)
    LOAD_CONST = auto()  # Push constant:     (LOAD_CONST, const_id, 0)
    STORE_SIG = auto()  # Blocking assign:   (STORE_SIG, sig_id, 0)
    NBA_SIG = auto()  # Non-blocking:      (NBA_SIG, sig_id, 0)
    STORE_BIT = auto()  # Bit-select store:  (STORE_BIT, sig_id, 0)
    NBA_BIT = auto()  # Bit-select NBA:    (NBA_BIT, sig_id, 0)
    STORE_RANGE = auto()  # Range store:       (STORE_RANGE, sig_id, 0)
    NBA_RANGE = auto()  # Range NBA:         (NBA_RANGE, sig_id, 0)
    RESIZE = auto()  # Resize TOS:        (RESIZE, width, 0)

    # ── Arithmetic ───────────────────────────────────────────────────
    ADD = auto()
    SUB = auto()
    MUL = auto()
    DIV = auto()
    MOD = auto()
    POW = auto()

    # ── Bitwise ──────────────────────────────────────────────────────
    BIT_AND = auto()
    BIT_OR = auto()
    BIT_XOR = auto()
    BIT_XNOR = auto()
    BIT_NOT = auto()
    SHL = auto()
    SHR = auto()
    ASHL = auto()
    ASHR = auto()

    # ── Comparison (produce 1-bit result) ────────────────────────────
    CMP_EQ = auto()
    CMP_NE = auto()
    CMP_LT = auto()
    CMP_LE = auto()
    CMP_GT = auto()
    CMP_GE = auto()
    CMP_CASE_EQ = auto()
    CMP_CASE_NE = auto()

    # ── Logical (produce 1-bit result) ───────────────────────────────
    LOG_AND = auto()
    LOG_OR = auto()
    LOG_NOT = auto()

    # ── Unary ────────────────────────────────────────────────────────
    NEG = auto()
    UPLUS = auto()

    # ── Reduction (produce 1-bit result) ─────────────────────────────
    RED_AND = auto()
    RED_OR = auto()
    RED_XOR = auto()
    RED_NAND = auto()
    RED_NOR = auto()
    RED_XNOR = auto()

    # ── Special expression operations ────────────────────────────────
    BIT_SELECT = auto()  # target[index]
    RANGE_SELECT = auto()  # target[msb:lsb]
    PART_SEL_UP = auto()  # target[base +: width]
    PART_SEL_DOWN = auto()  # target[base -: width]
    CONCAT = auto()  # {parts...}:        (CONCAT, n_parts, 0)
    REPLICATE = auto()  # {count{value}}

    # ── Control flow ─────────────────────────────────────────────────
    JUMP = auto()  # Unconditional:     (JUMP, addr, 0)
    JUMP_IF_ZERO = auto()  # Conditional:       (JUMP_IF_ZERO, addr, 0)
    JUMP_IF_NONZERO = auto()  # Conditional:     (JUMP_IF_NONZERO, addr, 0)
    DUP = auto()  # Duplicate TOS
    POP = auto()  # Discard TOS
    NOP = auto()  # No operation

    # ── System tasks ─────────────────────────────────────────────────
    SYS_DISPLAY = auto()  # $display:          (SYS_DISPLAY, n_args, 0)
    SYS_FINISH = auto()  # $finish/$stop
    SYS_TIME = auto()  # Push current time

    # ── Process management ───────────────────────────────────────────
    PROC_END = auto()  # End of process bytecode

    # ── Built-in functions ───────────────────────────────────────────
    FUNC_CLOG2 = auto()  # $clog2(TOS):       push ceil(log2(TOS))

    # ── Don't-care comparison (casex/casez) ──────────────────────────
    CMP_CASEX = auto()  # casex match (x/z as don't-care)
    CMP_CASEZ = auto()  # casez match (z as don't-care)

    # ── Signed comparison (produce 1-bit result) ─────────────────────
    CMP_SLT = auto()  # Signed <
    CMP_SLE = auto()  # Signed <=
    CMP_SGT = auto()  # Signed >
    CMP_SGE = auto()  # Signed >=

    # ── Memory array operations ──────────────────────────────────────
    LOAD_MEM = auto()  # Push mem[TOS]:    (LOAD_MEM, mem_id, 0)  stack: [idx] → [val]
    STORE_MEM = auto()  # Blocking store:   (STORE_MEM, mem_id, 0) stack: [val, idx] →
    NBA_MEM = auto()  # Non-blocking:     (NBA_MEM, mem_id, 0)   stack: [val, idx] →
    STORE_MEM_RANGE = auto()  # Blocking partial: (STORE_MEM_RANGE, mem_id, 0) stack: [val, idx, msb, lsb] →
    NBA_MEM_RANGE = auto()  # Non-blocking partial: (NBA_MEM_RANGE, mem_id, 0) stack: [val, idx, msb, lsb] →

    # ── Additional built-in functions ────────────────────────────────
    FUNC_RANDOM = auto()  # $random:          push pseudo-random 32-bit signed value

    # ── Sign extension ───────────────────────────────────────────────
    SIGN_EXT = auto()  # Sign-extend TOS to arg1 bits: (SIGN_EXT, target_width, 0)

    # ── System tasks ─────────────────────────────────────────────────
    SYS_READMEM = auto()  # $readmemh/$readmemb: (SYS_READMEM, task_id, 0)

    # ── Ternary merge ────────────────────────────────────────────────
    TERNARY = auto()  # cond ? true : false with x-merge: (TERNARY, 0, 0)
    # Stack: [cond, true_val, false_val] → [result]
    # If cond is fully defined: pick true_val (nonzero) or false_val (zero).
    # If cond has x/z bits: merge true_val and false_val bit-by-bit (agree→keep, disagree→x).

    # ── System tasks (monitor) ───────────────────────────────────────
    SYS_MONITOR = auto()  # $monitor: (SYS_MONITOR, n_args, 0)

    # ── File I/O ─────────────────────────────────────────────────────
    SYS_FOPEN = auto()  # $fopen: (SYS_FOPEN, task_id, 0) push fd
    SYS_FCLOSE = auto()  # $fclose: (SYS_FCLOSE, 0, 0) pop fd
    SYS_FDISPLAY = auto()  # $fdisplay: (SYS_FDISPLAY, n_args | fmt_id<<16, 0) pop fd+args
    SYS_FWRITE = auto()  # $fwrite: (SYS_FWRITE, n_args | fmt_id<<16, 0) pop fd+args
    SYS_FEOF = auto()  # $feof: (SYS_FEOF, 0, 0) pop fd, push 0/1

    # ── Signed arithmetic ──────────────────────────────────────────────
    SDIV = auto()  # Signed division (truncates toward zero)
    SMOD = auto()  # Signed modulus (matches trunc-div)


# ── Instruction constructor helpers ──────────────────────────────────


def instr(op: Op, arg1: int = 0, arg2: int = 0) -> tuple[int, int, int]:
    """Create an instruction tuple."""
    return (int(op), arg1, arg2)
