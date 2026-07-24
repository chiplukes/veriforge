# Python Overview

## Project Structure

```
src/veriforge/
├── __init__.py
├── _version.py           # Single source of truth for __version__; zero imports (safe for isolated builds)
├── __main__.py           # CLI entry point
├── verilog_parser.py     # Main parser class (Layer 1)
├── preprocessor.py       # Verilog preprocessor (`define/`ifdef/`include/`timescale etc.)
├── project.py            # Multi-file project support (parse_file/files/directory, parse cache)
├── scaffold.py           # Testbench scaffold + DSL export (build_testbench, build_testbench_plan, generate_python_testbench_skeleton, export_dsl_project)
├── lark_file/
│   ├── __init__.py
│   ├── gen_tree.py       # Grammar tree visualization
│   ├── parse_metadata.py # Metadata extraction and documentation generation
│   └── verilog.lark      # Verilog grammar (EBNF)
├── model/                # Semantic model classes (Layer 2)
│   ├── __init__.py       # Public API exports
│   ├── base.py           # VerilogNode, SourceLocation, Comment
│   ├── assignments.py    # ContinuousAssign
│   ├── behavioral.py     # AlwaysBlock, InitialBlock, SensitivityType
│   ├── design.py         # Design, Module
│   ├── expressions.py    # Expression hierarchy (15 types + Range)
│   ├── functions.py      # FunctionDecl, TaskDecl
│   ├── generate.py       # GenerateBlock, GenerateFor, GenerateIf, GenerateCase, GenvarDecl
│   ├── instances.py      # Instance, PortConnection, ParameterBinding
│   ├── nets.py           # Net, NetKind
│   ├── parameters.py     # Parameter
│   ├── ports.py          # Port, PortDirection
│   ├── specify.py        # SpecifyBlock (opaque, raw tree + source_text)
│   ├── interface.py      # Interface, Modport, ModportPort (SV interface/modport)
│   ├── package.py        # Package, ImportDecl (SV package/import)
│   ├── statements.py     # Statement hierarchy (18 types)
│   ├── sv_types.py       # EnumMember, EnumType, StructField, StructType, UnionType, TypedefDecl (SystemVerilog)
│   └── variables.py      # Variable, VariableKind
├── analysis/             # Connectivity & analysis (Layer 3)
│   ├── __init__.py       # Public API: analyze_design, Driver, Load, etc.
│   ├── resolver.py       # 4-pass analysis: link, resolve, connect, analyze
│   ├── width_inference.py # IEEE 1364-2005 expression width inference
│   ├── const_fold.py     # Constant folding & parameter evaluation
│   ├── clock_reset.py    # Clock/reset signal extraction from always blocks
│   └── lint.py           # Lint-style checks (8 check codes)
├── transforms/           # Tree-to-model conversion
│   ├── __init__.py
│   ├── tree_to_model.py  # Lark parse tree → model objects
│   ├── _assignments.py   # Shared continuous assignment and lvalue helpers
│   ├── _declarations.py  # Shared declaration/import/parameter/port/net/type helpers
│   ├── _design_builder.py # Shared Design, Module, Interface, Package, and module-item assembly helpers
│   ├── _expressions.py   # Shared expression/genvar dispatch, operator, literal, identifier, select, and call helpers
│   ├── _functions_tasks.py # Shared function/task declaration, port, local variable, and body helpers
│   ├── _generate.py      # Shared genvar and loop/if/case generate construct helpers
│   ├── _instances.py     # Shared module/primitive instance, parameter override, and port connection helpers
│   ├── _statements.py    # Shared always/initial, event-control, sensitivity, and procedural statement helpers
│   ├── _tree_utils.py    # Shared parse-tree location/text/cache helpers
│   └── comment_extractor.py  # Pre-parse comment extraction & attachment
├── refactor/             # Hierarchy/refactor analysis and edit planning
│   ├── __init__.py
│   ├── diagnostics.py    # RefactorDiagnostic
│   ├── hierarchy_collapse.py # Preview-only pure pass-through wrapper collapse edit plans
│   ├── hierarchy_extract.py # Preview-only selected-assignment extract-submodule edit plans
│   ├── hierarchy_boundary.py # Pull-up/push-down boundary movement API (preview contracts)
│   ├── hierarchy_graph.py # Hierarchy graph, wrapper classification, JSON serialization
│   ├── visualization.py  # Text, DOT, and Mermaid hierarchy graph serializers
│   ├── _boundary_models.py    # Boundary-move request/result dataclasses
│   ├── _boundary_selection.py # Selection resolution for boundary moves
│   ├── _boundary_validation.py # Fail-closed validation for boundary moves
│   ├── _boundary_pull_push.py # Shared pull-up/push-down plumbing
│   ├── _pull_up_engine.py     # Pull-up (child → parent) edit-plan engine
│   ├── _push_down_engine.py   # Push-down (parent → child) edit-plan engine
│   ├── _extract_classify.py   # Extract-scope statement classification
│   ├── _extract_models.py     # Extract request/result dataclasses
│   └── _refactor_utils.py     # Shared refactor helpers
└── codegen/              # Model-to-source code generation
    ├── __init__.py
    ├── format_style.py    # FormatStyle dataclass (knr/allman/gnu presets)
    ├── verilog_emitter.py # Model objects → Verilog source text (with comments)
    └── verilog_formatter.py # Style-configurable Verilog formatter
├── convert/              # Format conversion utilities
│   ├── __init__.py
│   └── to_dsl.py         # Model → Python DSL code (Verilog → DSL translator)
├── sim/                  # Simulation engine (Phase 6)
│   ├── __init__.py       # Public API exports
│   ├── value.py          # 4-state Value type (int-pair encoding, type_info slot)
│   ├── evaluator.py      # ExpressionEvaluator + EvalContext (struct field read, memory array access)
│   ├── executor.py       # StatementExecutor (blocking/NBA, struct field write, memory arrays, $readmemh, $dumpfile/$dumpvars)
│   ├── scheduler.py      # EventQueue, Process types, Scheduler (delta cycles, struct registration, memory registration, run_step)
│   ├── event_queue.py    # TimedEvent + EventQueueMixin + CoroutineMixin + SignalDictBase (shared primitives for all engines)
│   ├── elaborate.py      # Generate elaboration, hierarchy flattening, enum/package/struct resolution, interface binding
│   ├── testbench.py      # SignalHandle, Triggers, Clock, Simulator (engine selection)
│   ├── vcd.py            # VCD waveform output (IEEE 1364-2001)
│   ├── vcd_compare.py    # VCD parser + comparator for cross-sim validation
│   ├── cosim.py          # Cross-simulator validation (IcarusCosim, record_vcd)
│   ├── step_harness.py   # step_drive/step_eval_now/step_run_until helpers for stepped simulation on VM/compiled engines
│   ├── trace.py          # Reusable simulation tracing helpers
│   ├── example_runner.py # Shared helpers for example runner scripts
│   └── endpoints/        # Protocol endpoint helpers (Python-side drivers/monitors)
│       ├── __init__.py   # Public API: AXIStreamSource, AXIStreamSink, AXILiteMaster,
│       │                 #   AXILiteResponder, AXI4Master, AXI4Responder, AXI4ResponseError,
│       │                 #   AXILiteResponseError, StreamSource, StreamSink, AXIStreamFrame,
│       │                 #   BeatSizeError, ElementSizeError, MemBusMaster, MemBusResponder,
│       │                 #   EndpointCoordinator, DomainCoordinator, MultiDomainRunner,
│       │                 #   PauseGenerator, DetectedInterface, detect_interfaces,
│       │                 #   detect_axi_stream_interfaces, detect_axi_lite_interfaces,
│       │                 #   detect_axi4_interfaces, detect_stream_interfaces,
│       │                 #   detect_membus_interfaces, InterfaceDetectionError
│       ├── axis_source.py    # AXIStreamSource — drives tvalid/tdata/tlast; supports pause=
│       ├── axis_sink.py      # AXIStreamSink — captures tdata beats; supports pause=
│       ├── axi_lite_master.py  # AXILiteMaster — drives AW/W/B/AR/R channels on DUT slave
│       ├── axi_lite_responder.py  # AXILiteResponder — responds to DUT AXI-Lite master;
│       │                          #   auto-ticks via time-step callback; .memory/.write_log/
│       │                          #   .read_log/.queue_write/.queue_read
│       ├── axi4_master.py    # AXI4Master — burst read/write to DUT AXI4 slave; INCR-burst only
│       ├── axi4_responder.py # AXI4Responder — responds to DUT AXI4 master; .memory dict
│       ├── stream_source.py  # StreamSource — ready/valid source (Pulp-style)
│       ├── stream_sink.py    # StreamSink — ready/valid sink
│       ├── membus_master.py  # MemBusMaster — synchronous SRAM/BRAM-style master;
│       │                     #   .write(addr, data), .read(addr) → int; blocking transactions
│       ├── membus_responder.py  # MemBusResponder — SRAM/BRAM responder; auto-ticks via
│       │                        #   time-step callback; .memory dict; supports be strobes
│       ├── frame.py          # AXIStreamFrame — multi-beat AXIS frame container
│       ├── helpers.py        # EndpointCoordinator, DomainCoordinator, MultiDomainRunner
│       ├── _generator.py     # GeneratorEndpoint — wraps a generator function as a phase-contract
│       │                     #   endpoint; yield marks tick_pre/tick_post boundaries
│       ├── pause.py          # PauseGenerator(num_pause, denom, seed=) or .duty(rate, seed=);
│       │                     #   assign to endpoint.pause for random backpressure simulation
│       ├── detect.py         # detect_interfaces() — infer AXIS/AXI-Lite/AXI4/MemBus/stream
│       │                     #   bundles from flat port names; returns DetectedInterface list
│       ├── axi_lite_common.py      # _AXILiteSignals mixin (shared signal name resolution)
│       ├── axi_lite_request_driver.py  # AXILiteRequestDriver — low-level AW/W/AR driver
│       └── axi_lite_response_driver.py # AXILiteResponseDriver — low-level B/R driver
│   └── vm/               # Bytecode VM engine (high-performance alternative)
│       ├── __init__.py   # Public API: Compiler, Interpreter, VMScheduler, Op
│       ├── opcodes.py        # Op enum (74 opcodes) + instr() helper
│       ├── compiler.py       # AST → bytecode compiler (expression/statement/LHS, struct fields)
│       ├── interpreter.py    # Pure-Python stack-based bytecode interpreter (deferred NBA_RANGE)
│       ├── vm_scheduler.py   # Event-driven scheduler (EventQueueMixin, cascaded CA propagation)
│       └── _interp_fast.pyx  # Cython fast interpreter + C delta loop
│   └── compiled/         # Compiled Cython engine (design-specific .pyx)
│       ├── __init__.py   # Public API: CythonCompiler, CythonCodegen, CompiledScheduler
│       ├── compiler.py       # Runtime .pyx → .pyd/.so compilation + caching
│       ├── codegen.py        # Top-level codegen coordinator; delegates to mixin modules
│       ├── compiled_scheduler.py  # Scheduler adapter (EventQueueMixin, MEM[idx] ctx support)
│       │                          #   _sync_mem_to_ref / _sync_mem_from_ref: memory array sync
│       │                          #   _wire_vcd_from_ref: VCD callback wiring from ref executor
│       ├── _codegen_utils.py     # Shared helpers (indent, signal name mangling, etc.)
│       ├── _expr_emitter.py      # Expression AST → Cython expression string
│       ├── _stmt_emitters.py     # Statement AST → Cython statement list
│       ├── _process_compiler.py  # always/initial process compilation to Cython functions
│       ├── _gen_sections.py      # Top-level module section generators (ports, signals, etc.)
│       ├── _gen_narrow_accessors.py  # Narrow (<= 64-bit) signal accessor code generation
│       ├── _gen_narrow_assign.py     # Narrow signal non-blocking assignment code generation
│       ├── _gen_narrow_stage.py      # Narrow signal staging/NBA commit code generation
│       ├── _gen_narrow_tail.py       # Narrow signal tail (final update) code generation
│       ├── _gen_wide_section.py      # Wide (> 64-bit) signal code generation
│       └── _wide_emitter.py          # Wide signal expression emission helpers
│   └── bench/            # High-level transaction-level testbench DSL (Phase 7+)
│       ├── __init__.py   # Public API: Testbench, Domain, make_bench, AXIStreamProxy,
│       │                 #   AXILiteProxy, AXI4Proxy, MemBusProxy, StreamProxy, BenchTimeoutError,
│       │                 #   TestbenchPlan, build_plan, ClockDomain, ClockSpec, ResetSpec,
│       │                 #   InterfaceBinding, PlanValidationError, PlannerOverrides,
│       │                 #   AmbiguousDomainError, NoDomainError, compile_native,
│       │                 #   LoweredDesign, LoweringError, InterfaceLowering,
│       │                 #   AXIStreamSourceLowering, AXIStreamSinkLowering,
│       │                 #   AXILiteMasterLowering, AXILiteOp, AXILiteSlaveLowering,
│       │                 #   AXI4SlaveLowering
│       ├── plan.py       # TestbenchPlan / ClockDomain / ClockSpec / ResetSpec /
│       │                 #   InterfaceBinding dataclasses + summary()
│       ├── planner.py    # Plan inference from a parsed module (clock/reset/iface
│       │                 #   detection, overrides, strict-mode diagnostics)
│       ├── interfaces.py # Transaction-level proxies + BenchTimeoutError:
│       │                 #   AXIStreamProxy (role-inverted: slave=source, master=sink):
│       │                 #     put(data), put_frame(frame), get(timeout=), wait_drain(timeout=),
│       │                 #     pending(), expect(expected, timeout=), pause=
│       │                 #   AXILiteProxy (DUT-slave, supports role="slave" or role="master"):
│       │                 #     read(addr), write(addr, data), write_then_read(addr, data)
│       │                 #   AXI4Proxy (DUT-slave): read(addr, length), write(addr, data)
│       │                 #   StreamProxy (Pulp ready/valid): put(data), get(timeout=)
│       ├── runtime.py    # Testbench (orchestrates clocks/resets/MultiDomainRunner) +
│       │                 #   Domain (one clock + reset + DomainCoordinator) +
│       │                 #   make_bench factory
│       └── lowering.py   # Engine-native lowering: compile_native(), LoweredDesign,
│                         #   InterfaceLowering protocol, LoweringError,
│                         #   AXIStreamSourceLowering (case-ROM, O(n) C switch;
│                         #     optional PRNG pause: prng_bits/pause_threshold/prng_seed),
│                         #   AXIStreamSinkLowering (captures n beats + done signal;
│                         #     optional PRNG back-pressure: same 3 params),
│                         #   32-bit Galois LFSR helper (_build_lfsr_pause),
│                         #   AXILiteMasterLowering + AXILiteOp (scripted write/read seq.),
│                         #   AXILiteSlaveLowering (memory-backed responder for DUT master),
│                         #   AXI4SlaveLowering (burst responder for DUT AXI4 master);
│                         #   LoweredDesign.run(engine, cycles, vcd_path=) and
│                         #   LoweredDesign.batch_run(cycles) return merged dict of
│                         #   capture_signals + done_signals
├── dsl/                  # Hardware Construction DSL (Phase 7)
│   ├── __init__.py       # Public API: Module, Signal, Expr, Interface, helpers
│   ├── builder.py        # Operator-overloaded Python DSL → model objects
│   ├── interface.py      # Interface / bus grouping abstraction
│   ├── prelude.py        # Star-import convenience module for DSL user code
│   ├── ram.py            # RAM inference pattern library (single/dual-port, ROM)
│   ├── spec.py           # Declarative ModuleSpec layer (__set_name__ port descriptors)
│   ├── testbench.py      # Auto-generate testbench wrappers for DUT modules
│   ├── testbench_deps.py # Auto-discovery of child-module source dependencies for scaffolds
│   └── lib/              # Reusable component library
│       ├── __init__.py   # Re-exports all library components + RAM functions
│       ├── fifo.py       # sync_fifo() — pointer-based FIFO with full/empty/count
│       ├── cdc.py        # synchronizer(), edge_detector() — CDC & edge detection
│       ├── codec.py      # priority_encoder(), binary_decoder() — combinational logic
│       ├── axi_stream.py # axi_stream() interface + axis_register() pipeline reg
│       ├── axi.py        # axi4_lite() interface (all 5 channels, 19 signals)
│       ├── dsp.py        # mac(), pipelined_mult(), fir_filter() — DSP inference
│       └── xilinx.py     # shift_register_srl(), lutram() — Xilinx inference
```

