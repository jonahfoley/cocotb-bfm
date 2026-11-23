// Realistic Avalon-MM CSR Bank + SystemVerilog Interface + Packed Structs + Async Reset
//
// Register Map (word-addressed, 32-bit data, 4-bit byteenable):
//   0x00 (CTRL)    : RW   - Packed control register (`csr_ctrl_t`), reset = 32'h0000_0001
//   0x01 (SCRATCH) : RW   - 32-bit scratchpad supporting arbitrary byte-enable writes, reset = 0
//   0x02 (STATUS)  : RO   - Packed live hardware status (`csr_status_t`: write_cnt, read_cnt, last_tag, active)
//   0x03 (IRQ_STS) : W1C  - Interrupt status bits (latched from `irq_set`, cleared by writing 1s)
//   0x04 (DATA0)   : RW   - General-purpose 32-bit register
//   0x05 (DATA1)   : RW   - General-purpose 32-bit register
//
// Features exercised:
//   - SystemVerilog `interface avalon_csr_if` with packed struct sideband (`csr_meta_t`)
//   - Nested SystemVerilog status sideband interface (`csr_diag_if`) with packed struct (`csr_status_t`)
//   - Configurable waitrequest backpressure (`force_stall` + deterministic waitstate injection)
//   - Pipelined `readdatavalid` read responses
//   - Asynchronous active-low reset (`arst_n`) that resets registers immediately off the clock edge
//   - Second identical CSR bank exposed via flat prefixed signals (`flat_csr_*`)

typedef struct packed {
    logic [3:0] tag;
    logic [1:0] priv;
    logic       secure;
    logic       burst_hint;
} csr_meta_t;

typedef struct packed {
    logic [15:0] threshold;
    logic [7:0]  mode;
    logic [5:0]  reserved;
    logic        irq_en;
    logic        enable;
} csr_ctrl_t;

typedef struct packed {
    logic [7:0] write_count;
    logic [7:0] read_count;
    logic [3:0] last_tag;
    logic [1:0] last_priv;
    logic [8:0] reserved;
    logic       enabled;
} csr_status_t;

interface avalon_csr_if #(
    parameter int ADDR_W = 4,
    parameter int DATA_W = 32
);
    logic [ADDR_W-1:0]     address;
    logic                  read;
    logic                  write;
    logic [DATA_W-1:0]     writedata;
    logic [(DATA_W/8)-1:0] byteenable;
    logic [DATA_W-1:0]     readdata;
    logic                  readdatavalid;
    logic                  waitrequest;
    csr_meta_t             meta;
    csr_meta_t             resp_meta;
endinterface

interface csr_diag_if;
    csr_status_t status;
    logic        irq_pending;
endinterface

module avalon_csr_bank #(
    parameter int ADDR_W = 4,
    parameter int DATA_W = 32
) (
    input  logic                  clk,
    input  logic                  arst_n,
    input  logic                  force_stall,
    input  logic [2:0]            wait_cycles,
    input  logic [7:0]            irq_set,
    // Avalon-MM CSR port
    input  logic [ADDR_W-1:0]     avm_address,
    input  logic                  avm_read,
    input  logic                  avm_write,
    input  logic [DATA_W-1:0]     avm_writedata,
    input  logic [(DATA_W/8)-1:0] avm_byteenable,
    output logic [DATA_W-1:0]     avm_readdata,
    output logic                  avm_readdatavalid,
    output logic                  avm_waitrequest,
    input  csr_meta_t             avm_meta,
    output csr_meta_t             avm_resp_meta,
    // Diagnostic packed status outputs
    output csr_status_t           diag_status,
    output logic                  diag_irq_pending
);
    localparam logic [31:0] CTRL_RESET = 32'h0000_0001;

    logic [31:0] reg_ctrl;
    logic [31:0] reg_scratch;
    logic [31:0] reg_irq_sts;
    logic [31:0] reg_data0;
    logic [31:0] reg_data1;

    logic [7:0]  write_count;
    logic [7:0]  read_count;
    logic [3:0]  last_tag;
    logic [1:0]  last_priv;
    logic [2:0]  stall_cnt;

    wire active_req = avm_read || avm_write;
    assign avm_waitrequest = force_stall || (active_req && (stall_cnt < wait_cycles));

    always_comb begin
        diag_status = '0;
        diag_status.write_count = write_count;
        diag_status.read_count  = read_count;
        diag_status.last_tag    = last_tag;
        diag_status.last_priv   = last_priv;
        diag_status.enabled     = reg_ctrl[0];
        diag_irq_pending        = (|reg_irq_sts[7:0]) && reg_ctrl[1];
    end

    function automatic logic [31:0] apply_be(
        input logic [31:0] old_val,
        input logic [31:0] new_val,
        input logic [3:0]  be
    );
        logic [31:0] res;
        res = old_val;
        for (int b = 0; b < 4; b++) begin
            if (be[b]) begin
                res[b*8 +: 8] = new_val[b*8 +: 8];
            end
        end
        return res;
    endfunction

    always_ff @(posedge clk or negedge arst_n) begin
        if (!arst_n) begin
            reg_ctrl          <= CTRL_RESET;
            reg_scratch       <= 32'h0;
            reg_irq_sts       <= 32'h0;
            reg_data0         <= 32'h0;
            reg_data1         <= 32'h0;
            write_count       <= 8'h0;
            read_count        <= 8'h0;
            last_tag          <= 4'h0;
            last_priv         <= 2'h0;
            stall_cnt         <= 3'h0;
            avm_readdata      <= 32'h0;
            avm_readdatavalid <= 1'b0;
            avm_resp_meta     <= '0;
        end else begin
            // Default: clear single-cycle read-valid pulse and latch external IRQs
            avm_readdatavalid <= 1'b0;
            reg_irq_sts[7:0]  <= reg_irq_sts[7:0] | irq_set;

            // Track deterministic waitstate counter per request
            if (active_req && avm_waitrequest && !force_stall) begin
                stall_cnt <= stall_cnt + 3'd1;
            end else if (!avm_waitrequest) begin
                stall_cnt <= 3'd0;
            end

            // Execute accepted Avalon-MM request
            if (avm_write && !avm_waitrequest) begin
                write_count <= write_count + 8'd1;
                last_tag    <= avm_meta.tag;
                last_priv   <= avm_meta.priv;
                case (avm_address)
                    4'h0: reg_ctrl    <= apply_be(reg_ctrl, avm_writedata, avm_byteenable);
                    4'h1: reg_scratch <= apply_be(reg_scratch, avm_writedata, avm_byteenable);
                    4'h2: ; // STATUS is read-only
                    4'h3: begin
                        // Write-1-to-clear on low byte (respecting byteenable[0])
                        if (avm_byteenable[0]) begin
                            reg_irq_sts[7:0] <= (reg_irq_sts[7:0] | irq_set) & ~avm_writedata[7:0];
                        end
                    end
                    4'h4: reg_data0 <= apply_be(reg_data0, avm_writedata, avm_byteenable);
                    4'h5: reg_data1 <= apply_be(reg_data1, avm_writedata, avm_byteenable);
                    default: ;
                endcase
            end else if (avm_read && !avm_waitrequest) begin
                read_count        <= read_count + 8'd1;
                last_tag          <= avm_meta.tag;
                last_priv         <= avm_meta.priv;
                avm_readdatavalid <= 1'b1;
                avm_resp_meta     <= avm_meta;
                case (avm_address)
                    4'h0: avm_readdata <= reg_ctrl;
                    4'h1: avm_readdata <= reg_scratch;
                    4'h2: avm_readdata <= diag_status;
                    4'h3: avm_readdata <= reg_irq_sts;
                    4'h4: avm_readdata <= reg_data0;
                    4'h5: avm_readdata <= reg_data1;
                    default: avm_readdata <= 32'hDEAD_BEEF;
                endcase
            end
        end
    end
