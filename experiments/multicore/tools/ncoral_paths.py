"""Repository-relative source paths and optional external artifact/cache root."""
import os
from pathlib import Path

UPSTREAM = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ.get("NCORAL_WORK_DIR", str(Path(__file__).resolve().parent))).expanduser().resolve()
