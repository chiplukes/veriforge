"""Grammar-driven Verilog fuzzer.

Generates semantically valid Verilog modules by walking the parse grammar's
rule graph, producing model objects, comparing results across all simulation
engines, and cross-checking against Icarus Verilog as an external oracle.

Usage:  uv run -m veriforge.fuzz [--seed N] [--timeout HOURS] [--output DIR]

Module layout
    _grammar_guide   — wraps parse_metadata.GrammarMetadataParser for random
                       rule selection weighted by priority/support.
    _signal_context  — tracks the signal pool (inputs, wires, regs, locals)
                       available at any generation point with scope nesting.
    _expression_gen  — generates Expression model objects (replaces
                       test_differential.py's _gen_expr).
    _statement_gen   — generates Statement model objects (replaces
                       test_differential_statements.py's _gen_stmt).
    _module_gen      — assembles a complete Module from a Strategy.
    _strategies      — module shape strategies (feedforward, registered, …).
    _runner          — long-running fuzz loop: generate, sim, compare, log.
"""

__all__ = [
    "_expression_gen",
    "_grammar_guide",
    "_module_gen",
    "_runner",
    "_signal_context",
    "_statement_gen",
    "_strategies",
]
