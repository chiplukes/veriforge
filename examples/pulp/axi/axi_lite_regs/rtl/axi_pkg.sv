package axi_pkg;
  typedef logic [1:0] resp_t;
  typedef logic [2:0] prot_t;

  localparam resp_t RespOkay = 2'b00;
  localparam resp_t RespExokay = 2'b01;
  localparam resp_t RespSlverr = 2'b10;
  localparam resp_t RespDecerr = 2'b11;
endpackage
