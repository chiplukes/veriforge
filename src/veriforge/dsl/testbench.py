"""Testbench wrapper generation for DSL modules.

Auto-generates a Verilog testbench module that wraps a DUT (device under test),
providing:
  - ``reg`` declarations for every DUT input
  - ``wire`` declarations for every DUT output/inout
  - DUT instantiation with all ports connected
  - Clock generation for detected clock signals
  - Reset sequence for detected reset signals
  - VCD dump setup (optional)
  - Configurable timeout with ``$finish``
  - Placeholder stimulus section

Usage::

    from veriforge.dsl import Module, posedge
    from veriforge.dsl.testbench import generate_testbench
    from veriforge.codegen import emit_module

    # Build or parse your DUT
    dut = Module("counter")
    clk = dut.input("clk")
    rst = dut.input("rst")
    count = dut.output_reg("count", width=8)
    with dut.always(posedge(clk)):
        with dut.if_(rst):
            count <<= 0
        with dut.else_():
            count <<= count + 1

    # Generate testbench
    tb = generate_testbench(dut.build())
    print(emit_module(tb))
"""

from __future__ import annotations

import re
from typing import Any

from ..model.design import Module as ModelModule
from ..model.expressions import Literal
from ..model.ports import Port, PortDirection
from ..sim.endpoints import detect_axi_lite_interfaces, detect_axi_stream_interfaces

from . import Expr, Module, sim_time

# Common patterns for auto-detecting clock and reset signals
_CLOCK_PATTERNS = re.compile(r"^(clk|clock|clk_\w+|sys_clk|pclk|aclk|mclk)$", re.IGNORECASE)
_RESET_PATTERNS = re.compile(r"^(rst|reset|rst_n|reset_n|rstn|arst|arst_n|rst_\w+)$", re.IGNORECASE)
_ACTIVE_LOW_RESET = re.compile(r"_n$|n$", re.IGNORECASE)


def _port_width_int(port: Port) -> int:
    """Extract the integer bit width from a Port's Range, or 1 if scalar."""
    if port.width is None:
        return 1
    msb = port.width.msb
    lsb = port.width.lsb
    # DSL-built ports: Range(Literal(N-1), Literal(0))
    if isinstance(msb, Literal) and isinstance(lsb, Literal):
        return int(msb.value) - int(lsb.value) + 1
    # Parametric: Range(BinaryOp("-", param, Literal(1)), Literal(0))
    # Return 0 to indicate "unknown / parametric"
    return 0


def _is_clock(name: str) -> bool:
    """Heuristic: is this port name likely a clock?"""
    return bool(_CLOCK_PATTERNS.match(name))


def _is_reset(name: str) -> bool:
    """Heuristic: is this port name likely a reset?"""
    return bool(_RESET_PATTERNS.match(name))


def _is_active_low_reset(name: str) -> bool:
    """Heuristic: is this reset active-low (ends in _n or n)?"""
    return bool(_ACTIVE_LOW_RESET.search(name))


def _time_expr() -> Expr:
    """Create a $time system function call expression."""
    return sim_time()


def generate_testbench(  # noqa: PLR0912, PLR0913, PLR0915
    dut: ModelModule,
    *,
    tb_name: str | None = None,
    instance_name: str = "uut",
    clock_period: int = 10,
    reset_duration: int = 20,
    timeout: int = 1000,
    vcd: bool = True,
    vcd_filename: str | None = None,
) -> ModelModule:
    """Generate a testbench module that wraps *dut*.

    Args:
        dut:            The built DUT module (model.Module).
        tb_name:        Testbench module name (default: ``tb_<dut.name>``).
        instance_name:  DUT instance name (default: ``"uut"``).
        clock_period:   Clock period in time units (default: 10).
        reset_duration: How long to hold reset active (default: 20).
        timeout:        Simulation timeout — ``$finish`` after this many units (default: 1000).
        vcd:            Whether to generate VCD dump code (default: True).
        vcd_filename:   VCD output file name (default: ``"<tb_name>.vcd"``).

    Returns:
        Built testbench model.Module (ready for ``emit_module()``).

    The generated testbench includes:
      - ``reg`` for every DUT input, ``wire`` for every DUT output/inout
      - Clock generation (``always #half clk = ~clk``) for detected clocks
      - Reset sequence for detected reset signals
      - VCD dump setup (``$dumpfile`` / ``$dumpvars``)
      - Timeout watchdog (``$finish`` after *timeout* time units)
      - Placeholder comments for user stimulus
    """
    if tb_name is None:
        tb_name = f"tb_{dut.name}"
    if vcd_filename is None:
        vcd_filename = f"{tb_name}.vcd"

    tb = Module(tb_name)

    # --- Classify and declare signals ---
    clocks: list[str] = []
    resets: list[tuple[str, bool]] = []  # (name, active_low)
    port_map: dict[str, object] = {}  # port_name -> tb Signal

    for port in dut.ports:
        w = _port_width_int(port)
        width = w if w > 0 else 1

        if port.direction == PortDirection.INPUT:
            comment = None
            if _is_clock(port.name):
                clocks.append(port.name)
                comment = "Clock"
            elif _is_reset(port.name):
                active_low = _is_active_low_reset(port.name)
                resets.append((port.name, active_low))
                comment = "Reset (active-low)" if active_low else "Reset"
            sig = tb.reg(port.name, width=width)
            if comment:
                sig.comment(comment)
        else:
            sig = tb.wire(port.name, width=width)

        port_map[port.name] = sig

    # --- DUT instantiation ---
    tb.instance(dut.name, instance_name, ports=port_map)

    # --- Clock generation ---
    if clocks:
        half_period = clock_period // 2
        for clk_name in clocks:
            clk_sig = port_map[clk_name]
            with tb.initial():
                clk_sig @= 0
            with tb.always():
                tb.delay(half_period)
                clk_sig @= ~clk_sig

    # --- VCD dump ---
    if vcd:
        with tb.initial():
            tb._system_task("$dumpfile", (vcd_filename,))
            tb._system_task("$dumpvars", (0,))

    # --- Timeout watchdog ---
    with tb.initial():
        tb.delay(timeout)
        tb.display("ERROR: Simulation timeout at %0t", _time_expr())
        tb.finish()

    # --- Reset and stimulus ---
    with tb.initial():
        # Initialize all inputs
        for port in dut.ports:
            if port.direction == PortDirection.INPUT and not _is_clock(port.name):
                sig = port_map[port.name]
                if _is_reset(port.name):
                    active_low = _is_active_low_reset(port.name)
                    sig @= 0 if active_low else 1  # Assert reset
                else:
                    sig @= 0

        # Release reset after duration
        if resets:
            tb.delay(reset_duration)
            for rst_name, active_low in resets:
                rst_sig = port_map[rst_name]
                rst_sig @= 1 if active_low else 0  # De-assert reset
            tb.display("Reset released at %0t", _time_expr())

        # Placeholder for user stimulus
        tb.delay(clock_period * 2)
        tb.display("--- Add your stimulus here ---")

        # End simulation
        tb.delay(timeout - reset_duration - clock_period * 2)
        tb.display("Test complete at %0t", _time_expr())
        tb.finish()

    return tb.build()


