"""
parse_metadata.py - Extract metadata from verilog.lark for automated doc/test generation.

This module parses the verilog.lark grammar file and extracts metadata tags including:
- SECTION: Grammar section identifier (e.g., "A.1.1 Library source text")
- BNF: Original BNF from IEEE 1364-2005 specification
- PRIORITY: Rule importance (HIGH, MEDIUM, LOW)
- SYNTHESIZABLE: Whether the construct is synthesizable (YES, NO, PARTIAL)
- EXAMPLE: Short Verilog code example for the rule
- SUPPORT: Whether the rule is currently supported (YES, NO)
- DEPS: Rule dependencies (auto-generated)

Usage:
    python -m veriforge.lark_file.parse_metadata [options]

    Options:
        --table       Generate markdown support table (default)
        --json        Output as JSON
        --deps        Compute and display rule dependencies
        --stats       Show statistics summary
        --section X   Filter by section prefix (e.g., "A.1", "A.8")
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class RuleMetadata:
    """Metadata for a single grammar rule."""

    name: str
    line_number: int
    section: str = ""
    bnf: str = ""
    priority: Literal["HIGH", "MEDIUM", "LOW", ""] = ""
    synthesizable: Literal["YES", "NO", "PARTIAL", ""] = ""
    example: str = ""
    examples: list[str] = field(default_factory=list)
    support: Literal["YES", "NO", ""] = ""
    children: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    is_terminal: bool = False


class GrammarMetadataParser:
    """Parser for extracting metadata from verilog.lark grammar file."""

    # Regex patterns for metadata extraction
    SECTION_PATTERN = re.compile(r"//\s*SECTION:\s*(.+)")
    BNF_PATTERN = re.compile(r"//\s*(?:BNF|SV):\s*(.+)")
    PRIORITY_PATTERN = re.compile(r"//\s*PRIORITY:\s*(HIGH|MEDIUM|LOW)")
    SYNTHESIZABLE_PATTERN = re.compile(r"//\s*SYNTHESIZABLE:\s*(YES|NO|PARTIAL)")
    EXAMPLE_PATTERN = re.compile(r"//\s*EXAMPLE:\s*(.+)")
    SUPPORT_PATTERN = re.compile(r"//\s*SUPPORT:\s*(YES|NO)")
    RULE_PATTERN = re.compile(r"^([a-z_][a-z0-9_]*)\s*:")
    TERMINAL_PATTERN = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*:")

    def __init__(self, lark_file: Path | str | None = None):
        """Initialize parser with path to verilog.lark file."""
        if lark_file is None:
            lark_file = Path(__file__).parent / "verilog.lark"
        self.lark_file = Path(lark_file)
        self.rules: dict[str, RuleMetadata] = {}
        self.sections: list[str] = []

    def parse(self) -> dict[str, RuleMetadata]:
        """Parse the grammar file and extract all metadata."""
        with open(self.lark_file, encoding="utf-8") as f:
            lines = f.readlines()

        current_section = ""
        current_bnf_lines: list[str] = []
        current_priority = ""
        current_synthesizable = ""
        current_examples: list[str] = []
        current_support = ""
        in_bnf = False

        for line_num, line in enumerate(lines, start=1):
            line = line.rstrip()

            # Check for section header
            section_match = self.SECTION_PATTERN.search(line)
            if section_match:
                section_name = section_match.group(1).strip()
                current_section = section_name
                if section_name not in self.sections:
                    self.sections.append(section_name)
                continue

            # Check for BNF comment (may span multiple lines)
            bnf_match = self.BNF_PATTERN.search(line)
            if bnf_match:
                if in_bnf:
                    # Continuation of previous BNF
                    current_bnf_lines.append(bnf_match.group(1).strip())
                else:
                    # New BNF block
                    current_bnf_lines = [bnf_match.group(1).strip()]
                    in_bnf = True
                continue
            elif (
                in_bnf
                and line.startswith("//")
                and not any(
                    p.search(line)
                    for p in [
                        self.PRIORITY_PATTERN,
                        self.SYNTHESIZABLE_PATTERN,
                        self.EXAMPLE_PATTERN,
                        self.SUPPORT_PATTERN,
                        self.SECTION_PATTERN,
                    ]
                )
            ):
                # Continuation of BNF on multiple lines
                bnf_continuation = line.lstrip("/").strip()
                if bnf_continuation:
                    current_bnf_lines.append(bnf_continuation)
                continue
            else:
                in_bnf = False

            # Check for other metadata
            priority_match = self.PRIORITY_PATTERN.search(line)
            if priority_match:
                current_priority = priority_match.group(1)
                continue

            synthesizable_match = self.SYNTHESIZABLE_PATTERN.search(line)
            if synthesizable_match:
                current_synthesizable = synthesizable_match.group(1)
                continue

            example_match = self.EXAMPLE_PATTERN.search(line)
            if example_match:
                current_examples.append(example_match.group(1).strip())
                continue

            support_match = self.SUPPORT_PATTERN.search(line)
            if support_match:
                current_support = support_match.group(1)
                continue

            # Check for rule definition
            rule_match = self.RULE_PATTERN.match(line)
            terminal_match = self.TERMINAL_PATTERN.match(line)

            if rule_match or terminal_match:
                is_terminal = terminal_match is not None
                name = (terminal_match or rule_match).group(1)

                # Skip keywords
                if name.startswith("KW_") or name.startswith("OP_") or name.startswith("CH"):
                    # Reset metadata for next rule
                    current_bnf_lines = []
                    current_priority = ""
                    current_synthesizable = ""
                    current_examples = []
                    current_support = ""
                    continue

                rule = RuleMetadata(
                    name=name,
                    line_number=line_num,
                    section=current_section,
                    bnf=" ".join(current_bnf_lines),
                    priority=current_priority,
                    synthesizable=current_synthesizable,
                    example=current_examples[0] if current_examples else "",
                    examples=list(current_examples),
                    support=current_support,
                    is_terminal=is_terminal,
                )

                # Extract children from rule definition
                if not is_terminal:
                    rule.children = self._extract_children(line, lines, line_num - 1)

                self.rules[name] = rule

                # Reset metadata for next rule
                current_bnf_lines = []
                current_priority = ""
                current_synthesizable = ""
                current_examples = []
                current_support = ""

        # Compute parent relationships
        self._compute_parents()

        return self.rules

    def _extract_children(self, first_line: str, all_lines: list[str], start_idx: int) -> list[str]:
        """Extract child rule references from a rule definition."""
        children = set()

        # Combine the rule definition lines (may span multiple lines)
        rule_lines = [first_line.split(":", 1)[1] if ":" in first_line else ""]
        idx = start_idx + 1
        while idx < len(all_lines):
            line = all_lines[idx].strip()
            if not line or line.startswith("//") or self.RULE_PATTERN.match(line) or self.TERMINAL_PATTERN.match(line):
                break
            if line.startswith("|"):
                rule_lines.append(line)
            idx += 1

        rule_text = " ".join(rule_lines)

        # Remove quoted strings and special characters
        rule_text = re.sub(r'"[^"]*"', " ", rule_text)
        rule_text = re.sub(r"[()[\]*?|,.]", " ", rule_text)

        # Find identifiers (both lowercase rules and UPPERCASE terminals)
        for token in rule_text.split():
            token = token.strip()
            if not token:
                continue
            # Skip keywords, operators, and character constants
            if token.startswith("KW_") or token.startswith("OP_") or token.startswith("CH"):
                continue
            # Match rule names or terminal names
            if re.match(r"^[a-z_][a-z0-9_]*$", token) or re.match(r"^[A-Z_][A-Z0-9_]*$", token):
                if token != "":
                    children.add(token)

        return sorted(children)

    def _compute_parents(self) -> None:
        """Compute parent relationships for all rules."""
        for rule_name, rule in self.rules.items():
            for child_name in rule.children:
                if child_name in self.rules:
                    if rule_name not in self.rules[child_name].parents:
                        self.rules[child_name].parents.append(rule_name)

    def get_section_rules(self, section_prefix: str) -> list[RuleMetadata]:
        """Get all rules in sections matching the prefix."""
        matching_rules = []
        for rule in self.rules.values():
            if rule.section.startswith(section_prefix):
                matching_rules.append(rule)
        return sorted(matching_rules, key=lambda r: r.line_number)

    def generate_markdown_table(
        self,
        section_filter: str | None = None,
        include_examples: bool = True,
        include_deps: bool = False,
    ) -> str:
        """Generate a markdown table of rule support status."""
        lines = ["# Verilog Grammar Support Status", ""]
        lines.append("This table is auto-generated from `verilog.lark` metadata tags.")
        lines.append("")

        # Group rules by section
        sections: dict[str, list[RuleMetadata]] = {}
        for rule in self.rules.values():
            if rule.is_terminal:
                continue
            if section_filter and not rule.section.startswith(section_filter):
                continue
            section = rule.section or "Uncategorized"
            if section not in sections:
                sections[section] = []
            sections[section].append(rule)

        # Sort sections by their order in the file
        section_order = {s: i for i, s in enumerate(self.sections)}
        sorted_sections = sorted(sections.keys(), key=lambda s: section_order.get(s, 999))

        # Statistics
        total_rules = 0
        high_priority = 0
        medium_priority = 0
        low_priority = 0
        synthesizable_yes = 0
        synthesizable_no = 0
        synthesizable_partial = 0
        supported = 0

        for section_name in sorted_sections:
            rules = sorted(sections[section_name], key=lambda r: r.line_number)
            lines.append(f"## {section_name}")
            lines.append("")

            # Table header
            header = "| Rule | Line | Priority | Synth | Support |"
            separator = "|------|------|----------|-------|---------|"
            if include_examples:
                header += " Example |"
                separator += "---------|"
            if include_deps:
                header += " Dependencies |"
                separator += "--------------|"

            lines.append(header)
            lines.append(separator)

            for rule in rules:
                total_rules += 1

                # Count statistics
                if rule.priority == "HIGH":
                    high_priority += 1
                elif rule.priority == "MEDIUM":
                    medium_priority += 1
                elif rule.priority == "LOW":
                    low_priority += 1

                if rule.synthesizable == "YES":
                    synthesizable_yes += 1
                elif rule.synthesizable == "NO":
                    synthesizable_no += 1
                elif rule.synthesizable == "PARTIAL":
                    synthesizable_partial += 1

                if rule.support == "YES":
                    supported += 1

                # Format cells
                priority_badge = self._priority_badge(rule.priority)
                synth_badge = self._synth_badge(rule.synthesizable)
                support_badge = self._support_badge(rule.support)
                example = f"`{rule.example}`" if rule.example else ""

                row = f"| `{rule.name}` | {rule.line_number} | {priority_badge} | {synth_badge} | {support_badge} |"
                if include_examples:
                    row += f" {example} |"
                if include_deps:
                    deps = ", ".join(f"`{c}`" for c in rule.children[:5])
                    if len(rule.children) > 5:
                        deps += f" (+{len(rule.children) - 5} more)"
                    row += f" {deps} |"

                lines.append(row)

            lines.append("")

        # Add summary statistics
        lines.insert(3, "## Summary Statistics")
        lines.insert(4, "")
        lines.insert(5, "| Metric | Count |")
        lines.insert(6, "|--------|-------|")
        lines.insert(7, f"| Total Rules | {total_rules} |")
        lines.insert(8, f"| HIGH Priority | {high_priority} |")
        lines.insert(9, f"| MEDIUM Priority | {medium_priority} |")
        lines.insert(10, f"| LOW Priority | {low_priority} |")
        lines.insert(11, f"| Synthesizable (YES) | {synthesizable_yes} |")
        lines.insert(12, f"| Synthesizable (NO) | {synthesizable_no} |")
        lines.insert(13, f"| Synthesizable (PARTIAL) | {synthesizable_partial} |")
        lines.insert(14, f"| Supported | {supported} |")
        lines.insert(15, "")

        return "\n".join(lines)

    def _priority_badge(self, priority: str) -> str:
        """Format priority as a badge."""
        badges = {
            "HIGH": "🔴 HIGH",
            "MEDIUM": "🟡 MED",
            "LOW": "🟢 LOW",
        }
        return badges.get(priority, "-")

    def _synth_badge(self, synth: str) -> str:
        """Format synthesizable as a badge."""
        badges = {
            "YES": "✅",
            "NO": "❌",
            "PARTIAL": "⚠️",
        }
        return badges.get(synth, "-")

    def _support_badge(self, support: str) -> str:
        """Format support as a badge."""
        badges = {
            "YES": "✅",
            "NO": "❌",
        }
        return badges.get(support, "-")

    def generate_json(self, section_filter: str | None = None) -> str:
        """Export metadata as JSON."""
        data = {}
        for name, rule in self.rules.items():
            if section_filter and not rule.section.startswith(section_filter):
                continue
            data[name] = {
                "name": rule.name,
                "line_number": rule.line_number,
                "section": rule.section,
                "bnf": rule.bnf,
                "priority": rule.priority,
                "synthesizable": rule.synthesizable,
                "example": rule.example,
                "examples": rule.examples,
                "support": rule.support,
                "children": rule.children,
                "parents": rule.parents,
                "is_terminal": rule.is_terminal,
            }
        return json.dumps(data, indent=2)

    def get_statistics(self) -> dict:
        """Get statistics about the grammar."""
        stats = {
            "total_rules": 0,
            "total_terminals": 0,
            "by_priority": {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "unset": 0},
            "by_synthesizable": {"YES": 0, "NO": 0, "PARTIAL": 0, "unset": 0},
            "by_support": {"YES": 0, "NO": 0, "unset": 0},
            "with_examples": 0,
            "with_bnf": 0,
            "sections": len(self.sections),
        }

        for rule in self.rules.values():
            if rule.is_terminal:
                stats["total_terminals"] += 1
            else:
                stats["total_rules"] += 1

                if rule.priority:
                    stats["by_priority"][rule.priority] += 1
                else:
                    stats["by_priority"]["unset"] += 1

                if rule.synthesizable:
                    stats["by_synthesizable"][rule.synthesizable] += 1
                else:
                    stats["by_synthesizable"]["unset"] += 1

                if rule.support:
                    stats["by_support"][rule.support] += 1
                else:
                    stats["by_support"]["unset"] += 1

                if rule.example:
                    stats["with_examples"] += 1

                if rule.bnf:
                    stats["with_bnf"] += 1

        return stats

    def print_statistics(self) -> None:
        """Print statistics summary."""
        stats = self.get_statistics()
        print("=" * 50)
        print("Verilog Grammar Metadata Statistics")
        print("=" * 50)
        print(f"Total Grammar Rules:     {stats['total_rules']}")
        print(f"Total Terminals:         {stats['total_terminals']}")
        print(f"Total Sections:          {stats['sections']}")
        print()
        print("Priority Distribution:")
        print(f"  HIGH:     {stats['by_priority']['HIGH']}")
        print(f"  MEDIUM:   {stats['by_priority']['MEDIUM']}")
        print(f"  LOW:      {stats['by_priority']['LOW']}")
        print(f"  Unset:    {stats['by_priority']['unset']}")
        print()
        print("Synthesizable Distribution:")
        print(f"  YES:      {stats['by_synthesizable']['YES']}")
        print(f"  NO:       {stats['by_synthesizable']['NO']}")
        print(f"  PARTIAL:  {stats['by_synthesizable']['PARTIAL']}")
        print(f"  Unset:    {stats['by_synthesizable']['unset']}")
        print()
        print("Support Status:")
        print(f"  YES:      {stats['by_support']['YES']}")
        print(f"  NO:       {stats['by_support']['NO']}")
        print(f"  Unset:    {stats['by_support']['unset']}")
        print()
        print("Documentation Coverage:")
        print(f"  With Examples: {stats['with_examples']}")
        print(f"  With BNF:      {stats['with_bnf']}")
        print("=" * 50)

    def generate_deps_tags(self, dry_run: bool = True) -> tuple[int, list[str]]:
        """
        Generate DEPS tags for rules that reference other rules.

        Args:
            dry_run: If True, only report changes; if False, modify the file.

        Returns:
            Tuple of (count of rules that would/did get DEPS tags, list of changes)
        """
        with open(self.lark_file, encoding="utf-8") as f:
            lines = f.readlines()

        changes = []
        modified_lines = lines.copy()
        offset = 0  # Track line number offset from insertions

        for rule_name, rule in sorted(self.rules.items(), key=lambda x: x[1].line_number):
            if rule.is_terminal or not rule.children:
                continue

            # Filter children to only include actual grammar rules (not terminals/keywords)
            rule_children = [c for c in rule.children if c in self.rules and not self.rules[c].is_terminal]
            if not rule_children:
                continue

            deps_line = f"// DEPS: {', '.join(sorted(rule_children))}\n"

            # Find the line before the rule definition to insert DEPS
            insert_line = rule.line_number - 1 + offset  # 0-indexed

            # Check if DEPS already exists
            check_start = max(0, insert_line - 6)
            existing_deps = False
            for i in range(check_start, insert_line):
                if "// DEPS:" in modified_lines[i]:
                    existing_deps = True
                    # Update existing DEPS line
                    modified_lines[i] = deps_line
                    changes.append(f"Updated DEPS for {rule_name} at line {i + 1}")
                    break

            if not existing_deps:
                # Insert new DEPS line just before the rule
                # Find the best position (after EXAMPLE, before rule)
                insert_idx = insert_line
                for i in range(insert_line - 1, max(0, insert_line - 8), -1):
                    if modified_lines[i].strip().startswith(rule_name + ":"):
                        insert_idx = i
                        break
                    if "// EXAMPLE:" in modified_lines[i]:
                        insert_idx = i + 1
                        break

                modified_lines.insert(insert_idx, deps_line)
                offset += 1
                changes.append(f"Added DEPS for {rule_name}: {', '.join(rule_children)}")

        if not dry_run and changes:
            with open(self.lark_file, "w", encoding="utf-8") as f:
                f.writelines(modified_lines)

        return len(changes), changes


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Extract metadata from verilog.lark for documentation and testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--table",
        action="store_true",
        default=True,
        help="Generate markdown support table (default)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show statistics summary",
    )
    parser.add_argument(
        "--section",
        type=str,
        help="Filter by section prefix (e.g., 'A.1', 'A.8')",
    )
    parser.add_argument(
        "--deps",
        action="store_true",
        help="Include dependencies in table output",
    )
    parser.add_argument(
        "--generate-deps",
        action="store_true",
        help="Generate DEPS tags in verilog.lark file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what DEPS would be added without modifying files",
    )
    parser.add_argument(
        "--no-examples",
        action="store_true",
        help="Exclude examples from table output",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=str,
        help="Path to verilog.lark file",
    )

    args = parser.parse_args()

    # Parse grammar
    gmp = GrammarMetadataParser(args.file)
    gmp.parse()

    # Generate output
    if args.stats:
        gmp.print_statistics()
    elif getattr(args, "generate_deps", False):
        dry_run = args.dry_run
        count, changes = gmp.generate_deps_tags(dry_run=dry_run)
        if dry_run:
            print(f"Would add/update {count} DEPS tags:")
            for change in changes[:20]:
                print(f"  {change}")
            if len(changes) > 20:
                print(f"  ... and {len(changes) - 20} more")
        else:
            print(f"Added/updated {count} DEPS tags in {gmp.lark_file}")
    elif args.json:
        output = gmp.generate_json(args.section)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"JSON written to {args.output}")
        else:
            print(output)
    else:
        output = gmp.generate_markdown_table(
            section_filter=args.section,
            include_examples=not args.no_examples,
            include_deps=args.deps,
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"Markdown table written to {args.output}")
        else:
            print(output)


if __name__ == "__main__":
    main()
