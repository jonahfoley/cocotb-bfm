"""End-to-end Verilator 5 + cocotb 2.0 tests for the Avalon-MM CSR example.

Exercises:
- Packed structs (`csr_meta_t`, `csr_ctrl_t`, `csr_status_t`) inside SV `interface`s
- Nested composite `Bus` (`CsrSubsystemBus` binding both `csr_if` and `diag_if`)
- `RequestDriver` (`AvalonCsrMaster`) with typed responses, `waitrequest`, `byteenable`,
  W1C/RO CSRs
- Asynchronous active-low reset (`Reset(..., active_low=True, sync=False)`) asserted
  mid-clock-cycle under both `ResetPolicy.FLUSH` (default) and `ResetPolicy.PAUSE`
- `AvalonCsrBus.from_prefix(dut, "flat_csr")` proving identical BFM reuse on flat
  signals
- `Scoreboard` with golden CSR model and `KeyedMatcher` tag matching
"""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.handle import HierarchyObject, LogicObject
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from examples.avalon_mm_csr.avalon_csr import (
    AvalonCsrBus,
    AvalonCsrMaster,
    AvalonCsrMonitor,
    AvalonObservedTx,
    AvalonReq,
    CsrCtrl,
    CsrMeta,
    CsrStatus,
    CsrSubsystemBus,
)

from cocotb_bfm import (
    Bus,
    KeyedMatcher,
    Reset,
    ResetError,
    ResetPolicy,
    Scoreboard,
    VectorObject,
)

ADDR_CTRL = 0x0
ADDR_SCRATCH = 0x1
ADDR_STATUS = 0x2
ADDR_IRQ_STS = 0x3
ADDR_DATA0 = 0x4
ADDR_DATA1 = 0x5


class AvalonTbCtrlBus(Bus):
    """Typed handle container for the top-level testbench control pins."""

    clk: LogicObject
    arst_n: LogicObject
    force_stall: LogicObject
    wait_cycles: VectorObject
    irq_set: VectorObject


async def _init_avalon_dut(
    dut: HierarchyObject,
) -> tuple[AvalonTbCtrlBus, CsrSubsystemBus, AvalonCsrBus]:
    tb = AvalonTbCtrlBus.from_entity(dut)
    subsys = CsrSubsystemBus.from_interface(dut)
    flat_bus = AvalonCsrBus.from_prefix(dut, "flat_csr")

    Clock(tb.clk, 10, unit="ns").start()
    tb.arst_n.value = 0
    tb.force_stall.value = 0
    tb.wait_cycles.value = 0
    tb.irq_set.value = 0

    subsys.csr.drive_idle()
    flat_bus.drive_idle()

    await Timer(25, unit="ns")
    tb.arst_n.value = 1
    await ClockCycles(tb.clk, 2)
    return tb, subsys, flat_bus


