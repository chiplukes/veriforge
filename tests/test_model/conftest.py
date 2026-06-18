"""Shared fixtures for model tests."""

import pytest

from veriforge.verilog_parser import verilog_parser


@pytest.fixture
def parser():
    """A module_declaration parser."""
    return verilog_parser(start="module_declaration")


@pytest.fixture
def verilog_parser_full():
    """A full verilog parser (start='verilog')."""
    return verilog_parser(start="verilog")
