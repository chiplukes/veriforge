"""AST-to-bytecode compiler for the VM simulation engine.

Walks the model AST (Module → AlwaysBlock/ContinuousAssign → Statement →
Expression) and emits flat bytecode instruction arrays for the interpreter.

The compiler is invoked once at elaboration time; the resulting bytecode is
executed many times during simulation.
"""

from __future__ import annotations

import warnings
from enum import Enum, auto
from typing import TYPE_CHECKING

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from veriforge.model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    RangeSelect,
    Replication,
    StreamingConcatenation,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventControl,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    Statement,
    SensitivityEdge,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)

from ...semantics import const_int as _const_int
from ...semantics import range_width as _range_width
from ...semantics import var_width as _var_width
from ..value import Value
from .opcodes import Op, instr

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.variables import Variable


# ── Process types ────────────────────────────────────────────────────


class ProcessType(Enum):
    CONTINUOUS = auto()
    COMBINATIONAL = auto()
    SEQUENTIAL = auto()
    INITIAL = auto()


class CompiledProcess:
    """A compiled process ready for VM execution.

    Attributes:
        process_type:  Type of process (continuous, combinational, etc.)
        program:       Bytecode instruction array: list[(opcode, arg1, arg2)]
        sensitivity:   Set of signal IDs that trigger re-execution.
        edge_signals:  Dict of sig_id → "posedge"/"negedge" for sequential.
        source_block:  Reference to original AST block (for debug/initial).
    """

    __slots__ = ("edge_signals", "has_timing", "process_type", "program", "sensitivity", "source_block")

    def __init__(
        self,
        process_type: ProcessType,
        program: list[tuple[int, int, int]],
        sensitivity: set[int],
        edge_signals: dict[int, str] | None = None,
        source_block: AlwaysBlock | InitialBlock | ContinuousAssign | None = None,
        *,
        has_timing: bool = False,
    ) -> None:
        self.process_type = process_type
        self.program = program
        self.sensitivity = sensitivity
        self.edge_signals = edge_signals or {}
        self.source_block = source_block
        self.has_timing = has_timing


# ── Compiler ─────────────────────────────────────────────────────────


