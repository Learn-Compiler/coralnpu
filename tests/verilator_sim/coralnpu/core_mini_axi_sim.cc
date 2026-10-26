// Copyright 2024 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>

#include <optional>
#include <iostream>
#include <string>
#include <thread>

#include "absl/flags/flag.h"
#include "absl/flags/parse.h"
#include "absl/flags/usage.h"
#include "absl/log/check.h"
#include "absl/log/initialize.h"
#include "absl/log/log.h"
#include "tests/verilator_sim/coralnpu/core_mini_axi_tb.h"
#include "tests/verilator_sim/sysc_tb.h"

/* clang-format off */
#include <systemc.h>
/* clang-format on */

ABSL_FLAG(int, cycles, 100000000, "Simulation cycles");
ABSL_FLAG(bool, trace, false, "Dump VCD trace");
ABSL_FLAG(std::string, binary, "", "Binary to execute");
ABSL_FLAG(bool, debug_axi, false, "Enable AXI traffic debugging");
ABSL_FLAG(bool, instr_trace, false, "Log instructions to console");
ABSL_FLAG(bool, backdoor_load, false, "Enable high-speed backdoor code loading");
ABSL_FLAG(std::string, dump_memory, "", "Dump final RTL SRAM contents to this file");
ABSL_FLAG(std::string, console_file, "", "Write target UART bytes here instead of stdout");

static bool run(const char* name, const std::string binary, const int cycles,
                const bool trace, const bool debug_axi, const bool instr_trace,
                const bool backdoor_load, const std::string& dump_memory,
                const std::string& console_file) {
  FILE* console = console_file.empty() ? stdout : std::fopen(console_file.c_str(), "wb");
  CHECK(console != nullptr) << "Cannot open target console file";
  absl::Mutex halted_mtx;
  absl::CondVar halted_cv;
  bool halted_received = false;
  CoreMiniAxi_tb tb(CoreMiniAxi_tb::kCoreMiniAxiModelName, cycles, /* random= */ false, debug_axi,
                    instr_trace, backdoor_load,
                    /*wfi_cb=*/std::nullopt,
                    /*halted_cb=*/[&halted_mtx, &halted_cv, &halted_received]() {
                      absl::MutexLock lock_(&halted_mtx);
                      halted_received = true;
                      halted_cv.SignalAll();
                    });
  tb.SetConsole(console);
  // Ensure we call sc_start @0 time, before we startup anything else.
  // This allows Verilog `initial` blocks to run before we try to interact with the
  // DUT model.
  sc_start(SC_ZERO_TIME);

  if (trace) {
    tb.trace(tb.core());
  }

  std::thread sc_main_thread([&tb]() { tb.start(); });

  CHECK_OK(tb.LoadElfSync(binary));
  CHECK_OK(tb.ClockGateSync(false));
  CHECK_OK(tb.ResetAsync(false));

  {
    absl::MutexLock lock_(&halted_mtx);
    while (!halted_received) halted_cv.Wait(&halted_mtx);
  }

  CoreMiniAxi_tb::RunCounters counters;
  CHECK_OK(tb.SnapshotCountersSync(&counters));

  if (!tb.io_fault && !tb.tohost_halt) {
    CHECK_OK(tb.CheckStatusSync());
  }

  sc_stop();
  sc_main_thread.join();
  if (!console_file.empty()) CHECK_EQ(std::fclose(console), 0);
  const bool dump_ok = dump_memory.empty() ||
      sram_backdoor_dump_memory(dump_memory.c_str()) == 0;
  const bool success = !tb.io_fault &&
      !(tb.tohost_halt && tb.tohost_val != 1) && dump_ok;
  std::cerr << "Retired instructions: " << counters.retired_instructions << '\n'
            << "Core cycles: " << counters.cycles << '\n'
            << "Simulation status: " << (success ? "PASS" : "FAIL") << std::endl;
  std::cerr << "CORAL_SIM_RESULT {\"status\":\"" << (success ? "PASS" : "FAIL")
            << "\",\"halted\":" << (tb.io_halted.read() ? "true" : "false")
            << ",\"fault\":" << (tb.io_fault.read() ? "true" : "false")
            << ",\"dump_ok\":" << (dump_ok ? "true" : "false")
            << ",\"retired_instructions\":" << counters.retired_instructions
            << ",\"core_cycles\":" << counters.cycles
            << ",\"mcause\":" << counters.mcause
            << ",\"mepc\":" << counters.mepc
            << ",\"mtval\":" << counters.mtval
            << ",\"simulation_time_ns\":" << sc_time_stamp().to_seconds() * 1e9
            << "}" << std::endl;
  return success;
}

extern "C" int sc_main(int argc, char** argv) {
  absl::InitializeLog();
  absl::SetProgramUsageMessage("Usage: coral-sim program.elf [options]");
  auto args = absl::ParseCommandLine(argc, argv);
  if (args.size() == 2 && absl::GetFlag(FLAGS_binary).empty()) {
    absl::SetFlag(&FLAGS_binary, std::string(args[1]));
  } else if (args.size() != 1) {
    LOG(ERROR) << "Usage: coral-sim program.elf [options]";
    return 2;
  }

  // Pass clean arguments to Verilator.
  int v_argc = args.size();
  char** v_argv = &args[0];
  Verilated::commandArgs(v_argc, v_argv);

  if (absl::GetFlag(FLAGS_binary) == "") {
    LOG(ERROR) << "--binary is required!";
    return -1;
  }

  return run(Sysc_tb::get_name(argv[0]), absl::GetFlag(FLAGS_binary),
      absl::GetFlag(FLAGS_cycles), absl::GetFlag(FLAGS_trace),
      absl::GetFlag(FLAGS_debug_axi), absl::GetFlag(FLAGS_instr_trace),
      absl::GetFlag(FLAGS_backdoor_load), absl::GetFlag(FLAGS_dump_memory),
      absl::GetFlag(FLAGS_console_file)) ? 0 : 1;
}
