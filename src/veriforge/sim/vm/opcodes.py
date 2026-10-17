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
    SPOW = auto()  # Signed power (IEEE 1364-2005 Table 5-6 special values)

    # ── Streaming concatenation (`{<<slice_size{...}}`) ─────────────────
    STREAM_REVERSE = auto()  # Chunk-reverse TOS: (STREAM_REVERSE, slice_size, 0)

    # ── 2-state conversion (assignment to `int`/`bit`/...) ───────────────
    CLEAR_MASK = auto()  # TOS x/z bits -> 0: (CLEAR_MASK, 0, 0)

    # ── Reals (`$__vf_real(op, a, b)`, sim/reals.py) ──────────────────
    FUNC_REAL1 = auto()  # (FUNC_REAL1, op | b << 8, 0): [a] -> [op(a, b)] (itor: b = a's width; rtoi/trunc)
    FUNC_REAL2 = auto()  # (FUNC_REAL2, op, 0): [a, b] -> [op(a, b)]


# ── Instruction constructor helpers ──────────────────────────────────


def instr(op: Op, arg1: int = 0, arg2: int = 0) -> tuple[int, int, int]:
    """Create an instruction tuple."""
    return (int(op), arg1, arg2)


# ── Stack depth analysis ─────────────────────────────────────────────

# (pops, pushes) of every opcode with a fixed stack effect.
_STACK_EFFECT: dict[int, tuple[int, int]] = {
    **dict.fromkeys(
        (Op.LOAD_SIG, Op.LOAD_CONST, Op.DUP, Op.SYS_TIME, Op.FUNC_RANDOM, Op.SYS_FOPEN),
        (0, 1),
    ),
    **dict.fromkeys(
        (
            Op.RESIZE,
            Op.SIGN_EXT,
            Op.STREAM_REVERSE,
            Op.CLEAR_MASK,
            Op.FUNC_REAL1,
            Op.BIT_NOT,
            Op.LOG_NOT,
            Op.NEG,
            Op.UPLUS,
            Op.RED_AND,
            Op.RED_OR,
            Op.RED_XOR,
            Op.RED_NAND,
            Op.RED_NOR,
            Op.RED_XNOR,
            Op.FUNC_CLOG2,
            Op.LOAD_MEM,
            Op.SYS_FEOF,
        ),
        (1, 1),
    ),
    **dict.fromkeys(
        (
            Op.ADD,
            Op.SUB,
            Op.MUL,
            Op.DIV,
            Op.MOD,
            Op.POW,
            Op.SDIV,
            Op.SMOD,
            Op.FUNC_REAL2,
            Op.SPOW,
            Op.BIT_AND,
            Op.BIT_OR,
            Op.BIT_XOR,
            Op.BIT_XNOR,
            Op.SHL,
            Op.SHR,
            Op.ASHL,
            Op.ASHR,
            Op.CMP_EQ,
            Op.CMP_NE,
            Op.CMP_LT,
            Op.CMP_LE,
            Op.CMP_GT,
            Op.CMP_GE,
            Op.CMP_CASE_EQ,
            Op.CMP_CASE_NE,
            Op.CMP_CASEX,
            Op.CMP_CASEZ,
            Op.CMP_SLT,
            Op.CMP_SLE,
            Op.CMP_SGT,
            Op.CMP_SGE,
            Op.LOG_AND,
            Op.LOG_OR,
            Op.BIT_SELECT,
            Op.REPLICATE,
        ),
        (2, 1),
    ),
    **dict.fromkeys((Op.RANGE_SELECT, Op.PART_SEL_UP, Op.PART_SEL_DOWN, Op.TERNARY), (3, 1)),
    **dict.fromkeys(
        (Op.STORE_SIG, Op.NBA_SIG, Op.JUMP_IF_ZERO, Op.JUMP_IF_NONZERO, Op.POP, Op.SYS_FCLOSE),
        (1, 0),
    ),
    **dict.fromkeys((Op.STORE_BIT, Op.NBA_BIT, Op.STORE_MEM, Op.NBA_MEM), (2, 0)),
    **dict.fromkeys((Op.STORE_RANGE, Op.NBA_RANGE), (3, 0)),
    **dict.fromkeys((Op.STORE_MEM_RANGE, Op.NBA_MEM_RANGE), (4, 0)),
    **dict.fromkeys((Op.JUMP, Op.NOP, Op.SYS_FINISH, Op.PROC_END, Op.SYS_READMEM), (0, 0)),
}


def _stack_effect(op: int, arg1: int) -> tuple[int, int]:
    if op == Op.CONCAT:
        return arg1, 1
    if op in (Op.SYS_DISPLAY, Op.SYS_MONITOR):
        return arg1 & 0xFFFF, 0
    if op in (Op.SYS_FDISPLAY, Op.SYS_FWRITE):
        return (arg1 & 0xFFFF) + 1, 0
    # An unknown opcode: assume it pushes (no opcode pushes more than one).
    return _STACK_EFFECT.get(op, (0, 1))


def max_stack_depth(program: list[tuple[int, int, int]]) -> int:
    """The most stack slots *program* can occupy at once, by following every
    path through its jumps. vm-fast's interpreter has a fixed-size stack,
    so a design with a deeper program (a 300-part concatenation, say) must
    run on the Python interpreter. Returns ``len(program) + 1`` (an upper
    bound) when the depth along some path keeps growing round a loop."""
    n = len(program)
    depth_at: list[int] = [-1] * (n + 1)
    worst = 0
    work = [(0, 0)]
    while work:
        pc, depth = work.pop()
        while pc < n and depth > depth_at[pc]:
            depth_at[pc] = depth
            op, arg1, _arg2 = program[pc]
            pops, pushes = _stack_effect(op, arg1)
            depth = max(depth - pops, 0) + pushes
            worst = max(worst, depth)
            if worst > n:
                return n + 1
            if op in (Op.PROC_END, Op.SYS_FINISH):
                break
            if op == Op.JUMP:
                pc = arg1
            else:
                if op in (Op.JUMP_IF_ZERO, Op.JUMP_IF_NONZERO):
                    work.append((arg1, depth))
                pc += 1
    return worst
