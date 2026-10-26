"""Source and generated-output locations, independent of the working directory."""
import os
from pathlib import Path
BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
OUT = Path(os.environ.get('CORAL_SIM_OUT', ROOT / 'out/coral-sim')).expanduser().resolve()


def find_clang(requested=None):
    """Honor an explicit compiler; otherwise use clang or a versioned PATH binary."""
    import re
    import shutil
    requested = requested or os.environ.get('CORAL_SIM_CLANG')
    if requested:
        compiler = shutil.which(requested)
        if not compiler:
            raise RuntimeError('compiler not found: ' + requested + '; pass --clang /path/to/clang')
        return str(Path(compiler).absolute())
    compiler = shutil.which('clang')
    if compiler:
        return str(Path(compiler).absolute())
    candidates = []
    for directory in os.get_exec_path():
        try:
            for path in Path(directory).glob('clang-[0-9]*'):
                match = re.fullmatch(r'clang-(\d+)', path.name)
                if match and int(match[1]) >= 17 and shutil.which(str(path)):
                    candidates.append((int(match[1]), str(path)))
        except OSError:
            continue
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    raise RuntimeError('Clang required; install clang/clang-XX or pass --clang /path/to/clang')


def llvm_tool(name, compiler=None):
    """Find companion tools beside the real compiler, then by version and PATH."""
    import re
    import shutil
    compiler = Path(find_clang(compiler))
    resolved = compiler.resolve()
    version = re.search(r'-(\d+)$', compiler.name)
    if not version:
        version = re.search(r'/llvm-(\d+)(?:/|$)', str(resolved))
    candidates = []
    if version:
        candidates += [str(compiler.with_name(name + '-' + version[1])),
                       str(resolved.with_name(name + '-' + version[1]))]
    # An unversioned /usr/bin/clang often points into /usr/lib/llvm-XX/bin.
    # Prefer that installation's companions over another default LLVM version.
    candidates.append(str(resolved.with_name(name)))
    if version:
        candidates.append(name + '-' + version[1])
    candidates += [str(compiler.with_name(name)), name]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return str(Path(path).absolute())
    raise RuntimeError(name + ' required for ' + str(compiler) +
                       '; install the matching LLVM tools/LLD or add them to PATH')
