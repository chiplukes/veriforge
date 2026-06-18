"""Style-configurable Verilog formatter.

Provides :class:`VerilogFormatter` which converts model objects to Verilog
source text using the rules defined in a :class:`FormatStyle`.  Convenience
functions :func:`format_module` and :func:`format_design` are also exported.

Expression formatting is delegated to :func:`emit_expression` from the base
emitter — only structural / statement layout is style-dependent.
"""

from __future__ import annotations

from ..model.assignments import ContinuousAssign
from ..model.base import Comment, VerilogNode
from ..model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from ..model.design import Design, Module
from ..model.expressions import Range
from ..model.functions import FunctionDecl, TaskDecl
from ..model.generate import (
    GenerateBlock,
    GenerateCase,
    GenerateCaseItem,
    GenerateFor,
    GenerateIf,
    GenvarDecl,
)
from ..model.instances import Instance, ParameterBinding, PortConnection
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.specify import SpecifyBlock
from ..model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventControl,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    SensitivityEdge,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from ..model.variables import Variable
from .format_style import FormatStyle
from .verilog_emitter import emit_expression


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------


def format_design(design: Design, style: FormatStyle | None = None, *, emit_comments: bool = True) -> str:
    """Format a full :class:`Design` as Verilog source text."""
    return VerilogFormatter(style).format_design(design, emit_comments=emit_comments)


def format_module(module: Module, style: FormatStyle | None = None, *, emit_comments: bool = True) -> str:
    """Format a single :class:`Module` as Verilog source text."""
    return VerilogFormatter(style).format_module(module, emit_comments=emit_comments)


# ---------------------------------------------------------------------------
# Formatter class
# ---------------------------------------------------------------------------


