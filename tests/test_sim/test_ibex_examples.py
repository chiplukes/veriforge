"""Focused Ibex example regressions across simulation engines."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.testbench import Simulator

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines() -> list[str]:
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        try:
            import Cython  # noqa: F401, PLC0415

            engines.append("compiled")
        except ImportError:
            pass
    return engines


ENGINES = _engines()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_ibex_pmp_probe(engine: str, tmp_path: Path, tb_name: str, tb_source: str) -> list[str]:
    repo_root = _repo_root()
    rtl_dir = repo_root / "examples" / "ibex" / "rtl"
    tb = tmp_path / f"{tb_name}.sv"
    tb.write_text(tb_source, encoding="utf-8")

    files = [
        str(rtl_dir / "dv_fcov_macros.svh"),
        str(rtl_dir / "ibex_pkg.sv"),
        str(rtl_dir / "ibex_pmp.sv"),
        str(tb),
    ]
    design = parse_files(
        files,
        preprocess=True,
        include_paths=[str(rtl_dir)],
        defines={"SYNTHESIS": ""},
        cache_dir=str(tmp_path / "pcache"),
    )
    top = design.get_module(tb_name)

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    return sim.display_output


def _run_ibex_cs_registers_probe(engine: str, tmp_path: Path, tb_name: str, tb_source: str) -> list[str]:
    repo_root = _repo_root()
    rtl_dir = repo_root / "examples" / "ibex" / "rtl"
    tb = tmp_path / f"{tb_name}.sv"
    tb.write_text(tb_source, encoding="utf-8")

    files = [
        str(rtl_dir / "prim_assert.sv"),
        str(rtl_dir / "ibex_pkg.sv"),
        str(rtl_dir / "ibex_csr.sv"),
        str(rtl_dir / "ibex_counter.sv"),
        str(rtl_dir / "ibex_cs_registers.sv"),
        str(tb),
    ]
    design = parse_files(
        files,
        preprocess=True,
        include_paths=[str(rtl_dir)],
        defines={"SYNTHESIS": ""},
        cache_dir=str(tmp_path / "pcache"),
    )
    top = design.get_module(tb_name)

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=10)
    return sim.display_output


def _run_ibex_alu_probe(engine: str, tmp_path: Path, tb_name: str, tb_source: str) -> list[str]:
    repo_root = _repo_root()
    rtl_dir = repo_root / "examples" / "ibex" / "rtl"
    tb = tmp_path / f"{tb_name}.sv"
    tb.write_text(tb_source, encoding="utf-8")

    files = [
        str(rtl_dir / "ibex_pkg.sv"),
        str(rtl_dir / "ibex_alu.sv"),
        str(tb),
    ]
    design = parse_files(
        files,
        preprocess=True,
        include_paths=[str(rtl_dir)],
        defines={"SYNTHESIS": ""},
        cache_dir=str(tmp_path / "pcache"),
    )
    top = design.get_module(tb_name)

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    return sim.display_output


def _run_ibex_ex_block_probe(engine: str, tmp_path: Path, tb_name: str, tb_source: str) -> list[str]:
    repo_root = _repo_root()
    rtl_dir = repo_root / "examples" / "ibex" / "rtl"
    tb = tmp_path / f"{tb_name}.sv"
    tb.write_text(tb_source, encoding="utf-8")

    files = [
        str(rtl_dir / "ibex_pkg.sv"),
        str(rtl_dir / "ibex_alu.sv"),
        str(rtl_dir / "ibex_ex_block.sv"),
        str(tb),
    ]
    design = parse_files(
        files,
        preprocess=True,
        include_paths=[str(rtl_dir)],
        defines={"SYNTHESIS": ""},
        cache_dir=str(tmp_path / "pcache"),
    )
    top = design.get_module(tb_name)

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    return sim.display_output


def _run_ibex_controller_probe(
    engine: str,
    tmp_path: Path,
    tb_name: str,
    tb_source: str,
    *,
    max_time: int = 20,
) -> list[str]:
    repo_root = _repo_root()
    rtl_dir = repo_root / "examples" / "ibex" / "rtl"
    tb = tmp_path / f"{tb_name}.sv"
    tb.write_text(tb_source, encoding="utf-8")

    files = [
        str(rtl_dir / "ibex_pkg.sv"),
        str(rtl_dir / "ibex_controller.sv"),
        str(tb),
    ]
    design = parse_files(
        files,
        preprocess=True,
        include_paths=[str(rtl_dir)],
        defines={"SYNTHESIS": ""},
        cache_dir=str(tmp_path / "pcache"),
    )
    top = design.get_module(tb_name)

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=max_time)
    return sim.display_output


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_pmp_probe_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_pmp module agrees on a minimal NAPOT permission probe."""
    assert _run_ibex_pmp_probe(
        engine,
        tmp_path,
        "ibex_pmp_probe_tb",
        """\
module ibex_pmp_probe_tb;
  import ibex_pkg::*;

  pmp_cfg_t csr_pmp_cfg_i [1];
  logic [PMP_ADDR_MSB:0] csr_pmp_addr_i [1];
  pmp_mseccfg_t csr_pmp_mseccfg_i;
  logic debug_mode_i;
  priv_lvl_e priv_mode_i [1];
  logic [PMP_ADDR_MSB:0] pmp_req_addr_i [1];
  pmp_req_e pmp_req_type_i [1];
  logic pmp_req_err_o [1];

  ibex_pmp #(
    .PMPGranularity(0),
    .PMPNumChan(1),
    .PMPNumRegions(1)
  ) dut (
    .csr_pmp_cfg_i(csr_pmp_cfg_i),
    .csr_pmp_addr_i(csr_pmp_addr_i),
    .csr_pmp_mseccfg_i(csr_pmp_mseccfg_i),
    .debug_mode_i(debug_mode_i),
    .priv_mode_i(priv_mode_i),
    .pmp_req_addr_i(pmp_req_addr_i),
    .pmp_req_type_i(pmp_req_type_i),
    .pmp_req_err_o(pmp_req_err_o)
  );

  initial begin
    csr_pmp_cfg_i[0] = '0;
    csr_pmp_cfg_i[0].mode = PMP_MODE_NAPOT;
    csr_pmp_cfg_i[0].read = 1'b1;
    csr_pmp_cfg_i[0].write = 1'b1;
    csr_pmp_addr_i[0] = 34'h0000_000f;
    csr_pmp_mseccfg_i = '0;
    debug_mode_i = 1'b0;
    priv_mode_i[0] = PRIV_LVL_U;
    pmp_req_type_i[0] = PMP_ACC_READ;
    pmp_req_addr_i[0] = 34'h0000_000f;
    #0;
    $display("mask=%h eq=%b all=%b basic=%b perm=%b access=%b err=%b",
             dut.region_addr_mask[0],
             dut.region_match_eq[0][0],
             dut.region_match_all[0][0],
             dut.region_basic_perm_check[0][0],
             dut.region_perm_check[0][0],
             dut.access_fault_check_res[0],
             pmp_req_err_o[0]);
    $finish;
  end
endmodule
""",
    ) == ["mask=fffffff8 eq=1 all=1 basic=1 perm=1 access=0 err=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_pmp_multiphase_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_pmp module stays aligned across repeated #0 delta phases."""
    assert _run_ibex_pmp_probe(
        engine,
        tmp_path,
        "ibex_pmp_probe_tb2",
        """\
module ibex_pmp_probe_tb2;
  import ibex_pkg::*;

  pmp_cfg_t csr_pmp_cfg_i [2];
  logic [PMP_ADDR_MSB:0] csr_pmp_addr_i [2];
  pmp_mseccfg_t csr_pmp_mseccfg_i;
  logic debug_mode_i;
  priv_lvl_e priv_mode_i [1];
  logic [PMP_ADDR_MSB:0] pmp_req_addr_i [1];
  pmp_req_e pmp_req_type_i [1];
  logic pmp_req_err_o [1];

  ibex_pmp #(
    .PMPGranularity(0),
    .PMPNumChan(1),
    .PMPNumRegions(2)
  ) dut (
    .csr_pmp_cfg_i(csr_pmp_cfg_i),
    .csr_pmp_addr_i(csr_pmp_addr_i),
    .csr_pmp_mseccfg_i(csr_pmp_mseccfg_i),
    .debug_mode_i(debug_mode_i),
    .priv_mode_i(priv_mode_i),
    .pmp_req_addr_i(pmp_req_addr_i),
    .pmp_req_type_i(pmp_req_type_i),
    .pmp_req_err_o(pmp_req_err_o)
  );

  initial begin
    csr_pmp_cfg_i[0] = '0;
    csr_pmp_cfg_i[1] = '0;
    csr_pmp_addr_i[0] = '0;
    csr_pmp_addr_i[1] = '0;
    csr_pmp_mseccfg_i = '0;
    debug_mode_i = 1'b0;
    priv_mode_i[0] = PRIV_LVL_U;
    pmp_req_type_i[0] = PMP_ACC_READ;
    pmp_req_addr_i[0] = 34'h0;

    #0;
    $display("unmatched err=%0b all=%0b%0b perm=%0b%0b dbg=%0b access=%0b",
             pmp_req_err_o[0],
             dut.region_match_all[0][1], dut.region_match_all[0][0],
             dut.region_perm_check[0][1], dut.region_perm_check[0][0],
             dut.debug_mode_allowed_access[0],
             dut.access_fault_check_res[0]);

    csr_pmp_cfg_i[0].mode = PMP_MODE_NAPOT;
    csr_pmp_cfg_i[0].read = 1'b1;
    csr_pmp_cfg_i[0].write = 1'b1;
    csr_pmp_addr_i[0] = 34'h0000_000f;
    pmp_req_addr_i[0] = 34'h0000_000f;
    #0;
    $display("napot_allow err=%0b all=%0b%0b perm=%0b%0b dbg=%0b access=%0b",
             pmp_req_err_o[0],
             dut.region_match_all[0][1], dut.region_match_all[0][0],
             dut.region_perm_check[0][1], dut.region_perm_check[0][0],
             dut.debug_mode_allowed_access[0],
             dut.access_fault_check_res[0]);

    csr_pmp_cfg_i[0].write = 1'b0;
    pmp_req_type_i[0] = PMP_ACC_WRITE;
    #0;
    $display("napot_write_den err=%0b all=%0b%0b perm=%0b%0b dbg=%0b access=%0b",
             pmp_req_err_o[0],
             dut.region_match_all[0][1], dut.region_match_all[0][0],
             dut.region_perm_check[0][1], dut.region_perm_check[0][0],
             dut.debug_mode_allowed_access[0],
             dut.access_fault_check_res[0]);

    csr_pmp_cfg_i[1].mode = PMP_MODE_NAPOT;
    csr_pmp_cfg_i[1].read = 1'b1;
    csr_pmp_cfg_i[1].write = 1'b1;
    csr_pmp_addr_i[1] = 34'h0000_000f;
    pmp_req_type_i[0] = PMP_ACC_READ;
    #0;
    $display("priority_deny err=%0b all=%0b%0b perm=%0b%0b dbg=%0b access=%0b",
             pmp_req_err_o[0],
             dut.region_match_all[0][1], dut.region_match_all[0][0],
             dut.region_perm_check[0][1], dut.region_perm_check[0][0],
             dut.debug_mode_allowed_access[0],
             dut.access_fault_check_res[0]);

    csr_pmp_cfg_i[0] = '0;
    csr_pmp_cfg_i[1] = '0;
    csr_pmp_addr_i[0] = 34'h0000_0004;
    csr_pmp_addr_i[1] = 34'h0000_0008;
    csr_pmp_cfg_i[1].mode = PMP_MODE_TOR;
    csr_pmp_cfg_i[1].read = 1'b1;
    pmp_req_addr_i[0] = 34'h0000_0006;
    #0;
    $display("tor_allow err=%0b all=%0b%0b perm=%0b%0b dbg=%0b access=%0b",
             pmp_req_err_o[0],
             dut.region_match_all[0][1], dut.region_match_all[0][0],
             dut.region_perm_check[0][1], dut.region_perm_check[0][0],
             dut.debug_mode_allowed_access[0],
             dut.access_fault_check_res[0]);

    csr_pmp_cfg_i[1].read = 1'b0;
    debug_mode_i = 1'b1;
    pmp_req_addr_i[0] = 34'h1A110123;
    #0;
    $display("debug_override err=%0b all=%0b%0b perm=%0b%0b dbg=%0b access=%0b",
             pmp_req_err_o[0],
             dut.region_match_all[0][1], dut.region_match_all[0][0],
             dut.region_perm_check[0][1], dut.region_perm_check[0][0],
             dut.debug_mode_allowed_access[0],
             dut.access_fault_check_res[0]);
    $finish;
  end
endmodule
""",
    ) == [
        "unmatched err=1 all=00 perm=00 dbg=0 access=1",
        "napot_allow err=0 all=01 perm=01 dbg=0 access=0",
        "napot_write_den err=1 all=01 perm=00 dbg=0 access=1",
        "priority_deny err=0 all=11 perm=11 dbg=0 access=0",
        "tor_allow err=0 all=10 perm=10 dbg=0 access=0",
        "debug_override err=0 all=00 perm=00 dbg=1 access=1",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_pmp_epmp_permissions_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_pmp module agrees on ePMP MML/MMWP permission helper behavior."""
    assert _run_ibex_pmp_probe(
        engine,
        tmp_path,
        "ibex_pmp_probe_tb3",
        """\
module ibex_pmp_probe_tb3;
  import ibex_pkg::*;

  pmp_cfg_t csr_pmp_cfg_i [1];
  logic [PMP_ADDR_MSB:0] csr_pmp_addr_i [1];
  pmp_mseccfg_t csr_pmp_mseccfg_i;
  logic debug_mode_i;
  priv_lvl_e priv_mode_i [1];
  logic [PMP_ADDR_MSB:0] pmp_req_addr_i [1];
  pmp_req_e pmp_req_type_i [1];
  logic pmp_req_err_o [1];

  ibex_pmp #(
    .PMPGranularity(0),
    .PMPNumChan(1),
    .PMPNumRegions(1)
  ) dut (
    .csr_pmp_cfg_i(csr_pmp_cfg_i),
    .csr_pmp_addr_i(csr_pmp_addr_i),
    .csr_pmp_mseccfg_i(csr_pmp_mseccfg_i),
    .debug_mode_i(debug_mode_i),
    .priv_mode_i(priv_mode_i),
    .pmp_req_addr_i(pmp_req_addr_i),
    .pmp_req_type_i(pmp_req_type_i),
    .pmp_req_err_o(pmp_req_err_o)
  );

  initial begin
    csr_pmp_cfg_i[0] = '0;
    csr_pmp_addr_i[0] = '0;
    csr_pmp_mseccfg_i = '0;
    debug_mode_i = 1'b0;
    pmp_req_addr_i[0] = '0;

    priv_mode_i[0] = PRIV_LVL_M;
    pmp_req_type_i[0] = PMP_ACC_READ;
    #0;
    $display("m_unmatched_allow err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    csr_pmp_mseccfg_i.mmwp = 1'b1;
    #0;
    $display("m_unmatched_mmwp err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    csr_pmp_mseccfg_i.mmwp = 1'b0;
    csr_pmp_mseccfg_i.mml = 1'b1;
    pmp_req_type_i[0] = PMP_ACC_EXEC;
    #0;
    $display("m_unmatched_exec_mml err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    csr_pmp_cfg_i[0] = '0;
    csr_pmp_cfg_i[0].mode = PMP_MODE_NAPOT;
    csr_pmp_cfg_i[0].write = 1'b1;
    csr_pmp_cfg_i[0].lock = 1'b0;
    csr_pmp_cfg_i[0].exec = 1'b1;
    csr_pmp_addr_i[0] = 34'h0000_000f;
    pmp_req_addr_i[0] = 34'h0000_000f;
    pmp_req_type_i[0] = PMP_ACC_READ;
    priv_mode_i[0] = PRIV_LVL_U;
    #0;
    $display("mml_shared_read_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    pmp_req_type_i[0] = PMP_ACC_WRITE;
    #0;
    $display("mml_shared_write_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    priv_mode_i[0] = PRIV_LVL_M;
    #0;
    $display("mml_shared_write_m err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    $finish;
  end
endmodule
""",
    ) == [
        "m_unmatched_allow err=0 access=0 perm=1 all=0",
        "m_unmatched_mmwp err=1 access=1 perm=1 all=0",
        "m_unmatched_exec_mml err=1 access=1 perm=0 all=0",
        "mml_shared_read_u err=0 access=0 perm=1 all=1",
        "mml_shared_write_u err=0 access=0 perm=1 all=1",
        "mml_shared_write_m err=0 access=0 perm=1 all=1",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_pmp_epmp_special_regions_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_pmp module agrees on the remaining special MML permission branches."""
    assert _run_ibex_pmp_probe(
        engine,
        tmp_path,
        "ibex_pmp_probe_tb4",
        """\
module ibex_pmp_probe_tb4;
  import ibex_pkg::*;

  pmp_cfg_t csr_pmp_cfg_i [1];
  logic [PMP_ADDR_MSB:0] csr_pmp_addr_i [1];
  pmp_mseccfg_t csr_pmp_mseccfg_i;
  logic debug_mode_i;
  priv_lvl_e priv_mode_i [1];
  logic [PMP_ADDR_MSB:0] pmp_req_addr_i [1];
  pmp_req_e pmp_req_type_i [1];
  logic pmp_req_err_o [1];

  ibex_pmp #(
    .PMPGranularity(0),
    .PMPNumChan(1),
    .PMPNumRegions(1)
  ) dut (
    .csr_pmp_cfg_i(csr_pmp_cfg_i),
    .csr_pmp_addr_i(csr_pmp_addr_i),
    .csr_pmp_mseccfg_i(csr_pmp_mseccfg_i),
    .debug_mode_i(debug_mode_i),
    .priv_mode_i(priv_mode_i),
    .pmp_req_addr_i(pmp_req_addr_i),
    .pmp_req_type_i(pmp_req_type_i),
    .pmp_req_err_o(pmp_req_err_o)
  );

  initial begin
    csr_pmp_cfg_i[0] = '0;
    csr_pmp_addr_i[0] = 34'h0000_000f;
    csr_pmp_mseccfg_i = '0;
    csr_pmp_mseccfg_i.mml = 1'b1;
    debug_mode_i = 1'b0;
    pmp_req_addr_i[0] = 34'h0000_000f;
    csr_pmp_cfg_i[0].mode = PMP_MODE_NAPOT;

    csr_pmp_cfg_i[0].read = 1'b0;
    csr_pmp_cfg_i[0].write = 1'b1;
    csr_pmp_cfg_i[0].lock = 1'b0;
    csr_pmp_cfg_i[0].exec = 1'b0;
    priv_mode_i[0] = PRIV_LVL_U;
    pmp_req_type_i[0] = PMP_ACC_READ;
    #0;
    $display("shared00_read_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    pmp_req_type_i[0] = PMP_ACC_WRITE;
    #0;
    $display("shared00_write_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    priv_mode_i[0] = PRIV_LVL_M;
    #0;
    $display("shared00_write_m err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    csr_pmp_cfg_i[0].lock = 1'b1;
    csr_pmp_cfg_i[0].exec = 1'b0;
    priv_mode_i[0] = PRIV_LVL_U;
    pmp_req_type_i[0] = PMP_ACC_EXEC;
    #0;
    $display("shared10_exec_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    pmp_req_type_i[0] = PMP_ACC_READ;
    #0;
    $display("shared10_read_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    csr_pmp_cfg_i[0].exec = 1'b1;
    pmp_req_type_i[0] = PMP_ACC_EXEC;
    #0;
    $display("shared11_exec_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    pmp_req_type_i[0] = PMP_ACC_READ;
    #0;
    $display("shared11_read_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    priv_mode_i[0] = PRIV_LVL_M;
    #0;
    $display("shared11_read_m err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);

    csr_pmp_cfg_i[0].read = 1'b1;
    csr_pmp_cfg_i[0].write = 1'b1;
    csr_pmp_cfg_i[0].lock = 1'b1;
    csr_pmp_cfg_i[0].exec = 1'b1;
    priv_mode_i[0] = PRIV_LVL_U;
    pmp_req_type_i[0] = PMP_ACC_READ;
    #0;
    $display("shared_ro_read_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    pmp_req_type_i[0] = PMP_ACC_EXEC;
    #0;
    $display("shared_ro_exec_u err=%0b access=%0b perm=%0b all=%0b",
             pmp_req_err_o[0],
             dut.access_fault_check_res[0],
             dut.region_perm_check[0][0],
             dut.region_match_all[0][0]);
    $finish;
  end
endmodule
""",
    ) == [
        "shared00_read_u err=0 access=0 perm=1 all=1",
        "shared00_write_u err=1 access=1 perm=0 all=1",
        "shared00_write_m err=0 access=0 perm=1 all=1",
        "shared10_exec_u err=0 access=0 perm=1 all=1",
        "shared10_read_u err=1 access=1 perm=0 all=1",
        "shared11_exec_u err=0 access=0 perm=1 all=1",
        "shared11_read_u err=1 access=1 perm=0 all=1",
        "shared11_read_m err=0 access=0 perm=1 all=1",
        "shared_ro_read_u err=0 access=0 perm=1 all=1",
        "shared_ro_exec_u err=1 access=1 perm=0 all=1",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_cs_registers_mml_exec_suppression_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_cs_registers module agrees on MML exec-config suppression."""
    assert _run_ibex_cs_registers_probe(
        engine,
        tmp_path,
        "ibex_cs_registers_probe_tb",
        """\
module ibex_cs_registers_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic [31:0] hart_id_i;
  logic csr_mtvec_init_i;
  logic [31:0] boot_addr_i;
  logic csr_access_i;
  csr_num_e csr_addr_i;
  logic [31:0] csr_wdata_i;
  csr_op_e csr_op_i;
  logic csr_op_en_i;
  logic irq_software_i;
  logic irq_timer_i;
  logic irq_external_i;
  logic [14:0] irq_fast_i;
  logic nmi_mode_i;
  logic debug_mode_i;
  logic debug_mode_entering_i;
  dbg_cause_e debug_cause_i;
  logic debug_csr_save_i;
  logic [31:0] pc_if_i;
  logic [31:0] pc_id_i;
  logic [31:0] pc_wb_i;
  logic ic_scr_key_valid_i;
  logic csr_save_if_i;
  logic csr_save_id_i;
  logic csr_save_wb_i;
  logic csr_restore_mret_i;
  logic csr_restore_dret_i;
  logic csr_save_cause_i;
  exc_cause_t csr_mcause_i;
  logic [31:0] csr_mtval_i;
  logic instr_ret_i;
  logic instr_ret_compressed_i;
  logic instr_ret_spec_i;
  logic instr_ret_compressed_spec_i;
  logic iside_wait_i;
  logic jump_i;
  logic branch_i;
  logic branch_taken_i;
  logic mem_load_i;
  logic mem_store_i;
  logic dside_wait_i;
  logic mul_wait_i;
  logic div_wait_i;

  ibex_cs_registers #(
    .PMPEnable(1'b1),
    .PMPGranularity(0),
    .PMPNumRegions(1),
    .PMPRstMsecCfg('{rlb: 1'b0, mmwp: 1'b0, mml: 1'b1})
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .hart_id_i(hart_id_i),
    .csr_mtvec_init_i(csr_mtvec_init_i),
    .boot_addr_i(boot_addr_i),
    .csr_access_i(csr_access_i),
    .csr_addr_i(csr_addr_i),
    .csr_wdata_i(csr_wdata_i),
    .csr_op_i(csr_op_i),
    .csr_op_en_i(csr_op_en_i),
    .irq_software_i(irq_software_i),
    .irq_timer_i(irq_timer_i),
    .irq_external_i(irq_external_i),
    .irq_fast_i(irq_fast_i),
    .nmi_mode_i(nmi_mode_i),
    .debug_mode_i(debug_mode_i),
    .debug_mode_entering_i(debug_mode_entering_i),
    .debug_cause_i(debug_cause_i),
    .debug_csr_save_i(debug_csr_save_i),
    .pc_if_i(pc_if_i),
    .pc_id_i(pc_id_i),
    .pc_wb_i(pc_wb_i),
    .ic_scr_key_valid_i(ic_scr_key_valid_i),
    .csr_save_if_i(csr_save_if_i),
    .csr_save_id_i(csr_save_id_i),
    .csr_save_wb_i(csr_save_wb_i),
    .csr_restore_mret_i(csr_restore_mret_i),
    .csr_restore_dret_i(csr_restore_dret_i),
    .csr_save_cause_i(csr_save_cause_i),
    .csr_mcause_i(csr_mcause_i),
    .csr_mtval_i(csr_mtval_i),
    .instr_ret_i(instr_ret_i),
    .instr_ret_compressed_i(instr_ret_compressed_i),
    .instr_ret_spec_i(instr_ret_spec_i),
    .instr_ret_compressed_spec_i(instr_ret_compressed_spec_i),
    .iside_wait_i(iside_wait_i),
    .jump_i(jump_i),
    .branch_i(branch_i),
    .branch_taken_i(branch_taken_i),
    .mem_load_i(mem_load_i),
    .mem_store_i(mem_store_i),
    .dside_wait_i(dside_wait_i),
    .mul_wait_i(mul_wait_i),
    .div_wait_i(div_wait_i)
  );

  always #1 clk_i = ~clk_i;

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    hart_id_i = '0;
    csr_mtvec_init_i = 1'b0;
    boot_addr_i = '0;
    csr_access_i = 1'b0;
    csr_addr_i = csr_num_e'(12'h000);
    csr_wdata_i = '0;
    csr_op_i = CSR_OP_READ;
    csr_op_en_i = 1'b0;
    irq_software_i = 1'b0;
    irq_timer_i = 1'b0;
    irq_external_i = 1'b0;
    irq_fast_i = '0;
    nmi_mode_i = 1'b0;
    debug_mode_i = 1'b0;
    debug_mode_entering_i = 1'b0;
    debug_cause_i = dbg_cause_e'(0);
    debug_csr_save_i = 1'b0;
    pc_if_i = '0;
    pc_id_i = '0;
    pc_wb_i = '0;
    ic_scr_key_valid_i = 1'b0;
    csr_save_if_i = 1'b0;
    csr_save_id_i = 1'b0;
    csr_save_wb_i = 1'b0;
    csr_restore_mret_i = 1'b0;
    csr_restore_dret_i = 1'b0;
    csr_save_cause_i = 1'b0;
    csr_mcause_i = '0;
    csr_mtval_i = '0;
    instr_ret_i = 1'b0;
    instr_ret_compressed_i = 1'b0;
    instr_ret_spec_i = 1'b0;
    instr_ret_compressed_spec_i = 1'b0;
    iside_wait_i = 1'b0;
    jump_i = 1'b0;
    branch_i = 1'b0;
    branch_taken_i = 1'b0;
    mem_load_i = 1'b0;
    mem_store_i = 1'b0;
    dside_wait_i = 1'b0;
    mul_wait_i = 1'b0;
    div_wait_i = 1'b0;

    #2;
    rst_ni = 1'b1;
    csr_access_i = 1'b1;
    csr_op_i = CSR_OP_WRITE;
    csr_op_en_i = 1'b1;
    csr_addr_i = csr_num_e'(CSR_OFF_PMP_CFG);

    csr_wdata_i = 32'h0000_009c;
    #0;
    $display("exec_only suppress=%0b we=%0b illegal=%0b",
             dut.g_pmp_registers.pmp_cfg_wr_suppress[0],
             dut.g_pmp_registers.pmp_cfg_we[0],
             dut.illegal_csr_insn_o);

    csr_wdata_i = 32'h0000_009d;
    #0;
    $display("read_exec suppress=%0b we=%0b illegal=%0b",
             dut.g_pmp_registers.pmp_cfg_wr_suppress[0],
             dut.g_pmp_registers.pmp_cfg_we[0],
             dut.illegal_csr_insn_o);

    csr_wdata_i = 32'h0000_009f;
    #0;
    $display("all_perms suppress=%0b we=%0b illegal=%0b",
             dut.g_pmp_registers.pmp_cfg_wr_suppress[0],
             dut.g_pmp_registers.pmp_cfg_we[0],
             dut.illegal_csr_insn_o);
    $finish;
  end
endmodule
""",
    ) == [
        "exec_only suppress=1 we=0 illegal=0",
        "read_exec suppress=1 we=0 illegal=0",
        "all_perms suppress=0 we=1 illegal=0",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_cs_registers_assignment_patterns_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_cs_registers module agrees on mcause/mstatus assignment-pattern updates."""
    assert _run_ibex_cs_registers_probe(
        engine,
        tmp_path,
        "ibex_cs_registers_pattern_probe_tb",
        """\
module ibex_cs_registers_pattern_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic [31:0] hart_id_i;
  logic csr_mtvec_init_i;
  logic [31:0] boot_addr_i;
  logic csr_access_i;
  csr_num_e csr_addr_i;
  logic [31:0] csr_wdata_i;
  csr_op_e csr_op_i;
  logic csr_op_en_i;
  logic irq_software_i;
  logic irq_timer_i;
  logic irq_external_i;
  logic [14:0] irq_fast_i;
  logic nmi_mode_i;
  logic debug_mode_i;
  logic debug_mode_entering_i;
  dbg_cause_e debug_cause_i;
  logic debug_csr_save_i;
  logic [31:0] pc_if_i;
  logic [31:0] pc_id_i;
  logic [31:0] pc_wb_i;
  logic ic_scr_key_valid_i;
  logic csr_save_if_i;
  logic csr_save_id_i;
  logic csr_save_wb_i;
  logic csr_restore_mret_i;
  logic csr_restore_dret_i;
  logic csr_save_cause_i;
  exc_cause_t csr_mcause_i;
  logic [31:0] csr_mtval_i;
  logic instr_ret_i;
  logic instr_ret_compressed_i;
  logic instr_ret_spec_i;
  logic instr_ret_compressed_spec_i;
  logic iside_wait_i;
  logic jump_i;
  logic branch_i;
  logic branch_taken_i;
  logic mem_load_i;
  logic mem_store_i;
  logic dside_wait_i;
  logic mul_wait_i;
  logic div_wait_i;

  ibex_cs_registers #(
    .PMPEnable(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .hart_id_i(hart_id_i),
    .csr_mtvec_init_i(csr_mtvec_init_i),
    .boot_addr_i(boot_addr_i),
    .csr_access_i(csr_access_i),
    .csr_addr_i(csr_addr_i),
    .csr_wdata_i(csr_wdata_i),
    .csr_op_i(csr_op_i),
    .csr_op_en_i(csr_op_en_i),
    .irq_software_i(irq_software_i),
    .irq_timer_i(irq_timer_i),
    .irq_external_i(irq_external_i),
    .irq_fast_i(irq_fast_i),
    .nmi_mode_i(nmi_mode_i),
    .debug_mode_i(debug_mode_i),
    .debug_mode_entering_i(debug_mode_entering_i),
    .debug_cause_i(debug_cause_i),
    .debug_csr_save_i(debug_csr_save_i),
    .pc_if_i(pc_if_i),
    .pc_id_i(pc_id_i),
    .pc_wb_i(pc_wb_i),
    .ic_scr_key_valid_i(ic_scr_key_valid_i),
    .csr_save_if_i(csr_save_if_i),
    .csr_save_id_i(csr_save_id_i),
    .csr_save_wb_i(csr_save_wb_i),
    .csr_restore_mret_i(csr_restore_mret_i),
    .csr_restore_dret_i(csr_restore_dret_i),
    .csr_save_cause_i(csr_save_cause_i),
    .csr_mcause_i(csr_mcause_i),
    .csr_mtval_i(csr_mtval_i),
    .instr_ret_i(instr_ret_i),
    .instr_ret_compressed_i(instr_ret_compressed_i),
    .instr_ret_spec_i(instr_ret_spec_i),
    .instr_ret_compressed_spec_i(instr_ret_compressed_spec_i),
    .iside_wait_i(iside_wait_i),
    .jump_i(jump_i),
    .branch_i(branch_i),
    .branch_taken_i(branch_taken_i),
    .mem_load_i(mem_load_i),
    .mem_store_i(mem_store_i),
    .dside_wait_i(dside_wait_i),
    .mul_wait_i(mul_wait_i),
    .div_wait_i(div_wait_i)
  );

  always #1 clk_i = ~clk_i;

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    hart_id_i = '0;
    csr_mtvec_init_i = 1'b0;
    boot_addr_i = '0;
    csr_access_i = 1'b0;
    csr_addr_i = csr_num_e'(12'h000);
    csr_wdata_i = '0;
    csr_op_i = CSR_OP_READ;
    csr_op_en_i = 1'b0;
    irq_software_i = 1'b0;
    irq_timer_i = 1'b0;
    irq_external_i = 1'b0;
    irq_fast_i = '0;
    nmi_mode_i = 1'b0;
    debug_mode_i = 1'b0;
    debug_mode_entering_i = 1'b0;
    debug_cause_i = dbg_cause_e'(0);
    debug_csr_save_i = 1'b0;
    pc_if_i = '0;
    pc_id_i = '0;
    pc_wb_i = '0;
    ic_scr_key_valid_i = 1'b0;
    csr_save_if_i = 1'b0;
    csr_save_id_i = 1'b0;
    csr_save_wb_i = 1'b0;
    csr_restore_mret_i = 1'b0;
    csr_restore_dret_i = 1'b0;
    csr_save_cause_i = 1'b0;
    csr_mcause_i = '0;
    csr_mtval_i = '0;
    instr_ret_i = 1'b0;
    instr_ret_compressed_i = 1'b0;
    instr_ret_spec_i = 1'b0;
    instr_ret_compressed_spec_i = 1'b0;
    iside_wait_i = 1'b0;
    jump_i = 1'b0;
    branch_i = 1'b0;
    branch_taken_i = 1'b0;
    mem_load_i = 1'b0;
    mem_store_i = 1'b0;
    dside_wait_i = 1'b0;
    mul_wait_i = 1'b0;
    div_wait_i = 1'b0;

    #2;
    rst_ni = 1'b1;
    csr_access_i = 1'b1;
    csr_op_i = CSR_OP_WRITE;
    csr_op_en_i = 1'b1;

    csr_addr_i = CSR_MCAUSE;
    csr_wdata_i = 32'h8000_000b;
    #0;
    $display("mcause_ext irq_ext=%0b irq_int=%0b lower=%0d en=%0b",
             dut.mcause_d.irq_ext,
             dut.mcause_d.irq_int,
             dut.mcause_d.lower_cause,
             dut.mcause_en);

    csr_wdata_i = 32'hc000_0007;
    #0;
    $display("mcause_int irq_ext=%0b irq_int=%0b lower=%0d en=%0b",
             dut.mcause_d.irq_ext,
             dut.mcause_d.irq_int,
             dut.mcause_d.lower_cause,
             dut.mcause_en);

    csr_addr_i = CSR_MSTATUS;
    csr_wdata_i = '0;
    csr_wdata_i[CSR_MSTATUS_MIE_BIT] = 1'b1;
    csr_wdata_i[CSR_MSTATUS_MPIE_BIT] = 1'b1;
    csr_wdata_i[CSR_MSTATUS_MPP_BIT_HIGH:CSR_MSTATUS_MPP_BIT_LOW] = PRIV_LVL_S;
    csr_wdata_i[CSR_MSTATUS_MPRV_BIT] = 1'b1;
    csr_wdata_i[CSR_MSTATUS_TW_BIT] = 1'b1;
    #0;
    $display("mstatus_illegal mie=%0b mpie=%0b mpp=%0b mprv=%0b tw=%0b en=%0b",
             dut.mstatus_d.mie,
             dut.mstatus_d.mpie,
             dut.mstatus_d.mpp,
             dut.mstatus_d.mprv,
             dut.mstatus_d.tw,
             dut.mstatus_en);

    csr_wdata_i = '0;
    csr_wdata_i[CSR_MSTATUS_MPP_BIT_HIGH:CSR_MSTATUS_MPP_BIT_LOW] = PRIV_LVL_M;
    #0;
    $display("mstatus_m mie=%0b mpie=%0b mpp=%0b mprv=%0b tw=%0b en=%0b",
             dut.mstatus_d.mie,
             dut.mstatus_d.mpie,
             dut.mstatus_d.mpp,
             dut.mstatus_d.mprv,
             dut.mstatus_d.tw,
             dut.mstatus_en);
    $finish;
  end
endmodule
""",
    ) == [
        "mcause_ext irq_ext=1 irq_int=0 lower=11 en=1",
        "mcause_int irq_ext=0 irq_int=1 lower=7 en=1",
        "mstatus_illegal mie=1 mpie=1 mpp=0 mprv=1 tw=1 en=1",
        "mstatus_m mie=0 mpie=0 mpp=11 mprv=0 tw=0 en=1",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_alu_assignment_patterns_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_alu module agrees on unpacked-array assignment-pattern paths."""
    assert _run_ibex_alu_probe(
        engine,
        tmp_path,
        "ibex_alu_probe_tb",
        """\
module ibex_alu_probe_tb;
  import ibex_pkg::*;

  alu_op_e operator_full;
  logic [31:0] operand_a_full;
  logic [31:0] operand_b_full;
  logic instr_first_cycle_full;
  logic [32:0] multdiv_operand_a_full;
  logic [32:0] multdiv_operand_b_full;
  logic multdiv_sel_full;
  logic [31:0] imd_val_q_full [2];
  logic [31:0] imd_val_d_full [2];
  logic [1:0]  imd_val_we_full;
  logic [31:0] result_full;

  alu_op_e operator_none;
  logic [31:0] operand_a_none;
  logic [31:0] operand_b_none;
  logic instr_first_cycle_none;
  logic [32:0] multdiv_operand_a_none;
  logic [32:0] multdiv_operand_b_none;
  logic multdiv_sel_none;
  logic [31:0] imd_val_q_none [2];
  logic [31:0] imd_val_d_none [2];
  logic [1:0]  imd_val_we_none;
  logic [31:0] result_none;

  alu_op_e operator_bcomp;
  logic [31:0] operand_a_bcomp;
  logic [31:0] operand_b_bcomp;
  logic instr_first_cycle_bcomp;
  logic [32:0] multdiv_operand_a_bcomp;
  logic [32:0] multdiv_operand_b_bcomp;
  logic multdiv_sel_bcomp;
  logic [31:0] imd_val_q_bcomp [2];
  logic [31:0] imd_val_d_bcomp [2];
  logic [1:0]  imd_val_we_bcomp;
  logic [31:0] result_bcomp;

  ibex_alu #(.RV32B(RV32BFull)) dut_full (
    .operator_i(operator_full),
    .operand_a_i(operand_a_full),
    .operand_b_i(operand_b_full),
    .instr_first_cycle_i(instr_first_cycle_full),
    .multdiv_operand_a_i(multdiv_operand_a_full),
    .multdiv_operand_b_i(multdiv_operand_b_full),
    .multdiv_sel_i(multdiv_sel_full),
    .imd_val_q_i(imd_val_q_full),
    .imd_val_d_o(imd_val_d_full),
    .imd_val_we_o(imd_val_we_full),
    .adder_result_o(),
    .adder_result_ext_o(),
    .result_o(result_full),
    .comparison_result_o(),
    .is_equal_result_o()
  );

  ibex_alu #(.RV32B(RV32BNone)) dut_none (
    .operator_i(operator_none),
    .operand_a_i(operand_a_none),
    .operand_b_i(operand_b_none),
    .instr_first_cycle_i(instr_first_cycle_none),
    .multdiv_operand_a_i(multdiv_operand_a_none),
    .multdiv_operand_b_i(multdiv_operand_b_none),
    .multdiv_sel_i(multdiv_sel_none),
    .imd_val_q_i(imd_val_q_none),
    .imd_val_d_o(imd_val_d_none),
    .imd_val_we_o(imd_val_we_none),
    .adder_result_o(),
    .adder_result_ext_o(),
    .result_o(result_none),
    .comparison_result_o(),
    .is_equal_result_o()
  );

  ibex_alu #(.RV32B(RV32BFull)) dut_bcomp (
    .operator_i(operator_bcomp),
    .operand_a_i(operand_a_bcomp),
    .operand_b_i(operand_b_bcomp),
    .instr_first_cycle_i(instr_first_cycle_bcomp),
    .multdiv_operand_a_i(multdiv_operand_a_bcomp),
    .multdiv_operand_b_i(multdiv_operand_b_bcomp),
    .multdiv_sel_i(multdiv_sel_bcomp),
    .imd_val_q_i(imd_val_q_bcomp),
    .imd_val_d_o(imd_val_d_bcomp),
    .imd_val_we_o(imd_val_we_bcomp),
    .adder_result_o(),
    .adder_result_ext_o(),
    .result_o(result_bcomp),
    .comparison_result_o(),
    .is_equal_result_o()
  );

  initial begin
    operator_full = ALU_CMOV;
    operand_a_full = 32'h1234_5678;
    operand_b_full = 32'h0000_0000;
    instr_first_cycle_full = 1'b1;
    multdiv_operand_a_full = '0;
    multdiv_operand_b_full = '0;
    multdiv_sel_full = 1'b0;
    imd_val_q_full[0] = 32'hDEAD_BEEF;
    imd_val_q_full[1] = 32'hCAFE_BABE;

    operator_none = ALU_CMOV;
    operand_a_none = 32'h89AB_CDEF;
    operand_b_none = 32'h0000_0000;
    instr_first_cycle_none = 1'b1;
    multdiv_operand_a_none = '0;
    multdiv_operand_b_none = '0;
    multdiv_sel_none = 1'b0;
    imd_val_q_none[0] = 32'h1111_1111;
    imd_val_q_none[1] = 32'h2222_2222;

    operator_bcomp = ALU_BCOMPRESS;
    operand_a_bcomp = 32'h0000_00F3;
    operand_b_bcomp = 32'h0000_000F;
    instr_first_cycle_bcomp = 1'b1;
    multdiv_operand_a_bcomp = '0;
    multdiv_operand_b_bcomp = '0;
    multdiv_sel_bcomp = 1'b0;
    imd_val_q_bcomp[0] = '0;
    imd_val_q_bcomp[1] = '0;

    #0;
    $display("cmov d0=%08h d1=%08h we=%02b res=%08h",
             imd_val_d_full[0], imd_val_d_full[1], imd_val_we_full, result_full);
    $display("none d0=%08h d1=%08h we=%02b res=%08h",
             imd_val_d_none[0], imd_val_d_none[1], imd_val_we_none, result_none);
    $display("bcomp d0=%08h d1=%08h we=%02b res=%08h",
             imd_val_d_bcomp[0], imd_val_d_bcomp[1], imd_val_we_bcomp, result_bcomp);
    $finish;
  end
endmodule
""",
    ) == [
        "cmov d0=12345678 d1=00000000 we=01 res=12345678",
        "none d0=00000000 d1=00000000 we=00 res=00000000",
        "bcomp d0=00000005 d1=00ff0001 we=11 res=c0000000",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_ex_block_intermediate_bridge_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_ex_block module agrees on its ALU intermediate-value array bridge."""
    assert _run_ibex_ex_block_probe(
        engine,
        tmp_path,
        "ibex_ex_block_probe_tb",
        """\
module ibex_ex_block_probe_tb;
  import ibex_pkg::*;

  alu_op_e alu_operator_full;
  logic [31:0] alu_operand_a_full;
  logic [31:0] alu_operand_b_full;
  logic alu_instr_first_cycle_full;
  logic [33:0] imd_val_q_full [2];
  logic [33:0] imd_val_d_full [2];
  logic [1:0] imd_val_we_full;
  logic [31:0] result_full;
  logic ex_valid_full;

  alu_op_e alu_operator_none;
  logic [31:0] alu_operand_a_none;
  logic [31:0] alu_operand_b_none;
  logic alu_instr_first_cycle_none;
  logic [33:0] imd_val_q_none [2];
  logic [33:0] imd_val_d_none [2];
  logic [1:0] imd_val_we_none;
  logic [31:0] result_none;
  logic ex_valid_none;

  ibex_ex_block #(
    .RV32M(RV32MNone),
    .RV32B(RV32BFull),
    .BranchTargetALU(1'b0)
  ) dut_full (
    .clk_i(1'b0),
    .rst_ni(1'b1),
    .alu_operator_i(alu_operator_full),
    .alu_operand_a_i(alu_operand_a_full),
    .alu_operand_b_i(alu_operand_b_full),
    .alu_instr_first_cycle_i(alu_instr_first_cycle_full),
    .bt_a_operand_i('0),
    .bt_b_operand_i('0),
    .multdiv_operator_i(md_op_e'(0)),
    .mult_en_i(1'b0),
    .div_en_i(1'b0),
    .mult_sel_i(1'b0),
    .div_sel_i(1'b0),
    .multdiv_signed_mode_i('0),
    .multdiv_operand_a_i('0),
    .multdiv_operand_b_i('0),
    .multdiv_ready_id_i(1'b0),
    .data_ind_timing_i(1'b0),
    .imd_val_we_o(imd_val_we_full),
    .imd_val_d_o(imd_val_d_full),
    .imd_val_q_i(imd_val_q_full),
    .alu_adder_result_ex_o(),
    .result_ex_o(result_full),
    .branch_target_o(),
    .branch_decision_o(),
    .ex_valid_o(ex_valid_full)
  );

  ibex_ex_block #(
    .RV32M(RV32MNone),
    .RV32B(RV32BNone),
    .BranchTargetALU(1'b0)
  ) dut_none (
    .clk_i(1'b0),
    .rst_ni(1'b1),
    .alu_operator_i(alu_operator_none),
    .alu_operand_a_i(alu_operand_a_none),
    .alu_operand_b_i(alu_operand_b_none),
    .alu_instr_first_cycle_i(alu_instr_first_cycle_none),
    .bt_a_operand_i('0),
    .bt_b_operand_i('0),
    .multdiv_operator_i(md_op_e'(0)),
    .mult_en_i(1'b0),
    .div_en_i(1'b0),
    .mult_sel_i(1'b0),
    .div_sel_i(1'b0),
    .multdiv_signed_mode_i('0),
    .multdiv_operand_a_i('0),
    .multdiv_operand_b_i('0),
    .multdiv_ready_id_i(1'b0),
    .data_ind_timing_i(1'b0),
    .imd_val_we_o(imd_val_we_none),
    .imd_val_d_o(imd_val_d_none),
    .imd_val_q_i(imd_val_q_none),
    .alu_adder_result_ex_o(),
    .result_ex_o(result_none),
    .branch_target_o(),
    .branch_decision_o(),
    .ex_valid_o(ex_valid_none)
  );

  initial begin
    alu_operator_full = ALU_CMOV;
    alu_operand_a_full = 32'h1234_5678;
    alu_operand_b_full = 32'h0000_0001;
    alu_instr_first_cycle_full = 1'b0;
    imd_val_q_full[0] = 34'h2_DEAD_BEEF;
    imd_val_q_full[1] = 34'h1_CAFE_BABE;

    alu_operator_none = ALU_CMOV;
    alu_operand_a_none = 32'h89AB_CDEF;
    alu_operand_b_none = 32'h0000_0000;
    alu_instr_first_cycle_none = 1'b1;
    imd_val_q_none[0] = 34'h3_1111_1111;
    imd_val_q_none[1] = 34'h0_2222_2222;

    #0;
    $display("ex_full d0=%09h d1=%09h we=%02b res=%08h valid=%0b",
             imd_val_d_full[0], imd_val_d_full[1], imd_val_we_full, result_full, ex_valid_full);
    $display("ex_none d0=%09h d1=%09h we=%02b res=%08h valid=%0b",
             imd_val_d_none[0], imd_val_d_none[1], imd_val_we_none, result_none, ex_valid_none);
    $finish;
  end
endmodule
""",
    ) == [
        "ex_full d0=012345678 d1=000000000 we=00 res=deadbeef valid=1",
        "ex_none d0=000000000 d1=000000000 we=00 res=00000000 valid=1",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_fast_irq_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on fast-IRQ cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_probe_tb",
        """\
module ibex_controller_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = '0;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    irq_pending_i = 1'b1;
    irqs_i.irq_fast[3] = 1'b1;

    #5;
    #0;
    $display("fast_irq ext=%0b int=%0b cause=%0d pc_set=%0b save=%0b",
             exc_cause_o.irq_ext,
             exc_cause_o.irq_int,
             exc_cause_o.lower_cause,
             pc_set_o,
             csr_save_cause_o);
    $finish;
  end
endmodule
""",
    ) == ["fast_irq ext=1 int=0 cause=19 pc_set=1 save=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_internal_nmi_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on internal-NMI cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_nmi_probe_tb",
        """\
module ibex_controller_nmi_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b1)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("nmi_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = '0;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = 32'h1A11_0123;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    mem_resp_intg_err_i = 1'b1;
    #2;
    mem_resp_intg_err_i = 1'b0;
    #8;
    $finish;
  end
endmodule
""",
    ) == ["nmi_edge ext=0 int=1 cause=0 mtval=1a110123 pc_set=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_illegal_compressed_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on compressed illegal-instruction cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_illegal_probe_tb",
        """\
module ibex_controller_illegal_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("illegal_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = 32'h89AB_CDEF;
    instr_compressed_i = 16'h6141;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    illegal_insn_i = 1'b1;
    instr_is_compressed_i = 1'b1;
    #2;
    instr_valid_i = 1'b0;
    illegal_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["illegal_edge ext=0 int=0 cause=2 mtval=00006141 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_load_fault_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on load-fault cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_load_probe_tb",
        """\
module ibex_controller_load_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("load_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = 32'h1111_2222;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    load_err_i = 1'b1;
    #2;
    load_err_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["load_edge ext=0 int=0 cause=5 mtval=11112222 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_store_fault_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on store-fault cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_store_probe_tb",
        """\
module ibex_controller_store_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("store_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = 32'h1111_2222;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    store_err_i = 1'b1;
    #2;
    store_err_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["store_edge ext=0 int=0 cause=7 mtval=11112222 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_fetch_fault_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on fetch-fault cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_fetch_probe_tb",
        """\
module ibex_controller_fetch_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("fetch_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = 32'h1234_5678;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    instr_fetch_err_i = 1'b1;
    instr_fetch_err_plus2_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["fetch_edge ext=0 int=0 cause=1 mtval=00000102 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_breakpoint_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on non-debug breakpoint cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_break_probe_tb",
        """\
module ibex_controller_break_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("break_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = 32'h1234_5678;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    ebrk_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    ebrk_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["break_edge ext=0 int=0 cause=3 mtval=00000000 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_ecall_mmode_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on M-mode ECALL cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_ecall_m_probe_tb",
        """\
module ibex_controller_ecall_m_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("ecall_m_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = 32'h1234_5678;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    ecall_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    ecall_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["ecall_m_edge ext=0 int=0 cause=11 mtval=00000000 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_ecall_umode_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on U-mode ECALL cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_ecall_u_probe_tb",
        """\
module ibex_controller_ecall_u_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("ecall_u_edge ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b idsave=%0b flush=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               csr_save_id_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = 32'h1234_5678;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_U;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    ecall_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    ecall_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["ecall_u_edge ext=0 int=0 cause=8 mtval=00000000 pc_set=1 idsave=0 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_ebreak_debug_entry_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on forced EBREAK debug entry."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_ebreak_debug_probe_tb",
        """\
module ibex_controller_ebreak_debug_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (debug_mode_entering_o || debug_csr_save_o || csr_save_cause_o) begin
      $display("dbg_enter enter=%0b dsave=%0b csave=%0b idsave=%0b pc_set=%0b flush=%0b cause=%0d dbgcause=%0d dbgmode=%0b",
               debug_mode_entering_o,
               debug_csr_save_o,
               csr_save_cause_o,
               csr_save_id_o,
               pc_set_o,
               flush_id_o,
               exc_cause_o.lower_cause,
               debug_cause_o,
               debug_mode_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = 32'h0010_0073;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b1;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    ebrk_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    ebrk_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["dbg_enter enter=1 dsave=1 csave=1 idsave=1 pc_set=1 flush=1 cause=0 dbgcause=1 dbgmode=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_debug_request_entry_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on halt-request debug entry."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_debugreq_probe_tb",
        """\
module ibex_controller_debugreq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (debug_csr_save_o) begin
      $display("dbg_req enter=%0b dsave=%0b ifsave=%0b csave=%0b pc_set=%0b flush=%0b dbgcause=%0d dbgmode=%0b",
               debug_mode_entering_o,
               debug_csr_save_o,
               csr_save_if_o,
               csr_save_cause_o,
               pc_set_o,
               flush_id_o,
               debug_cause_o,
               debug_mode_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #2;
    debug_req_i = 1'b1;
    #8;
    debug_req_i = 1'b0;
    #8;
    $finish;
  end
endmodule
""",
    ) == ["dbg_req enter=1 dsave=1 ifsave=1 csave=1 pc_set=1 flush=1 dbgcause=3 dbgmode=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_single_step_entry_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on single-step debug entry."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_sstep_probe_tb",
        """\
module ibex_controller_sstep_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (debug_csr_save_o) begin
      $display("sstep enter=%0b dsave=%0b ifsave=%0b csave=%0b pc_set=%0b flush=%0b dbgcause=%0d dbgmode=%0b",
               debug_mode_entering_o,
               debug_csr_save_o,
               csr_save_if_o,
               csr_save_cause_o,
               pc_set_o,
               flush_id_o,
               debug_cause_o,
               debug_mode_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    debug_single_step_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    debug_single_step_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["sstep enter=1 dsave=1 ifsave=1 csave=1 pc_set=1 flush=1 dbgcause=4 dbgmode=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_trigger_match_entry_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on trigger-match debug entry."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_trigger_match_probe_tb",
        """\
module ibex_controller_trigger_match_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (debug_csr_save_o) begin
      $display("trigger enter=%0b dsave=%0b ifsave=%0b csave=%0b pc_set=%0b flush=%0b dbgcause=%0d dbgmode=%0b",
               debug_mode_entering_o,
               debug_csr_save_o,
               csr_save_if_o,
               csr_save_cause_o,
               pc_set_o,
               flush_id_o,
               debug_cause_o,
               debug_mode_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    trigger_match_i = 1'b1;
    #8;
    instr_valid_i = 1'b0;
    trigger_match_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["trigger enter=1 dsave=1 ifsave=1 csave=1 pc_set=1 flush=1 dbgcause=2 dbgmode=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_mret_restore_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on MRET restore control."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_mret_probe_tb",
        """\
module ibex_controller_mret_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_restore_mret_id_o) begin
      $display("mret_edge pc_set=%0b mret=%0b dret=%0b nmi=%0b dbgmode=%0b pcmux=%0d flush=%0b",
               pc_set_o,
               csr_restore_mret_id_o,
               csr_restore_dret_id_o,
               nmi_mode_o,
               debug_mode_o,
               pc_mux_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #6;
    instr_valid_i = 1'b1;
    mret_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    mret_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["mret_edge pc_set=1 mret=1 dret=0 nmi=0 dbgmode=0 pcmux=3 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_mret_restore_from_nmi_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on MRET restore while exiting NMI mode."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_mret_nmi_probe_tb",
        """\
module ibex_controller_mret_nmi_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b1)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_restore_mret_id_o) begin
      $display("mret_nmi pc_set=%0b mret=%0b nmi=%0b dbgmode=%0b pcmux=%0d flush=%0b",
               pc_set_o,
               csr_restore_mret_id_o,
               nmi_mode_o,
               debug_mode_o,
               pc_mux_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = 32'h1A11_0123;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    mem_resp_intg_err_i = 1'b1;
    #2;
    mem_resp_intg_err_i = 1'b0;
    wait (nmi_mode_o == 1'b1);
    #4;
    instr_valid_i = 1'b1;
    mret_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    mret_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["mret_nmi pc_set=1 mret=1 nmi=1 dbgmode=0 pcmux=3 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_dret_restore_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on DRET restore control."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_dret_probe_tb",
        """\
module ibex_controller_dret_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_restore_dret_id_o) begin
      $display("dret_edge pc_set=%0b dret=%0b dbgmode=%0b pcmux=%0d flush=%0b",
               pc_set_o,
               csr_restore_dret_id_o,
               debug_mode_o,
               pc_mux_o,
               flush_id_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #2;
    debug_req_i = 1'b1;
    #8;
    debug_req_i = 1'b0;
    wait (debug_mode_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    dret_insn_i = 1'b1;
    #6;
    instr_valid_i = 1'b0;
    dret_insn_i = 1'b0;
    #10;
    $finish;
  end
endmodule
""",
    ) == ["dret_edge pc_set=1 dret=1 dbgmode=1 pcmux=4 flush=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_sleep_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on WFI sleep-state control."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_probe_tb",
        """\
module ibex_controller_wfi_probe_tb;
  import ibex_pkg::*;

  integer cycle;
  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    cycle <= cycle + 1;
    if (cycle == 6) begin
      $display("wfi_sleep busy=%0b run=%0b req=%0b flush=%0b pc_set=%0b clr=%0b ready=%0b dbg=%0b",
               ctrl_busy_o,
               controller_run_o,
               instr_req_o,
               flush_id_o,
               pc_set_o,
               instr_valid_clear_o,
               id_in_ready_o,
               debug_mode_o);
    end
  end

  initial begin
    cycle = 0;
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #18;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_sleep busy=0 run=0 req=0 flush=1 pc_set=0 clr=1 ready=0 dbg=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_debug_wakeup_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on debug-request wakeup from WFI sleep."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_wakeup_probe_tb",
        """\
module ibex_controller_wfi_wakeup_probe_tb;
  import ibex_pkg::*;

  integer cycle;
  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    cycle <= cycle + 1;
    if (cycle == 7) begin
      $display("wfi_wakeup busy=%0b run=%0b req=%0b flush=%0b pc_set=%0b clr=%0b ready=%0b dbgreq=%0b",
               ctrl_busy_o,
               controller_run_o,
               instr_req_o,
               flush_id_o,
               pc_set_o,
               instr_valid_clear_o,
               id_in_ready_o,
               debug_req_i);
    end
  end

  initial begin
    cycle = 0;
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    debug_req_i = 1'b1;
    #6;
    debug_req_i = 1'b0;
    #12;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_wakeup busy=1 run=0 req=0 flush=1 pc_set=0 clr=1 ready=0 dbgreq=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_fast_irq_service_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on fast-IRQ service after WFI wakeup."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_irq_probe_tb",
        """\
module ibex_controller_wfi_irq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("wfi_irq ext=%0b int=%0b cause=%0d pc_set=%0b run=%0b wfi=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               controller_run_o,
               wfi_insn_i);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    irq_pending_i = 1'b1;
    irqs_i.irq_fast[3] = 1'b1;
    #10;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #10;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_irq ext=1 int=0 cause=19 pc_set=1 run=0 wfi=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_external_nmi_service_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on external NMI service after WFI wakeup."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_nmi_probe_tb",
        """\
module ibex_controller_wfi_nmi_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("wfi_nmi ext=%0b int=%0b cause=%0d pc_set=%0b run=%0b wfi=%0b nmi=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               controller_run_o,
               wfi_insn_i,
               nmi_mode_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    irq_nm_ext_i = 1'b1;
    #10;
    irq_nm_ext_i = 1'b0;
    #10;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_nmi ext=1 int=0 cause=31 pc_set=1 run=0 wfi=1 nmi=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_internal_nmi_service_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on internal NMI service after WFI wakeup."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_intnmi_probe_tb",
        """\
module ibex_controller_wfi_intnmi_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b1)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("wfi_intnmi ext=%0b int=%0b cause=%0d mtval=%08h pc_set=%0b run=%0b wfi=%0b nmi=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               csr_mtval_o,
               pc_set_o,
               controller_run_o,
               wfi_insn_i,
               nmi_mode_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = 32'h1A11_0456;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    mem_resp_intg_err_i = 1'b1;
    #2;
    mem_resp_intg_err_i = 1'b0;
    #10;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
        max_time=40,
    ) == ["wfi_intnmi ext=0 int=1 cause=0 mtval=1a110456 pc_set=1 run=0 wfi=1 nmi=0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_single_step_wakeup_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on single-step wakeup from WFI sleep."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_step_probe_tb",
        """\
module ibex_controller_wfi_step_probe_tb;
  import ibex_pkg::*;

  integer cycle;
  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    cycle <= cycle + 1;
    if (cycle == 7) begin
      $display("wfi_step busy=%0b run=%0b req=%0b flush=%0b pc_set=%0b clr=%0b ready=%0b step=%0b",
               ctrl_busy_o,
               controller_run_o,
               instr_req_o,
               flush_id_o,
               pc_set_o,
               instr_valid_clear_o,
               id_in_ready_o,
               debug_single_step_i);
    end
  end

  initial begin
    cycle = 0;
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    debug_single_step_i = 1'b1;
    #6;
    debug_single_step_i = 1'b0;
    #12;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_step busy=1 run=0 req=0 flush=1 pc_set=0 clr=1 ready=0 step=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_debug_mode_nop_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on the first self-wake cycle for WFI in debug mode."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_debugmode_probe_tb",
        """\
module ibex_controller_wfi_debugmode_probe_tb;
  import ibex_pkg::*;

  integer cycle;
  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    cycle <= cycle + 1;
    if (cycle == 8) begin
      $display("wfi_dbg busy=%0b run=%0b req=%0b flush=%0b pc_set=%0b clr=%0b ready=%0b dbg=%0b wfi=%0b",
               ctrl_busy_o,
               controller_run_o,
               instr_req_o,
               flush_id_o,
               pc_set_o,
               instr_valid_clear_o,
               id_in_ready_o,
               debug_mode_o,
               wfi_insn_i);
    end
  end

  initial begin
    cycle = 0;
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    #2;
    debug_req_i = 1'b1;
    #8;
    debug_req_i = 1'b0;
    wait (debug_mode_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #18;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #8;
    $finish;
  end
endmodule
""",
    ) == ["wfi_dbg busy=1 run=0 req=1 flush=1 pc_set=0 clr=1 ready=0 dbg=1 wfi=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_external_irq_service_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on external interrupt service after WFI wakeup."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_extirq_probe_tb",
        """\
module ibex_controller_wfi_extirq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("wfi_extirq ext=%0b int=%0b cause=%0d pc_set=%0b run=%0b wfi=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               controller_run_o,
               wfi_insn_i);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    irq_pending_i = 1'b1;
    irqs_i.irq_external = 1'b1;
    #10;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #10;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_extirq ext=1 int=0 cause=11 pc_set=1 run=0 wfi=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_software_irq_service_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on software interrupt service after WFI wakeup."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_swirq_probe_tb",
        """\
module ibex_controller_wfi_swirq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("wfi_swirq ext=%0b int=%0b cause=%0d pc_set=%0b run=%0b wfi=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               controller_run_o,
               wfi_insn_i);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    irq_pending_i = 1'b1;
    irqs_i.irq_software = 1'b1;
    #10;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #10;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_swirq ext=1 int=0 cause=3 pc_set=1 run=0 wfi=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_wfi_timer_irq_service_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on timer interrupt service after WFI wakeup."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_wfi_timerirq_probe_tb",
        """\
module ibex_controller_wfi_timerirq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o && exc_cause_o.lower_cause != 0) begin
      $display("wfi_timerirq ext=%0b int=%0b cause=%0d pc_set=%0b run=%0b wfi=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               controller_run_o,
               wfi_insn_i);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    wait (controller_run_o == 1'b1);
    #2;
    instr_valid_i = 1'b1;
    wfi_insn_i = 1'b1;
    #10;
    irq_pending_i = 1'b1;
    irqs_i.irq_timer = 1'b1;
    #10;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #10;
    instr_valid_i = 1'b0;
    wfi_insn_i = 1'b0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["wfi_timerirq ext=1 int=0 cause=7 pc_set=1 run=0 wfi=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_external_irq_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on external interrupt cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_extirq_probe_tb",
        """\
module ibex_controller_extirq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("extirq ext=%0b int=%0b cause=%0d pc_set=%0b save=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               csr_save_cause_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    irq_pending_i = 1'b1;
    irqs_i.irq_external = 1'b1;
    #8;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["extirq ext=1 int=0 cause=11 pc_set=1 save=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_software_irq_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on software interrupt cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_swirq_probe_tb",
        """\
module ibex_controller_swirq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("swirq ext=%0b int=%0b cause=%0d pc_set=%0b save=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               csr_save_cause_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    irq_pending_i = 1'b1;
    irqs_i.irq_software = 1'b1;
    #8;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["swirq ext=1 int=0 cause=3 pc_set=1 save=1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ibex_controller_timer_irq_cause_cross_engine(engine: str, tmp_path: Path):
    """The real ibex_controller module agrees on timer interrupt cause packing."""
    assert _run_ibex_controller_probe(
        engine,
        tmp_path,
        "ibex_controller_timerirq_probe_tb",
        """\
module ibex_controller_timerirq_probe_tb;
  import ibex_pkg::*;

  logic clk_i;
  logic rst_ni;
  logic ctrl_busy_o;
  logic illegal_insn_i;
  logic ecall_insn_i;
  logic mret_insn_i;
  logic dret_insn_i;
  logic wfi_insn_i;
  logic ebrk_insn_i;
  logic csr_pipe_flush_i;
  logic instr_valid_i;
  logic [31:0] instr_i;
  logic [15:0] instr_compressed_i;
  logic instr_is_compressed_i;
  logic instr_bp_taken_i;
  logic instr_fetch_err_i;
  logic instr_fetch_err_plus2_i;
  logic [31:0] pc_id_i;
  logic instr_valid_clear_o;
  logic id_in_ready_o;
  logic controller_run_o;
  logic instr_exec_i;
  logic instr_req_o;
  logic pc_set_o;
  pc_sel_e pc_mux_o;
  logic nt_branch_mispredict_o;
  exc_pc_sel_e exc_pc_mux_o;
  exc_cause_t exc_cause_o;
  logic [31:0] lsu_addr_last_i;
  logic load_err_i;
  logic store_err_i;
  logic mem_resp_intg_err_i;
  logic wb_exception_o;
  logic id_exception_o;
  logic branch_set_i;
  logic branch_not_set_i;
  logic jump_set_i;
  logic csr_mstatus_mie_i;
  logic irq_pending_i;
  irqs_t irqs_i;
  logic irq_nm_ext_i;
  logic nmi_mode_o;
  logic debug_req_i;
  dbg_cause_e debug_cause_o;
  logic debug_csr_save_o;
  logic debug_mode_o;
  logic debug_mode_entering_o;
  logic debug_single_step_i;
  logic debug_ebreakm_i;
  logic debug_ebreaku_i;
  logic trigger_match_i;
  logic csr_save_if_o;
  logic csr_save_id_o;
  logic csr_save_wb_o;
  logic csr_restore_mret_id_o;
  logic csr_restore_dret_id_o;
  logic csr_save_cause_o;
  logic [31:0] csr_mtval_o;
  priv_lvl_e priv_mode_i;
  logic stall_id_i;
  logic stall_wb_i;
  logic flush_id_o;
  logic ready_wb_i;
  logic perf_jump_o;
  logic perf_tbranch_o;

  ibex_controller #(
    .WritebackStage(1'b0),
    .BranchPredictor(1'b0),
    .MemECC(1'b0)
  ) dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .ctrl_busy_o(ctrl_busy_o),
    .illegal_insn_i(illegal_insn_i),
    .ecall_insn_i(ecall_insn_i),
    .mret_insn_i(mret_insn_i),
    .dret_insn_i(dret_insn_i),
    .wfi_insn_i(wfi_insn_i),
    .ebrk_insn_i(ebrk_insn_i),
    .csr_pipe_flush_i(csr_pipe_flush_i),
    .instr_valid_i(instr_valid_i),
    .instr_i(instr_i),
    .instr_compressed_i(instr_compressed_i),
    .instr_is_compressed_i(instr_is_compressed_i),
    .instr_bp_taken_i(instr_bp_taken_i),
    .instr_fetch_err_i(instr_fetch_err_i),
    .instr_fetch_err_plus2_i(instr_fetch_err_plus2_i),
    .pc_id_i(pc_id_i),
    .instr_valid_clear_o(instr_valid_clear_o),
    .id_in_ready_o(id_in_ready_o),
    .controller_run_o(controller_run_o),
    .instr_exec_i(instr_exec_i),
    .instr_req_o(instr_req_o),
    .pc_set_o(pc_set_o),
    .pc_mux_o(pc_mux_o),
    .nt_branch_mispredict_o(nt_branch_mispredict_o),
    .exc_pc_mux_o(exc_pc_mux_o),
    .exc_cause_o(exc_cause_o),
    .lsu_addr_last_i(lsu_addr_last_i),
    .load_err_i(load_err_i),
    .store_err_i(store_err_i),
    .mem_resp_intg_err_i(mem_resp_intg_err_i),
    .wb_exception_o(wb_exception_o),
    .id_exception_o(id_exception_o),
    .branch_set_i(branch_set_i),
    .branch_not_set_i(branch_not_set_i),
    .jump_set_i(jump_set_i),
    .csr_mstatus_mie_i(csr_mstatus_mie_i),
    .irq_pending_i(irq_pending_i),
    .irqs_i(irqs_i),
    .irq_nm_ext_i(irq_nm_ext_i),
    .nmi_mode_o(nmi_mode_o),
    .debug_req_i(debug_req_i),
    .debug_cause_o(debug_cause_o),
    .debug_csr_save_o(debug_csr_save_o),
    .debug_mode_o(debug_mode_o),
    .debug_mode_entering_o(debug_mode_entering_o),
    .debug_single_step_i(debug_single_step_i),
    .debug_ebreakm_i(debug_ebreakm_i),
    .debug_ebreaku_i(debug_ebreaku_i),
    .trigger_match_i(trigger_match_i),
    .csr_save_if_o(csr_save_if_o),
    .csr_save_id_o(csr_save_id_o),
    .csr_save_wb_o(csr_save_wb_o),
    .csr_restore_mret_id_o(csr_restore_mret_id_o),
    .csr_restore_dret_id_o(csr_restore_dret_id_o),
    .csr_save_cause_o(csr_save_cause_o),
    .csr_mtval_o(csr_mtval_o),
    .priv_mode_i(priv_mode_i),
    .stall_id_i(stall_id_i),
    .stall_wb_i(stall_wb_i),
    .flush_id_o(flush_id_o),
    .ready_wb_i(ready_wb_i),
    .perf_jump_o(perf_jump_o),
    .perf_tbranch_o(perf_tbranch_o)
  );

  always #1 clk_i = ~clk_i;

  always @(posedge clk_i) begin
    if (csr_save_cause_o) begin
      $display("timerirq ext=%0b int=%0b cause=%0d pc_set=%0b save=%0b",
               exc_cause_o.irq_ext,
               exc_cause_o.irq_int,
               exc_cause_o.lower_cause,
               pc_set_o,
               csr_save_cause_o);
    end
  end

  initial begin
    clk_i = 1'b0;
    rst_ni = 1'b0;
    illegal_insn_i = 1'b0;
    ecall_insn_i = 1'b0;
    mret_insn_i = 1'b0;
    dret_insn_i = 1'b0;
    wfi_insn_i = 1'b0;
    ebrk_insn_i = 1'b0;
    csr_pipe_flush_i = 1'b0;
    instr_valid_i = 1'b0;
    instr_i = '0;
    instr_compressed_i = '0;
    instr_is_compressed_i = 1'b0;
    instr_bp_taken_i = 1'b0;
    instr_fetch_err_i = 1'b0;
    instr_fetch_err_plus2_i = 1'b0;
    pc_id_i = 32'h0000_0100;
    instr_exec_i = 1'b1;
    lsu_addr_last_i = '0;
    load_err_i = 1'b0;
    store_err_i = 1'b0;
    mem_resp_intg_err_i = 1'b0;
    wb_exception_o = 1'b0;
    id_exception_o = 1'b0;
    branch_set_i = 1'b0;
    branch_not_set_i = 1'b0;
    jump_set_i = 1'b0;
    csr_mstatus_mie_i = 1'b1;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    irq_nm_ext_i = 1'b0;
    debug_req_i = 1'b0;
    debug_single_step_i = 1'b0;
    debug_ebreakm_i = 1'b0;
    debug_ebreaku_i = 1'b0;
    trigger_match_i = 1'b0;
    priv_mode_i = PRIV_LVL_M;
    stall_id_i = 1'b0;
    stall_wb_i = 1'b0;
    ready_wb_i = 1'b1;

    #2;
    rst_ni = 1'b1;
    irq_pending_i = 1'b1;
    irqs_i.irq_timer = 1'b1;
    #8;
    irq_pending_i = 1'b0;
    irqs_i = '0;
    #4;
    $finish;
  end
endmodule
""",
    ) == ["timerirq ext=1 int=0 cause=7 pc_set=1 save=1"]
