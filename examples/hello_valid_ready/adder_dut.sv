// Simple 1-stage valid/ready pipeline that adds 1 to each 8-bit input byte.
module adder_dut (
    input  logic       clk,
    input  logic       rst_n,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
    assign in_ready = !out_valid || out_ready;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            out_data  <= 8'd0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            out_data  <= in_data + 8'd1;
        end
    end
endmodule
