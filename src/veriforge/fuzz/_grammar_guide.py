"""Grammar guide for random rule selection."""

from __future__ import annotations

import random
from functools import cached_property

from ..lark_file.parse_metadata import GrammarMetadataParser, RuleMetadata


class GrammarGuide:
    """Random rule selection guided by grammar metadata.

    Wraps ``parse_metadata.GrammarMetadataParser``'s full rule graph so the
    module/expression/statement generators can ask questions like "what are the
    valid alternatives for a ``statement``?" and get back a correctly-filtered
    list weighted by priority and synthesizability.

    Parameters
    ----------
    only_supported:
        If True (the default), ``SUPPORT: NO`` and untagged rules are excluded
        from all queries.  Set to False to allow generating even unsupported
        constructs (useful for testing the parser itself).
    """

    def __init__(self, *, only_supported: bool = True) -> None:
        metadata = GrammarMetadataParser()
        metadata.parse()
        self._rules: dict[str, RuleMetadata] = metadata.rules
        self._only_supported = only_supported

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def rule(self, name: str) -> RuleMetadata | None:
        """Return metadata for *name*, or ``None``."""
        return self._rules.get(name)

    def children(self, name: str) -> list[str]:
        """Direct child rule names referenced by *name*.

        These are the grammar rules (non-terminals) that appear in the
        definition of *name*, e.g. ``expression`` → ``[primary, binary_operator, ...]``.
        """
        r = self._rules.get(name)
        if r is None:
            return []
        return [c for c in r.children if c in self._rules and not self._rules[c].is_terminal]

    @cached_property
    def _supported_children(self) -> dict[str, list[str]]:
        """Cache of name → list of supported non-terminal children."""
        result: dict[str, list[str]] = {}
        for name in self._rules:
            children = self.children(name)
            if self._only_supported:
                children = [c for c in children if c not in self._unsupported_rules]
            result[name] = children
        return result

    def supported_children(self, name: str) -> list[str]:
        """Like ``children()`` but filtered to SUPPORT: YES rules."""
        return self._supported_children.get(name, [])

    @cached_property
    def _unsupported_rules(self) -> set[str]:
        """Names of rules with SUPPORT: NO (or unset, when filtering)."""
        return {n for n, r in self._rules.items() if r.support != "YES" and not r.is_terminal}

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def reachable_from(self, name: str) -> set[str]:
        """Return all rule names transitively reachable from *name*.

        BFS that follows grammar rule references.  When filtering, only
        supported children are traversed.
        """
        seen: set[str] = set()
        queue = [name]
        while queue:
            cur = queue.pop()
            if cur in seen or cur not in self._rules:
                continue
            seen.add(cur)
            for child in self.supported_children(cur):
                if child not in seen:
                    queue.append(child)
        return seen

    # ------------------------------------------------------------------
    # Weighted selection
    # ------------------------------------------------------------------

    def pick_child(
        self,
        rng: random.Random,
        name: str,
        *,
        weights: dict[str, float] | None = None,
    ) -> str | None:
        """Randomly pick one supported child of *name*.

        Parameters
        ----------
        rng:
            Pre-seeded ``random.Random`` instance.
        name:
            Parent rule to expand.
        weights:
            Optional per-child weight multipliers (default: 1.0 for all).
            Higher weights make a child more likely to be selected.  Children
            not listed get weight 1.0.  Only children that appear in
            ``supported_children(name)`` are eligible.
        """
        children = self.supported_children(name)
        if not children:
            return None
        if weights is None:
            weights = {}
        ws = [weights.get(c, 1.0) for c in children]
        return rng.choices(children, weights=ws, k=1)[0]

    def pick_supported(
        self,
        rng: random.Random,
        candidates: list[str],
        *,
        weights: dict[str, float] | None = None,
    ) -> str | None:
        """Pick one of *candidates* that is supported.

        Similar to ``pick_child`` but the candidate list is explicitly provided
        rather than deriving from a single parent rule.
        """
        if self._only_supported:
            candidates = [c for c in candidates if c not in self._unsupported_rules]
        if not candidates:
            return None
        if weights is None:
            weights = {}
        ws = [weights.get(c, 1.0) for c in candidates]
        return rng.choices(candidates, weights=ws, k=1)[0]
