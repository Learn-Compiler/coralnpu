package mydie.cluster

import chisel3._
import chisel3.util._
import bus._
import _root_.circt.stage.ChiselStage
import java.nio.file.{Files, Paths}

/** ISC-licensed upstream CPU. No behavioral CPU model or instruction emulation. */
class PicoRV32 extends BlackBox(Map(
  "ENABLE_COUNTERS" -> 0, "ENABLE_COUNTERS64" -> 0,
  "ENABLE_FAST_MUL" -> 1, "ENABLE_DIV" -> 1, "BARREL_SHIFTER" -> 1,
  "COMPRESSED_ISA" -> 0, "ENABLE_IRQ" -> 0,
  "PROGADDR_RESET" -> 0, "STACKADDR" -> 0xff00
)) {
  override val desiredName = "picorv32"
  val io = IO(new Bundle {
    val clk = Input(Clock())
    val resetn = Input(Bool())
    val trap = Output(Bool())
    val mem_valid = Output(Bool())
    val mem_instr = Output(Bool())
    val mem_ready = Input(Bool())
    val mem_addr = Output(UInt(32.W))
    val mem_wdata = Output(UInt(32.W))
    val mem_wstrb = Output(UInt(4.W))
    val mem_rdata = Input(UInt(32.W))
    val mem_la_read = Output(Bool())
    val mem_la_write = Output(Bool())
    val mem_la_addr = Output(UInt(32.W))
    val mem_la_wdata = Output(UInt(32.W))
    val mem_la_wstrb = Output(UInt(4.W))
    val pcpi_valid = Output(Bool())
    val pcpi_insn = Output(UInt(32.W))
    val pcpi_rs1 = Output(UInt(32.W))
    val pcpi_rs2 = Output(UInt(32.W))
    val pcpi_wr = Input(Bool())
    val pcpi_rd = Input(UInt(32.W))
    val pcpi_wait = Input(Bool())
    val pcpi_ready = Input(Bool())
    val irq = Input(UInt(32.W))
    val eoi = Output(UInt(32.W))
    val trace_valid = Output(Bool())
    val trace_data = Output(UInt(36.W))
  })
}

/** A real synthesizable 16 KiB byte-enabled SRAM with a bounded AXI target.
  * One read burst and one write burst can be active independently.
  * Unsupported modes/bounds return DECERR; R/B payload stays stable under stalls.
  * Read-only inspection port is for post-execution verification, never loading.
  */
class DieSram extends Module {
  val io = IO(new Bundle {
    val axi = Flipped(new AxiMasterIO(32, 128, 6))
    val stallPeriod = Input(UInt(10.W))
    val inspectEn = Input(Bool())
    val inspectIndex = Input(UInt(10.W))
    val inspectValid = Output(Bool())
    val inspectData = Output(UInt(128.W))
    val readBeats = Output(UInt(32.W))
    val writeBeats = Output(UInt(32.W))
    val traceValid = Output(Bool())
    val traceData = Output(UInt(32.W))
    val traceCycle = Output(UInt(32.W))
  })
  val memory = SyncReadMem(1024, Vec(16, UInt(8.W)))
  val inspect = memory.read(io.inspectIndex, io.inspectEn)
  io.inspectValid := RegNext(io.inspectEn, false.B)
  io.inspectData := inspect.asUInt
  val cycles = RegInit(0.U(32.W))
  cycles := cycles + 1.U
  val traceValid = RegInit(false.B)
  val traceData = RegInit(0.U(32.W))
  val traceCycle = RegInit(0.U(32.W))
  traceValid := false.B
  io.traceValid := traceValid
  io.traceData := traceData
  io.traceCycle := traceCycle
  val allow = io.stallPeriod === 0.U || cycles % io.stallPeriod =/= 0.U
  def inRange(a: UInt, size: UInt): Bool = {
    a >= "h20000000".U && a < "h20004000".U && size <= 4.U &&
      ((a & 15.U) +& (1.U(5.W) << size)) <= 16.U
  }
  def next(a: AxiAddress): UInt = Mux(a.burst === 1.U, a.addr + (1.U << a.size), a.addr)

