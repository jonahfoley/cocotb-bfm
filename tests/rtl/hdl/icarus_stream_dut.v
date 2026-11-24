// Pure Verilog-2005 DUT for Icarus Verilog integration tests (including 4-state X/Z).
`timescale 1ns/1ps

module icarus_stream_dut (
    input  wire       clk,
    input  wire       rst_n,

    input  wire       in_valid,
    output wire       in_ready,
    input  wire [7:0] in_data,

    output reg        out_valid,
    input  wire       out_ready,
    output reg  [7:0] out_data,

    // 4-state X/Z test ports (only possible on 4-state simulators like Icarus)
    input  wire       xz_rst_n,
    output wire [7:0] xz_vec,

    // 1-bit vector ports (`[0:0]`)
    input  wire [0:0] onebit_flag,
    output reg  [0:0] onebit_echo
);
    assign in_ready = !out_valid || out_ready;
    assign xz_vec   = 8'b1010_xx1z;

    always @(posedge clk) begin
        if (!rst_n) begin
            out_valid   <= 1'b0;
            out_data    <= 8'd0;
            onebit_echo <= 1'b0;
        end else begin
            onebit_echo <= onebit_flag;
            if (in_ready) begin
                out_valid <= in_valid;
                out_data  <= in_data ^ 8'h5A;
            end
        end
    end
endmodule
