"""
eval/run_evaluation.py — Single evaluation harness for all conditions.

Execution order (non-negotiable per DESIGN_DECISIONS.md):
  1. validate_dataset.py — exits code 1 if dataset is skewed/unusable
  2. Single execution pass:
     - baseline_a condition (comparator, not part of ablation)
     - flag_off condition  (ablation arm: no LLM triage for unknown bucket)
     - flag_on  condition  (ablation arm: LLM triage for unknown bucket)
     All three run through the SAME evaluate_and_execute() in pipeline.py.
  3. Statistics pass (in-memory only — NO DB writes, NO re-execution):
     - Build paired_table from outcomes
     - McNemar's test (scipy.stats.mcnemar) on real paired contingency table
     - Bootstrap CI (B=500 index resamples) on the same paired_table
  4. Write reproducibility record to evaluation_runs table + eval/runs/<run_id>.json
  5. Generate eval/report.md

DESIGN_DECISIONS.md item 12: The pipeline executes ONCE per event per condition.
Bootstrap resamples INDICES from the outcome array — it never re-executes the pipeline.

Phase 4 stub — implement in Phase 4.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

EVAL_RUNS_DIR = Path(__file__).parent / "runs"
REPORT_PATH = Path(__file__).parent / "report.md"
N_BOOTSTRAP = 500
SEED = 42


def run_dataset_validation() -> None:
    """
    Runs data/validate_dataset.py and aborts if it returns non-zero.
    Called before any strategy executes.

    Phase 1 stub (validation script) + Phase 4 stub (this caller).
    """
    raise NotImplementedError("run_dataset_validation: Phase 4 stub")


def run_single_execution_pass(db, run_id: str, llm_client=None) -> dict:
    """
    Executes all three conditions (baseline_a, flag_off, flag_on) exactly once
    per event through evaluate_and_execute() in pipeline.py.

    Returns dict of paired outcomes keyed by event_id:
        {event_id: {"baseline_a": bool, "flag_off": bool, "flag_on": bool}}

    Phase 4 stub.
    """
    raise NotImplementedError("run_single_execution_pass: Phase 4 stub")


def run_statistics_pass(paired_outcomes: dict, seed: int = SEED) -> dict:
    """
    Purely in-memory statistics. No DB writes. No pipeline re-execution.

    Computes:
      - Point estimate: flag_on recovery rate minus flag_off recovery rate
      - McNemar's test: chi-squared statistic + exact p-value
      - Bootstrap CI: 95% percentile CI over B=500 index resamples

    Returns dict with all statistical outputs.

    DESIGN_DECISIONS.md item 12: bootstrap resamples indices from the paired_outcomes
    array — it never calls evaluate_and_execute() again.

    Phase 4 stub.
    """
    raise NotImplementedError("run_statistics_pass: Phase 4 stub")


def log_run(run_id: str, config: dict) -> None:
    """
    Writes reproducibility record to:
      1. evaluation_runs table in reclaim.db
      2. eval/runs/<run_id>.json

    Config should contain: llm_triage_enabled, model_name, seed, dataset_version,
    n_bootstrap, git_commit.

    DESIGN_DECISIONS.md item 14.
    Phase 4 stub.
    """
    raise NotImplementedError("log_run: Phase 4 stub")


def get_git_commit() -> str:
    """Returns current git commit hash, or 'unknown' if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def generate_report(paired_outcomes: dict, stats: dict, run_id: str) -> None:
    """
    Generates eval/report.md containing:
      - Per-condition recovery metrics table
      - McNemar's test result with p-value
      - 95% bootstrap CI
      - Exception list using classify_outcome_reason() from app.audit
      - Honest "recovery scope" disclosure (DESIGN_DECISIONS.md item 15)

    Phase 4/5 stub.
    """
    raise NotImplementedError("generate_report: Phase 4/5 stub")


def main():
    run_id = str(uuid.uuid4())
    git_commit = get_git_commit()

    logging.basicConfig(level=logging.INFO)
    logger.info("=" * 70)
    logger.info("Reclaim Evaluation — run_id=%s", run_id)
    logger.info("=" * 70)

    # Phase 4 stub — full implementation in Phase 4
    raise NotImplementedError("main(): Phase 4 stub — implement in Phase 4")


if __name__ == "__main__":
    main()
    run_evaluation(args.split, args.fake_llm)
