"""Hierarchy tree, hierarchy graph, and signal-trace payload builders.

Split out of ``handlers/extended.py`` (work plan item 4.4): everything here
answers "what does the design hierarchy / a signal's connectivity look
like", as opposed to ``handlers/refactor.py``'s "rewrite the source".
``extended.py`` keeps ``register()`` as the sole ``@ls.command`` aggregator
and re-exports the pieces it needs from here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pygls.lsp.server import LanguageServer

from veriforge.model.assignments import ContinuousAssign
from veriforge.model.behavioral import AlwaysBlock, InitialBlock
from veriforge.model.expressions import Identifier
from veriforge.model.instances import Instance
from veriforge.model.nets import Net
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import BlockingAssign, NonblockingAssign
from veriforge.model.variables import Variable
from veriforge.refactor import (
    build_hierarchy_graph,
    classify_wrapper_module,
    hierarchy_graph_to_dot,
    hierarchy_graph_to_mermaid,
    hierarchy_graph_to_text,
)
from veriforge_lsp.payloads import (
    ErrorPayload,
    HierarchyGraphRequest,
    HierarchyGraphResponse,
    TraceEntry,
    TraceSignalInfo,
    TraceSignalRequest,
    TraceSignalResponse,
)
from veriforge_lsp.protocol import loc_to_lsp_range, path_to_uri, uri_to_path

log = logging.getLogger(__name__)


def _error_payload(code: str, message: str) -> dict:
    return ErrorPayload(code, message).to_dict()


def push_hierarchy_tree(ls: LanguageServer) -> None:
    """Send verilog/hierarchyTree notification to the client."""
    ws = ls.workspace_manager  # type: ignore[attr-defined]
    if ws is None:
        log.warning("push_hierarchy_tree: workspace_manager is None")
        return
    roots = _build_tree(ws)
    log.warning("push_hierarchy_tree: sending verilog/hierarchyTree with %d roots", len(roots))
    ls.protocol.notify("verilog/hierarchyTree", {"roots": roots})
    log.warning("push_hierarchy_tree: notification sent")


def _trace_signal_payload(ws: Any, params: dict | None) -> dict | None:
    if not isinstance(params, dict):
        return None
    request = TraceSignalRequest.from_dict(params)
    path = uri_to_path(request.uri)
    line = request.line
    char = request.character
    node = ws.index.node_at(path, line, char)
    log.warning("traceSignal: path=%s line=%d char=%d", path, line, char)
    log.warning("traceSignal: node type=%s repr=%r", type(node).__name__, node)
    if node is None:
        return None
    if hasattr(node, "resolved"):
        log.warning("traceSignal: node.resolved=%r", node.resolved)
    target = _resolve_signal_target(node)
    log.warning("traceSignal: target type=%s name=%r", type(target).__name__, getattr(target, "name", None))
    log.warning(
        "traceSignal: drivers=%r loads=%r",
        getattr(target, "drivers", "NO_ATTR"),
        getattr(target, "loads", "NO_ATTR"),
    )
    parent = getattr(target, "parent", None)
    log.warning("traceSignal: parent=%r", parent)
    if parent is not None:
        net_names = [n.name for n in getattr(parent, "nets", [])]
        var_names = [v.name for v in getattr(parent, "variables", [])]
        log.warning("traceSignal: parent.nets=%s", net_names)
        log.warning("traceSignal: parent.vars=%s", var_names)
    if target is None:
        return None
    return _build_trace(target, ws.design, path)


# ------------------------------------------------------------------
# Hierarchy tree builders
# ------------------------------------------------------------------


def _build_tree(ws: Any) -> list[dict]:
    roots = ws.get_hierarchy_roots()
    design = ws.design
    return [_module_node(m, design, depth=2) for m in roots]


def _module_node(mod: Any, design: Any, depth: int, instance_path: str | None = None) -> dict:
    module_path = instance_path or mod.name
    node: dict = {
        "name": mod.name,
        "moduleName": mod.name,
        "instancePath": module_path,
        "file": path_to_uri(mod.loc.file) if (mod.loc and mod.loc.file) else "",
        "range": loc_to_lsp_range(mod.loc) if mod.loc else {},
        "children": _module_children(mod, design, depth, parent_path=module_path) if depth > 0 else [],
        "hasMoreChildren": depth <= 0 and bool(mod.instances),
    }
    return node


def _instance_node(inst: Any, design: Any, depth: int, parent_path: str) -> dict:
    instance_path = f"{parent_path}/{inst.instance_name}"
    resolved = inst.resolved_module
    file_uri = ""
    rng: dict = {}
    if resolved and resolved.loc and resolved.loc.file:
        file_uri = path_to_uri(resolved.loc.file)
        rng = loc_to_lsp_range(resolved.loc)
    # Instantiation location — where "u1 counter(...)" appears in parent file
    inst_file_uri = ""
    inst_rng: dict = {}
    if inst.loc and inst.loc.file:
        inst_file_uri = path_to_uri(inst.loc.file)
        inst_rng = loc_to_lsp_range(inst.loc)
    children: list[dict] = []
    has_more = False
    if resolved:
        if depth > 0:
            children = _module_children(resolved, design, depth, parent_path=instance_path)
        else:
            has_more = bool(resolved.instances)
    node = {
        "instanceName": inst.instance_name,
        "moduleName": inst.module_name,
        "instancePath": instance_path,
        "file": file_uri,
        "range": rng,
        "instanceFile": inst_file_uri,
        "instanceRange": inst_rng,
        "children": children,
        "hasMoreChildren": has_more,
    }
    node.update(classify_wrapper_module(resolved).to_dict())
    return node


def _module_children(mod: Any, design: Any, depth: int, parent_path: str) -> list[dict]:
    children: list[dict] = []
    for inst in mod.instances or []:
        children.append(_instance_node(inst, design, depth - 1, parent_path=parent_path))
    return children


def _hierarchy_graph_payload(ws: Any, params: dict | None = None) -> dict:
    request = HierarchyGraphRequest.from_dict(params)
    try:
        max_depth = _lsp_max_depth(request.max_depth)
        graph = build_hierarchy_graph(ws.design, top=request.top or ws.top_module, max_depth=max_depth)
    except ValueError as exc:
        return _error_payload("hierarchy-graph-error", str(exc))

    if request.format == "json":
        return HierarchyGraphResponse(hierarchy_graph=graph.to_dict()).to_dict()
    if request.format == "text":
        visualization = hierarchy_graph_to_text(graph)
    elif request.format == "dot":
        visualization = hierarchy_graph_to_dot(graph)
    elif request.format == "mermaid":
        visualization = hierarchy_graph_to_mermaid(graph)
    else:
        return _error_payload("unsupported-hierarchy-format", f"Unsupported hierarchy graph format: {request.format}")
    return HierarchyGraphResponse(
        hierarchy_graph=graph.to_dict(), visualization=visualization, format=request.format
    ).to_dict()


def _lsp_max_depth(value: Any) -> int | None:
    if value is None:
        return None
    max_depth = int(value)
    return None if max_depth < 0 else max_depth


# ------------------------------------------------------------------
# Signal trace
# ------------------------------------------------------------------


def _resolve_signal_target(node: Any) -> Any:
    candidate = node
    if isinstance(node, Identifier) and node.resolved is not None:
        candidate = node.resolved

    if isinstance(candidate, (Net, Variable)):
        return candidate
    if isinstance(candidate, Port):
        # Ports don't carry drivers/loads — look up same-named Net or Variable in parent module
        parent_mod = candidate.parent
        if parent_mod is not None:
            net = getattr(parent_mod, "get_net", lambda _: None)(candidate.name)
            if net is not None:
                return net
            var = getattr(parent_mod, "get_variable", lambda _: None)(candidate.name)
            if var is not None:
                return var
        return candidate  # fall back to Port so caller gets an empty trace rather than None
    return None


_MAX_TRACE_DEPTH = 2


def _build_trace(target: Any, design: Any, context_file: str) -> dict:
    name = target.name
    parent_mod = getattr(target, "parent", None)
    module_name = getattr(parent_mod, "name", "") if parent_mod else ""
    loc = getattr(target, "loc", None)
    signal_info: dict = {
        "name": name,
        "width": _width_str(target),
        "module": module_name,
        "file": path_to_uri(loc.file) if (loc and loc.file) else path_to_uri(context_file),
        "definitionRange": loc_to_lsp_range(loc) if loc else {},
    }

    drivers: list[dict] = []
    loads: list[dict] = []

    for driver in getattr(target, "drivers", []) or []:
        source = getattr(driver, "source", None) or driver
        drivers.extend(_entries_for_source(source, name, module_name, [name], 0))
        # When the driver is an instance, also gather that instance's internal loads
        # (reads of the corresponding internal signal) into the loads section.
        loads.extend(_instance_internal_loads(source, name, module_name, [name]))

    for load in getattr(target, "loads", []) or []:
        consumer = getattr(load, "consumer", None) or load
        loads.extend(_entries_for_consumer(consumer, name, module_name, [name], 0))

    # Upward tracing: if this signal is a port (or shares a name with one), add
    # the parent-module port connections so the user can trace back out.
    if parent_mod is not None:
        port = parent_mod.get_port(name)
        if port is not None:
            upward = _find_upward_connections(port, parent_mod, design, name)
            if port.direction == PortDirection.INPUT:
                drivers.extend(upward)  # input comes FROM outside → parent = driver
            elif port.direction == PortDirection.OUTPUT:
                loads.extend(upward)  # output goes TO outside → parent = load
            else:
                drivers.extend(upward)
                loads.extend(upward)

    return TraceSignalResponse(
        signal=TraceSignalInfo.from_dict(signal_info),
        drivers=[TraceEntry.from_dict(entry) for entry in drivers],
        loads=[TraceEntry.from_dict(entry) for entry in loads],
    ).to_dict()


def _instance_internal_loads(source: Any, signal_name: str, inst_path: str, chain: list[str]) -> list[dict]:
    """When source is an Instance that drives signal_name, return load entries for the
    corresponding internal signal inside that instance (reads of the signal inside the child module).
    Includes a boundary entry so the caller knows which instance contains the loads."""
    if not isinstance(source, Instance):
        return []
    conn = _find_port_conn(source, signal_name)
    loc = getattr(conn, "loc", None) if conn else getattr(source, "loc", None)
    if not loc or not loc.file:
        return []
    port_name = getattr(conn, "port_name", None) if conn else None
    if not port_name:
        return []
    resolved = getattr(source, "resolved_module", None)
    if not resolved:
        return []
    child_sig = resolved.get_net(port_name) or resolved.get_variable(port_name) or resolved.get_port(port_name)
    if child_sig is None:
        return []
    new_chain = [*chain, port_name] if port_name != signal_name else chain
    child_path = f"{inst_path}/{source.instance_name}"
    label = f"{source.instance_name}.{port_name}"
    entries: list[dict] = [_make_entry("port_connection", label, loc, inst_path, chain, style="boundary")]
    child_entries: list[dict] = []
    for ld in getattr(child_sig, "loads", []) or []:
        con = getattr(ld, "consumer", None) or ld
        for e in _entries_for_consumer(con, port_name, child_path, new_chain, 1):
            e["indent"] = e.get("indent", 0) + 1
            child_entries.append(e)
    child_entries.sort(key=lambda e: e.get("range", {}).get("start", {}).get("line", 0))
    entries.extend(child_entries)
    return entries


def _entries_for_source(source: Any, signal_name: str, inst_path: str, chain: list[str], depth: int) -> list[dict]:
    """Entries for something that drives signal_name; recurses into child modules."""
    if not isinstance(source, Instance):
        return _simple_entries(source, signal_name, inst_path, chain, style="driver")

    conn = _find_port_conn(source, signal_name)
    loc = getattr(conn, "loc", None) if conn else getattr(source, "loc", None)
    if not loc or not loc.file:
        return []

    port_name = getattr(conn, "port_name", None) if conn else None
    label = f"{source.instance_name}.{port_name}" if port_name else source.instance_name
    entries = [_make_entry("port_connection", label, loc, inst_path, chain, style="boundary")]

    if depth < _MAX_TRACE_DEPTH and port_name:
        resolved = getattr(source, "resolved_module", None)
        if resolved:
            child_sig = (
                resolved.get_net(port_name)
                or resolved.get_variable(port_name)
                or resolved.get_port(port_name)  # output reg without separate variable
            )
            if child_sig is not None:
                new_chain = [*chain, port_name] if port_name != signal_name else chain
                child_path = f"{inst_path}/{source.instance_name}"
                child_entries: list[dict] = []
                for drv in getattr(child_sig, "drivers", []) or []:
                    src = getattr(drv, "source", None) or drv
                    for e in _entries_for_source(src, port_name, child_path, new_chain, depth + 1):
                        e["indent"] = e.get("indent", 0) + 1
                        child_entries.append(e)
                child_entries.sort(key=lambda e: e.get("range", {}).get("start", {}).get("line", 0))
                entries.extend(child_entries)

    return entries


def _entries_for_consumer(consumer: Any, signal_name: str, inst_path: str, chain: list[str], depth: int) -> list[dict]:
    """Entries for something that reads signal_name; recurses into child modules."""
    if not isinstance(consumer, Instance):
        return _simple_entries(consumer, signal_name, inst_path, chain)

    conn = _find_port_conn(consumer, signal_name)
    loc = getattr(conn, "loc", None) if conn else getattr(consumer, "loc", None)
    if not loc or not loc.file:
        return []

    port_name = getattr(conn, "port_name", None) if conn else None
    label = f"{consumer.instance_name}.{port_name}" if port_name else consumer.instance_name
    entries = [_make_entry("port_connection", label, loc, inst_path, chain, style="boundary")]

    if depth < _MAX_TRACE_DEPTH and port_name:
        resolved = getattr(consumer, "resolved_module", None)
        if resolved:
            child_sig = (
                resolved.get_net(port_name)
                or resolved.get_variable(port_name)
                or resolved.get_port(port_name)  # input port without separate net
            )
            if child_sig is not None:
                new_chain = [*chain, port_name] if port_name != signal_name else chain
                child_path = f"{inst_path}/{consumer.instance_name}"
                child_entries = []
                for ld in getattr(child_sig, "loads", []) or []:
                    con = getattr(ld, "consumer", None) or ld
                    for e in _entries_for_consumer(con, port_name, child_path, new_chain, depth + 1):
                        e["indent"] = e.get("indent", 0) + 1
                        child_entries.append(e)
                child_entries.sort(key=lambda e: e.get("range", {}).get("start", {}).get("line", 0))
                entries.extend(child_entries)

    return entries


def _find_port_conn(inst: Any, signal_name: str) -> Any:
    """Find the PortConnection on inst whose expression is an Identifier named signal_name."""
    for conn in getattr(inst, "port_connections", []):
        expr = getattr(conn, "expression", None)
        if expr is None:
            continue
        if isinstance(expr, Identifier) and expr.name == signal_name:
            return conn
        if getattr(expr, "name", None) == signal_name:
            return conn
    return None


def _find_port_conn_by_port(inst: Any, port_name: str) -> Any:
    """Find the PortConnection on inst for a specific port name (child-side name)."""
    for conn in getattr(inst, "port_connections", []):
        if getattr(conn, "port_name", None) == port_name:
            return conn
    return None


def _find_upward_connections(port: Any, module: Any, design: Any, signal_name: str) -> list[dict]:  # noqa: PLR0912
    """Search all parent modules for instances of module and return their port connection entries.

    For each parent instance found, emits a yellow boundary entry then follows
    the parent-side signal to find its drivers (for input ports) or loads (for output ports).
    """
    entries: list[dict] = []
    for parent_mod in design.modules:
        for inst in parent_mod.instances:
            if getattr(inst, "resolved_module", None) is not module:
                continue
            conn = _find_port_conn_by_port(inst, port.name)
            inst_loc = getattr(inst, "loc", None)
            if conn is None:
                # Unconnected port — mark red using instance location
                if inst_loc and inst_loc.file:
                    label = f"{inst.instance_name}.{port.name}  [unconnected]"
                    entries.append(
                        _make_entry(
                            "port_connection", label, inst_loc, parent_mod.name, [signal_name], style="unconnected"
                        )
                    )
                continue
            loc = getattr(conn, "loc", None) or inst_loc
            if not loc or not loc.file:
                continue
            expr = getattr(conn, "expression", None)
            parent_sig_name = getattr(expr, "name", "") if expr else ""
            if not parent_sig_name:
                # Connected port with no expression — effectively unconnected
                label = f"{inst.instance_name}.{port.name}  [unconnected]"
                entries.append(
                    _make_entry("port_connection", label, loc, parent_mod.name, [signal_name], style="unconnected")
                )
                continue
            chain = [signal_name, parent_sig_name] if parent_sig_name != signal_name else [signal_name]
            label = f"{inst.instance_name}.{port.name} → {parent_sig_name}"
            entries.append(_make_entry("port_connection", label, loc, parent_mod.name, chain, style="boundary"))

            # Follow the parent-side signal to find its drivers (input) or loads (output)
            if parent_sig_name:
                parent_sig = _get_signal_from_module(parent_mod, parent_sig_name)
                if parent_sig is not None:
                    if port.direction == PortDirection.INPUT:
                        for drv in getattr(parent_sig, "drivers", []) or []:
                            src = getattr(drv, "source", None) or drv
                            for e in _entries_for_source(src, parent_sig_name, parent_mod.name, chain, 0):
                                e["indent"] = e.get("indent", 0) + 1
                                entries.append(e)
                    elif port.direction == PortDirection.OUTPUT:
                        for ld in getattr(parent_sig, "loads", []) or []:
                            con = getattr(ld, "consumer", None) or ld
                            for e in _entries_for_consumer(con, parent_sig_name, parent_mod.name, chain, 0):
                                e["indent"] = e.get("indent", 0) + 1
                                entries.append(e)
    return entries


def _get_signal_from_module(mod: Any, name: str) -> Any:
    """Look up a Net, Variable, or Port by name in a module."""
    for method in ("get_net", "get_variable", "get_port"):
        fn = getattr(mod, method, None)
        if fn:
            result = fn(name)
            if result is not None:
                return result
    return None


def _simple_entries(node: Any, signal_name: str, inst_path: str, chain: list[str], style: str = "") -> list[dict]:
    """Return one entry per unique source line where signal_name appears inside node."""
    base_loc = getattr(node, "loc", None)
    if not base_loc or not base_loc.file:
        return []

    # Detect rename assigns: `assign b = a` where both sides are simple identifiers.
    if isinstance(node, ContinuousAssign):
        lhs = node.lhs
        rhs = node.rhs
        if isinstance(lhs, Identifier) and isinstance(rhs, Identifier):
            other = lhs.name if rhs.name == signal_name else rhs.name
            rename_chain = [*chain, other] if other not in chain else chain
            locs = [base_loc]
            return [
                _make_entry("assign", _node_label(node), loc, inst_path, rename_chain, style="rename") for loc in locs
            ]

    lhs_only = style == "driver"
    exclude_lhs = not lhs_only  # for consumer entries, skip write-side occurrences
    locs = _find_all_signal_locs_in_node(node, signal_name, lhs_only=lhs_only, exclude_lhs=exclude_lhs)
    if not locs:
        locs = [base_loc]
    kind = _node_kind(node)
    label = _node_label(node)
    return [_make_entry(kind, label, loc, inst_path, chain, style=style) for loc in locs]


def _find_all_signal_locs_in_node(
    node: Any, signal_name: str, lhs_only: bool = False, exclude_lhs: bool = False
) -> list[Any]:
    """Return unique-line SourceLocations for signal_name inside node.

    lhs_only=True  — only lines where signal_name is on the LHS of an assignment (true writes).
    exclude_lhs=True — skip lines where signal_name is on the LHS (true reads only).
    Both default to False, which returns all occurrences.
    """
    if isinstance(node, (AlwaysBlock, InitialBlock)):
        subtree = getattr(node, "body", None)
    else:
        subtree = node

    if subtree is None:
        return []

    seen_lines: set[int] = set()
    locs: list[Any] = []

    if lhs_only:
        # Driver mode: only locations where signal_name is written (LHS of assignment).
        for child in subtree.walk():
            if not isinstance(child, (BlockingAssign, NonblockingAssign)):
                continue
            lhs = child.lhs
            lhs_names = {n.name for n in lhs.walk() if isinstance(n, Identifier)}
            if signal_name not in lhs_names:
                continue
            loc = getattr(child, "loc", None)
            if loc and loc.file and loc.line not in seen_lines:
                seen_lines.add(loc.line)
                locs.append(loc)
    else:
        # Collect lines where signal_name appears on the LHS so we can skip them
        # when exclude_lhs=True (consumer/load mode).
        lhs_lines: set[int] = set()
        if exclude_lhs:
            for child in subtree.walk():
                if not isinstance(child, (BlockingAssign, NonblockingAssign)):
                    continue
                for n in child.lhs.walk():
                    if isinstance(n, Identifier) and n.name == signal_name:
                        loc = getattr(n, "loc", None)
                        if loc:
                            lhs_lines.add(loc.line)

        for child in subtree.walk():
            if isinstance(child, Identifier) and child.name == signal_name:
                loc = getattr(child, "loc", None)
                if loc and loc.file and loc.line not in seen_lines and loc.line not in lhs_lines:
                    seen_lines.add(loc.line)
                    locs.append(loc)
    return locs


def _make_entry(  # noqa: PLR0913
    kind: str,
    label: str,
    loc: Any,
    inst_path: str,
    chain: list[str],
    *,
    style: str = "",
    indent: int = 0,
) -> dict:
    return {
        "kind": kind,
        "label": label,
        "file": path_to_uri(loc.file),
        "range": loc_to_lsp_range(loc),
        "instancePath": inst_path,
        "preview": _read_preview(loc.file, loc.line),
        "signalChain": list(chain),
        "style": style,
        "indent": indent,
    }


def _node_label(node: Any) -> str:
    if isinstance(node, ContinuousAssign):
        lhs_name = getattr(node.lhs, "name", None) or repr(node.lhs)
        return f"assign {lhs_name} = ..."
    if isinstance(node, AlwaysBlock):
        return "always @(...)"
    return type(node).__name__


def _node_kind(node: Any) -> str:
    if isinstance(node, ContinuousAssign):
        return "assign"
    if isinstance(node, AlwaysBlock):
        return "always"
    if isinstance(node, Instance):
        return "port_connection"
    return "expression"


def _read_preview(file_path: str, lark_line: int, context: int = 15) -> str:
    """Return a few lines of source context around lark_line (1-based)."""
    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
        idx = max(0, lark_line - 1)  # convert to 0-based
        start = max(0, idx - context)
        end = min(len(lines), idx + context + 1)
        return "\n".join(lines[start:end])
    except OSError:
        return ""


def _width_str(node: Any) -> str:
    rng = getattr(node, "width", None)
    if rng is None:
        return ""
    msb = getattr(rng, "msb", None)
    lsb = getattr(rng, "lsb", None)
    if msb is None or lsb is None:
        return ""
    return f"[{msb}:{lsb}]"
