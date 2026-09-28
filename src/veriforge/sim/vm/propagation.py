"""Conservative scheduling plans for native batch continuous assignments."""

from __future__ import annotations

from collections import deque

from .compiler import CompiledProcess, ProcessType
from .opcodes import Op


# An allowlist is deliberate: new opcodes must be reviewed for side effects
# before an assignment containing them may execute fewer times or out of order.
_PURE_EXPRESSION_OPS = frozenset(
    {
        Op.LOAD_SIG,
        Op.LOAD_CONST,
        Op.RESIZE,
        Op.SIGN_EXT,
        Op.ADD,
        Op.SUB,
        Op.MUL,
        Op.DIV,
        Op.MOD,
        Op.POW,
        Op.SDIV,
        Op.SMOD,
        Op.SPOW,
        Op.BIT_AND,
        Op.BIT_OR,
        Op.BIT_XOR,
        Op.BIT_XNOR,
        Op.BIT_NOT,
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
        Op.CMP_SLT,
        Op.CMP_SLE,
        Op.CMP_SGT,
        Op.CMP_SGE,
        Op.LOG_AND,
        Op.LOG_OR,
        Op.LOG_NOT,
        Op.NEG,
        Op.UPLUS,
        Op.RED_AND,
        Op.RED_OR,
        Op.RED_XOR,
        Op.RED_NAND,
        Op.RED_NOR,
        Op.RED_XNOR,
        Op.BIT_SELECT,
        Op.RANGE_SELECT,
        Op.PART_SEL_UP,
        Op.PART_SEL_DOWN,
        Op.CONCAT,
        Op.REPLICATE,
        Op.TERNARY,
        Op.STREAM_REVERSE,
        Op.FUNC_CLOG2,
    }
)
_SIGNAL_WRITES = frozenset({Op.STORE_SIG, Op.STORE_BIT, Op.STORE_RANGE, Op.NBA_SIG, Op.NBA_BIT, Op.NBA_RANGE})


def plan_continuous_order(processes: list[CompiledProcess]) -> tuple[list[int], int] | None:
    """Return assignment indices in dependency order and maximum path length.

    Require a pure, single-writer DAG whose destinations cannot trigger any
    procedural process. Even an acyclic diamond can glitch while settling;
    suppressing that glitch must not suppress a procedural activation. Only
    batch execution uses this plan, after rejecting coroutines and monitors.
    Any uncertain case falls back to the existing multi-pass scheduler.
    """
    continuous = [p for p in processes if p.process_type == ProcessType.CONTINUOUS]
    if len(continuous) < 2 or any(p.has_timing for p in processes):
        return None
    writers: dict[int, int] = {}
    for index, proc in enumerate(continuous):
        program = proc.program
        if len(program) < 3 or program[-1][0] != Op.PROC_END or program[-2][0] != Op.STORE_SIG:
            return None
        if any(op not in _PURE_EXPRESSION_OPS for op, _, _ in program[:-2]):
            return None
        # Check the compiler's sensitivity metadata against actual bytecode
        # reads before relying on it for dependency scheduling.
        reads = {sid for op, sid, _ in program[:-2] if op == Op.LOAD_SIG}
        if reads != proc.sensitivity:
            return None
        dest = program[-2][1]
        if dest in writers:
            return None
        writers[dest] = index

    for proc in processes:
        if proc.process_type == ProcessType.CONTINUOUS:
            continue
        if writers.keys() & (proc.sensitivity | proc.edge_signals.keys()):
            return None
        if any(op in _SIGNAL_WRITES and sid in writers for op, sid, _ in proc.program):
            return None

    dependents: list[list[int]] = [[] for _ in continuous]
    pending = [0] * len(continuous)
    for index, proc in enumerate(continuous):
        for sid in sorted(proc.sensitivity):
            parent = writers.get(sid)
            if parent is not None:
                dependents[parent].append(index)
                pending[index] += 1
    ready = deque(i for i, count in enumerate(pending) if count == 0)
    order: list[int] = []
    depth = [1] * len(continuous)
    while ready:
        index = ready.popleft()
        order.append(index)
        for child in dependents[index]:
            depth[child] = max(depth[child], depth[index] + 1)
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    max_depth = max(depth)
    if len(order) != len(continuous) or max_depth == 1:
        return None  # Cycles require iteration; independent assigns need no reordering.
    if not any(len(p.sensitivity) > 1 and writers.keys() & p.sensitivity for p in continuous):
        # Single-input chains/fanout already propagate once per assignment.
        # Queue bookkeeping adds cost without removing redundant evaluations.
        return None
    return order, max_depth
