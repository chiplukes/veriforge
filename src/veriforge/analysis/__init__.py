"""Analysis passes for the Verilog semantic model (Layer 3).

Usage:
    from veriforge.analysis import analyze_design

    design = tree_to_design(tree)
    analyze_design(design)  # mutates design in-place

    # After analysis, cross-references are populated:
    # - Identifier.resolved → Port / Net / Variable / Parameter
    # - Instance.resolved_module → Module
    # - PortConnection.resolved_port → Port
    # - Net.drivers / Net.loads → list[Driver] / list[Load]
    # - Variable.drivers / Variable.loads → list[Driver] / list[Load]
"""

from .clock_reset import (
    ClockResetInfo,
    ClockSignal,
    ResetSignal,
    extract_clocks_resets,
    extract_clocks_resets_from_design,
    extract_clocks_resets_hier,
)
from .const_fold import const_fold, const_int, const_range_width, fold_constants, fold_constants_in_module
from .lint import LintCode, LintWarning, lint_design, lint_module
from .resolver import (
    Driver,
    Load,
    analyze_connectivity,
    analyze_design,
    link_instances,
    resolve_names,
    resolve_port_connections,
)
from .width_inference import infer_expr_width, infer_widths, infer_widths_in_module

__all__ = [
    "ClockResetInfo",
    "ClockSignal",
    "Driver",
    "LintCode",
    "LintWarning",
    "Load",
    "ResetSignal",
    "analyze_connectivity",
    "analyze_design",
    "const_fold",
    "const_int",
    "const_range_width",
    "extract_clocks_resets",
    "extract_clocks_resets_from_design",
    "extract_clocks_resets_hier",
    "fold_constants",
    "fold_constants_in_module",
    "infer_expr_width",
    "infer_widths",
    "infer_widths_in_module",
    "link_instances",
    "lint_design",
    "lint_module",
    "resolve_names",
    "resolve_port_connections",
]
