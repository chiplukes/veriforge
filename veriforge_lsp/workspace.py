"""
Workspace: manages project-level parsing, caching, and the Verible linter subprocess.

Parsing happens in three tiers (see notes/veriforge_lsp.md):
  Syntax  — Verible only, debounced on didChange  (fast, tolerates errors)
  File    — veriforge single file, on didSave if Verible reports no syntax error
  Full    — veriforge whole workspace, on startup or when module interfaces change
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from veriforge.analysis import analyze_design, lint_design
from veriforge.model.design import Design
from veriforge.project import parse_directory, parse_files

from veriforge_lsp.index import LocationIndex
from veriforge_lsp.protocol import path_to_uri, uri_to_path

log = logging.getLogger(__name__)

_VERILOG_EXTS = {".v", ".vh", ".sv", ".svh"}
_DEFAULT_ROOT_MARKERS = [".git", "Makefile", "*.f"]

# Idle delay before running the Lark syntax check on an unsaved buffer.
# Short enough to be useful, long enough not to fire on every keystroke.
_LARK_DEBOUNCE = 1.2  # seconds

# Module-level singleton: Lark parser is created once (grammar load is ~1 s).
_lark_parser_singleton: "Any | None" = None
_lark_parser_lock = threading.Lock()


def _get_lark_parser():
    """Return a cached verilog_parser instance (created on first call)."""
    global _lark_parser_singleton
    if _lark_parser_singleton is None:
        with _lark_parser_lock:
            if _lark_parser_singleton is None:
                from veriforge.verilog_parser import verilog_parser as _VP

                _lark_parser_singleton = _VP(start="source_text")
    return _lark_parser_singleton


class Workspace:
    """
    Owns the Design object and the LocationIndex for one LSP workspace root.

    Thread safety: parse operations run in a ThreadPoolExecutor; callers on
    the pygls asyncio thread interact via thread-safe methods only.
    """

    def __init__(
        self,
        root_path: str,
        progress_cb: Any = None,
        on_parse_complete: Any = None,
        verible_rules: list[str] | None = None,
    ) -> None:
        self.root = root_path
        self._progress_cb = progress_cb  # callable(token, kind, value) or None
        self._on_parse_complete = on_parse_complete  # callable() or None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verilog-parse")
        self._lock = threading.Lock()

        # Current design and index (swapped atomically under _lock)
        self._design: Design = Design()
        self._index: LocationIndex = LocationIndex()

        # Per-file state
        self._file_hashes: dict[str, str] = {}
        self._file_has_syntax_error: dict[str, bool] = {}
        self._file_is_stale: dict[str, bool] = {}
        # open buffer contents (uri → text), overrides on-disk when set
        self._open_texts: dict[str, str] = {}

        # Workspace config from .veriforge_lsp.json
        self._config: dict = {}
        self._config_path = Path(root_path) / ".veriforge_lsp.json"
        self._load_config()

        # Extra Verible rule exemptions passed programmatically (merged with config)
        self._extra_verible_rules: list[str] = list(verible_rules or [])

        # Verible binary (None if not found)
        self._verible_bin: str | None = self._find_verible()

        # Pending diagnostic callbacks: path → list[dict]
        self._verible_diags: dict[str, list[dict]] = {}
        self._semantic_diags: dict[str, list[dict]] = {}
        # callback(uri, diagnostics) registered by the server
        self._diag_cb: Any = None

        # Debounce timers for the Lark fallback syntax check (uri → Timer).
        # Only active when Verible is not installed.
        self._lark_debounce: dict[str, threading.Timer] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        if self._config_path.exists():
            try:
                self._config = json.loads(self._config_path.read_text(encoding="utf-8"))
                log.debug("Loaded workspace config from %s", self._config_path)
            except Exception as e:
                log.warning("Failed to load .veriforge_lsp.json: %s", e)
        else:
            self._config = {}

    def save_config(self) -> None:
        try:
            self._config_path.write_text(json.dumps(self._config, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Failed to save .veriforge_lsp.json: %s", e)

    @property
    def top_module(self) -> str | None:
        return self._config.get("top_module")

    def set_top_module(self, name: str | None) -> None:
        self._config["top_module"] = name
        self.save_config()

    @property
    def include_dirs(self) -> list[str]:
        return self._config.get("include_dirs", [])

    @property
    def defines(self) -> list[str]:
        return self._config.get("parse_options", {}).get("defines", [])

    # ------------------------------------------------------------------
    # Verible
    # ------------------------------------------------------------------

    def _find_verible(self) -> str | None:
        configured = self._config.get("verible_lint_path")
        if configured:
            return configured if shutil.which(configured) else None
        return shutil.which("verible-verilog-lint")

    @property
    def verible_available(self) -> bool:
        return self._verible_bin is not None

    @property
    def verible_rules_active(self) -> list[str]:
        """The merged list of verible rules that will be passed to the linter."""
        return list(self._config.get("verible_rules", [])) + self._extra_verible_rules

    # Pattern: filename:line:col: message [Category: desc] [rule-name]
    _VERIBLE_LINE_RE = re.compile(r"^(.+?):(\d+):(\d+):\s*(.+)$")
    # Trailing [rule] bracket (last bracket group in the line)
    _VERIBLE_RULE_RE = re.compile(r"\[([^\]]+)\]\s*$")

    def run_verible(self, file_path: str, content: str | None = None) -> list[dict]:
        """Run verible-verilog-lint on a file; return LSP Diagnostic dicts."""
        if not self._verible_bin:
            return []
        try:
            all_rules = self.verible_rules_active
            cmd = [self._verible_bin, "--rules_config_search"]
            if all_rules:
                cmd.append(f"--rules={','.join(all_rules)}")
            if content is not None:
                cmd.append("-")
                result = subprocess.run(  # noqa: S603
                    cmd, input=content.encode(), capture_output=True, timeout=10, check=False
                )
            else:
                cmd.append(file_path)
                result = subprocess.run(cmd, capture_output=True, timeout=10, check=False)  # noqa: S603
            # verible exits non-zero when violations found; combine stdout+stderr
            output = result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
            return self._parse_verible_output(output, file_path)
        except Exception as e:
            log.warning("verible error for %s: %s", file_path, e)
            return []

    def _parse_verible_output(self, output: str, fallback_path: str) -> list[dict]:
        """Parse Verible text-format output into LSP Diagnostic dicts.

        Format: filename:line:col: message [Category: desc] [rule-name]
        """
        diags: list[dict] = []
        for raw_line in output.splitlines():
            line_str = raw_line.strip()
            if not line_str:
                continue
            m = self._VERIBLE_LINE_RE.match(line_str)
            if not m:
                continue
            _fname, line_1, col_1, msg = m.group(1), m.group(2), m.group(3), m.group(4)
            line = max(0, int(line_1) - 1)
            col = max(0, int(col_1) - 1)
            # Extract rule name from trailing [rule] bracket, if present
            rule_m = self._VERIBLE_RULE_RE.search(msg)
            rule = rule_m.group(1) if rule_m else ""
            # Strip trailing bracket groups from the human-readable message
            message = re.sub(r"\s*\[[^\]]*\]\s*$", "", msg).strip()
            message = re.sub(r"\s*\[[^\]]*\]\s*$", "", message).strip()
            is_syntax = "syntax error" in msg.lower()
            severity = 1 if is_syntax else 2
            diags.append(
                {
                    "range": {
                        "start": {"line": line, "character": col},
                        "end": {"line": line, "character": col + 1},
                    },
                    "severity": severity,
                    "source": "verible",
                    "code": rule,
                    "message": message,
                }
            )
        return diags

    def _has_verible_syntax_error(self, diags: list[dict]) -> bool:
        return any("syntax" in str(d.get("code", "")).lower() for d in diags if d.get("severity") == 1)

    # ------------------------------------------------------------------
    # Public file lifecycle (called from pygls handlers)
    # ------------------------------------------------------------------

    def on_did_open(self, uri: str, text: str) -> None:
        self._open_texts[uri] = text

    def on_did_change(self, uri: str, text: str, schedule_verible: bool = True) -> None:
        """Record new buffer content; trigger debounced syntax check.

        When Verible is installed it is used (existing behaviour). When it is
        absent a debounced Lark parse runs instead so the user still receives
        syntax diagnostics while typing between saves.
        """
        self._open_texts[uri] = text
        if schedule_verible:
            if self._verible_bin:
                self._executor.submit(self._run_verible_tier, uri_to_path(uri), text, uri)
            else:
                self._schedule_lark_check(uri, text)

    def on_did_save(self, uri: str) -> None:
        """On save: check hash, run Verible, conditionally run file-tier parse."""
        path = uri_to_path(uri)
        text = self._open_texts.get(uri)
        if text is None:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return
        new_hash = hashlib.sha256(text.encode()).hexdigest()
        old_hash = self._file_hashes.get(path)
        if new_hash == old_hash:
            log.debug("save: unchanged hash for %s, skipping parse", path)
            return
        self._executor.submit(self._run_save_tier, path, text, uri, new_hash)

    def on_did_close(self, uri: str) -> None:
        self._open_texts.pop(uri, None)
        timer = self._lark_debounce.pop(uri, None)
        if timer is not None:
            timer.cancel()

    # ------------------------------------------------------------------
    # Full workspace parse (startup + explicit reparse)
    # ------------------------------------------------------------------

    def parse_workspace_async(self) -> None:
        """Trigger a full workspace parse in the background."""
        self._executor.submit(self._run_full_parse)

    # ------------------------------------------------------------------
    # Lark fallback syntax check (used when Verible is not installed)
    # ------------------------------------------------------------------

    def _lark_syntax_check(self, text: str) -> list[dict]:
        """Parse *text* with Lark; return a one-element LSP Diagnostic list on error.

        Returns an empty list when the file parses cleanly.  All Lark
        ``UnexpectedInput`` variants carry ``.line`` and ``.column`` attributes
        (1-indexed); other exceptions are swallowed and logged.
        """
        from lark.exceptions import UnexpectedInput

        try:
            _get_lark_parser().build_tree(text)
            return []
        except UnexpectedInput as exc:
            line = max(0, (getattr(exc, "line", 1) or 1) - 1)
            col = max(0, (getattr(exc, "column", 1) or 1) - 1)
            msg = str(exc).splitlines()[0]
            return [
                {
                    "range": {
                        "start": {"line": line, "character": col},
                        "end": {"line": line, "character": col + 1},
                    },
                    "severity": 1,
                    "source": "veriforge-lsp (lark)",
                    "code": "syntax-error",
                    "message": msg,
                }
            ]
        except Exception as exc:
            log.debug("Lark syntax check failed unexpectedly: %s", exc)
            return []

    def _schedule_lark_check(self, uri: str, text: str) -> None:
        """Debounce a Lark syntax check: cancel any pending timer and restart it."""
        old = self._lark_debounce.pop(uri, None)
        if old is not None:
            old.cancel()
        path = uri_to_path(uri)

        def _fire() -> None:
            self._lark_debounce.pop(uri, None)
            self._executor.submit(self._run_lark_tier, path, text, uri)

        t = threading.Timer(_LARK_DEBOUNCE, _fire)
        self._lark_debounce[uri] = t
        t.daemon = True
        t.start()

    def _run_lark_tier(self, path: str, text: str, uri: str) -> None:
        """Run a Lark syntax check and publish diagnostics (executor thread)."""
        diags = self._lark_syntax_check(text)
        has_error = bool(diags)
        with self._lock:
            self._verible_diags[path] = diags
            self._file_has_syntax_error[path] = has_error
        self._publish_diagnostics_for(path, uri)

    def _run_full_parse(self) -> None:  # noqa: PLR0912
        log.warning("veriforge-lsp: starting full workspace parse at %s", self.root)
        self._send_progress("workspace-parse", "begin", "Parsing workspace…", 0)
        try:
            verilog_files = self._collect_files()
            if not verilog_files:
                log.warning("veriforge-lsp: no Verilog files found under %s", self.root)
                self._send_progress("workspace-parse", "end", "No files found")
                return
            log.warning("veriforge-lsp: found %d files, parsing…", len(verilog_files))
            self._send_progress("workspace-parse", "report", f"Parsing {len(verilog_files)} files…")
            defines_dict: dict[str, str | None] = {}
            for d in self.defines:
                if "=" in d:
                    k, v = d.split("=", 1)
                    defines_dict[k] = v
                else:
                    defines_dict[d] = None
            import pathlib

            cache_dir = pathlib.Path.home() / ".cache" / "veriforge_lsp" / "parse"
            design = parse_directory(
                self.root,
                extensions=list(_VERILOG_EXTS),
                analyze=False,  # we call analyze_design below for full connectivity
                defines=defines_dict if defines_dict else None,
                cache_dir=cache_dir,
            )
            log.warning("veriforge-lsp: parsed %d modules, running analysis…", len(design.modules))
            analyze_design(design)
            index = LocationIndex()
            index.build(design)
            # Record hashes for all parsed files
            new_hashes: dict[str, str] = {}
            for fpath in verilog_files:
                try:
                    content = Path(fpath).read_text(encoding="utf-8", errors="replace")
                    new_hashes[fpath] = hashlib.sha256(content.encode()).hexdigest()
                except OSError:
                    pass
            # Build semantic diagnostics: use per-signal loc where available,
            # fall back to module declaration line.
            mod_by_name = {mod.name: mod for mod in design.modules}
            semantic: dict[str, list[dict]] = {}
            for warning in lint_design(design):
                mod = mod_by_name.get(warning.module)
                if mod is None or not mod.loc or not mod.loc.file:
                    continue
                # Try to find the declaration location for the specific signal
                loc = _signal_loc(mod, warning.signal) if warning.signal else None
                if loc is None or not loc.line:
                    loc = mod.loc  # fall back to module declaration line
                lsp_line = max(0, (loc.line or 1) - 1)
                lsp_file = loc.file or mod.loc.file
                semantic.setdefault(lsp_file, []).append(
                    {
                        "range": {
                            "start": {"line": lsp_line, "character": 0},
                            "end": {"line": lsp_line, "character": 0},
                        },
                        "severity": 2,
                        "source": "veriforge-lsp",
                        "code": warning.code.name,
                        "message": warning.message,
                    }
                )
            with self._lock:
                self._design = design
                self._index = index
                self._file_hashes.update(new_hashes)
                self._semantic_diags = semantic
                # Clear stale flags for files we just parsed
                for fpath in new_hashes:
                    self._file_is_stale.pop(fpath, None)
            log.warning("veriforge-lsp: parse complete, %d modules found", len(design.modules))
            self._send_progress("workspace-parse", "end", "Workspace parsed")
            self._publish_all_diagnostics()
            if self._on_parse_complete:
                log.warning("veriforge-lsp: calling on_parse_complete")
                try:
                    self._on_parse_complete()
                except Exception:
                    log.exception("on_parse_complete callback error")
            else:
                log.warning("veriforge-lsp: no on_parse_complete callback registered")
        except Exception:
            log.exception("Full workspace parse failed")
            self._send_progress("workspace-parse", "end", "Parse failed")

    def _run_verible_tier(self, path: str, text: str, uri: str) -> None:
        diags = self.run_verible(path, text)
        has_error = self._has_verible_syntax_error(diags)
        with self._lock:
            self._verible_diags[path] = diags
            self._file_has_syntax_error[path] = has_error
        self._publish_diagnostics_for(path, uri)

    def _run_save_tier(self, path: str, text: str, uri: str, new_hash: str) -> None:
        # Verible first
        diags = self.run_verible(path, text)
        has_error = self._has_verible_syntax_error(diags)
        with self._lock:
            self._verible_diags[path] = diags
            self._file_has_syntax_error[path] = has_error

        if has_error:
            # Can't parse — mark stale and publish Verible-only diagnostics
            with self._lock:
                self._file_is_stale[path] = True
            self._publish_diagnostics_for(path, uri)
            return

        # File-tier veriforge parse
        try:
            old_signature = self._module_signature(path)
            defines_dict: dict[str, str | None] = {}
            for d in self.defines:
                if "=" in d:
                    k, v = d.split("=", 1)
                    defines_dict[k] = v
                else:
                    defines_dict[d] = None
            file_design = parse_files(
                [path],
                analyze=False,  # just parse this file; full cross-file analysis deferred
                defines=defines_dict if defines_dict else None,
            )
            new_signature = _module_signature_from_design(file_design)
            interface_changed = new_signature != old_signature
            with self._lock:
                self._file_hashes[path] = new_hash
                self._file_is_stale.pop(path, None)
                # Merge new modules into existing design
                _merge_file_into_design(self._design, file_design, path)
            self._publish_diagnostics_for(path, uri)
            if interface_changed:
                log.debug("Interface changed for %s — scheduling full re-parse", path)
                self._run_full_parse()
            else:
                # Rebuild index for the affected file only
                self._executor.submit(self._rebuild_index)
        except Exception as e:
            log.debug("File-tier parse failed for %s: %s", path, e)
            if not self.verible_available:
                # Verible is absent: re-run Lark so the user gets a syntax
                # diagnostic on save even when the full parse fails.
                lark_diags = self._lark_syntax_check(text)
                has_lark_error = bool(lark_diags)
                with self._lock:
                    if lark_diags:
                        self._verible_diags[path] = lark_diags
                        self._file_has_syntax_error[path] = has_lark_error
                        self._file_is_stale[path] = True
            self._publish_diagnostics_for(path, uri)

    def _rebuild_index(self) -> None:
        with self._lock:
            design = self._design
        new_index = LocationIndex()
        new_index.build(design)
        with self._lock:
            self._index = new_index

    # ------------------------------------------------------------------
    # Design + index accessors (thread-safe snapshots)
    # ------------------------------------------------------------------

    @property
    def design(self) -> Design:
        with self._lock:
            return self._design

    @property
    def index(self) -> LocationIndex:
        with self._lock:
            return self._index

    def is_stale(self, path: str) -> bool:
        with self._lock:
            return self._file_is_stale.get(path, False)

    # ------------------------------------------------------------------
    # Diagnostics publishing
    # ------------------------------------------------------------------

    def register_diag_callback(self, cb: Any) -> None:
        self._diag_cb = cb

    def _publish_diagnostics_for(self, path: str, uri: str) -> None:
        with self._lock:
            verible = list(self._verible_diags.get(path, []))
            stale = self._file_is_stale.get(path, False)
            semantic = [] if stale else list(self._semantic_diags.get(path, []))
        if self._diag_cb:
            self._diag_cb(uri, verible + semantic)

    def _publish_all_diagnostics(self) -> None:
        if not self._diag_cb:
            return
        with self._lock:
            files = set(self._verible_diags) | set(self._semantic_diags)
        for path in files:
            uri = path_to_uri(path)
            self._publish_diagnostics_for(path, uri)

    # ------------------------------------------------------------------
    # Hierarchy helpers
    # ------------------------------------------------------------------

    def get_hierarchy_roots(self) -> list[Any]:
        """Return top-level modules for the hierarchy tree."""
        design = self.design
        pin = self.top_module
        if pin:
            modules = [m for m in design.modules if m.name == pin]
            log.warning("veriforge-lsp: hierarchy roots (pinned to %s): %d", pin, len(modules))
        else:
            modules = design.get_top_modules() if hasattr(design, "get_top_modules") else []
            if not modules:
                modules = _auto_top_modules(design)
            all_names = [m.name for m in design.modules]
            root_names = [m.name for m in modules]
            log.warning("veriforge-lsp: all modules=%s  hierarchy roots=%s", all_names, root_names)
        return modules

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _collect_files(self) -> list[str]:
        excludes = self._config.get("exclude_patterns", [])
        files: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for fname in filenames:
                if Path(fname).suffix in _VERILOG_EXTS:
                    full = os.path.join(dirpath, fname)
                    if not _is_excluded(full, self.root, excludes):
                        files.append(full)
        return files

    def _module_signature(self, path: str) -> dict:
        """Return a {module_name: (ports, params)} snapshot for change detection."""
        design = self.design
        sig: dict = {}
        for mod in design.modules:
            if mod.loc and mod.loc.file and os.path.normpath(mod.loc.file) == os.path.normpath(path):
                port_sig = tuple((p.name, str(p.direction)) for p in (mod.ports or []))
                param_sig = tuple(p.name for p in (mod.parameters or []))
                sig[mod.name] = (port_sig, param_sig)
        return sig

    def _send_progress(self, token: str, kind: str, message: str = "", percentage: int = 0) -> None:
        if self._progress_cb:
            value: dict = {"kind": kind}
            if message:
                value["message"] = message
            if kind == "begin":
                value["title"] = message
            if percentage:
                value["percentage"] = percentage
            try:
                self._progress_cb(token, kind, value)
            except Exception as e:
                log.debug("progress callback error: %s", e)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _module_signature_from_design(design: Design) -> dict:
    sig: dict = {}
    for mod in design.modules:
        port_sig = tuple((p.name, str(p.direction)) for p in (mod.ports or []))
        param_sig = tuple(p.name for p in (mod.parameters or []))
        sig[mod.name] = (port_sig, param_sig)
    return sig


def _merge_file_into_design(target: Design, source: Design, path: str) -> None:
    """Replace modules from *path* in *target* with those from *source*."""
    norm = os.path.normpath(path)
    target.modules = [m for m in target.modules if not (m.loc and m.loc.file and os.path.normpath(m.loc.file) == norm)]
    target.modules.extend(source.modules)


def _auto_top_modules(design: Design) -> list[Any]:
    """Modules that are never instantiated by another module in the design."""
    instantiated: set[str] = set()
    for mod in design.modules:
        for inst in mod.instances or []:
            instantiated.add(inst.module_name)
    return [m for m in design.modules if m.name not in instantiated]


def _signal_loc(mod: Any, signal_name: str) -> Any:
    """Return the SourceLocation for a named net/variable in *mod*, or None."""
    for net in mod.nets or []:
        if net.name == signal_name and net.loc and net.loc.line:
            return net.loc
    for var in mod.variables or []:
        if var.name == signal_name and var.loc and var.loc.line:
            return var.loc
    for port in mod.ports or []:
        if port.name == signal_name and port.loc and port.loc.line:
            return port.loc
    return None


def _is_excluded(full_path: str, root: str, patterns: list[str]) -> bool:
    import fnmatch

    rel = os.path.relpath(full_path, root).replace("\\", "/")
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)