The top-level `veriforge_lsp/` package (language server: `server.py`, `workspace.py`,
`index.py`, `protocol.py`, `handlers/`) is documented separately in
[notes/veriforge_lsp.md](veriforge_lsp.md).

```
tests/
├── __init__.py
├── conftest.py                    # Pytest fixtures and configuration
├── test_verilog_parser/
│   ├── test_all.py                # Original basic tests
│   ├── test_rule_examples.py      # Auto-generated per-rule EXAMPLE tests (337 tests)
│   ├── test_section_a1.py         # A.1 Source text tests
│   ├── test_section_a2.py         # A.2 Declaration tests
│   ├── test_section_a6.py         # A.6 Behavioral statement tests
│   ├── test_section_a8.py         # A.8 Expression tests
│   ├── test_sv_features.py       # SystemVerilog extensions tests (41 tests)
│   └── verilog/
│       ├── v_module1.v            # Simple module test file
│       └── verilog_all.v          # Comprehensive test file
└── test_model/
    ├── __init__.py
    ├── conftest.py                # Model test fixtures (parser)
    ├── test_comments.py           # Comment extraction/attachment/emission tests (21 tests)
    ├── test_instances.py          # Instance, assign, roundtrip tests (37 tests)
    ├── test_module.py             # Module/port/net/var/param tests (38 tests)
    ├── test_roundtrip.py          # Parse→model→emit→re-parse tests (13 tests)
    ├── test_behavioral.py         # Always/initial/statement/emitter tests (39 tests)
    ├── test_functions_generate.py  # Function/task/generate tests (45 tests)
    ├── test_specify.py            # Specify block tests (22 tests)
    ├── test_comment_roundtrip.py   # Comment round-trip tests (11 tests)
    ├── test_corpus.py             # Real-world corpus + iverilog tests (88+ tests)
    └── test_analysis.py           # Connectivity & analysis tests (43 tests)
├── test_sim/                      # Simulation engine tests
│   ├── __init__.py
│   ├── test_value.py              # 4-state Value type tests (97 tests)
│   ├── test_evaluator.py          # Expression evaluator tests (80 tests)
│   ├── test_executor.py           # Statement executor tests (41 tests)
│   ├── test_scheduler.py          # Scheduler/event queue tests (26 tests)
│   ├── test_testbench.py          # Testbench API tests (34 tests)
│   ├── test_vcd.py                # VCD output tests (19 tests)
│   ├── test_vm.py                 # Bytecode VM engine tests (185 tests)
│   ├── test_compiled.py           # Compiled Cython engine tests (107 tests)
│   ├── test_generate.py           # Generate construct elaboration tests (40 tests)
│   ├── test_hierarchy.py          # Hierarchy flattening + hierarchical signal access tests (62 tests)
│   ├── test_function_task.py      # User-defined function/task simulation tests (22 tests)
│   ├── test_memory.py             # Memory array, $readmemh/$readmemb, $dumpfile/$dumpvars tests (18 tests)
│   ├── test_sim_sv.py             # SystemVerilog simulation support tests (55 tests)
│   ├── test_structural_patterns.py  # DarkRISCV structural pattern tests, all 3 engines (192 tests)
│   ├── test_darkriscv_constructs.py # DarkRISCV-inspired construct tests, all 3 engines (251 tests)
│   ├── test_precedence_and_fixes.py # Operator precedence & regression fix tests (68 tests)
│   ├── test_axis_endpoints.py     # AXIStreamSource/Sink endpoint tests
│   ├── test_axis_frame.py         # AXIStreamFrame tests
│   ├── test_axi_lite_master.py    # AXILiteMaster endpoint tests
│   ├── test_interface_detection.py # detect_interfaces() tests
│   ├── test_stream_protocol.py    # StreamSource/Sink (Pulp ready/valid) tests
│   ├── test_bench_plan.py         # TestbenchPlan dataclass tests
│   ├── test_bench_planner.py      # build_plan() inference tests
│   ├── test_bench_runtime.py      # Testbench/Domain/proxy runtime tests
│   ├── test_bench_native.py       # compile_native + all lowerings: AXIS source/sink,
│   │                              #   AXILiteMaster, AXILiteSlave, AXI4Slave (46 tests)
│   ├── test_multi_domain_runner.py # MultiDomainRunner tests
│   ├── test_planner_naming_fallback.py # Planner port-name fallback (clk_i/rst_ni etc.)
│   ├── test_pulp_axi_examples.py  # Pulp AXI integration tests
│   ├── test_pulp_common_cells_examples.py # Pulp common_cells integration tests
│   ├── test_pulp_ready_valid_examples.py  # Pulp ready/valid protocol tests
│   ├── test_ibex_examples.py      # Ibex core integration tests
│   ├── test_value_widths.py       # Value width edge cases
│   ├── test_param_width.py        # Parametric width tests
│   ├── test_wide_signal_catchall.py # Wide signal handling tests
│   ├── test_membus_endpoints.py   # MemBusMaster/Responder/Proxy + detection tests (42 tests)
│   ├── test_combinational_coordinator.py # CombinationalCoordinator (clockless DUT) tests (7 tests)
│   ├── test_coordinator_strict.py # EndpointCoordinator(strict=True) contract tests (14 tests)
│   ├── test_compiled_latent_risks.py    # Compiled-engine latent-risk regression tests (3 tests)
│   └── test_compiled_batch_run_propagation.py # batch_run event-propagation fix regression (3 tests)
├── test_validation/               # 3rd-party simulator cross-validation
│   ├── __init__.py
│   ├── test_iverilog_validation.py # iverilog VCD comparison tests (24 ref + 12 VM-vs-icarus)
│   └── test_vm_vs_reference.py    # VM vs reference engine cross-validation (41 tests)
├── test_dsl/                      # DSL builder tests
│   ├── __init__.py
│   ├── test_builder.py            # DSL builder + integration tests (255 tests)
│   ├── test_examples.py           # DSL example integration tests
│   ├── test_ram.py                # RAM inference pattern tests (33 tests)
│   ├── test_lib_fifo.py           # FIFO library tests (27 tests)
│   ├── test_lib_cdc.py            # CDC/edge detector tests (24 tests)
│   ├── test_lib_codec.py          # Encoder/decoder tests (24 tests)
│   ├── test_lib_axi.py            # AXI-Stream/AXI4-Lite tests (31 tests)
│   ├── test_lib_dsp.py            # DSP inference library tests (33 tests)
│   ├── test_lib_xilinx.py         # Xilinx inference library tests (32 tests)
│   ├── test_builder_errors_m9.py  # Builder error checks M9-M23 (25 tests)
│   ├── test_builder_errors_m24.py # Builder error checks M24-M33 (42 tests)
│   ├── test_sv_interface_emit.py  # SV interface emit mode tests (25 tests)
│   ├── test_testbench.py          # Testbench generator tests (51 tests)
│   ├── test_convert_to_dsl.py     # Verilog → DSL translator tests (106 tests)
│   ├── test_roundtrip_dsl.py      # Verilog → DSL → Verilog round-trip tests (54 tests)
│   ├── test_sv_dsl.py             # SV DSL builder + translator tests (44 tests)
│   ├── test_dsl_boundary.py       # Python/DSL boundary semantics tests (20 tests)
│   ├── test_testbench_bench_style.py # Bench-framework-style testbench generator tests (24 tests)
│   ├── test_testbench_deps.py     # SV dependency-discovery helper + CLI --auto-deps tests (3 tests)
│   ├── test_testbench_enhanced.py # Enhanced multi-domain testbench generator tests (11 tests)
│   ├── test_axi4_mem_example.py   # AXI4 bench-codegen example tests (3 tests)
│   ├── test_axi_cdc_pulp_example.py      # Pulp axi_cdc two-clock-domain CDC bridge tests (2 tests)
│   ├── test_axi_fifo_pulp_example.py     # Pulp AXI FIFO example tests (2 tests)
│   ├── test_axi_lite_dw_pulp_example.py  # Pulp AXI-Lite data-width converter tests (2 tests)
│   ├── test_axi_lite_mailbox_pulp_example.py # Pulp AXI-Lite mailbox tests (3 tests)
│   ├── test_axi_lite_regs_example.py     # Pulp AXI-Lite register-file tests (3 tests)
│   ├── test_axi_lite_to_axi_pulp_example.py  # Pulp AXI-Lite to AXI4 converter tests (2 tests)
│   ├── test_axi_lite_xbar_pulp_example.py    # Pulp AXI-Lite crossbar tests (2 tests)
│   ├── test_axi_to_axi_lite_pulp_example.py  # Pulp AXI4 to AXI-Lite converter tests (2 tests)
│   ├── test_axi_xbar_pulp_example.py     # Pulp AXI4 crossbar tests (2 tests)
│   ├── test_taxi_axil_ram.py      # Taxi axil_ram example tests (3 tests)
│   ├── test_taxi_axis_adapter.py  # Taxi axis_adapter example tests (4 tests)
│   ├── test_taxi_axis_arb_mux.py  # Taxi axis_arb_mux example tests (3 tests)
│   ├── test_taxi_axis_async_fifo.py         # Taxi axis_async_fifo example tests (4 tests)
│   ├── test_taxi_axis_async_fifo_dualclk.py # Taxi dual-clock async FIFO example tests (4 tests)
│   ├── test_taxi_axis_broadcast.py # Taxi axis_broadcast example tests (3 tests)
│   └── test_taxi_axis_register.py # Taxi axis_register example tests (4 tests)
├── test_preprocessor/             # Preprocessor tests
│   ├── __init__.py
│   └── test_preprocessor.py       # `define, `ifdef, `include, `timescale, DarkRISCV patterns (61 tests)
├── test_project/                  # Multi-file project tests
│   ├── __init__.py
│   ├── test_project.py            # Parse file/dir, merge, DSL export, build_testbench, E2E sim tests (87 tests)
│   └── test_darkriscv.py          # DarkRISCV preprocess, parse, simulation tests (24 tests)
├── test_formatter/                # Formatter tests
│   ├── __init__.py
│   └── test_formatter.py         # Style-configurable formatter tests (37 tests)
├── test_analysis/                 # Analysis pass tests
│   ├── __init__.py
│   ├── test_width_inference.py    # Width inference tests (90 tests)
│   ├── test_const_fold.py         # Constant folding tests (102 tests)
│   ├── test_clock_reset.py        # Clock/reset extraction tests (21 tests)
│   ├── test_clock_reset_hier.py   # Hierarchical clock/reset extraction via instance port maps (3 tests)
│   ├── test_lint.py               # Lint checks tests (31 tests)
│   ├── test_typedef_enum.py       # typedef/enum grammar, model, round-trip (28 tests)
│   ├── test_interface.py          # interface/modport grammar, model, round-trip (29 tests)
│   ├── test_package.py            # package/import grammar, model, round-trip (47 tests)
│   ├── test_struct_union.py       # struct/union grammar, model, round-trip (64 tests)
│   ├── test_block_locals.py       # Procedural block-local declarations tests (2 tests)
│   └── test_generate_improvements.py  # SV generate: ++/--, +=, inline genvar, qualified case (62 tests)

docs/
├── grammar_support.md    # Auto-generated support table
└── grammar_deps.json     # Rule dependency map (JSON)

examples/                          # Runnable DSL examples
├── basics/                        # Introductory DSL examples
│   ├── counter.py                 # 8-bit counter with simulation
│   ├── shift_register.py          # Parameterized shift register
│   ├── fsm.py                     # Traffic light FSM (two-process)
│   ├── alu.py                     # 8-bit ALU with full simulation
│   └── testbench.py               # Testbench pattern (system tasks, delays)
├── library/                       # Library component usage examples
│   ├── fifo_example.py            # sync_fifo with various configurations
│   ├── cdc_example.py             # Synchronizers and edge detectors
│   ├── codec_example.py           # Priority encoder and binary decoder
│   ├── dsp_example.py             # MAC, pipelined multiplier, FIR filter demos
│   └── xilinx_example.py          # SRL16/32, LUTRAM demos
├── axi/                           # AXI protocol examples
│   ├── axi_stream_example.py      # AXI-Stream master, slave, pipeline register
│   └── axi_lite_example.py        # AXI4-Lite slave register file
├── composability/                 # Composability showcase examples
│   ├── pipeline_generator.py      # Reusable pipeline from lambda stages
│   ├── design_explorer.py         # Parameter sweep with comparison tables
│   └── register_bank.py           # Register file from Python dict config
├── pause_demo/                    # PauseGenerator + compile_native showcase
│   └── pause_demo.py              # 6 demos: AXIS/AXI-Lite/AXI4 with backpressure;
│                                  #   Demos 1-5: Python PauseGenerator (reference engine)
│                                  #   Demo 6: compile_native fast path (AXIS loopback)
├── python_testbench/              # High-level Testbench DSL examples
│   ├── axi_stream_loopback.py     # AXIStreamProxy put/get on loopback DUT
│   └── multi_domain_axis.py      # Two-clock-domain AXIS bench with Domain.step()
├── darkriscv/                     # DarkRISCV simulation examples
│   ├── run_sim.py                 # Full event-loop simulation (VCD, $display)
│   ├── run_fast.py                # Pure-C batch_run simulation (512x faster)
│   ├── sim/darksimv.v             # Original testbench (clock gen + reset)
│   └── sim/darksimv_fast.v        # Minimal testbench (no timing, for batch_run)
├── femtorv/                       # FemtoRV32 Quark (RV32I) simulation
│   ├── run_sim.py                 # Reference engine test runner
│   ├── run_fast.py                # Compiled engine batch_run runner
│   ├── gen_firmware.py            # RV32I test firmware generator
│   ├── cosim_validate.py          # Icarus Verilog co-simulation comparison
│   ├── rtl/femtorv32_quark.v      # FemtoRV32 Quark processor (BSD-3)
│   ├── sim/testbench.v            # Full testbench (clock + reset + memory)
│   ├── sim/testbench_fast.v       # Minimal testbench (for batch_run)
│   └── sim/firmware.hex           # Generated RV32I test firmware
```

