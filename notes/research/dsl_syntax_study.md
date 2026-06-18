# DSL Syntax Study — Python Tricks for Hardware Construction

## Purpose

Investigate Python language features that could improve the DSL ergonomics before settling the API and writing examples/libraries. The key pain point is blocking assignment (currently `.set()`) vs non-blocking assignment (`<<=`). We also explore broader syntax improvements.

---

## 1. Survey of Other Python HDL DSLs

### Amaranth (formerly nMigen)

**Assignment syntax:** Uses `.eq()` method for all assignments — no operator overloading for assignment.
```python
m.d.sync += counter.eq(counter + 1)   # synchronous (register)
m.d.comb += out.eq(a + b)             # combinational (wire)
```
The *domain* (`sync` vs `comb`) determines whether the assignment is blocking or non-blocking. The `.eq()` call just creates an `Assign` object; it's added to a domain with `+=`.

**Key design decisions:**
- All augmented assignment operators (`__iadd__`, `__ilshift__`, etc.) on `Value` are **forbidden** — they raise `TypeError("Signal object doesn't support augmented assignment")`. This avoids confusion between expression operators and assignment.
- Control flow uses `with m.If(cond):`, `with m.Elif(cond):`, `with m.Else():`, `with m.Switch(val):` — very similar to our DSL.
- Operators like `+`, `-`, `*`, `//`, `%`, `&`, `|`, `^`, `~`, `<<`, `>>`, comparisons — all overloaded to create expression trees (identical approach to ours).
- `Cat()` for concatenation, `Mux()` for ternary, `.replicate()` for replication.
- Logical operators: Since Python's `and`/`or`/`not` can't be overloaded, they use `&`, `|`, `~` on boolean signals (same problem we face, same solution with `land`/`lor`/`lnot`).

**Module pattern:** Class-based. `class MyModule(Elaboratable)` with `def elaborate(self, platform)` method.

### MyHDL

**Assignment syntax:** Uses `.next` property for signal assignment.
```python
sig.next = value   # like non-blocking assignment
```
There is no separate blocking vs non-blocking — MyHDL infers register/wire from the sensitivity list (generator-based `@always(clk.posedge)` vs `@always_comb`).

**Key design decisions:**
- All augmented assignment operators are **forbidden** — they all raise `TypeError("Signal object doesn't support augmented assignment")`.
- Uses Python generators with `yield` for timing/sensitivity (unique approach).
- `@always(clk.posedge)` decorator for sequential logic.
- `@always_comb` decorator for combinational logic.
- Operators delegate to the current *value* (`.val`) — they return Python ints, not expression trees. This means MyHDL expressions compute at simulation time, not at elaboration time. (Very different from our approach.)

### PyRTL

**Assignment syntax:** Uses `<<=` (`__ilshift__`) for wire connection.
```python
output <<= a + b          # combinational
reg.next <<= reg + 1      # register (via Register.next property)
```

**Key design decisions:**
- `<<=` is the universal connection operator.
- Registers are explicit (`Register` class) with `.next` attribute for the input.
- No separate blocking/non-blocking — the type of wire determines behavior.
- Uses a global "working block" (similar to implicit module context).

### Chisel (Scala, for reference)

```scala
io.out := io.a + io.b       // combinational (wires)
reg := reg + 1.U            // register assignment
```
Uses `:=` (Scala allows custom operator names). No blocking/non-blocking distinction — determined by target type (`Wire` vs `Reg`).

---

## 2. Current DSL Operator Usage Inventory

### Operators used for expressions (on `Expr` class):

| Python Operator | Verilog Equivalent | Method |
|---|---|---|
| `+` | `+` | `__add__`, `__radd__` |
| `-` | `-` (binary/unary) | `__sub__`, `__rsub__`, `__neg__` |
| `*` | `*` | `__mul__`, `__rmul__` |
| `//` | `/` | `__floordiv__`, `__rfloordiv__` |
| `%` | `%` | `__mod__`, `__rmod__` |
| `**` | `**` | `__pow__`, `__rpow__` |
| `&` | `&` | `__and__`, `__rand__` |
| `\|` | `\|` | `__or__`, `__ror__` |
| `^` | `^` | `__xor__`, `__rxor__` |
| `~` | `~` | `__invert__` |
| `<<` | `<<` | `__lshift__`, `__rlshift__` |
| `>>` | `>>` | `__rshift__`, `__rrshift__` |
| `==` | `==` | `__eq__` |
| `!=` | `!=` | `__ne__` |
| `<` | `<` | `__lt__` |
| `<=` | `<=` | `__le__` |
| `>` | `>` | `__gt__` |
| `>=` | `>=` | `__ge__` |
| `[i]` | bit select | `__getitem__` |
| `[m:l]` | range select | `__getitem__` (slice) |

### Operators used for assignment:

| Syntax | Purpose | Method |
|---|---|---|
| `<<=` | Non-blocking assign (NBA) | `__ilshift__` |
| `.set()` | Blocking assign | Method call |

### Operators available (not used by DSL):

| Python Operator | Augmented | Method | Notes |
|---|---|---|---|
| `@` | `@=` | `__matmul__` / `__imatmul__` | Added Python 3.5, originally for matrix multiply |
| `/` | `/=` | `__truediv__` / `__itruediv__` | Could conflict with division expectation |
| `//=` | — | `__ifloordiv__` | `//` already used for expression division |
| `%=` | — | `__imod__` | `%` already used for expression modulo |
| `**=` | — | `__ipow__` | `**` already used for expression power |
| `>>=` | — | `__irshift__` | `>>` already used for expression right-shift |
| `&=` | — | `__iand__` | `&` already used for expression bitwise AND |
| `\|=` | — | `__ior__` | `\|` already used for expression bitwise OR |
| `^=` | — | `__ixor__` | `^` already used for expression bitwise XOR |
| `+=` | — | `__iadd__` | `+` already used for expression addition |
| `-=` | — | `__isub__` | `-` already used for expression subtraction |
| `*=` | — | `__imul__` | `*` already used for expression multiplication |

---

## 3. Analysis: Blocking Assignment Operator Candidates

The current `.set()` for blocking assignment works but is asymmetric with `<<=` for non-blocking. We want both assignment types to feel like first-class operators.

### Candidate 1: `@=` (`__imatmul__`)

```python
# Non-blocking (unchanged)
count <<= count + 1

# Blocking
count @= count + 1
```

**Pros:**
- `@` / `@=` is the only Python operator with no pre-existing mathematical meaning in hardware contexts. It was added for matrix multiplication but has no usage in Verilog/HDL.
- Short, clean syntax. Visually distinct from `<<=`.
- `@` is symmetrically unused — we don't need `a @ b` as an expression operator.
- Doesn't conflict with any expression operator we use.

**Cons:**
- `@=` is obscure for users unfamiliar with Python 3.5+ matrix operators.
- No obvious mnemonic for "blocking assignment" (but `<<=` isn't obviously "non-blocking" either — it's just convention).

### Candidate 2: `>>=` (`__irshift__`)

```python
count >>= count + 1
```

**Pros:**
- Uses an arrow-like visual: `>>=` could suggest "push value in".

**Cons:**
- **MAJOR:** `>>` is already used for right-shift expressions. If `a >> 3` returns an Expr, then `a >>= 3` would be ambiguous — Python sees `a = a.__irshift__(3)`. We can't use `>>` for shift AND `>>=` for assignment without careful trickery (returning the target Signal from `__irshift__` would break shift expression semantics).
- Confusing: `a >>= b` looks like "right-shift a by b".

**Verdict: REJECTED** — conflicts with right-shift expression operator.

### Candidate 3: Other augmented assignments (`&=`, `|=`, `^=`, `+=`, etc.)

All rejected for the same reason: the non-augmented operator is already used for expressions, so `a += b` would be ambiguous between "a = a + b (expression)" and "blocking assign a to a+b (statement)".

### Candidate 4: Keep `.set()` but add an alias

```python
count.b(count + 1)    # "b" for blocking
count.assign(count + 1)
count.blocking(count + 1)
```

**Pros:**
- No operator overloading tricks needed.
- Can have any name.

**Cons:**
- Still asymmetric with `<<=`, user still wonders "why can't I use an operator?"

### Candidate 5: Use `.eq()` for blocking (Amaranth-style) and `<<=` for NBA

```python
count <<= count + 1    # non-blocking (unchanged)
count.eq(count + 1)    # blocking
```

**Cons:**
- `.eq()` might be confused with equality test (`.eq` is used in Amaranth for *all* assignment, not just blocking).
- Not much better than `.set()`.

---

## 4. `@=` for Blocking Assignment — Implemented

`@=` (`__imatmul__`) was implemented as the blocking assignment operator in
`src/veriforge/dsl/builder.py`. It is the only free augmented assignment
operator — all others conflict with expression operators. The DSL now supports:

```python
with m.always(posedge(clk)):
    with m.if_(rst):
        count <<= 0          # non-blocking: count <= 0
    with m.else_():
        count <<= count + 1   # non-blocking: count <= count + 1

with m.always(a, b):
    sum_ @= a + b             # blocking: sum_ = a + b
    carry @= a & b            # blocking: carry = a & b
```

`.set()` was kept as a deprecated fallback (`builder.py` line 447 has the deprecation
notice). Subscript blocking assign (`data[3:0] @= value`) works automatically.

---

## 5. Other Syntax Improvements Considered

### 5a. Decorator-based always blocks