  val rIdle :: rLoad :: rCapture :: rResponse :: Nil = Enum(4)
  val rState = RegInit(rIdle)
  val rd = Reg(new AxiAddress(32, 128, 6))
  val rBad = RegInit(false.B)
  val rData = Reg(UInt(128.W))
  val rCount = RegInit(0.U(32.W))
  val validRead = inRange(rd.addr, rd.size) && !rBad
  val line = memory.read(rd.addr(13, 4), rState === rLoad && validRead)
  io.axi.read.addr.ready := rState === rIdle && allow
  io.axi.read.data.valid := rState === rResponse
  io.axi.read.data.bits.data := rData
  io.axi.read.data.bits.id := rd.id
  io.axi.read.data.bits.last := rd.len === 0.U
  io.axi.read.data.bits.resp := Mux(validRead, 0.U, 3.U)
  when(io.axi.read.addr.fire) {
    rd := io.axi.read.addr.bits
    rBad := io.axi.read.addr.bits.burst > 1.U
    rState := rLoad
  }
  when(rState === rLoad) { rState := rCapture }
  when(rState === rCapture) {
    rData := Mux(validRead, line.asUInt, 0.U)
    rState := rResponse
  }
  when(io.axi.read.data.fire) {
    rCount := rCount + 1.U
    when(rd.len === 0.U) { rState := rIdle }.otherwise {
      rd.addr := next(rd)
      rd.len := rd.len - 1.U
      rState := rLoad
    }
  }

  val wIdle :: wData :: wResponse :: Nil = Enum(3)
  val wState = RegInit(wIdle)
  val wr = Reg(new AxiAddress(32, 128, 6))
  val wBad = RegInit(false.B)
  val wCount = RegInit(0.U(32.W))
  io.axi.write.addr.ready := wState === wIdle && allow
  io.axi.write.data.ready := wState === wData && allow
  io.axi.write.resp.valid := wState === wResponse
  io.axi.write.resp.bits.id := wr.id
  io.axi.write.resp.bits.resp := Mux(wBad, 3.U, 0.U)
  when(io.axi.write.addr.fire) {
    wr := io.axi.write.addr.bits
    wBad := io.axi.write.addr.bits.burst > 1.U
    wState := wData
  }
  val baseMask = MuxLookup(wr.size, 0.U(16.W))(Seq(
    0.U -> 1.U, 1.U -> 3.U, 2.U -> 15.U, 3.U -> 255.U, 4.U -> 65535.U
  ))
  val transferMask = (baseMask << wr.addr(3, 0))(15, 0)
  val writeValid = inRange(wr.addr, wr.size) && !wBad &&
    (io.axi.write.data.bits.strb & ~transferMask) === 0.U
  // Last 16B line is reserved. A narrow full-word store at 0x20003ff0
  // prints one packed event; timestamp records the actual AXI W handshake.
  val traceLine = wr.addr >= "h20003ff0".U && wr.addr < "h20004000".U
  val traceWrite = wr.addr === "h20003ff0".U && wr.size === 2.U &&
    wr.len === 0.U && io.axi.write.data.bits.last &&
    io.axi.write.data.bits.strb === "h000f".U
  when(io.axi.write.data.fire) {
    wCount := wCount + 1.U
    when(writeValid && !traceLine) {
      memory.write(wr.addr(13, 4), io.axi.write.data.bits.data.asTypeOf(Vec(16, UInt(8.W))),
        io.axi.write.data.bits.strb.asBools)
    }
    when(writeValid && traceWrite) {
      traceValid := true.B
      traceData := io.axi.write.data.bits.data(31,0)
      traceCycle := cycles
    }
    when(!writeValid || (traceLine && !traceWrite) ||
      io.axi.write.data.bits.last =/= (wr.len === 0.U)) { wBad := true.B }
    when(wr.len === 0.U || io.axi.write.data.bits.last) { wState := wResponse }.otherwise {
      wr.addr := next(wr)
      wr.len := wr.len - 1.U
    }
  }
  when(io.axi.write.resp.fire) { wState := wIdle }
  io.readBeats := rCount
  io.writeBeats := wCount
}

/** RV32 native bus to boot SRAM, system CSR, four Coral host windows and shared AXI.
  * Address windows: CPU RAM 0..64KiB; Coral i at 0x40000000+i*1MiB;
  * shared SRAM at 0x20000000; system CSR at 0x50000000.
  */