## Command-Line Tools

### Verilog Parser

Parse Verilog files and display the AST:

```powershell
# Parse default test file
uv run python -m veriforge -t

# Parse a specific file
uv run python -m veriforge -f path/to/file.v -t

# With debug output
uv run python -m veriforge -f file.v -t -d

# Reconstruct Verilog from AST
uv run python -m veriforge -f file.v -t -r
```

Options:
| Flag | Description |
|------|-------------|
| `-f, --file PATH` | Path to Verilog file (default: tests/test_verilog_parser/verilog/verilog_all.v) |
| `-t, --tree` | Display parse tree |
| `-r, --reconstruct` | Reconstruct Verilog from parse tree |
| `-d, --debug` | Enable debug mode |
| `-parser {earley,lalr}` | Parser type (default: earley) |
| `-log LEVEL` | Logging level: debug, info, warning, error, critical |
| `--version` | Show version |

### Grammar Tree Visualization

Visualize the grammar rule hierarchy from `verilog.lark`:

```powershell
# Show supported rules only (default)
uv run python -m veriforge.lark_file.gen_tree

# Show all rules (including unsupported)
uv run python -m veriforge.lark_file.gen_tree --all

# Limit depth
uv run python -m veriforge.lark_file.gen_tree --depth 5

# Start from a specific rule
uv run python -m veriforge.lark_file.gen_tree --root module_declaration

# Quiet mode (rich tree only, no text output)
uv run python -m veriforge.lark_file.gen_tree -q

# Combined example
uv run python -m veriforge.lark_file.gen_tree -a -d 4 -r expression -q
```