def generate_python_testbench(  # noqa: PLR0912, PLR0913, PLR0915
    dut: ModelModule,
    *,
    function_name: str = "run_smoke_test",
    clock_period: int = 10,
    clock_max_time: int = 1000,
    reset_release_time: int = 22,
    axis_timeout_steps: int = 40,
    enhanced: bool = False,
    style: str = "legacy",
    dut_source_path: str | None = None,
    dut_dependency_paths: list[str] | None = None,
    plan: object = None,
    overrides: object = None,
    strict: bool = True,
    design: object = None,
    engine: str = "reference",
    cosim: bool = False,
) -> str:
    """Generate a Python testbench skeleton for a DUT module.

    The generated code is a starting point for Python-driven simulation using
    the in-process simulator and endpoint helpers. It is intentionally small and
    focuses on clock/reset scaffolding plus minimal AXI/AXIS transactions when
    detectable.

    When ``enhanced=True`` (or when ``plan`` is provided), the generator emits
    a multi-domain skeleton derived from a :class:`TestbenchPlan`: per-domain
    clock scheduling, per-domain reset, interfaces grouped by their owning
    clock domain, and a leading docstring summarizing the inferred plan.
    The default (``enhanced=False``) path remains byte-identical for backward
    compatibility with existing golden tests.

    When ``style='bench'`` (combined with ``enhanced=True``), the generator
    emits a scaffold built on the higher-level :class:`Testbench` framework
    instead of the legacy ``Simulator`` + ``step_drive`` style: per-interface
    proxy stubs (``bench.iface(...).put(...)`` / ``.expect(...)``), inferred
    ``iface_layouts`` (elements_per_beat / element_size_bits derived from
    TKEEP / TDATA widths), an ``argparse --vcd`` flag, and a
    ``with bench.run(vcd=...):`` block.
    """
    if enhanced or plan is not None:
        if plan is None:
            from ..sim.bench.planner import build_plan  # noqa: PLC0415

            plan = build_plan(dut, overrides=overrides, strict=strict, design=design)
        if style == "bench":
            return _render_bench_testbench(
                dut,
                plan,
                function_name=function_name,
                dut_source_path=dut_source_path,
                dut_dependency_paths=dut_dependency_paths,
                engine=engine,
                cosim=cosim,
            )
        return _render_enhanced_testbench(
            dut,
            plan,
            function_name=function_name,
            clock_period=clock_period,
            clock_max_time=clock_max_time,
            reset_release_time=reset_release_time,
            axis_timeout_steps=axis_timeout_steps,
        )

    clocks = [port.name for port in dut.ports if port.direction == PortDirection.INPUT and _is_clock(port.name)]
    resets = [
        (port.name, _is_active_low_reset(port.name))
        for port in dut.ports
        if port.direction == PortDirection.INPUT and _is_reset(port.name)
    ]
    axis_interfaces = detect_axi_stream_interfaces(dut)
    axi_lite_interfaces = detect_axi_lite_interfaces(dut)

    imports = [
        "from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until",
        "from veriforge.sim.testbench import Clock, Simulator",
    ]

    endpoint_names: list[str] = []
    if axis_interfaces:
        endpoint_names.extend(["AXIStreamFrame", "AXIStreamSink", "AXIStreamSource", "EndpointCoordinator"])
    if axi_lite_interfaces:
        endpoint_names.append("AXILiteMaster")
    if endpoint_names:
        unique_names = ", ".join(sorted(set(endpoint_names)))
        imports.append(f"from veriforge.sim.endpoints import {unique_names}")

    lines: list[str] = [
        '"""Auto-generated Python testbench skeleton."""',
        "",
        *imports,
        "",
        "",
        "def _settle_drives(sim: Simulator, engine: str) -> None:",
        '    if engine == "reference":',
        "        sim.run(max_time=0)",
        "    else:",
        "        step_eval_now(sim)",
        "",
        "",
        'def _make_sim(module, *, design=None, engine: str = "reference") -> Simulator:',
        "    sim = Simulator(module, engine=engine, design=design)",
        "    sim.run(max_time=0)",
    ]

    init_ports = [port.name for port in dut.ports if port.direction == PortDirection.INPUT]
    if init_ports:
        lines.extend(
            [
                "    for signal_name in [",
                *[f'        "{name}",' for name in init_ports],
                "    ]:",
                "        step_drive(sim, engine, signal_name, 0)",
                "    _settle_drives(sim, engine)",
            ]
        )

    if clocks:
        lines.extend(
            [
                f'    sim._schedule_clock_events(Clock(sim.signal("{clocks[0]}"), period={clock_period}), {clock_max_time})',
                "    _settle_drives(sim, engine)",
            ]
        )

    if resets:
        first_reset, active_low = resets[0]
        assert_level = 0 if active_low else 1
        release_level = 1 - assert_level
        lines.extend(
            [
                f'    step_drive(sim, engine, "{first_reset}", {assert_level})',
                "    _settle_drives(sim, engine)",
                f"    step_run_until(sim, {reset_release_time})",
                f'    step_drive(sim, engine, "{first_reset}", {release_level})',
                "    _settle_drives(sim, engine)",
            ]
        )
    else:
        lines.append("    # No reset port was detected; initialize DUT inputs as needed.")

    lines.extend(
        [
            "    return sim",
            "",
            "",
            f'def {function_name}(module, *, design=None, engine: str = "reference") -> None:',
            "    sim = _make_sim(module, design=design, engine=engine)",
        ]
    )

    axis_source_prefixes = [bundle.prefix for bundle in axis_interfaces if bundle.role == "slave"]
    axis_sink_prefixes = [bundle.prefix for bundle in axis_interfaces if bundle.role == "master"]
    axi_master_prefixes = [bundle.prefix for bundle in axi_lite_interfaces if bundle.role == "slave"]

    if axis_source_prefixes:
        lines.extend(
            [
                "    axis_sources = {",
                *[f'        "{prefix}": AXIStreamSource(sim, "{prefix}"),' for prefix in axis_source_prefixes],
                "    }",
            ]
        )
    if axis_sink_prefixes:
        lines.extend(
            [
                "    axis_sinks = {",
                *[f'        "{prefix}": AXIStreamSink(sim, "{prefix}"),' for prefix in axis_sink_prefixes],
                "    }",
            ]
        )
    if axis_source_prefixes or axis_sink_prefixes:
        lines.extend(
            [
                "    coordinator = EndpointCoordinator(sim, [*axis_sources.values(), *axis_sinks.values()])",
            ]
        )

    if axi_master_prefixes:
        lines.extend(
            [
                "    axi_lite_masters = {",
                *[f'        "{prefix}": AXILiteMaster(sim, "{prefix}"),' for prefix in axi_master_prefixes],
                "    }",
            ]
        )

    if axis_source_prefixes and axis_sink_prefixes:
        source_prefix = axis_source_prefixes[0]
        sink_prefix = axis_sink_prefixes[0]
        lines.extend(
            [
                "",
                "    # AXI-Stream example transaction",
                f'    axis_sources["{source_prefix}"].send(AXIStreamFrame(data=[0x11, 0x22, 0x33]))',
                f'    coordinator.run_until(lambda: axis_sinks["{sink_prefix}"].count() == 1, max_steps={axis_timeout_steps}, message="AXIS frame receipt")',
                f'    frame = axis_sinks["{sink_prefix}"].recv()',
                '    print("Received AXIS frame:", frame)',
            ]
        )
    elif axis_source_prefixes or axis_sink_prefixes:
        lines.extend(
            [
                "",
                "    # AXI-Stream interface(s) detected, but only one direction is present.",
                "    # Add the matching source or sink endpoint from your test environment here.",
            ]
        )

    if axi_master_prefixes:
        axi_prefix = axi_master_prefixes[0]
        lines.extend(
            [
                "",
                "    # AXI-Lite example transactions",
                f'    value = axi_lite_masters["{axi_prefix}"].read(0x0)',
                '    print(f"Read register 0: {value:#x}")',
                f'    axi_lite_masters["{axi_prefix}"].write(0x0, 0x12345678)',
            ]
        )

    if not axis_interfaces and not axi_lite_interfaces:
        lines.extend(
            [
                "",
                "    # No AXI-Stream or AXI-Lite interfaces were detected.",
                "    # Add direct sim.drive()/sim.read() stimulus here.",
            ]
        )

    lines.extend(
        [
            "",
            "    # Add assertions and extra transactions here.",
        ]
    )

    if cosim and dut_source_path is not None:
        src_repr = repr(str(dut_source_path))
        deps_repr = "[" + ", ".join(repr(str(d)) for d in (dut_dependency_paths or [])) + "]"
        lines += [
            "",
            "",
            "# Cross-validation against Icarus Verilog (requires iverilog + vvp on PATH)",
            f"_DUT_PATH = {src_repr}",
            f"_DEPS: list[str] = {deps_repr}",
            "",
            "",
            "def validate_with_icarus(module, *, max_time: int = 1000) -> None:",
            '    """Validate all engines against Icarus Verilog.',
            "",
            "    Generates a Verilog wrapper testbench and compares each available",
            "    simulator engine's VCD output against the Icarus reference.",
            "    Requires iverilog and vvp on PATH.",
            '    """',
            "    import tempfile",
            "    from pathlib import Path",
            "    from veriforge.codegen import emit_module",
            "    from veriforge.dsl.testbench import generate_testbench",
            "    from veriforge.sim.cosim import IcarusCosim, find_icarus",
            "",
            "    if find_icarus() is None:",
            '        print("Skipping cosim validation: iverilog not found on PATH.")',
            "        return",
            "",
            "    tb_model = generate_testbench(module, timeout=max_time)",
            "    tb_verilog = emit_module(tb_model)",
            "",
            "    with tempfile.TemporaryDirectory() as tmpdir:",
            '        tb_path = str(Path(tmpdir) / (tb_model.name + ".v"))',
            '        Path(tb_path).write_text(tb_verilog, encoding="utf-8")',
            "        cosim = IcarusCosim(",
            "            files=[*_DEPS, _DUT_PATH, tb_path],",
            "            top_module=tb_model.name,",
            "        )",
            "        results = cosim.run_all_engines(max_time=max_time)",
            "",
            "    diffs = [d for r in results.values() for d in r.diffs]",
            "    if diffs:",
            "        for d in diffs:",
            "            print(d)",
            '        raise AssertionError(f"Cosim: {len(diffs)} differences vs Icarus")',
            '    print("Cosim OK -", list(results.keys()), "match Icarus for", repr(module.name))',
        ]

    return "\n".join(lines) + "\n"


