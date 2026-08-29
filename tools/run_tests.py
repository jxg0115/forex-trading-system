# -*- coding: utf-8 -*-
"""一键回归测试（D10）：python -m pytest tests -q（runtime python-embed 已装 pytest）。

用法：python tools/run_tests.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "runtime" / "python-embed" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)


def main() -> int:
    cmd = [str(PY), "-m", "pytest", "tests", "-q"]
    print(">", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    print(f"\n一键回归测试退出码：{result.returncode}（0=全绿）")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())