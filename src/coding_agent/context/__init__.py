"""Context budgeting, projection, pruning, and compaction contracts."""

from coding_agent.context.builder import (
    CompactionCandidate,
    CompactionPlan,
    CompactionRequired,
    ContextBuilder,
    ContextBuildResult,
    ContextOverflow,
    ContextRequest,
    ContextView,
    ReadyContext,
)
from coding_agent.context.compactor import CompactionResult, Compactor, CompressionError
from coding_agent.context.estimator import ESTIMATOR_ID, estimate_input_tokens

__all__ = [
    "ESTIMATOR_ID",
    "CompactionCandidate",
    "CompactionPlan",
    "CompactionRequired",
    "CompactionResult",
    "Compactor",
    "CompressionError",
    "ContextBuildResult",
    "ContextBuilder",
    "ContextOverflow",
    "ContextRequest",
    "ContextView",
    "ReadyContext",
    "estimate_input_tokens",
]