class Compiler:  # cm:8c1e4a
    """Compile a Module's AST into bytecode for the VM interpreter.

    Usage:
        compiler = Compiler()
        compiler.compile_module(module)
        # Access results:
        compiler.signal_map        # name → signal ID
        compiler.sig_val / sig_mask / sig_width / sig_names  # flat arrays
        compiler.const_pool        # list[Value] constants
        compiler.processes         # list[CompiledProcess]
    """

    __slots__ = (
        "_const_cache",
        "_func_call_depth",
        "_function_map",
        "_mem_signals",
        "_memory_bases",
        "_param_env",
        "_signal_bases",
        "_struct_signal_types",
        "_struct_type_map",
        "_task_map",
        "const_pool",
        "display_formats",
        "fopen_tasks",
        "loop_limit",
        "mem_count",
        "mem_info",
        "mem_map",
        "mem_marker_sigs",
        "mem_mask",
        "mem_val",
        "monitor_programs",
        "processes",
        "readmem_tasks",
        "sig_mask",
        "sig_names",
        "sig_signed",
        "sig_val",
        "sig_width",
        "signal_count",
        "signal_map",
    )

    def __init__(self, *, loop_limit: int = 100_000) -> None:
        # Signal storage
        self.signal_map: dict[str, int] = {}
        self.signal_count: int = 0
        self.sig_val: list[int] = []
        self.sig_mask: list[int] = []
        self.sig_width: list[int] = []
        self.sig_signed: list[bool] = []
        self.sig_names: list[str] = []

        # Memory array storage
        # mem_map: name → mem_id
        # mem_info: list of (element_width, depth, base_addr) per mem_id
        # mem_val/mem_mask: flat arrays indexed by (mem_id_offset + addr)
        self.mem_map: dict[str, int] = {}
        self.mem_count: int = 0
        self.mem_info: list[tuple[int, int, int]] = []  # (elem_width, depth, base_addr)
        self.mem_val: list[int] = []
        self.mem_mask: list[int] = []
        self._mem_signals: set[str] = set()  # signal names that are memories
        self.mem_marker_sigs: list[int] = []  # mem_id → marker signal ID for dirty tracking

        # Constant pool
        self.const_pool: list[Value] = []
        self._const_cache: dict[tuple[int, int, int], int] = {}  # (val,mask,width) → pool index

        # $readmemh/$readmemb task table: (filename, mem_id, is_hex)
        self.readmem_tasks: list[tuple[str, int, bool]] = []

        # $fopen task table: (filename, mode) per task_id
        self.fopen_tasks: list[tuple[str, str]] = []

        # $display/$monitor format string table
        self.display_formats: list[str] = []

        # $monitor: list of (monitor_program, sensitivity_sigs) tuples.
        # Each entry is a self-contained bytecoded program that, when executed,
        # pushes one display line into the interpreter's display_output.
        self.monitor_programs: list[tuple[list[tuple[int, int, int]], set[int]]] = []

        # Compiled processes
        self.processes: list[CompiledProcess] = []

        self.loop_limit = loop_limit

        # Struct type registry: signal_name -> StructLayout
        self._struct_signal_types: dict[str, object] = {}
        self._struct_type_map: dict[str, object] = {}

        # Non-zero packed base offsets on memory elements: memory_name -> lsb_offset
        self._memory_bases: dict[str, int] = {}

        # Non-zero base offsets: signal_name -> lsb_offset
        self._signal_bases: dict[str, int] = {}

        # Parameter environment: name → int value (for resolving widths)
        self._param_env: dict[str, int] = {}

    # ── Signal registration ──────────────────────────────────────

    def _register_signal(self, name: str, width: int, signed: bool = False) -> int:
        """Register a signal and return its ID. If already registered, return existing ID."""
        if name in self.signal_map:
            return self.signal_map[name]
        sid = self.signal_count
        self.signal_count += 1
        self.signal_map[name] = sid
        # Initialize to x (same as reference scheduler)
        wmask = (1 << width) - 1
        self.sig_val.append(0)
        self.sig_mask.append(wmask)
        self.sig_width.append(width)
        self.sig_signed.append(signed)
        self.sig_names.append(name)
        return sid

    @staticmethod
    def _resolve_id_name(ident: "Identifier") -> str:
        """Return the full signal name for an Identifier, including hierarchy."""
        if ident.hierarchy:
            return ".".join(ident.hierarchy) + "." + ident.name
        return ident.name

    def _resolve_struct_access(self, name: str) -> tuple[int, int, int] | None:
        """Resolve nested struct access to (base_sid, offset, width)."""
        from ..elaborate import resolve_struct_access  # noqa: PLC0415

        info = resolve_struct_access(name, self._struct_signal_types, self.signal_map)
        if info is None:
            return None
        base_name, offset, width = info
        base_sid = self.signal_map.get(base_name)
        if base_sid is None:
            return None
        return base_sid, offset, width

    def _resolve_struct_storage_access(self, name: str) -> tuple[str, int, int | str | None, int, int] | None:
        """Resolve nested struct access to either a signal base or a memory element."""
        from ..elaborate import resolve_struct_storage_access  # noqa: PLC0415

        info = resolve_struct_storage_access(name, self._struct_signal_types, self.signal_map, set(self.mem_map))
        if info is None:
            return None
        storage_name, storage_index, offset, width = info
        if storage_index is None:
            sid = self.signal_map.get(storage_name)
            if sid is None:
                return None
            return "signal", sid, None, offset, width
        mid = self.mem_map.get(storage_name)
        if mid is None:
            return None
        return "memory", mid, storage_index, offset, width

    def _struct_layout_for_type(self, type_name: str | None):
        """Resolve a typedef-backed struct type name to its layout."""
        if not type_name:
            return None
        bare = type_name.rsplit("::", 1)[-1] if "::" in type_name else type_name
        return self._struct_type_map.get(bare)

    def _compile_struct_storage_index(self, index_spec: int | str | None, program: list[tuple[int, int, int]]) -> bool:
        """Push a struct-storage memory index onto the stack."""
        if index_spec is None:
            return False
        if isinstance(index_spec, int):
            cid = self._add_int_const(index_spec, 32)
            program.append(instr(Op.LOAD_CONST, cid))
            return True
        sid = self.signal_map.get(index_spec)
        if sid is None:
            return False
        program.append(instr(Op.LOAD_SIG, sid))
        return True

    def _get_signal_id(self, name: str) -> int:
        """Get signal ID by name. Raises if not registered."""
        sid = self.signal_map.get(name)
        if sid is None:
            # Auto-register with width 1 (for signals referenced but not declared)
            sid = self._register_signal(name, 1)
        return sid

    # ── Constant pool ────────────────────────────────────────────

    def _add_const(self, value: Value) -> int:
        """Add a constant to the pool, deduplicating. Returns pool index."""
        key = (value.val, value.mask, value.width)
        idx = self._const_cache.get(key)
        if idx is not None:
            return idx
        idx = len(self.const_pool)
        self.const_pool.append(value)
        self._const_cache[key] = idx
        return idx

    def _add_int_const(self, val: int, width: int = 32) -> int:
        """Convenience: add an integer constant."""
        return self._add_const(Value(val, width=width))

    # ── Module compilation ───────────────────────────────────────

    def compile_module(self, module: Module) -> None:
        """Compile an entire module: register signals, compile all processes."""
        # Build function/task lookup maps
        self._function_map: dict[str, object] = {f.name: f for f in module.functions}
        self._task_map: dict[str, object] = {t.name: t for t in module.tasks}
        self._func_call_depth: int = 0
        self._register_signals(module)
        self._compile_continuous_assigns(module)
        self._compile_always_blocks(module)
        self._compile_initial_blocks(module)

    def _register_memory(self, name: str, elem_width: int, depth: int) -> int:
        """Register a memory array and return its mem_id."""
        if name in self.mem_map:
            return self.mem_map[name]
        mid = self.mem_count
        self.mem_count += 1
        self.mem_map[name] = mid
        base = len(self.mem_val)
        self.mem_info.append((elem_width, depth, base))
        self._mem_signals.add(name)
        # Register a 1-bit marker signal for memory dirty tracking.
        # This synthetic signal participates in sensitivity/dirty tracking:
        # combo always @(*) blocks that read this memory will include the
        # marker in their sensitivity set, and STORE_MEM/NBA_MEM will mark
        # it dirty so those processes re-fire.
        marker_sid = self._register_signal(f"__mem_{mid}_wr", 1)
        self.mem_marker_sigs.append(marker_sid)
        # Initialize all elements to x
        wmask = (1 << elem_width) - 1
        for _ in range(depth):
            self.mem_val.append(0)
            self.mem_mask.append(wmask)
        return mid

    def _is_memory(self, name: str) -> bool:
        """Check if a signal name is a memory array."""
        return name in self._mem_signals

    def _resolve_whole_memory_identifier(self, expr: Expression) -> tuple[str, int] | None:
        """Return ``(name, mem_id)`` for a plain whole-memory identifier."""
        if isinstance(expr, Identifier):
            name = self._resolve_id_name(expr)
            mid = self.mem_map.get(name)
            if mid is not None:
                return name, mid
        return None

    def _compile_whole_memory_copy(
        self, lhs: Expression, rhs: Expression, program: list[tuple[int, int, int]], *, immediate: bool
    ) -> bool:
        """Emit an element-wise whole-memory initialization or copy.

        Handles two cases:
        - Both sides are plain memories: element-wise copy.
        - LHS is a memory and RHS is ``'{default: expr}``: fill all elements with expr.
        """
        lhs_info = self._resolve_whole_memory_identifier(lhs)
        if lhs_info is None:
            return False
        _lhs_name, lhs_mid = lhs_info
        lhs_elem_w, lhs_depth, _lhs_base = self.mem_info[lhs_mid]
        marker_sid = self.mem_marker_sigs[lhs_mid]
        encoded_arg = lhs_mid | (marker_sid << 16)

        rhs_info = self._resolve_whole_memory_identifier(rhs)
        if rhs_info is not None:
            _rhs_name, rhs_mid = rhs_info
            rhs_elem_w, rhs_depth, _rhs_base = self.mem_info[rhs_mid]
            if lhs_elem_w != rhs_elem_w or lhs_depth != rhs_depth:
                return False
            for addr in range(lhs_depth):
                addr_cid = self._add_int_const(addr, 32)
                program.append(instr(Op.LOAD_CONST, addr_cid))
                program.append(instr(Op.LOAD_MEM, rhs_mid))
                program.append(instr(Op.LOAD_CONST, addr_cid))
                if immediate:
                    program.append(instr(Op.STORE_MEM, encoded_arg))
                else:
                    program.append(instr(Op.NBA_MEM, encoded_arg))
            return True

        # Handle '{default: expr} — fill every element with the same value.
        if (
            isinstance(rhs, AssignmentPattern)
            and rhs.default_value is not None
            and not rhs.named_pairs
            and not rhs.positional
        ):
            for addr in range(lhs_depth):
                self._compile_expr(rhs.default_value, program, width=lhs_elem_w)
                if self._expr_width(rhs.default_value) != lhs_elem_w:
                    program.append(instr(Op.RESIZE, lhs_elem_w))
                addr_cid = self._add_int_const(addr, 32)
                program.append(instr(Op.LOAD_CONST, addr_cid))
                if immediate:
                    program.append(instr(Op.STORE_MEM, encoded_arg))
                else:
                    program.append(instr(Op.NBA_MEM, encoded_arg))
            return True

        # Handle '{a, b, ...} — an unkeyed positional assignment pattern.
        # Per IEEE 1800-2017 §10.9.1, item k assigns array index k in
        # declaration order — the SAME index order plain `arr[k]` indexing
        # already uses, for either an ascending- or descending-declared
        # array. This must be compiled item-by-item, NOT by concatenating
        # the whole pattern into one flat value and unflattening it into the
        # memory (the packed-array MSB-first convention that a general
        # `_compile_expr`+whole-memory-store would apply is only correct
        # for a genuine 2-D packed array's own bit layout — for a plain
        # unpacked array it silently reverses element order). Confirmed
        # wrong against the real `ibex_alu` RTL: `imd_val_d_o = '{operand_a_i,
        # 32'h0};` (a 2-element unpacked array) previously wrote
        # operand_a_i to index 1 and 0 to index 0 — reversed from the
        # LRM-correct index-0/index-1 assignment.
        if isinstance(rhs, AssignmentPattern) and rhs.positional and not rhs.named_pairs:
            if len(rhs.positional) != lhs_depth:
                return False
            for addr, part in enumerate(rhs.positional):
                self._compile_expr(part, program, width=lhs_elem_w)
                if self._expr_width(part) != lhs_elem_w:
                    program.append(instr(Op.RESIZE, lhs_elem_w))
                addr_cid = self._add_int_const(addr, 32)
                program.append(instr(Op.LOAD_CONST, addr_cid))
                if immediate:
                    program.append(instr(Op.STORE_MEM, encoded_arg))
                else:
                    program.append(instr(Op.NBA_MEM, encoded_arg))
            return True

        return False

    def _eval_initial_value(self, expr, width: int) -> Value | None:
        """Try to statically evaluate an initial_value expression. Returns None on failure."""
        if expr is None:
            return None
        try:
            if isinstance(expr, Literal):
                v = self._compile_literal(expr)
                return v.resize(width) if v.width != width else v
            if isinstance(expr, UnaryOp):
                inner = self._eval_initial_value(expr.operand, width)
                if inner is not None and inner.is_defined:
                    if expr.op == "-":
                        mask = (1 << width) - 1
                        return Value((-inner.val) & mask, width=width)
                    if expr.op == "~":
                        mask = (1 << width) - 1
                        return Value((~inner.val) & mask, width=width)
            if isinstance(expr, BinaryOp):
                left = self._eval_initial_value(expr.left, width)
                right = self._eval_initial_value(expr.right, width)
                if left is not None and left.is_defined and right is not None and right.is_defined:
                    mask = (1 << width) - 1
                    if expr.op == "+":
                        return Value((left.val + right.val) & mask, width=width)
                    if expr.op == "-":
                        return Value((left.val - right.val) & mask, width=width)
        except Exception:
            pass
        return None

    def _apply_initial_value(self, sid: int, init_expr, width: int) -> None:
        """Apply an initial_value expression to a registered signal."""
        v = self._eval_initial_value(init_expr, width)
        if v is not None:
            self.sig_val[sid] = v.val
            self.sig_mask[sid] = v.mask

    def _append_base_adjust(self, program: list[tuple[int, int, int]], base: int) -> None:
        """Adjust the top-of-stack bit index for a declared non-zero packed LSB."""
        if base == 0:
            return
        cid = self._add_int_const(base, 32)
        program.append(instr(Op.LOAD_CONST, cid))
        program.append(instr(Op.SUB))

    def _select_base(self, target: Expression) -> int:
        """Return the packed-range LSB base for scalar or memory-element selects."""
        if type(target) is Identifier:
            return self._signal_bases.get(self._resolve_id_name(target), 0)
        if type(target) is BitSelect and type(target.target) is Identifier:
            tname = self._resolve_id_name(target.target)
            if self._is_memory(tname):
                return self._memory_bases.get(tname, 0)
        return 0

    def _register_signals(self, module: Module) -> None:
        """Register all nets, variables, and ports from the module."""
        from ..elaborate import _build_param_env, parameter_signal_width  # noqa: PLC0415
        from veriforge.model.ports import PortDirection  # noqa: PLC0415

        param_env = _build_param_env(module)

        for net in module.nets:
            senv = _scoped_env(net.name, param_env)
            width = _range_width(net.width, senv)
            lsb = 0
            if net.width is not None:
                lsb_val = _const_int(net.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if net.dimensions:
                if lsb != 0:
                    self._memory_bases[net.name] = lsb
                depth, elem_width = _memory_shape(net.dimensions, width, senv, net.packed_dim_count)
                self._register_memory(net.name, elem_width, depth)
            else:
                if lsb != 0:
                    self._signal_bases[net.name] = lsb
                sid = self._register_signal(net.name, width, signed=net.signed)
                if hasattr(net, "initial_value") and net.initial_value is not None:
                    self._apply_initial_value(sid, net.initial_value, width)

        for var in module.variables:
            senv = _scoped_env(var.name, param_env)
            width = _var_width(var, senv)
            lsb = 0
            if var.width is not None:
                lsb_val = _const_int(var.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if var.dimensions:
                if lsb != 0:
                    self._memory_bases[var.name] = lsb
                depth, elem_width = _memory_shape(var.dimensions, width, senv, var.packed_dim_count)
                self._register_memory(var.name, elem_width, depth)
            else:
                if lsb != 0:
                    self._signal_bases[var.name] = lsb
                sid = self._register_signal(var.name, width, signed=var.signed)
                if var.initial_value is not None:
                    self._apply_initial_value(sid, var.initial_value, width)

        for port in module.ports:
            senv = _scoped_env(port.name, param_env)
            width = _range_width(port.width, senv)
            lsb = 0
            if port.width is not None:
                lsb_val = _const_int(port.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if port.name not in self.signal_map and port.name not in self.mem_map:
                if getattr(port, "dimensions", None):
                    if lsb != 0:
                        self._memory_bases[port.name] = lsb
                    depth, elem_width = _memory_shape(port.dimensions, width, senv, port.packed_dim_count)
                    self._register_memory(port.name, elem_width, depth)
                else:
                    if lsb != 0:
                        self._signal_bases[port.name] = lsb
                    sid = self._register_signal(port.name, width, signed=port.signed)
                    # ANSI inline initializer (`output reg foo = 1;`) --
                    # applies to output/inout ports the same way an
                    # initial_value applies to a net/var above. Input
                    # ports carrying a default_value are the unrelated
                    # SV "unconnected instance port" fallback feature,
                    # which real hardware can't apply to an externally-
                    # driven signal -- deliberately excluded, matching
                    # `check_input_port_init`'s warning for that case.
                    if port.direction != PortDirection.INPUT and port.default_value is not None:
                        self._apply_initial_value(sid, port.default_value, width)

        # Register parameters as constant-valued signals
        self._register_parameters(module)

        # Register enum member constants from typedefs
        self._register_enum_constants(module)

        # Register struct type information for field access
        self._register_struct_types(module)

    def _register_parameters(self, module: Module) -> None:
        """Register parameters as signals initialized to their constant values."""
        from ..elaborate import _build_param_env, parameter_signal_width  # noqa: PLC0415

        param_env = _build_param_env(module)
        self._param_env = param_env
        for p in module.parameters:
            if p.name in param_env and p.name not in self.signal_map:
                val = param_env[p.name]
                if isinstance(val, str):
                    # Byte-pack string parameters (e.g. RESET_STRATEGY="MINI")
                    int_val = 0
                    for ch in val:
                        int_val = (int_val << 8) | ord(ch)
                    width = parameter_signal_width(p, param_env, val)
                    sid = self._register_signal(p.name, width, signed=p.signed)
                    self.sig_val[sid] = int_val & ((1 << width) - 1)
                    self.sig_mask[sid] = 0  # fully defined
                elif isinstance(val, int):
                    width = parameter_signal_width(p, param_env, val)
                    sid = self._register_signal(p.name, width, signed=p.signed)
                    self.sig_val[sid] = val & ((1 << width) - 1)
                    self.sig_mask[sid] = 0  # fully defined

    def _register_enum_constants(self, module: Module) -> None:
        """Register enum member constants from typedefs as signals."""
        from ..elaborate import _build_enum_env  # noqa: PLC0415

        enum_env = _build_enum_env(module)
        for name, (val, width) in enum_env.items():
            if name not in self.signal_map:
                sid = self._register_signal(name, width)
                mask = (1 << width) - 1
                self.sig_val[sid] = val & mask
                self.sig_mask[sid] = 0  # fully defined

    def _register_struct_types(self, module: Module) -> None:
        """Register struct type information for field access resolution."""
        from ..elaborate import _build_struct_env  # noqa: PLC0415

        _type_map, struct_signal_map = _build_struct_env(module)
        self._struct_type_map.update(_type_map)
        self._struct_signal_types.update(struct_signal_map)

    def _concat_parts_match_width(self, lhs_part: Expression, rhs_part: Expression) -> bool:
        lhs_width = self._concat_part_width_info(lhs_part)
        rhs_width = self._concat_part_width_info(rhs_part)
        if lhs_width is not None and rhs_width is not None:
            return lhs_width == rhs_width
        if isinstance(lhs_part, BitSelect) and isinstance(rhs_part, BitSelect):
            return True
        if isinstance(lhs_part, RangeSelect) and isinstance(rhs_part, RangeSelect):
            return repr(lhs_part.msb) == repr(rhs_part.msb) and repr(lhs_part.lsb) == repr(rhs_part.lsb)
        if isinstance(lhs_part, PartSelect) and isinstance(rhs_part, PartSelect):
            return repr(lhs_part.width) == repr(rhs_part.width)
        return False

    def _compile_matching_concat_copy(
        self,
        lhs: Concatenation,
        rhs: Expression,
        program: list[tuple[int, int, int]],
        *,
        immediate: bool,
    ) -> bool:
        if not isinstance(rhs, Concatenation) or len(lhs.parts) != len(rhs.parts):
            return False
        if not all(
            self._concat_parts_match_width(lhs_part, rhs_part)
            for lhs_part, rhs_part in zip(lhs.parts, rhs.parts, strict=True)
        ):
            return False

        for lhs_part, rhs_part in zip(lhs.parts, rhs.parts, strict=True):
            part_width = self._concat_part_width_info(lhs_part)
            self._compile_expr(rhs_part, program, width=part_width or 0)
            rhs_width = self._concat_part_width_info(rhs_part)
            if part_width is not None and rhs_width is not None and rhs_width < part_width:
                program.append(instr(Op.RESIZE, part_width))

        for lhs_part in reversed(lhs.parts):
            self._compile_store_lhs(
                lhs_part,
                program,
                immediate=immediate,
                total_width=self._concat_part_width_info(lhs_part),
            )
        return True

    def _compile_continuous_assigns(self, module: Module) -> None:
        """Compile continuous assignments."""
        for assign in module.continuous_assigns:
            program: list[tuple[int, int, int]] = []
            if isinstance(assign.lhs, Concatenation) and self._compile_matching_concat_copy(
                assign.lhs, assign.rhs, program, immediate=True
            ):
                program.append(instr(Op.PROC_END))

                sensitivity = self._collect_expr_signals(assign.rhs)
                proc = CompiledProcess(
                    ProcessType.CONTINUOUS,
                    program,
                    sensitivity,
                    source_block=assign,
                )
                self.processes.append(proc)
                continue
            # Bare-Identifier-to-bare-Identifier (or `'{...}`) whole-memory
            # LHS (typically a hierarchy-flattened whole-array PORT
            # CONNECTION, e.g. `.imd_val_q_i(imd_val_q_full)`): a direct
            # per-index copy/assign, NOT the generic eval-to-flat-value +
            # unflatten path below (which applies a packed-array MSB-first
            # convention that's only valid for a genuine 2-D packed array,
            # and silently reverses/misorders elements of a plain unpacked
            # array). Mirrors `_compile_whole_memory_copy`'s existing use
            # from procedural blocking/nonblocking assigns -- continuous
            # assigns never called it at all before.
            if isinstance(assign.lhs, Identifier) and self._compile_whole_memory_copy(
                assign.lhs, assign.rhs, program, immediate=True
            ):
                program.append(instr(Op.PROC_END))

                sensitivity = self._collect_expr_signals(assign.rhs)
                proc = CompiledProcess(
                    ProcessType.CONTINUOUS,
                    program,
                    sensitivity,
                    source_block=assign,
                )
                self.processes.append(proc)
                continue
            lhs_w = self._expr_width(assign.lhs)
            if isinstance(assign.lhs, Concatenation):
                lhs_w = sum(self._concat_eval_widths(assign.lhs.parts, self._expr_width(assign.rhs)))
            self._compile_expr(assign.rhs, program, width=lhs_w)
            self._emit_sign_ext_if_needed(assign.rhs, assign.lhs, program)
            self._compile_store_lhs(assign.lhs, program, immediate=True, total_width=lhs_w)
            program.append(instr(Op.PROC_END))

            sensitivity = self._collect_expr_signals(assign.rhs)
            proc = CompiledProcess(
                ProcessType.CONTINUOUS,
                program,
                sensitivity,
                source_block=assign,
            )
            self.processes.append(proc)

    def _compile_always_blocks(self, module: Module) -> None:
        """Compile always blocks."""
        for block in module.always_blocks:
            program: list[tuple[int, int, int]] = []
            has_timing = self._compile_stmt(block.body, program)
            program.append(instr(Op.PROC_END))

            sensitivity, edges = self._always_sensitivity(block)

            if block.sensitivity_type == SensitivityType.COMBINATIONAL:
                ptype = ProcessType.COMBINATIONAL
            else:
                ptype = ProcessType.SEQUENTIAL

            proc = CompiledProcess(
                ptype,
                program,
                sensitivity,
                edge_signals=edges,
                source_block=block,
                has_timing=has_timing,
            )
            self.processes.append(proc)

    def _compile_initial_blocks(self, module: Module) -> None:
        """Compile initial blocks.

        Initial blocks with delay/event controls are flagged with
        has_timing=True so the scheduler can route them to the reference
        executor's coroutine path for proper suspend/resume.
        """
        for block in module.initial_blocks:
            program: list[tuple[int, int, int]] = []
            has_timing = self._compile_stmt(block.body, program)
            program.append(instr(Op.PROC_END))

            proc = CompiledProcess(
                ProcessType.INITIAL,
                program,
                set(),  # initial blocks don't have sensitivity
                source_block=block,
                has_timing=has_timing,
            )
            self.processes.append(proc)

    # ── Expression compilation ───────────────────────────────────

    def _compile_expr(
        self,
        expr: Expression,
        program: list[tuple[int, int, int]],
        width: int = 0,
        signed_override: bool | None = None,
    ) -> None:  # noqa: PLR0912, PLR0911, PLR0915
        """Compile an expression to bytecode (post-order: children first).

        *width* is the context-determined bit-width from an enclosing
        assignment LHS (IEEE 1364-2005 §5.4.1).  When non-zero, operands
        of context-determined operators are widened with RESIZE before
        the operation so that upper bits are not lost.

        *signed_override*, when not None, replaces `_expr_signed()` for
        every extension decision made while compiling *expr* (propagated
        unchanged through nested context-determined operators). This
        mirrors `ExpressionEvaluator.eval`'s `signed_override` -- see its
        docstring for the full rationale (IEEE 1364-2005 §5.5.1's rule for
        the conditional operator).
        """
        etype = type(expr)

        # -- Identifier: load signal --
        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self.signal_map.get(name)
            if sid is not None:
                program.append(instr(Op.LOAD_SIG, sid))
                if width and self.sig_width[sid] < width:
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                    if eff_signed:
                        program.append(instr(Op.SIGN_EXT, width, 0))
                    else:
                        program.append(instr(Op.RESIZE, width))
                return
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                storage_kind, storage_id, storage_index, offset, field_width = struct_info
                if storage_kind == "signal":
                    program.append(instr(Op.LOAD_SIG, storage_id))
                else:
                    if not self._compile_struct_storage_index(storage_index, program):
                        raise ValueError(f"Unsupported dynamic memory struct index: {name!r}")
                    program.append(instr(Op.LOAD_MEM, storage_id))
                msb_cid = self._add_const(Value(offset + field_width - 1, width=32))
                lsb_cid = self._add_const(Value(offset, width=32))
                program.append(instr(Op.LOAD_CONST, msb_cid))
                program.append(instr(Op.LOAD_CONST, lsb_cid))
                program.append(instr(Op.RANGE_SELECT))
                return
            if self._is_memory(name):
                # Whole-array reference (2-D packed array read as a single
                # flat vector, e.g. `assign wide = foo;`) -- the depth is
                # known at compile time, so synthesize the equivalent
                # concatenation of every element (highest Verilog index
                # first, matching standard packed-array bit layout) and
                # compile that instead, reusing Concatenation's own
                # (already-correct) codegen rather than duplicating it.
                mid = self.mem_map[name]
                _elem_w, depth, _base = self.mem_info[mid]
                for idx in range(depth - 1, -1, -1):
                    part = BitSelect(target=Identifier(name), index=Literal(idx))
                    self._compile_expr(part, program, self._expr_width(part))
                program.append(instr(Op.CONCAT, depth))
                if width and width > depth * _elem_w:
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                    program.append(instr(Op.SIGN_EXT if eff_signed else Op.RESIZE, width, 0))
                return
            sid = self._get_signal_id(name)
            program.append(instr(Op.LOAD_SIG, sid))
            if width and self.sig_width[sid] < width:
                eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                if eff_signed:
                    program.append(instr(Op.SIGN_EXT, width, 0))
                else:
                    program.append(instr(Op.RESIZE, width))
            return

        # -- Literal: load constant --
        if etype is Literal:
            # A declared-signed literal (e.g. `4'sb1000` = -8) whose own
            # top bit is 1 needs sign-extension when nested inside a wider
            # context (a $signed()-wrapped/ternary-forced-signed operand)
            # -- same gap as BitSelect/RangeSelect/PartSelect above, and
            # the identical bug already fixed in `sim/evaluator.py` and
            # the compiled engine's wide emitter.
            val = self._compile_literal(expr)
            cid = self._add_const(val)
            program.append(instr(Op.LOAD_CONST, cid))
            if width and val.width < width:
                eff_signed = signed_override if signed_override is not None else expr.signed
                program.append(instr(Op.SIGN_EXT if eff_signed else Op.RESIZE, width, 0))
            return

        # -- BinaryOp --
        if etype is BinaryOp:
            # IEEE 1364-2005 §5.4.1: operator categories for width propagation.
            static_result_w = 0
            # `&&`/`||` produce a 1-bit result and their operands are each
            # independently self-determined -- truthiness doesn't need a
            # shared width between the two sides (unlike comparisons
            # below), so no width propagation is needed here.
            if expr.op in ("&&", "||"):
                self._compile_expr(expr.left, program)
                self._compile_expr(expr.right, program)
            # Comparison/equality ops produce a 1-bit RESULT (self-
            # determined outward, not context-determined from the LHS),
            # but their TWO OPERANDS are mutually context-determined
            # between each other (IEEE 1364-2005 §5.5.2: "The relational
            # and equality operators have operands that are neither fully
            # self-determined nor fully context-determined. The operands
            # shall affect each other as if they were context-determined
            # operands with a result type and size ... determined from
            # them" -- i.e. via §5.5.1's normal combining rule, "if any
            # operand is unsigned, the result is unsigned" -- NOT each
            # operand's own individual signedness). This is exactly `/ %`'s
            # "combined signedness governs BOTH operands uniformly" model,
            # not `+ - *`'s "each operand uses its own signedness" model:
            # `both_signed` is computed once and forced as the compiled
            # `signed_override` into BOTH operands, propagating into
            # whatever nested operator either operand is (so e.g. a nested
            # unary `-` sees the comparison's OWN combined decision, not
            # its operand's individual type) -- and each operand is
            # compiled directly AT the shared target width (not its own
            # self-width, resized afterward) so that width propagates all
            # the way down for the SAME "unary `-` must negate at its
            # final width, not self-width-then-extend" reason established
            # for the arithmetic operand-extension redesign above.
            # Mirrors the identical fix in `sim/evaluator.py`; confirmed
            # wrong (individual-signedness version) against Icarus for
            # `(a5[5:2] < a0)` (a5[5:2] an unsigned part-select, a0 a
            # signed 1-bit register).
            elif expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">="):
                target = max(self._expr_width(expr.left), self._expr_width(expr.right))
                both_signed = self._expr_signed(expr.left) and self._expr_signed(expr.right)
                self._compile_expr(expr.left, program, target, both_signed)
                self._compile_expr(expr.right, program, target, both_signed)
            # Shift operators — only LEFT operand is context-determined;
            # right is always self-determined.
            elif expr.op in ("<<", ">>", "<<<", ">>>"):
                if expr.op == ">>>" and self._expr_signed(expr.left):
                    self._compile_expr(expr.left, program)
                else:
                    # Compile the left operand directly at `target` (max of
                    # the enclosing context width and its own static
                    # self-determined width), NOT `width` alone --
                    # `_compile_expr` for some node types (Replication is
                    # the confirmed case) directly SIZES its result to
                    # whatever width it's asked to compile at, so
                    # requesting the (possibly narrower) outer `width` here
                    # would already discard bits above `width` DURING
                    # compilation itself, before the RESIZE/SIGN_EXT below
                    # ever runs -- widening AFTERWARD only zero-pads on top
                    # of that already-truncated value; it cannot recover
                    # bits that were never computed. Target max(width,
                    # static self-determined width) -- NOT `width` alone.
                    # Context-determined extension must only ever WIDEN an
                    # operand, never narrow it below its own natural width
                    # (an operand can legitimately be wider than the
                    # enclosing context, e.g. a wide concat feeding a
                    # narrower assignment; SIGN_EXT/RESIZE truncate when
                    # the target is smaller than the value's current
                    # width, which would corrupt the operator's input).
                    # Using the static estimate as a floor is always safe
                    # even when it's an overestimate (e.g. expr.left
                    # contains a ternary, whose static width is
                    # max(branches) but the actual picked branch may be
                    # narrower): the target is then still >= the true
                    # runtime width, so this only ever widens or no-ops,
                    # never truncates. Confirmed against Icarus
                    # (cross-engine, vm/vm-fast vs reference/compiled) for
                    # `({2{$unsigned(a5)}} >> (shift amount between the
                    # enclosing 96-bit context width and the replication's
                    # own true 130-bit width))`: bits 96-129 of the
                    # replication (needed once shifted down into the
                    # visible low bits) were silently dropped by compiling
                    # the replication at the narrower 96-bit context width
                    # first.
                    target = max(width, self._expr_width(expr.left)) if width else width
                    self._compile_expr(expr.left, program, target, signed_override)
                    if width:
                        eff_signed = signed_override if signed_override is not None else self._expr_signed(expr.left)
                        if eff_signed:
                            program.append(instr(Op.SIGN_EXT, target, 0))
                        else:
                            program.append(instr(Op.RESIZE, target))
                        # A shift's own result width is exactly its (now
                        # resized) left operand's width (IEEE 1364-2005
                        # Table 5-22: "L(i)") -- RESIZE/SIGN_EXT above just
                        # deterministically set the runtime width to
                        # `target`, so the shift op's result will be
                        # `target` too. Recorded so the shared tail below
                        # can narrow it back to `width` if `target > width`
                        # (self_width(expr.left) exceeded the requested
                        # context), the same gap as the arithmetic branch's
                        # `*` case below.
                        static_result_w = target
                self._compile_expr(expr.right, program)  # self-determined
            # Power operator: grouped with the SHIFT row in IEEE 1364-2005
            # Table 5-22 (`>> << ** >>> <<<` -> `L(i)`), not the generic
            # `max(L(i),L(j))` row `+ - * / %` share -- confirmed directly
            # against the primary spec text ("In all cases, the second
            # operand of the power operator shall be treated as
            # self-determined"). The BASE is context-determined exactly
            # like a shift's left operand; the EXPONENT is always
            # self-determined at its own natural width (via
            # `self._expr_width`, not a bare `_compile_expr` call, so a
            # nested context-determined operator within it still resizes
            # correctly before `**` runs). Mirrors the identical fix in
            # `sim/evaluator.py`.
            elif expr.op == "**":
                self._compile_expr(expr.left, program, width, signed_override)
                if width:
                    target = max(width, self._expr_width(expr.left))
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr.left)
                    if eff_signed:
                        program.append(instr(Op.SIGN_EXT, target, 0))
                    else:
                        program.append(instr(Op.RESIZE, target))
                    static_result_w = target
                self._compile_expr(expr.right, program, self._expr_width(expr.right))
            # Bitwise ops (IEEE 1364-2005 §5.5.2): combine at the operator's
            # own natural width (max of the two operands' self-determined
            # widths) first -- each operand extended using its OWN
            # individual signedness only far enough to align with the
            # other, NOT extended straight to some wider outer `width`
            # using its own signedness (which would let one signed
            # operand's sign-extension -- e.g. of an X value -- smear
            # across the whole outer width even when the operator's own
            # COMBINED signedness, which is what should govern the outer
            # extension, is unsigned). The combined result is then extended
            # separately to `width` using the whole BinaryOp's own combined
            # signedness. Mirrors the identical fix in `sim/evaluator.py`
            # (see notes/known_issues.md).
            elif expr.op in ("&", "|", "^", "~^", "^~"):
                # `signed_override` must NEVER be forwarded into either
                # operand's own recursive `_compile_expr` call, nor govern
                # their own widen-to-`op_width` step below -- a bitwise
                # op's combined signedness is entirely SELF-CONTAINED
                # (determined solely by its own two operands' individual
                # types), unlike `%`'s divisor-widening override (say), which
                # genuinely needs to force a decision into a DIFFERENT
                # operator. Forwarding it here would leak an unrelated outer
                # decision into a NESTED, structurally-unrelated operand's
                # own independent signed/unsigned computation, corrupting
                # its computed VALUE. Only the RESULT-level extension below
                # (`if width and width != op_width`) may still consult it,
                # since that governs how THIS bitwise op's already-computed
                # result gets read by the outer context. Mirrors the
                # identical fix in `sim/evaluator.py`'s bitwise branch (see
                # its docstring for the concrete Icarus-confirmed repro,
                # `(|a3[45]) % (($signed(a4[23]) - a0) | 1)`) and
                # `_wide_emitter.py`'s/`_expr_emitter.py`'s equivalent
                # `combined_override` fixes.
                # `width` (the OUTER destination context, when known) is
                # folded in as a FLOOR on op_width, not a replacement for
                # the max-of-operands computation -- needed for a `~`/
                # unary-`-` operand: it must extend ITS OWN operand to the
                # width it's compiled AT before complementing/negating, not
                # complement/negate narrow then get zero-extended
                # afterward. When such an operand's own self-width already
                # equals the OTHER operand's self-width, op_width alone
                # doesn't widen it at all. Mirrors the identical fix in
                # `sim/evaluator.py` (see its docstring for the concrete
                # Icarus-confirmed repro, `o5 | ~i3[6:3]`).
                #
                # CORRECTION (August 2026, two parts, both mirrored exactly
                # in `sim/evaluator.py` -- see its own docstring for the
                # full writeup and Icarus repros): (1) folding `width` into
                # `op_width` UNCONDITIONALLY (for both operands, not just
                # one that actually needs it) let a plain/nested operand
                # (not a `~`/unary-`-`) get compiled at the outer width too,
                # so ITS OWN individual signedness could sign-extend it
                # past what this operator's combined signedness intends --
                # only widen a SPECIFIC operand's own compile width when
                # that operand is itself `~`/unary `-`. (2) the alignment
                # step (extending the narrower operand up to `op_width`)
                # must use the OPERATOR's own COMBINED signedness
                # (`both_signed`, also forced as the compiled-in
                # `signed_override` for each operand's own recursive
                # compilation -- mirrors the comparison-operator fix
                # earlier in this file), not each operand's own individual
                # signedness -- IEEE 1364-2005 SS5.5.2's combining rule
                # ("if any operand is unsigned, the result is unsigned")
                # governs this alignment too, not just the final
                # RESULT-to-`width` extension.
                natural_op_width = max(self._expr_width(expr.left), self._expr_width(expr.right))
                both_signed = self._expr_signed(expr.left) and self._expr_signed(expr.right)
                left_compile_width = (
                    max(natural_op_width, width or 0)
                    if isinstance(expr.left, UnaryOp) and expr.left.op in ("~", "-")
                    else natural_op_width
                )
                right_compile_width = (
                    max(natural_op_width, width or 0)
                    if isinstance(expr.right, UnaryOp) and expr.right.op in ("~", "-")
                    else natural_op_width
                )
                # `op_width` is fully static (derived only from the AST,
                # not from anything computed at compile time), so it can
                # -- and must -- be computed once up front, THEN used to
                # align both operands, exactly like the original
                # unconditional-SIGN_EXT/RESIZE-after-each-operand
                # structure below (the VM's own SIGN_EXT/RESIZE opcodes are
                # no-ops when the value is already at the target width).
                op_width = max(natural_op_width, left_compile_width, right_compile_width)
                self._compile_expr(expr.left, program, left_compile_width, both_signed)
                program.append(instr(Op.SIGN_EXT if both_signed else Op.RESIZE, op_width, 0))
                self._compile_expr(expr.right, program, right_compile_width, both_signed)
                program.append(instr(Op.SIGN_EXT if both_signed else Op.RESIZE, op_width, 0))
                op = _BINARY_OP_MAP.get(expr.op)
                if op is None:
                    raise ValueError(f"Unknown binary operator: {expr.op!r}")
                program.append(instr(op))
                if width and width != op_width:
                    result_eff_signed = signed_override if signed_override is not None else both_signed
                    if result_eff_signed:
                        program.append(instr(Op.SIGN_EXT, width, 0))
                    else:
                        program.append(instr(Op.RESIZE, width))
                return
            else:
                # Context-determined arithmetic (+,-,*,/,%): each operand
                # is compiled FIRST at its OWN self-determined width
                # (respecting whatever internal signedness/cast decisions
                # it has for THAT computation, without letting the outer
                # `width` reach down into it), then SEPARATELY extended to
                # a common target width using the OPERATOR's own COMBINED
                # signedness (IEEE 1364-2005 SS5.5.2: "any context-
                # determined operand shall be the SAME TYPE AND SIZE as
                # the RESULT of the operator") -- NOT the operand's own
                # individual signedness, which would let e.g. a
                # $signed(...)-cast operand's sign-extension leak into the
                # sum even when the OPERATOR's combined signedness
                # (governed by the OTHER, unsigned operand) says the whole
                # expression should be unsigned.
                #
                # Extending to the COMMON target width BEFORE combining
                # (rather than combining at a narrower natural width and
                # extending the RESULT afterward, which works fine for
                # bitwise ops -- see above) matters specifically for
                # +/-/*//: they have a carry/borrow chain that can't be
                # computed with partial unknowns (`Op.ADD` etc., via
                # `Value.__add__`, already implement "any x bit ANYWHERE
                # in either operand makes the ENTIRE result x") -- that
                # rule must see the FULL target width's worth of operand
                # bits to correctly taint the whole result; combining
                # narrower and widening the (by-then-already-resolved)
                # result afterward would incorrectly leave the
                # destination's extended bits looking definite even when
                # a genuine x exists elsewhere in a wider-context operand.
                #
                # Confirmed against Icarus for `($signed($unsigned({a1,
                # a0, a1[5]})) + (~|((-a4) ^ a1)))`: with `a0` defined,
                # the unsigned reduction operand forces the whole
                # addition's combined signedness unsigned, so the sum
                # zero-extends into the 96-bit destination regardless of
                # the cast's own sign-extend decision; with `a0` x, the
                # ENTIRE 96-bit result must be x, not just the cast
                # operand's own narrow 10-bit self-width. Mirrors the
                # identical fix in `sim/evaluator.py`.
                # Each operand is compiled directly AT the full target
                # width (not its own self-width, resized afterward) with
                # `signed_override` reset to None so a nested context-
                # determined operator (unary `-`, a further `+`/`-`, a
                # `$signed`/`$unsigned` cast) sees `target` propagate all
                # the way down through the recursive `_compile_expr` call
                # and applies its OWN sign/zero-extension decision (e.g.
                # negating an unsigned operand AFTER it has been RESIZE'd
                # to the target width, not before) rather than being
                # compiled at a narrower self-width and passively resized
                # after the fact -- those two only agree for width-
                # commuting operators, and unary `-` is not one of them.
                # Confirmed wrong against Icarus for `(-a5) - {(~&(~|a7)),
                # a2, a6[63]}` (a5 unsigned, 65 bits): compiling `-a5` at
                # its own self-width 65 and RESIZE-ing the negation result
                # to 96 afterward gives a different (wrong) value than
                # RESIZE-ing `a5` to 96 bits first and negating at that
                # width, since two's-complement negation does not commute
                # with zero-extension. Mirrors the identical fix in
                # `sim/evaluator.py`.
                #
                # `+ - * / %` ALL need the OPERATOR's combined signedness
                # (signed only if BOTH operands are signed, IEEE 1364-2005
                # §5.5.1) to govern EVERY operand's own extension here --
                # not each operand's own individual declared type. This
                # used to special-case `/`/`%` alone for this, reasoning
                # that `+`/`-`/`*` are "residue-safe" (their modular
                # arithmetic gives the same answer regardless of HOW each
                # operand was individually extended, as long as each
                # operand's bit pattern is "correct" at the target width)
                # -- but that reasoning is simply wrong: sign- vs zero-
                # extending a signed operand produces a DIFFERENT integer
                # value (e.g. a 1-bit signed `1` means -1 sign-extended
                # but +1 zero-extended), and `(a - b) mod N` genuinely
                # differs depending on WHICH of those two different values
                # `a` is taken to be -- "residue-safe" only holds once each
                # operand's value is ALREADY fixed, it says nothing about
                # which extension choice fixes it correctly in the first
                # place. Per §5.5.1, when a signed operand is paired with
                # an unsigned one in `+`/`-`/`*`, the WHOLE operation reads
                # as unsigned, and the signed operand's bit pattern must be
                # read as its RAW unsigned magnitude (zero-extended), not
                # resurrected as negative via its own declared type.
                # Confirmed against Icarus for `(sa - ub)` with `sa` a
                # signed 1-bit register holding `1` (i.e. -1) and `ub` an
                # unsigned 2-bit `0`: Icarus gives `1` (zero-extending
                # `sa` to +1 first, per the pair's combined-unsigned type),
                # not `-1`/`3` (sign-extending `sa` on its own, what
                # individual-signedness compilation computed here before
                # this fix) -- the same bug shape already fixed for `/`/
                # `%` below, comparisons, and bitwise ops, just missed for
                # the other three arithmetic operators specifically
                # because of this flawed argument. `both_signed` is forced
                # into both recursive `_compile_expr` calls so this
                # combined decision -- not each operand's own type --
                # governs every extension nested within either operand too
                # (a `$signed`/`$unsigned` cast reached within either
                # operand still wins over this override, exactly like
                # every other signed_override use in this function).
                # Mirrors the identical fix in `sim/evaluator.py`.
                target = max(width, self._expr_width(expr.left), self._expr_width(expr.right))
                both_signed = self._expr_signed(expr.left) and self._expr_signed(expr.right)
                self._compile_expr(expr.left, program, target, both_signed)
                self._compile_expr(expr.right, program, target, both_signed)
                static_result_w = target + target if expr.op == "*" else target
            # Detect signed comparison: both operands must be signed
            if expr.op in _SIGNED_CMP_MAP and self._expr_signed(expr.left) and self._expr_signed(expr.right):
                program.append(instr(_SIGNED_CMP_MAP[expr.op]))
            elif expr.op in _SIGNED_DIVMOD_MAP and self._expr_signed(expr.left) and self._expr_signed(expr.right):
                program.append(instr(_SIGNED_DIVMOD_MAP[expr.op]))
            # Signed power: IEEE 1364-2005 §5.5.1 ("if all operands are
            # signed, the result will be signed") plus Table 5-6's
            # negative-base/negative-exponent special values only apply
            # under a genuinely signed interpretation -- Op.POW (used by
            # the `else` branch below) always treats both operands as
            # unsigned raw bit patterns, correct for the unsigned case
            # (an unsigned exponent can never be negative, so Table 5-6's
            # special cells never trigger) but wrong once either
            # operand's two's-complement bit pattern is meant to be read
            # as negative -- and dangerous: a "negative" exponent stored
            # as a raw unsigned bit pattern is astronomically large,
            # which Op.POW would previously have tried to compute
            # directly. Mirrors the identical fix in `sim/evaluator.py`.
            elif expr.op in _SIGNED_POW_MAP and self._expr_signed(expr.left) and self._expr_signed(expr.right):
                program.append(instr(_SIGNED_POW_MAP[expr.op]))
            else:
                op = _BINARY_OP_MAP.get(expr.op)
                if op is None:
                    raise ValueError(f"Unknown binary operator: {expr.op!r}")
                program.append(instr(op))
            # The RESULT must end up at exactly the caller's requested
            # `width` (mirroring the already-correct bitwise-op branch
            # above), not just whatever width the emitted opcode happens
            # to produce at runtime -- two distinct gaps, both silently
            # corrupting `.concat()`'s bit-packing whenever this BinaryOp
            # lands as a ternary branch or concat member (no wider
            # top-level assignment step around to paper over it):
            #  - Comparisons/&&/|| are self-determined-always-1-bit (IEEE
            #    1364-2005 Table 5-22) -- their 1-bit result was simply
            #    never extended to a wider requested `width` at all.
            #    Confirmed wrong against Icarus for `{8'hAA, (cond ? (a ==
            #    b) : c)}` with `c` 64 bits.
            #  - Multiplication's runtime result (see the arithmetic
            #    branch above) is the SUM of its operand widths, which is
            #    virtually always wider than a self-determined `width`
            #    request and was never narrowed back down. Confirmed
            #    wrong against Icarus for `(-{$signed({2{a7}}), ((a3 ?
            #    a1[0] : a6[64]) * (a1[6] < a1[2:0]))})`.
            # `static_result_w` (1 for comparisons/&&/||, set above for
            # shifts and the arithmetic branch) is the statically-known
            # runtime width at this point; mirrors the identical fix in
            # `sim/evaluator.py`.
            result_w = 1 if expr.op in _FIXED_SELF_DETERMINED_BINOPS else static_result_w
            if width and result_w != width:
                eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                if eff_signed:
                    program.append(instr(Op.SIGN_EXT, width, 0))
                else:
                    program.append(instr(Op.RESIZE, width))
            return

        # -- UnaryOp --
        if etype is UnaryOp:
            op = _UNARY_OP_MAP.get(expr.op)
            if op is None:
                raise ValueError(f"Unknown unary operator: {expr.op!r}")

            # ~/+/- are context-determined (IEEE 1364-2005 Table 5-22):
            # compile the operand with the surrounding context width, extend
            # it BEFORE the operator runs. `~` used to be treated as
            # self-determined here (compile at the operand's own width,
            # extend the RESULT afterward) -- that's wrong for unsigned
            # operands, since zero-extension doesn't commute with bitwise
            # complement (only sign-extension does), confirmed against
            # Icarus/Verilator (see notes/known_issues.md).
            if expr.op in ("~", "+", "-"):
                # `~` used to be special-cased here: compile at the
                # operand's own FIXED self-determined width first (when
                # the operand is itself a comparison/reduction/&&/||/!
                # result), THEN extend the RESULT -- on the theory that
                # zero-extending a fixed-width value and THEN
                # complementing flips the newly-added padding bits too
                # (wrong), unlike unary `-`, left on the normal "compile
                # at context width FIRST, then apply" path below since
                # two's-complement negation of an already-extended value
                # gives the correct modular wraparound regardless. A
                # systematic truth-table sweep (see notes/known_issues.md's
                # seed 2182 entry) found this backwards for `~`: Icarus
                # extends the operand to context width FIRST, THEN applies
                # `~` to the WHOLE extended value, exactly like unary `-`
                # already does -- there is no special case for either
                # operator. Confirmed against Icarus for `~(a && b)` in a
                # 16-bit unsigned context with `a=b=1`: Icarus gives
                # `16'hFFFE`, not the `16'h0000` the old special case
                # computed. `~` now simply falls through to the same
                # context-determined path as `+`/`-` below. Mirrors the
                # identical fix in `sim/evaluator.py`.
                # Compile the operand directly at `target` (max of the
                # enclosing context width and its own static
                # self-determined width), NOT `width` alone -- mirrors the
                # identical shift left-operand fix a few hundred lines up
                # (see its comment for the full "truncate-before-widen"
                # rationale): `_compile_expr` for some node types
                # (confirmed: `Concatenation`) directly SIZES its result to
                # whatever width it's asked to compile at, so requesting
                # the narrower `width` here would already discard bits
                # above it DURING compilation, before the negation (and
                # this RESIZE/SIGN_EXT) ever run. That matters specifically
                # for `-`: unlike `~`/bitwise ops, a SINGLE x/z bit
                # ANYWHERE in the operand makes the ENTIRE negation result
                # x (no per-bit borrow-chain precision -- matches Icarus),
                # so discarding an x-bearing HIGH portion of a wider
                # operand before negating silently turns a genuinely-all-x
                # result into a spurious fully-defined one. Confirmed
                # against Icarus (cross-engine, vm/vm-fast/compiled vs
                # reference) for `(-{a3, (^a5[16]), {3{(a7 ? a6[66:21] :
                # a3)}}})` widened into a 96-bit destination with `a3`
                # (63 bits, fully x) positioned as the concatenation's
                # MSB-most member: the concatenation's own true width is
                # 202 bits, so `a3`'s x-bearing bits sit at positions
                # 139-201, entirely above the 96-bit truncation point --
                # compiling the concatenation at 96 bits directly dropped
                # them before negation ever saw them, giving a fully
                # defined (wrong) result instead of Icarus's all-x one.
                target = max(width, self._expr_width(expr.operand)) if width else width
                self._compile_expr(expr.operand, program, target, signed_override)
                if width:
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr.operand)
                    if eff_signed:
                        program.append(instr(Op.SIGN_EXT, target, 0))
                    else:
                        program.append(instr(Op.RESIZE, target))
            else:
                # `!` and the reduction ops are themselves self-determined
                # (always 1 bit), but their OPERAND is still
                # self-determined too (IEEE 1364-2005 §5.4.1) -- its OWN
                # natural width is the context that resizes any nested
                # context-determined operator within it. Compiling with
                # width=0 here leaves such a nested operator entirely
                # unresized -- mirrors the identical fix in
                # `sim/evaluator.py`; confirmed wrong against Icarus for
                # `!(~(a2[2] ? a6[49] : a6[38:9]))`.
                self._compile_expr(expr.operand, program, self._expr_width(expr.operand))
                program.append(instr(op))
                # The OPERATOR's own 1-bit RESULT must still be extended
                # to the caller's requested `width` when this UnaryOp is
                # itself embedded in a wider context -- this was simply
                # never done here, unlike every sibling
                # self-determined-fixed-width case (`~`/unary `-` on such
                # an operand above, BinaryOp comparison/&&/|| results)
                # which all resize their RESULT after computing it at its
                # own fixed width. Always unsigned in its own right (IEEE
                # 1364-2005 §5.5.1), except when signed_override forces
                # sign-extension. Mirrors the identical fix in
                # `sim/evaluator.py`; confirmed wrong against Icarus for
                # `(a1[2:1] ? a3 : (~&{2{a4}}))` used as a concat member
                # alongside a wider sibling.
                if width and width > 1:
                    eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                    if eff_signed:
                        program.append(instr(Op.SIGN_EXT, width, 0))
                    else:
                        program.append(instr(Op.RESIZE, width))
                return
            program.append(instr(op))
            return

        # -- TernaryOp (evaluate both branches, merge on x-condition) --
        if etype is TernaryOp:
            # IEEE 1364-2005 §5.5.1: the conditional operator's own combined
            # signedness (signed only if BOTH branches are signed) governs
            # every extension needed while compiling either branch -- not
            # each branch's own individual signedness. This establishes a
            # *fresh* override for both branches (see `signed_override` on
            # `_compile_expr`), replacing whatever override was active from
            # further out; a nested TernaryOp will similarly establish its
            # own fresh override for its own branches, so this one's
            # override does not reach past it.
            #
            # `width` is propagated straight into BOTH branches (mirroring
            # the reference evaluator's dynamic dispatch, which propagates
            # it into whichever branch is actually selected at runtime): a
            # branch that itself consumes width (Identifier, BinaryOp,
            # nested UnaryOp +/-, nested TernaryOp) extends using the
            # propagated override; other kinds (FunctionCall, Concatenation,
            # Replication, selects) ignore it entirely, exactly as they do
            # at the top level, and simply return their own self-determined
            # value -- there is deliberately no extra extension attempted
            # at the ternary's own level (matching the reference evaluator,
            # which just returns whichever branch's eval() produced with no
            # further wrapping).
            #
            # Op.TERNARY does not require its two operands to share a width:
            # when the condition is fully defined it picks one operand
            # wholesale (using that operand's own width/value), and when the
            # condition is x/z it merges bit-by-bit, zero-padding the
            # narrower operand.
            own_signed = self._expr_signed(expr)
            # The condition is self-determined (IEEE 1364-2005 Table
            # 5-22): its OWN natural width is the context that resizes any
            # nested context-determined operator within it -- compiling
            # with width=0 here leaves such a nested operator entirely
            # unresized, the same bug already fixed for the `!`/
            # reduction-op operand above, one more leaf position. Mirrors
            # the identical fix in `sim/evaluator.py`; confirmed wrong
            # against Icarus for `(($unsigned(a1[5]) ? (a4 ^ a4[1]) :
            # (~a0)) ? a0 : (^{2{a0}}))`.
            self._compile_expr(expr.condition, program, self._expr_width(expr.condition))
            # Each branch must be compiled at (at least) the ternary's OWN
            # combined self-determined width -- max of both branches' self-
            # widths, per IEEE 1364-2005 Table 5-22's `L(i) = L(j)`
            # context-determined-among-each-other rule for `?:` -- not
            # whatever `width` this TernaryOp node itself happened to be
            # called with. Blindly forwarding a caller's `width=0` (self-
            # determined request, e.g. from `&&`/`||`, which never
            # propagate a shared width into their operands) straight into
            # `_compile_expr(branch, program, width, ...)` left a nested
            # context-determined operator WITHIN a branch (e.g. `~a0`)
            # unresized at its own narrow width (1 bit) instead of the
            # ternary's true combined width. Mirrors the identical fix in
            # `sim/evaluator.py`; confirmed wrong against Icarus for
            # `(((-a4[17:9]) ? (~a0) : a3) && a4)`.
            own_width = max(width, self._expr_width(expr.true_expr), self._expr_width(expr.false_expr))
            self._compile_expr(expr.true_expr, program, own_width, own_signed)
            self._compile_expr(expr.false_expr, program, own_width, own_signed)
            program.append(instr(Op.TERNARY))
            return

        # -- Concatenation --
        if etype is Concatenation:
            # Each member is self-determined (IEEE 1364-2005 §5.4.1): its
            # OWN natural width is the "context" that resizes any
            # context-determined operator (~, arithmetic, a nested ternary)
            # within it. Compiling with width=0 (the default) would leave
            # those nested operators entirely unresized -- confirmed wrong
            # against Icarus (see notes/known_issues.md), and matches the
            # identical fix in `sim/evaluator.py`'s reference engine.
            for part in expr.parts:
                self._compile_expr(part, program, self._expr_width(part))
            program.append(instr(Op.CONCAT, len(expr.parts)))
            # A concatenation is always unsigned in its OWN right, so its
            # own aggregate result normally needs no further extension --
            # but when the WHOLE concatenation is wrapped in `$signed(...)`
            # (signed_override True) and requested at a wider `width`,
            # that aggregate needs sign-extension, not zero-extension.
            # Individual MEMBERS never see signed_override (compiled
            # self-determined, per the calls above) -- this only concerns
            # the concat's own aggregate value. Mirrors the identical fix
            # in `sim/evaluator.py`.
            if width:
                program.append(instr(Op.SIGN_EXT if signed_override else Op.RESIZE, width, 0))
            return

        # -- StreamingConcatenation (`{<<[N]{...}}`, IEEE 1800-2017
        # SS11.4.14.1) -- only ever built for `<<`; `>>` desugars straight
        # to Concatenation at AST-build time. Compile the plain
        # concatenation of parts exactly as above, then chunk-reverse it.
        if etype is StreamingConcatenation:
            for part in expr.parts:
                self._compile_expr(part, program, self._expr_width(part))
            program.append(instr(Op.CONCAT, len(expr.parts)))
            slice_size = 1
            if expr.slice_size is not None:
                resolved = _const_int(expr.slice_size, self._param_env)
                if resolved is None:
                    raise NotImplementedError(
                        f"Streaming concatenation slice_size must be a compile-time constant (got {expr.slice_size!r})"
                    )
                slice_size = resolved
            if slice_size <= 0:
                raise ValueError(f"Streaming concatenation slice_size must be positive, got {slice_size}")
            if slice_size > 64:
                # Would need multi-word chunk extraction in the Cython
                # vm-fast interpreter's fixed-width word-array
                # representation; `vm`/`reference` have no such limit
                # (Value.stream_reverse is arbitrary-precision), but the
                # bytecode is shared across `vm`/`vm-fast` and must behave
                # identically on both.
                raise NotImplementedError(
                    f"Streaming concatenation slice_size > 64 is not supported (got {slice_size})"
                )
            program.append(instr(Op.STREAM_REVERSE, slice_size))
            if width:
                program.append(instr(Op.SIGN_EXT if signed_override else Op.RESIZE, width, 0))
            return

        # -- BitSelect (memory read or scalar bit-select) --
        if etype is BitSelect:
            # A bit-select is always unsigned in its own right (IEEE
            # 1364-2005 §5.5.1) -- but when nested inside a
            # $signed()-wrapped context (signed_override True, forced by
            # an outer FunctionCall/ternary/bitwise-op), a wider requested
            # `width` needs sign-, not zero-, extension. Without this, a
            # bit-select nested one level deeper than an assignment's own
            # top-level RHS (e.g. a ternary branch) never gets extended at
            # all -- mirrors the identical fix in `sim/evaluator.py`.
            if type(expr.target) is Identifier and self._is_memory(self._resolve_id_name(expr.target)):
                mid = self.mem_map[self._resolve_id_name(expr.target)]
                self._compile_expr(expr.index, program)
                program.append(instr(Op.LOAD_MEM, mid))
            else:
                self._compile_expr(expr.target, program)
                self._compile_expr(expr.index, program)
                self._append_base_adjust(program, self._select_base(expr.target))
                program.append(instr(Op.BIT_SELECT))
            if width:
                program.append(instr(Op.SIGN_EXT if signed_override else Op.RESIZE, width, 0))
            return

        # -- RangeSelect --
        if etype is RangeSelect:
            # Same signed_override reasoning as BitSelect above.
            self._compile_expr(expr.target, program)
            self._compile_expr(expr.msb, program)
            base = self._select_base(expr.target)
            self._append_base_adjust(program, base)
            self._compile_expr(expr.lsb, program)
            self._append_base_adjust(program, base)
            program.append(instr(Op.RANGE_SELECT))
            if width:
                program.append(instr(Op.SIGN_EXT if signed_override else Op.RESIZE, width, 0))
            return

        # -- Replication --
        if etype is Replication:
            self._compile_expr(expr.count, program)
            # Self-determined, same reasoning as Concatenation above.
            self._compile_expr(expr.value, program, self._expr_width(expr.value))
            program.append(instr(Op.REPLICATE))
            # Same signed_override reasoning as Concatenation's own
            # aggregate result above.
            if width:
                program.append(instr(Op.SIGN_EXT if signed_override else Op.RESIZE, width, 0))
            return

        # -- AssignmentPattern --
        if etype is AssignmentPattern:
            self._compile_assignment_pattern(expr, program, width)
            return

        # -- PartSelect --
        if etype is PartSelect:
            # Same signed_override reasoning as BitSelect above.
            self._compile_expr(expr.target, program)
            self._compile_expr(expr.base, program)
            self._append_base_adjust(program, self._select_base(expr.target))
            self._compile_expr(expr.width, program)
            if expr.direction == "+:":
                program.append(instr(Op.PART_SEL_UP))
            else:
                program.append(instr(Op.PART_SEL_DOWN))
            if width:
                program.append(instr(Op.SIGN_EXT if signed_override else Op.RESIZE, width, 0))
            return

        # -- FunctionCall --
        if etype is FunctionCall:
            fname = expr.name.lower()
            if fname in ("$signed", "$unsigned") and expr.arguments:
                # $signed/$unsigned are transparent to VALUE -- they only
                # mark the expression's signedness for the ENCLOSING
                # context's extension decision. `_compile_function_call`'s
                # own $signed/$unsigned branch compiles the argument
                # self-determined with no width, which only happens to
                # work when $signed(...) is the assignment's own top-level
                # RHS (a separate post-hoc sign-extend emitted by the
                # caller covers for it there). Nested one level deeper --
                # e.g. a ternary branch -- that top-level cover doesn't
                # reach, and the cast's own argument never gets extended
                # to the ternary's combined width at all.
                #
                # CORRECTION (August 2026): the `{3{(a0 ? $signed(a4[4:2])
                # : a3)}}` citation this comment used to cite was
                # re-verified directly against Icarus and found to have
                # been checked with `a3` mistakenly declared `signed` (a
                # degenerate case where the ternary's own combined
                # signedness and the cast's own decision happen to agree,
                # so it can't distinguish the two rules). With `a3`
                # correctly `unsigned` (its real declaration), Icarus
                # zero-extends -- the ternary's own combined UNSIGNED
                # signedness wins, `$signed` does NOT force its own
                # decision. Mirrors the identical correction in
                # `sim/evaluator.py`; found while root-causing seed 2197
                # of the `random-verilog-gen` fuzzer batch. Only fall back
                # to the cast's own decision when NO override is active
                # (`signed_override is None`).
                #
                # Directly-nested casts (`$unsigned($signed(x))`) are still
                # unwrapped here so the OUTERMOST cast's OWN decision (not
                # the inner one) is what falls back to when no override is
                # active -- unchanged by this correction, still confirmed
                # via `test_differential_statements.py` phase 1's
                # `$unsigned($signed((a < b)))` case (no enclosing ternary
                # there, so `signed_override` was already `None` and this
                # distinction never mattered for that specific citation).
                inner = expr.arguments[0]
                while (
                    isinstance(inner, FunctionCall)
                    and inner.name.lower() in ("$signed", "$unsigned")
                    and inner.arguments
                ):
                    inner = inner.arguments[0]
                # $signed/$unsigned are themselves SELF-DETERMINED (IEEE
                # 1364-2005 Table 5-22): compile the argument at its OWN
                # self-determined width, not the width requested by
                # whatever outer context-determined operator is asking for
                # this cast's value -- the cast's job is only to decide
                # sign- vs zero-extension when the (already self-width-
                # compiled) result is later widened to that outer `width`.
                # Compiling `inner` directly at the outer `width` used to
                # force a nested context-determined operator inside the
                # cast (e.g. `%`) to propagate that OUTER width into ITS
                # OWN operands too, which is wrong. Mirrors the identical
                # fix in `sim/evaluator.py`; confirmed wrong against Icarus
                # for `$signed((a3 % (a0 | 1))) + a1`.
                inner_w = self._expr_width(inner)
                self._compile_expr(inner, program, inner_w, None)
                if width and inner_w != width:
                    eff_signed = signed_override if signed_override is not None else (fname == "$signed")
                    program.append(instr(Op.SIGN_EXT if eff_signed else Op.RESIZE, width, 0))
                return
            self._compile_function_call(expr, program)
            return

        # -- StringLiteral --
        if etype is StringLiteral:
            val = 0
            for ch in expr.value:
                val = (val << 8) | ord(ch)
            cid = self._add_const(Value(val, width=len(expr.value) * 8))
            program.append(instr(Op.LOAD_CONST, cid))
            return

        # -- Mintypmax --
        if etype is Mintypmax:
            self._compile_expr(expr.typ_val, program)
            return

        raise TypeError(f"Cannot compile expression type: {type(expr).__name__}")

    def _compile_assignment_pattern(
        self,
        expr: AssignmentPattern,
        program: list[tuple[int, int, int]],
        width: int,
    ) -> None:
        """Compile a packed assignment pattern as a concatenation of field values."""
        from ..elaborate import match_assignment_pattern_layout  # noqa: PLC0415

        if expr.named_pairs:
            layout = match_assignment_pattern_layout(expr, self._struct_type_map)
            if layout is None:
                raise ValueError(f"Cannot find matching struct layout for assignment pattern: {expr!r}")
            named_values = {name: value_expr for name, value_expr in expr.named_pairs}
            ordered_fields = sorted(layout.fields.items(), key=lambda item: item[1][0], reverse=True)
            for field_name, (_offset, field_width) in ordered_fields:
                field_expr = named_values.get(field_name, expr.default_value)
                if field_expr is None:
                    cid = self._add_const(Value(0, width=field_width))
                    program.append(instr(Op.LOAD_CONST, cid))
                    continue
                self._compile_expr(field_expr, program, width=field_width)
                if self._expr_width(field_expr) != field_width:
                    program.append(instr(Op.RESIZE, field_width))
            program.append(instr(Op.CONCAT, len(ordered_fields)))
            if width and layout.total_width != width:
                program.append(instr(Op.RESIZE, width))
            return

        if expr.positional:
            # Self-determined, same reasoning as Concatenation above.
            for part in expr.positional:
                self._compile_expr(part, program, self._expr_width(part))
            program.append(instr(Op.CONCAT, len(expr.positional)))
            total_width = sum(self._expr_width(part) for part in expr.positional)
            if width and total_width != width:
                program.append(instr(Op.RESIZE, width))
            return

        if expr.default_value is not None:
            self._compile_expr(expr.default_value, program, width=width)
            if width and self._expr_width(expr.default_value) != width:
                program.append(instr(Op.RESIZE, width))
            return

        cid = self._add_const(Value(0, width=width or 1))
        program.append(instr(Op.LOAD_CONST, cid))

    def _compile_literal(self, lit: Literal) -> Value:
        """Convert a model Literal to a Value for the constant pool."""
        width = lit.width or 32

        # If original_text is available, it preserves per-bit x/z info
        # (e.g. 4'b1xxx → val=8, mask=7).  Check it first.
        if lit.original_text:
            try:
                return Value.from_verilog(lit.original_text)
            except ValueError:
                pass

        if lit.is_x or lit.is_z:
            return Value.x(width)

        if isinstance(lit.value, (int, float)):
            return Value(int(lit.value), width=width)

        if isinstance(lit.value, str):
            text = lit.value.strip()
            try:
                return Value(int(text, 0), width=width)
            except (ValueError, TypeError):
                return Value.x(width)

        return Value.x(width)

    def _compile_function_call(self, call: FunctionCall, program: list[tuple[int, int, int]]) -> None:  # noqa: PLR0912, PLR0911, PLR0915
        """Compile a system function call."""
        name = call.name.lower()

        if name == "$clog2":
            if call.arguments:
                self._compile_expr(call.arguments[0], program)
            else:
                cid = self._add_const(Value(0, width=32))
                program.append(instr(Op.LOAD_CONST, cid))
            program.append(instr(Op.FUNC_CLOG2))
            return

        if name in ("$signed", "$unsigned"):
            if call.arguments:
                self._compile_expr(call.arguments[0], program)
            else:
                cid = self._add_const(Value.x(32))
                program.append(instr(Op.LOAD_CONST, cid))
            return

        if name == "$bits":
            if call.arguments:
                arg0 = call.arguments[0]
                # Check for typedef name: $bits(typename)
                w = None
                if isinstance(arg0, Identifier):
                    bits_key = f"$bits:{arg0.name}"
                    if bits_key in self._param_env:
                        w = int(self._param_env[bits_key])
                if w is None:
                    w = self._expr_width(arg0)
                cid = self._add_const(Value(w, width=32))
                program.append(instr(Op.LOAD_CONST, cid))
            else:
                cid = self._add_const(Value(0, width=32))
                program.append(instr(Op.LOAD_CONST, cid))
            return

        if name == "$time":
            program.append(instr(Op.SYS_TIME))
            return

        if name == "$realtime":
            # $realtime returns real; we use integer time, so same as $time
            program.append(instr(Op.SYS_TIME))
            return

        if name == "$stime":
            # $stime returns lower 32 bits of simulation time
            program.append(instr(Op.SYS_TIME))
            program.append(instr(Op.RESIZE, 32))
            return

        if name == "$random":
            program.append(instr(Op.FUNC_RANDOM))
            return

        if name == "$fopen":
            # $fopen(filename [, mode]) — filename/mode stored in fopen_tasks table
            if call.arguments:
                fname_expr = call.arguments[0]
                fname = fname_expr.value if isinstance(fname_expr, StringLiteral) else str(fname_expr)
                mode = "w"
                if len(call.arguments) >= 2:
                    mode_expr = call.arguments[1]
                    mode = mode_expr.value if isinstance(mode_expr, StringLiteral) else "w"
                task_id = len(self.fopen_tasks)
                self.fopen_tasks.append((fname, mode))
                program.append(instr(Op.SYS_FOPEN, task_id))
                return
            program.append(instr(Op.LOAD_CONST, self._add_const(Value(0, width=32))))
            return

        if name == "$feof":
            # $feof(fd) — push fd, then SYS_FEOF
            if call.arguments:
                self._compile_expr(call.arguments[0], program)
            else:
                program.append(instr(Op.LOAD_CONST, self._add_const(Value(0, width=32))))
            program.append(instr(Op.SYS_FEOF))
            return

        # User-defined function call
        func = self._function_map.get(call.name)
        if func is not None:
            self._compile_user_function(func, call, program)
            return

        raise NotImplementedError(
            f"VM bytecode compiler: unknown function '{name}' — user-defined functions not yet supported"
        )

    def _compile_user_function(self, func, call: FunctionCall, program: list[tuple[int, int, int]]) -> None:
        """Inline a user-defined function call at the call site."""
        from veriforge.model.functions import FunctionDecl
        from veriforge.model.ports import PortDirection

        func: FunctionDecl
        depth = self._func_call_depth
        prefix = f"__func_{func.name}_{depth}"
        # Bump `_func_call_depth` immediately, BEFORE compiling this
        # call's own ARGUMENTS (not just its body, as the code used to)
        # -- a NESTED user-function call reached while compiling one of
        # THIS call's arguments (e.g. `fn_xor64s(fn_xor64s(a, b), c)`)
        # must get a DIFFERENT depth than this (outer) call's own, or it
        # computes the identical `depth` value here (since the increment
        # previously happened only right before the BODY, well after the
        # argument loop) and therefore the IDENTICAL `__func_{name}_
        # {depth}` prefix -- meaning the inner and outer calls to the
        # SAME function silently share the SAME local port/return
        # signals, and the inner call's own argument-binding writes (its
        # own STORE_SIG into `.a`/`.b`) corrupt the outer call's
        # not-yet-consumed ones. Confirmed against Icarus (cross-engine,
        # `reference`/`compiled` already agreed) for `fn_xor64s(a2[12:0],
        # {fn_xor64s(a0, a2[0]), $signed(a2)})`: the outer call's own
        # `a2[12:0]` argument value got corrupted by the inner
        # `fn_xor64s(a0, a2[0])` call sharing its port signals.
        self._func_call_depth += 1

        # Determine return width
        ret_width = 32
        if func.return_range is not None:
            if isinstance(func.return_range.msb, Literal) and isinstance(func.return_range.lsb, Literal):
                ret_width = abs(int(func.return_range.msb.value) - int(func.return_range.lsb.value)) + 1
        elif func.return_kind == "integer":
            ret_width = 32

        # Register local signals for ports and return variable
        port_sids: list[int] = []
        for port in func.ports:
            local_name = f"{prefix}.{port.name}"
            w = _range_width(port.width, self._param_env)
            sid = self._register_signal(local_name, w)
            port_sids.append(sid)
            layout = self._struct_layout_for_type(getattr(port, "data_type", None))
            if layout is not None:
                self._struct_signal_types[local_name] = layout

        ret_name = f"{prefix}.{func.name}"
        ret_sid = self._register_signal(ret_name, ret_width)
        for local_var in func.locals:
            local_name = f"{prefix}.{local_var.name}"
            self._register_signal(local_name, _range_width(local_var.width, self._param_env))
            layout = self._struct_layout_for_type(getattr(local_var, "type_name", None))
            if layout is not None:
                self._struct_signal_types[local_name] = layout

        # Initialize return value to 0
        zero_cid = self._add_const(Value(0, width=ret_width))
        program.append(instr(Op.LOAD_CONST, zero_cid))
        program.append(instr(Op.STORE_SIG, ret_sid, ret_width))

        # Compile and store each argument into the corresponding port signal
        for i, port in enumerate(func.ports):
            port_w = _range_width(port.width, self._param_env)
            if i < len(call.arguments):
                # A call's argument-to-port binding is really an implicit
                # assignment (`port = arg_expr;`), so the argument is
                # compiled DIRECTLY at the PORT's own declared width
                # (mirroring every other "compile RHS at target width"
                # call site in this file) -- NOT at the argument's own
                # self-determined width with a RESIZE/SIGN_EXT applied
                # afterward alone (the first version of this fix). Those
                # are NOT equivalent whenever the argument is itself a
                # NESTED context-determined operator (unary `-`, a
                # further `+`/`-`/`*`, a `$signed`/`$unsigned` cast):
                # two's-complement negation, in particular, does not
                # commute with extension, the same "unary `-` must negate
                # at its final width, not self-width-then-extend"
                # principle already established for ordinary context-
                # determined arithmetic elsewhere in this file. Confirmed
                # against Icarus for `fn_neg(-(!a7))` with `fn_neg(input
                # signed [15:0] a)` and `a7` = 0: negating `!a7`=1 at its
                # own 1-bit self-determined width first (mod-2 arithmetic)
                # gives 1, zero-extended to 16 bits = `1` -- wrong; Icarus
                # negates directly at the port's 16-bit context, correctly
                # giving `-1` (0xFFFF).
                #
                # The RESIZE/SIGN_EXT safety net below is STILL needed on
                # top of that -- it exists for a separate reason (see the
                # ORIGINAL Icarus-confirmed repro this same net already
                # fixed, `fn_xor64s(((cond) ? a6 : (-a3)), a6[35])`):
                # whenever the argument expression's own self-determined
                # width exceeds 64 bits, `_compile_expr` emits WIDE
                # bytecode for it regardless of the `width` just passed
                # in (matching this file's "only ever widen, never
                # narrow, at the node's own dispatch" convention),
                # leaving a wide-flagged value on the interpreter stack
                # that `OP_STORE_SIG`'s narrow-destination branch (used
                # whenever the PORT itself is <=64 bits) can't read
                # directly -- an explicit RESIZE/SIGN_EXT forces the
                # value back onto the narrow stack slot BEFORE
                # `STORE_SIG` runs.
                eff_signed = self._expr_signed(call.arguments[i])
                self._compile_expr(call.arguments[i], program, port_w)
                arg_w = self._expr_width(call.arguments[i])
                if arg_w != port_w:
                    program.append(instr(Op.SIGN_EXT if eff_signed else Op.RESIZE, port_w, 0))
            else:
                x_cid = self._add_const(Value.x(1))
                program.append(instr(Op.LOAD_CONST, x_cid))
            program.append(instr(Op.STORE_SIG, port_sids[i], port_w))

        # Remap identifiers: build a mapping from original names to local names
        import copy

        body_copy = copy.deepcopy(func.body)
        local_names = {port.name for port in func.ports}
        local_names.update(local_var.name for local_var in func.locals)
        local_names.add(func.name)
        self._remap_identifiers(body_copy, local_names, prefix)

        # Compile the function body with remapped identifiers -- already
        # running at `depth + 1` (bumped once, up front, before the
        # argument loop too -- see that comment for why).
        self._compile_stmt(body_copy, program)
        self._func_call_depth -= 1

        # Load the return value onto the stack
        program.append(instr(Op.LOAD_SIG, ret_sid, ret_width))

    def _remap_identifiers(self, root, local_names: set[str], prefix: str) -> None:
        """Walk AST and remap local identifiers to prefixed names."""
        for node in root.walk():
            if not isinstance(node, Identifier):
                continue
            if node.name in local_names:
                node.name = f"{prefix}.{node.name}"
            if node.hierarchy and node.hierarchy[0] in local_names:
                node.hierarchy[0] = f"{prefix}.{node.hierarchy[0]}"

    def _compile_task_enable(self, stmt: TaskEnable, program: list[tuple[int, int, int]]) -> bool:
        """Inline a user-defined task call at the call site."""
        import copy

        from veriforge.model.functions import TaskDecl
        from veriforge.model.ports import PortDirection

        task: TaskDecl | None = self._task_map.get(stmt.task_name)
        if task is None:
            return False  # unknown task — silently skip

        depth = self._func_call_depth
        prefix = f"__task_{task.name}_{depth}"

        # Register local signals for all task ports
        port_sids: list[int] = []
        for port in task.ports:
            local_name = f"{prefix}.{port.name}"
            w = _range_width(port.width, self._param_env)
            sid = self._register_signal(local_name, w)
            port_sids.append(sid)

        # Compile arguments: store inputs/inouts, track output bindings
        output_bindings: list[tuple[int, Expression]] = []  # (port_sid, caller_lhs_expr)
        for i, port in enumerate(task.ports):
            w = _range_width(port.width, self._param_env)
            if port.direction == PortDirection.INPUT:
                if i < len(stmt.arguments):
                    self._compile_expr(stmt.arguments[i], program)
                else:
                    x_cid = self._add_const(Value.x(w))
                    program.append(instr(Op.LOAD_CONST, x_cid))
                program.append(instr(Op.STORE_SIG, port_sids[i], w))
            elif port.direction == PortDirection.INOUT:
                if i < len(stmt.arguments):
                    self._compile_expr(stmt.arguments[i], program)
                    program.append(instr(Op.STORE_SIG, port_sids[i], w))
                    output_bindings.append((port_sids[i], stmt.arguments[i]))
                else:
                    zero_cid = self._add_const(Value(0, width=w))
                    program.append(instr(Op.LOAD_CONST, zero_cid))
                    program.append(instr(Op.STORE_SIG, port_sids[i], w))
            elif port.direction == PortDirection.OUTPUT:
                # Initialize output to 0
                zero_cid = self._add_const(Value(0, width=w))
                program.append(instr(Op.LOAD_CONST, zero_cid))
                program.append(instr(Op.STORE_SIG, port_sids[i], w))
                if i < len(stmt.arguments):
                    output_bindings.append((port_sids[i], stmt.arguments[i]))

        # Deep-copy and remap the task body
        body_copy = copy.deepcopy(task.body)
        local_names = {port.name for port in task.ports}
        self._remap_identifiers(body_copy, local_names, prefix)

        # Compile the task body
        self._func_call_depth += 1
        has_timing = False
        if body_copy:
            has_timing = self._compile_stmt(body_copy, program)
        self._func_call_depth -= 1

        # Copy output/inout values back to caller
        for port_sid, lhs_expr in output_bindings:
            w = self.sig_width[port_sid]
            program.append(instr(Op.LOAD_SIG, port_sid, w))
            self._compile_store_lhs(lhs_expr, program, immediate=True)

        return has_timing

    # ── Statement compilation ────────────────────────────────────

    def _compile_stmt(self, stmt: Statement, program: list[tuple[int, int, int]]) -> bool:  # noqa: PLR0912, PLR0911, PLR0915
        """Compile a statement to bytecode.

        Returns True if the statement contains timing controls (delay/event)
        that would require suspension.
        """
        if stmt is None:
            return False

        stype = type(stmt)
        has_timing = False

        # -- Blocking assignment --
        if stype is BlockingAssign:
            if self._compile_whole_memory_copy(stmt.lhs, stmt.rhs, program, immediate=True):
                return False
            if isinstance(stmt.lhs, Concatenation) and self._compile_matching_concat_copy(
                stmt.lhs, stmt.rhs, program, immediate=True
            ):
                return False
            lhs_w = self._expr_width(stmt.lhs)
            if isinstance(stmt.lhs, Concatenation):
                lhs_w = sum(self._concat_eval_widths(stmt.lhs.parts, self._expr_width(stmt.rhs)))
            self._compile_expr(stmt.rhs, program, width=lhs_w)
            self._emit_sign_ext_if_needed(stmt.rhs, stmt.lhs, program)
            self._compile_store_lhs(stmt.lhs, program, immediate=True, total_width=lhs_w)
            return False

        # -- Non-blocking assignment --
        if stype is NonblockingAssign:
            if self._compile_whole_memory_copy(stmt.lhs, stmt.rhs, program, immediate=False):
                return False
            if isinstance(stmt.lhs, Concatenation) and self._compile_matching_concat_copy(
                stmt.lhs, stmt.rhs, program, immediate=False
            ):
                return False
            lhs_w = self._expr_width(stmt.lhs)
            if isinstance(stmt.lhs, Concatenation):
                lhs_w = sum(self._concat_eval_widths(stmt.lhs.parts, self._expr_width(stmt.rhs)))
            self._compile_expr(stmt.rhs, program, width=lhs_w)
            self._emit_sign_ext_if_needed(stmt.rhs, stmt.lhs, program)
            self._compile_store_lhs(stmt.lhs, program, immediate=False, total_width=lhs_w)
            return False

        # -- Sequential block --
        if stype is SeqBlock:
            for s in stmt.statements:
                if self._compile_stmt(s, program):
                    has_timing = True
            return has_timing

        # -- Parallel block (compiled as sequential — fork/join not supported) --
        if stype is ParBlock:
            warnings.warn(
                "VM bytecode compiler: fork/join (ParBlock) compiled as sequential — "
                "true parallel semantics not yet implemented",
                stacklevel=2,
            )
            for s in stmt.statements:
                if self._compile_stmt(s, program):
                    has_timing = True
            return has_timing

        # -- If statement --
        if stype is IfStatement:
            # Self-determined width, not the default 0 -- see the
            # identical note on the TernaryOp condition above; without
            # this, a nested context-determined operator within the
            # condition (e.g. `~` wrapping a ternary whose branches have
            # different widths) never gets resized before it runs.
            # Mirrors the identical fix in `sim/executor.py`'s IfStatement/
            # ForLoop/WhileLoop condition checks; confirmed wrong against
            # Icarus for `if ((~((~a4[29:10]) ? (!(~&a0)) : {3{(~&a2)}})))`.
            self._compile_expr(stmt.condition, program, self._expr_width(stmt.condition))
            jz_idx = len(program)
            program.append(instr(Op.JUMP_IF_ZERO, 0))  # placeholder

            if stmt.then_body:
                if self._compile_stmt(stmt.then_body, program):
                    has_timing = True

            if stmt.else_body:
                jmp_idx = len(program)
                program.append(instr(Op.JUMP, 0))  # placeholder
                else_addr = len(program)
                program[jz_idx] = instr(Op.JUMP_IF_ZERO, else_addr)
                if self._compile_stmt(stmt.else_body, program):
                    has_timing = True
                end_addr = len(program)
                program[jmp_idx] = instr(Op.JUMP, end_addr)
            else:
                end_addr = len(program)
                program[jz_idx] = instr(Op.JUMP_IF_ZERO, end_addr)

            return has_timing

        # -- Case statement --
        if stype is CaseStatement:
            return self._compile_case(stmt, program)

        # -- For loop --
        if stype is ForLoop:
            if type(stmt.init) is BlockingAssign and type(stmt.init.lhs) is Identifier:
                loop_var_name = self._resolve_id_name(stmt.init.lhs)
                if loop_var_name not in self.signal_map:
                    loop_var_width = self._expr_width(stmt.init.rhs)
                    if stmt.signed_var:
                        loop_var_width = max(loop_var_width, 32)
                    self._register_signal(loop_var_name, max(loop_var_width, 1))
            self._compile_stmt(stmt.init, program)
            loop_top = len(program)
            negative_jump_idx = None
            if stmt.signed_var and type(stmt.init) is BlockingAssign and type(stmt.init.lhs) is Identifier:
                loop_width = self._expr_width(stmt.init.lhs)
                if loop_width > 1:
                    self._compile_expr(BitSelect(stmt.init.lhs, Literal(loop_width - 1)), program)
                    negative_jump_idx = len(program)
                    program.append(instr(Op.JUMP_IF_NONZERO, 0))  # placeholder
            # Self-determined width, not the default 0 -- see the
            # identical note on the TernaryOp condition above; without
            # this, a nested context-determined operator within the
            # condition (e.g. `~` wrapping a ternary whose branches have
            # different widths) never gets resized before it runs.
            # Mirrors the identical fix in `sim/executor.py`'s IfStatement/
            # ForLoop/WhileLoop condition checks; confirmed wrong against
            # Icarus for `if ((~((~a4[29:10]) ? (!(~&a0)) : {3{(~&a2)}})))`.
            self._compile_expr(stmt.condition, program, self._expr_width(stmt.condition))
            jz_idx = len(program)
            program.append(instr(Op.JUMP_IF_ZERO, 0))  # placeholder
            if stmt.body:
                if self._compile_stmt(stmt.body, program):
                    has_timing = True
            self._compile_stmt(stmt.update, program)
            program.append(instr(Op.JUMP, loop_top))
            end_addr = len(program)
            if negative_jump_idx is not None:
                program[negative_jump_idx] = instr(Op.JUMP_IF_NONZERO, end_addr)
            program[jz_idx] = instr(Op.JUMP_IF_ZERO, end_addr)
            return has_timing

        # -- While loop --
        if stype is WhileLoop:
            loop_top = len(program)
            # Self-determined width, not the default 0 -- see the
            # identical note on the TernaryOp condition above; without
            # this, a nested context-determined operator within the
            # condition (e.g. `~` wrapping a ternary whose branches have
            # different widths) never gets resized before it runs.
            # Mirrors the identical fix in `sim/executor.py`'s IfStatement/
            # ForLoop/WhileLoop condition checks; confirmed wrong against
            # Icarus for `if ((~((~a4[29:10]) ? (!(~&a0)) : {3{(~&a2)}})))`.
            self._compile_expr(stmt.condition, program, self._expr_width(stmt.condition))
            jz_idx = len(program)
            program.append(instr(Op.JUMP_IF_ZERO, 0))  # placeholder
            if stmt.body:
                if self._compile_stmt(stmt.body, program):
                    has_timing = True
            program.append(instr(Op.JUMP, loop_top))
            end_addr = len(program)
            program[jz_idx] = instr(Op.JUMP_IF_ZERO, end_addr)
            return has_timing

        # -- Forever loop --
        if stype is ForeverLoop:
            loop_top = len(program)
            if stmt.body:
                if self._compile_stmt(stmt.body, program):
                    has_timing = True
            program.append(instr(Op.JUMP, loop_top))
            return has_timing

        # -- Repeat loop --
        if stype is RepeatLoop:
            # Compile: push count, then loop: DUP, JZ end, body, LOAD 1, SUB, JUMP top; end: POP
            self._compile_expr(stmt.count, program)
            loop_top = len(program)
            program.append(instr(Op.DUP))
            jz_idx = len(program)
            program.append(instr(Op.JUMP_IF_ZERO, 0))  # placeholder
            if stmt.body:
                if self._compile_stmt(stmt.body, program):
                    has_timing = True
            one_cid = self._add_int_const(1, 32)
            program.append(instr(Op.LOAD_CONST, one_cid))
            program.append(instr(Op.SUB))
            program.append(instr(Op.JUMP, loop_top))
            end_addr = len(program)
            program[jz_idx] = instr(Op.JUMP_IF_ZERO, end_addr)
            program.append(instr(Op.POP))  # pop counter when done
            return has_timing

        # -- Delay control (#N) --
        if stype is DelayControl:
            # Timing controls cannot be compiled to simple bytecode - they
            # require process suspension. Mark as having timing.
            return True

        # -- Event control (@(...)) --
        if stype is EventControl:
            return True

        # -- Wait statement --
        if stype is WaitStatement:
            return True

        # -- Disable statement --
        if stype is DisableStatement:
            raise NotImplementedError("'disable' statement is not supported in VM bytecode compilation")

        # -- Event trigger (-> event) --
        if stype is EventTrigger:
            # Toggle the event signal
            sid = self._get_signal_id(stmt.event)
            # Load current value, invert, store
            program.append(instr(Op.LOAD_SIG, sid))
            program.append(instr(Op.LOG_NOT))
            program.append(instr(Op.STORE_SIG, sid))
            return False

        # -- System task call --
        if stype is SystemTaskCall:
            return self._compile_system_task(stmt, program)

        # -- Task enable --
        if stype is TaskEnable:
            return self._compile_task_enable(stmt, program)

        raise TypeError(f"Cannot compile statement type: {type(stmt).__name__}")

    def _compile_case(self, stmt: CaseStatement, program: list[tuple[int, int, int]]) -> bool:  # noqa: PLR0912
        """Compile a case/casex/casez statement."""
        has_timing = False

        # Push selector onto stack
        self._compile_expr(stmt.expression, program)

        # Collect jump targets for patching
        body_jumps: list[tuple[int, int]] = []  # (item_index, jump_addr_in_program)
        end_jumps: list[int] = []

        default_idx: int | None = None

        # Phase 1: generate comparison + jump-to-body for each item
        for i, item in enumerate(stmt.items):
            if item.is_default:
                default_idx = i
                continue
            for val_expr in item.values:
                program.append(instr(Op.DUP))  # dup selector for comparison
                self._compile_expr(val_expr, program)
                if stmt.case_type == "case":
                    program.append(instr(Op.CMP_EQ))
                elif stmt.case_type == "casex":
                    program.append(instr(Op.CMP_CASEX))
                elif stmt.case_type == "casez":
                    program.append(instr(Op.CMP_CASEZ))
                else:
                    program.append(instr(Op.CMP_EQ))
                jnz_idx = len(program)
                program.append(instr(Op.JUMP_IF_NONZERO, 0))  # placeholder
                body_jumps.append((i, jnz_idx))

        # If no match and there's a default, jump to it; else jump to end
        if default_idx is not None:
            default_jmp_idx = len(program)
            program.append(instr(Op.JUMP, 0))  # placeholder → default body
        else:
            # No default: pop selector and jump to end
            program.append(instr(Op.POP))
            end_jmp_idx = len(program)
            program.append(instr(Op.JUMP, 0))  # placeholder → end
            end_jumps.append(end_jmp_idx)

        # Phase 2: generate bodies for each item
        body_addrs: dict[int, int] = {}  # item_index → body address
        for i, item in enumerate(stmt.items):
            body_addrs[i] = len(program)
            program.append(instr(Op.POP))  # pop selector
            if item.body:
                if self._compile_stmt(item.body, program):
                    has_timing = True
            jmp_idx = len(program)
            program.append(instr(Op.JUMP, 0))  # placeholder → end
            end_jumps.append(jmp_idx)

        end_addr = len(program)

        # Phase 3: patch all jump targets
        for item_idx, jnz_addr in body_jumps:
            program[jnz_addr] = instr(Op.JUMP_IF_NONZERO, body_addrs[item_idx])

        if default_idx is not None:
            program[default_jmp_idx] = instr(Op.JUMP, body_addrs[default_idx])

        for jmp_addr in end_jumps:
            program[jmp_addr] = instr(Op.JUMP, end_addr)

        return has_timing

    def _compile_system_task(self, task: SystemTaskCall, program: list[tuple[int, int, int]]) -> bool:  # noqa: PLR0912, PLR0915
        """Compile a system task call."""
        name = task.task_name.lower()

        if name in ("$display", "$write", "$monitor", "$error", "$warning", "$info"):
            # Check if first argument is a format string
            fmt_id = 0  # 0 = no format string
            value_args = list(task.arguments)
            if value_args and isinstance(value_args[0], StringLiteral):
                fmt_str = value_args[0].value
                fmt_id = len(self.display_formats) + 1  # 1-indexed
                self.display_formats.append(fmt_str)
                value_args = value_args[1:]  # remaining are data args

            for arg in value_args:
                self._compile_expr(arg, program)

            if name == "$monitor":
                monitor_id = len(self.monitor_programs)
                program.append(instr(Op.SYS_MONITOR, len(value_args) | (fmt_id << 16), monitor_id))
                # Build a standalone monitor program for re-fire at end of timestep.
                # This is the same sequence of load + SYS_DISPLAY instructions.
                mon_prog: list[tuple[int, int, int]] = []
                mon_sigs: set[int] = set()
                for arg in value_args:
                    self._compile_expr(arg, mon_prog)
                    self._walk_expr_signals(arg, mon_sigs)
                # Use SYS_DISPLAY since we just want to produce output
                mon_prog.append(instr(Op.SYS_DISPLAY, len(value_args) | (fmt_id << 16), fmt_id))
                mon_prog.append(instr(Op.PROC_END))
                self.monitor_programs.append((mon_prog, mon_sigs))
            else:
                program.append(instr(Op.SYS_DISPLAY, len(value_args) | (fmt_id << 16), fmt_id))
            return False

        if name in ("$finish", "$stop"):
            program.append(instr(Op.SYS_FINISH))
            return False

        if name in ("$readmemh", "$readmemb"):
            if len(task.arguments) >= 2:
                filename_expr = task.arguments[0]
                mem_expr = task.arguments[1]
                if isinstance(filename_expr, StringLiteral) and isinstance(mem_expr, Identifier):
                    if self._is_memory(self._resolve_id_name(mem_expr)):
                        mid = self.mem_map[self._resolve_id_name(mem_expr)]
                        is_hex = name == "$readmemh"
                        task_id = len(self.readmem_tasks)
                        self.readmem_tasks.append((filename_expr.value, mid, is_hex))
                        program.append(instr(Op.SYS_READMEM, task_id))
            return False

        if name == "$fclose":
            # $fclose(fd) — compile fd expression, then SYS_FCLOSE
            if task.arguments:
                self._compile_expr(task.arguments[0], program)
                program.append(instr(Op.SYS_FCLOSE))
            return False

        if name in ("$fdisplay", "$fwrite"):
            # $fdisplay(fd, [fmt,] args...) / $fwrite(fd, [fmt,] args...)
            # First arg is file descriptor, rest like $display/$write
            if task.arguments:
                # Compile fd expression (kept on stack)
                self._compile_expr(task.arguments[0], program)
                remaining = list(task.arguments[1:])
                fmt_id = 0
                if remaining and isinstance(remaining[0], StringLiteral):
                    fmt_str = remaining[0].value
                    fmt_id = len(self.display_formats) + 1
                    self.display_formats.append(fmt_str)
                    remaining = remaining[1:]
                for arg in remaining:
                    self._compile_expr(arg, program)
                op = Op.SYS_FDISPLAY if name == "$fdisplay" else Op.SYS_FWRITE
                program.append(instr(op, len(remaining) | (fmt_id << 16)))
            return False

        if name == "$fflush":
            return False

        if name in ("$dumpfile", "$dumpvars"):
            # Mark as has_timing so the initial block routes to the reference
            # executor, which handles VCD creation natively.
            return True

        raise NotImplementedError(f"VM bytecode compiler: unknown system task '{name}' not yet supported")

    # ── Expression width inference ───────────────────────────────

    def _concat_part_width_info(self, part: Expression) -> int | None:
        if isinstance(part, RangeSelect):
            if isinstance(part.msb, Literal) and isinstance(part.lsb, Literal):
                return int(part.msb.value) - int(part.lsb.value) + 1
            msb_val = _const_int(part.msb, self._param_env)
            lsb_val = _const_int(part.lsb, self._param_env)
            if msb_val is not None and lsb_val is not None:
                return msb_val - lsb_val + 1
            return None
        if isinstance(part, PartSelect):
            if isinstance(part.width, Literal):
                return int(part.width.value)
            w_val = _const_int(part.width, self._param_env)
            if w_val is not None:
                return w_val
            return None
        return self._expr_width(part)

    def _concat_eval_widths(self, parts: list[Expression], total_width: int | None = None) -> list[int]:
        widths = [self._concat_part_width_info(part) for part in parts]
        unknown_indices = [index for index, width in enumerate(widths) if width is None]
        if total_width is not None and len(unknown_indices) == 1:
            known_total = sum(width for width in widths if width is not None)
            inferred_width = total_width - known_total
            if inferred_width >= 0:
                widths[unknown_indices[0]] = inferred_width
        return [width if width is not None else self._expr_width(parts[index]) for index, width in enumerate(widths)]

    def _expr_width(self, expr: Expression) -> int:  # noqa: PLR0911, PLR0912
        """Compute the compile-time bit-width of an expression.

        Used for concat LHS decomposition — must return the right width
        for each part so the RHS can be sliced correctly.
        """
        etype = type(expr)

        if etype is Identifier:
            name = self._resolve_id_name(expr)
            sid = self.signal_map.get(name)
            if sid is not None:
                return self.sig_width[sid]
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                return struct_info[4]
            if self._is_memory(name):
                # Whole-array reference: the flat width is depth*elem_width,
                # not a fabricated 1-bit phantom signal's width (which used
                # to truncate the RHS to 1 bit before it ever reached the
                # whole-array LHS/RHS handling elsewhere in this file).
                mid = self.mem_map[name]
                elem_w, depth, _base = self.mem_info[mid]
                return elem_w * depth
            sid = self._get_signal_id(name)
            return self.sig_width[sid]

        if etype is Literal:
            return expr.width or 32

        if etype is BitSelect:
            if type(expr.target) is Identifier and self._is_memory(self._resolve_id_name(expr.target)):
                mid = self.mem_map[self._resolve_id_name(expr.target)]
                return self.mem_info[mid][0]  # element width
            return 1

        if etype is RangeSelect:
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                return int(expr.msb.value) - int(expr.lsb.value) + 1
            msb_val = _const_int(expr.msb, self._param_env)
            lsb_val = _const_int(expr.lsb, self._param_env)
            if msb_val is not None and lsb_val is not None:
                return msb_val - lsb_val + 1
            # Dynamic (runtime-only) msb/lsb: the true extracted width
            # isn't known at compile time. Falling back to a bare `1` here
            # used to feed a truncating RESIZE after the (correctly
            # runtime-sized) RANGE_SELECT opcode, silently destroying the
            # real value. A range-select can never be wider than the thing
            # it's slicing, so that width is a safe *upper* bound: at worst
            # this makes the post-hoc resize a widen (or no-op), never a
            # truncation of an already-correct value.
            return self._expr_width(expr.target)

        if etype is PartSelect:
            if isinstance(expr.width, Literal):
                return int(expr.width.value)
            w_val = _const_int(expr.width, self._param_env)
            if w_val is not None:
                return w_val
            # Same reasoning as the RangeSelect fallback above.
            return self._expr_width(expr.target)

        if etype is Concatenation:
            return sum(self._expr_width(p) for p in expr.parts)

        if etype is StreamingConcatenation:
            # Chunk reversal never changes total bit width.
            return sum(self._expr_width(p) for p in expr.parts)

        if etype is Replication:
            if isinstance(expr.count, Literal):
                return int(expr.count.value) * self._expr_width(expr.value)
            resolved = _const_int(expr.count, self._param_env)
            if resolved is not None:
                return resolved * self._expr_width(expr.value)
            return self._expr_width(expr.value)

        if etype is AssignmentPattern:
            from ..elaborate import match_assignment_pattern_layout  # noqa: PLC0415

            if expr.named_pairs:
                layout = match_assignment_pattern_layout(expr, self._struct_type_map)
                if layout is not None:
                    return layout.total_width
            if expr.positional:
                return sum(self._expr_width(part) for part in expr.positional)
            if expr.default_value is not None:
                return self._expr_width(expr.default_value)
            return 1

        if etype is BinaryOp:
            if expr.op in (
                "==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||",
            ):  # fmt: skip
                return 1
            if expr.op in ("<<", "<<<"):
                # Left-shift's self-determined width needs left_width +
                # shift amount, not just max() -- otherwise `hi << 32`
                # (hi 31 bits) gets underestimated at 32 bits instead of
                # 63, silently truncating away the shifted-in bits when
                # this width is used as a bitwise operand's evaluation
                # width (see the "&"/"|"/"^" branch in `_compile_expr`).
                lw = self._expr_width(expr.left)
                if isinstance(expr.right, Literal) and not (expr.right.is_x or expr.right.is_z):
                    return lw + int(expr.right.value)
                return max(lw, self._expr_width(expr.right))
            if expr.op in (">>", ">>>", "**"):
                # A shift's self-determined width is its LEFT operand's
                # width only -- the shift amount never contributes bits.
                # `**` (power) shares this SAME row in IEEE 1364-2005
                # Table 5-22 (`>> << ** >>> <<<` -> `L(i)`, exponent
                # always self-determined) -- verified directly against
                # the primary spec text; mirrors the identical fix in
                # `sim/evaluator.py`'s `_expr_self_width`.
                return self._expr_width(expr.left)
            return max(self._expr_width(expr.left), self._expr_width(expr.right))

        if etype is UnaryOp:
            if expr.op in ("&", "|", "^", "~&", "~|", "~^", "^~", "!"):
                return 1
            return self._expr_width(expr.operand)

        if etype is TernaryOp:
            return max(self._expr_width(expr.true_expr), self._expr_width(expr.false_expr))

        if etype is StringLiteral:
            return len(expr.value) * 8

        if etype is FunctionCall:
            name = expr.name.lower()
            if name in ("$signed", "$unsigned") and expr.arguments:
                return self._expr_width(expr.arguments[0])
            # A user-defined function call's self-determined width is its
            # OWN declared return width, not the generic fallback below --
            # mirrors `_compile_user_function`'s own return-width
            # computation and the identical fix in `sim/evaluator.py`'s
            # `_expr_self_width` (see its docstring for the concrete
            # Icarus-confirmed repro, `($unsigned(fn_sel1(a4[27:8], a1)) ^
            # (~^(-a4)))` with `fn_sel1` declared `function [62:0]
            # fn_sel1(...)`): the old hardcoded `32` here silently
            # truncated the XOR's own `target` width computation,
            # corrupting the zero-extension of the call's real 63-bit
            # return value into the 64-bit result.
            func = self._function_map.get(expr.name)
            if func is not None and func.return_range is not None:
                msb, lsb = func.return_range.msb, func.return_range.lsb
                if isinstance(msb, Literal) and isinstance(lsb, Literal):
                    return abs(int(msb.value) - int(lsb.value)) + 1

        return 32  # fallback

    def _expr_signed(self, expr: Expression, cache: dict[int, bool] | None = None) -> bool:
        """Return True if *expr* is fully signed per IEEE 1364-2005 §5.5.

        Uses ``self.sig_signed`` for signal signedness lookups.
        When *cache* is provided, intermediate results are memoised.
        """
        if cache is not None:
            key = id(expr)
            cached = cache.get(key)
            if cached is not None:
                return cached

        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self.signal_map.get(name)
            result = sid is not None and sid < len(self.sig_signed) and self.sig_signed[sid]

        elif etype is Literal:
            result = expr.signed

        elif etype in (BitSelect, RangeSelect, PartSelect):
            # Always unsigned (§5.5.1), regardless of the target's declared
            # signedness.
            result = False

        elif etype is UnaryOp:
            # `!` and all reduction ops always produce an unsigned 1-bit
            # result regardless of the operand's own signedness (IEEE
            # 1364-2005 §5.5.1) -- only the context-determined
            # pass-through ops (~, +, -) inherit the operand's signedness.
            if expr.op in ("!", "&", "|", "^", "~&", "~|", "~^", "^~"):
                result = False
            else:
                result = self._expr_signed(expr.operand, cache)

        elif etype is BinaryOp:
            # Comparisons and logical ops always produce an unsigned 1-bit
            # result regardless of operand signedness (IEEE 1364-2005
            # §5.5.1, Table 5-22).
            if expr.op in ("<<", ">>", "<<<", ">>>"):
                result = self._expr_signed(expr.left, cache)
            elif expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"):
                result = False
            else:
                result = self._expr_signed(expr.left, cache) and self._expr_signed(expr.right, cache)

        elif etype is TernaryOp:
            result = self._expr_signed(expr.true_expr, cache) and self._expr_signed(expr.false_expr, cache)

        elif etype in (Concatenation, Replication, StreamingConcatenation):
            result = False

        elif etype is FunctionCall:
            name = expr.name.lower()
            if name == "$signed":
                result = True
            elif name == "$unsigned":
                result = False
            else:
                # A user function call's own signedness is its declared
                # return type's signedness, NOT unconditionally False --
                # mirrors the identical fix in `sim/evaluator.py`'s
                # `_expr_signed` (see its docstring for the concrete
                # Icarus-confirmed repro).
                func = self._function_map.get(expr.name)
                result = func is not None and func.signed

        else:
            result = False

        if cache is not None:
            cache[key] = result
        return result

    # ── Sign extension for $signed() RHS ───────────────────────

    def _emit_sign_ext_if_needed(
        self,
        rhs: Expression,
        lhs: Expression,
        program: list[tuple[int, int, int]],
    ) -> None:
        """Emit SIGN_EXT if RHS expression is signed and LHS is wider.

        Whether to emit is decided purely from `_expr_signed(rhs)`, not from
        comparing static `_expr_width` estimates: when `rhs` contains a
        ternary, `_expr_width` returns max(true_width, false_width), which
        can overestimate the ACTUAL runtime width (whichever branch is
        picked may be narrower). Gating on that static estimate can wrongly
        conclude "already wide enough" and skip sign-extension when the
        real, narrower runtime value still needs it. Op.SIGN_EXT's own
        runtime check (comparing its target width against the value's
        actual current width) makes it a safe no-op to always emit here.
        """
        if self._expr_signed(rhs):
            lhs_w = self._expr_width(lhs)
            program.append((Op.SIGN_EXT, lhs_w, 0))

    # ── LHS compilation ──────────────────────────────────────────

    def _compile_store_lhs(  # noqa: PLR0912
        self,
        lhs: Expression,
        program: list[tuple[int, int, int]],
        *,
        immediate: bool,
        total_width: int | None = None,
    ) -> None:
        """Compile an assignment target (LHS).

        Expects the RHS value to already be on the stack.
        """
        ltype = type(lhs)

        if ltype is Identifier:
            name = self._resolve_id_name(lhs)
            sid = self.signal_map.get(name)
            if sid is not None:
                # Direct signal store
                target_width = self.sig_width[sid]
                program.append(instr(Op.RESIZE, target_width))
                if immediate:
                    program.append(instr(Op.STORE_SIG, sid))
                else:
                    program.append(instr(Op.NBA_SIG, sid))
                return
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                storage_kind, storage_id, storage_index, offset, field_width = struct_info
                program.append(instr(Op.RESIZE, field_width))
                msb_cid = self._add_const(Value(offset + field_width - 1, width=32))
                lsb_cid = self._add_const(Value(offset, width=32))
                if storage_kind == "memory":
                    marker_sid = self.mem_marker_sigs[storage_id]
                    encoded_arg = storage_id | (marker_sid << 16)
                    if not self._compile_struct_storage_index(storage_index, program):
                        raise ValueError(f"Unsupported dynamic memory struct index: {name!r}")
                    program.append(instr(Op.LOAD_CONST, msb_cid))
                    program.append(instr(Op.LOAD_CONST, lsb_cid))
                    if immediate:
                        program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                    else:
                        program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                    return
                program.append(instr(Op.LOAD_CONST, msb_cid))
                program.append(instr(Op.LOAD_CONST, lsb_cid))
                if immediate:
                    program.append(instr(Op.STORE_RANGE, storage_id))
                else:
                    program.append(instr(Op.NBA_RANGE, storage_id))
                return
            if self._is_memory(name):
                # Whole-array LHS (2-D packed array assigned as a single
                # flat vector, e.g. `assign dout = din;`): the depth is
                # known at compile time, so treat this exactly like a
                # `{mem[depth-1], ..., mem[0]} = rhs` Concatenation LHS
                # (see that branch below) -- DUP + RANGE_SELECT the
                # matching slice for each element, in from-MSB order,
                # then let the existing BitSelect-targeting-a-memory LHS
                # path (below) do the actual STORE_MEM/NBA_MEM.
                mid = self.mem_map[name]
                elem_w, depth, _base = self.mem_info[mid]
                program.append(instr(Op.RESIZE, depth * elem_w))
                offset = 0
                for idx in range(depth):
                    program.append(instr(Op.DUP))
                    msb_cid = self._add_int_const(offset + elem_w - 1, 32)
                    lsb_cid = self._add_int_const(offset, 32)
                    program.append(instr(Op.LOAD_CONST, msb_cid))
                    program.append(instr(Op.LOAD_CONST, lsb_cid))
                    program.append(instr(Op.RANGE_SELECT))
                    part = BitSelect(target=Identifier(name), index=Literal(idx))
                    self._compile_store_lhs(part, program, immediate=immediate, total_width=elem_w)
                    offset += elem_w
                program.append(instr(Op.POP))
                return
            sid = self._get_signal_id(name)
            target_width = self.sig_width[sid]
            program.append(instr(Op.RESIZE, target_width))
            if immediate:
                program.append(instr(Op.STORE_SIG, sid))
            else:
                program.append(instr(Op.NBA_SIG, sid))
            return

        if ltype is BitSelect:
            if type(lhs.target) is Identifier:
                tname = self._resolve_id_name(lhs.target)
                struct_info = self._resolve_struct_storage_access(tname)
                if struct_info is not None:
                    storage_kind, storage_id, storage_index, offset, _field_width = struct_info
                    select_base = self._signal_bases.get(tname, 0)
                    if storage_kind == "memory":
                        marker_sid = self.mem_marker_sigs[storage_id]
                        encoded_arg = storage_id | (marker_sid << 16)
                        if not self._compile_struct_storage_index(storage_index, program):
                            raise ValueError(f"Unsupported dynamic memory struct index: {tname!r}")
                    self._compile_expr(lhs.index, program)
                    if select_base != 0:
                        cid = self._add_int_const(select_base, 32)
                        program.append(instr(Op.LOAD_CONST, cid))
                        program.append(instr(Op.SUB))
                    if offset != 0:
                        cid = self._add_int_const(offset, 32)
                        program.append(instr(Op.LOAD_CONST, cid))
                        program.append(instr(Op.ADD))
                    program.append(instr(Op.DUP))
                    if storage_kind == "memory":
                        if immediate:
                            program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                        else:
                            program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                        return
                    if immediate:
                        program.append(instr(Op.STORE_RANGE, storage_id))
                    else:
                        program.append(instr(Op.NBA_RANGE, storage_id))
                    return
                if self._is_memory(tname):
                    mid = self.mem_map[tname]
                    marker_sid = self.mem_marker_sigs[mid]
                    encoded_arg = mid | (marker_sid << 16)
                    self._compile_expr(lhs.index, program)
                    if immediate:
                        program.append(instr(Op.STORE_MEM, encoded_arg))
                    else:
                        program.append(instr(Op.NBA_MEM, encoded_arg))
                    return
                sid = self._get_signal_id(tname)
                self._compile_expr(lhs.index, program)
                # Adjust for non-zero base offset
                base = self._signal_bases.get(tname, 0)
                if base != 0:
                    cid = self._add_int_const(base, 32)
                    program.append(instr(Op.LOAD_CONST, cid))
                    program.append(instr(Op.SUB))
                if immediate:
                    program.append(instr(Op.STORE_BIT, sid))
                else:
                    program.append(instr(Op.NBA_BIT, sid))
                return
            if type(lhs.target) is BitSelect and type(lhs.target.target) is Identifier:
                tname = self._resolve_id_name(lhs.target.target)
                if self._is_memory(tname):
                    mid = self.mem_map[tname]
                    marker_sid = self.mem_marker_sigs[mid]
                    encoded_arg = mid | (marker_sid << 16)
                    base = self._memory_bases.get(tname, 0)
                    self._compile_expr(lhs.target.index, program)
                    self._compile_expr(lhs.index, program)
                    self._append_base_adjust(program, base)
                    self._compile_expr(lhs.index, program)
                    self._append_base_adjust(program, base)
                    if immediate:
                        program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                    else:
                        program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                    return

        if ltype is RangeSelect:
            if type(lhs.target) is BitSelect and type(lhs.target.target) is Identifier:
                # Memory element partial write: memory[addr][msb:lsb] <= value
                tname = self._resolve_id_name(lhs.target.target)
                if self._is_memory(tname):
                    mid = self.mem_map[tname]
                    marker_sid = self.mem_marker_sigs[mid]
                    encoded_arg = mid | (marker_sid << 16)
                    base = self._memory_bases.get(tname, 0)
                    self._compile_expr(lhs.target.index, program)
                    self._compile_expr(lhs.msb, program)
                    self._append_base_adjust(program, base)
                    self._compile_expr(lhs.lsb, program)
                    self._append_base_adjust(program, base)
                    if immediate:
                        program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                    else:
                        program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                    return
            if type(lhs.target) is Identifier:
                tname = self._resolve_id_name(lhs.target)
                struct_info = self._resolve_struct_storage_access(tname)
                if struct_info is not None:
                    storage_kind, storage_id, storage_index, offset, _field_width = struct_info
                    select_base = self._signal_bases.get(tname, 0)
                    if storage_kind == "memory":
                        marker_sid = self.mem_marker_sigs[storage_id]
                        encoded_arg = storage_id | (marker_sid << 16)
                        if not self._compile_struct_storage_index(storage_index, program):
                            raise ValueError(f"Unsupported dynamic memory struct index: {tname!r}")
                    self._compile_expr(lhs.msb, program)
                    if select_base != 0:
                        cid = self._add_int_const(select_base, 32)
                        program.append(instr(Op.LOAD_CONST, cid))
                        program.append(instr(Op.SUB))
                    if offset != 0:
                        cid = self._add_int_const(offset, 32)
                        program.append(instr(Op.LOAD_CONST, cid))
                        program.append(instr(Op.ADD))
                    self._compile_expr(lhs.lsb, program)
                    if select_base != 0:
                        cid = self._add_int_const(select_base, 32)
                        program.append(instr(Op.LOAD_CONST, cid))
                        program.append(instr(Op.SUB))
                    if offset != 0:
                        cid = self._add_int_const(offset, 32)
                        program.append(instr(Op.LOAD_CONST, cid))
                        program.append(instr(Op.ADD))
                    if storage_kind == "memory":
                        if immediate:
                            program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                        else:
                            program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                        return
                    if immediate:
                        program.append(instr(Op.STORE_RANGE, storage_id))
                    else:
                        program.append(instr(Op.NBA_RANGE, storage_id))
                    return
                sid = self._get_signal_id(tname)
                base = self._signal_bases.get(tname, 0)
                self._compile_expr(lhs.msb, program)
                if base != 0:
                    cid = self._add_int_const(base, 32)
                    program.append(instr(Op.LOAD_CONST, cid))
                    program.append(instr(Op.SUB))
                self._compile_expr(lhs.lsb, program)
                if base != 0:
                    cid = self._add_int_const(base, 32)
                    program.append(instr(Op.LOAD_CONST, cid))
                    program.append(instr(Op.SUB))
                if immediate:
                    program.append(instr(Op.STORE_RANGE, sid))
                else:
                    program.append(instr(Op.NBA_RANGE, sid))
                return

        if ltype is PartSelect:
            if type(lhs.target) is BitSelect and type(lhs.target.target) is Identifier:
                tname = self._resolve_id_name(lhs.target.target)
                if self._is_memory(tname):
                    mid = self.mem_map[tname]
                    marker_sid = self.mem_marker_sigs[mid]
                    encoded_arg = mid | (marker_sid << 16)
                    base = self._memory_bases.get(tname, 0)
                    self._compile_expr(lhs.target.index, program)
                    if lhs.direction == "+:":
                        self._compile_expr(lhs.base, program)
                        self._append_base_adjust(program, base)
                        self._compile_expr(lhs.width, program)
                        program.append(instr(Op.ADD))
                        one_cid = self._add_int_const(1, 32)
                        program.append(instr(Op.LOAD_CONST, one_cid))
                        program.append(instr(Op.SUB))
                        self._compile_expr(lhs.base, program)
                        self._append_base_adjust(program, base)
                    else:
                        self._compile_expr(lhs.base, program)
                        self._append_base_adjust(program, base)
                        self._compile_expr(lhs.base, program)
                        self._append_base_adjust(program, base)
                        self._compile_expr(lhs.width, program)
                        program.append(instr(Op.SUB))
                        one_cid = self._add_int_const(1, 32)
                        program.append(instr(Op.LOAD_CONST, one_cid))
                        program.append(instr(Op.ADD))
                    if immediate:
                        program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                    else:
                        program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                    return
            if type(lhs.target) is Identifier:
                tname = self._resolve_id_name(lhs.target)
                struct_info = self._resolve_struct_storage_access(tname)
                if struct_info is not None:
                    storage_kind, storage_id, storage_index, offset, _field_width = struct_info
                    select_base = self._signal_bases.get(tname, 0)
                    if storage_kind == "memory":
                        marker_sid = self.mem_marker_sigs[storage_id]
                        encoded_arg = storage_id | (marker_sid << 16)
                        if not self._compile_struct_storage_index(storage_index, program):
                            raise ValueError(f"Unsupported dynamic memory struct index: {tname!r}")
                    if lhs.direction == "+:":
                        self._compile_expr(lhs.base, program)
                        if select_base != 0:
                            cid = self._add_int_const(select_base, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.SUB))
                        self._compile_expr(lhs.width, program)
                        program.append(instr(Op.ADD))
                        one_cid = self._add_int_const(1, 32)
                        program.append(instr(Op.LOAD_CONST, one_cid))
                        program.append(instr(Op.SUB))
                        if offset != 0:
                            cid = self._add_int_const(offset, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.ADD))
                        self._compile_expr(lhs.base, program)
                        if select_base != 0:
                            cid = self._add_int_const(select_base, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.SUB))
                        if offset != 0:
                            cid = self._add_int_const(offset, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.ADD))
                    else:
                        self._compile_expr(lhs.base, program)
                        if select_base != 0:
                            cid = self._add_int_const(select_base, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.SUB))
                        if offset != 0:
                            cid = self._add_int_const(offset, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.ADD))
                        self._compile_expr(lhs.base, program)
                        if select_base != 0:
                            cid = self._add_int_const(select_base, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.SUB))
                        self._compile_expr(lhs.width, program)
                        program.append(instr(Op.SUB))
                        one_cid = self._add_int_const(1, 32)
                        program.append(instr(Op.LOAD_CONST, one_cid))
                        program.append(instr(Op.ADD))
                        if offset != 0:
                            cid = self._add_int_const(offset, 32)
                            program.append(instr(Op.LOAD_CONST, cid))
                            program.append(instr(Op.ADD))
                    if storage_kind == "memory":
                        if immediate:
                            program.append(instr(Op.STORE_MEM_RANGE, encoded_arg))
                        else:
                            program.append(instr(Op.NBA_MEM_RANGE, encoded_arg))
                        return
                    if immediate:
                        program.append(instr(Op.STORE_RANGE, storage_id))
                    else:
                        program.append(instr(Op.NBA_RANGE, storage_id))
                    return
                sid = self._get_signal_id(tname)
                # PartSelect LHS: sig[base +: width] or sig[base -: width]
                # Convert to effective msb/lsb and use STORE_RANGE/NBA_RANGE.
                # For "+:": lsb = base, msb = base + width - 1
                # For "-:": msb = base, lsb = base - width + 1
                if lhs.direction == "+:":
                    self._compile_expr(lhs.base, program)  # base → stack
                    self._compile_expr(lhs.width, program)  # width → stack
                    program.append(instr(Op.ADD))  # base + width
                    one_cid = self._add_int_const(1, 32)
                    program.append(instr(Op.LOAD_CONST, one_cid))
                    program.append(instr(Op.SUB))  # msb = base + width - 1
                    self._compile_expr(lhs.base, program)  # lsb = base
                else:
                    self._compile_expr(lhs.base, program)  # msb = base
                    self._compile_expr(lhs.base, program)  # base → stack
                    self._compile_expr(lhs.width, program)  # width → stack
                    program.append(instr(Op.SUB))  # base - width
                    one_cid = self._add_int_const(1, 32)
                    program.append(instr(Op.LOAD_CONST, one_cid))
                    program.append(instr(Op.ADD))  # lsb = base - width + 1
                if immediate:
                    program.append(instr(Op.STORE_RANGE, sid))
                else:
                    program.append(instr(Op.NBA_RANGE, sid))
                return

        if ltype is Concatenation:
            # Concatenation LHS: {a, b, c} = rhs
            # Parts are MSB-first: a gets the highest bits, c gets the lowest.
            # Decompose RHS by extracting the correct bit range for each part.
            part_widths = self._concat_eval_widths(lhs.parts, total_width)
            offset = 0
            for i in reversed(range(len(lhs.parts))):
                part = lhs.parts[i]
                pw = part_widths[i]
                program.append(instr(Op.DUP))
                msb_cid = self._add_int_const(offset + pw - 1, 32)
                lsb_cid = self._add_int_const(offset, 32)
                program.append(instr(Op.LOAD_CONST, msb_cid))
                program.append(instr(Op.LOAD_CONST, lsb_cid))
                program.append(instr(Op.RANGE_SELECT))
                self._compile_store_lhs(part, program, immediate=immediate, total_width=pw)
                offset += pw
            program.append(instr(Op.POP))
            return

    # ── Sensitivity analysis ─────────────────────────────────────

    def _collect_expr_signals(self, expr: Expression) -> set[int]:
        """Collect signal IDs read by an expression."""
        signals: set[int] = set()
        self._walk_expr_signals(expr, signals)
        return signals

    def _walk_expr_signals(self, expr: Expression, signals: set[int]) -> None:  # noqa: PLR0911, PLR0912
        """Recursively collect signal IDs from an expression."""
        if isinstance(expr, Identifier):
            name = self._resolve_id_name(expr)
            if name.startswith("__vt_local_for_"):
                return
            sid = self.signal_map.get(name)
            if sid is not None:
                signals.add(sid)
                return
            mid = self.mem_map.get(name)
            if mid is not None:
                signals.add(self.mem_marker_sigs[mid])
                return
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    signals.add(struct_info[1])
                else:
                    signals.add(self.mem_marker_sigs[struct_info[1]])
                    if isinstance(struct_info[2], str):
                        sid = self.signal_map.get(struct_info[2])
                        if sid is not None:
                            signals.add(sid)
            return

        if isinstance(expr, Literal):
            return

        if isinstance(expr, BinaryOp):
            self._walk_expr_signals(expr.left, signals)
            self._walk_expr_signals(expr.right, signals)
            return

        if isinstance(expr, UnaryOp):
            self._walk_expr_signals(expr.operand, signals)
            return

        if isinstance(expr, TernaryOp):
            self._walk_expr_signals(expr.condition, signals)
            self._walk_expr_signals(expr.true_expr, signals)
            self._walk_expr_signals(expr.false_expr, signals)
            return

        if isinstance(expr, Concatenation):
            for part in expr.parts:
                self._walk_expr_signals(part, signals)
            return

        if isinstance(expr, Replication):
            self._walk_expr_signals(expr.count, signals)
            self._walk_expr_signals(expr.value, signals)
            return

        if isinstance(expr, BitSelect):
            # If this is a memory read, add the memory's marker signal
            # so that combo processes re-fire when the memory is written.
            if isinstance(expr.target, Identifier) and self._is_memory(self._resolve_id_name(expr.target)):
                mid = self.mem_map[self._resolve_id_name(expr.target)]
                signals.add(self.mem_marker_sigs[mid])
            else:
                self._walk_expr_signals(expr.target, signals)
            self._walk_expr_signals(expr.index, signals)
            return

        if isinstance(expr, RangeSelect):
            self._walk_expr_signals(expr.target, signals)
            self._walk_expr_signals(expr.msb, signals)
            self._walk_expr_signals(expr.lsb, signals)
            return

        if isinstance(expr, PartSelect):
            self._walk_expr_signals(expr.target, signals)
            self._walk_expr_signals(expr.base, signals)
            self._walk_expr_signals(expr.width, signals)
            return

        if isinstance(expr, FunctionCall):
            for arg in expr.arguments:
                self._walk_expr_signals(arg, signals)
            return

        if isinstance(expr, StringLiteral):
            return

        if isinstance(expr, Mintypmax):
            self._walk_expr_signals(expr.typ_val, signals)
            return

        if isinstance(expr, AssignmentPattern):
            # Previously unhandled: fell through to a silent no-op, so any
            # signal referenced ONLY inside an assignment pattern's field
            # values was invisible to sensitivity analysis. See the
            # matching fix/comment in `sim/scheduler.py`'s `_walk_expr_
            # reads` for the reference-engine counterpart of this same
            # bug (confirmed cross-engine against Icarus).
            for _name, value_expr in expr.named_pairs:
                self._walk_expr_signals(value_expr, signals)
            if expr.positional:
                for value_expr in expr.positional:
                    self._walk_expr_signals(value_expr, signals)
            if expr.default_value is not None:
                self._walk_expr_signals(expr.default_value, signals)
            return

    def _collect_stmt_signals(self, stmt: Statement, signals: set[int]) -> None:  # noqa: PLR0912, PLR0911
        """Collect signal IDs read in a statement's expressions."""
        if stmt is None:
            return

        if isinstance(stmt, (BlockingAssign, NonblockingAssign)):
            self._walk_expr_signals(stmt.rhs, signals)
            self._collect_lhs_index_signals(stmt.lhs, signals)
            return

        if isinstance(stmt, IfStatement):
            self._walk_expr_signals(stmt.condition, signals)
            self._collect_stmt_signals(stmt.then_body, signals)
            self._collect_stmt_signals(stmt.else_body, signals)
            return

        if isinstance(stmt, CaseStatement):
            self._walk_expr_signals(stmt.expression, signals)
            for item in stmt.items:
                for val in item.values:
                    self._walk_expr_signals(val, signals)
                self._collect_stmt_signals(item.body, signals)
            return

        if isinstance(stmt, SeqBlock):
            for s in stmt.statements:
                self._collect_stmt_signals(s, signals)
            return

        if isinstance(stmt, ParBlock):
            for s in stmt.statements:
                self._collect_stmt_signals(s, signals)
            return

        if isinstance(stmt, ForLoop):
            self._collect_stmt_signals(stmt.init, signals)
            self._walk_expr_signals(stmt.condition, signals)
            self._collect_stmt_signals(stmt.update, signals)
            self._collect_stmt_signals(stmt.body, signals)
            return

        if isinstance(stmt, WhileLoop):
            self._walk_expr_signals(stmt.condition, signals)
            self._collect_stmt_signals(stmt.body, signals)
            return

        if isinstance(stmt, (ForeverLoop, RepeatLoop)):
            if hasattr(stmt, "count"):
                self._walk_expr_signals(stmt.count, signals)
            self._collect_stmt_signals(stmt.body, signals)
            return

        if isinstance(stmt, SystemTaskCall):
            for arg in stmt.arguments:
                self._walk_expr_signals(arg, signals)
            return

        if isinstance(stmt, (DelayControl, EventControl)):
            if hasattr(stmt, "body") and stmt.body:
                self._collect_stmt_signals(stmt.body, signals)
            return

    def _collect_lhs_index_signals(self, lhs: Expression, signals: set[int]) -> None:
        """Collect signal reads from LHS index expressions (not target itself)."""
        if isinstance(lhs, BitSelect):
            self._walk_expr_signals(lhs.index, signals)
        elif isinstance(lhs, RangeSelect):
            self._walk_expr_signals(lhs.msb, signals)
            self._walk_expr_signals(lhs.lsb, signals)
        elif isinstance(lhs, Concatenation):
            for part in lhs.parts:
                self._collect_lhs_index_signals(part, signals)

    def _collect_stmt_writes(self, stmt: Statement, signals: set[int]) -> None:  # noqa: PLR0912, PLR0911
        """Remove signals written by a statement tree from inferred reads."""
        if stmt is None:
            return

        if isinstance(stmt, (BlockingAssign, NonblockingAssign)):
            self._remove_lhs_targets(stmt.lhs, signals)
            return

        if isinstance(stmt, IfStatement):
            self._collect_stmt_writes(stmt.then_body, signals)
            self._collect_stmt_writes(stmt.else_body, signals)
            return

        if isinstance(stmt, CaseStatement):
            for item in stmt.items:
                self._collect_stmt_writes(item.body, signals)
            return

        if isinstance(stmt, SeqBlock):
            for s in stmt.statements:
                self._collect_stmt_writes(s, signals)
            return

        if isinstance(stmt, ParBlock):
            for s in stmt.statements:
                self._collect_stmt_writes(s, signals)
            return

        if isinstance(stmt, ForLoop):
            self._collect_stmt_writes(stmt.init, signals)
            self._collect_stmt_writes(stmt.update, signals)
            self._collect_stmt_writes(stmt.body, signals)
            return

        if isinstance(stmt, WhileLoop):
            self._collect_stmt_writes(stmt.body, signals)
            return

        if isinstance(stmt, (ForeverLoop, RepeatLoop, DelayControl, EventControl)):
            if hasattr(stmt, "body") and stmt.body:
                self._collect_stmt_writes(stmt.body, signals)
            return

    def _remove_lhs_targets(self, lhs: Expression, signals: set[int]) -> None:
        """Remove base LHS targets from an inferred sensitivity set."""
        if isinstance(lhs, Identifier):
            name = self._resolve_id_name(lhs)
            sid = self.signal_map.get(name)
            if sid is not None:
                signals.discard(sid)
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    signals.discard(struct_info[1])
                else:
                    signals.discard(self.mem_marker_sigs[struct_info[1]])
            return
        if isinstance(lhs, (BitSelect, RangeSelect, PartSelect)):
            self._remove_lhs_targets(lhs.target, signals)
            return
        if isinstance(lhs, Concatenation):
            for part in lhs.parts:
                self._remove_lhs_targets(part, signals)

    def _always_sensitivity(self, block: AlwaysBlock) -> tuple[set[int], dict[int, str]]:
        """Determine sensitivity set and edge types for an always block.

        Returns:
            (signal_ids, edge_dict) where edge_dict maps sig_id → "posedge"/"negedge"
        """
        signals: set[int] = set()
        edges: dict[int, str] = {}

        if block.sensitivity_type == SensitivityType.COMBINATIONAL:
            # @(*) — infer from all reads in the body
            self._collect_stmt_signals(block.body, signals)
            self._collect_stmt_writes(block.body, signals)
            return signals, edges

        # Explicit sensitivity list
        for edge in block.sensitivity_list:
            if isinstance(edge, SensitivityEdge):
                if isinstance(edge.signal, Identifier):
                    sid = self._get_signal_id(edge.signal.name)
                    signals.add(sid)
                    if edge.edge in ("posedge", "negedge"):
                        edges[sid] = edge.edge

        return signals, edges