Options:
| Flag | Description |
|------|-------------|
| `-a, --all` | Show all rules, not just supported ones |
| `-d, --depth N` | Maximum depth to display (default: 8) |
| `-r, --root RULE` | Root rule to start from (default: verilog) |
| `-q, --quiet` | Only show rich tree, suppress text output |

Output features:
- Unsupported rules shown in red with `(unsupported)` marker
- Terminals (UPPERCASE) shown dimmed
- Recursive references detected and marked
- Line numbers from `verilog.lark` shown after each rule

### Hierarchy Refactor Inspection

Inspect resolved project hierarchy and wrapper candidates:

```powershell
# Print wrapper classifications as JSON for editor/tooling integration
uv run python -m veriforge hierarchy wrappers rtl --top top --json

# Print the hierarchy graph as JSON, including Peovim-compatible node metadata
uv run python -m veriforge hierarchy graph rtl --top top --json

# Export graph formats for visualization
uv run python -m veriforge hierarchy graph rtl --top top --format dot
uv run python -m veriforge hierarchy graph rtl --top top --format mermaid

# Preview a pure pass-through wrapper collapse as JSON or unified diff
uv run python -m veriforge hierarchy collapse rtl --top top --instance top/u_wrap --preview --json
uv run python -m veriforge hierarchy collapse rtl --top top --instance top/u_wrap --preview

# Apply a safe pure pass-through wrapper collapse and reparse the project
uv run python -m veriforge hierarchy collapse rtl --top top --instance top/u_wrap --write --json

# Preview extracting selected continuous assignments into a child module
uv run python -m veriforge hierarchy extract rtl --module top --range rtl/top.v:42-47 --name extracted_logic --preview --json

# Limit serialized hierarchy depth
uv run python -m veriforge hierarchy graph rtl --top top --max-depth 3 --json
```

Initial classifications are conservative:

| Class | Meaning |
|-------|---------|
| `pure_pass_through` | Single-instance wrapper with direct port aliases or simple pass-through assigns. |
| `structural_wrapper` | Structural module with instances but not yet safe for automatic collapse. |
| `behavioral_wrapper` | Module contains always/initial/function/task behavior and is visualization-only for now. |
| `unknown_or_unsupported` | Unresolved child, generate/specify/interface complexity, recursion, or unsupported source shape. |

### Grammar Metadata Tool

Extract metadata from `verilog.lark` for documentation and testing:

```powershell
# Show statistics
uv run python -m veriforge.lark_file.parse_metadata --stats

# Generate markdown support table
uv run python -m veriforge.lark_file.parse_metadata --table -o docs/grammar_support.md

# Export as JSON
uv run python -m veriforge.lark_file.parse_metadata --json -o docs/grammar_deps.json

# Filter by section
uv run python -m veriforge.lark_file.parse_metadata --section A.8 --table

# Include dependencies in table
uv run python -m veriforge.lark_file.parse_metadata --table --deps

# Preview DEPS tag generation (dry run)
uv run python -m veriforge.lark_file.parse_metadata --generate-deps --dry-run
```

Options:
| Flag | Description |
|------|-------------|
| `--stats` | Show statistics summary |
| `--table` | Generate markdown support table (default) |
| `--json` | Output as JSON |
| `--section PREFIX` | Filter by section (e.g., "A.1", "A.8") |
| `--deps` | Include dependencies in table |
| `--generate-deps` | Generate DEPS tags in verilog.lark |
| `--dry-run` | Preview changes without modifying files |
| `--no-examples` | Exclude examples from table |
| `-o, --output FILE` | Output file path |
| `-f, --file FILE` | Path to verilog.lark |

Metadata tags extracted:
- `SECTION:` - Grammar section identifier
- `BNF:` - Original IEEE 1364-2005 BNF
- `PRIORITY:` - HIGH, MEDIUM, LOW
- `SYNTHESIZABLE:` - YES, NO, PARTIAL
- `EXAMPLE:` - Short Verilog code example
- `SUPPORT:` - YES, NO

## Python API

### verilog_parser class

```python
from veriforge.verilog_parser import verilog_parser
from pathlib import Path

# Create parser
vp = verilog_parser(parser="earley", start="verilog")

# Parse from file
tree = vp.build_tree(text=Path("myfile.v"))

# Parse from string
tree = vp.build_tree(text="module foo(); endmodule")

# Reconstruct Verilog from tree
verilog_text = vp.reconstruct(tree)
```

**Note:** The transformer option is currently disabled. LALR parser requires unambiguous grammar, which the current `verilog.lark` does not fully support.

### Semantic Model (Layer 2)

```python
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms import tree_to_design, extract_comments, attach_comments
from veriforge.codegen import emit_design, emit_module

# Parse → Model (without comments)
vp = verilog_parser(start="module_declaration")
tree = vp.build_tree("module m #(parameter W=8)(input [W-1:0] d, output reg q); wire w; endmodule")
design = tree_to_design(tree, source_file="myfile.v")

# Parse → Model (with comments)
source = "// Counter\nmodule m(input clk); wire w; endmodule"
cleaned, comments = extract_comments(source, source_file="myfile.v")
tree = vp.build_tree(cleaned)
design = tree_to_design(tree, source_file="myfile.v", comments=comments)

# Inspect model
module = design.modules[0]
print(module.name)                    # "m"
print(module.input_ports())           # [Port(input [W-1:0] d)]
print(module.get_parameter("W"))      # Parameter(parameter W)
print(module.get_net("w"))            # Net(wire w)
print(module.all_signals())           # [Net(wire w)]

# Traverse
for node in design.walk():
    print(type(node).__name__)

# Serialize to JSON
json_str = design.to_json(indent=2)

# Emit back to Verilog
verilog_text = emit_design(design)
```

