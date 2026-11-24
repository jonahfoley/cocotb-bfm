// Two-lane tagged packet responder with different per-lane latencies so
// odd-tid packets overtake even-tid packets (out-of-order across IDs).
typedef struct packed {
    logic [3:0] tid;
    logic [1:0] op;
    logic [7:0] payload;
} pkt_t;

module tagged_dut (
    input  logic clk,
    input  logic rst_n,

    input  logic req_valid,
    input  pkt_t req_pkt,

    output logic resp_valid,
    output pkt_t resp_pkt
);
    // Slow lane (even tid): 3-cycle latency.
    logic slow_v0, slow_v1, slow_v2;
    pkt_t slow_p0, slow_p1, slow_p2;

    // Fast lane (odd tid): 1-cycle latency.
    logic fast_v0;
    pkt_t fast_p0;

    function automatic logic [7:0] apply_op(input logic [1:0] op, input logic [7:0] d);
        case (op)
            2'd0:    return d + 8'd1;
            2'd1:    return ~d;
            default: return d;
        endcase
    endfunction

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            slow_v0 <= 1'b0;
            slow_v1 <= 1'b0;
            slow_v2 <= 1'b0;
            fast_v0 <= 1'b0;
            slow_p0 <= '0;
            slow_p1 <= '0;
            slow_p2 <= '0;
            fast_p0 <= '0;
        end else begin
            // Stage 0 capture for even (slow) vs odd (fast) tids.
            slow_v0 <= req_valid && !req_pkt.tid[0];
            slow_p0 <= '{
                tid:     req_pkt.tid,
                op:      req_pkt.op,
                payload: apply_op(req_pkt.op, req_pkt.payload)
            };

            // Stages 1 & 2 for slow lane.
            slow_v1 <= slow_v0;
            slow_p1 <= slow_p0;
            slow_v2 <= slow_v1;
            slow_p2 <= slow_p1;

            // Fast lane (1 cycle).
            fast_v0 <= req_valid && req_pkt.tid[0];
            fast_p0 <= '{
                tid:     req_pkt.tid,
                op:      req_pkt.op,
                payload: apply_op(req_pkt.op, req_pkt.payload)
            };
        end
    end

    assign resp_valid = fast_v0 || slow_v2;
    assign resp_pkt   = fast_v0 ? fast_p0 : slow_p2;
endmodule
