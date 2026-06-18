"""Debug: check widths of u_dcsr_csr signals in flattened module."""

import os
import sys

sys.path.insert(0, "src")
from veriforge.project import parse_files
from veriforge.sim.elaborate import flatten_module, _build_param_env
from veriforge.sim.compiled.codegen import _scoped_env, _range_width, _var_width

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "examples", "ibex", "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "examples", "ibex", "rtl")
DEFINES = {"SYNTHESIS": ""}

FILES = [
    os.path.join(RTL_DIR, "prim_assert.sv"),
    os.path.join(RTL_DIR, "dv_fcov_macros.svh"),
    os.path.join(RTL_DIR, "ibex_pkg.sv"),
    os.path.join(RTL_DIR, "ibex_alu.sv"),
    os.path.join(RTL_DIR, "ibex_branch_predict.sv"),
    os.path.join(RTL_DIR, "ibex_compressed_decoder.sv"),
    os.path.join(RTL_DIR, "ibex_counter.sv"),
    os.path.join(RTL_DIR, "ibex_csr.sv"),
    os.path.join(RTL_DIR, "ibex_decoder.sv"),
    os.path.join(RTL_DIR, "ibex_fetch_fifo.sv"),
    os.path.join(RTL_DIR, "ibex_multdiv_fast.sv"),
    os.path.join(RTL_DIR, "ibex_pmp.sv"),
    os.path.join(RTL_DIR, "ibex_prefetch_buffer.sv"),
    os.path.join(RTL_DIR, "ibex_register_file_ff.sv"),
    os.path.join(RTL_DIR, "ibex_wb_stage.sv"),
    os.path.join(RTL_DIR, "ibex_load_store_unit.sv"),
    os.path.join(RTL_DIR, "ibex_ex_block.sv"),
    os.path.join(RTL_DIR, "ibex_if_stage.sv"),
    os.path.join(RTL_DIR, "ibex_cs_registers.sv"),
    os.path.join(RTL_DIR, "ibex_id_stage.sv"),
    os.path.join(RTL_DIR, "ibex_controller.sv"),
    os.path.join(RTL_DIR, "ibex_core.sv"),
    os.path.join(SIM_DIR, "testbench.v"),
]

os.chdir(SIM_DIR)
print("Starting parse...", flush=True)

design = parse_files(
    FILES,
    defines=DEFINES,
    cache_dir=os.path.join(SIM_DIR, ".pcache"),
    preprocess=True,
)
print("Parsed, flattening...", flush=True)

tb = design.get_module("testbench")
flat = flatten_module(tb, design=design)

# Check for u_dcsr_csr signals in variables
print("=== Variables with u_dcsr_csr ===")
for var in flat.variables:
    if "u_dcsr_csr" in var.name:
        print(f"  {var.name}: width={var.width}, kind={var.kind}")

print("\n=== Nets with u_dcsr_csr ===")
for net in flat.nets:
    if "u_dcsr_csr" in net.name:
        print(f"  {net.name}: width={net.width}, kind={net.kind}")

print("\n=== Ports with u_dcsr_csr ===")
for port in flat.ports:
    if "u_dcsr_csr" in port.name:
        print(f"  {port.name}: width={port.width}, dir={port.direction}")

# Check param env for Width
param_env = _build_param_env(flat)
print("\n=== Params with u_dcsr_csr ===")
for k, v in param_env.items():
    if "u_dcsr_csr" in k.lower():
        print(f"  {k} = {v}")

# Test scoped env resolution
print("\n=== Scoped env test for u_dcsr_csr signals ===")
for net in flat.nets:
    if "u_dcsr_csr.wr_data_i" in net.name:
        senv = _scoped_env(net.name, param_env)
        print(f"  Net {net.name}:")
        print(f"    width obj = {net.width}")
        if net.width:
            print(f"    msb type = {type(net.width.msb).__name__}, repr = {net.width.msb}")
            print(f"    lsb type = {type(net.width.lsb).__name__}, repr = {net.width.lsb}")
        w = _range_width(net.width, senv)
        print(f"    resolved width = {w}")
        # Show relevant env entries
        for ek, ev in senv.items():
            if "width" in ek.lower() or ek == "Width":
                print(f"    env[{ek}] = {ev}")

for var in flat.variables:
    if "u_dcsr_csr.wr_data_i" in var.name:
        senv = _scoped_env(var.name, param_env)
        print(f"  Var {var.name}:")
        print(f"    width obj = {var.width}")
        if var.width:
            print(f"    msb type = {type(var.width.msb).__name__}, repr = {var.width.msb}")
            print(f"    lsb type = {type(var.width.lsb).__name__}, repr = {var.width.lsb}")
        w = _var_width(var, senv)
        print(f"    resolved width = {w}")
        for ek, ev in senv.items():
            if "width" in ek.lower() or ek == "Width":
                print(f"    env[{ek}] = {ev}")
