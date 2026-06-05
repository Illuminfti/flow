"""Single source of truth for engine magic numbers."""
from __future__ import annotations

# Budget
MAX_CALLS_BACKSTOP = 1000          # runaway-fanout backstop
DEFAULT_EST_OUT_TOKENS = 400       # output-token estimate when caller gives none

# Concurrency
LEAF_WORKERS_CAP = 32              # min(CAP, cpu*RATIO) leaf-pool default
LEAF_WORKERS_RATIO = 4
ORCH_WORKERS = 256                 # orchestration pool (cheap, mostly-blocked threads)

# Backends
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096

# Retry (P1): transient-error backoff
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_MS = 500
RETRY_CAP_MS = 30000
