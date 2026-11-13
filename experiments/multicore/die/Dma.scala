package mydie.cluster

import chisel3._
import chisel3.util._
import bus._

class DmaCommand extends Bundle {
  val src = UInt(32.W)
  val dst = UInt(32.W)
  val bytes = UInt(32.W)
  val tag = UInt(32.W)
}

/** Independent AXI copy engine. One transfer, one outstanding beat at a time.
  * 16-byte beats when both ends permit it; 4-byte beats handle aligned tails.
  * Completion is published only after the final write response. No CPU copying.
  */
class DieDma extends Module {
  val io = IO(new Bundle {
    val command = Flipped(Valid(new DmaCommand))
    val clear = Input(Bool())
    val ports = Vec(5, new AxiMasterIO(32, 128, 6))
    val busy = Output(Bool())
    val done = Output(Bool())
    val error = Output(UInt(3.W))
    val tag = Output(UInt(32.W))
    val progress = Output(UInt(32.W))
    val completed = Output(UInt(32.W))
    val rejected = Output(UInt(32.W))
  })
  val idle :: readAddr :: readData :: writeBeat :: writeResp :: Nil = Enum(5)
  val state = RegInit(idle)
  val src = Reg(UInt(32.W))
  val dst = Reg(UInt(32.W))
  val remaining = Reg(UInt(32.W))
  val payload = Reg(UInt(128.W))
  val awSent = RegInit(false.B)
  val wSent = RegInit(false.B)
  val done = RegInit(false.B)
  val error = RegInit(0.U(3.W))
  val tag = RegInit(0.U(32.W))
  val progress = RegInit(0.U(32.W))
  val completed = RegInit(0.U(32.W))
  val rejected = RegInit(0.U(32.W))
  io.busy := state =/= idle
  io.done := done
  io.error := error
  io.tag := tag
  io.progress := progress
  io.completed := completed
  io.rejected := rejected

  def validRange(address: UInt, bytes: UInt): Bool = {
    val end = address +& bytes
    val local = address(19, 0)
    val localEnd = local +& bytes
    val shared = address >= "h20000000".U && end <= "h20003ff0".U
    val core = address >= "h40000000".U && address < "h40400000".U &&
      ((local < "h2000".U && localEnd <= "h2000".U) ||
       (local >= "h10000".U && localEnd <= "h18000".U))
    !end(32) && (shared || core)
  }
  def target(address: UInt): UInt = Mux(address(30), address(21, 20), 4.U)
  def local(address: UInt): UInt = Mux(address(30), Cat(0.U(12.W), address(19, 0)), address)
  val wide = src(3, 0) === 0.U && dst(3, 0) === 0.U && remaining >= 16.U
  val size = Mux(wide, 4.U, 2.U)
  val beatBytes = Mux(wide, 16.U(5.W), 4.U(5.W))
  val readTarget = target(src)
  val writeTarget = target(dst)
  when(io.clear && state === idle) { done := false.B; error := 0.U }
  when(io.command.valid) {
    when(state =/= idle) { rejected := rejected + 1.U }.otherwise {
      val c = io.command.bits
      val sameSpace = (c.src(31, 20) === c.dst(31, 20))
      val overlap = sameSpace && c.src < (c.dst +& c.bytes) && c.dst < (c.src +& c.bytes)
      done := false.B
      error := 0.U
      tag := c.tag
      progress := 0.U
      when(c.bytes === 0.U || (c.src | c.dst | c.bytes)(1, 0) =/= 0.U || overlap) {
        done := true.B; error := 1.U
      }.elsewhen(!validRange(c.src, c.bytes) || !validRange(c.dst, c.bytes)) {
        done := true.B; error := 2.U
      }.otherwise {
        src := c.src; dst := c.dst; remaining := c.bytes
        state := readAddr
      }
    }
  }
  for (i <- 0 until 5) {
    val p = io.ports(i)
    p.read.addr.bits := 0.U.asTypeOf(p.read.addr.bits)
    p.read.addr.bits.addr := local(src)
    p.read.addr.bits.size := size
    p.read.addr.bits.burst := 1.U
    p.read.addr.valid := state === readAddr && readTarget === i.U
    p.read.data.ready := state === readData && readTarget === i.U
    p.write.addr.bits := 0.U.asTypeOf(p.write.addr.bits)
    p.write.addr.bits.addr := local(dst)
    p.write.addr.bits.size := size
    p.write.addr.bits.burst := 1.U
    p.write.addr.valid := state === writeBeat && writeTarget === i.U && !awSent
    p.write.data.bits.data := (payload << (dst(3, 0) << 3))(127, 0)
    p.write.data.bits.strb := (Mux(wide, "hffff".U(16.W), "h000f".U(16.W)) << dst(3, 0))(15, 0)
    p.write.data.bits.last := true.B
    p.write.data.valid := state === writeBeat && writeTarget === i.U && !wSent
    p.write.resp.ready := state === writeResp && writeTarget === i.U
    when(p.read.addr.fire) { state := readData }
    when(p.read.data.fire) {
      when(p.read.data.bits.resp =/= 0.U || !p.read.data.bits.last) {
        error := 3.U; done := true.B; state := idle
      }.otherwise {
        payload := p.read.data.bits.data >> (src(3, 0) << 3)
        awSent := false.B; wSent := false.B; state := writeBeat
      }
    }
    when(p.write.addr.fire) { awSent := true.B }
    when(p.write.data.fire) { wSent := true.B }
    when(state === writeBeat && writeTarget === i.U &&
      (awSent || p.write.addr.fire) && (wSent || p.write.data.fire)) { state := writeResp }
    when(p.write.resp.fire) {
      when(p.write.resp.bits.resp =/= 0.U) {
        error := 4.U; done := true.B; state := idle
      }.otherwise {
        progress := progress + beatBytes
        when(remaining === beatBytes) {
          completed := completed + 1.U; done := true.B; state := idle
        }.otherwise {
          src := src + beatBytes; dst := dst + beatBytes
          remaining := remaining - beatBytes; state := readAddr
        }
      }
    }
  }
}
