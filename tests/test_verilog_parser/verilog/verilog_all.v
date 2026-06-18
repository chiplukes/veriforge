/* -----------------------------------------------------------------------------
 * Parsing test 1
 * -----------------------------------------------------------------------------
*/

//-------------------------------------
module v_module1

#(
    parameter   PARAM1 = 2,
    parameter   PARAM2 = 4
)
(
    input                       clk,
    input                       rst,

    input        [PARAM1-1:0]   insig_vect0,
    input        [1:0]          insig_vect1,
    input                       insig_single,
    output  reg                 outsig_single = 0,
    output  reg  [PARAM1-1:0]   outsig_vect0 = 0,
    output  reg  [1:0]          outsig_vect1 = 0,
    output  wire [PARAM2-1:0]   outsig_vect2
);


always @(posedge clk)
    if (rst)
        begin
        outsig_single <= 0;
        outsig_vect0 <= 0;
        outsig_vect1 <= 0;
        end
    else
        begin
        outsig_single <= insig_single;
        outsig_vect0 <= insig_vect0;
        outsig_vect1 <= insig_vect1;
        end

assign outsig_vect2 = {PARAM2{1'b1}};

endmodule