class DieControlFabric extends Module {
  val io = IO(new Bundle {
    val cpuValid = Input(Bool())
    val cpuAddr = Input(UInt(32.W))
    val cpuWdata = Input(UInt(32.W))
    val cpuWstrb = Input(UInt(4.W))
    val cpuReady = Output(Bool())
    val cpuRdata = Output(UInt(32.W))
    val run = Input(Bool())
    val bootEn = Input(Bool())
    val bootIndex = Input(UInt(14.W))
    val bootData = Input(UInt(32.W))
    val ports = Vec(5, new AxiMasterIO(32, 128, 6))
    val coralWfi = Input(UInt(4.W))
    val coralFault = Input(UInt(4.W))
    val finish = Output(UInt(32.W))
    val phase = Output(UInt(32.W))
    val checksum = Output(UInt(32.W))
    val started = Output(UInt(4.W))
    val busError = Output(Bool())
    val decodeErrors = Output(UInt(32.W))
    val launches = Output(UInt(32.W))
    val trapMask = Output(UInt(4.W))
  })
  val boot = SyncReadMem(16384, Vec(4, UInt(8.W)))
  when(io.bootEn && !io.run) {
    boot.write(io.bootIndex, io.bootData.asTypeOf(Vec(4, UInt(8.W))))
  }
  val idle :: bootLoad :: bootCapture :: issueRead :: readResponse :: issueWrite :: writeResponse :: response :: Nil = Enum(8)
  val state = RegInit(idle)
  val address = Reg(UInt(32.W))
  val wdata = Reg(UInt(32.W))
  val strb = Reg(UInt(4.W))
  val target = Reg(UInt(3.W))
  val rdata = RegInit(0.U(32.W))
  val awSent = RegInit(false.B)
  val wSent = RegInit(false.B)
  val busError = RegInit(false.B)
  val decodeErrors = RegInit(0.U(32.W))
  val finish = RegInit(0.U(32.W))
  val phase = RegInit(0.U(32.W))
  val checksum = RegInit(0.U(32.W))
  val started = RegInit(0.U(4.W))
  val launches = RegInit(0.U(32.W))
  val trapMask = RegInit(0.U(4.W))
  val cycles = RegInit(0.U(32.W))
  cycles := cycles + 1.U
  io.cpuReady := state === response
  io.cpuRdata := rdata
  io.finish := finish
  io.phase := phase
  io.checksum := checksum
  io.started := started
  io.busError := busError
  io.decodeErrors := decodeErrors
  io.launches := launches
  io.trapMask := trapMask
  val bootRead = boot.read(address(15, 2), state === bootLoad)
  when(state === bootLoad) { state := bootCapture }
  when(state === bootCapture) {
    rdata := bootRead.asUInt
    state := response
  }
  when(state === response && io.cpuValid) { state := idle }