### Analysis API (Layer 3)

```python
from veriforge.analysis import (
    analyze_design, link_instances, resolve_names,
    resolve_port_connections, analyze_connectivity,
    Driver, Load,
)

# Run all 4 analysis passes on a design
analyze_design(design)

# Or run individual passes:
link_instances(design)           # Pass 1: Instance.resolved_module
resolve_names(design)            # Pass 2: Identifier.resolved (symbol tables)
resolve_port_connections(design)  # Pass 3: PortConnection.resolved_port
analyze_connectivity(design)     # Pass 4: Net.drivers/loads, Variable.drivers/loads

# Query results
for net in module.nets:
    for d in net.drivers:
        print(f"{net.name} driven by {d.source}")
    for l in net.loads:
        print(f"{net.name} read by {l.consumer}")
```

| Class | Module | Key Fields |
|-------|--------|------------|
| `Driver` | `analysis.resolver` | source (VerilogNode that drives) |
| `Load` | `analysis.resolver` | consumer (VerilogNode that reads) |

### Constant Folding API

```python
from veriforge.analysis import const_int, const_fold, const_range_width, fold_constants

# Evaluate a constant expression to int (returns int | None)
value = const_int(expr)              # e.g. const_int(BinaryOp("+", Literal(3), Literal(4))) → 7

# Follows parameter references: Identifier → Parameter.default_value
p = Parameter("WIDTH", default_value=Literal(8))
ident = Identifier("WIDTH")
ident.resolved = p
const_int(ident)                     # → 8

# System functions: $clog2, $signed, $unsigned
const_int(FunctionCall("$clog2", [Literal(256)], is_system=True))  # → 8

# Fold to a Literal node
lit = const_fold(expr)               # → Literal(7) or None

# Resolve parameterized ranges: [WIDTH-1:0] → 8
w = const_range_width(Range(BinaryOp("-", ident, Literal(1)), Literal(0)))  # → 8

# Fold all parameters in a design (after resolve_names)
fold_constants(design)
```

The constant folder is automatically used by width inference — `_const_int()` and
`_range_width()` in `width_inference.py` delegate to the folder, so parameterized
widths like `[WIDTH-1:0]` are resolved when the parameter value is known.

### Width Inference API

```python
from veriforge.analysis import infer_widths, infer_widths_in_module, infer_expr_width

# Infer widths across entire design (run after analyze_design)
infer_widths(design)

# Or on a single module
infer_widths_in_module(module)

# Or on a single expression
width = infer_expr_width(expr)  # returns int | None, sets expr.inferred_width
```

IEEE 1364-2005 rules: Literal→explicit/32, Identifier→declaration width,
UnaryOp (reduction→1, bitwise→operand), BinaryOp (arith→max, shift→left,
compare→1), Ternary→max(branches), Concat→sum, Replication→count×width,
BitSelect→1, RangeSelect→msb-lsb+1, FunctionCall→lookup, String→8×len.

### Clock/Reset Extraction API

```python
from veriforge.analysis import extract_clocks_resets, extract_clocks_resets_from_design

# Extract clocks and resets from a single module
info = extract_clocks_resets(module)

for clk in info.clocks:
    print(f"Clock: {clk.name} ({clk.edge}), drives {len(clk.always_blocks)} blocks")

for rst in info.resets:
    print(f"Reset: {rst.name}, style={rst.style}, active_low={rst.active_low}")
    print(f"  edge={rst.edge}, clock={rst.clock}")

# Convenience helpers
info.clock_names()   # → sorted unique clock names
info.reset_names()   # → sorted unique reset names
info.domain_map()    # → {"clk": [block1, block2], "clk_b": [block3]}

# Design-level extraction (all modules)
results = extract_clocks_resets_from_design(design)  # → dict[module_name, ClockResetInfo]
```

Detects by structural analysis of `AlwaysBlock` sensitivity lists:
- **Async resets**: Edge-triggered signals matched to the first `if` condition
- **Sync resets**: Condition-only resets inside single-edge sequential blocks
- **Active-low detection**: `!rst`, `~rst`, `rst == 0` patterns
- **Multi-domain**: Separate `ClockSignal` entries, merged when shared

### Lint Checks API

```python
from veriforge.analysis import lint_module, lint_design, LintCode

# Lint a single module (run after analyze_design + infer_widths)
warnings = lint_module(module)
for w in warnings:
    print(f"[{w.code.name}] {w.message}")

# Skip specific checks
warnings = lint_module(module, skip={LintCode.UNUSED, LintCode.UNDRIVEN})

# Lint all modules in a design
all_warnings = lint_design(design)
```

**Check codes** (`LintCode` enum):
| Code | Description |
|------|-------------|
| `UNDRIVEN` | Net/variable/output port with no drivers |
| `UNUSED` | Net/variable with no loads (dead signal) |
| `MULTI_DRIVEN` | Wire with multiple drivers |
| `LATCH_INFERRED` | Incomplete if/case in combinational always block |
| `WIDTH_MISMATCH` | LHS/RHS width differs in assign or port connection |
| `MIXED_BLOCKING` | Blocking `=` in sequential always block |
| `MIXED_NONBLOCKING` | Non-blocking `<=` in combinational always block |
| `UNCONNECTED_PORT` | Instance port connection left open |

### Interface / Modport API

```python
from veriforge.model import Interface, Modport, ModportPort
from veriforge.codegen.verilog_emitter import emit_interface

# After parsing source with interfaces:
for iface in design.interfaces:
    print(iface.name)
    for mp in iface.modports:
        print(f"  modport {mp.name}:")
        for p in mp.ports:
            print(f"    {p.direction.value} {p.name}")
    print(emit_interface(iface))
```

**Model classes** (in `model/interface.py`):
- `ModportPort(name, direction)` — single port entry in a modport
- `Modport(name, ports)` — modport declaration
- `Interface(name, parameters, nets, variables, continuous_assigns, modports, typedefs)` — interface declaration

Stored in `Design.interfaces` list. Supports:
- Parameters, nets, variables, continuous assigns, typedefs inside interface
- Multiple modport declarations with input/output/inout directions
- Parse → emit → re-parse round-trip

### typedef/enum Model API

```python
from veriforge.model import EnumMember, EnumType, TypedefDecl

# After parsing a module with typedef enum declarations:
for td in module.typedefs:
    print(td.name)           # e.g. "state_t"
    if td.enum_type:
        for m in td.enum_type.members:
            print(f"  {m.name} = {m.value}")
    if td.type_ref:
        print(f"  alias for {td.type_ref}")
```

**Model classes** (in `model/sv_types.py`):
- `EnumMember(name, value=Expression|None)` — single enum member
- `EnumType(members, base_type, width, signed)` — enum type specifier
- `TypedefDecl(name, enum_type, type_ref, loc)` — typedef declaration

Stored in `Module.typedefs` list. Supports grammar forms:
- `typedef enum {A, B, C} name;`
- `typedef enum logic [N:0] {A=0, B=1} name;`
- `typedef <base_type> name;` (type alias)
- Base types: logic, bit, reg, int, integer, shortint, longint, byte
- Optional `signed` qualifier and range

### Testbench Generator API

```python
from veriforge.dsl.testbench import generate_testbench
from veriforge.codegen import emit_module

# Generate testbench for a DUT module
tb = generate_testbench(
    dut,                      # ModelModule from builder.build() or parsed
    tb_name="tb_counter",     # Default: "tb_" + dut.name
    instance_name="uut",      # DUT instance name
    clock_period=10,           # Clock period in time units
    reset_duration=20,         # Reset assertion duration
    timeout=1000,              # Simulation timeout
    vcd=True,                  # Enable VCD dump
    vcd_filename="dump.vcd",  # Custom VCD filename
)
print(emit_module(tb))
```

Auto-detects clock signals (`clk`, `clock`, `sys_clk`, `pclk`, `aclk`, etc.)
and reset signals (`rst`, `reset`, `rst_n`, `arst`, etc.). Generates: reg/wire
declarations, DUT instantiation, clock generator, reset sequence (active-high
and active-low), VCD dump, timeout watchdog, stimulus placeholder.

### Verilog → DSL Translator API

### Preprocessor API

```python
from veriforge.preprocessor import preprocess, preprocess_file, PreprocessorError

# Preprocess a string (expand `define, `ifdef, `include, strip `timescale)
output = preprocess(source_text, defines={"SIMULATION": "", "__ICARUS__": ""})

# Preprocess with include search paths
output = preprocess(source_text, include_paths=["rtl/", "include/"])

# Preprocess a file (auto-adds file's directory to include path)
output = preprocess_file("rtl/top.v", defines={"__ICARUS__": ""})

# Get final defines back for chaining across files
output, final_defines = preprocess_file("rtl/top.v", return_defines=True)

# Strip comments before preprocessing
output = preprocess(source_text, strip_comments=True)
```

| Function | Description |
|----------|-------------|
| `preprocess(source, defines, include_paths, source_file, strip_comments, return_defines)` | Preprocess Verilog source text |
| `preprocess_file(path, defines, include_paths, strip_comments, return_defines, encoding)` | Preprocess a Verilog file |
| `PreprocessorError` | Exception with file/line context |

Directives handled:
- `\`define` / `\`undef` — macro definition/removal with line continuation support
- `\`ifdef` / `\`ifndef` / `\`else` / `\`elsif` / `\`endif` — conditional compilation (arbitrary nesting)
- `\`include "file"` / `\`include <file>` — file inclusion with search paths, recursion guard (max depth 64)
- `\`timescale`, `\`resetall`, `\`default_nettype`, `\`pragma`, `\`line`, `\`celldefine`, etc. — stripped (blank lines preserve line numbers)
- Macro expansion: `\`NAME` → value in non-directive lines

