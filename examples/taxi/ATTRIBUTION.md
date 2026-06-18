# Attribution

The vendor RTL files in this directory are the work of Alex Forencich / FPGA Ninja, LLC.

## taxi (CERN Open Hardware License 2.0 - Strongly Reciprocal)

Source: https://github.com/alexforencich/taxi
Copyright (c) 2014-2025 FPGA Ninja, LLC
Authors: Alex Forencich
License: CERN-OHL-S-2.0 (RTL modules), MIT (interface files: taxi_axis_if.sv, taxi_axil_if.sv)

Files in `vendor/taxi/` are used verbatim without modification.
Flat-port wrapper files in `wrappers/` instantiate taxi modules and are therefore
derived works, distributed under CERN-OHL-S-2.0.

## verilog-axis (MIT License)

Source: https://github.com/alexforencich/verilog-axis
Copyright (c) 2014-2023 Alex Forencich
License: MIT

Used as fallback flat Verilog 2001 implementations when the taxi SV interface
versions encounter simulation compatibility issues.

## verilog-axi (MIT License)

Source: https://github.com/alexforencich/verilog-axi
Copyright (c) 2018 Alex Forencich
License: MIT

Used as fallback flat Verilog 2001 AXI implementations.

## Python testbench code

The Python testbench files in `tb/` are part of the veriforge project and
are not derived from any of the above hardware designs.
