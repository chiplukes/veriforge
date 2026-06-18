"""
Pytest configuration and fixtures for veriforge tests.

This module provides fixtures for testing the Verilog parser across
different grammar sections and rule types.
"""

import json
import logging
import os
import shutil
from pathlib import Path

import pytest

from veriforge.verilog_parser import verilog_parser

log = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _compile_cache_per_test(tmp_path, monkeypatch):
    """Redirect the compiled-engine cache to a per-test temp dir and clean up
    build artifacts (C files, object files) after each test.

    This prevents .cycache from growing to tens of GB during a full regression.
    On Windows, loaded .pyd files cannot be deleted mid-process; they remain in
    the pytest temp tree until the next run's cleanup.  Everything else
    (generated C source, obj files, setup.py) is deleted immediately.

    If VERILOG_TOOLS_COMPILE_CACHE is already set in the environment the
    persistent cache is used unchanged — developers who want cross-run caching
    can set that variable in their shell profile.
    """
    if os.environ.get("VERILOG_TOOLS_COMPILE_CACHE"):
        yield
        return

    cache_dir = str(tmp_path / "compile_cache")
    monkeypatch.setenv("VERILOG_TOOLS_COMPILE_CACHE", cache_dir)
    yield
    try:
        from veriforge.sim.compiled.compiler import CythonCompiler

        CythonCompiler(cache_dir=cache_dir).clear_cache()
    except Exception:
        pass


# Paths
TESTS_DIR = Path(__file__).parent
VERILOG_DIR = TESTS_DIR / "test_verilog_parser" / "verilog"
LARK_DIR = Path(__file__).parent.parent / "src" / "veriforge" / "lark_file"
DOCS_DIR = Path(__file__).parent.parent / "docs"


# ============================================================================
# Parser Fixtures
# ============================================================================


@pytest.fixture
def parser():
    """Create a full Verilog parser starting at 'verilog' rule."""
    return verilog_parser(start="verilog")


@pytest.fixture
def module_parser():
    """Create a parser starting at 'module_declaration' rule."""
    return verilog_parser(start="module_declaration")


@pytest.fixture
def expression_parser():
    """Create a parser starting at 'expression' rule."""
    return verilog_parser(start="expression")


@pytest.fixture
def statement_parser():
    """Create a parser starting at 'statement' rule."""
    return verilog_parser(start="statement")


# ============================================================================
# Parametrized Fixtures - Create parsers for specific rules
# ============================================================================


def parser_for_rule(rule_name: str):
    """Factory to create a parser for a specific grammar rule."""
    return verilog_parser(start=rule_name)


# ============================================================================
# Metadata Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def grammar_metadata():
    """Load grammar metadata from parse_metadata tool."""
    # Try to import the parser
    try:
        from veriforge.lark_file.parse_metadata import GrammarMetadataParser

        gmp = GrammarMetadataParser()
        return gmp.parse()
    except ImportError:
        pytest.skip("parse_metadata module not available")


