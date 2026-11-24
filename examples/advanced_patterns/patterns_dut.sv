// Minimal DUT exposing non-standard port naming, regex-friendly ports, and an
// SV interface array for demonstrating resolvers, Component, Role, QueueAdapter,
// WindowMatcher, and UnorderedMatcher.
interface lane_if;
    logic       valid;
    logic       ready;
    logic [7:0] data;
endinterface

module patterns_dut (
    input  logic       clk,
    input  logic       rst_n,

    // Regex-friendly ports (`rx0_<signal>_[io]`) -> loopback to `tx0_<signal>_[io]`
    input  logic       rx0_valid_i,
    output logic       rx0_ready_o,
    input  logic [7:0] rx0_data_i,

    output logic       tx0_valid_o,
    input  logic       tx0_ready_i,
    output logic [7:0] tx0_data_o,

    // Irregular port names for `from_mapping()` / `CompositeResolver`
    input  logic       chA_vld_sig,
    output logic       chA_rdy_sig,
    input  logic [7:0] chA_dat_sig,

    output logic       chB_vld_sig,
    input  logic       chB_rdy_sig,
    output logic [7:0] chB_dat_sig
);
    // Interface array: lanes[0] -> lanes[1] (1-cycle pipeline)
    lane_if lanes[2]();

    assign rx0_ready_o = !tx0_valid_o || tx0_ready_i;
    assign chA_rdy_sig = !chB_vld_sig || chB_rdy_sig;
    assign lanes[0].ready = !lanes[1].valid || lanes[1].ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            tx0_valid_o    <= 1'b0;
            tx0_data_o     <= 8'd0;
            chB_vld_sig    <= 1'b0;
            chB_dat_sig    <= 8'd0;
            lanes[1].valid <= 1'b0;
            lanes[1].data  <= 8'd0;
        end else begin
            if (rx0_ready_o) begin
                tx0_valid_o <= rx0_valid_i;
                tx0_data_o  <= rx0_data_i;
            end
            if (chA_rdy_sig) begin
                chB_vld_sig <= chA_vld_sig;
                chB_dat_sig <= chA_dat_sig ^ 8'hFF;
            end
            if (lanes[0].ready) begin
                lanes[1].valid <= lanes[0].valid;
                lanes[1].data  <= lanes[0].data + 8'd1;
            end
        end
    end
endmodule