**Idea:** Instead of `with m.always(posedge(clk)):`, allow:
```python
@m.always_ff(posedge(clk))
def counter_logic():
    count <<= count + 1

@m.always_comb
def adder():
    sum_ @= a + b
```

**Analysis:**
- Would require the decorator to execute the function body *once* to capture statements (like MyHDL does).
- Problem: The function body writes Python statements that create model nodes. With `with` blocks we use context managers that manage the statement stack. A decorator approach would need to be careful about re-entrant statement capture.
- **Verdict: DEFER** — `with` blocks work well and are more Pythonic for our use case. Decorators could be added later as syntactic sugar without breaking existing code.

### 5b. `__set_name__` / descriptor-based signal names

**Idea:** Automatically infer signal name from variable name:
```python
class MyModule(Module):
    clk = Input()         # name auto-inferred as "clk"
    rst = Input()         # name auto-inferred as "rst"
    count = OutputReg(8)  # name auto-inferred as "count"
```

**Analysis:**
- `__set_name__` is called when a descriptor is assigned to a class attribute.
- Requires a class-based module pattern (see 5d below).
- Would be very clean for port declarations.
- **Verdict: FUTURE** — excellent idea for a class-based module syntax, but independent of the assignment operator question.

### 5c. Context variables for implicit module reference

**Idea:** Use `contextvars.ContextVar` to avoid passing `m.` everywhere:
```python
from contextvars import ContextVar
_current_module = ContextVar('current_module')

# User code:
with Module("counter") as m:
    # m is automatically set as current module
    clk = input("clk")  # no m. prefix needed
```

**Analysis:**
- Reduces boilerplate but makes the API less explicit.
- Could cause confusion in nested module definitions.
- Other HDLs (PyRTL, Amaranth) use either explicit module reference or global state.
- **Verdict: NOT RECOMMENDED** — explicit `m.` prefix is clear and Pythonic. The slight verbosity is worth the clarity.

### 5d. Class-based module syntax

**Idea:** Define modules as classes instead of procedural code:
```python
class Counter(HWModule):
    # Port declarations (using descriptors)
    clk = Input()
    rst = Input()
    count = OutputReg(8)

    def body(self):
        with self.always(posedge(self.clk)):
            with self.if_(self.rst):
                self.count <<= 0
            with self.else_():
                self.count <<= self.count + 1
```

**Analysis:**
- More structured, familiar to Amaranth/Chisel users.
- `__set_name__` would work for auto-naming ports.
- However, it would be a **major** API change and parallel system to maintain.
- Our current procedural approach is simpler and works well for Python scripts.
- **Verdict: FUTURE** — could be added as an alternative module declaration style without replacing the current one. Good candidate for the "composability showcase" roadmap item.

### 5e. Type hints as width

**Idea:** Use type annotations for width:
```python
count: Bits[8]
addr: Unsigned[16]
offset: Signed[12]
```

**Analysis:**
- Python type hints are not evaluated at runtime by default (PEP 563).
- Would require `__class_getitem__` on a custom class.
- Could be elegant in a class-based module context.
- **Verdict: FUTURE** — nice idea, pairs well with class-based syntax (5d).

### 5f. Subscript assignment for bit/range blocking assign

**Current limitation:** `data[3:0] <<= value` works for NBA but `data[3:0].set(value)` is needed for blocking.

**With @=:** `data[3:0] @= value` would work naturally since `__imatmul__` (like `__ilshift__`) triggers `__setitem__` afterward, which is already a no-op.

**Verdict:** This is a **free benefit** of adding `@=`.

---

## 6. What Was Done

`@=` was implemented; `.set()` kept as a deprecated alias; `dsl_guide.md` updated.

The deferred items (decorator-based always blocks, class-based module syntax,
`__set_name__` descriptor ports, type hints as width, context variables) remain
unimplemented and are still candidates for future work.

---

## 7. Operator Quick Reference (Post-Change)

```
Expression Operators (return Expr):
  +  -  *  //  %  **          arithmetic
  &  |  ^  ~                  bitwise
  <<  >>                      shift
  ==  !=  <  <=  >  >=        comparison
  [i]  [m:l]                  bit/range select

Assignment Operators (create statements):
  <<=    non-blocking assign   (Verilog <=)
  @=     blocking assign       (Verilog =)

Helper Functions:
  posedge(sig)                 sensitivity edge
  negedge(sig)                 sensitivity edge
  cat(a, b, ...)              concatenation {a, b, ...}
  rep(n, sig)                  replication {n{sig}}
  mux(sel, t, f)               ternary sel ? t : f
  land(a, b)                   logical AND a && b
  lor(a, b)                    logical OR  a || b
  lnot(a)                      logical NOT !a
  reduce_and(a)                reduction &a
  reduce_or(a)                 reduction |a
  reduce_xor(a)                reduction ^a
```