# ── Operator maps ────────────────────────────────────────────────────

_BINARY_OP_MAP: dict[str, Op] = {
    "+": Op.ADD,
    "-": Op.SUB,
    "*": Op.MUL,
    "/": Op.DIV,
    "%": Op.MOD,
    "**": Op.POW,
    "&": Op.BIT_AND,
    "|": Op.BIT_OR,
    "^": Op.BIT_XOR,
    "~^": Op.BIT_XNOR,
    "^~": Op.BIT_XNOR,
    "<<": Op.SHL,
    ">>": Op.SHR,
    "<<<": Op.ASHL,
    ">>>": Op.ASHR,
    "==": Op.CMP_EQ,
    "!=": Op.CMP_NE,
    "<": Op.CMP_LT,
    "<=": Op.CMP_LE,
    ">": Op.CMP_GT,
    ">=": Op.CMP_GE,
    "===": Op.CMP_CASE_EQ,
    "!==": Op.CMP_CASE_NE,
    "&&": Op.LOG_AND,
    "||": Op.LOG_OR,
}

_UNARY_OP_MAP: dict[str, Op] = {
    "~": Op.BIT_NOT,
    "!": Op.LOG_NOT,
    "-": Op.NEG,
    "+": Op.UPLUS,
    "&": Op.RED_AND,
    "|": Op.RED_OR,
    "^": Op.RED_XOR,
    "~&": Op.RED_NAND,
    "~|": Op.RED_NOR,
    "~^": Op.RED_XNOR,
    "^~": Op.RED_XNOR,
}