def _render_enhanced_testbench(  # noqa: PLR0912, PLR0913, PLR0915
    dut: ModelModule,
    plan: object,
    *,
    function_name: str,
    clock_period: int,
    clock_max_time: int,
    reset_release_time: int,
    axis_timeout_steps: int,
) -> str:
    """Render a multi-domain Python testbench skeleton from a TestbenchPlan."""
    domains = list(plan.domains)
    real_domains = [d for d in domains if d.name != "__combinational__"]
    bindings = list(plan.interfaces)

    # ── Imports ────────────────────────────────────────────────
    imports = [
        "from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until",
        "from veriforge.sim.testbench import Clock, Simulator",
    ]

    has_axis = any(b.protocol == "axi_stream" for b in bindings)
    has_axi_lite = any(b.protocol == "axi_lite" for b in bindings)

    endpoint_names: list[str] = []
    if has_axis:
        endpoint_names.extend(["AXIStreamFrame", "AXIStreamSink", "AXIStreamSource", "EndpointCoordinator"])
    if has_axi_lite:
        endpoint_names.append("AXILiteMaster")
    if endpoint_names:
        unique_names = ", ".join(sorted(set(endpoint_names)))
        imports.append(f"from veriforge.sim.endpoints import {unique_names}")

    # ── Header docstring (plan summary) ────────────────────────
    summary_lines = plan.summary().splitlines() if hasattr(plan, "summary") else []
    docstring = ['"""Auto-generated Python testbench skeleton (enhanced multi-domain).', ""]
    docstring.append("Plan summary:")
    docstring.extend(f"  {line}" for line in summary_lines)
    docstring.append('"""')

    lines: list[str] = [
        *docstring,
        "",
        *imports,
        "",
        "",
        "def _settle_drives(sim: Simulator, engine: str) -> None:",
        '    if engine == "reference":',
        "        sim.run(max_time=0)",
        "    else:",
        "        step_eval_now(sim)",
        "",
        "",
        'def _make_sim(module, *, design=None, engine: str = "reference") -> Simulator:',
        "    sim = Simulator(module, engine=engine, design=design)",
        "    sim.run(max_time=0)",
    ]

    # ── Initialize all input ports to zero ─────────────────────
    init_ports = [port.name for port in dut.ports if port.direction == PortDirection.INPUT]
    if init_ports:
        lines.extend(
            [
                "    for signal_name in [",
                *[f'        "{name}",' for name in init_ports],
                "    ]:",
                "        step_drive(sim, engine, signal_name, 0)",
                "    _settle_drives(sim, engine)",
            ]
        )

    # ── Per-domain clock scheduling ────────────────────────────
    for d in real_domains:
        period = d.clock.period_hint if d.clock.period_hint is not None else clock_period
        lines.extend(
            [
                f"    # domain {d.name!r}: clock {d.clock.name!r}",
                f'    sim._schedule_clock_events(Clock(sim.signal("{d.clock.name}"), period={period}), {clock_max_time})',
                "    _settle_drives(sim, engine)",
            ]
        )

    # ── Per-domain reset assertion ─────────────────────────────
    for d in real_domains:
        if d.reset is None:
            lines.append(f"    # domain {d.name!r}: no reset detected")
            continue
        active_low = bool(d.reset.active_low)
        assert_level = 0 if active_low else 1
        release_level = 1 - assert_level
        polarity_label = "active_low" if active_low else "active_high"
        lines.append(f"    # domain {d.name!r}: reset {d.reset.name!r} ({polarity_label})")
        lines.append(f'    step_drive(sim, engine, "{d.reset.name}", {assert_level})')
        lines.append("    _settle_drives(sim, engine)")
        lines.append(f"    step_run_until(sim, {reset_release_time})")
        lines.append(f'    step_drive(sim, engine, "{d.reset.name}", {release_level})')
        lines.append("    _settle_drives(sim, engine)")

    if not real_domains:
        lines.append("    # No clock domains were detected; initialize DUT inputs as needed.")

    lines.extend(
        [
            "    return sim",
            "",
            "",
            f'def {function_name}(module, *, design=None, engine: str = "reference") -> None:',
            "    sim = _make_sim(module, design=design, engine=engine)",
        ]
    )

    # ── Endpoints grouped by domain ────────────────────────────
    any_axis_pair_per_domain: dict[str, tuple[str | None, str | None]] = {}

    for d in domains:
        domain_bindings = [b for b in bindings if b.domain_name == d.name]
        if not domain_bindings:
            continue

        axis_sources = [b.prefix for b in domain_bindings if b.protocol == "axi_stream" and b.role == "slave"]
        axis_sinks = [b.prefix for b in domain_bindings if b.protocol == "axi_stream" and b.role == "master"]
        axi_masters = [b.prefix for b in domain_bindings if b.protocol == "axi_lite" and b.role == "slave"]

        var_suffix = _identifier(d.name)
        if axis_sources:
            lines.extend(
                [
                    "",
                    f"    # domain {d.name!r}: AXI-Stream sources",
                    f"    axis_sources_{var_suffix} = {{",
                    *[f'        "{p}": AXIStreamSource(sim, "{p}"),' for p in axis_sources],
                    "    }",
                ]
            )
        if axis_sinks:
            lines.extend(
                [
                    "",
                    f"    # domain {d.name!r}: AXI-Stream sinks",
                    f"    axis_sinks_{var_suffix} = {{",
                    *[f'        "{p}": AXIStreamSink(sim, "{p}"),' for p in axis_sinks],
                    "    }",
                ]
            )
        if axis_sources or axis_sinks:
            ep_parts: list[str] = []
            if axis_sources:
                ep_parts.append(f"*axis_sources_{var_suffix}.values()")
            if axis_sinks:
                ep_parts.append(f"*axis_sinks_{var_suffix}.values()")
            lines.append(f"    coord_{var_suffix} = EndpointCoordinator(sim, [{', '.join(ep_parts)}])")

        if axi_masters:
            lines.extend(
                [
                    "",
                    f"    # domain {d.name!r}: AXI-Lite masters",
                    f"    axi_lite_masters_{var_suffix} = {{",
                    *[f'        "{p}": AXILiteMaster(sim, "{p}"),' for p in axi_masters],
                    "    }",
                ]
            )

        any_axis_pair_per_domain[d.name] = (
            axis_sources[0] if axis_sources else None,
            axis_sinks[0] if axis_sinks else None,
        )

    # ── Example transactions ───────────────────────────────────
    emitted_example = False
    for d in domains:
        src, snk = any_axis_pair_per_domain.get(d.name, (None, None))
        if src and snk:
            var_suffix = _identifier(d.name)
            lines.extend(
                [
                    "",
                    f"    # AXI-Stream example transaction on domain {d.name!r}",
                    f'    axis_sources_{var_suffix}["{src}"].send(AXIStreamFrame(data=[0x11, 0x22, 0x33]))',
                    f'    coord_{var_suffix}.run_until(lambda: axis_sinks_{var_suffix}["{snk}"].count() == 1, '
                    f'max_steps={axis_timeout_steps}, message="AXIS frame receipt on {d.name}")',
                    f'    frame = axis_sinks_{var_suffix}["{snk}"].recv()',
                    f'    print("Received AXIS frame on {d.name}:", frame)',
                ]
            )
            emitted_example = True

    if has_axi_lite:
        # Find the first AXI-Lite master across domains for an example.
        for d in domains:
            domain_bindings = [b for b in bindings if b.domain_name == d.name]
            axi_masters = [b.prefix for b in domain_bindings if b.protocol == "axi_lite" and b.role == "slave"]
            if axi_masters:
                var_suffix = _identifier(d.name)
                first = axi_masters[0]
                lines.extend(
                    [
                        "",
                        f"    # AXI-Lite example transactions on domain {d.name!r}",
                        f'    value = axi_lite_masters_{var_suffix}["{first}"].read(0x0)',
                        '    print(f"Read register 0: {value:#x}")',
                        f'    axi_lite_masters_{var_suffix}["{first}"].write(0x0, 0x12345678)',
                    ]
                )
                emitted_example = True
                break

    if not bindings:
        lines.extend(
            [
                "",
                "    # No AXI-Stream or AXI-Lite interfaces were detected.",
                "    # Add direct sim.drive()/sim.read() stimulus here.",
            ]
        )
    elif not emitted_example:
        lines.extend(
            [
                "",
                "    # Interfaces detected, but no complete source/sink pair was available for a demo.",
                "    # Add the matching endpoint from your test environment here.",
            ]
        )

    if plan.warnings:
        lines.append("")
        lines.append("    # Planner warnings:")
        lines.extend(f"    #   - {w}" for w in plan.warnings)

    lines.extend(
        [
            "",
            "    # Add assertions and extra transactions here.",
        ]
    )

    return "\n".join(lines) + "\n"


