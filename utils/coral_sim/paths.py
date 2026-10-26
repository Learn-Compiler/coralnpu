"""Source and generated-output locations, independent of the working directory."""
import os
from pathlib import Path
BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
OUT = Path(os.environ.get('CORAL_SIM_OUT', ROOT / 'out/coral-sim')).expanduser().resolve()


def llvm_tool(name, compiler='clang-17'):
    """Prefer the selected compiler version, then normal PATH tool names."""
    import re
    import shutil
    version = re.search(r'-(\d+)$', Path(compiler).name)
    candidates = ([name + '-' + version[1]] if version else []) + [name, name + '-17']
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    raise RuntimeError(name + ' required; install the matching LLVM tools or add them to PATH')
