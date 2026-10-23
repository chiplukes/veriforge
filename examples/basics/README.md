# Basic Examples

Small examples that demonstrate the Python DSL and common generated-Verilog
patterns.

| File | Demonstrates |
| --- | --- |
| `counter.py` | A simple counter module. |
| `shift_register.py` | Parameterized shift-register construction. |
| `fsm.py` | Finite-state-machine structure. |
| `alu.py` | ALU-style combinational logic. |
| `testbench.py` | Basic testbench-oriented DSL constructs. |

The examples name most signals after their Python variable
(`clk = m.input()`, `count = m.output_reg(width=8, comment="...")`); see
[Signal names](../../notes/dsl/dsl_guide.md#signal-names-inferred-or-explicit)
for when to pass a name instead.

Run an example from the repository root:

```powershell
uv run python examples\basics\counter.py
```
