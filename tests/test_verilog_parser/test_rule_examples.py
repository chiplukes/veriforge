"""
Test grammar rules using embedded EXAMPLE tags from verilog.lark.

Each rule in verilog.lark has one or more EXAMPLE tags with short Verilog snippets.
This test file automatically extracts all examples and verifies that each
one parses correctly when using its rule as the start symbol.

Uses Lark's multi-start feature: one parser instance with all rule names
as possible start symbols, then parse(text, start=rule_name) for each.

Rules may have multiple EXAMPLE tags to cover different alternatives and
optional elements within the rule. All examples are tested.
"""

import pytest
from collections import Counter
from pathlib import Path
from lark import Lark

from veriforge.lark_file.parse_metadata import GrammarMetadataParser

# ============================================================================
# Collect examples from grammar metadata (session-scoped for performance)
# ============================================================================

LARK_FILE = Path(__file__).parent.parent.parent / "src" / "veriforge" / "lark_file" / "verilog.lark"


def _load_rule_examples() -> list[tuple[str, str, str]]:
    """Extract (rule_name, example, section) tuples from grammar metadata.

    Rules may have multiple EXAMPLE tags; each becomes a separate test case.
    Test IDs are formatted as 'rule_name' for single-example rules, or
    'rule_name[0]', 'rule_name[1]', etc. for multi-example rules.
    """
    gmp = GrammarMetadataParser(LARK_FILE)
    rules = gmp.parse()
    result = []
    for name, rule in sorted(rules.items(), key=lambda x: x[1].line_number):
        if rule.is_terminal or not rule.examples:
            continue
        for example in rule.examples:
            result.append((name, example, rule.section))
    return result


def _make_test_ids(examples: list[tuple[str, str, str]]) -> list[str]:
    """Generate unique test IDs, adding index suffix for multi-example rules."""
    name_counts = Counter(name for name, _, _ in examples)
    name_seen: dict[str, int] = {}
    ids = []
    for name, _, _ in examples:
        if name_counts[name] == 1:
            ids.append(name)
        else:
            idx = name_seen.get(name, 0)
            ids.append(f"{name}[{idx}]")
            name_seen[name] = idx + 1
    return ids


# Collect at import time so parametrize has access
RULE_EXAMPLES = _load_rule_examples()
RULE_NAMES = list({name for name, _, _ in RULE_EXAMPLES})


# ============================================================================
# Session-scoped parser fixture (created once, reused for all tests)
# ============================================================================


@pytest.fixture(scope="session")
def example_parser():
    """Create a single Lark parser with all example rules as start symbols.

    Lark accepts start= as a list of strings, allowing one parser instance
    to parse with any of the listed rules as the top-level symbol.
    Parser creation takes ~0.3s; parsing all 337 examples takes ~0.9s.
    """
    with open(LARK_FILE) as f:
        grammar = f.read()

    return Lark(
        grammar,
        parser="earley",
        propagate_positions=True,
        start=RULE_NAMES,
        keep_all_tokens=False,
        maybe_placeholders=False,
    )


# ============================================================================
# Parametrized test - one test case per rule with an EXAMPLE tag
# ============================================================================


@pytest.mark.grammar
class TestRuleExamples:
    """Test each grammar rule parses its own EXAMPLE tag successfully."""

    @pytest.mark.parametrize(
        "rule_name, example, section",
        RULE_EXAMPLES,
        ids=_make_test_ids(RULE_EXAMPLES),
    )
    def test_rule_example_parses(self, example_parser, rule_name, example, section):
        """Verify that EXAMPLE text parses under its rule.

        Each rule in verilog.lark has one or more comments like:
            // EXAMPLE: module top(); endmodule
        This test parses that example using start=<rule_name> and asserts:
            1. Parsing succeeds (no exception)
            2. The tree root matches the rule name
        """
        tree = example_parser.parse(example, start=rule_name)
        assert tree is not None, f"Parser returned None for {rule_name}"
        assert tree.data == rule_name, f"Expected tree root '{rule_name}', got '{tree.data}'"
