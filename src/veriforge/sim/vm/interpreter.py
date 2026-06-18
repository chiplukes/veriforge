"""Bytecode interpreter for the VM simulation engine.

Executes a flat instruction array against signal storage arrays and a
value stack. This is the inner loop of the simulation — its performance
directly determines simulation throughput.

Design for speed:
- Single while loop, no recursion
- Local variable caching for stack operations
- Integer-indexed signal access (no dict lookups)
- Constant pool: Value objects created once at compile time
"""

from __future__ import annotations

from ..value import Value, _mask_for_width
from .opcodes import Op

import random as _random


class StopSimulation(Exception):
    """Raised by SYS_FINISH to halt the simulation."""

    __slots__ = ()


def _format_display(args: list, fmt_id: int, display_formats: list[str], sim_time: int) -> str:  # noqa: PLR0912, PLR0915
    """Format $display arguments with optional Verilog format string.

    Args:
        args: List of Value objects (data arguments).
        fmt_id: Format string ID (1-indexed), 0 = no format string.
        display_formats: The format string table.
        sim_time: Current simulation time (for %t).

    Returns:
        Formatted string.
    """
    if fmt_id == 0 or fmt_id > len(display_formats):
        # No format string: just join values with spaces
        parts: list[str] = []
        for v in args:
            if v.is_defined:
                parts.append(str(int(v)))
            else:
                parts.append(str(v))
        return " ".join(parts)

    fmt = display_formats[fmt_id - 1]
    result: list[str] = []
    arg_idx = 0
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch == "%":
            i += 1
            if i >= len(fmt):
                break
            # Parse optional zero-pad flag and width
            zero_pad = False
            if fmt[i] == "0":
                zero_pad = True
                i += 1
                if i >= len(fmt):
                    break
            width = 0
            while i < len(fmt) and fmt[i].isdigit():
                width = width * 10 + int(fmt[i])
                i += 1
            if i >= len(fmt):
                break
            spec = fmt[i].lower()
            i += 1
            if spec == "%":
                result.append("%")
                continue
            if spec == "t":
                # %t doesn't consume an argument — uses sim_time
                result.append(str(sim_time))
                continue
            if spec == "m":
                # %m doesn't consume an argument — module path placeholder
                result.append("<module>")
                continue
            if arg_idx < len(args):
                v = args[arg_idx]
                arg_idx += 1
                fill = "0" if zero_pad else " "
                if spec == "d":
                    if v.is_defined:
                        s = str(int(v))
                        result.append(s.rjust(width, fill) if width else s)
                    else:
                        result.append("x")
                elif spec in ("h", "x"):
                    if v.is_defined:
                        s = format(int(v), "x")
                        result.append(s.rjust(width, fill) if width else s)
                    else:
                        result.append("x")
                elif spec == "b":
                    if v.is_defined:
                        s = format(int(v), "b")
                        result.append(s.rjust(width, fill) if width else s)
                    else:
                        result.append("x")
                elif spec == "o":
                    if v.is_defined:
                        s = format(int(v), "o")
                        result.append(s.rjust(width, fill) if width else s)
                    else:
                        result.append("x")
                elif spec == "c":
                    if v.is_defined:
                        result.append(chr(int(v) & 0xFF))
                    else:
                        result.append("?")
                elif spec == "s":
                    # String: interpret value bytes as ASCII
                    if v.is_defined:
                        n = int(v)
                        chars = []
                        while n:
                            chars.append(chr(n & 0xFF))
                            n >>= 8
                        result.append("".join(reversed(chars)))
                    else:
                        result.append("")
                else:
                    # Unknown spec, treat as literal
                    arg_idx -= 1
                    result.append("%" + spec)
            else:
                result.append("%" + spec)
        elif ch == "\\":
            i += 1
            if i < len(fmt):
                esc = fmt[i]
                i += 1
                if esc == "n":
                    result.append("\n")
                elif esc == "t":
                    result.append("\t")
                elif esc == "\\":
                    result.append("\\")
                else:
                    result.append("\\" + esc)
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def _signed_cmp(a: Value, b: Value, op: str) -> Value:
    """Signed relational comparison, interpreting values as two's-complement."""
    if a.mask or b.mask:
        return Value.x(1)
    sa, sb = a.as_signed(), b.as_signed()
    if op == "<":
        result = sa < sb
    elif op == "<=":
        result = sa <= sb
    elif op == ">":
        result = sa > sb
    else:
        result = sa >= sb
    return Value(1 if result else 0, width=1)