@pytest.fixture(scope="session")
def grammar_deps():
    """Load grammar dependency map from JSON if available."""
    deps_file = DOCS_DIR / "grammar_deps.json"
    if deps_file.exists():
        with open(deps_file, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================================
# Section Fixtures - Rules organized by grammar section
# ============================================================================


@pytest.fixture(scope="session")
def high_priority_rules(grammar_metadata):
    """Get all HIGH priority rules."""
    return {name: rule for name, rule in grammar_metadata.items() if rule.priority == "HIGH" and not rule.is_terminal}


@pytest.fixture(scope="session")
def synthesizable_rules(grammar_metadata):
    """Get all synthesizable rules."""
    return {
        name: rule for name, rule in grammar_metadata.items() if rule.synthesizable == "YES" and not rule.is_terminal
    }


@pytest.fixture(scope="session")
def rules_with_examples(grammar_metadata):
    """Get all rules that have EXAMPLE tags."""
    return {name: rule for name, rule in grammar_metadata.items() if rule.example and not rule.is_terminal}


def get_section_rules(grammar_metadata, section_prefix: str):
    """Get rules for a specific grammar section."""
    return {
        name: rule
        for name, rule in grammar_metadata.items()
        if rule.section.startswith(section_prefix) and not rule.is_terminal
    }


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def simple_module_code():
    """Simple module for basic parsing tests."""
    return "module test(); endmodule"


@pytest.fixture
def module_with_ports():
    """Module with port list."""
    return "module foo(a, b, c); input a; output b, c; endmodule"


@pytest.fixture
def module_with_params():
    """Module with parameters."""
    return "module bar #(parameter WIDTH=8) (input [WIDTH-1:0] data); endmodule"


@pytest.fixture
def always_block_code():
    """Simple always block."""
    return "always @(posedge clk) q <= d;"


@pytest.fixture
def assign_statement_code():
    """Continuous assignment."""
    return "assign y = a & b;"


# ============================================================================
# Verilog File Fixtures
# ============================================================================


@pytest.fixture
def v_module1_path():
    """Path to v_module1.v test file."""
    return VERILOG_DIR / "v_module1.v"


@pytest.fixture
def verilog_all_path():
    """Path to verilog_all.v test file."""
    return VERILOG_DIR / "verilog_all.v"


# ============================================================================
# Test Helpers
# ============================================================================


class ParseHelper:
    """Helper class for parsing tests."""

    def __init__(self, start_rule: str = "verilog"):
        self._parser = None
        self._start_rule = start_rule

    @property
    def parser(self):
        if self._parser is None:
            self._parser = verilog_parser(start=self._start_rule)
        return self._parser

    def parse(self, code: str):
        """Parse code and return tree."""
        return self.parser.build_tree(code)

    def can_parse(self, code: str) -> bool:
        """Check if code can be parsed without error."""
        try:
            tree = self.parse(code)
            return tree is not None
        except Exception:
            return False


@pytest.fixture
def parse_helper():
    """Get ParseHelper factory."""
    return ParseHelper


# ============================================================================
# Markers
# ============================================================================


def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "grammar: marks tests for grammar rules")
    config.addinivalue_line("markers", "section_a1: marks tests for Section A.1 (Source text)")
    config.addinivalue_line("markers", "section_a2: marks tests for Section A.2 (Declarations)")
    config.addinivalue_line("markers", "section_a6: marks tests for Section A.6 (Behavioral)")
    config.addinivalue_line("markers", "section_a8: marks tests for Section A.8 (Expressions)")
    config.addinivalue_line("markers", "synthesizable: marks tests for synthesizable constructs")


def pytest_addoption(parser):
    """Register custom CLI options."""
    parser.addoption(
        "--clear-cython-cache",
        action="store_true",
        default=False,
        help="Delete all cached Cython compiled extensions before running tests.",
    )
    parser.addoption(
        "--vcd-dir",
        action="store",
        default=None,
        metavar="DIR",
        help="Write simulator VCD outputs for tests that support tracing into DIR.",
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Include tests marked @pytest.mark.slow (e.g. TestWideSignalExternalIO, ~3800 tests).",
    )


def pytest_collection_modifyitems(config, items):
    """Skip slow-marked tests unless --run-slow is passed."""
    if config.getoption("--run-slow", default=False):
        return
    skip_slow = pytest.mark.skip(reason="slow test — pass --run-slow to include")
    for item in items:
        if item.get_closest_marker("slow"):
            item.add_marker(skip_slow)


@pytest.fixture
def vcd_dir(request):
    """Optional output directory for test-generated VCD traces."""
    configured = request.config.getoption("--vcd-dir", default=None)
    if not configured:
        return None
    output_dir = Path(configured)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def pytest_sessionstart(session):
    """Optionally clear the Cython compiled-simulation cache.

    The cache uses a content-hash + version key, so stale entries are
    automatically bypassed (and retried on load failure).  Use
    ``--clear-cython-cache`` to force a full cache wipe when needed.
    """
    if session.config.getoption("--clear-cython-cache", default=False):
        from veriforge.sim.compiled.compiler import CythonCompiler

        cache_dir = Path(CythonCompiler().cache_dir)
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            log.info("Cleared compiled sim cache: %s", cache_dir)

    # Warn about stale .pyd/.so files that shadow pure-Python sources.
    # These compiled extensions take import priority over .py files,
    # silently ignoring any edits to the Python source.
    src_root = Path(__file__).parent.parent / "src"
    stale = []
    for ext in src_root.rglob("*.pyd"):
        py = ext.with_suffix(".py")
        pyx = ext.with_suffix(".pyx")
        source = pyx if pyx.exists() else (py if py.exists() else None)
        if source is not None and source.stat().st_mtime > ext.stat().st_mtime:
            stale.append(ext)
    if stale:
        import warnings

        msg = (
            "Stale compiled extensions found (source is newer than .pyd).\n"
            "These will shadow your Python edits! Delete them or rebuild:\n" + "\n".join(f"  {p}" for p in stale)
        )
        warnings.warn(msg, stacklevel=1)
