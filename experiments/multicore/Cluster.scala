package mydie.cluster

import chisel3._
import chisel3.util._
import bus._
import coralnpu.{CoreAxi, MemoryRegions, Parameters}
import _root_.circt.stage.ChiselStage
import java.nio.file.{Files, Paths}

/** One outstanding read and one outstanding write; owners stay locked to RLAST/B.
  * AW is arbitrated independently of AR. W is routed only after AW selects its owner.
  * No assumption that different cores use different AXI IDs.
  */
class SharedAxiArbiter(addrBits: Int, dataBits: Int, idBits: Int, numCores: Int) extends Module {
  require(Set(2, 4).contains(numCores))
  private val ownerBits = log2Ceil(numCores)
  val io = IO(new Bundle {
    val cores = Vec(numCores, Flipped(new AxiMasterIO(addrBits, dataBits, idBits)))
    val memory = new AxiMasterIO(addrBits, dataBits, idBits)
  })
  val readBusy = RegInit(false.B)
  val readOwner = RegInit(0.U(ownerBits.W))
  val readArb = Module(new RRArbiter(new AxiAddress(addrBits, dataBits, idBits), numCores))
  for (i <- 0 until numCores) {
    readArb.io.in(i).bits := io.cores(i).read.addr.bits
    readArb.io.in(i).valid := io.cores(i).read.addr.valid && !readBusy
    io.cores(i).read.addr.ready := readArb.io.in(i).ready && !readBusy
    io.cores(i).read.data.bits := io.memory.read.data.bits
    io.cores(i).read.data.valid := io.memory.read.data.valid && readBusy && (readOwner === i.U)
  }
  io.memory.read.addr.bits := readArb.io.out.bits
  io.memory.read.addr.valid := readArb.io.out.valid && !readBusy
  readArb.io.out.ready := io.memory.read.addr.ready && !readBusy
  io.memory.read.data.ready := readBusy && io.cores(readOwner).read.data.ready
  when(io.memory.read.addr.fire) {
    readBusy := true.B
    readOwner := readArb.io.chosen
  }
  when(io.memory.read.data.fire && io.memory.read.data.bits.last) { readBusy := false.B }

  val idle :: data :: response :: Nil = Enum(3)
  val writeState = RegInit(idle)
  val writeOwner = RegInit(0.U(ownerBits.W))
  val writeArb = Module(new RRArbiter(new AxiAddress(addrBits, dataBits, idBits), numCores))
  for (i <- 0 until numCores) {
    writeArb.io.in(i).bits := io.cores(i).write.addr.bits
    writeArb.io.in(i).valid := io.cores(i).write.addr.valid && (writeState === idle)
    io.cores(i).write.addr.ready := writeArb.io.in(i).ready && (writeState === idle)
    io.cores(i).write.data.ready := io.memory.write.data.ready && (writeState === data) && (writeOwner === i.U)
    io.cores(i).write.resp.bits := io.memory.write.resp.bits
    io.cores(i).write.resp.valid := io.memory.write.resp.valid && (writeState === response) && (writeOwner === i.U)
  }
  io.memory.write.addr.bits := writeArb.io.out.bits
  io.memory.write.addr.valid := writeArb.io.out.valid && (writeState === idle)
  writeArb.io.out.ready := io.memory.write.addr.ready && (writeState === idle)
  io.memory.write.data.bits := io.cores(writeOwner).write.data.bits
  io.memory.write.data.valid := io.cores(writeOwner).write.data.valid && (writeState === data)
  io.memory.write.resp.ready := io.cores(writeOwner).write.resp.ready && (writeState === response)
  when(io.memory.write.addr.fire) {
    writeOwner := writeArb.io.chosen
    writeState := data
  }
  when(io.memory.write.data.fire && io.memory.write.data.bits.last) { writeState := response }
  when(io.memory.write.resp.fire) { writeState := idle }
}

