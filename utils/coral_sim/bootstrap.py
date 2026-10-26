"""Locate Bazel, or download the repository-pinned Linux binary without sudo."""
import datetime as dt
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo
from paths import ROOT, OUT


def find_bazel(requested=None):
    requested = requested or os.environ.get('BAZEL')
    if requested:
        executable = shutil.which(requested)
        if not executable:
            raise RuntimeError('Bazel executable not found: ' + requested +
                               '; omit --bazel to locate/download it automatically')
        return str(Path(executable).absolute())
    for name in ('bazelisk', 'bazel'):
        executable = shutil.which(name)
        if executable:
            return str(Path(executable).absolute())
    version = (ROOT / '.bazelversion').read_text().strip()
    for path in (Path.home() / '.local/bin/bazelisk', Path.home() / '.local/bin/bazel',
                 ROOT.parent / '.tools' / ('bazel-' + version + '-linux-' + machine()),
                 OUT / 'tools' / ('bazel-' + version + '-linux-' + machine())):
        executable = shutil.which(str(path))
        if executable:
            return str(Path(executable).absolute())
    return None


def machine():
    architecture = platform.machine().lower()
    return {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(architecture, architecture)


def download_bazel(seconds=180):
    if platform.system() != 'Linux' or machine() not in ('x86_64', 'arm64'):
        raise RuntimeError('automatic Bazel download supports Linux x86_64/arm64; pass --bazel')
    version = (ROOT / '.bazelversion').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise RuntimeError('unsupported .bazelversion for direct download; install Bazelisk')
    name = 'bazel-' + version + '-linux-' + machine()
    destination = OUT / 'tools' / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = 'https://releases.bazel.build/' + version + '/release/' + name
    deadline = dt.datetime.now(ZoneInfo('Asia/Shanghai')) + dt.timedelta(seconds=seconds)
    end = time.monotonic() + seconds
    print('Bazel download deadline: ' + deadline.isoformat() + f' ({seconds}s)', flush=True)
    print('Download: ' + url, flush=True)

    def remaining():
        left = end - time.monotonic()
        if left <= 0:
            raise TimeoutError('Bazel download deadline exceeded')
        return min(left, 30)

    descriptor, temporary_name = tempfile.mkstemp(prefix='.' + name + '-', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with urllib.request.urlopen(url + '.sha256', timeout=remaining()) as response:
            tokens = response.read(4096).decode('ascii').split()
            expected = tokens[0].lower() if tokens else ''
        if not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise RuntimeError('invalid SHA256 response for Bazel')
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=remaining()) as response, temporary.open('wb') as output:
            while True:
                remaining()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError('Bazel SHA256 verification failed; downloaded binary was discarded')
        temporary.chmod(0o755)
        temporary.replace(destination)
        print('Bazel SHA256 verified: ' + str(destination), flush=True)
        return str(destination)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError('Bazel download failed: ' + str(error) +
                           '; set HTTPS_PROXY or use --bazel /existing/path/bazel') from error
    finally:
        temporary.unlink(missing_ok=True)