@cocotb.test()
async def test_avalon_csr_sv_interface_packed_structs_and_scoreboard(
    dut: HierarchyObject,
) -> None:
    """End-to-end Avalon-MM CSR test via SV interfaces, packed structs, and SB."""
    tb, subsys, _ = await _init_avalon_dut(dut)
    rst = Reset(tb.arst_n, active_low=True, sync=False)

    async with (
        AvalonCsrMaster(subsys.csr, tb.clk, reset=rst, name="avm_master") as master,
        AvalonCsrMonitor(subsys.csr, tb.clk, reset=rst, name="avm_mon") as mon,
        Scoreboard(name="avalon_csr_sb") as sb,
    ):
        ch = sb.add_channel("csr_tx", mon)

        # 1. Verify reset default CTRL register & packed CsrCtrl decode with wait=2.
        tb.wait_cycles.value = 2
        m1 = CsrMeta(tag=0x3, priv=1, secure=1, burst_hint=0)
        ch.expect(AvalonObservedTx(ADDR_CTRL, False, 0x0000_0001, 0xF, m1))
        resp = await master.read_reg(ADDR_CTRL, meta=m1)
        assert resp.readdata == 0x0000_0001
        assert resp.resp_meta == m1
        ctrl_rst = CsrCtrl.unpack(resp.readdata)
        assert ctrl_rst == CsrCtrl(threshold=0, mode=0, reserved=0, irq_en=0, enable=1)

        # 2. Write packed CsrCtrl struct with waitstates = 3 and read back.
        tb.wait_cycles.value = 3
        new_ctrl = CsrCtrl(threshold=0xBEEF, mode=0x42, reserved=0, irq_en=1, enable=1)
        m2 = CsrMeta(tag=0xA, priv=3, secure=1, burst_hint=1)
        ch.expect(AvalonObservedTx(ADDR_CTRL, True, new_ctrl.to_int(), 0xF, m2))
        await master.write_reg(ADDR_CTRL, new_ctrl.to_int(), meta=m2)

        ch.expect(AvalonObservedTx(ADDR_CTRL, False, new_ctrl.to_int(), 0xF, m2))
        resp_ctrl = await master.read_reg(ADDR_CTRL, meta=m2)
        assert CsrCtrl.unpack(resp_ctrl.readdata) == new_ctrl
        assert resp_ctrl.resp_meta == m2

        # 3. Byte-enable masked writes on SCRATCH across individual lanes.
        tb.wait_cycles.value = 1
        m3 = CsrMeta(tag=0x5, priv=2, secure=0, burst_hint=0)
        ch.expect(AvalonObservedTx(ADDR_SCRATCH, True, 0x1122_3344, 0xF, m3))
        await master.write_reg(ADDR_SCRATCH, 0x1122_3344, byteenable=0xF, meta=m3)

        # Overwrite only byte 1 (bits 15:8) and byte 3 (bits 31:24)
        ch.expect(AvalonObservedTx(ADDR_SCRATCH, True, 0xAA00_BB00, 0xA, m3))
        await master.write_reg(ADDR_SCRATCH, 0xAA00_BB00, byteenable=0xA, meta=m3)

        ch.expect(AvalonObservedTx(ADDR_SCRATCH, False, 0xAA22_BB44, 0xF, m3))
        resp_scratch = await master.read_reg(ADDR_SCRATCH, meta=m3)
        assert resp_scratch.readdata == 0xAA22_BB44

        # 4. Read-only STATUS register ignores writes and matches diag_if packed struct.
        m4 = CsrMeta(tag=0xC, priv=0, secure=1, burst_hint=0)
        ch.expect(AvalonObservedTx(ADDR_STATUS, True, 0xFFFF_FFFF, 0xF, m4))
        await master.write_reg(ADDR_STATUS, 0xFFFF_FFFF, meta=m4)

        # 4 writes (CTRL, SCRATCHx2, STATUS) and 3 reads completed before this read.
        await RisingEdge(tb.clk)
        live_diag = CsrStatus.from_handle(subsys.diag.status)
        assert live_diag == CsrStatus(
            write_count=4,
            read_count=3,
            last_tag=0xC,
            last_priv=0,
            reserved=0,
            enabled=1,
        )

        ch.expect(AvalonObservedTx(ADDR_STATUS, False, live_diag.to_int(), 0xF, m4))
        resp_status = await master.read_reg(ADDR_STATUS, meta=m4)
        assert CsrStatus.unpack(resp_status.readdata) == live_diag

        # 5. W1C IRQ_STS register + diag_if.irq_pending sideband.
        tb.irq_set.value = 0x55
        await RisingEdge(tb.clk)
        tb.irq_set.value = 0x00
        await RisingEdge(tb.clk)
        assert int(subsys.diag.irq_pending.value) == 1

        # Clear bits 0x05 via W1C -> 0x50 remains.
        m5 = CsrMeta(tag=0x7, priv=1, secure=0, burst_hint=0)
        ch.expect(AvalonObservedTx(ADDR_IRQ_STS, True, 0x0000_0005, 0x1, m5))
        await master.write_reg(ADDR_IRQ_STS, 0x0000_0005, byteenable=0x1, meta=m5)

        ch.expect(AvalonObservedTx(ADDR_IRQ_STS, False, 0x0000_0050, 0xF, m5))
        resp_irq = await master.read_reg(ADDR_IRQ_STS, meta=m5)
        assert resp_irq.readdata == 0x0000_0050
        assert int(subsys.diag.irq_pending.value) == 1

        # Clear remaining bits -> irq_pending drops to 0.
        ch.expect(AvalonObservedTx(ADDR_IRQ_STS, True, 0x0000_0050, 0x1, m5))
        await master.write_reg(ADDR_IRQ_STS, 0x0000_0050, byteenable=0x1, meta=m5)
        await RisingEdge(tb.clk)
        assert int(subsys.diag.irq_pending.value) == 0