/** Unmodified Coral AXI cores; host ports remain independent local apertures. */
class CoralCluster(numCores: Int, enableRvv: Boolean, observeRvv: Boolean = false) extends RawModule {
  require(Set(2, 4).contains(numCores))
  require(!observeRvv || enableRvv)
  override val desiredName = (if (numCores == 4) "QuadCoral" else "DualCoral") +
    (if (enableRvv) "Rvv" else "")
  val p = new Parameters(MemoryRegions.default)
  p.fetchDataBits = 128
  p.lsuDataBits = 128
  p.enableFloat = true
  p.enableZfbfmin = true
  p.enableRvv = enableRvv
  p.enableVectorBf16 = enableRvv
  p.rawExposeDebugPorts = observeRvv // Observe dispatch without enabling full ROB verification.
  val io = IO(new Bundle {
    val aclk = Input(Clock())
    val aresetn = Input(AsyncReset())
    val hosts = Vec(numCores, Flipped(new AxiMasterIO(p.axi2AddrBits, p.axi2DataBits, p.axi2IdBits)))
    val memory = new AxiMasterIO(p.axi2AddrBits, p.axi2DataBits, p.axi2IdBits)
    val halted = Output(Vec(numCores, Bool()))
    val fault = Output(Vec(numCores, Bool()))
    val wfi = Output(Vec(numCores, Bool()))
    val rvvMacIssued = Output(Vec(numCores, UInt(32.W)))
  })
  val arbiter = withClockAndReset(io.aclk, (!io.aresetn.asBool).asAsyncReset) {
    Module(new SharedAxiArbiter(p.axi2AddrBits, p.axi2DataBits, p.axi2IdBits, numCores))
  }
  io.memory <> arbiter.io.memory
  for (i <- 0 until numCores) {
    val core = Module(new CoreAxi(p, if (enableRvv) "ClusterRvvCore" else "ClusterScalarCore"))
    core.suggestName(s"coral_$i")
    core.io.aclk := io.aclk
    core.io.aresetn := io.aresetn
    core.io.axi_slave <> io.hosts(i)
    arbiter.io.cores(i) <> core.io.axi_master
    core.io.irq := false.B
    core.io.timer_irq := false.B
    core.io.software_irq := false.B
    core.io.boot_addr := 0.U
    core.io.te := false.B
    core.io.dm.req.valid := false.B
    core.io.dm.req.bits := 0.U.asTypeOf(core.io.dm.req.bits)
    core.io.dm.rsp.ready := true.B
    io.halted(i) := core.io.halted
    io.fault(i) := core.io.fault
    io.wfi(i) := core.io.wfi
    if (observeRvv) {
      withClockAndReset(io.aclk, (!io.aresetn.asBool).asAsyncReset) {
        val count = RegInit(0.U(32.W))
        // vmacc.vx / vmadd.vx: OP-V=0x57, funct6=101101/101001, funct3=110.
        // Count accepted dispatches, not fetches or architectural retirements.
        val macs = core.io.debug.get.dispatch.map { lane =>
          lane.instFire && lane.instInst(6,0) === "h57".U &&
            (lane.instInst(31,26) === "b101101".U || lane.instInst(31,26) === "b101001".U) &&
            lane.instInst(14,12) === "b110".U
        }
        when(!core.io.wfi && !core.io.halted) { count := count + PopCount(macs) }
        io.rvvMacIssued(i) := count
      }
    } else { io.rvvMacIssued(i) := 0.U }
  }
}

object EmitCluster extends App {
  val target = args.find(_.startsWith("--target-dir=")).map(_.split("=", 2)(1)).getOrElse(".")
  val rvv = args.contains("--rvv")
  val cores = args.find(_.startsWith("--cores=")).map(_.split("=", 2)(1).toInt).getOrElse(2)
  val name = (if (cores == 4) "QuadCoral" else "DualCoral") + (if (rvv) "Rvv" else "")
  val source = ChiselStage.emitSystemVerilog(new CoralCluster(cores, rvv), Array(), Array(
    "--lowering-options=disallowLocalVariables,locationInfoStyle=none",
    "-enable-layers=Verification"
  ))
  val separator = "// ----- 8< ----- FILE \"firrtl_black_box_resource_files.f\" ----- 8< -----"
  Files.createDirectories(Paths.get(target))
  Files.write(Paths.get(target, name + ".sv"), source.split(java.util.regex.Pattern.quote(separator))(0).getBytes("UTF-8"))
}
