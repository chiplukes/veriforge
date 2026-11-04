// Minimal repro: signals driven slice-by-slice from a generate loop do not notify
// their continuous-assign / port-connection readers in the "reference" engine.
//
// Each generate iteration registers `d` (+ lane index) and drives ONE slice of `vec`
// with a continuous assign. `vec` is then read three different ways. The reads in
// always_ff blocks are fine; the three "fanout" reads below stay at their power-up X.

module sub (
    input  logic [7:0] in,
    output logic [7:0] out
);
    assign out = in;
endmodule

module partial_assign_fanout (
    input  logic       clk,
    input  logic [3:0] d,
    output logic [3:0] y_last_slice,   // (A) assign of a part-select of vec
    output logic [7:0] y_whole,        // (B) assign of the whole vec
    output logic [7:0] y_sub,          // (C) vec connected to a submodule input port
    output logic [3:0] y_sampled       // (D) always_ff sampling vec -- control, works
);
    localparam N = 2;

    logic [N*4-1:0] vec;

    genvar g;
    generate
        for (g = 0; g < N; g++) begin : g_lane
            logic [3:0] r;
            always_ff @(posedge clk)
                r <= d + g;              // lane 0 holds d, lane 1 holds d+1
            assign vec[g*4 +: 4] = r;    // slice driver
        end
    endgenerate

    assign y_last_slice = vec[(N-1)*4 +: 4];   // (A) expected d+1 one clock after d is applied
    assign y_whole      = vec;                 // (B) expected {d+1, d}

    sub u_sub (.in(vec), .out(y_sub));         // (C) expected {d+1, d}

    always_ff @(posedge clk)
        y_sampled <= vec[(N-1)*4 +: 4];        // (D) expected d+1 two clocks after d
endmodule