### Multi-File Project API

```python
from veriforge.project import parse_file, parse_files, parse_directory, export_dsl_project

# Parse a single file
design = parse_file("adder.v")

# Parse with preprocessor enabled
design = parse_file("top.v", preprocess=True, defines={"__ICARUS__": ""})

# Parse multiple files into a unified Design
design = parse_files(["top.v", "adder.v", "inverter.v"])

# Parse with preprocessing
design = parse_files(paths, preprocess=True, defines={"SIM": ""}, include_paths=["rtl/"])

# Parse a directory (recursively by default)
design = parse_directory("rtl/", extensions=(".v", ".sv"))

# With exclude patterns
design = parse_directory("rtl/", exclude=["*_tb.v", "testbench/"])

# Non-recursive scan (top-level only)
design = parse_directory("rtl/", recursive=False)

# Skip comment extraction for speed
design = parse_files(paths, comments=False)

# Skip instance linking (analysis)
design = parse_files(paths, analyze=False)

# Identify top modules (never instantiated)
tops = design.get_top_modules()

# Export DSL files (one .py per module)
written = export_dsl_project(design, "output_dir/")

# Export as single file
written = export_dsl_project(design, "output_dir/", one_file_per_module=False)

# Simulate from project
from veriforge.sim.testbench import Simulator
sim = Simulator(tops[0], engine="reference", design=design)
```

| Function | Description |
|----------|-------------|
| `parse_file(path, comments, preprocess, defines, include_paths)` | Parse single file → Design |
| `parse_files(paths, comments, analyze, preprocess, defines, include_paths, cache_dir)` | Parse multiple files → merged Design with instance linking |
| `parse_directory(dir, extensions, recursive, comments, analyze, exclude, preprocess, defines, include_paths, cache_dir)` | Scan directory → merged Design |
| `export_dsl_project(design, output_dir, one_file_per_module=True)` | Export Design to DSL .py files |

### Hierarchy Refactor API

```python
from veriforge.analysis import analyze_design
from veriforge.project import parse_directory
from veriforge.refactor import build_hierarchy_graph

design = parse_directory("rtl/")
analyze_design(design)

graph = build_hierarchy_graph(design, top="top")
payload = graph.to_dict()
wrappers = payload["wrappers"]
```

The initial `veriforge.refactor` surface is analysis-only. It builds
hierarchy trees with stable slash-separated instance paths such as
`top/u_wrapper/u_core`, classifies wrapper candidates, and serializes JSON
payloads that can be consumed by CLI tools or the Peovim Verilog LSP plugin. It
also previews pure pass-through wrapper collapse edits and supports guarded CLI
write mode plus LSP `WorkspaceEdit` payloads for editor-applied refactors.
Extract-module preview currently supports complete continuous assignments
selected by source line range, computes input/output/internal boundaries, and
generates a child module plus replacement instance without writing files.

### Verilog → DSL Translator API

```python
from veriforge.convert.to_dsl import module_to_dsl, design_to_dsl

# Convert a single parsed module to DSL Python code
python_code = module_to_dsl(module)             # returns str

# Convert all modules in a design
python_code = design_to_dsl(design)             # returns str

# The generated code is executable — uses veriforge.dsl API
exec(python_code)  # defines `module` variable
```

Translates model objects → Python DSL source code. Supports ports (input/output/
output_reg/inout), nets (wire), variables (reg/integer), parameters, localparam,
continuous assigns, always/initial blocks, if/elif/else chains, case/casex/casez,
blocking (@=) and non-blocking (<<=) assignments, system tasks ($display, $finish,
etc.), instances with port/parameter bindings, concatenation (cat), replication
(rep), ternary (mux), and all arithmetic/bitwise/comparison/logical operators.
SystemVerilog constructs: typedef enum/struct/union/alias via builder methods
and round-trip translation, package imports via `import_pkg()`, package and
interface informational output via `package_to_dsl()` and `interface_to_dsl()`.
`design_to_dsl()` emits packages, interfaces, then modules in order.
Unsupported constructs (for/while/forever loops, fork/join, wait, disable,
functions/tasks, generate) emit `# UNSUPPORTED:` comments. Auto-generates
appropriate `from veriforge.dsl import ...` based on features used.

#### Model Class Hierarchy

All model classes use `__slots__` for memory efficiency / Cython compatibility.

| Class | Module | Key Fields |
|-------|--------|------------|
| `VerilogNode` | `model.base` | loc, comments, parent, _parse_tree |
| `SourceLocation` | `model.base` | file, line, column, end_line, end_column |
| `Comment` | `model.base` | text, loc, kind, position |
| `Design` | `model.design` | modules, source_files |
| `Module` | `model.design` | name, parameters, ports, nets, variables, instances, ... |
| `Port` | `model.ports` | name, direction, net_type, data_type, width, signed |
| `Parameter` | `model.parameters` | name, param_type, width, signed, default_value, is_local |
| `Net` | `model.nets` | name, kind, width, signed, dimensions, initial_value |
| `Variable` | `model.variables` | name, kind, width, signed, dimensions, initial_value |
| `ContinuousAssign` | `model.assignments` | lhs, rhs (Expression) |
| `Instance` | `model.instances` | module_name, instance_name, instance_array, parameter_bindings, port_connections |
| `PortConnection` | `model.instances` | port_name, expression, is_named, resolved_port |
| `ParameterBinding` | `model.instances` | name, value |
| `AlwaysBlock` | `model.behavioral` | sensitivity_list, sensitivity_type, body (Statement) |
| `InitialBlock` | `model.behavioral` | body (Statement) |
| `Statement` | `model.statements` | (base class for all statements) |
| `BlockingAssign` | `model.statements` | lhs, rhs (Expression) |
| `NonblockingAssign` | `model.statements` | lhs, rhs (Expression) |
| `IfStatement` | `model.statements` | condition, then_body, else_body |
| `CaseStatement` | `model.statements` | case_type, expression, items |
| `CaseItem` | `model.statements` | values, body |
| `ForLoop` | `model.statements` | init, condition, update, body |
| `WhileLoop` | `model.statements` | condition, body |
| `ForeverLoop` | `model.statements` | body |
| `RepeatLoop` | `model.statements` | count, body |
| `SeqBlock` | `model.statements` | name, statements |
| `ParBlock` | `model.statements` | name, statements |
| `WaitStatement` | `model.statements` | condition, body |
| `DisableStatement` | `model.statements` | target |
| `EventTrigger` | `model.statements` | event |
| `TaskEnable` | `model.statements` | task_name, arguments |
| `SystemTaskCall` | `model.statements` | task_name, arguments |
| `DelayControl` | `model.statements` | delay, body |
| `EventControl` | `model.statements` | event, body |
| `SensitivityEdge` | `model.statements` | edge, signal |
| `Expression` | `model.expressions` | (base class) inferred_width |
| `Identifier` | `model.expressions` | name, hierarchy, resolved |
| `Literal` | `model.expressions` | value, width, base, signed, is_x, is_z, original_text |
| `BinaryOp` | `model.expressions` | op, left, right |
| `UnaryOp` | `model.expressions` | op, operand |
| `Range` | `model.expressions` | msb, lsb (lightweight, not VerilogNode) |
| `FunctionDecl` | `model.functions` | name, return_range, return_kind, is_automatic, ports, body |
| `TaskDecl` | `model.functions` | name, is_automatic, ports, body |
| `GenerateBlock` | `model.generate` | name, items |
| `GenerateFor` | `model.generate` | genvar, init_value, condition, update, body |
| `GenerateIf` | `model.generate` | condition, then_body, else_body |
| `GenerateCase` | `model.generate` | expression, items |
| `GenerateCaseItem` | `model.generate` | values, is_default, body |
| `GenvarDecl` | `model.generate` | names |
| `SpecifyBlock` | `model.specify` | raw_tree, source_text |

#### Enums

| Enum | Values |
|------|--------|
| `PortDirection` | INPUT, OUTPUT, INOUT |
| `NetKind` | WIRE, TRI, WAND, WOR, TRIAND, TRIOR, TRI0, TRI1, SUPPLY0, SUPPLY1, UWIRE, TRIREG |
| `VariableKind` | REG, INTEGER, REAL, REALTIME, TIME, EVENT |
| `SensitivityType` | COMBINATIONAL, SEQUENTIAL, LATCH, UNKNOWN |

## Testing

### Simulation API (Phase 6)

```python
from veriforge.sim import Simulator, Clock, Value, VcdWriter

# Build design via parser/transformer
design = tree_to_design(tree, source_file="counter.v")
module = design.modules[0]

# Create simulator
sim = Simulator(module)

# Get signal handles
clk = sim.signal("clk")
rst = sim.signal("rst")
count = sim.signal("count")

# Drive signals
clk.value = 0
rst.value = 1

# Fork a clock
sim.fork(Clock(clk, period=10))

# Run with a test function
def test_counter(s):
    s.drive("rst", 1)
    # ... drives and assertions

sim.run(test_counter, max_time=1000)

# Read results
assert count.value == expected
```

#### Hierarchical Signal Access

