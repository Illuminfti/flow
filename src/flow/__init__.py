"""flow — a generic dynamic-workflow engine for any agent, any model.

Code owns orchestration; models do bounded leaf work that runs concurrently,
routes by cost, enforces JSON schemas (with one repair turn), and resumes after
a crash. Point any agent at it; configure any model in one YAML file.
"""
from __future__ import annotations

from .backends import BackendError, BackendResponse, register_backend
from .budget import Budget, BudgetExceeded
from .config import ConfigError
from .router import RouterError, choose
from .runtime import Workflow, run_workflow

__version__ = "1.0.1"

__all__ = [
    "Workflow",
    "run_workflow",
    "Budget",
    "BudgetExceeded",
    "RouterError",
    "ConfigError",
    "BackendError",
    "BackendResponse",
    "register_backend",
    "choose",
    "__version__",
]
