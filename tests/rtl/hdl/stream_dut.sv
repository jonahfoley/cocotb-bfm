// SystemVerilog testbench DUT for cocotb-bfm Verilator integration tests.
// Exercises:
//   1. Real SystemVerilog `interface` instances (`if_in`, `if_out`)
//   2. Real SystemVerilog `interface` array (`lanes_if[2]`)
//   3. Flat prefixed signals (`raw_in_*`, `raw_out_*`)
//   4. 1-bit packed vector (`logic [0:0] onebit_flag`) for cross-simulator type tolerance

interface stream_if #(
    parameter int DATA_W = 8
);
    logic              valid;
    logic              ready;
    logic [DATA_W-1:0] data;
    logic              last;
endinterface

module stream_xor_pipe #(
    parameter int DATA_W = 8,
    parameter logic [DATA_W-1:0] XOR_MASK = 8'h5A
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              in_valid,
    output logic              in_ready,
    input  logic [DATA_W-1:0] in_data,
    input  logic              in_last,
    output logic              out_valid,
    input  logic              out_ready,
    output logic [DATA_W-1:0] out_data,
    output logic              out_last
);
    // 1-deep skid/pipeline register with full valid/ready backpressure.
    assign in_ready = !out_valid || out_ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            out_data  <= '0;
            out_last  <= 1'b0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_data <= in_data ^ XOR_MASK;
                out_last <= in_last;
            end
        end
    end
endmodule

module stream_tb_top (
    input  logic       clk,
    input  logic       rst_n,
    // Flat prefixed bus (raw_in -> raw_out)
    input  logic       raw_in_valid,
    output logic       raw_in_ready,
    input  logic [7:0] raw_in_data,
    input  logic       raw_in_last,
    output logic       raw_out_valid,
    input  logic       raw_out_ready,
    output logic [7:0] raw_out_data,
    output logic       raw_out_last,
    // 1-bit vector port for LogicObject tolerance verification
    input  logic [0:0] onebit_flag,
    output logic [0:0] onebit_echo
);
    // SystemVerilog interface instances exposed to cocotb via VPI
    stream_if #(.DATA_W(8)) if_in();
    stream_if #(.DATA_W(8)) if_out();

    // SystemVerilog interface array (2 lanes)
    stream_if #(.DATA_W(8)) lanes_if[2]();

    assign onebit_echo = onebit_flag;

    // Connect SV interface pipeline
    stream_xor_pipe #(.DATA_W(8), .XOR_MASK(8'h5A)) u_if_pipe (
        .clk       (clk),
        .rst_n     (rst_n),
        .in_valid  (if_in.valid),
        .in_ready  (if_in.ready),
        .in_data   (if_in.data),
        .in_last   (if_in.last),
        .out_valid (if_out.valid),
        .out_ready (if_out.ready),
        .out_data  (if_out.data),
        .out_last  (if_out.last)
    );

    // Connect flat prefixed pipeline
    stream_xor_pipe #(.DATA_W(8), .XOR_MASK(8'hA5)) u_raw_pipe (
        .clk       (clk),
        .rst_n     (rst_n),
        .in_valid  (raw_in_valid),
        .in_ready  (raw_in_ready),
        .in_data   (raw_in_data),
        .in_last   (raw_in_last),
        .out_valid (raw_out_valid),
        .out_ready (raw_out_ready),
        .out_data  (raw_out_data),
        .out_last  (raw_out_last)
    );

    // Connect interface array: lane 0 -> lane 1
    stream_xor_pipe #(.DATA_W(8), .XOR_MASK(8'hFF)) u_lane_pipe (
        .clk       (clk),
        .rst_n     (rst_n),
        .in_valid  (lanes_if[0].valid),
        .in_ready  (lanes_if[0].ready),
        .in_data   (lanes_if[0].data),
        .in_last   (lanes_if[0].last),
        .out_valid (lanes_if[1].valid),
        .out_ready (lanes_if[1].ready),
        .out_data  (lanes_if[1].data),
        .out_last  (lanes_if[1].last)
    );
endmodule
