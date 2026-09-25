# Project Files

## Project Structure

```
src/veriforge/
├── __init__.py
├── _version.py           # Single source of truth for __version__; zero imports (safe for isolated builds)
├── _env.py               # VERIFORGE_<suffix> env var reads with VERILOG_TOOLS_<suffix> legacy-prefix fallback
├── __main__.py           # CLI entry point
├── verilog_parser.py     # Main parser class (Layer 1)
├── preprocessor.py       # Verilog preprocessor (`define/`ifdef/`include/`timescale etc.)
├── project.py            # Multi-file project support (parse_file/files/directory, parse cache)
├── scaffold.py           # Testbench scaffold + DSL export (build_testbench, build_testbench_plan, generate_python_testbench_skeleton, export_dsl_project)
├── semantics.py          # Unified width/signedness/const-eval semantics (const_int, range_width, var_width, net_width, expr_width, expr_signed); stdlib+model only
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
│       ├── axi4_master.py    # AXI4Master — burst read/write to DUT AXI4 slave; INCR-burst only;
│       │                     #   works against write-only or read-only DUTs
│       ├── axi4_responder.py # AXI4Responder — responds to DUT AXI4 master; .memory dict;
│       │                     #   rd/wr_latency_cycles + max_bw_percent (DDR/HBM-style model),
│       │                     #   memory_depth bound-check, per-channel .pause_aw/.pause_w/
│       │                     #   .pause_ar/.pause_b/.pause_r, write-only/read-only construction
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
│       │                 #   AXI4Proxy (role="slave": read(addr, length), write(addr, data);
│       │                 #     role="master": .memory/.write_log/.read_log,
│       │                 #     rd_latency_cycles/max_bw_percent, per-channel
│       │                 #     .pause_aw/.pause_w/.pause_ar/.pause_b/.pause_r)
│       │                 #   StreamProxy (Pulp ready/valid): put(data), get(timeout=)
│       ├── runtime.py    # Testbench (orchestrates clocks/resets/MultiDomainRunner) +
│       │                 #   Domain (one clock + reset + DomainCoordinator) +
│       │                 #   make_bench factory
│       ├── lowering.py   # Engine-native lowering: compile_native(), LoweredDesign,
│       │                 #   InterfaceLowering protocol, LoweringError,
│       │                 #   AXIStreamSourceLowering (case-ROM, O(n) C switch;
│       │                 #     optional PRNG pause: prng_bits/pause_threshold/prng_seed),
│       │                 #   AXIStreamSinkLowering (captures n beats + done signal;
│       │                 #     optional PRNG back-pressure: same 3 params),
│       │                 #   32-bit Galois LFSR helper (_build_lfsr_pause),
│       │                 #   AXILiteMasterLowering + AXILiteOp (scripted write/read seq.),
│       │                 #   AXILiteSlaveLowering (memory-backed responder for DUT master),
│       │                 #   AXI4SlaveLowering (burst responder for DUT AXI4 master;
│       │                 #     independent read/write FSMs, max_outstanding read/B-response
│       │                 #     queues, rd/wr_latency_cycles + max_bw_percent, per-channel
│       │                 #     aw/w/ar/b/r_pause);
│       │                 #   LoweredDesign.run(engine, cycles, vcd_path=) and
│       │                 #   LoweredDesign.batch_run(cycles) return merged dict of
│       │                 #   capture_signals + done_signals
│       └── skeleton.py   # generate_testbench()/generate_python_testbench(): auto-generate
│                         #   testbench wrappers for DUT modules (moved from dsl/testbench.py
│                         #   to break a sim <-> dsl import cycle; dsl/testbench.py is now a
│                         #   thin backward-compatible re-export shim, see notes/developer/architecture.md)
├── dsl/                  # Hardware Construction DSL (Phase 7)
│   ├── __init__.py       # Public API: Module, Signal, Expr, Interface, helpers
│   ├── builder.py        # Operator-overloaded Python DSL → model objects
│   ├── interface.py      # Interface / bus grouping abstraction
│   ├── prelude.py        # Star-import convenience module for DSL user code
│   ├── ram.py            # RAM inference pattern library (single/dual-port, ROM)
│   ├── spec.py           # Declarative ModuleSpec layer (__set_name__ port descriptors)
│   ├── testbench.py      # Backward-compat re-export shim → sim/bench/skeleton.py
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
└── fuzz/                 # Grammar-driven randomized differential-testing fuzzer
    ├── __init__.py       # Public API exports
    ├── __main__.py       # CLI entry point (python -m veriforge.fuzz)
    ├── _grammar_guide.py # Grammar guide for random rule selection
    ├── _signal_context.py # Tracks available signals during module generation
    ├── _expression_gen.py # Expression generator — random Expression model objects
    ├── _statement_gen.py # Statement generator — random Statement model objects
    ├── _module_gen.py    # Module generation strategies + top-level ModuleGenerator
    └── _runner.py        # Long-running loop: generate → simulate (all engines) → compare → log
```

The top-level `veriforge_lsp/` package (language server: `server.py`, `workspace.py`,
`index.py`, `protocol.py`, `handlers/`) is documented separately in
[notes/veriforge_lsp.md](../veriforge_lsp.md).

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
│   ├── compiled/                  # Compiled Cython engine tests, feature-organized package (4627 tests)
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
│   ├── test_axi4_responder.py     # AXI4Master (vs. real RTL DUT) + AXI4Responder (raw signal
│   │                              #   poking) conformance: channel-optional, memory_depth,
│   │                              #   latency/bandwidth, per-channel pause, strict WLAST mode
│   ├── test_interface_detection.py # detect_interfaces() tests, incl. full-AXI4 (AWLEN/ARLEN)
│   │                              #   detection, near-miss reporting, read-only/write-only AXI4
│   ├── test_stream_protocol.py    # StreamSource/Sink (Pulp ready/valid) tests
│   ├── test_bench_plan.py         # TestbenchPlan dataclass tests
│   ├── test_bench_planner.py      # build_plan() inference tests
│   ├── test_bench_runtime.py      # Testbench/Domain/proxy runtime tests
│   ├── test_bench_native.py       # compile_native + all lowerings: AXIS source/sink,
│   │                              #   AXILiteMaster, AXILiteSlave, AXI4Slave (incl.
│   │                              #   concurrency/latency/bandwidth/pause/id_width),
│   │                              #   AXI4Master, MemBus (119 tests)
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