endmodule

module avalon_csr_tb_top (
    input  logic        clk,
    input  logic        arst_n,
    input  logic        force_stall,
    input  logic [2:0]  wait_cycles,
    input  logic [7:0]  irq_set,
    // Flat prefixed Avalon-MM CSR bank (demonstrates same Bus via from_prefix)
    input  logic [3:0]  flat_csr_address,
    input  logic        flat_csr_read,
    input  logic        flat_csr_write,
    input  logic [31:0] flat_csr_writedata,
    input  logic [3:0]  flat_csr_byteenable,
    output logic [31:0] flat_csr_readdata,
    output logic        flat_csr_readdatavalid,
    output logic        flat_csr_waitrequest
);
    // Primary CSR bank exposed via SystemVerilog interfaces with packed structs
    avalon_csr_if #(.ADDR_W(4), .DATA_W(32)) csr_if();
    csr_diag_if diag_if();

    avalon_csr_bank u_if_csr (
        .clk               (clk),
        .arst_n            (arst_n),
        .force_stall       (force_stall),
        .wait_cycles       (wait_cycles),
        .irq_set           (irq_set),
        .avm_address       (csr_if.address),
        .avm_read          (csr_if.read),
        .avm_write         (csr_if.write),
        .avm_writedata     (csr_if.writedata),
        .avm_byteenable    (csr_if.byteenable),
        .avm_readdata      (csr_if.readdata),
        .avm_readdatavalid (csr_if.readdatavalid),
        .avm_waitrequest   (csr_if.waitrequest),
        .avm_meta          (csr_if.meta),
        .avm_resp_meta     (csr_if.resp_meta),
        .diag_status       (diag_if.status),
        .diag_irq_pending  (diag_if.irq_pending)
    );

    // Secondary CSR bank exposed via flat prefixed signals (no optional meta sideband)
    csr_meta_t   unused_resp_meta;
    csr_status_t unused_diag_status;
    logic        unused_irq_pending;

    avalon_csr_bank u_flat_csr (
        .clk               (clk),
        .arst_n            (arst_n),
        .force_stall       (1'b0),
        .wait_cycles       (3'd1),
        .irq_set           (8'h0),
        .avm_address       (flat_csr_address),
        .avm_read          (flat_csr_read),
        .avm_write         (flat_csr_write),
        .avm_writedata     (flat_csr_writedata),
        .avm_byteenable    (flat_csr_byteenable),
        .avm_readdata      (flat_csr_readdata),
        .avm_readdatavalid (flat_csr_readdatavalid),
        .avm_waitrequest   (flat_csr_waitrequest),
        .avm_meta          ('0),
        .avm_resp_meta     (unused_resp_meta),
        .diag_status       (unused_diag_status),
        .diag_irq_pending  (unused_irq_pending)
    );
endmodule
