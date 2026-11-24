"""Reference Example: Statically Typed Avalon-MM CSR BFM built with ``cocotb-bfm``.

Demonstrates how users build a production-grade protocol BFM on top of ``cocotb-bfm``:

- Typed :class:`~cocotb_bfm.Bus` with raw cocotb 2.0 handles and optional sidebands
- Typed :class:`~cocotb_bfm.PackedStruct` codecs matching SystemVerilog ``struct
  packed``
- Nested composite :class:`~cocotb_bfm.Bus` combining an Avalon-MM SV interface + diag
  interface
- :class:`~cocotb_bfm.RequestDriver` returning statically typed :class:`AvalonResp`
  objects
- Passive :class:`~cocotb_bfm.Monitor` capturing transactions for
  :class:`~cocotb_bfm.Scoreboard`
- Full support for ``waitrequest`` backpressure, ``byteenable``, ``readdatavalid``, and
  async reset
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from cocotb.handle import LogicObject
from cocotb.triggers import RisingEdge

from cocotb_bfm import (
    Bus,
    Direction,
    Monitor,
    PackedStruct,
    RequestDriver,
    VectorObject,
    bitfield,
    signal,
    sub_bus,
)

# ---------------------------------------------------------------------------
# 1. Typed SystemVerilog `struct packed` definitions (MSB-to-LSB SV order)
# ---------------------------------------------------------------------------


class CsrMeta(PackedStruct):
    """Matches SystemVerilog ``csr_meta_t`` (8 bits total)."""

    tag: int = bitfield(4, default=0)
    priv: int = bitfield(2, default=0)
    secure: int = bitfield(1, default=0)
    burst_hint: int = bitfield(1, default=0)


class CsrCtrl(PackedStruct):
    """Matches SystemVerilog ``csr_ctrl_t`` (32 bits total)."""

    threshold: int = bitfield(16, default=0)
    mode: int = bitfield(8, default=0)
    reserved: int = bitfield(6, default=0)
    irq_en: int = bitfield(1, default=0)
    enable: int = bitfield(1, default=1)


class CsrStatus(PackedStruct):
    """Matches SystemVerilog ``csr_status_t`` (32 bits total)."""

    write_count: int = bitfield(8, default=0)
    read_count: int = bitfield(8, default=0)
    last_tag: int = bitfield(4, default=0)
    last_priv: int = bitfield(2, default=0)
    reserved: int = bitfield(9, default=0)
    enabled: int = bitfield(1, default=0)


# ---------------------------------------------------------------------------
# 2. Statically typed Bus definitions (SV interface OR flat prefixed signals)
# ---------------------------------------------------------------------------


class AvalonCsrBus(Bus):
    """Avalon-MM CSR bus with optional ``byteenable``, ``waitrequest``, and packed
    ``meta``.
    """

    address: VectorObject = signal(direction=Direction.INITIATOR, idle_value=0)
    read: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    write: LogicObject = signal(direction=Direction.INITIATOR, idle_value=0)
    writedata: VectorObject = signal(direction=Direction.INITIATOR, idle_value=0)
    readdata: VectorObject = signal(direction=Direction.TARGET)
    readdatavalid: LogicObject | None = signal(default=None, direction=Direction.TARGET)
    waitrequest: LogicObject | None = signal(default=None, direction=Direction.TARGET)
    byteenable: VectorObject | None = signal(
        default=None, direction=Direction.INITIATOR, idle_value=0
    )
    meta: VectorObject | None = signal(
        default=None, direction=Direction.INITIATOR, width=8, idle_value=0
    )
    resp_meta: VectorObject | None = signal(
        default=None, direction=Direction.TARGET, width=8
    )


class CsrDiagBus(Bus):
    """Diagnostic SystemVerilog interface carrying a 32-bit packed status struct."""

    status: VectorObject = signal(direction=Direction.TARGET, width=32)
    irq_pending: LogicObject = signal(direction=Direction.TARGET)


class CsrSubsystemBus(Bus):
    """Composite bus combining Avalon-MM CSR and diagnostic SV interfaces."""

    csr: AvalonCsrBus = sub_bus(hdl_name="csr_if")
    diag: CsrDiagBus = sub_bus(hdl_name="diag_if")


# ---------------------------------------------------------------------------
# 3. Typed Transaction Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AvalonReq:
    """High-level Avalon-MM CSR request."""

    address: int
    is_write: bool
    writedata: int = 0
    byteenable: int = 0xF
    meta: CsrMeta | None = None


@dataclass(frozen=True)
class AvalonResp:
    """Typed Avalon-MM CSR response returned by :class:`AvalonCsrMaster`."""

    address: int
    is_write: bool
    readdata: int = 0
    resp_meta: CsrMeta | None = None


@dataclass(frozen=True)
class AvalonObservedTx:
    """Completed transaction reconstructed passively by :class:`AvalonCsrMonitor`."""

    address: int
    is_write: bool
    data: int
    byteenable: int
    meta: CsrMeta | None = None


# ---------------------------------------------------------------------------
# 4. Avalon-MM CSR Master (`RequestDriver`) & Passive Monitor (`Monitor`)
# ---------------------------------------------------------------------------


class AvalonCsrMaster(RequestDriver[AvalonCsrBus, AvalonReq, AvalonResp]):
    """Avalon-MM CSR initiator built on :class:`~cocotb_bfm.RequestDriver`.

    Provides typed convenience helpers :meth:`write_reg` and :meth:`read_reg`
    while inheriting full queueing, explicit lifecycle, and sync/async reset
    supervision from ``cocotb-bfm``.
    """

    async def write_reg(
        self,
        address: int,
        data: int,
        *,
        byteenable: int = 0xF,
        meta: CsrMeta | None = None,
    ) -> AvalonResp:
        """Issue a CSR write and wait for command acceptance."""
        return await self.request(
            AvalonReq(
                address=address,
                is_write=True,
                writedata=data,
                byteenable=byteenable,
                meta=meta,
            )
        )

    async def read_reg(
        self,
        address: int,
        *,
        meta: CsrMeta | None = None,
    ) -> AvalonResp:
        """Issue a CSR read and return the typed :class:`AvalonResp`."""
        return await self.request(
            AvalonReq(
                address=address,
                is_write=False,
                meta=meta,
            )
        )

    async def _handle_request(self, req: AvalonReq) -> AvalonResp:
        bus = self.bus
        bus.address.value = req.address
        bus.read.value = 0 if req.is_write else 1
        bus.write.value = 1 if req.is_write else 0
        bus.writedata.value = req.writedata
        if bus.byteenable is not None:
            bus.byteenable.value = req.byteenable
        if bus.meta is not None:
            if req.meta is not None:
                req.meta.write_handle(bus.meta)
            else:
                bus.meta.value = 0

        # Hold command stable until accepted on a clock edge with waitrequest == 0.
        while True:
            await RisingEdge(self.clock)
            if bus.waitrequest is None or int(bus.waitrequest.value) == 0:
                break

        # Deassert command phase immediately after acceptance.
        bus.read.value = 0
        bus.write.value = 0

        if req.is_write:
            return AvalonResp(address=req.address, is_write=True)

        # Wait for pipelined readdatavalid (or 1-cycle fixed latency if omitted).
        if bus.readdatavalid is not None:
            while True:
                await RisingEdge(self.clock)
                if int(bus.readdatavalid.value) == 1:
                    break
        else:
            await RisingEdge(self.clock)

        rdata = bus.readdata.value.to_unsigned()
        rmeta = (
            CsrMeta.from_handle(bus.resp_meta) if bus.resp_meta is not None else None
        )
        return AvalonResp(
            address=req.address,
            is_write=False,
            readdata=rdata,
            resp_meta=rmeta,
        )


class AvalonCsrMonitor(Monitor[AvalonCsrBus, AvalonObservedTx]):
    """Passive Avalon-MM CSR monitor tracking writes and pipelined reads.

    Automatically clears its pending-read FIFO on reset via :meth:`_on_reset`.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._pending_reads: deque[tuple[int, int]] = deque()

    async def _on_reset(self) -> None:
        self._pending_reads.clear()

    async def _monitor_loop(self) -> None:
        bus = self.bus
        while True:
            await RisingEdge(self.clock)
            accepted = bus.waitrequest is None or int(bus.waitrequest.value) == 0

            # 1. Sample returning pipelined read data (pre-edge values).
            if bus.readdatavalid is not None and int(bus.readdatavalid.value) == 1:
                if self._pending_reads:
                    raddr, rbe = self._pending_reads.popleft()
                else:
                    raddr, rbe = bus.address.value.to_unsigned(), 0xF
                rmeta = (
                    CsrMeta.from_handle(bus.resp_meta)
                    if bus.resp_meta is not None
                    else None
                )
                self._emit(
                    AvalonObservedTx(
                        address=raddr,
                        is_write=False,
                        data=bus.readdata.value.to_unsigned(),
                        byteenable=rbe,
                        meta=rmeta,
                    )
                )

            # 2. Sample newly accepted write/read commands.
            if accepted and int(bus.write.value) == 1:
                be = (
                    bus.byteenable.value.to_unsigned()
                    if bus.byteenable is not None
                    else 0xF
                )
                meta = CsrMeta.from_handle(bus.meta) if bus.meta is not None else None
                self._emit(
                    AvalonObservedTx(
                        address=bus.address.value.to_unsigned(),
                        is_write=True,
                        data=bus.writedata.value.to_unsigned(),
                        byteenable=be,
                        meta=meta,
                    )
                )
            elif accepted and int(bus.read.value) == 1:
                be = (
                    bus.byteenable.value.to_unsigned()
                    if bus.byteenable is not None
                    else 0xF
                )
                self._pending_reads.append((bus.address.value.to_unsigned(), be))
