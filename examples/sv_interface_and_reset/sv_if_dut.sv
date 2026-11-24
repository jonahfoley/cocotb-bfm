// SystemVerilog interface + 1-stage XOR pipeline for demonstrating
// `Bus.from_interface()`, `sub_bus()`, `Scoreboard.connect_model()`, and `Reset`.
interface byte_if;
    logic       valid;
    logic       ready;
    logic [7:0] data;
endinterface

module sv_if_dut (
    input logic clk,
    input logic rst_n
);
    byte_if in_if();
    byte_if out_if();

    assign in_if.ready = !out_if.valid || out_if.ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            out_if.valid <= 1'b0;
            out_if.data  <= 8'd0;
        end else if (in_if.ready) begin
            out_if.valid <= in_if.valid;
            out_if.data  <= in_if.data ^ 8'h5A;
        end
    end
endmodule