def _infer_axis_layout(
    binding: object,
    port_widths: dict[str, int],
) -> dict[str, object] | None:
    """Infer ``{elements_per_beat, element_size_bits, endian}`` for an AXIS bundle.

    Heuristic:
      * ``elements_per_beat`` = TKEEP width if TKEEP is present; otherwise 1.
      * ``element_size_bits`` = TDATA width / elements_per_beat (must divide evenly).
      * Endianness defaults to ``"little"`` (the framework default); overridable
        in the generated source.

    Returns ``None`` when the layout matches the framework's auto-inferred default
    (1 element per beat, element_size_bits == TDATA width, little endian) so the
    generator can omit a redundant override entry.
    """
    signals = binding.signals  # type: ignore[attr-defined]
    tdata_port = signals.get("tdata")
    if not tdata_port:
        return None
    tdata_width = port_widths.get(tdata_port, 0)
    if tdata_width <= 0:
        return None

    tkeep_port = signals.get("tkeep")
    tkeep_width = port_widths.get(tkeep_port, 0) if tkeep_port else 0

    if tkeep_width > 1 and tdata_width % tkeep_width == 0:
        epb = tkeep_width
        esb = tdata_width // tkeep_width
    else:
        epb = 1
        esb = tdata_width

    # Skip when this matches the framework default (epb=1, esb=tdata_width).
    if epb == 1 and esb == tdata_width:
        return None
    return {"elements_per_beat": epb, "element_size_bits": esb, "endian": "little"}


def _is_natively_lowerable(binding: object) -> bool:
    """Return True if this binding can be driven by an engine-native lowering."""
    proto = getattr(binding, "protocol", "")
    role = getattr(binding, "role", "")
    if proto == "axi_stream":
        return True
    if proto == "axi_lite":
        return True
    if proto == "axi4" and role == "master":
        return True
    return False


def _infer_data_width(binding: object, port_widths: dict[str, int], default: int = 8) -> int:
    """Infer data bus width from the binding's port signals."""
    signals = getattr(binding, "signals", {})
    for key in ("tdata", "wdata", "rdata"):
        port_name = signals.get(key)
        if port_name:
            w = port_widths.get(port_name, 0)
            if w > 0:
                return w
    return default


def _infer_addr_width(binding: object, port_widths: dict[str, int], default: int = 32) -> int:
    """Infer address bus width from the binding's port signals."""
    signals = getattr(binding, "signals", {})
    for key in ("awaddr", "araddr"):
        port_name = signals.get(key)
        if port_name:
            w = port_widths.get(port_name, 0)
            if w > 0:
                return w
    return default


def _native_build_bench_lines(
    module_name: str,
    bindings: list[Any],
    port_widths: dict[str, int],
) -> list[str]:
    axis_slaves = [b for b in bindings if b.protocol == "axi_stream" and b.role == "slave"]
    axis_masters = [b for b in bindings if b.protocol == "axi_stream" and b.role == "master"]
    axi_lite_slaves = [b for b in bindings if b.protocol == "axi_lite" and b.role == "slave"]
    axi_lite_masters = [b for b in bindings if b.protocol == "axi_lite" and b.role == "master"]
    axi4_masters = [b for b in bindings if b.protocol == "axi4" and b.role == "master"]
    iface_domains = {b.prefix: b.domain_name for b in bindings}

    lines: list[str] = [
        "",
        "",
        "def build_native_bench() -> LoweredDesign:",
        '    """Construct and compile the engine-native testbench."""',
        "    design, dut = parse_dut()",
        "    overrides = PlannerOverrides(",
    ]
    if iface_domains:
        lines.append("        iface_domains={")
        for prefix, dom in iface_domains.items():
            lines.append(f"            {prefix!r}: {dom!r},")
        lines.append("        },")
    lines.append("    )")
    lines.append("    bench = Testbench(dut, design=design, overrides=overrides)")
    lines.append("    lowerings = {")

    for b in axis_slaves:
        dw = _infer_data_width(b, port_widths, default=8)
        lines += [
            f"        # {b.prefix!r}: DUT-slave AXIS input — bench drives as source",
            f"        {b.prefix!r}: AXIStreamSourceLowering(",
            "            beats=[0x00, 0x01, 0x02, 0x03],  # TODO: replace with real stimulus",
            f"            data_width={dw},",
            "        ),",
        ]

    for b in axis_masters:
        dw = _infer_data_width(b, port_widths, default=8)
        lines += [
            f"        # {b.prefix!r}: DUT-master AXIS output — bench captures beats",
            f"        {b.prefix!r}: AXIStreamSinkLowering(",
            "            n_beats=4,  # TODO: set to expected number of output beats",
            f"            data_width={dw},",
            "        ),",
        ]

    for b in axi_lite_slaves:
        dw = _infer_data_width(b, port_widths, default=32)
        aw = _infer_addr_width(b, port_widths, default=32)
        lines += [
            f"        # {b.prefix!r}: DUT-slave AXI-Lite — bench issues scripted ops",
            f"        {b.prefix!r}: AXILiteMasterLowering(",
            "            operations=[",
            "                AXILiteOp.write(addr=0x00, data=0xDEAD_BEEF),  # TODO: real ops",
            "                AXILiteOp.read(addr=0x00),",
            "            ],",
            f"            data_width={dw},",
            f"            addr_width={aw},",
            "        ),",
        ]

    for b in axi_lite_masters:
        dw = _infer_data_width(b, port_widths, default=32)
        lines += [
            f"        # {b.prefix!r}: DUT-master AXI-Lite — bench acts as memory-backed slave",
            f"        {b.prefix!r}: AXILiteSlaveLowering(",
            "            memory_depth=16,  # TODO: set to required address space in words",
            f"            data_width={dw},",
            "        ),",
        ]

    for b in axi4_masters:
        dw = _infer_data_width(b, port_widths, default=32)
        lines += [
            f"        # {b.prefix!r}: DUT-master AXI4 — bench acts as memory-backed slave",
            f"        {b.prefix!r}: AXI4SlaveLowering(",
            "            memory_depth=64,  # TODO: set to required address space in words",
            f"            data_width={dw},",
            "        ),",
        ]

    lines += [
        "    }",
        "    return compile_native(bench, lowerings=lowerings)",
    ]
    return lines


