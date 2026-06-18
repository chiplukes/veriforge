# Ibex Verilator Testbench Makefile
#
# Builds and runs a Verilator simulation of ibex_core with a simple
# memory testbench, producing ibex_trace.vcd for reference waveforms.
#
# Prerequisites (WSL/Linux):
#   sudo apt install verilator build-essential
#
# Usage:
#   make           # verilate, build, and run
#   make verilate  # just verilate (generate C++)
#   make build     # compile the generated C++
#   make run       # run simulation (produces ibex_trace.vcd)
#   make clean     # remove build artifacts and VCD

# RTL source directory
RTL_DIR := ../rtl
SIM_DIR := ../sim

# All RTL files in dependency order
RTL_FILES := \
	$(RTL_DIR)/ibex_pkg.sv \
	$(RTL_DIR)/ibex_alu.sv \
	$(RTL_DIR)/ibex_branch_predict.sv \
	$(RTL_DIR)/ibex_compressed_decoder.sv \
	$(RTL_DIR)/ibex_counter.sv \
	$(RTL_DIR)/ibex_csr.sv \
	$(RTL_DIR)/ibex_decoder.sv \
	$(RTL_DIR)/ibex_fetch_fifo.sv \
	$(RTL_DIR)/ibex_multdiv_fast.sv \
	$(RTL_DIR)/ibex_pmp.sv \
	$(RTL_DIR)/ibex_prefetch_buffer.sv \
	$(RTL_DIR)/ibex_register_file_ff.sv \
	$(RTL_DIR)/ibex_wb_stage.sv \
	$(RTL_DIR)/ibex_load_store_unit.sv \
	$(RTL_DIR)/ibex_ex_block.sv \
	$(RTL_DIR)/ibex_if_stage.sv \
	$(RTL_DIR)/ibex_cs_registers.sv \
	$(RTL_DIR)/ibex_id_stage.sv \
	$(RTL_DIR)/ibex_controller.sv \
	$(RTL_DIR)/ibex_core.sv

# Top-level testbench
TB_FILE := tb_verilator.sv

# Verilator flags
VERILATOR_FLAGS := \
	--cc \
	--exe \
	--trace \
	--build \
	-j 0 \
	-Wall \
	-Wno-UNUSEDSIGNAL \
	-Wno-UNDRIVEN \
	-Wno-UNUSEDPARAM \
	-Wno-WIDTHEXPAND \
	-Wno-WIDTHTRUNC \
	-Wno-CASEINCOMPLETE \
	-Wno-UNSIGNED \
	-Wno-PINCONNECTEMPTY \
	-Wno-PINMISSING \
	-Wno-PROCASSINIT \
	-Wno-SYNCASYNCNET \
	-Wno-UNOPTFLAT \
	--top-module tb_verilator \
	-I$(RTL_DIR) \
	-DSYNTHESIS

.PHONY: all verilate build run clean firmware

all: firmware verilate run

firmware:
	@if [ ! -f firmware.hex ]; then \
		echo "Copying firmware.hex from sim directory..."; \
		cp $(SIM_DIR)/firmware.hex .; \
	fi

verilate: firmware
	@echo "=== Verilating Ibex ==="
	verilator $(VERILATOR_FLAGS) \
		$(RTL_FILES) \
		$(TB_FILE) \
		sim_main.cpp \
		-o Vtb_verilator

build: verilate

run: build
	@echo ""
	@echo "=== Running simulation ==="
	./obj_dir/Vtb_verilator
	@echo ""
	@ls -lh ibex_trace.vcd 2>/dev/null || true

clean:
	rm -rf obj_dir
	rm -f ibex_trace.vcd
	rm -f firmware.hex