  val local = io.cpuAddr(19, 0)
  val coralWindow = io.cpuAddr >= "h40000000".U && io.cpuAddr < "h40400000".U
  val localMapped = local < "h2000".U ||
    (local >= "h10000".U && local < "h18000".U) ||
    (local >= "h30000".U && local < "h3000c".U) ||
    (local >= "h30100".U && local < "h30124".U)
  when(state === idle && io.cpuValid && io.run) {
    address := Mux(coralWindow, Cat(0.U(12.W), local), io.cpuAddr)
    wdata := io.cpuWdata
    strb := io.cpuWstrb
    awSent := false.B
    wSent := false.B
    rdata := 0.U
    when(io.cpuAddr < "h10000".U) {
      when(io.cpuWstrb.orR) {
        boot.write(io.cpuAddr(15, 2), io.cpuWdata.asTypeOf(Vec(4, UInt(8.W))), io.cpuWstrb.asBools)
        state := response
      }.otherwise { state := bootLoad }
    }.elsewhen(io.cpuAddr >= "h50000000".U && io.cpuAddr < "h50000030".U && io.cpuAddr(1, 0) === 0.U) {
      state := response
      rdata := MuxLookup(io.cpuAddr(7, 0), 0.U)(Seq(
        0x00.U -> "h434f5241".U, 0x04.U -> io.coralWfi,
        0x08.U -> io.coralFault, 0x0c.U -> finish,
        0x10.U -> cycles, 0x14.U -> started,
        0x18.U -> busError.asUInt, 0x1c.U -> phase,
        0x20.U -> checksum, 0x24.U -> decodeErrors, 0x28.U -> launches, 0x2c.U -> trapMask
      ))
      when(io.cpuWstrb.orR) {
        when(io.cpuAddr(7, 0) === 0x0c.U) { finish := io.cpuWdata }
        when(io.cpuAddr(7, 0) === 0x18.U && io.cpuWdata(0)) { busError := false.B }
        when(io.cpuAddr(7, 0) === 0x1c.U) { phase := io.cpuWdata }
        when(io.cpuAddr(7, 0) === 0x20.U) { checksum := io.cpuWdata }
        when(io.cpuAddr(7, 0) === 0x2c.U) { trapMask := io.cpuWdata(3, 0) }
      }
    }.elsewhen((coralWindow && localMapped) ||
      (io.cpuAddr >= "h20000000".U && io.cpuAddr < "h20004000".U)) {
      target := Mux(coralWindow, io.cpuAddr(21, 20), 4.U)
      state := Mux(io.cpuWstrb.orR, issueWrite, issueRead)
    }.otherwise {
      rdata := "hdeadbeef".U
      busError := true.B
      decodeErrors := decodeErrors + 1.U
      state := response
    }
  }
  for (i <- 0 until 5) {
    val port = io.ports(i)
    port.read.addr.bits := 0.U.asTypeOf(port.read.addr.bits)
    port.read.addr.bits.addr := address
    port.read.addr.bits.size := 2.U
    port.read.addr.bits.burst := 1.U
    port.read.addr.valid := target === i.U && state === issueRead
    port.read.data.ready := target === i.U && state === readResponse
    port.write.addr.bits := port.read.addr.bits
    port.write.addr.valid := target === i.U && state === issueWrite && !awSent
    port.write.data.bits.data := (wdata << (address(3, 0) << 3))(127, 0)
    port.write.data.bits.strb := (strb << address(3, 0))(15, 0)
    port.write.data.bits.last := true.B
    port.write.data.valid := target === i.U && state === issueWrite && !wSent
    port.write.resp.ready := target === i.U && state === writeResponse
    when(port.read.addr.fire) { state := readResponse }
    when(port.read.data.fire) {
      rdata := (port.read.data.bits.data >> (address(3, 0) << 3))(31, 0)
      when(port.read.data.bits.resp =/= 0.U) { busError := true.B }
      state := response
    }
    when(port.write.addr.fire) { awSent := true.B }
    when(port.write.data.fire) { wSent := true.B }
    when(target === i.U && state === issueWrite &&
      (awSent || port.write.addr.fire) && (wSent || port.write.data.fire)) {
      state := writeResponse
    }
    when(port.write.resp.fire) {
      when(port.write.resp.bits.resp =/= 0.U) { busError := true.B }
      when(i.U < 4.U && address === "h30000".U && wdata === 0.U && port.write.resp.bits.resp === 0.U) {
        started := started | (1.U(4.W) << i)(3, 0)
        launches := launches + 1.U
      }
      state := response
    }
  }
}

/** Single-die functional RTL: one actual RV32IM control CPU + four Coral cores.
  * Boot loading is only possible while the controller is held in reset.
  * No external AXI host ports or C++ shared-memory target are used by this top.
  */