```python
# Access submodule signals via dotted hierarchical names
sim = Simulator(top_module, design=design)
sim.drive("inp", Value(99, width=8))
sim.run(max_time=0)

# Read internal signals (dot-separated instance path)
handle = sim.signal("u_mid.u_leaf.a")   # deeply nested signal
val = handle.value                       # → Value(99, width=8)

# List all signal names (sorted)
all_names = sim.signals()                # → ["inp", "outp", "u_mid.u_leaf.a", ...]

# Filter by prefix
u1_sigs = sim.signals("u1.")            # → ["u1.a", "u1.y"]

# Instance hierarchy: path → module name mapping
h = sim.hierarchy()                      # → {"u_mid": "mid", "u_mid.u_leaf": "leaf"}

# Typo detection: KeyError with close-match suggestions
sim.signal("in_b")                       # KeyError: "Signal 'in_b' not found. Did you mean: in_a?"
```

#### Bytecode VM Engine

High-performance alternative engine that compiles AST to bytecode at elaboration
time and executes it in a tight interpreter loop. Selectable via `engine` parameter:

```python
# Use the bytecode VM engine instead of the tree-walking reference engine
sim = Simulator(module, engine="vm")

# All testbench API (signals, clocks, drive, read) works identically
sim.fork(Clock(sim.signal("clk"), period=10))
sim.run(test_fn, max_time=1000)

# Cross-validation: run same design through both engines
sim_ref = Simulator(module, engine="reference")
sim_vm  = Simulator(module, engine="vm")
# ... drive same inputs, compare outputs
```

Architecture: `Compiler` walks model AST → emits flat `list[tuple[int,int,int]]`
instruction arrays. `Interpreter` executes in a single while loop with if/elif
dispatch over 74 opcodes. Signal storage uses flat integer arrays indexed by
signal ID (no dict lookups in inner loop). See `notes/simulation/simulator_bytecode_vm.md` for full
design document.

#### Cython Fast Interpreter (`_interp_fast.pyx`)

High-performance Cython replacement for the pure-Python `Interpreter`.
Compiles to a C extension that runs the bytecode execution loop and full
delta-cycle loop entirely in C, with no Python object allocations in the
hot path. ~2,580 lines.

**Build command:**
```powershell
uv run python setup_cython.py build_ext --inplace
```

**Key data structures (C structs):**

| Struct | Purpose |
|--------|---------|
| `SVal` | Stack entry: `(val, mask, width)` — 4-state value triple |
| `NBAEntry` | Non-blocking assignment: `(sig_id, val, mask)` |
| `NBAMemEntry` | Memory NBA: `(mem_id, addr, val, mask)` |
| `ExecResult` | Return to Python: `(status, nba_count, dirty_count)` |
| `DeltaCtx` | Full delta-loop context: signal arrays, programs, sensitivity CSR, edge info, working buffers |

**Core C functions (nogil):**

| Function | Lines | Purpose |
|----------|-------|---------|
| `_execute_core()` | ~1,170 | Single process execution — big if/elif dispatch over 70 opcodes. Operates on C arrays, returns status code (0=ok, 1=finish, 2=error) |
| `_run_delta_loop_core()` | ~230 | Full delta-cycle loop in C — applies NBAs, detects changes, triggers combinational re-eval, iterates until stable. Uses CSR (compressed sparse row) index for sensitivity lookup |

**Inline helpers:**
- `mask_for_width(w)` — bit mask for a given width
- `popcount64(x)` — set-bit count via Kernighan's method (O(popcount) iterations)

