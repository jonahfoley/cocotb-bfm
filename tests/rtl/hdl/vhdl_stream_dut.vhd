-- Native VHDL-2008 DUT exercising LogicObject, LogicArrayObject, ascending
-- non-zero ranges, and 1-bit std_logic_vector(0 downto 0) on GHDL / NVC.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity vhdl_stream_dut is
    port (
        clk         : in  std_logic;
        rst_n       : in  std_logic;

        in_valid    : in  std_logic;
        in_ready    : out std_logic;
        in_data     : in  std_logic_vector(7 downto 0);

        out_valid   : out std_logic;
        out_ready   : in  std_logic;
        out_data    : out std_logic_vector(7 downto 0);

        -- 1-bit std_logic_vector(0 downto 0) for LogicObject tolerance testing
        onebit_flag : in  std_logic_vector(0 downto 0);
        onebit_echo : out std_logic_vector(0 downto 0);

        -- Non-zero ascending std_logic_vector(1 to 8) for PackedStruct testing
        pkt_in      : in  std_logic_vector(1 to 8);
        pkt_out     : out std_logic_vector(1 to 8)
    );
end entity vhdl_stream_dut;

architecture rtl of vhdl_stream_dut is
    signal out_valid_r : std_logic := '0';
    signal in_ready_i  : std_logic;
begin
    in_ready_i <= (not out_valid_r) or out_ready;
    in_ready   <= in_ready_i;
    out_valid  <= out_valid_r;

    process (clk)
    begin
        if rising_edge(clk) then
            if rst_n = '0' then
                out_valid_r <= '0';
                out_data    <= (others => '0');
                onebit_echo <= (others => '0');
                pkt_out     <= (others => '0');
            else
                onebit_echo <= onebit_flag;
                pkt_out     <= pkt_in;
                if in_ready_i = '1' then
                    out_valid_r <= in_valid;
                    out_data    <= in_data xor x"5A";
                end if;
            end if;
        end if;
    end process;
end architecture rtl;