@cocotb.test()
async def test_avalon_csr_async_reset_off_clock_edge_flush_and_pause(
    dut: HierarchyObject,
) -> None:
    """Verify async reset asserted mid-cycle under ResetPolicy.FLUSH and PAUSE."""
    tb, subsys, _ = await _init_avalon_dut(dut)
    arst = Reset(tb.arst_n, active_low=True, sync=False)

    # --- Part A: Default ResetPolicy.FLUSH with async mid-cycle reset ---
    async with (
        AvalonCsrMaster(subsys.csr, tb.clk, reset=arst, name="avm_flush") as master,
        AvalonCsrMonitor(subsys.csr, tb.clk, reset=arst, name="mon_flush") as mon,
    ):
        # Program non-zero values before reset (default=0 bitfields omit cleanly!).
        await master.write_reg(ADDR_DATA0, 0xCAFE_BABE, meta=CsrMeta(tag=0x9, priv=2))
        r0 = await master.read_reg(ADDR_DATA0)
        assert r0.readdata == 0xCAFE_BABE

        # Stall waitrequest and enqueue an in-flight write + queued read.
        tb.force_stall.value = 1
        h_stalled = master.enqueue_request(
            _make_write(ADDR_DATA1, 0x1234_5678, tag=0xE)
        )
        h_queued = master.enqueue_request(_make_read(ADDR_DATA0, tag=0xF))

        await ClockCycles(tb.clk, 2)
        assert not h_stalled.done
        assert int(subsys.csr.write.value) == 1

        # Assert asynchronous reset 3 ns AFTER posedge clk (off the clock edge!).
        await Timer(3, unit="ns")
        tb.arst_n.value = 0
        await Timer(2, unit="ns")

        # Hardware registers reset immediately before the next clock edge!
        assert CsrStatus.from_handle(subsys.diag.status).write_count == 0
        # Driver flushed stalled + queued requests with ResetError and idled bus!
        for handle in (h_stalled, h_queued):
            try:
                await handle.wait()
                raise AssertionError("Expected ResetError on async reset FLUSH")
            except ResetError:
                pass
        assert int(subsys.csr.write.value) == 0
        assert int(subsys.csr.read.value) == 0

        # Release stall and async reset off-edge; verify clean recovery!
        tb.force_stall.value = 0
        await Timer(2, unit="ns")
        tb.arst_n.value = 1
        await RisingEdge(tb.clk)

        # Post-reset read confirms DATA0 was asynchronously cleared to 0.
        r_after = await master.read_reg(ADDR_DATA0, meta=CsrMeta(tag=0x1))
        assert r_after.readdata == 0
        assert r_after.resp_meta == CsrMeta(tag=0x1)
        assert mon.count >= 1

    # --- Part B: Explicit ResetPolicy.PAUSE retries stalled read after async reset ---
    async with AvalonCsrMaster(
        subsys.csr,
        tb.clk,
        reset=arst,
        reset_policy=ResetPolicy.PAUSE,
        name="avm_pause",
    ) as master_pause:
        tb.force_stall.value = 1
        retry_meta = CsrMeta(tag=0xD, priv=3, secure=1, burst_hint=0)
        h_retry = master_pause.enqueue_request(
            _make_read(ADDR_CTRL, tag=0xD, meta=retry_meta)
        )

        await ClockCycles(tb.clk, 2)
        assert not h_retry.done
        assert int(subsys.csr.read.value) == 1

        # Pulse async reset off the clock edge.
        await Timer(3, unit="ns")
        tb.arst_n.value = 0
        await Timer(2, unit="ns")
        assert int(subsys.csr.read.value) == 0  # idled during reset

        # Unstall and release async reset -> paused read retries and succeeds!
        tb.force_stall.value = 0
        await Timer(2, unit="ns")
        tb.arst_n.value = 1

        resp_retry = await h_retry.wait()
        assert resp_retry.readdata == 0x0000_0001
        assert resp_retry.resp_meta == retry_meta