_SIGNED_CMP_MAP: dict[str, Op] = {
    "<": Op.CMP_SLT,
    "<=": Op.CMP_SLE,
    ">": Op.CMP_SGT,
    ">=": Op.CMP_SGE,
}

_SIGNED_DIVMOD_MAP: dict[str, Op] = {
    "/": Op.SDIV,
    "%": Op.SMOD,
}

_SIGNED_POW_MAP: dict[str, Op] = {
    "**": Op.SPOW,
}


def _is_signed_call(expr) -> bool:
    """True when *expr* is ``$signed(...)``."""
    return isinstance(expr, FunctionCall) and expr.name.lower() == "$signed"


_FIXED_SELF_DETERMINED_BINOPS = frozenset({"==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"})


# ── Helpers ──────────────────────────────────────────────────────────


def _scoped_env(signal_name: str, param_env: dict[str, int]) -> dict[str, int]:
    """Build a param env with unprefixed aliases for a hierarchically-prefixed signal."""
    dot = signal_name.rfind(".")
    if dot < 0:
        return param_env
    prefix = signal_name[: dot + 1]
    local = dict(param_env)
    for k, v in param_env.items():
        if k.startswith(prefix):
            unprefixed = k[len(prefix) :]
            if unprefixed not in local:
                local[unprefixed] = v
    return local