**Constants (DEF — compiled as C #defines):**
- `STACK_MAX = 256` — operand stack depth
- `NBA_MAX = 256` — NBA queue capacity
- `NBA_MEM_MAX = 64` — memory NBA queue capacity
- `DISP_BUF_CAP = 4096` — display output buffer slots

**Python-visible class: `CyContext`**

Extension type that owns all C arrays and provides methods called by
`VMScheduler`. Allocated once at elaboration, reused across all timesteps.

| Method | Purpose |
|--------|---------|
| `setup(sig_vals, sig_masks, ..., const_pool, fmt_strings)` | Allocate signal/constant C arrays from Python lists |
| `setup_memory(mem_vals, mem_masks, mem_info)` | Allocate memory (reg array) C storage |
| `setup_processes(proc_ops, proc_args, ..., edge_info)` | Flatten process programs + build sensitivity CSR |
| `execute_procs(proc_indices)` | Run a list of processes, collect NBAs + dirty signals |
| `apply_nbas()` | Apply queued non-blocking assignments to signal arrays |
| `run_delta_loop(changed_sids, delta_limit)` | Run full delta-cycle loop in C, return `(status, changed_set, display_lines)` |
| `write_signal(sid, val, mask)` | Direct signal write from Python |
| `read_signal(sid)` | Read `(val, mask)` for a signal from C arrays |
| `set_time(t)` | Update simulation time for `$time` opcode |
| `sync_signals_from_lists(vals, masks)` / `sync_signals_to_lists(vals, masks)` | Bulk sync between Python lists and C arrays |
| `sync_mem_from_lists(vals, masks)` / `sync_mem_to_lists(vals, masks)` | Bulk sync memory arrays |
| `snapshot_signals()` / `take_snapshot()` | Save signal state for edge detection |
| `reset_seq_fired()` | Clear sequential-process-fired flags for new timestep |
| `drain_display_buffer()` | Retrieve accumulated `$display`/`$monitor` output lines |

**Memory encoding for STORE_MEM / NBA_MEM:**
`arg1 = mem_id | (marker_sid << 16)` — marker signal triggers combinational
re-evaluation when memory is written.

**Display encoding for SYS_DISPLAY / SYS_MONITOR:**
`arg1 = n_args | (fmt_id << 16)` — format string index packed with argument count.
`SYS_MONITOR` also uses `arg2 = monitor_id`.

#### Simulation Classes

| Class | Module | Purpose |
|-------|--------|---------|
| `Value` | `sim.value` | 4-state bit vector (val, mask, width) |
| `EvalContext` | `sim.evaluator` | Signal state dict + memory arrays for evaluation |
| `ExpressionEvaluator` | `sim.evaluator` | Walk Expression → Value (memory-aware BitSelect) |
| `StatementExecutor` | `sim.executor` | Walk Statement → mutate state (memory writes, $readmemh, VCD) |
| `Scheduler` | `sim.scheduler` | Event queue, delta cycles, process management |
| `Simulator` | `sim.testbench` | Top-level entry point (engine="reference"\|"vm") |
| `SignalHandle` | `sim.testbench` | Read/write proxy to a signal |
| `Clock` | `sim.testbench` | Clock generator utility |
| `VcdWriter` | `sim.vcd` | VCD waveform file output |
| `Op` | `sim.vm.opcodes` | IntEnum of 74 bytecode opcodes |
| `Compiler` | `sim.vm.compiler` | AST → bytecode compiler |
| `CompiledProcess` | `sim.vm.compiler` | Bytecode program + sensitivity metadata |
| `Interpreter` | `sim.vm.interpreter` | Stack-based bytecode execution loop |
| `VMScheduler` | `sim.vm.vm_scheduler` | Event-driven scheduler using compiled processes |

#### VCD Output

```python
import io
from veriforge.sim import VcdWriter, Value

buf = io.StringIO()  # or filename string
with VcdWriter(buf, timescale="1ns") as w:
    w.add_signal("clk", width=1)
    w.add_signal("count", width=8, scope="counter")
    w.write_header()
    w.write_initial({"clk": Value(0, width=1), "count": Value(0, width=8)})
    w.set_time(5)
    w.change("clk", Value(1, width=1))
    w.set_time(10)
    w.change("clk", Value(0, width=1))
    w.change("count", Value(1, width=8))
```

### Hardware Construction DSL (Phase 7)

Operator-overloaded Python API that builds model objects directly,
then emits Verilog or feeds into the simulation engine.

```python
from veriforge.dsl import Module, Signal, posedge, negedge, cat, rep, mux
from veriforge.codegen import emit_module

# Build a counter module
with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    count = m.output_reg("count", width=8)
    with m.always(posedge(clk)):
        with m.if_(rst):
            count <<= 0           # NBA: count <= 0
        with m.else_():
            count <<= count + 1   # NBA: count <= count + 1

module = m.build()
print(emit_module(module))
```

#### DSL Signal Declarations

```python
m = Module("example")
clk = m.input("clk")                   # input clk;
a   = m.input("a", width=8)             # input [7:0] a;
q   = m.output("q", width=8)            # output [7:0] q;
q_r = m.output_reg("q_r", width=8)      # output reg [7:0] q_r;
d   = m.inout("d", width=4)             # inout [3:0] d;
w   = m.wire("w", width=16)             # wire [15:0] w;
r   = m.reg("r", width=8)               # reg [7:0] r;
i   = m.integer("i")                    # integer i;
p   = m.parameter("WIDTH", default=8)   # parameter WIDTH = 8;
lp  = m.localparam("DEPTH", default=4)  # localparam DEPTH = 4;
```

#### DSL Operator Overloading

`Signal` and `Expr` support Python operators that build Expression trees:

| Python | Verilog | Expression Type |
|--------|---------|----------------|
| `a + b` | `a + b` | `BinaryOp("+")` |
| `a - b` | `a - b` | `BinaryOp("-")` |
| `a * b` | `a * b` | `BinaryOp("*")` |
| `a // b` | `a / b` | `BinaryOp("/")` |
| `a % b` | `a % b` | `BinaryOp("%")` |
| `a ** b` | `a ** b` | `BinaryOp("**")` |
| `a & b` | `a & b` | `BinaryOp("&")` |
| `a \| b` | `a \| b` | `BinaryOp("\|")` |
| `a ^ b` | `a ^ b` | `BinaryOp("^")` |
| `a << n` | `a << n` | `BinaryOp("<<")` |
| `a >> n` | `a >> n` | `BinaryOp(">>")` |
| `~a` | `~a` | `UnaryOp("~")` |
| `-a` | `-a` | `UnaryOp("-")` |
| `a == b` | `a == b` | `BinaryOp("==")` |
| `a != b` | `a != b` | `BinaryOp("!=")` |
| `a[3]` | `a[3]` | `BitSelect` |
| `a[7:4]` | `a[7:4]` | `RangeSelect` |

#### DSL Assignments

```python
# Non-blocking assignment (inside always): count <<= count + 1
count <<= count + 1

# Blocking assignment (inside always): count @= count + 1
count @= count + 1

# Continuous assignment: assign y = a + b
m.assign(y, a + b)
```

#### DSL Context Managers

```python
# Always block with edge sensitivity
with m.always(posedge(clk), negedge(rst_n)):
    # ...

# Combinational always (no sensitivity args → always @(*))
with m.always():
    # ...

# Initial block
with m.initial():
    # ...

# If / elif / else chains
with m.if_(condition):
    # ...
with m.elif_(other_condition):
    # ...
with m.else_():
    # ...

# Case / casex / casez
with m.case(sel) as c:
    with c.when(0):
        # ...
    with c.when(1):
        # ...
    with c.default():
        # ...
```

#### DSL Helper Functions

| Function | Verilog | Description |
|----------|---------|-------------|
| `posedge(sig)` | `posedge sig` | Rising edge sensitivity |
| `negedge(sig)` | `negedge sig` | Falling edge sensitivity |
| `cat(a, b, ...)` | `{a, b, ...}` | Concatenation |
| `rep(n, sig)` | `{n{sig}}` | Replication |
| `mux(sel, t, f)` | `sel ? t : f` | Ternary mux |
| `land(a, b)` | `a && b` | Logical AND |
| `lor(a, b)` | `a \|\| b` | Logical OR |
| `lnot(a)` | `!a` | Logical NOT |
| `reduce_and(a)` | `&a` | Reduction AND |
| `reduce_or(a)` | `\|a` | Reduction OR |
| `reduce_xor(a)` | `^a` | Reduction XOR |
| `m.display(...)` | `$display(...)` | System task: print with newline |
| `m.write(...)` | `$write(...)` | System task: print without newline |
| `m.monitor(...)` | `$monitor(...)` | System task: monitor signals |
| `m.finish()` | `$finish` | System task: end simulation |
| `m.stop()` | `$stop` | System task: stop simulation |
| `m.readmemh(f, m)` | `$readmemh(f, m)` | System task: load hex file into memory |
| `m.readmemb(f, m)` | `$readmemb(f, m)` | System task: load binary file into memory |
| `m.typedef_enum(n, members, ...)` | `typedef enum ...` | Typedef enum declaration |
| `m.typedef_struct(n, fields, ...)` | `typedef struct ...` | Typedef struct declaration |
| `m.typedef_union(n, fields, ...)` | `typedef union ...` | Typedef union declaration |
| `m.typedef_alias(n, ref)` | `typedef ref n;` | Type alias declaration |
| `m.import_pkg(pkg)` | `import pkg::*;` | Package wildcard import |
| `m.import_pkg(pkg, item)` | `import pkg::item;` | Package specific import |
| `m.delay(t)` | `#t` | Delay control (standalone or `with` block) |
| `m.wait_posedge(s)` | `@(posedge s)` | Wait for rising edge |
| `m.wait_negedge(s)` | `@(negedge s)` | Wait for falling edge |
| `m.wait_event(...)` | `@(...)` | Event control (standalone or `with` block) |

#### DSL → Simulation Integration

```python
from veriforge.dsl import Module, posedge
from veriforge.sim import Simulator, Clock

# Build module via DSL
with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    cnt = m.output_reg("cnt", width=8)
    with m.always(posedge(clk)):
        with m.if_(rst):
            cnt <<= 0
        with m.else_():
            cnt <<= cnt + 1

# Simulate it
sim = Simulator(m.build())
sim.fork(Clock(sim.signal("clk"), period=10))
def test(s):
    s.drive("rst", 1)
sim.run(test, max_time=5)
assert sim.read("cnt") == 0
```

#### DSL Classes

| Class/Function | Module | Purpose |
|----------------|--------|---------|
| `Module` | `dsl.builder` | Hardware module builder (context manager) |
| `Signal(Expr)` | `dsl.builder` | Named signal proxy wrapping Identifier |
| `Expr` | `dsl.builder` | Expression proxy with operator overloading |
| `Interface` | `dsl.interface` | Reusable bus/interface template |
| `BoundInterface` | `dsl.interface` | Interface bound to a module with prefix/role |
| `_DelayContext` | `dsl.builder` | Context manager for `#delay` blocks |
| `_EventContext` | `dsl.builder` | Context manager for `@(event)` blocks |

#### RAM Inference Library

Factory functions in `dsl.ram` that return pre-built `Module` objects:

| Function | Description |
|----------|-------------|
| `single_port_ram(data_width, depth, sync_read, style)` | Single R/W port RAM |
| `simple_dual_port_ram(data_width, depth, sync_read, style)` | Separate read/write ports |
| `true_dual_port_ram(data_width, depth, style)` | Two independent R/W ports |
| `rom(data_width, depth, init_file, sync_read)` | ROM with optional `$readmemh` init |

All functions auto-calculate address width and support `(* ram_style *)` attributes.

#### Component Library

Importable via `from veriforge.dsl.lib import ...`:

| Function | Description |
|----------|-------------|
| `sync_fifo(data_width, depth, style)` | Synchronous FIFO with pointer-based full/empty/count |
| `synchronizer(width, stages)` | Multi-FF CDC synchronizer with `(* async_reg *)` |
| `edge_detector(edge_type)` | Rising/falling/any edge pulse generator |
| `priority_encoder(width)` | MSB-priority encoder with valid output |
| `binary_decoder(width)` | Binary-to-one-hot decoder with enable |
| `axi_stream(data_width, ...)` | AXI4-Stream `Interface` template |
| `axis_register(data_width)` | AXI-Stream forward pipeline register |
| `axi4_lite(data_width, addr_width)` | AXI4-Lite `Interface` template (5 channels) |
| `mac(a_width, b_width, acc_width, ...)` | DSP48 multiply-accumulate with rst/clr/en |
| `pipelined_mult(a_width, b_width, stages, ...)` | Variable-stage pipelined multiplier |
| `fir_filter(data_width, coeff_width, num_taps, ...)` | Transposed FIR filter with coefficient loading |
| `shift_register_srl(width, depth, style)` | Xilinx SRL inference shift register |
| `lutram(data_width, depth)` | Xilinx distributed RAM (LUTRAM) inference |

The `lib` package also re-exports all RAM functions (`single_port_ram`, `simple_dual_port_ram`, `true_dual_port_ram`, `rom`) for convenience.

## Testing

### Running Tests

```powershell
# Run all tests
uv run --extra test pytest tests/ -v

# Run specific test groups
uv run --extra test pytest tests/test_dsl/ -v       # DSL builder tests
uv run --extra test pytest tests/test_sim/ -v        # Simulation tests
uv run --extra test pytest tests/test_model/ -v      # Model tests

# Run tests by section marker
uv run --extra test pytest tests/ -m section_a1 -v    # Source text
uv run --extra test pytest tests/ -m section_a2 -v    # Declarations
uv run --extra test pytest tests/ -m section_a6 -v    # Behavioral
uv run --extra test pytest tests/ -m section_a8 -v    # Expressions

# Run with coverage
uv run --extra test pytest tests/ --cov=veriforge --cov-report=html
```

### Test Organization

Tests are organized by IEEE 1364-2005 grammar sections:

| File | Section | Coverage |
|------|---------|----------|
| `test_all.py` | Basic | Parser creation, simple modules |
| `test_rule_examples.py` | All | Per-rule EXAMPLE tag parsing (370 parametrized tests) |
| `test_section_a1.py` | A.1 Source text | Modules, ports, parameters |
| `test_section_a2.py` | A.2 Declarations | Wire, reg, parameter, function, task |
| `test_section_a6.py` | A.6 Behavioral | Always, initial, if, case, loops |
| `test_section_a8.py` | A.8 Expressions | Operators, numbers, concatenation |

### Test Fixtures (conftest.py)

Available fixtures:

| Fixture | Description |
|---------|-------------|
| `parser` | Full parser starting at 'verilog' rule |
| `module_parser` | Parser starting at 'module_declaration' |
| `expression_parser` | Parser starting at 'expression' |
| `statement_parser` | Parser starting at 'statement' |
| `grammar_metadata` | Parsed metadata from verilog.lark |
| `grammar_deps` | Dependency map from JSON |
| `high_priority_rules` | Rules with PRIORITY: HIGH |
| `synthesizable_rules` | Rules with SYNTHESIZABLE: YES |
| `rules_with_examples` | Rules that have EXAMPLE tags |
| `parse_helper` | Factory for ParseHelper instances |

### Custom Markers

```python
@pytest.mark.section_a1      # Source text tests
@pytest.mark.section_a2      # Declaration tests
@pytest.mark.section_a6      # Behavioral tests
@pytest.mark.section_a8      # Expression tests
@pytest.mark.synthesizable   # Synthesizable construct tests
@pytest.mark.slow            # Slow-running tests
@pytest.mark.grammar         # Grammar rule tests
```
