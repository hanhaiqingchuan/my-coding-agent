"""The reproducible evaluation harness for this agent.

The harness drives the shipped headless CLI as a subprocess. It contains no second
planning loop and never imports the Agent Loop, the Run Coordinator or tool internals.
"""

from __future__ import annotations

from coding_agent.evaluation.manifest import (
    CATEGORIES,
    SCHEMA_VERSION,
    EvaluationManifest,
    ManifestError,
    TaskSpec,
    validate_manifest,
)
from coding_agent.evaluation.report import (
    ReportError,
    RunResult,
    Summary,
    compute_artifact_correct,
    compute_strict_success,
    summarize,
    summarize_campaign,
)
from coding_agent.evaluation.runner import (
    CampaignError,
    CampaignPlan,
    CampaignResult,
    run_campaign,
    verify_task_setup,
)

__all__ = [
    "CATEGORIES",
    "CampaignError",
    "CampaignPlan",
    "CampaignResult",
    "EvaluationManifest",
    "ManifestError",
    "ReportError",
    "RunResult",
    "SCHEMA_VERSION",
    "Summary",
    "TaskSpec",
    "compute_artifact_correct",
    "compute_strict_success",
    "run_campaign",
    "summarize",
    "summarize_campaign",
    "validate_manifest",
    "verify_task_setup",
]
