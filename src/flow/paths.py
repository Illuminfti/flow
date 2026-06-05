"""Path anchoring — XDG-compliant, no environment coupling.

Run data lives under ``config.engine.data_dir`` if set, else
``$XDG_DATA_HOME/flow`` (default ``~/.local/share/flow``), overridable
with ``$FLOW_DATA_DIR``.
"""
from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    env = os.environ.get("FLOW_DATA_DIR")
    if env:
        root = Path(env)
    else:
        try:
            from . import config
            cfg_dir = (config.get().get("engine") or {}).get("data_dir")
        except Exception:
            cfg_dir = None
        if cfg_dir:
            root = Path(cfg_dir)
        else:
            xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            root = Path(xdg) / "flow"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_dir_for(run_id: str) -> Path:
    return data_root() / "runs" / run_id