def _native_run_and_main_lines(
    module_name: str,
    function_name: str,
    plan: object,
    bindings: list[Any],
    port_widths: dict[str, int],
    *,
    engine: str,
    cosim: bool,
) -> list[str]:
    axis_masters = [b for b in bindings if b.protocol == "axi_stream" and b.role == "master"]
    axi_lite_slaves = [b for b in bindings if b.protocol == "axi_lite" and b.role == "slave"]
    axi4_masters = [b for b in bindings if b.protocol == "axi4" and b.role == "master"]

    lines: list[str] = [
        "",
        "",
        f"def run_bench(engine: str = {engine!r}, *, vcd: Path | None = None) -> dict[str, int]:",
        f'    """Run the engine-native {module_name!r} testbench and return captured results.',
        "",
        "    Clock scheduling, reset sequencing, VCD attachment, and simulation are",
        "    all handled automatically by ``lowered.run()``.",
        '    """',
        "    lowered = build_native_bench()",
        "    results = lowered.run(engine, vcd=vcd)",
    ]

    capture_ifaces = axis_masters + axi_lite_slaves + axi4_masters
    if capture_ifaces:
        lines += [
            "",
            "    # ── Inspect captured results ─────────────────────────────────────────────",
        ]
        for b in axis_masters:
            dw = _infer_data_width(b, port_widths, default=8)
            hex_w = dw // 4
            lines += [
                f"    for i, name in enumerate(lowered.capture_signals.get({b.prefix!r}, [])):",
                "        val = results[name]",
                f'        print(f"{b.prefix} beat[{{i}}] = 0x{{val:0{hex_w}x}}")',
            ]
        for b in axi_lite_slaves:
            dw = _infer_data_width(b, port_widths, default=32)
            hex_w = dw // 4
            lines += [
                f"    for i, name in enumerate(lowered.capture_signals.get({b.prefix!r}, [])):",
                "        val = results[name]",
                f'        print(f"{b.prefix} mem[{{i}}] = 0x{{val:0{hex_w}x}}")',
            ]
        for b in axi4_masters:
            dw = _infer_data_width(b, port_widths, default=32)
            hex_w = dw // 4
            lines += [
                f"    for i, name in enumerate(lowered.capture_signals.get({b.prefix!r}, [])):",
                "        val = results[name]",
                f'        print(f"{b.prefix} mem[{{i}}] = 0x{{val:0{hex_w}x}}")',
            ]

    if plan.warnings:  # type: ignore[attr-defined]
        lines.append("")
        lines.append("    # Planner warnings:")
        lines.extend(f"    #   - {w}" for w in plan.warnings)  # type: ignore[attr-defined]

    lines.append("    return results")

    if cosim:
        lines += [
            "",
            "",
            "def validate_with_icarus(*, max_time: int = 1000) -> None:",
            '    """Validate all engines against Icarus Verilog.',
            "",
            "    Generates a Verilog wrapper testbench and compares each available",
            "    simulator engine's VCD output against the Icarus reference.",
            "    Requires iverilog and vvp on PATH.",
            '    """',
            "    import tempfile",
            "    from veriforge.codegen import emit_module",
            "    from veriforge.dsl.testbench import generate_testbench",
            "    from veriforge.sim.cosim import IcarusCosim, find_icarus",
            "",
            "    if find_icarus() is None:",
            '        print("Skipping cosim validation: iverilog not found on PATH.")',
            "        return",
            "",
            "    design, dut = parse_dut()",
            "    tb_model = generate_testbench(dut, timeout=max_time)",
            "    tb_verilog = emit_module(tb_model)",
            "    src_files = [str(f) for f in [*DEPS, DUT_PATH]]",
            "",
            "    with tempfile.TemporaryDirectory() as tmpdir:",
            '        tb_path = str(Path(tmpdir) / (tb_model.name + ".v"))',
            '        Path(tb_path).write_text(tb_verilog, encoding="utf-8")',
            "        cosim = IcarusCosim(",
            "            files=[*src_files, tb_path],",
            "            top_module=tb_model.name,",
            "        )",
            "        results = cosim.run_all_engines(max_time=max_time)",
            "",
            "    diffs = [d for r in results.values() for d in r.diffs]",
            "    if diffs:",
            "        for d in diffs:",
            "            print(d)",
            '        raise AssertionError(f"Cosim: {len(diffs)} differences vs Icarus")',
            '    print("Cosim OK -", list(results.keys()), "match Icarus for", repr(dut.name))',
        ]

    lines += [
        "",
        "",
        f"def {function_name}() -> None:",
        f'    """Auto-generated entry point for the {module_name!r} engine-native testbench."""',
        "    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])",
        "    parser.add_argument(",
        '        "--engine",',
        '        choices=["reference", "vm", "compiled"],',
        f"        default={engine!r},",
        '        help="Simulation engine (default: %(default)s).",',
        "    )",
        "    parser.add_argument(",
        '        "--vcd",',
        "        type=Path,",
        "        default=None,",
        '        help="Optional VCD output path.",',
        "    )",
        "    args = parser.parse_args()",
        "    run_bench(engine=args.engine, vcd=args.vcd)",
        "",
        "",
        'if __name__ == "__main__":',
        f"    {function_name}()",
        "",
    ]
    return lines


def _render_native_bench_testbench(  # noqa: PLR0912, PLR0913, PLR0915
    dut: ModelModule,
    plan: object,
    *,
    function_name: str,
    dut_source_path: str | None,
    dut_dependency_paths: list[str] | None = None,
    engine: str = "compiled",
    cosim: bool = False,
) -> str:
    """Render an engine-native compile_native testbench scaffold from a TestbenchPlan.

    When all detected interfaces are natively lowerable, this emits a scaffold
    that calls ``compile_native()`` and runs at vm/compiled engine speed instead
    of the Python-stepped Testbench framework.
    """
    bindings = list(plan.interfaces)  # type: ignore[attr-defined]
    port_widths = {p.name: _port_width_int(p) for p in dut.ports}
    module_name = dut.name

    axis_slaves = [b for b in bindings if b.protocol == "axi_stream" and b.role == "slave"]
    axis_masters = [b for b in bindings if b.protocol == "axi_stream" and b.role == "master"]
    axi_lite_slaves = [b for b in bindings if b.protocol == "axi_lite" and b.role == "slave"]
    axi_lite_masters = [b for b in bindings if b.protocol == "axi_lite" and b.role == "master"]
    axi4_masters = [b for b in bindings if b.protocol == "axi4" and b.role == "master"]

    # Build selective lowering imports
    lowering_imports: list[str] = []
    if axis_slaves:
        lowering_imports.append("    AXIStreamSourceLowering,")
    if axis_masters:
        lowering_imports.append("    AXIStreamSinkLowering,")
    if axi_lite_slaves:
        lowering_imports.append("    AXILiteMasterLowering,")
        lowering_imports.append("    AXILiteOp,")
    if axi_lite_masters:
        lowering_imports.append("    AXILiteSlaveLowering,")
    if axi4_masters:
        lowering_imports.append("    AXI4SlaveLowering,")

    # Docstring
    summary_lines = plan.summary().splitlines() if hasattr(plan, "summary") else []
    docstring: list[str] = [
        '"""Auto-generated engine-native testbench scaffold.',
        "",
        "Uses compile_native() to lower interfaces into hardware DSL for",
        "simulation at vm/compiled engine speed.",
        "",
        "Edit the TODO markers below with your stimulus and depth limits.",
        "",
        "Plan summary:",
    ]
    docstring.extend(f"  {line}" for line in summary_lines)
    docstring.append('"""')

    lines: list[str] = [*docstring, ""]
    lines += [
        "from __future__ import annotations",
        "",
        "import argparse",
        "from pathlib import Path",
        "",
        "from veriforge.project import parse_file, parse_files",
        "from veriforge.sim.bench import (",
        *lowering_imports,
        "    LoweredDesign,",
        "    PlannerOverrides,",
        "    Testbench,",
        "    compile_native,",
        ")",
        "",
    ]

    # DUT loader
    if dut_source_path:
        if dut_dependency_paths:
            lines.append(f"DUT_PATH = Path(r{dut_source_path!r})")
            lines.append("DEPS = [")
            for dep in dut_dependency_paths:
                lines.append(f"    Path(r{dep!r}),")
            lines.append("]")
            lines += [
                "",
                "",
                "def parse_dut():",
                '    """Parse the DUT module (with dependency files) from disk."""',
                "    design = parse_files([*DEPS, DUT_PATH])",
                f"    return design, design.get_module({module_name!r})",
            ]
        else:
            lines += [
                f"DUT_PATH = Path(r{dut_source_path!r})",
                "DEPS: list[Path] = []",
                "",
                "",
                "def parse_dut():",
                '    """Parse the DUT module from disk."""',
                "    design = parse_file(DUT_PATH)",
                f"    return design, design.get_module({module_name!r})",
            ]
    else:
        lines += [
            "# TODO: point this at the Verilog source file for the DUT.",
            'DUT_PATH = Path("path/to/your_dut.v")',
            "# TODO: list child-module source files required to elaborate the DUT.",
            "DEPS: list[Path] = []",
            "",
            "",
            "def parse_dut():",
            '    """Parse the DUT module from disk."""',
            "    design = parse_files([*DEPS, DUT_PATH]) if DEPS else parse_file(DUT_PATH)",
            f"    return design, design.get_module({module_name!r})",
        ]

    lines.extend(_native_build_bench_lines(module_name, bindings, port_widths))
    lines.extend(
        _native_run_and_main_lines(
            module_name,
            function_name,
            plan,
            bindings,
            port_widths,
            engine=engine,
            cosim=cosim,
        )
    )

    return "\n".join(lines)


