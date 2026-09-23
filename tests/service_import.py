"""Import one server's modules in isolation for tests.

Every server under servers/ ships its own top-level `tools` package (and
`mcp_server` module). Once one is imported, Python reuses it for every later
`import tools`, so a test run that touches two servers would silently get the
wrong one. This helper drops the cached copies and puts the requested
server's directory first on sys.path before importing.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ROOT / "servers"
_SHADOWED = ("tools", "mcp_server", "main")


def load_service_module(service: str, module: str) -> ModuleType:
    for name in list(sys.modules):
        if name in _SHADOWED or name.startswith("tools."):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not p.startswith(str(SERVERS))]
    sys.path.insert(0, str(SERVERS / service))
    return importlib.import_module(module)