class CoralDie extends RawModule {
  val io = IO(new Bundle {
    val aclk = Input(Clock())
    val aresetn = Input(AsyncReset())
    val run = Input(Bool())
    val bootEn = Input(Bool())
    val bootIndex = Input(UInt(14.W))
    val bootData = Input(UInt(32.W))
    val stallPeriod = Input(UInt(10.W))
    val inspectEn = Input(Bool())
    val inspectIndex = Input(UInt(10.W))
    val inspectValid = Output(Bool())
    val inspectData = Output(UInt(128.W))
    val cpuTrap = Output(Bool())
    val cpuAddress = Output(UInt(32.W))
    val coralFault = Output(UInt(4.W))
    val coralWfi = Output(UInt(4.W))
    val finish = Output(UInt(32.W))
    val phase = Output(UInt(32.W))
    val checksum = Output(UInt(32.W))
    val started = Output(UInt(4.W))
    val busError = Output(Bool())
    val decodeErrors = Output(UInt(32.W))
    val launches = Output(UInt(32.W))
    val trapMask = Output(UInt(4.W))
    val readBeats = Output(UInt(32.W))
    val writeBeats = Output(UInt(32.W))
    val traceValid = Output(Bool())
    val traceData = Output(UInt(32.W))
    val traceCycle = Output(UInt(32.W))
    val rvvMacIssued = Output(Vec(4, UInt(32.W)))
  })
  withClockAndReset(io.aclk, (!io.aresetn.asBool).asAsyncReset) {
    val cpu = Module(new PicoRV32)
    cpu.io.clk := io.aclk
    cpu.io.resetn := io.aresetn.asBool && io.run
    cpu.io.pcpi_wr := false.B
    cpu.io.pcpi_rd := 0.U
    cpu.io.pcpi_wait := false.B
    cpu.io.pcpi_ready := false.B
    cpu.io.irq := 0.U
    val cluster = Module(new CoralCluster(4, true, observeRvv = true))
    io.rvvMacIssued := cluster.io.rvvMacIssued
    cluster.io.aclk := io.aclk
    cluster.io.aresetn := io.aresetn
    val fabric = Module(new DieControlFabric)
    fabric.io.cpuValid := cpu.io.mem_valid
    fabric.io.cpuAddr := cpu.io.mem_addr
    fabric.io.cpuWdata := cpu.io.mem_wdata
    fabric.io.cpuWstrb := cpu.io.mem_wstrb
    cpu.io.mem_ready := fabric.io.cpuReady
    cpu.io.mem_rdata := fabric.io.cpuRdata
    fabric.io.run := io.run
    fabric.io.bootEn := io.bootEn
    fabric.io.bootIndex := io.bootIndex
    fabric.io.bootData := io.bootData
    fabric.io.coralFault := cluster.io.fault.asUInt
    fabric.io.coralWfi := cluster.io.wfi.asUInt
    for (i <- 0 until 4) { cluster.io.hosts(i) <> fabric.io.ports(i) }
    val shared = Module(new SharedAxiArbiter(32, 128, 6, 2))
    shared.io.cores(0) <> cluster.io.memory
    shared.io.cores(1) <> fabric.io.ports(4)
    val ram = Module(new DieSram)
    ram.io.axi <> shared.io.memory
    ram.io.stallPeriod := io.stallPeriod
    ram.io.inspectEn := io.inspectEn
    ram.io.inspectIndex := io.inspectIndex
    io.inspectValid := ram.io.inspectValid
    io.inspectData := ram.io.inspectData
    io.readBeats := ram.io.readBeats
    io.writeBeats := ram.io.writeBeats
    io.traceValid := ram.io.traceValid
    io.traceData := ram.io.traceData
    io.traceCycle := ram.io.traceCycle
    io.cpuTrap := cpu.io.trap
    io.cpuAddress := cpu.io.mem_addr
    io.coralFault := cluster.io.fault.asUInt
    io.coralWfi := cluster.io.wfi.asUInt
    io.finish := fabric.io.finish
    io.phase := fabric.io.phase
    io.checksum := fabric.io.checksum
    io.started := fabric.io.started
    io.busError := fabric.io.busError
    io.decodeErrors := fabric.io.decodeErrors
    io.launches := fabric.io.launches
    io.trapMask := fabric.io.trapMask
  }
}

object EmitDie extends App {
  val target = args.find(_.startsWith("--target-dir=")).map(_.split("=", 2)(1)).getOrElse(".")
  val source = ChiselStage.emitSystemVerilog(new CoralDie, Array(), Array(
    "--lowering-options=disallowLocalVariables,locationInfoStyle=none", "-enable-layers=Verification"
  ))
  val separator = "// ----- 8< ----- FILE \"firrtl_black_box_resource_files.f\" ----- 8< -----"
  Files.createDirectories(Paths.get(target))
  Files.write(Paths.get(target, "CoralDie.sv"), source.split(java.util.regex.Pattern.quote(separator))(0).getBytes("UTF-8"))
}
