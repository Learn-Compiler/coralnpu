// ELF functional runner for the official Coral MPACT instruction model.
#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <unistd.h>

#include "absl/log/initialize.h"
#include "sim/coralnpu_simulator.h"
#include "mpact/sim/util/memory/memory_watcher.h"

using coralnpu::sim::CoralNPUSimulator;
using coralnpu::sim::CoralNPUSimulatorOptions;
using coralnpu::sim::MemoryPermission;
using mpact::sim::generic::Instruction;
using mpact::sim::util::MemoryWatcher;

int main(int argc, char** argv) {
  absl::InitializeLog();
  if (argc < 2) { std::cerr << "Usage: coral_iss program.elf [--max-instructions=N] [--json=FILE]\n"; return 2; }
  std::string elf = argv[1], json_path;
  uint64_t limit = 100000000;
  uint32_t stack_top = 0x7fffe000;
  auto architecture = coralnpu::sim::Architecture::kV2;
  for (int i = 2; i < argc; ++i) {
    std::string arg = argv[i];
    try {
      if (arg.starts_with("--max-instructions=")) limit = std::stoull(arg.substr(19));
      else if (arg.starts_with("--json=")) json_path = arg.substr(7);
      else if (arg.starts_with("--stack-top=")) stack_top = std::stoul(arg.substr(12), nullptr, 0);
      else if (arg == "--cpu=coral-v2") architecture = coralnpu::sim::Architecture::kV2;
      else if (arg == "--cpu=coral-m3") architecture = coralnpu::sim::Architecture::kM3;
      else if (arg == "--cpu=coral-m4") architecture = coralnpu::sim::Architecture::kM4;
      else { std::cerr << "Unknown option: " << arg << '\n'; return 2; }
    } catch (...) { std::cerr << "Invalid option: " << arg << '\n'; return 2; }
  }
  if (!limit) { std::cerr << "Instruction limit must be positive\n"; return 2; }
  std::ifstream input(elf, std::ios::binary);
  unsigned char header[20] = {};
  input.read(reinterpret_cast<char*>(header), sizeof(header));
  if (input.gcount() != sizeof(header) || header[0] != 0x7f || header[1] != 'E' ||
      header[2] != 'L' || header[3] != 'F' || header[4] != 1 || header[5] != 1 ||
      header[18] != 0xf3 || header[19] != 0) {
    std::cerr << "Coral ISS requires a little-endian ELF32 RISC-V executable\n"; return 2;
  }
  CoralNPUSimulatorOptions options;
  options.architecture = architecture;
  options.initial_misa_value = 0x40201120;
  options.skip_default_handlers = true;
  options.semihost_htif = false;
  // Functional address space, lazily backed. This does not model TCM capacity
  // or physical memory timing; use the RTL runner for those constraints.
  options.memory_regions = {
      {0, 0x80000000u, MemoryPermission::kReadWriteExecute},
      {0x80000000u, 0x80000000u, MemoryPermission::kReadWriteExecute}};
  CoralNPUSimulator sim(options);
  auto loaded = sim.LoadProgram(elf);
  if (!loaded.ok()) { std::cerr << "ELF load failed: " << loaded << '\n'; return 2; }
  auto sp = sim.WriteRegister("x2", stack_top);
  if (!sp.ok()) { std::cerr << sp << '\n'; return 2; }
  int code = 0;
  bool finished = false;
  std::string reason = "instruction_limit";
  auto stop = [&](int value, const char* why, const Instruction* inst) {
    code = value; reason = why; finished = true;
    sim.top()->RequestHalt(CoralNPUSimulator::HaltReason::kUserRequest, inst);
  };
  auto reg = [&](int number) -> uint32_t {
    auto value = sim.ReadRegister("x" + std::to_string(number));
    return value.ok() ? static_cast<uint32_t>(*value) : 0;
  };
  // Debugger writes/reads require a halted core. Syscall/device callbacks run
  // inside Step(), so access architectural buffers and raw backing memory.
  auto result = [&](int64_t value) {
    sim.state()->registers()->at("x10")->data_buffer()->Set<uint32_t>(0, static_cast<uint32_t>(value));
  };
  auto read = [&](uint32_t address, void* bytes, size_t count) {
    if (uint64_t(address) + count > 0x100000000ull) return false;
    if (!count) return true;
    auto* db = sim.state()->db_factory()->Allocate<uint8_t>(count);
    sim.memory()->Load(address, db, nullptr, nullptr);
    std::memcpy(bytes, db->Get<uint8_t>().data(), count);
    db->DecRef();
    return true;
  };
  // Observe stores through the model memory interface, including identical
  // consecutive bytes and NUL, are observed without instruction polling.
  MemoryWatcher uart(sim.state()->memory());
  auto watched = uart.SetStoreWatchCallback(MemoryWatcher::AddressRange(0x54000000, 0x54000003),
      [&](uint64_t address, int size) {
        std::vector<unsigned char> bytes(size);
        if (!read(address, bytes.data(), bytes.size())) { stop(1, "uart_read_error", nullptr); return; }
        if (std::fwrite(bytes.data(), 1, bytes.size(), stdout) != bytes.size() || std::fflush(stdout))
          stop(1, "console_error", nullptr);
      });
  if (!watched.ok()) { std::cerr << watched << '\n'; return 2; }
  sim.state()->set_memory(&uart);
  sim.state()->AddMpauseHandler([&](const Instruction* inst) { stop(0, "mpause", inst); return true; });
  sim.state()->set_on_trap([&](bool interrupt, uint64_t value, uint64_t cause, uint64_t pc, const Instruction* inst) {
    std::cerr << "Trap: cause=" << cause << " pc=0x" << std::hex << pc
              << " value=0x" << value << std::dec << '\n';
    stop(1, interrupt ? "interrupt" : "trap", inst); return true;
  });
  sim.state()->set_on_ecall([&](const Instruction* inst) {
    uint32_t call = reg(17), address = reg(11), count = reg(12);
    if (call == 93 || call == 94) { stop(reg(10) & 255, "exit", inst); return true; }
    if (call == 64 || call == 63) {
      int fd = reg(10);
      if ((call == 64 && fd != 1 && fd != 2) || (call == 63 && fd != 0)) { result(-EBADF); return true; }
      if (count > 16 * 1024 * 1024 || uint64_t(address) + count > 0x100000000ull) { result(-EFAULT); return true; }
      std::vector<unsigned char> bytes(count);
      if (call == 64) {
        if (!read(address, bytes.data(), count)) { result(-EFAULT); return true; }
        ssize_t written = ::write(fd, bytes.data(), count);
        result(written < 0 ? -errno : written);
      } else {
        ssize_t received = ::read(fd, bytes.data(), count);
        if (received >= 0) {
          if (received) {
            auto* db = sim.state()->db_factory()->Allocate<uint8_t>(received);
            std::memcpy(db->Get<uint8_t>().data(), bytes.data(), received);
            sim.memory()->Store(address, db);
            db->DecRef();
          }
        }
        result(received < 0 ? -errno : received);
      }
      return true;
    }
    std::cerr << "Unsupported bare-metal ECALL " << call
              << "; Linux programs require --abi linux\n";
    stop(1, "unsupported_ecall", inst); return true;
  });
  // Standard RISC-V semihosting breakpoint marker, not an arbitrary ebreak.
  sim.state()->AddEbreakHandler([&](const Instruction* inst) {
    uint32_t before = 0, after = 0;
    if (inst->address() >= 4 && read(inst->address() - 4, &before, 4) &&
        read(inst->address() + 4, &after, 4) && before == 0x01f01013 && after == 0x40705013) {
      uint32_t call = reg(10), parameter = reg(11);
      if (call == 3 || call == 4) {
        size_t length = 0;
        while (length < 1024 * 1024) {
          unsigned char ch;
          if (!read(parameter + length, &ch, 1)) { stop(1, "semihost_read_error", inst); break; }
          if (call == 4 && !ch) break;
          std::fputc(ch, stdout); ++length;
          if (call == 3) break;
        }
        std::fflush(stdout); result(0); return true;
      }
      if (call == 0x18 || call == 0x20) {
        uint32_t words[2] = {};
        int status = call == 0x18 && parameter == 0x20026 ? 0 : 1;
        if (call == 0x20 && read(parameter, words, sizeof(words)) && words[0] == 0x20026) status = words[1] & 255;
        stop(status, "semihost_exit", inst); return true;
      }
      stop(1, "unsupported_semihost_call", inst); return true;
    }
    stop(1, "ebreak", inst); return true;
  });
  uint64_t steps = 0;
  while (!finished && steps < limit) {
    int batch = static_cast<int>(std::min<uint64_t>(10000, limit - steps));
    auto advanced = sim.Step(batch);
    if (!advanced.ok()) { std::cerr << advanced.status() << '\n'; stop(1, "execution_error", nullptr); break; }
    steps += *advanced;
    if (*advanced < batch && !finished) { finished = true; code = 1; reason = "unexpected_model_halt"; }
  }
  if (!finished) code = 124;
  auto instructions = sim.top()->counter_num_instructions()->GetValue();
  std::cerr << "Instructions: " << instructions << '\n' << "Exit status: " << code
            << " (" << reason << ")\n";
  if (!json_path.empty()) {
    std::ofstream json(json_path);
    json << "{\"engine\":\"mpact\",\"instructions\":" << instructions
         << ",\"exit_code\":" << code << ",\"reason\":\"" << reason << "\"}\n";
    if (!json) return 2;
  }
  sim.state()->set_memory(sim.memory());
  return code;
}
