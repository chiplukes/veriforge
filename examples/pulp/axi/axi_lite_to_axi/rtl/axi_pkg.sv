package axi_pkg;

  typedef logic [1:0] resp_t;
  typedef logic [2:0] prot_t;
  typedef logic [3:0] cache_t;
  typedef logic [2:0] size_t;
  typedef logic [1:0] burst_t;

  localparam resp_t RESP_OKAY = 2'b00;
  localparam resp_t RESP_EXOKAY = 2'b01;
  localparam resp_t RESP_SLVERR = 2'b10;
  localparam resp_t RESP_DECERR = 2'b11;

  localparam burst_t BURST_FIXED = 2'b00;

endpackage