def _bench_iface_stub_lines(bindings: list[Any]) -> list[str]:
    """Return per-interface stub function definitions for the bench-style testbench."""
    axis_slaves = [b for b in bindings if b.protocol == "axi_stream" and b.role == "slave"]
    axis_masters = [b for b in bindings if b.protocol == "axi_stream" and b.role == "master"]
    axi_lite_slaves = [b for b in bindings if b.protocol == "axi_lite" and b.role == "slave"]
    axi_lite_masters = [b for b in bindings if b.protocol == "axi_lite" and b.role == "master"]
    axi4_slaves = [b for b in bindings if b.protocol == "axi4" and b.role == "slave"]
    axi4_masters = [b for b in bindings if b.protocol == "axi4" and b.role == "master"]
    stream_slaves = [b for b in bindings if b.protocol == "stream" and b.role == "slave"]
    stream_masters = [b for b in bindings if b.protocol == "stream" and b.role == "master"]
    membus_slaves = [b for b in bindings if b.protocol == "membus" and b.role == "slave"]
    membus_masters = [b for b in bindings if b.protocol == "membus" and b.role == "master"]

    lines: list[str] = []

    for b in axis_slaves:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def drive_{ident}(bench: Testbench) -> list[list[int]]:",
                f'    """Drive the {b.prefix!r} AXI-Stream input (DUT-slave).',
                "",
                f"    Domain: {b.domain_name!r}",
                "",
                "    Returns the list of frames that were queued so that checkers can",
                "    derive expected output from the same stimulus data.",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # Optional: add random source gaps (hold tvalid low ~25% of cycles).",
                "    # iface.pause = PauseGenerator(1, 4)",
                f"    # TODO: replace with real stimulus for {b.prefix!r}.",
                "    # tlast=1 is set on the last beat automatically (override with last=[...] if needed).",
                "    # Other sideband kwargs: dest=..., tid=..., user=..., last_user=..., keep=...",
                "    frames = [[0x00, 0x01, 0x02, 0x03]]",
                "    for f in frames:",
                "        iface.put(f)",
                "    return frames",
            ]
        )

    for b in axis_masters:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def expect_{ident}(bench: Testbench, sent: list[list[int]]) -> None:",
                f'    """Read and check the {b.prefix!r} AXI-Stream output (DUT-master).',
                "",
                f"    Domain: {b.domain_name!r}",
                "",
                "    Args:",
                "        sent: frames that were queued on the source interface(s). Used to",
                "            compute expected output — replace the identity transform below",
                "            with whatever function this DUT applies to the data.",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # Optional: add random back-pressure (hold tready low ~25% of cycles).",
                "    # iface.pause = PauseGenerator(1, 4)",
                "    for sent_frame in sent:",
                "        # TODO: transform sent_frame into the expected output if the DUT modifies data.",
                "        # Examples:",
                "        #   passthrough:    expected = sent_frame",
                "        #   byte packer:    expected = pack_pixels(sent_frame)",
                "        #   frame splitter: call get() multiple times per sent_frame",
                "        expected = sent_frame  # replace with real transform",
                "        frame = iface.get(timeout=200)",
                "        assert list(frame.data) == expected, (",
                f'            f"received {b.prefix}: {{list(frame.data)!r}}, expected {{expected!r}}"',
                "        )",
                f'        print(f"received {b.prefix}:", list(frame.data))',
            ]
        )

    for b in axi_lite_slaves:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def axi_lite_{ident}(bench: Testbench) -> None:",
                f'    """Issue AXI-Lite transactions to {b.prefix!r}.',
                "",
                f"    Domain: {b.domain_name!r}",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # TODO: replace with real register accesses.",
                "    iface.write(0x0, 0xDEADBEEF)",
                "    value = iface.read(0x0)",
                f'    print(f"{b.prefix} reg[0] = 0x{{value:08x}}")',
            ]
        )

    for b in axi4_slaves:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def axi4_{ident}(bench: Testbench) -> None:",
                f'    """Issue AXI4 burst transactions to {b.prefix!r}.',
                "",
                f"    Domain: {b.domain_name!r}",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # TODO: replace with real burst stimulus.",
                "    # Single-beat write then 4-beat INCR read-back.",
                "    iface.write(0x0, [0xDEADBEEF])",
                "    beats = iface.read(0x0, length=1)",
                f'    print(f"{b.prefix} read[0] = 0x{{beats[0]:08x}}")',
            ]
        )

    for b in axi_lite_masters:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def axi_lite_resp_{ident}(bench: Testbench) -> None:",
                f'    """Install an AXI-Lite responder for {b.prefix!r} (DUT master).',
                "",
                f"    Domain: {b.domain_name!r}",
                "",
                "    The responder is created on first ``bench.iface(...)`` call and",
                "    auto-services AW/W/AR transactions in the background. Seed or",
                "    inspect via ``iface.memory`` and ``iface.write_log`` /",
                "    ``iface.read_log``.",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # TODO: optionally seed memory or queue specific responses.",
                "    # iface.memory[0x0] = 0xDEADBEEF",
                "    # iface.queue_read_response(0xCAFEBABE)",
                "    _ = iface",
            ]
        )

    for b in axi4_masters:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def axi4_resp_{ident}(bench: Testbench) -> None:",
                f'    """Install an AXI4 responder for {b.prefix!r} (DUT master).',
                "",
                f"    Domain: {b.domain_name!r}",
                "",
                "    The responder is created on first ``bench.iface(...)`` call and",
                "    auto-services AW/W/AR/burst transactions in the background.",
                "    Seed or inspect via ``iface.memory``.",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # TODO: optionally seed memory.",
                "    # iface.memory[0x0] = 0xDEADBEEF",
                "    _ = iface",
            ]
        )

    for b in stream_slaves:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def drive_{ident}(bench: Testbench) -> None:",
                f'    """Drive the {b.prefix!r} ready/valid stream input (DUT-slave).',
                "",
                f"    Domain: {b.domain_name!r}",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # Optional: add random source gaps (hold valid low ~25% of cycles).",
                "    # iface.pause = PauseGenerator(1, 4)",
                f"    # TODO: replace with real stimulus for {b.prefix!r}.",
                "    # Pass `sideband={'name': value, ...}` to drive extra bundle signals.",
                "    iface.write([0x00, 0x01, 0x02, 0x03])",
            ]
        )

    for b in stream_masters:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def expect_{ident}(bench: Testbench) -> None:",
                f'    """Read and check the {b.prefix!r} ready/valid stream output (DUT-master).',
                "",
                f"    Domain: {b.domain_name!r}",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # Optional: add random back-pressure (hold ready low ~25% of cycles).",
                "    # iface.pause = PauseGenerator(1, 4)",
                "    # TODO: replace with `iface.expect_sequence([...])` for hard checks.",
                "    for _ in range(4):",
                "        data, sideband = iface.get(timeout=200)",
                f'        print(f"received {b.prefix}: 0x{{data:x}} sideband={{sideband}}")',
            ]
        )

    for b in membus_slaves:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def drive_{ident}(bench: Testbench) -> None:",
                f'    """Drive the {b.prefix!r} memory-bus slave (bench writes/reads the DUT).',
                "",
                f"    Domain: {b.domain_name!r}",
                '    """',
                f"    iface = bench.iface({b.prefix!r})",
                "    # TODO: replace with real address/data values.",
                "    iface.write(0x0, 0xDEAD_BEEF)",
                "    value = iface.read(0x0)",
                f'    print(f"{b.prefix} mem[0] = 0x{{value:08x}}")',
            ]
        )

    for b in membus_masters:
        ident = _identifier(b.prefix)
        lines.extend(
            [
                "",
                "",
                f"def start_{ident}_responder(bench: Testbench) -> None:",
                f'    """Activate the auto-responder for {b.prefix!r} (DUT drives the memory bus).',
                "",
                f"    Domain: {b.domain_name!r}",
                "    The responder auto-ticks; call this once before starting stimulus.",
                '    """',
                f"    # Access bench.iface({b.prefix!r}) to materialise the responder.",
                f"    _iface = bench.iface({b.prefix!r})",
                "    # Pre-populate memory if desired:",
                "    # _iface.memory[0x00] = 0xDEAD_BEEF",
                f'    print(f"{b.prefix} responder ready.")',
            ]
        )

    return lines