@cocotb.test()
async def test_avalon_csr_flat_prefix_and_keyed_scoreboard(
    dut: HierarchyObject,
) -> None:
    """Bind AvalonCsrBus via from_prefix AND verify KeyedMatcher by packed tag."""
    tb, subsys, flat_bus = await _init_avalon_dut(dut)
    assert flat_bus.meta is None
    assert flat_bus.resp_meta is None

    arst = Reset(tb.arst_n, active_low=True, sync=False)
    async with (
        AvalonCsrMaster(flat_bus, tb.clk, reset=arst, name="flat_master") as flat_drv,
        AvalonCsrMonitor(flat_bus, tb.clk, reset=arst, name="flat_mon") as flat_mon,
        Scoreboard(name="flat_csr_sb") as sb,
    ):
        ch = sb.add_channel("flat", flat_mon)
        ch.expect(AvalonObservedTx(ADDR_DATA0, True, 0x1357_2468, 0xF, None))
        ch.expect(AvalonObservedTx(ADDR_DATA0, False, 0x1357_2468, 0xF, None))

        await flat_drv.write_reg(ADDR_DATA0, 0x1357_2468)
        resp = await flat_drv.read_reg(ADDR_DATA0)
        assert resp.readdata == 0x1357_2468
        assert resp.resp_meta is None
        await ClockCycles(tb.clk, 2)

    # 2. KeyedMatcher on SV interface CSR bank keyed by packed-struct `meta.tag`.
    async with (
        AvalonCsrMaster(
            subsys.csr, tb.clk, reset=arst, name="keyed_master"
        ) as keyed_master,
        AvalonCsrMonitor(subsys.csr, tb.clk, reset=arst, name="keyed_mon") as keyed_mon,
        Scoreboard(name="keyed_sb") as keyed_sb,
    ):
        kch = keyed_sb.add_channel(
            "tagged",
            keyed_mon,
            matcher=KeyedMatcher(
                key=lambda tx: tx.meta.tag if tx.meta is not None else -1
            ),
        )
        m_a = CsrMeta(tag=2, priv=1)
        m_b = CsrMeta(tag=6, priv=2)
        # Expect tag=6 before tag=2 to verify per-tag out-of-order matching!
        kch.expect(AvalonObservedTx(ADDR_DATA1, True, 0x2222_2222, 0xF, m_b))
        kch.expect(AvalonObservedTx(ADDR_DATA0, True, 0x1111_1111, 0xF, m_a))

        await keyed_master.write_reg(ADDR_DATA0, 0x1111_1111, meta=m_a)
        await keyed_master.write_reg(ADDR_DATA1, 0x2222_2222, meta=m_b)
        await ClockCycles(tb.clk, 2)


def _make_write(address: int, data: int, *, tag: int = 0) -> AvalonReq:
    return AvalonReq(
        address=address, is_write=True, writedata=data, meta=CsrMeta(tag=tag)
    )


def _make_read(address: int, *, tag: int = 0, meta: CsrMeta | None = None) -> AvalonReq:
    return AvalonReq(
        address=address,
        is_write=False,
        meta=meta if meta is not None else CsrMeta(tag=tag),
    )
