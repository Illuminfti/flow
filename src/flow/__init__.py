"""flow — a generic dynamic-workflow engine for any agent, any model.

Code owns orchestration; models do bounded leaf work that runs concurrently,
routes by cost, enforces JSON schemas (with one repair turn), and resumes after
a crash. Point any agent at it; configure any model in one YAML file.
"""
from __future__ import annotations

from .backends import BackendError, BackendResponse, register_backend
from .blocks import BlockStore, dedup_report
from .budget import Budget, BudgetExceeded
from .config import ConfigError
from .loop import IterationRecord, LoopLedger, LoopRun, LoopSpec, VerifierIdentityError
from .router import RouterError, choose
from .runtime import ExecutionResult, FailureMode, ParallelError, Workflow, run_workflow
from .tools import ToolDefinition, register_tool

__version__ = "1.3.0"

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
    "FailureMode",
    "ExecutionResult",
    "ParallelError",
    "BlockStore",
    "dedup_report",
    "LoopSpec",
    "LoopRun",
    "IterationRecord",
    "LoopLedger",
    "VerifierIdentityError",
    "register_tool",
    "ToolDefinition",
    "choose",
    "__version__",
]