class VerilogFormatter:
    """Emit Verilog source with configurable formatting style."""

    def __init__(self, style: FormatStyle | None = None) -> None:
        self.style = style or FormatStyle()
        self._ind = " " * self.style.indent_width
        self._comments = True  # toggled per format_* call

    # -- Top-level entry points ---------------------------------------------

    def format_design(self, design: Design, *, emit_comments: bool = True) -> str:
        self._comments = emit_comments
        parts: list[str] = []
        for i, mod in enumerate(design.modules):
            if i > 0:
                parts.append("")
            parts.append(self.format_module(mod, emit_comments=emit_comments))
        return "\n".join(parts) + "\n"

    def format_module(self, module: Module, *, emit_comments: bool = True) -> str:  # noqa: PLR0912, PLR0915
        self._comments = emit_comments
        lines: list[str] = []
        ind = self._ind

        # Leading comments
        if self._comments:
            lines.extend(self._leading_comments(module))

        # Module header ---
        header = f"module {module.name}"

        # Parameter port list
        param_ports = [p for p in module.parameters if not p.is_local]
        if param_ports:
            header += " #("
            param_strs = [self._parameter_port(p) for p in param_ports]
            if len(param_strs) == 1:
                header += param_strs[0] + ")"
            else:
                header += "\n"
                for i, ps in enumerate(param_strs):
                    sep = "," if i < len(param_strs) - 1 else ""
                    header += f"{ind}{ps}{sep}\n"
                header += ")"

        # Port list
        if module.ports:
            header += " ("
            port_strs = self._format_port_strings(module.ports)
            has_extras = self._comments and any(p.comments for p in module.ports)
            has_attrs = any(p.attributes for p in module.ports)
            if self._ports_fit_one_line(port_strs) and not has_extras and not has_attrs:
                header += ", ".join(port_strs) + ");"
            else:
                header += "\n"
                for i, port in enumerate(module.ports):
                    if self._comments:
                        for lc in self._leading_comments(port, ind):
                            header += lc + "\n"
                    for attr_line in self._attributes(port, ind):
                        header += attr_line + "\n"
                    ps = port_strs[i]
                    sep = "," if i < len(module.ports) - 1 else ""
                    line = f"{ind}{ps}{sep}"
                    if self._comments:
                        trailing = self._trailing_comment(port)
                        if trailing:
                            line += f"  {trailing}"
                    header += line + "\n"
                header += ");"
        else:
            header += ";"

        lines.append(header)

        # Body declarations ---
        body: list[str] = []

        # Localparams
        for p in module.parameters:
            if p.is_local:
                if self._comments:
                    body.extend(self._leading_comments(p, ind))
                item = self._localparam(p, ind)
                if self._comments:
                    t = self._trailing_comment(p)
                    if t:
                        item += f"  {t}"
                body.append(item)

        # Nets
        for n in module.nets:
            if self._comments:
                body.extend(self._leading_comments(n, ind))
            body.extend(self._attributes(n, ind))
            item = self._net(n, ind)
            if self._comments:
                t = self._trailing_comment(n)
                if t:
                    item += f"  {t}"
            body.append(item)

        # Variables (skip output reg duplicates)
        port_reg_names = {p.name for p in module.ports if p.data_type == "reg" and p.direction == PortDirection.OUTPUT}
        for v in module.variables:
            if v.name in port_reg_names:
                continue
            if self._comments:
                body.extend(self._leading_comments(v, ind))
            body.extend(self._attributes(v, ind))
            item = self._variable(v, ind)
            if self._comments:
                t = self._trailing_comment(v)
                if t:
                    item += f"  {t}"
            body.append(item)

        # Continuous assigns
        for ca in module.continuous_assigns:
            if self._comments:
                body.extend(self._leading_comments(ca, ind))
            item = self._continuous_assign(ca, ind)
            if self._comments:
                t = self._trailing_comment(ca)
                if t:
                    item += f"  {t}"
            body.append(item)

        # Instances
        for inst in module.instances:
            if self._comments:
                body.extend(self._leading_comments(inst, ind))
            item = self._instance(inst, ind)
            if self._comments:
                t = self._trailing_comment(inst)
                if t:
                    item += f"  {t}"
            body.append(item)

        # Always blocks
        for ab in module.always_blocks:
            if self._comments:
                body.extend(self._leading_comments(ab, ind))
            body.append("")
            body.append(self._always_block(ab, ind))

        # Initial blocks
        for ib in module.initial_blocks:
            if self._comments:
                body.extend(self._leading_comments(ib, ind))
            body.append("")
            body.append(self._initial_block(ib, ind))

        # Functions
        for fn in module.functions:
            if self._comments:
                body.extend(self._leading_comments(fn, ind))
            body.append("")
            body.append(self._function_decl(fn, ind))

        # Tasks
        for tk in module.tasks:
            if self._comments:
                body.extend(self._leading_comments(tk, ind))
            body.append("")
            body.append(self._task_decl(tk, ind))

        # Generate
        for gb in module.generate_blocks:
            if self._comments:
                body.extend(self._leading_comments(gb, ind))
            body.append("")
            body.append(self._generate_construct(gb, ind))

        # Specify
        for sb in module.specify_blocks:
            if self._comments:
                body.extend(self._leading_comments(sb, ind))
            body.append("")
            body.append(self._specify_block(sb, ind))

        if body:
            lines.append("")
            lines.extend(body)

        lines.append("")
        lines.append("endmodule")
        return "\n".join(lines)

    # -- Port formatting ----------------------------------------------------

    def _format_port_strings(self, ports: list[Port]) -> list[str]:
        """Return formatted port strings, optionally aligned."""
        if not self.style.align_ports:
            return [self._port_str(p) for p in ports]

        # Compute (prefix, name, default) tuples
        parts: list[tuple[str, str, str]] = []
        for p in ports:
            segs = [p.direction.value]
            if p.net_type:
                segs.append(p.net_type)
            if p.data_type:
                segs.append(p.data_type)
            if p.signed:
                segs.append("signed")
            if p.width:
                segs.append(self._range(p.width))
            prefix = " ".join(segs)
            default = f" = {emit_expression(p.default_value)}" if p.default_value else ""
            parts.append((prefix, p.name, default))

        max_pfx = max(len(pfx) for pfx, _, _ in parts)
        return [f"{pfx:<{max_pfx}} {name}{dflt}" for pfx, name, dflt in parts]

    _MAX_INLINE_PORTS = 3
    _MAX_PORT_STR_LEN = 30

    @staticmethod
    def _ports_fit_one_line(port_strs: list[str]) -> bool:
        return len(port_strs) <= VerilogFormatter._MAX_INLINE_PORTS and all(
            len(s) < VerilogFormatter._MAX_PORT_STR_LEN for s in port_strs
        )

    # -- begin/end core helper ----------------------------------------------

    def _controlled_body(self, control_line: str, body: Statement | None, ci: str) -> str:
        """Join *control_line* with its *body*, applying the begin/end style.

        For every style the statements inside a ``begin``/``end`` block are
        indented one level deeper than the control keyword.  Only the
        placement of ``begin`` and ``end`` differs:

        * **knr** — ``begin`` appended to *control_line*; ``end`` at *ci*.
        * **allman** — ``begin`` and ``end`` both at *ci + indent* (same column
          as the statements).
        * **gnu** — ``begin`` and ``end`` at *ci*; statements at *ci + indent*.
        """
        if body is None:
            return f"{control_line} ;"

        inner = ci + self._ind
        style = self.style.begin_end_style

        if isinstance(body, SeqBlock):
            name_sfx = f" : {body.name}" if body.name else ""
            stmts = [self._stmt(s, inner) for s in body.statements]

            if style == "knr":
                parts = [f"{control_line} begin{name_sfx}"]
                parts.extend(stmts)
                parts.append(f"{ci}end")
            elif style == "allman":
                parts = [control_line]
                parts.append(f"{inner}begin{name_sfx}")
                parts.extend(stmts)
                parts.append(f"{inner}end")
            else:  # gnu
                parts = [control_line]
                parts.append(f"{ci}begin{name_sfx}")
                parts.extend(stmts)
                parts.append(f"{ci}end")
            return "\n".join(parts)

        return f"{control_line}\n{self._stmt(body, inner)}"

    def _end_indent(self, ci: str) -> str:
        """Return the indentation used for ``end`` at control indent *ci*."""
        if self.style.begin_end_style == "allman":
            return ci + self._ind
        return ci

    # -- Statement dispatch -------------------------------------------------

    def _stmt(self, stmt: Statement, ci: str) -> str:  # noqa: PLR0911, PLR0912
        """Format a statement at indentation *ci*."""
        if isinstance(stmt, SeqBlock):
            return self._seq_block(stmt, ci)
        if isinstance(stmt, ParBlock):
            return self._par_block(stmt, ci)
        if isinstance(stmt, BlockingAssign):
            return f"{ci}{emit_expression(stmt.lhs)} = {emit_expression(stmt.rhs)};"
        if isinstance(stmt, NonblockingAssign):
            return f"{ci}{emit_expression(stmt.lhs)} <= {emit_expression(stmt.rhs)};"
        if isinstance(stmt, IfStatement):
            return self._if_stmt(stmt, ci)
        if isinstance(stmt, CaseStatement):
            return self._case_stmt(stmt, ci)
        if isinstance(stmt, ForLoop):
            return self._for_loop(stmt, ci)
        if isinstance(stmt, WhileLoop):
            return self._while_loop(stmt, ci)
        if isinstance(stmt, ForeverLoop):
            return self._forever_loop(stmt, ci)
        if isinstance(stmt, RepeatLoop):
            return self._repeat_loop(stmt, ci)
        if isinstance(stmt, SystemTaskCall):
            return self._system_task(stmt, ci)
        if isinstance(stmt, TaskEnable):
            return self._task_enable(stmt, ci)
        if isinstance(stmt, DelayControl):
            return self._delay_control(stmt, ci)
        if isinstance(stmt, EventControl):
            return self._event_control(stmt, ci)
        if isinstance(stmt, WaitStatement):
            return self._wait_stmt(stmt, ci)
        if isinstance(stmt, DisableStatement):
            return f"{ci}disable {stmt.target};"
        if isinstance(stmt, EventTrigger):
            return f"{ci}-> {stmt.event};"
        return f"{ci}/* unknown statement */;"

    # -- Blocks -------------------------------------------------------------

    def _seq_block(self, block: SeqBlock, ci: str) -> str:
        """Format a standalone ``begin``/``end`` block."""
        inner = ci + self._ind
        name_sfx = f" : {block.name}" if block.name else ""
        lines = [f"{ci}begin{name_sfx}"]
        for s in block.statements:
            lines.append(self._stmt(s, inner))
        lines.append(f"{ci}end")
        return "\n".join(lines)

    def _par_block(self, block: ParBlock, ci: str) -> str:
        inner = ci + self._ind
        name_sfx = f" : {block.name}" if block.name else ""
        lines = [f"{ci}fork{name_sfx}"]
        for s in block.statements:
            lines.append(self._stmt(s, inner))
        lines.append(f"{ci}join")
        return "\n".join(lines)

    # -- Control structures -------------------------------------------------

    def _if_stmt(self, stmt: IfStatement, ci: str) -> str:
        """Format ``if``/``else if``/``else`` with begin/end style."""
        style = self.style.begin_end_style
        inner = ci + self._ind
        lines: list[str] = []

        control = f"{ci}if ({emit_expression(stmt.condition)})"

        # ---- Then branch ----
        then_is_block = isinstance(stmt.then_body, SeqBlock)

        if then_is_block:
            assert isinstance(stmt.then_body, SeqBlock)
            block = stmt.then_body
            name_sfx = f" : {block.name}" if block.name else ""
            stmts = [self._stmt(s, inner) for s in block.statements]

            if style == "knr":
                lines.append(f"{control} begin{name_sfx}")
            elif style == "allman":
                lines.append(control)
                lines.append(f"{inner}begin{name_sfx}")
            else:
                lines.append(control)
                lines.append(f"{ci}begin{name_sfx}")
            lines.extend(stmts)
            # end appended below (may merge with else)
        else:
            lines.append(control)
            if stmt.then_body:
                lines.append(self._stmt(stmt.then_body, inner))

        # ---- Else branch ----
        if stmt.else_body:
            end_ci = self._end_indent(ci)

            if then_is_block and style == "knr" and self.style.end_else_same_line:
                # "end else ..." on one line
                self._append_knr_end_else(lines, stmt.else_body, ci, inner, end_ci)
            else:
                if then_is_block:
                    lines.append(f"{end_ci}end")
                self._append_else(lines, stmt.else_body, ci, inner)
        elif then_is_block:
            lines.append(f"{self._end_indent(ci)}end")

        return "\n".join(lines)

    def _append_knr_end_else(
        self,
        lines: list[str],
        else_body: Statement,
        ci: str,
        inner: str,
        end_ci: str,
    ) -> None:
        """Append ``end else …`` on a single line (KNR only)."""
        if isinstance(else_body, IfStatement):
            nested = self._if_stmt(else_body, ci)
            first, *rest = nested.split("\n")
            lines.append(f"{end_ci}end else {first[len(ci) :]}")
            lines.extend(rest)
        elif isinstance(else_body, SeqBlock):
            name_sfx = f" : {else_body.name}" if else_body.name else ""
            lines.append(f"{end_ci}end else begin{name_sfx}")
            for s in else_body.statements:
                lines.append(self._stmt(s, inner))
            lines.append(f"{ci}end")
        else:
            lines.append(f"{end_ci}end else")
            lines.append(self._stmt(else_body, inner))

    def _append_else(self, lines: list[str], else_body: Statement, ci: str, inner: str) -> None:
        """Append ``else`` clause on separate line(s)."""
        if isinstance(else_body, IfStatement):
            nested = self._if_stmt(else_body, ci)
            first, *rest = nested.split("\n")
            lines.append(f"{ci}else {first[len(ci) :]}")
            lines.extend(rest)
        elif isinstance(else_body, SeqBlock):
            lines.append(self._controlled_body(f"{ci}else", else_body, ci))
        else:
            lines.append(f"{ci}else")
            lines.append(self._stmt(else_body, inner))

    def _case_stmt(self, stmt: CaseStatement, ci: str) -> str:
        inner = ci + self._ind
        lines = [f"{ci}{stmt.case_type} ({emit_expression(stmt.expression)})"]
        for item in stmt.items:
            lines.append(self._case_item(item, inner))
        lines.append(f"{ci}endcase")
        return "\n".join(lines)

    def _case_item(self, item: CaseItem, ci: str) -> str:
        inner = ci + self._ind
        if item.is_default:
            label = f"{ci}default:"
        else:
            values = ", ".join(emit_expression(v) for v in item.values)
            label = f"{ci}{values}:"

        if not item.body:
            return f"{label} ;"

        if isinstance(item.body, SeqBlock):
            block = item.body
            name_sfx = f" : {block.name}" if block.name else ""
            style = self.style.begin_end_style
            stmts = [self._stmt(s, inner) for s in block.statements]

            if style == "knr":
                parts = [f"{label} begin{name_sfx}"]
                parts.extend(stmts)
                parts.append(f"{ci}end")
            elif style == "allman":
                parts = [label]
                parts.append(f"{inner}begin{name_sfx}")
                parts.extend(stmts)
                parts.append(f"{inner}end")
            else:
                parts = [label]
                parts.append(f"{ci}begin{name_sfx}")
                parts.extend(stmts)
                parts.append(f"{ci}end")
            return "\n".join(parts)

        # Single statement inline after label
        return f"{label} {self._stmt(item.body, '').lstrip()}"

    # -- Loops --------------------------------------------------------------

    def _for_loop(self, stmt: ForLoop, ci: str) -> str:
        init = f"{emit_expression(stmt.init.lhs)} = {emit_expression(stmt.init.rhs)}"
        cond = emit_expression(stmt.condition)
        update = f"{emit_expression(stmt.update.lhs)} = {emit_expression(stmt.update.rhs)}"
        return self._controlled_body(f"{ci}for ({init}; {cond}; {update})", stmt.body, ci)

    def _while_loop(self, stmt: WhileLoop, ci: str) -> str:
        return self._controlled_body(f"{ci}while ({emit_expression(stmt.condition)})", stmt.body, ci)

    def _forever_loop(self, stmt: ForeverLoop, ci: str) -> str:
        return self._controlled_body(f"{ci}forever", stmt.body, ci)

    def _repeat_loop(self, stmt: RepeatLoop, ci: str) -> str:
        return self._controlled_body(f"{ci}repeat ({emit_expression(stmt.count)})", stmt.body, ci)

    # -- Always / Initial ---------------------------------------------------

    def _always_block(self, ab: AlwaysBlock, ci: str) -> str:
        control = f"{ci}always"
        if ab.sensitivity_list:
            control += f" @({self._sensitivity_list(ab.sensitivity_list)})"
        elif ab.sensitivity_type == SensitivityType.COMBINATIONAL:
            control += " @(*)"
        return self._controlled_body(control, ab.body, ci)

    def _initial_block(self, ib: InitialBlock, ci: str) -> str:
        return self._controlled_body(f"{ci}initial", ib.body, ci)

    # -- Misc statements ----------------------------------------------------

    def _system_task(self, stmt: SystemTaskCall, ci: str) -> str:
        if stmt.arguments:
            args = ", ".join(emit_expression(a) for a in stmt.arguments)
            return f"{ci}{stmt.task_name}({args});"
        return f"{ci}{stmt.task_name};"

    def _task_enable(self, stmt: TaskEnable, ci: str) -> str:
        if stmt.arguments:
            args = ", ".join(emit_expression(a) for a in stmt.arguments)
            return f"{ci}{stmt.task_name}({args});"
        return f"{ci}{stmt.task_name};"

    def _delay_control(self, stmt: DelayControl, ci: str) -> str:
        result = f"{ci}#{emit_expression(stmt.delay)} "
        if stmt.body:
            result += self._stmt(stmt.body, "").lstrip()
        return result

    def _event_control(self, stmt: EventControl, ci: str) -> str:
        if stmt.events:
            result = f"{ci}@({self._sensitivity_list(stmt.events)}) "
        else:
            result = f"{ci}@(*) "
        if stmt.body:
            result += self._stmt(stmt.body, "").lstrip()
        return result

    def _wait_stmt(self, stmt: WaitStatement, ci: str) -> str:
        result = f"{ci}wait ({emit_expression(stmt.condition)}) "
        if stmt.body:
            result += self._stmt(stmt.body, "").lstrip()
        else:
            result += ";"
        return result

    # -- Function / Task emission -------------------------------------------

    def _function_decl(self, fn: FunctionDecl, ci: str) -> str:
        inner = ci + self._ind
        parts = [f"{ci}function"]
        if fn.is_automatic:
            parts.append("automatic")
        if fn.return_kind:
            parts.append(fn.return_kind)
        elif fn.return_range:
            parts.append(self._range(fn.return_range))
        parts.append(fn.name)

        lines: list[str] = []
        if fn.ports:
            port_strs = [self._port_str(p) for p in fn.ports]
            lines.append(f"{' '.join(parts)} ({', '.join(port_strs)});")
        else:
            lines.append(" ".join(parts) + ";")

        if fn.body:
            lines.append(self._stmt(fn.body, inner))
        lines.append(f"{ci}endfunction")
        return "\n".join(lines)

    def _task_decl(self, tk: TaskDecl, ci: str) -> str:
        inner = ci + self._ind
        parts = [f"{ci}task"]
        if tk.is_automatic:
            parts.append("automatic")
        parts.append(tk.name)

        lines: list[str] = []
        if tk.ports:
            port_strs = [self._port_str(p) for p in tk.ports]
            lines.append(f"{' '.join(parts)} ({', '.join(port_strs)});")
        else:
            lines.append(" ".join(parts) + ";")

        if tk.body:
            lines.append(self._stmt(tk.body, inner))
        lines.append(f"{ci}endtask")
        return "\n".join(lines)

    # -- Generate constructs ------------------------------------------------

    def _generate_construct(self, gen: VerilogNode, ci: str) -> str:
        if isinstance(gen, GenvarDecl):
            return f"{ci}genvar {', '.join(gen.names)};"
        if isinstance(gen, GenerateFor):
            return self._generate_for(gen, ci)
        if isinstance(gen, GenerateIf):
            return self._generate_if(gen, ci)
        if isinstance(gen, GenerateCase):
            return self._generate_case(gen, ci)
        return f"{ci}/* unknown generate construct */;"

    def _generate_for(self, gen: GenerateFor, ci: str) -> str:
        init = f"{gen.genvar} = {emit_expression(gen.init_value)}"
        cond = emit_expression(gen.condition)
        update = f"{gen.genvar} = {emit_expression(gen.update)}" if gen.update is not None else gen.genvar
        control = f"{ci}for ({init}; {cond}; {update})"
        lines = [control]
        lines.append(self._generate_block(gen.body, ci))
        return "\n".join(lines)

    def _generate_if(self, gen: GenerateIf, ci: str) -> str:
        lines = [f"{ci}if ({emit_expression(gen.condition)})"]
        if gen.then_body:
            lines.append(self._generate_block(gen.then_body, ci))
        else:
            lines.append(f"{ci};")
        if gen.else_body:
            lines.append(f"{ci}else")
            lines.append(self._generate_block(gen.else_body, ci))
        return "\n".join(lines)

    def _generate_case(self, gen: GenerateCase, ci: str) -> str:
        inner = ci + self._ind
        lines = [f"{ci}case ({emit_expression(gen.expression)})"]
        for item in gen.items:
            lines.append(self._generate_case_item(item, inner))
        lines.append(f"{ci}endcase")
        return "\n".join(lines)

    def _generate_case_item(self, item: GenerateCaseItem, ci: str) -> str:
        if item.is_default:
            label = f"{ci}default:"
        else:
            values = ", ".join(emit_expression(v) for v in item.values)
            label = f"{ci}{values}:"
        if item.body:
            return f"{label} {self._generate_block(item.body, ci).lstrip()}"
        return f"{label} ;"

    def _generate_block(self, block: GenerateBlock, ci: str) -> str:
        inner = ci + self._ind
        if block.name or len(block.items) != 1:
            name_sfx = f" : {block.name}" if block.name else ""
            lines = [f"{ci}begin{name_sfx}"]
            for item in block.items:
                lines.append(self._generate_block_item(item, inner))
            lines.append(f"{ci}end")
            return "\n".join(lines)
        elif block.items:
            return self._generate_block_item(block.items[0], inner)
        return f"{ci};"

    def _generate_block_item(self, item: VerilogNode, ci: str) -> str:  # noqa: PLR0911
        if isinstance(item, Net):
            return self._net(item, ci)
        if isinstance(item, Variable):
            return self._variable(item, ci)
        if isinstance(item, ContinuousAssign):
            return self._continuous_assign(item, ci)
        if isinstance(item, Instance):
            return self._instance(item, ci)
        if isinstance(item, Parameter):
            return self._localparam(item, ci)
        if isinstance(item, AlwaysBlock):
            return self._always_block(item, ci)
        if isinstance(item, InitialBlock):
            return self._initial_block(item, ci)
        if isinstance(item, (GenerateFor, GenerateIf, GenerateCase, GenvarDecl)):
            return self._generate_construct(item, ci)
        return f"{ci}/* unknown generate item */;"

    # -- Specify block ------------------------------------------------------

    def _specify_block(self, sb: SpecifyBlock, ci: str) -> str:
        if sb.source_text:
            raw_lines = sb.source_text.splitlines()
            if not raw_lines:
                return f"{ci}specify\n{ci}endspecify"
            result_lines: list[str] = []
            for line in raw_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("specify") or stripped.startswith("endspecify"):
                    result_lines.append(f"{ci}{stripped}")
                else:
                    result_lines.append(f"{ci}  {stripped}")
            return "\n".join(result_lines)

        from lark import Token as LarkToken
        from lark import Tree as LarkTree

        def _gather_tokens(tree: LarkTree) -> list[str]:
            tokens: list[str] = []
            for child in tree.children:
                if isinstance(child, LarkToken):
                    tokens.append(str(child))
                elif isinstance(child, LarkTree):
                    tokens.extend(_gather_tokens(child))
            return tokens

        inner_parts: list[str] = []
        inner_indent = ci + "  "
        for child in sb.raw_tree.children:
            if isinstance(child, LarkToken):
                continue
            elif isinstance(child, LarkTree):
                tokens = _gather_tokens(child)
                if tokens:
                    inner_parts.append(f"{inner_indent}{' '.join(tokens)} ;")

        lines = [f"{ci}specify"]
        lines.extend(inner_parts)
        lines.append(f"{ci}endspecify")
        return "\n".join(lines)

    # -- Instance emission with column-limit wrapping -----------------------

    def _instance(self, inst: Instance, ci: str) -> str:
        parts = [f"{ci}{inst.module_name}"]

        if inst.has_parameter_override:
            param_str = self._parameter_bindings(inst.parameter_bindings)
            parts.append(f" #({param_str})")

        parts.append(f" {inst.instance_name}")
        if inst.instance_array:
            parts.append(f" {self._range(inst.instance_array)}")

        conn_str = self._port_connections(inst.port_connections)
        parts.append(f" ({conn_str});")

        one_line = "".join(parts)
        limit = self.style.column_limit

        if limit and len(one_line) > limit:
            return self._instance_wrapped(inst, ci)
        return one_line

    def _instance_wrapped(self, inst: Instance, ci: str) -> str:
        """Emit an instance with port connections on separate lines."""
        inner = ci + self._ind
        parts = [f"{ci}{inst.module_name}"]

        if inst.has_parameter_override:
            pb_strs = self._parameter_binding_list(inst.parameter_bindings)
            if len(pb_strs) > 1:
                parts.append(" #(\n")
                for i, pb in enumerate(pb_strs):
                    sep = "," if i < len(pb_strs) - 1 else ""
                    parts.append(f"{inner}{pb}{sep}\n")
                parts.append(")")
            elif len(pb_strs) == 1:
                parts.append(f" #({pb_strs[0]})")
            else:
                parts.append(" #()")

        parts.append(f" {inst.instance_name}")
        if inst.instance_array:
            parts.append(f" {self._range(inst.instance_array)}")

        pc_strs = self._port_connection_list(inst.port_connections)
        parts.append(" (\n")
        for i, pc in enumerate(pc_strs):
            sep = "," if i < len(pc_strs) - 1 else ""
            parts.append(f"{inner}{pc}{sep}\n")
        parts.append(f"{ci});")

        return "".join(parts)

    # -- Simple declaration helpers (thin wrappers) -------------------------

    @staticmethod
    def _port_str(port: Port) -> str:
        parts = [port.direction.value]
        if port.net_type:
            parts.append(port.net_type)
        if port.data_type:
            parts.append(port.data_type)
        if port.signed:
            parts.append("signed")
        if port.width:
            parts.append(f"[{emit_expression(port.width.msb)}:{emit_expression(port.width.lsb)}]")
        parts.append(port.name)
        if port.default_value:
            parts.append("=")
            parts.append(emit_expression(port.default_value))
        return " ".join(parts)

    @staticmethod
    def _range(r: Range) -> str:
        return f"[{emit_expression(r.msb)}:{emit_expression(r.lsb)}]"

    def _localparam(self, param: Parameter, ci: str) -> str:
        parts = [f"{ci}localparam"]
        if param.param_type:
            parts.append(param.param_type)
        if param.signed:
            parts.append("signed")
        if param.width:
            parts.append(self._range(param.width))
        parts.append(param.name)
        if param.default_value:
            parts.append("=")
            parts.append(emit_expression(param.default_value))
        return " ".join(parts) + ";"

    def _parameter_port(self, param: Parameter) -> str:
        parts = ["parameter"]
        if param.param_type:
            parts.append(param.param_type)
        if param.signed:
            parts.append("signed")
        if param.width:
            parts.append(self._range(param.width))
        parts.append(param.name)
        if param.default_value:
            parts.append("=")
            parts.append(emit_expression(param.default_value))
        return " ".join(parts)

    def _net(self, net: Net, ci: str) -> str:
        parts = [f"{ci}{net.kind.value}"]
        if net.signed:
            parts.append("signed")
        if net.width:
            parts.append(self._range(net.width))
        parts.append(net.name)
        if net.dimensions:
            for dim in net.dimensions:
                parts.append(self._range(dim))
        if net.initial_value:
            parts.append("=")
            parts.append(emit_expression(net.initial_value))
        return " ".join(parts) + ";"

    def _variable(self, var: Variable, ci: str) -> str:
        parts = [f"{ci}{var.kind.value}"]
        if var.signed:
            parts.append("signed")
        if var.width:
            parts.append(self._range(var.width))
        parts.append(var.name)
        if var.dimensions:
            for dim in var.dimensions:
                parts.append(self._range(dim))
        if var.initial_value:
            parts.append("=")
            parts.append(emit_expression(var.initial_value))
        return " ".join(parts) + ";"

    def _continuous_assign(self, ca: ContinuousAssign, ci: str) -> str:
        return f"{ci}assign {emit_expression(ca.lhs)} = {emit_expression(ca.rhs)};"

    # -- Sensitivity / expression helpers -----------------------------------

    @staticmethod
    def _sensitivity_list(edges: list[SensitivityEdge]) -> str:
        parts: list[str] = []
        for edge in edges:
            if edge.edge == "level":
                parts.append(emit_expression(edge.signal))
            else:
                parts.append(f"{edge.edge} {emit_expression(edge.signal)}")
        return " or ".join(parts)

    @staticmethod
    def _expr(expr: object) -> str:
        """Shorthand for :func:`emit_expression`."""
        return emit_expression(expr)  # type: ignore[arg-type]

    # -- Comment / attribute helpers ----------------------------------------

    @staticmethod
    def _comment_text(c: Comment) -> str:
        if c.kind == "block":
            return f"/* {c.text} */"
        return f"// {c.text}"

    def _leading_comments(self, node: VerilogNode, prefix: str = "") -> list[str]:
        result: list[str] = []
        for c in node.comments:
            if c.position == "leading":
                result.append(f"{prefix}{self._comment_text(c)}")
        return result

    def _trailing_comment(self, node: VerilogNode) -> str | None:
        for c in node.comments:
            if c.position == "trailing":
                return self._comment_text(c)
        return None

    @staticmethod
    def _attributes(node: VerilogNode, prefix: str = "") -> list[str]:
        if not node.attributes:
            return []
        parts: list[str] = []
        for k, v in node.attributes.items():
            if v is not None:
                parts.append(f'{k} = "{v}"')
            else:
                parts.append(k)
        return [f"{prefix}(* {', '.join(parts)} *)"]

    # -- Binding helpers ----------------------------------------------------

    @staticmethod
    def _parameter_bindings(bindings: list[ParameterBinding]) -> str:
        strs: list[str] = []
        for b in bindings:
            if b.name is not None:
                val = emit_expression(b.value) if b.value is not None else ""
                strs.append(f".{b.name}({val})")
            else:
                strs.append(emit_expression(b.value) if b.value is not None else "")
        return ", ".join(strs)

    @staticmethod
    def _parameter_binding_list(bindings: list[ParameterBinding]) -> list[str]:
        strs: list[str] = []
        for b in bindings:
            if b.name is not None:
                val = emit_expression(b.value) if b.value is not None else ""
                strs.append(f".{b.name}({val})")
            else:
                strs.append(emit_expression(b.value) if b.value is not None else "")
        return strs

    @staticmethod
    def _port_connections(connections: list[PortConnection]) -> str:
        strs: list[str] = []
        for c in connections:
            if c.is_named:
                expr = emit_expression(c.expression) if c.expression is not None else ""
                strs.append(f".{c.port_name}({expr})")
            else:
                strs.append(emit_expression(c.expression) if c.expression is not None else "")
        return ", ".join(strs)

    @staticmethod
    def _port_connection_list(connections: list[PortConnection]) -> list[str]:
        strs: list[str] = []
        for c in connections:
            if c.is_named:
                expr = emit_expression(c.expression) if c.expression is not None else ""
                strs.append(f".{c.port_name}({expr})")
            else:
                strs.append(emit_expression(c.expression) if c.expression is not None else "")
        return strs