def _bench_main_body_lines(  # noqa: PLR0912
    module_name: str,
    function_name: str,
    dut: ModelModule,
    plan: object,
    bindings: list[Any],
    *,
    cosim: bool,
) -> list[str]:
    """Return the main() function body, cosim section, and __main__ guard."""
    axis_slaves = [b for b in bindings if b.protocol == "axi_stream" and b.role == "slave"]
    axis_masters = [b for b in bindings if b.protocol == "axi_stream" and b.role == "master"]
    axi_lite_slaves = [b for b in bindings if b.protocol == "axi_lite" and b.role == "slave"]
    axi_lite_masters = [b for b in bindings if b.protocol == "axi_lite" and b.role == "master"]
    axi4_slaves = [b for b in bindings if b.protocol == "axi4" and b.role == "slave"]
    axi4_masters = [b for b in bindings if b.protocol == "axi4" and b.role == "master"]
    stream_slaves = [b for b in bindings if b.protocol == "stream" and b.role == "slave"]
    stream_masters = [b for b in bindings if b.protocol == "stream" and b.role == "master"]
    membus_slaves = [b for b in bindings if b.protocol == "membus" and b.role == "slave"]
    membus_masters = [b for b in bindings if b.protocol == "membus" and b.role == "master"]

    lines: list[str] = [
        "",
        "",
        f"def {function_name}() -> None:",
        f'    """Auto-generated entry point for the {module_name!r} testbench."""',
        "    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])",
        "    parser.add_argument(",
        '        "--vcd",',
        "        type=Path,",
        f"        default=Path({(module_name + '.vcd')!r}),",
        '        help="VCD output path (default: %(default)s). Set to None in source to disable.",',
        "    )",
        "    parser.add_argument(",
        '        "--engine",',
        '        default="reference",',
        '        choices=["reference", "vm", "vm-fast"],',
        "        help=(",
        '            "Simulation engine (default: %(default)s). "',
        "            \"'vm' is ~5x faster; 'vm-fast' ~10x (requires Cython build). \"",
        '            "For native speed regenerate with: veriforge generate-python-testbench --engine compiled"',
        "        ),",
        "    )",
        "    args = parser.parse_args()",
        "",
        "    bench = build_bench(engine=args.engine)",
        '    print("Discovered testbench plan:\\n")',
        "    print(bench.plan.summary())",
        "    print()",
        "",
        "    with bench.run(vcd=args.vcd):",
        '        print(f"VCD tracing -> {args.vcd}\\n")',
        "        bench.reset_all()",
        "",
        "        # TODO: orchestrate stimulus across domains.",
    ]

    if not (
        axis_slaves
        or axis_masters
        or axi_lite_slaves
        or axi_lite_masters
        or axi4_slaves
        or axi4_masters
        or stream_slaves
        or stream_masters
        or membus_slaves
        or membus_masters
    ):
        lines.append("        # No protocol bundles were detected; drive raw signals via bench.sim.")
        clock_reset_names: set[str] = set()
        for dom in plan.domains:  # type: ignore[attr-defined]
            clock_reset_names.add(dom.clock.name)
            if dom.reset is not None:
                clock_reset_names.add(dom.reset.name)
        input_ports = [p for p in dut.input_ports() if p.name not in clock_reset_names]
        output_ports = [p for p in dut.output_ports() if p.name not in clock_reset_names]
        if input_ports:
            lines.append("        # Drive DUT inputs (example — replace with real values):")
            for p in input_ports[:4]:
                lines.append(f"        bench.sim.signal({p.name!r}).value = 0")
        if output_ports:
            lines.append("        bench.step(1)")
            lines.append("        # Sample DUT outputs (example):")
            for p in output_ports[:4]:
                lines.append(f"        print({p.name!r}, '=', bench.sim.signal({p.name!r}).value)")

    for b in axi_lite_masters:
        lines.append(f"        axi_lite_resp_{_identifier(b.prefix)}(bench)")
    for b in axi4_masters:
        lines.append(f"        axi4_resp_{_identifier(b.prefix)}(bench)")

    if axis_slaves:
        # Declare per-source accumulators that collect what was sent.
        for b in axis_slaves:
            lines.append(f"        sent_{_identifier(b.prefix)}: list[list[int]] = []")
        # Pre-load all input frames (no clock steps yet) and record what was sent.
        # The source drives beats automatically as the sim clock steps inside get().
        lines.extend(
            [
                "        # TODO: set NUM_FRAMES to the number of input packets to send.",
                "        NUM_FRAMES = 1",
                "        for _i in range(NUM_FRAMES):",
                *[
                    f"            sent_{_identifier(b.prefix)}.extend(drive_{_identifier(b.prefix)}(bench))"
                    for b in axis_slaves
                ],
            ]
        )
        # If there are non-AXIS outputs (AXI4, AXI-Lite, membus) that the stimulus
        # feeds into, add a commented cross-check block as a starting point.
        has_non_axis_output = bool(axi4_masters or axi_lite_masters or membus_masters)
        if has_non_axis_output and not axis_masters:
            first_slave_var = f"sent_{_identifier(axis_slaves[0].prefix)}"
            lines.extend(
                [
                    "        # Cross-protocol check: compare sent frames against what arrived at the",
                    "        # non-AXIS output(s). Access the output interface *after* the simulation",
                    "        # has consumed all inputs (use bench.step(N) to let it drain).",
                    "        bench.step(500)  # TODO: tune cycle budget",
                    "        # Example for an AXI4 DMA sink:",
                    f"        # expected_bytes = list(itertools.chain.from_iterable({first_slave_var}))",
                    "        # iface = bench.iface('m_axi')  # TODO: replace with real interface name",
                    "        # for i, b in enumerate(expected_bytes):",
                    "        #     assert iface.memory[BASE_ADDR + i] == b, (",
                    "        #         f'DMA mismatch at offset {i}: got {iface.memory[BASE_ADDR+i]:#x}, want {b:#x}'",
                    "        #     )",
                ]
            )

    if axis_slaves and axis_masters:
        # Wire each master checker to the sent data it needs to derive expected output.
        # For a single source, all masters receive the same sent frames.
        # For multiple sources, adjust which sent_* variable(s) are passed.
        if len(axis_slaves) == 1:
            sent_arg = f"sent_{_identifier(axis_slaves[0].prefix)}"
            for b in axis_masters:
                lines.append(f"        expect_{_identifier(b.prefix)}(bench, {sent_arg})")
        else:
            for b in axis_masters:
                # Default: pass first slave's data. TODO: adjust for your routing logic.
                sent_arg = f"sent_{_identifier(axis_slaves[0].prefix)}"
                lines.append(
                    f"        expect_{_identifier(b.prefix)}(bench, {sent_arg})"
                    "  # TODO: adjust which sent_* to pass for this checker"
                )
    elif axis_masters:
        # Masters with no AXIS source — produced by some other interface.
        for b in axis_masters:
            lines.append(f"        expect_{_identifier(b.prefix)}(bench, [])")

    for b in axi_lite_slaves:
        lines.append(f"        axi_lite_{_identifier(b.prefix)}(bench)")
    for b in axi4_slaves:
        lines.append(f"        axi4_{_identifier(b.prefix)}(bench)")
    for b in stream_slaves:
        lines.append(f"        drive_{_identifier(b.prefix)}(bench)")
    for b in stream_masters:
        lines.append(f"        expect_{_identifier(b.prefix)}(bench)")
    for b in membus_masters:
        lines.append(f"        start_{_identifier(b.prefix)}_responder(bench)")
    for b in membus_slaves:
        lines.append(f"        drive_{_identifier(b.prefix)}(bench)")

    if axis_masters:
        first_master = axis_masters[0]
        lines.extend(
            [
                "",
                "        # Demonstrate that timeouts are domain-local.",
                "        try:",
                f"            bench.iface({first_master.prefix!r}).get(timeout=10)",
                "        except BenchTimeoutError as exc:",
                '            print(f"  caught (as expected): {exc}")',
            ]
        )

    if plan.warnings:  # type: ignore[attr-defined]
        lines.append("")
        lines.append("    # Planner warnings:")
        lines.extend(f"    #   - {w}" for w in plan.warnings)  # type: ignore[attr-defined]

    if cosim:
        lines += [
            "",
            "",
            "def validate_with_icarus(*, max_time: int = 1000) -> None:",
            '    """Validate all engines against Icarus Verilog.',
            "",
            "    Generates a Verilog wrapper testbench and compares each available",
            "    simulator engine's VCD output against the Icarus reference.",
            "    Requires iverilog and vvp on PATH.",
            '    """',
            "    import tempfile",
            "    from veriforge.codegen import emit_module",
            "    from veriforge.dsl.testbench import generate_testbench",
            "    from veriforge.sim.cosim import IcarusCosim, find_icarus",
            "",
            "    if find_icarus() is None:",
            '        print("Skipping cosim validation: iverilog not found on PATH.")',
            "        return",
            "",
            "    design, dut = parse_dut()",
            "    tb_model = generate_testbench(dut, timeout=max_time)",
            "    tb_verilog = emit_module(tb_model)",
        ]
        lines += [
            "    src_files = [str(f) for f in [*DEPS, DUT_PATH]]",
            "",
            "    with tempfile.TemporaryDirectory() as tmpdir:",
            '        tb_path = str(Path(tmpdir) / (tb_model.name + ".v"))',
            '        Path(tb_path).write_text(tb_verilog, encoding="utf-8")',
            "        cosim = IcarusCosim(",
            "            files=[*src_files, tb_path],",
            "            top_module=tb_model.name,",
            "        )",
            "        results = cosim.run_all_engines(max_time=max_time)",
            "",
            "    diffs = [d for r in results.values() for d in r.diffs]",
            "    if diffs:",
            "        for d in diffs:",
            "            print(d)",
            '        raise AssertionError(f"Cosim: {len(diffs)} differences vs Icarus")',
            '    print("Cosim OK -", list(results.keys()), "match Icarus for", repr(dut.name))',
        ]

    lines += [
        "",
        "",
        'if __name__ == "__main__":',
        f"    {function_name}()",
        "",
    ]
    return lines


