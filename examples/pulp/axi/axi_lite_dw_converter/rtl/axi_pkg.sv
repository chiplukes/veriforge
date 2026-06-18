package axi_pkg;

  typedef logic [1:0] resp_t;
  typedef logic [2:0] prot_t;

  localparam resp_t RESP_OKAY = 2'b00;
  localparam resp_t RESP_EXOKAY = 2'b01;
  localparam resp_t RESP_SLVERR = 2'b10;
  localparam resp_t RESP_DECERR = 2'b11;

endpackage
