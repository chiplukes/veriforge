# test_README.md

Strategy:
* print out tree using gentree.  Pick out the largest sections that I don't want to support and mark those in the lark file.  Then re-print out the tree and change color of sections that are not supported (red?)
* do the same thing, but mark sections as TEST that I want to create tests for. Mark these as (blue?)
* keep doing this until the tree looks complete.
* auto generate test files with the structure below (WIP) (will likely need manual intervention to write the verilog sections)
    * pass top into test function

# Sample minimal Verilog code for testing

``` python

SIMPLE_VERILOG = """
module test(input wire a, output wire b);
    assign b = a;
endmodule
"""

INVALID_VERILOG = """
module test(input wire a, output wire b)
    assign b = a;
endmodule
"""
import subprocess
import sys
from pathlib import Path
import tempfile

import pytest

def test_cli_parsing_success():
        # Create a temporary Verilog file
        with tempfile.NamedTemporaryFile("w", suffix=".v", delete=False) as f:
                f.write(SIMPLE_VERILOG)
                vfile = f.name

        # Run the CLI as described in the README
        result = subprocess.run(
                [sys.executable, "-m", "veriforge", "-tree", "-f", vfile],
                capture_output=True,
                text=True,
        )
        Path(vfile).unlink()
        assert result.returncode == 0
        assert "module" in result.stdout.lower()

def test_cli_parsing_failure():
        # Create a temporary invalid Verilog file
        with tempfile.NamedTemporaryFile("w", suffix=".v", delete=False) as f:
                f.write(INVALID_VERILOG)
                vfile = f.name

        # Run the CLI and expect a nonzero exit code or error in output
        result = subprocess.run(
                [sys.executable, "-m", "veriforge", "-tree", "-f", vfile],
                capture_output=True,
                text=True,
        )
        Path(vfile).unlink()
        assert result.returncode != 0 or "error" in result.stderr.lower()

def test_api_build_tree():
        # Test the Python API directly
        from veriforge import build_tree
        tree = build_tree(text=SIMPLE_VERILOG, transformer=None, parser="earley", start="verilog", debug=False)
        assert tree is not None
        assert hasattr(tree, "children") or hasattr(tree, "pretty")

def test_precommit_hook_install():
        # Check that pre-commit can be installed (dry run)
        result = subprocess.run(
                ["pre-commit", "install", "--help"],
                capture_output=True,
                text=True,
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower()
