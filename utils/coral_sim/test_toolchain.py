#!/usr/bin/env python3
"""Tool discovery on fresh machines, without requiring every LLVM version installed."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from paths import find_clang, llvm_tool


class ToolchainDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.environment = patch.dict(os.environ, {'PATH': str(self.bin), 'CORAL_SIM_CLANG': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        path.chmod(0o755)
        return path

    def test_unversioned_clang_uses_its_installation(self):
        installation = self.root / 'lib/llvm-21/bin'
        real = self.executable(installation / 'clang')
        (self.bin / 'clang').symlink_to(real)
        # Default tools may come from an older installation.
        for name in ('clang++', 'ld.lld', 'llvm-ar', 'llvm-objcopy'):
            self.executable(self.bin / name)
            expected = self.executable(installation / name)
            self.assertEqual(llvm_tool(name, find_clang()), str(expected))
        self.assertEqual(find_clang(), str(self.bin / 'clang'))

    def test_only_versioned_compilers_in_path(self):
        self.executable(self.bin / 'clang-17')
        selected = self.executable(self.bin / 'clang-21')
        self.executable(self.bin / 'clang-scan-deps-99')
        self.assertEqual(find_clang(), str(selected))
        companion = self.executable(self.bin / 'ld.lld-21')
        self.assertEqual(llvm_tool('ld.lld', str(selected)), str(companion))

    def test_explicit_compiler_and_environment_take_priority(self):
        self.executable(self.bin / 'clang')
        selected = self.executable(self.bin / 'clang-19')
        self.assertEqual(find_clang('clang-19'), str(selected))
        with patch.dict(os.environ, {'CORAL_SIM_CLANG': 'clang-19'}):
            self.assertEqual(find_clang(), str(selected))
        with self.assertRaisesRegex(RuntimeError, 'compiler not found: clang-18'):
            find_clang('clang-18')

    def test_missing_linker_reports_selected_compiler(self):
        compiler = self.executable(self.bin / 'clang-21')
        with self.assertRaisesRegex(RuntimeError, 'ld.lld required for .*clang-21'):
            llvm_tool('ld.lld', str(compiler))


if __name__ == '__main__':
    unittest.main()