def _render_bench_testbench(  # noqa: PLR0912, PLR0913, PLR0915
    dut: ModelModule,
    plan: object,
    *,
    function_name: str,
    dut_source_path: str | None,
    dut_dependency_paths: list[str] | None = None,
    engine: str = "reference",
    cosim: bool = False,
) -> str:
    """Render a Testbench-framework Python scaffold from a TestbenchPlan.

    Unlike :func:`_render_enhanced_testbench`, this emits high-level
    ``bench.iface(...).put(...)`` / ``.expect(...)`` calls against
    :class:`veriforge.sim.bench.Testbench`, with inferred
    ``iface_layouts`` and an ``argparse --vcd`` flag.

    When ``engine != "reference"`` and all detected interfaces are natively
    lowerable, delegates to :func:`_render_native_bench_testbench` instead.
    """
    bindings = list(plan.interfaces)  # type: ignore[attr-defined]
    port_widths = {p.name: _port_width_int(p) for p in dut.ports}

    # Engine-native dispatch: when engine != "reference" lower all interfaces natively.
    _non_lowerable: list[object] = []
    if engine != "reference":
        _non_lowerable = [b for b in bindings if not _is_natively_lowerable(b)]
        if not _non_lowerable:
            return _render_native_bench_testbench(
                dut,
                plan,
                function_name=function_name,
                dut_source_path=dut_source_path,
                dut_dependency_paths=dut_dependency_paths,
                engine=engine,
                cosim=cosim,
            )

    # Plan-summary docstring
    summary_lines = plan.summary().splitlines() if hasattr(plan, "summary") else []
    docstring: list[str] = [
        '"""Auto-generated Python testbench scaffold (bench framework).',
        "",
        "Edit the TODO markers below with stimulus and expectations.",
        "",
        "Plan summary:",
    ]
    docstring.extend(f"  {line}" for line in summary_lines)
    docstring.append('"""')

    has_pauseable = any(b.protocol in ("axi_stream", "stream") for b in bindings)

    imports = [
        "from __future__ import annotations",
        "",
        "import argparse",
        "from pathlib import Path",
        "",
        "from veriforge.project import parse_file, parse_files",
        "from veriforge.sim.bench import BenchTimeoutError, PlannerOverrides, Testbench",
    ]
    if has_pauseable:
        imports.append("from veriforge.sim.endpoints import PauseGenerator")

    module_name = dut.name

    # Warning block when engine != "reference" but some interfaces lack native lowerings.
    warn_lines: list[str] = []
    if _non_lowerable:
        non_str = ", ".join(f"{b.prefix!r} ({b.protocol}/{b.role})" for b in _non_lowerable)
        warn_lines = [
            "# NOTE: `--engine` requested native lowering but the following interfaces",
            "# do not have a native lowering yet; using Python Testbench scaffold instead:",
            f"#   - {non_str}",
            "# To accelerate, implement native lowerings for these interfaces.",
            "",
        ]
    lines: list[str] = [*docstring, "", *warn_lines, *imports, ""]

    # ── DUT loader ─────────────────────────────────────────────
    if dut_source_path:
        if dut_dependency_paths:
            lines.append(f"DUT_PATH = Path(r{dut_source_path!r})")
            lines.append("DEPS = [")
            for dep in dut_dependency_paths:
                lines.append(f"    Path(r{dep!r}),")
            lines.append("]")
            lines.extend(
                [
                    "",
                    "",
                    "def parse_dut():",
                    '    """Parse the DUT module (with dependency files) from disk."""',
                    "    design = parse_files([*DEPS, DUT_PATH])",
                    f"    return design, design.get_module({module_name!r})",
                ]
            )
        else:
            lines.extend(
                [
                    f"DUT_PATH = Path(r{dut_source_path!r})",
                    "DEPS: list[Path] = []",
                    "",
                    "",
                    "def parse_dut():",
                    '    """Parse the DUT module from disk."""',
                    "    design = parse_file(DUT_PATH)",
                    f"    return design, design.get_module({module_name!r})",
                ]
            )
    else:
        lines.extend(
            [
                "# TODO: point this at the Verilog source file for the DUT.",
                'DUT_PATH = Path("path/to/your_dut.v")',
                "# TODO: list child-module source files required to elaborate the DUT.",
                "DEPS: list[Path] = []",
                "",
                "",
                "def parse_dut():",
                '    """Parse the DUT module from disk."""',
                "    design = parse_files([*DEPS, DUT_PATH]) if DEPS else parse_file(DUT_PATH)",
                f"    return design, design.get_module({module_name!r})",
            ]
        )

    # ── build_bench(): apply iface_domains + iface_layouts ─────
    iface_domains: dict[str, str] = {b.prefix: b.domain_name for b in bindings}
    iface_layouts: dict[str, dict[str, object]] = {}
    for b in bindings:
        if b.protocol != "axi_stream":
            continue
        layout = _infer_axis_layout(b, port_widths)
        if layout is not None:
            iface_layouts[b.prefix] = layout

    lines.extend(["", "", 'def build_bench(engine: str = "reference") -> Testbench:'])
    lines.append('    """Construct the multi-domain Testbench from the parsed DUT."""')
    lines.append("    design, dut = parse_dut()")
    lines.append("    overrides = PlannerOverrides(")
    if iface_domains:
        lines.append("        iface_domains={")
        for prefix, dom in iface_domains.items():
            lines.append(f"            {prefix!r}: {dom!r},")
        lines.append("        },")
    if iface_layouts:
        lines.append("        iface_layouts={")
        for prefix, layout in iface_layouts.items():
            entries = ", ".join(f"{k!r}: {v!r}" for k, v in layout.items())
            lines.append(f"            {prefix!r}: {{{entries}}},")
        lines.append("        },")
    lines.append("    )")
    lines.append("    return Testbench(dut, design=design, overrides=overrides, engine=engine)")

    lines.extend(_bench_iface_stub_lines(bindings))
    lines.extend(
        _bench_main_body_lines(
            module_name,
            function_name,
            dut,
            plan,
            bindings,
            cosim=cosim,
        )
    )

    return "\n".join(lines)


def _identifier(name: str) -> str:
    """Sanitize a domain name into a Python identifier suffix."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"d_{cleaned}"
    return cleaned