class Interpreter:  # cm:e3f1b4
    """Execute bytecode programs against signal storage.

    Attributes:
        sig_val:    Flat array of signal integer values.
        sig_mask:   Flat array of signal x/z masks.
        sig_width:  Flat array of signal bit widths (constant).
        const_pool: List of constant Value objects (from compiler).
        nba_queue:  List of (sig_id, Value) pending non-blocking assigns.
        display_output: List of $display output strings.
        time:       Current simulation time (for $time).
        dirty:      Set of signal IDs written during this execution.
    """

    __slots__ = (
        "_next_fd",
        "active_monitor_id",
        "const_pool",
        "dirty",
        "display_formats",
        "display_output",
        "file_handles",
        "fopen_tasks",
        "loop_limit",
        "mem_info",
        "mem_mask",
        "mem_val",
        "nba_mem_queue",
        "nba_mem_range_queue",
        "nba_queue",
        "readmem_tasks",
        "sig_mask",
        "sig_val",
        "sig_width",
        "time",
    )

    def __init__(
        self,
        sig_val: list[int],
        sig_mask: list[int],
        sig_width: list[int],
        const_pool: list[Value],
        *,
        loop_limit: int = 100_000,
    ) -> None:
        self.sig_val = sig_val
        self.sig_mask = sig_mask
        self.sig_width = sig_width
        self.const_pool = const_pool
        self.nba_queue: list[tuple[int, Value]] = []
        self.nba_mem_queue: list[tuple[int, int, Value]] = []  # (mem_id, addr, val)
        self.nba_mem_range_queue: list[tuple[int, int, int, int, Value]] = []  # (mem_id, addr, msb, lsb, val)
        self.display_output: list[str] = []
        self.time: int = 0
        self.dirty: set[int] = set()
        self.loop_limit = loop_limit
        self.active_monitor_id: int = -1  # -1 = no active monitor

        # Memory storage (set by scheduler if memories exist)
        self.mem_val: list[int] = []
        self.mem_mask: list[int] = []
        self.mem_info: list[tuple[int, int, int]] = []  # (elem_width, depth, base_addr)

        # $readmemh/$readmemb task table (set by scheduler from compiler)
        self.readmem_tasks: list[tuple[str, int, bool]] = []  # (filename, mem_id, is_hex)

        # $fopen task table (set by scheduler from compiler)
        self.fopen_tasks: list[tuple[str, str]] = []  # (filename, mode)

        # Open file handles: fd → file object (fd is small integer, starting at 1)
        self.file_handles: dict[int, object] = {}
        self._next_fd: int = 1

        # $display/$monitor format string table (set by scheduler from compiler)
        self.display_formats: list[str] = []

    def execute(self, program: list[tuple[int, int, int]]) -> None:  # noqa: PLR0912, PLR0915
        """Execute a bytecode program.

        Runs the program from instruction 0 until PROC_END or end of array.
        Updates sig_val/sig_mask in place. Appends to nba_queue for NBAs.

        Raises:
            StopSimulation: When SYS_FINISH is executed.
        """
        # Cache locals for speed
        stack: list[Value] = []
        s_append = stack.append
        s_pop = stack.pop
        sig_val = self.sig_val
        sig_mask = self.sig_mask
        sig_width = self.sig_width
        const_pool = self.const_pool
        nba_queue = self.nba_queue
        dirty = self.dirty
        prog_len = len(program)
        loop_limit = self.loop_limit
        loop_iters = 0
        pc = 0

        while pc < prog_len:
            op, arg1, _arg2 = program[pc]
            pc += 1

            # ── Data movement ────────────────────────────────────

            if op == Op.LOAD_SIG:
                s_append(Value(sig_val[arg1], width=sig_width[arg1], mask=sig_mask[arg1]))
                continue

            if op == Op.LOAD_CONST:
                s_append(const_pool[arg1])
                continue

            if op == Op.STORE_SIG:
                val = s_pop()
                w = sig_width[arg1]
                wmask = _mask_for_width(w)
                new_val = val.val & wmask & ~val.mask
                new_mask = val.mask & wmask
                if sig_val[arg1] != new_val or sig_mask[arg1] != new_mask:
                    sig_val[arg1] = new_val
                    sig_mask[arg1] = new_mask
                    dirty.add(arg1)
                continue

            if op == Op.NBA_SIG:
                val = s_pop()
                w = sig_width[arg1]
                wmask = _mask_for_width(w)
                nba_val = Value(val.val & wmask, width=w, mask=val.mask & wmask)
                nba_queue.append((arg1, nba_val))
                continue

            if op == Op.RESIZE:
                val = s_pop()
                s_append(val.resize(arg1))
                continue

            if op == Op.STORE_BIT:
                idx = s_pop()
                val = s_pop()
                if idx.is_defined:
                    i = idx.val
                    w = sig_width[arg1]
                    if 0 <= i < w:
                        current = Value(sig_val[arg1], width=w, mask=sig_mask[arg1])
                        updated = current.set_bit(i, val.val & 1)
                        if sig_val[arg1] != updated.val or sig_mask[arg1] != updated.mask:
                            sig_val[arg1] = updated.val
                            sig_mask[arg1] = updated.mask
                            dirty.add(arg1)
                continue

            if op == Op.NBA_BIT:
                idx = s_pop()
                val = s_pop()
                if idx.is_defined:
                    i = idx.val
                    w = sig_width[arg1]
                    if 0 <= i < w:
                        bit_val = Value(val.val & 1, width=1, mask=val.mask & 1)
                        nba_queue.append((arg1, i, i, bit_val))
                continue

            if op == Op.STORE_RANGE:
                lsb = s_pop()
                msb = s_pop()
                val = s_pop()
                if msb.is_defined and lsb.is_defined:
                    w = sig_width[arg1]
                    current = Value(sig_val[arg1], width=w, mask=sig_mask[arg1])
                    updated = current.set_range(msb.val, lsb.val, val)
                    if sig_val[arg1] != updated.val or sig_mask[arg1] != updated.mask:
                        sig_val[arg1] = updated.val
                        sig_mask[arg1] = updated.mask
                        dirty.add(arg1)
                continue

            if op == Op.NBA_RANGE:
                lsb = s_pop()
                msb = s_pop()
                val = s_pop()
                if msb.is_defined and lsb.is_defined:
                    # Store as (sid, msb, lsb, field_val) tuple; apply_nba resolves lazily
                    nba_queue.append((arg1, msb.val, lsb.val, val))
                continue

            if op == Op.SIGN_EXT:
                v = s_pop()
                s_append(v.sign_extend(arg1))
                continue

            # ── Arithmetic ───────────────────────────────────────

            if op == Op.ADD:
                b = s_pop()
                a = s_pop()
                s_append(a + b)
                continue

            if op == Op.SUB:
                b = s_pop()
                a = s_pop()
                s_append(a - b)
                continue

            if op == Op.MUL:
                b = s_pop()
                a = s_pop()
                s_append(a * b)
                continue

            if op == Op.DIV:
                b = s_pop()
                a = s_pop()
                s_append(a // b)
                continue

            if op == Op.MOD:
                b = s_pop()
                a = s_pop()
                s_append(a % b)
                continue

            if op == Op.SDIV:
                b = s_pop()
                a = s_pop()
                if a.mask or b.mask or b.val == 0:
                    s_append(Value.x(a.width))
                    continue
                sa = a.as_signed()
                sb = b.as_signed()
                s_append(Value(int(sa / sb), width=a.width))
                continue

            if op == Op.SMOD:
                b = s_pop()
                a = s_pop()
                if a.mask or b.mask or b.val == 0:
                    s_append(Value.x(a.width))
                    continue
                sa = a.as_signed()
                sb = b.as_signed()
                s_append(Value(sa - sb * int(sa / sb), width=a.width))
                continue

            if op == Op.POW:
                b = s_pop()
                a = s_pop()
                s_append(a**b)
                continue

            # ── Bitwise ──────────────────────────────────────────

            if op == Op.BIT_AND:
                b = s_pop()
                a = s_pop()
                s_append(a & b)
                continue

            if op == Op.BIT_OR:
                b = s_pop()
                a = s_pop()
                s_append(a | b)
                continue

            if op == Op.BIT_XOR:
                b = s_pop()
                a = s_pop()
                s_append(a ^ b)
                continue

            if op == Op.BIT_XNOR:
                b = s_pop()
                a = s_pop()
                s_append(~(a ^ b))
                continue

            if op == Op.BIT_NOT:
                a = s_pop()
                s_append(~a)
                continue

            if op == Op.SHL:
                b = s_pop()
                a = s_pop()
                s_append(a << b)
                continue

            if op == Op.SHR:
                b = s_pop()
                a = s_pop()
                s_append(a >> b)
                continue

            if op == Op.ASHL:
                b = s_pop()
                a = s_pop()
                s_append(a << b)
                continue

            if op == Op.ASHR:
                b = s_pop()
                a = s_pop()
                # Arithmetic shift right (sign-extend)
                if isinstance(b, Value):
                    if b.mask:
                        s_append(Value.x(a.width))
                        continue
                    shift = b.val
                else:
                    shift = b
                if a.mask:
                    s_append(Value.x(a.width))
                    continue
                signed_val = a.as_signed()
                result = signed_val >> shift
                s_append(Value(result, width=a.width))
                continue

            # ── Comparison ───────────────────────────────────────

            if op == Op.CMP_EQ:
                b = s_pop()
                a = s_pop()
                s_append(a.eq(b))
                continue

            if op == Op.CMP_NE:
                b = s_pop()
                a = s_pop()
                s_append(a.ne(b))
                continue

            if op == Op.CMP_LT:
                b = s_pop()
                a = s_pop()
                s_append(a.lt(b))
                continue

            if op == Op.CMP_LE:
                b = s_pop()
                a = s_pop()
                s_append(a.le(b))
                continue

            if op == Op.CMP_GT:
                b = s_pop()
                a = s_pop()
                s_append(a.gt(b))
                continue

            if op == Op.CMP_GE:
                b = s_pop()
                a = s_pop()
                s_append(a.ge(b))
                continue

            if op == Op.CMP_CASE_EQ:
                b = s_pop()
                a = s_pop()
                s_append(a.case_eq(b))
                continue

            if op == Op.CMP_CASE_NE:
                b = s_pop()
                a = s_pop()
                s_append(a.case_ne(b))
                continue

            # ── Signed comparison ────────────────────────────────

            if op == Op.CMP_SLT:
                b = s_pop()
                a = s_pop()
                s_append(_signed_cmp(a, b, "<"))
                continue

            if op == Op.CMP_SLE:
                b = s_pop()
                a = s_pop()
                s_append(_signed_cmp(a, b, "<="))
                continue

            if op == Op.CMP_SGT:
                b = s_pop()
                a = s_pop()
                s_append(_signed_cmp(a, b, ">"))
                continue

            if op == Op.CMP_SGE:
                b = s_pop()
                a = s_pop()
                s_append(_signed_cmp(a, b, ">="))
                continue

            # ── Logical ──────────────────────────────────────────

            if op == Op.LOG_AND:
                b = s_pop()
                a = s_pop()
                s_append(a.logical_and(b))
                continue

            if op == Op.LOG_OR:
                b = s_pop()
                a = s_pop()
                s_append(a.logical_or(b))
                continue

            if op == Op.LOG_NOT:
                a = s_pop()
                s_append(a.logical_not())
                continue

            # ── Unary ────────────────────────────────────────────

            if op == Op.NEG:
                a = s_pop()
                s_append(-a)
                continue

            if op == Op.UPLUS:
                # Identity — leave stack unchanged
                continue

            # ── Reduction ────────────────────────────────────────

            if op == Op.RED_AND:
                a = s_pop()
                s_append(a.reduce_and())
                continue

            if op == Op.RED_OR:
                a = s_pop()
                s_append(a.reduce_or())
                continue

            if op == Op.RED_XOR:
                a = s_pop()
                s_append(a.reduce_xor())
                continue

            if op == Op.RED_NAND:
                a = s_pop()
                s_append(a.reduce_nand())
                continue

            if op == Op.RED_NOR:
                a = s_pop()
                s_append(a.reduce_nor())
                continue

            if op == Op.RED_XNOR:
                a = s_pop()
                s_append(a.reduce_xnor())
                continue

            # ── Special expression operations ────────────────────

            if op == Op.BIT_SELECT:
                index = s_pop()
                target = s_pop()
                if index.is_defined:
                    s_append(target[index.val])
                else:
                    s_append(Value.x(1))
                continue

            if op == Op.RANGE_SELECT:
                lsb = s_pop()
                msb = s_pop()
                target = s_pop()
                if msb.is_defined and lsb.is_defined:
                    s_append(target[msb.val : lsb.val])
                else:
                    w = (msb.val - lsb.val + 1) if msb.is_defined and lsb.is_defined else 1
                    s_append(Value.x(w))
                continue

            if op == Op.PART_SEL_UP:
                width = s_pop()
                base = s_pop()
                target = s_pop()
                if base.is_defined and width.is_defined:
                    w = width.val
                    b = base.val
                    s_append(target[b + w - 1 : b])
                else:
                    s_append(Value.x(1))
                continue

            if op == Op.PART_SEL_DOWN:
                width = s_pop()
                base = s_pop()
                target = s_pop()
                if base.is_defined and width.is_defined:
                    w = width.val
                    b = base.val
                    s_append(target[b : b - w + 1])
                else:
                    s_append(Value.x(1))
                continue

            if op == Op.CONCAT:
                n_parts = arg1
                parts = [s_pop() for _ in range(n_parts)]
                parts.reverse()  # Stack order is reversed
                if parts:
                    result = parts[0]
                    for p in parts[1:]:
                        result = result.concat(p)
                    s_append(result)
                else:
                    s_append(Value(0, width=0))
                continue

            if op == Op.REPLICATE:
                value = s_pop()
                count = s_pop()
                if count.is_defined:
                    s_append(value.replicate(count.val))
                else:
                    s_append(Value.x(value.width))
                continue

            # ── Control flow ─────────────────────────────────────

            if op == Op.JUMP:
                if arg1 <= pc - 1:
                    loop_iters += 1
                    if loop_iters > loop_limit:
                        raise RuntimeError(f"Loop exceeded {loop_limit} iterations (backward jump at pc={pc - 1})")
                pc = arg1
                continue

            if op == Op.JUMP_IF_ZERO:
                val = s_pop()
                if not val.is_defined:
                    pc = arg1  # x/z treated as false
                elif val.val == 0:
                    pc = arg1
                continue

            if op == Op.JUMP_IF_NONZERO:
                val = s_pop()
                if val.is_defined and val.val != 0:
                    pc = arg1
                continue

            if op == Op.DUP:
                s_append(stack[-1])
                continue

            if op == Op.POP:
                if stack:
                    s_pop()
                continue

            if op == Op.NOP:
                continue

            # ── System tasks ─────────────────────────────────────

            if op == Op.SYS_DISPLAY:
                n_args = arg1 & 0xFFFF
                fmt_id = _arg2
                args = [s_pop() for _ in range(n_args)]
                args.reverse()
                self.display_output.append(_format_display(args, fmt_id, self.display_formats, self.time))
                continue

            if op == Op.SYS_FINISH:
                raise StopSimulation()

            if op == Op.SYS_TIME:
                s_append(Value(self.time, width=64))
                continue

            # ── Process management ───────────────────────────────

            if op == Op.PROC_END:
                return

            if op == Op.FUNC_CLOG2:
                a = s_pop()
                if a.is_defined:
                    n = a.val
                    if n <= 1:
                        result = 0
                    else:
                        result = (n - 1).bit_length()
                    s_append(Value(result, width=32))
                else:
                    s_append(Value.x(32))
                continue

            if op == Op.CMP_CASEX:
                b = s_pop()
                a = s_pop()
                combined_mask = a.mask | b.mask
                match = ((a.val ^ b.val) & ~combined_mask) == 0
                s_append(Value(1 if match else 0, width=1))
                continue

            if op == Op.CMP_CASEZ:
                b = s_pop()
                a = s_pop()
                # In our 4-state model x and z share the same mask
                # representation, so casez behaves identically to casex.
                combined_mask = a.mask | b.mask
                match = ((a.val ^ b.val) & ~combined_mask) == 0
                s_append(Value(1 if match else 0, width=1))
                continue

            if op == Op.FUNC_RANDOM:
                # IEEE 1364 $random returns a 32-bit signed integer
                r = _random.getrandbits(32)
                s_append(Value(r, width=32))
                continue

            # ── Memory array operations ──────────────────────────

            if op == Op.LOAD_MEM:
                idx = s_pop()
                mem_info = self.mem_info
                mem_val = self.mem_val
                mem_mask = self.mem_mask
                if idx.is_defined and arg1 < len(mem_info):
                    ew, depth, base = mem_info[arg1]
                    addr = idx.val
                    if 0 <= addr < depth:
                        flat = base + addr
                        s_append(Value(mem_val[flat], width=ew, mask=mem_mask[flat]))
                    else:
                        ew = mem_info[arg1][0] if arg1 < len(mem_info) else 1
                        s_append(Value.x(ew))
                else:
                    ew = mem_info[arg1][0] if arg1 < len(mem_info) else 1
                    s_append(Value.x(ew))
                continue

            if op == Op.STORE_MEM:
                # arg1 = mem_id (low 16 bits) | (marker_sid << 16)
                mid = arg1 & 0xFFFF
                marker_sid = arg1 >> 16
                idx = s_pop()
                val = s_pop()
                mem_info = self.mem_info
                mem_val = self.mem_val
                mem_mask = self.mem_mask
                if idx.is_defined and mid < len(mem_info):
                    ew, depth, base = mem_info[mid]
                    addr = idx.val
                    if 0 <= addr < depth:
                        flat = base + addr
                        wmask = _mask_for_width(ew)
                        new_val = val.val & wmask & ~val.mask
                        new_mask = val.mask & wmask
                        # Only mark the memory marker dirty if the cell actually
                        # changed; otherwise CAs that self-write the same memory
                        # they read would loop forever, and the propagator would
                        # be unable to distinguish "value changed" from
                        # "process re-fired with same value".
                        if mem_val[flat] != new_val or mem_mask[flat] != new_mask:
                            mem_val[flat] = new_val
                            mem_mask[flat] = new_mask
                            dirty.add(marker_sid)
                continue

            if op == Op.NBA_MEM:
                # arg1 = mem_id (low 16 bits) | (marker_sid << 16)
                mid = arg1 & 0xFFFF
                idx = s_pop()
                val = s_pop()
                if idx.is_defined and mid < len(self.mem_info):
                    ew, depth, base = self.mem_info[mid]
                    addr = idx.val
                    if 0 <= addr < depth:
                        wmask = _mask_for_width(ew)
                        nba_val = Value(val.val & wmask, width=ew, mask=val.mask & wmask)
                        self.nba_mem_queue.append((mid, addr, nba_val))
                continue

            if op == Op.STORE_MEM_RANGE:
                # arg1 = mem_id (low 16 bits) | (marker_sid << 16)
                # stack: [val, idx, msb, lsb]
                mid = arg1 & 0xFFFF
                marker_sid = arg1 >> 16
                lsb = s_pop()
                msb = s_pop()
                idx = s_pop()
                val = s_pop()
                mem_info = self.mem_info
                mem_val = self.mem_val
                mem_mask = self.mem_mask
                if idx.is_defined and msb.is_defined and lsb.is_defined and mid < len(mem_info):
                    ew, depth, base = mem_info[mid]
                    addr = idx.val
                    if 0 <= addr < depth:
                        flat = base + addr
                        wmask = _mask_for_width(ew)
                        current = Value(mem_val[flat] & wmask, width=ew, mask=mem_mask[flat] & wmask)
                        updated = current.set_range(msb.val, lsb.val, val)
                        new_val = updated.val & wmask & ~updated.mask
                        new_mask = updated.mask & wmask
                        # Only mark dirty on actual value change — see STORE_MEM.
                        if mem_val[flat] != new_val or mem_mask[flat] != new_mask:
                            mem_val[flat] = new_val
                            mem_mask[flat] = new_mask
                            dirty.add(marker_sid)
                continue

            if op == Op.NBA_MEM_RANGE:
                # arg1 = mem_id (low 16 bits) | (marker_sid << 16)
                # stack: [val, idx, msb, lsb]
                mid = arg1 & 0xFFFF
                lsb = s_pop()
                msb = s_pop()
                idx = s_pop()
                val = s_pop()
                if idx.is_defined and msb.is_defined and lsb.is_defined and mid < len(self.mem_info):
                    ew, depth, base = self.mem_info[mid]
                    addr = idx.val
                    if 0 <= addr < depth:
                        self.nba_mem_range_queue.append((mid, addr, msb.val, lsb.val, val))
                continue

            if op == Op.SYS_READMEM:
                # arg1 = task_id into readmem_tasks table
                if arg1 < len(self.readmem_tasks):
                    filename, mid, is_hex = self.readmem_tasks[arg1]
                    if mid < len(self.mem_info):
                        ew, depth, base = self.mem_info[mid]
                        wmask = _mask_for_width(ew)
                        try:
                            with open(filename) as f:
                                addr = 0
                                for line in f:
                                    line = line.strip()
                                    if not line or line.startswith("//"):
                                        continue
                                    if line.startswith("@"):
                                        # Address specification: @addr
                                        addr = int(line[1:], 16)
                                        continue
                                    for token in line.split():
                                        if addr >= depth:
                                            break
                                        val = int(token, 16 if is_hex else 2)
                                        self.mem_val[base + addr] = val & wmask
                                        self.mem_mask[base + addr] = 0
                                        addr += 1
                        except FileNotFoundError:
                            raise FileNotFoundError("VM interpreter: $readmemh/$readmemb file not found") from None
                continue

            # ── Ternary merge ────────────────────────────────────

            if op == Op.TERNARY:
                false_val = s_pop()
                true_val = s_pop()
                cond = s_pop()
                if cond.mask == 0:
                    # Condition fully defined: pick one branch
                    s_append(true_val if cond.val != 0 else false_val)
                else:
                    # Condition has x/z: merge true/false bit by bit
                    w = max(true_val.width, false_val.width)
                    wmask = _mask_for_width(w)
                    tv = true_val.resize(w)
                    fv = false_val.resize(w)
                    agree = ~(tv.val ^ fv.val) & ~(tv.mask ^ fv.mask) & wmask
                    new_mask = ~agree & wmask
                    new_val = tv.val & fv.val & ~new_mask & wmask
                    s_append(Value(new_val, width=w, mask=new_mask))
                continue

            # ── Monitor ──────────────────────────────────────────

            if op == Op.SYS_MONITOR:
                # First invocation prints immediately; scheduler handles re-fire
                n_args = arg1 & 0xFFFF
                fmt_id = arg1 >> 16
                monitor_id = _arg2
                args_m = [s_pop() for _ in range(n_args)]
                args_m.reverse()
                self.display_output.append(_format_display(args_m, fmt_id, self.display_formats, self.time))
                self.active_monitor_id = monitor_id
                continue

            # ── File I/O ─────────────────────────────────────────

            if op == Op.SYS_FOPEN:
                # arg1 = task_id into fopen_tasks table
                if arg1 < len(self.fopen_tasks):
                    filename, mode = self.fopen_tasks[arg1]
                    try:
                        fh = open(filename, mode)
                        fd = self._next_fd
                        self._next_fd += 1
                        self.file_handles[fd] = fh
                        s_append(Value(fd, width=32))
                    except OSError:
                        s_append(Value(0, width=32))  # 0 = error
                else:
                    s_append(Value(0, width=32))
                continue

            if op == Op.SYS_FCLOSE:
                fd_val = s_pop()
                fd = fd_val.val
                fh = self.file_handles.pop(fd, None)
                if fh is not None:
                    fh.close()
                continue

            if op in (Op.SYS_FDISPLAY, Op.SYS_FWRITE):
                n_args = arg1 & 0xFFFF
                fmt_id = arg1 >> 16
                args_f = [s_pop() for _ in range(n_args)]
                args_f.reverse()
                fd_val = s_pop()  # fd was pushed first (bottom of args)
                fd = fd_val.val
                text = _format_display(args_f, fmt_id, self.display_formats, self.time)
                if op == Op.SYS_FDISPLAY:
                    text += "\n"
                fh = self.file_handles.get(fd)
                if fh is not None:
                    fh.write(text)
                continue

            if op == Op.SYS_FEOF:
                fd_val = s_pop()
                fd = fd_val.val
                fh = self.file_handles.get(fd)
                if fh is not None:
                    # Peek one char: if EOF, read returns empty
                    pos = fh.tell()
                    ch = fh.read(1)
                    if ch:
                        fh.seek(pos)
                        s_append(Value(0, width=1))
                    else:
                        s_append(Value(1, width=1))
                else:
                    s_append(Value(1, width=1))  # invalid fd → EOF
                continue

        # If we reach here without PROC_END, execution is complete