def _dim_depth(dim, param_env=None) -> int:
    """Compute the number of elements from an unpacked dimension Range."""
    try:
        if isinstance(dim.msb, Literal) and isinstance(dim.lsb, Literal):
            lo = int(dim.lsb.value)
            hi = int(dim.msb.value)
            return abs(hi - lo) + 1
    except (TypeError, ValueError):
        pass
    # Fallback: constant-expression evaluation for parametric bounds
    try:
        from ..elaborate import _eval_const_expr  # noqa: PLC0415

        env = param_env if param_env is not None else {}
        lo = _eval_const_expr(dim.lsb, env)
        hi = _eval_const_expr(dim.msb, env)
        return abs(hi - lo) + 1
    except (ValueError, TypeError):
        pass
    return 1


def _memory_shape(dimensions: list, base_width: int, senv: dict, packed_dim_count: int = 0) -> tuple[int, int]:
    """Resolve a dimensioned declaration's true (depth, elem_width).

    Mirrors `sim/scheduler.py`'s identical helper of the same name: a
    dimensioned net/var/port's `dimensions` list can hold BOTH extra
    PACKED dims (from the element's own multi-dim packed declaration,
    e.g. the outer `[3:0]` in `logic [3:0][7:0] mem [3:0]`) and genuinely
    UNPACKED array dimension(s) (the trailing `[3:0]` there) --
    `dimensions` alone can't tell the two apart (both are just `Range`
    entries, and guessing "last dim only" broke a genuine multi-
    dimensional unpacked array's depth computation the same way guessing
    "every dim" broke the packed-element case), so this uses
    `packed_dim_count` (`Net`/`Variable`/`Port`'s own field recording how
    many LEADING `dimensions` entries are packed) instead of guessing.
    Every dimension from `packed_dim_count` onward is a genuine address
    dimension; depth is their product (this engine only ever addresses
    memories with a single flat index, so multiple genuine dims collapse
    to one flat depth here). A no-op for the common single-dimension
    case either way.
    """
    elem_width = base_width
    for extra_dim in dimensions[:packed_dim_count]:
        elem_width *= _dim_depth(extra_dim, senv)
    depth = 1
    for addr_dim in dimensions[packed_dim_count:]:
        depth *= _dim_depth(addr_dim, senv)
    return depth, elem_width
