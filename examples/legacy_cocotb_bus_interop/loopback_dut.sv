// Minimal SystemVerilog loopback DUT with a SystemVerilog `interface`
// used by the legacy cocotb-bus / cocotbext-* interoperability example.

interface byte_stream_if;
    logic       valid;
    logic       ready;
    logic [7:0] data;
endinterface

module loopback_dut (
    input logic clk,
    input logic rst_n
);
    byte_stream_if in_if();
    byte_stream_if out_if();

    assign in_if.ready = !out_if.valid || out_if.ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            out_if.valid <= 1'b0;
            out_if.data  <= 8'h00;
        end else if (in_if.ready) begin
            out_if.valid <= in_if.valid;
            if (in_if.valid) begin
                out_if.data <= in_if.data;
            end
        end
    end
endmodule
