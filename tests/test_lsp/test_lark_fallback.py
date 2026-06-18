"""Tests for the Lark syntax fallback in Workspace (when Verible is absent)."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("pygls")

from veriforge_lsp.protocol import path_to_uri
from veriforge_lsp.workspace import Workspace

_GOOD_V = """\
module counter(
    input clk,
    input rst,
    output reg [7:0] count
);
    always @(posedge clk) begin
        if (rst) count <= 0;
        else count <= count + 1;
    end
endmodule
"""

_BAD_V = """\
module broken(
    input clk
    output reg [7:0] count  // missing semicolon after clk
);
endmodule
"""


@pytest.fixture(scope="module")
def no_verible_ws(tmp_path_factory):
    """Workspace with Verible explicitly disabled."""
    root = tmp_path_factory.mktemp("lark_ws")
    ws = Workspace(str(root))
    ws._verible_bin = None  # simulate Verible absent
    return ws


class TestLarkSyntaxCheck:
    def test_clean_file_returns_empty(self, no_verible_ws):
        diags = no_verible_ws._lark_syntax_check(_GOOD_V)
        assert diags == []

    def test_broken_file_returns_diagnostic(self, no_verible_ws):
        diags = no_verible_ws._lark_syntax_check(_BAD_V)
        assert len(diags) == 1
        d = diags[0]
        assert d["severity"] == 1
        assert d["source"] == "veriforge-lsp (lark)"
        assert d["code"] == "syntax-error"
        assert "line" in d["range"]["start"]

    def test_diagnostic_line_is_zero_indexed(self, no_verible_ws):
        diags = no_verible_ws._lark_syntax_check(_BAD_V)
        assert diags[0]["range"]["start"]["line"] >= 0

    def test_empty_string_returns_empty_or_diagnostic(self, no_verible_ws):
        # Empty input either parses cleanly (empty file) or reports an error —
        # both are acceptable; it must not raise.
        diags = no_verible_ws._lark_syntax_check("")
        assert isinstance(diags, list)


class TestLarkTierIntegration:
    def test_on_did_change_no_verible_schedules_lark(self, tmp_path, no_verible_ws):
        """on_did_change with Verible absent must schedule a Lark debounce timer."""
        uri = path_to_uri(str(tmp_path / "x.v"))
        no_verible_ws.on_did_change(uri, _GOOD_V, schedule_verible=True)
        assert uri in no_verible_ws._lark_debounce
        # Cleanup
        t = no_verible_ws._lark_debounce.pop(uri, None)
        if t:
            t.cancel()

    def test_on_did_close_cancels_pending_timer(self, tmp_path, no_verible_ws):
        uri = path_to_uri(str(tmp_path / "y.v"))
        no_verible_ws.on_did_change(uri, _GOOD_V, schedule_verible=True)
        assert uri in no_verible_ws._lark_debounce
        no_verible_ws.on_did_close(uri)
        assert uri not in no_verible_ws._lark_debounce

    def test_run_lark_tier_sets_diags_and_error_flag(self, tmp_path, no_verible_ws):
        """_run_lark_tier stores diagnostics and marks the file as having a syntax error."""
        path = str(tmp_path / "broken.v")
        uri = path_to_uri(path)
        published: list[tuple] = []
        no_verible_ws.register_diag_callback(lambda u, d: published.append((u, d)))

        no_verible_ws._run_lark_tier(path, _BAD_V, uri)

        assert no_verible_ws._file_has_syntax_error.get(path) is True
        assert no_verible_ws._verible_diags.get(path)
        assert published and published[-1][0] == uri

    def test_run_lark_tier_clean_clears_error_flag(self, tmp_path, no_verible_ws):
        path = str(tmp_path / "good.v")
        uri = path_to_uri(path)
        no_verible_ws.register_diag_callback(lambda u, d: None)

        no_verible_ws._run_lark_tier(path, _GOOD_V, uri)

        assert no_verible_ws._file_has_syntax_error.get(path) is False
        assert no_verible_ws._verible_diags.get(path) == []

    def test_lark_fires_after_debounce(self, tmp_path):
        """The Lark check actually runs after the debounce delay."""
        import threading as _t

        root = str(tmp_path)
        ws = Workspace(root)
        ws._verible_bin = None
        published: list[list] = []
        ws.register_diag_callback(lambda u, d: published.append(d))

        v_path = tmp_path / "f.v"
        v_path.write_text(_BAD_V, encoding="utf-8")
        uri = path_to_uri(str(v_path))
        ws.on_did_open(uri, _BAD_V)
        ws.on_did_change(uri, _BAD_V, schedule_verible=True)

        # Wait up to 3 s for the debounce timer to fire and the executor to run.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if published:
                break
            time.sleep(0.05)

        ws.shutdown()
        assert published, "Lark check never published diagnostics within 3 s"
        # The broken file must have produced at least one diagnostic
        assert any(d for d in published)
