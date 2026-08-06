#!/usr/bin/env python3
"""Add SUPPORT: YES/NO metadata tags to verilog.lark for all grammar rules
that lack them. Terminal (lexer) rules are always skipped.

Uses section-based heuristics and a NO override list for constructs the
reference simulator cannot execute (gates, UDPs, specify blocks, force/release,
real types, event declarations, config/library text, drive strengths, includes,
DPI).

Usage:  uv run python tools/add_support_tags.py [--dry-run]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LARK_FILE = Path(__file__).parent.parent / "src" / "veriforge" / "lark_file" / "verilog.lark"

# ---------------------------------------------------------------------------
# Rules the reference simulator CANNOT execute
# ---------------------------------------------------------------------------
UNSUPPORTED_RULES: set[str] = {
    # A.1.1 Library source text
    "library_declaration",
    "library_description",
    "include_statement",
    # A.1.5 Configuration source text
    "design_statement",
    "config_rule_statement",
    "default_clause",
    "inst_clause",
    "inst_name",
    "cell_clause",
    "liblist_clause",
    "use_clause",
    # A.2.2.2 Strengths
    "drive_strength",
    "strength0",
    "strength1",
    "charge_strength",
    # A.2.2.3 Delays (specify-oriented delay specs)
    "delay3",
    "delay2",
    "delay_value",
    # A.2.1.1 / A.2.4 — specparams
    "specparam_declaration",
    "specparam_assignment",
    "pulse_control_specparam",
    "error_limit_value",
    "reject_limit_value",
    "limit_value",
    "list_of_specparam_assignments",
    # A.2.1.3 — unsupported type declarations (real, event, time)
    "event_declaration",
    "real_declaration",
    "realtime_declaration",
    "time_declaration",
    # A.2.2.1 — real_type
    "real_type",
    # A.2.3 — unsupported decl lists
    "list_of_event_identifiers",
    "list_of_real_identifiers",
    # A.2.8 — unsupported block decl lists
    "list_of_block_real_identifiers",
    "block_real_type",
    # A.1.4 module items — DPI import/export
    "dpi_import_export",
    # A.6.2 — procedural continuous assignments (force/release)
    "procedural_continuous_assignments",
    # A.8.7 — real numbers
    "real_number",
    "exp",
    # A.8.2 — system function calls limited
    "constant_system_function_call",
    # A.8.3 — mintypmax expressions (timing)
    "mintypmax_expression",
    "constant_mintypmax_expression",
    # ===== A.3.x — gate primitives =====
    "cmos_switch_instance",
    "enable_gate_instance",
    "mos_switch_instance",
    "n_input_gate_instance",
    "n_output_gate_instance",
    "pass_switch_instance",
    "pass_enable_switch_instance",
    "pull_gate_instance",
    "name_of_gate_instance",
    "pulldown_strength",
    "pullup_strength",
    "enable_terminal",
    "inout_terminal",
    "input_terminal",
    "ncontrol_terminal",
    "output_terminal",
    "pcontrol_terminal",
    "cmos_switchtype",
    "enable_gatetype",
    "mos_switchtype",
    "n_input_gatetype",
    "n_output_gatetype",
    "pass_en_switchtype",
    "pass_switchtype",
    # ===== A.5.x — UDP primitives =====
    "udp_port_list",
    "udp_declaration_port_list",
    "udp_port_declaration",
    "udp_output_declaration",
    "udp_input_declaration",
    "udp_reg_declaration",
    "udp_body",
    "combinational_body",
    "combinational_entry",
    "sequential_body",
    "udp_initial_statement",
    "init_val",
    "sequential_entry",
    "seq_input_list",
    "level_input_list",
    "edge_input_list",
    "edge_indicator",
    "current_state",
    "next_state",
    "output_symbol",
    "level_symbol",
    "edge_symbol",
    "udp_instantiation",
    "udp_instance",
    "name_of_udp_instance",
    # ===== A.7.x — specify blocks and timing =====
    "specify_block",
    "specify_item",
    "pulsestyle_declaration",
    "showcancelled_declaration",
    "path_declaration",
    "simple_path_declaration",
    "parallel_path_description",
    "full_path_description",
    "list_of_path_inputs",
    "list_of_path_outputs",
    "specify_input_terminal_descriptor",
    "specify_output_terminal_descriptor",
    "input_identifier",
    "output_identifier",
    "path_delay_value",
    "list_of_path_delay_expressions",
    "t_path_delay_expression",
    "trise_path_delay_expression",
    "tfall_path_delay_expression",
    "tz_path_delay_expression",
    "t01_path_delay_expression",
    "t10_path_delay_expression",
    "t0z_path_delay_expression",
    "tz1_path_delay_expression",
    "t1z_path_delay_expression",
    "tz0_path_delay_expression",
    "t0x_path_delay_expression",
    "tx1_path_delay_expression",
    "t1x_path_delay_expression",
    "tx0_path_delay_expression",
    "txz_path_delay_expression",
    "tzx_path_delay_expression",
    "path_delay_expression",
    "edge_sensitive_path_declaration",
    "parallel_edge_sensitive_path_description",
    "full_edge_sensitive_path_description",
    "data_source_expression",
    "edge_identifier",
    "state_dependent_path_declaration",
    "polarity_operator",
    "system_timing_check",
    "setup_timing_check",
    "hold_timing_check",
    "setuphold_timing_check",
    "recovery_timing_check",
    "removal_timing_check",
    "recrem_timing_check",
    "skew_timing_check",
    "timeskew_timing_check",
    "fullskew_timing_check",
    "period_timing_check",
    "width_timing_check",
    "nochange_timing_check",
    "checktime_condition",
    "controlled_reference_event",
    "data_event",
    "delayed_data",
    "delayed_reference",
    "end_edge_offset",
    "event_based_flag",
    "notifier",
    "reference_event",
    "remain_active_flag",
    "stamptime_condition",
    "start_edge_offset",
    "threshold",
    "timing_check_limit",
    "timing_check_event",
    "controlled_timing_check_event",
    "timing_check_event_control",
    "specify_terminal_descriptor",
    "edge_control_specifier",
    "edge_descriptor",
    "zero_or_one",
    "z_or_x",
    "timing_check_condition",
    "scalar_timing_check_condition",
    "scalar_constant",
    # A.8.1 — specify module-path concat/replication
    "module_path_concatenation",
    "module_path_multiple_concatenation",
    # A.8.3 — specify module-path expressions
    "module_path_conditional_expression",
    "module_path_expression",
    "module_path_mintypmax_expression",
    # A.8.4 — specify module-path primary
    "module_path_primary",
    # A.8.6 — specify module-path operators
    "unary_module_path_operator",
    "binary_module_path_operator",
}

# Rules that already have explicit SUPPORT tags — don't touch these
KNOWN_SUPPORTED: set[str] = {
    "verilog",
    "source_text",
    "description",
    "module_declaration",
    "udp_declaration",
    "library_text",
    "config_declaration",
    "gate_instantiation",
}

RULE_PATTERN = re.compile(r"^([a-z_][a-z0-9_]*)\s*:")
TERMINAL_PATTERN = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*:")
SUPPORT_PATTERN = re.compile(r"//\s*SUPPORT:")


def _is_skippable(name: str) -> bool:
    return name.startswith("KW_") or name.startswith("OP_") or name.startswith("CH")


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    with open(LARK_FILE, encoding="utf-8") as f:
        lines = f.readlines()

    # ---- Phase 1: scan existing SUPPORT tags ----
    rule_existing_support: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = SUPPORT_PATTERN.search(line)
        if not m:
            continue
        support_val = "YES" if "YES" in line else "NO"
        # Walk forward from this comment to find the next rule/terminal definition
        for j in range(i + 1, min(i + 20, len(lines))):
            m2 = RULE_PATTERN.match(lines[j]) or TERMINAL_PATTERN.match(lines[j])
            if m2:
                name = m2.group(1)
                if not _is_skippable(name):
                    rule_existing_support[name] = support_val
                break

    # ---- Phase 2: build the output, inserting SUPPORT tags ----
    result: list[str] = []
    modified = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        m = RULE_PATTERN.match(line) or TERMINAL_PATTERN.match(line)
        if m:
            name = m.group(1)

            if _is_skippable(name):
                result.append(line)
                i += 1
                continue

            is_terminal = TERMINAL_PATTERN.match(line) is not None

            # Determine if we need to insert a SUPPORT comment
            needs_tag = not is_terminal and name not in rule_existing_support and name not in KNOWN_SUPPORTED

            if needs_tag:
                support_val = "NO" if name in UNSUPPORTED_RULES else "YES"

                # Find the last metadata comment line *before* this rule
                # to insert the SUPPORT tag in the right position.
                # We have to look at what we've already output to 'result'.
                insert_idx = len(result)
                # Walk backward through already-inserted lines (which are
                # from lines[0:i]) to find the last metadata comment.
                # We want to insert just before the rule line, which is
                # at index insert_idx in result right now.
                for j in range(i - 1, max(i - 30, -1), -1):
                    prev = lines[j].strip()
                    if prev.startswith("//") and any(
                        kw in prev
                        for kw in ("BNF:", "SV:", "PRIORITY:", "SYNTHESIZABLE:", "EXAMPLE:", "SECTION:", "DEPS:")
                    ):
                        # We found the metadata block for this rule.
                        # Insert the SUPPORT line right after it in the result.
                        # Since we've already inserted lines[0:j+1] into result,
                        # we need to find where line j is in the result.
                        # Insert right after the comment line.
                        for ri in range(len(result) - 1, -1, -1):
                            if result[ri] == lines[j]:
                                result.insert(ri + 1, f"// SUPPORT: {support_val}\n")
                                modified += 1
                                break
                        break
                else:
                    # No metadata comment found — insert right before the rule line
                    result.insert(insert_idx, f"// SUPPORT: {support_val}\n")
                    modified += 1

            result.append(line)
        else:
            result.append(line)

        i += 1

    if dry_run:
        print(f"Would add SUPPORT tags for {modified} grammar rules")
        print("Use without --dry-run to apply changes")
        return

    with open(LARK_FILE, "w", encoding="utf-8") as f:
        f.writelines(result)
    print(f"Added SUPPORT tags for {modified} grammar rules")

    # ---- Verify ----
    from veriforge.lark_file.parse_metadata import GrammarMetadataParser

    gmp = GrammarMetadataParser()
    gmp.parse()
    stats = gmp.get_statistics()
    print(f"Total rules: {stats['total_rules']}")
    print(f"SUPPORT YES: {stats['by_support']['YES']}")
    print(f"SUPPORT NO:  {stats['by_support']['NO']}")
    print(f"SUPPORT unset: {stats['by_support']['unset']}")

    if stats["by_support"]["unset"] > 0:
        unset = sorted(
            [(n, r.line_number) for n, r in gmp.rules.items() if not r.is_terminal and not r.support],
            key=lambda x: x[1],
        )
        print(f"\n{len(unset)} rules still unset:")
        for name, ln in unset:
            print(f"  line {ln:5d}  {name}")


if __name__ == "__main__":
    main()
