"""flow — a generic dynamic-workflow engine for any agent, any model.

Code owns orchestration; models do bounded leaf work that runs concurrently,
routes by cost, enforces JSON schemas (with one repair turn), and resumes after
a crash. v2 adds the bounded loop envelope: iterate-to-goal with acceptance
gates, an identity-distinct verifier, stall detection, and a handoff report.
Point any agent at it; configure any model in one YAML file.
"""
from __future__ import annotations

from .backends import BackendError, BackendResponse, register_backend
from .blocks import BlockStore, dedup_report
from .budget import Budget, BudgetExceeded
from .config import ConfigError
from .gates import GateResult, GateRunner, GoalContract, default_goal_contract
from .handoff import HandoffReport
from .loop import IterationRecord, LoopLedger, LoopRun, LoopSpec, VerifierIdentityError
from .router import RouterError, choose
from .runtime import ExecutionResult, FailureMode, ParallelError, Workflow, run_workflow
from .signatures import FailureSignature, FailureSignatureRegistry, RepairRouter
from .tools import ToolDefinition, register_tool

__version__ = "3.0.0"

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
    "GateResult",
    "GateRunner",
    "GoalContract",
    "default_goal_contract",
    "HandoffReport",
    "FailureSignature",
    "FailureSignatureRegistry",
    "RepairRouter",
    "register_tool",
    "ToolDefinition",
    "choose",
    "__version__",
]
