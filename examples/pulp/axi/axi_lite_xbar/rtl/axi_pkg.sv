package axi_pkg;

  typedef logic [1:0] resp_t;
  typedef logic [2:0] prot_t;

  localparam resp_t RESP_OKAY = 2'b00;
  localparam resp_t RESP_EXOKAY = 2'b01;
  localparam resp_t RESP_SLVERR = 2'b10;
  localparam resp_t RESP_DECERR = 2'b11;

  typedef enum logic [9:0] {
    NO_LATENCY = 10'b0000000000,
    CUT_ALL_AX = 10'b1111100000
  } xbar_latency_e;

  typedef struct packed {
    logic [31:0] NoSlvPorts;
    logic [31:0] NoMstPorts;
    logic [31:0] MaxMstTrans;
    logic [31:0] MaxSlvTrans;
    logic        FallThrough;
    xbar_latency_e LatencyMode;
    logic [31:0] AxiIdWidthSlvPorts;
    logic [31:0] AxiIdUsedSlvPorts;
    logic [31:0] AxiAddrWidth;
    logic [31:0] AxiDataWidth;
    logic [31:0] NoAddrRules;
  } xbar_cfg_t;

  typedef struct packed {
    logic [31:0] idx;
    logic [31:0] start_addr;
    logic [31:0] end_addr;
  } xbar_rule_32_t;

  typedef struct packed {
    logic [31:0] idx;
    logic [63:0] start_addr;
    logic [63:0] end_addr;
  } xbar_rule_64_t;

endpackage
