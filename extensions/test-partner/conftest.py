"""pytest 根配置：把仓库根塞进 sys.path，让测试能 `from server import ...`。"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
