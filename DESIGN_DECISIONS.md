# DESIGN_DECISIONS.md — Reclaim Rebuild

Pre-build confirmation document. Every item was answered explicitly and reviewed before
any code was written. Items marked [CORRECTED] were revised after the initial Part A
review. Status: Approved and locked. Any implementation divergence must be flagged here
with a dated note, not silently absorbed into the code.

---

## Part A — Confirmed Design Decisions

### 1. Pipeline Structure

There is exactly **ONE** `evaluate_and_execute()` function. All conditions route through
it. Strategies/conditions are *recommenders only* — they return
`(recommended_action, reasoning, source_label)` and nothing else.

**File:** `app/pipeline.py`
**Signature:**
```python
def evaluate_and_execute(
    event: PaymentEvent,
    recommender: Callable[[PaymentEvent, Session], tuple[str, str, str]],
    condition_label: str,   # "flag_off" / "flag_on" / "baseline_a"
    run_id: str,
    db: Session,
    current_time: Optional[datetime] = None,
) -> PipelineResult
```

The Policy Engine call and the Executor call live ONLY inside `evaluate_and_execute()`.
Any recommender file that calls `PolicyEngine.evaluate()` directly is an architecture
violation. If that temptation arises during implementation, stop and flag it here.

---

### 2. What Varies Between Conditions

This is an **ABLATION**, not two separate strategy implementations.

- **`flag_off`:** For `unknown`-bucket events, recommender goes straight to heuristic
  (`escalate_human`). No LLM call.
- **`flag_on`:** For `unknown`-bucket events, recommender calls LLM triage first.
- **All other five buckets:** IDENTICAL deterministic heuristic logic for both conditions.

The toggle is a single boolean `llm_triage_enabled: bool` passed to the recommender
factory. Strategy A (naive baseline) remains as a separate comparator but is NOT part
of the ablation pair.

---

### 3. Instrument Type

Every synthetic payment is tagged at generation time with `instrument_type`:

- `"mandate"` — UPI Autopay or NACH mandate-based recurring charge.
  Automated retries (`retry_now`, `retry_later`) are VALID.
- `"one_off_card"` — One-off card charge.
  Automated retries are INVALID (no pre-authorised mandate to re-debit).

**Rule encoded in the Policy Engine (not in any recommender):**
> If `instrument_type == "one_off_card"` and `recommended_action in ["retry_now",
> "retry_later"]`, verdict is `INSTRUMENT_INELIGIBLE` (blocked). Only valid automated
> actions: `switch_method`, `escalate_human`, `stop`.

New rejection rule: `RejectionRuleEnum.INSTRUMENT_INELIGIBLE`
Checked BEFORE any retry-cap or backoff check.

**KNOWN LIMITATION:** The Policy Engine blocks all automated retries on `one_off_card`
uniformly, including `network_error`. In reality, a transient network glitch where the
customer is still present in an active checkout session is often eligible for in-session
resubmission without mandate consent. This model overstates the no-retry constraint for
`network_error`/`one_off_card` events.
*Disclosure locations: Policy Engine code comment, this document, README.md.*

---

### 4. LLM Call Placement

LLM triage calls happen **EXCLUSIVELY** in an async background worker. Never
synchronously on any webhook response or evaluation orchestration thread.

During batch evaluation: each `unknown`-bucket event is submitted to a
`concurrent.futures.ThreadPoolExecutor` (bounded to max 4 workers) with a per-call
timeout of 30 seconds. This was the root cause of the previous test suite failures
(no concurrency cap -> all events fired simultaneously -> provider rejection).

**Rate limit / timeout handling:**
- Each LLM call: 3 attempts, exponential backoff (10s, 30s, 60s).
- `RateLimitError` triggers backoff retry; other exceptions log at WARNING and fall through.
- After all providers exhausted: `LLMProviderExhausted` exception raised (see item 10).

---

### 5. Test Harness Independence

LLM client is **dependency-injected**. `evaluate_and_execute()` accepts:
`llm_client: LLMClientProtocol`

Both `LangChainLLMClient` and `FakeLLMClient` implement this protocol.

- **Default for all tests:** `FakeLLMClient` — deterministic, no network calls.
- **Live-API tests:** marked `@pytest.mark.live_llm`, skipped unless `RUN_LIVE_LLM_TESTS=1`.
- A `conftest.py` autouse fixture enforces this skip.
- **Live-API test files:**
  - `tests/integration/test_llm_provider_openai_live.py`
  - `tests/integration/test_llm_provider_groq_live.py`

A normal `pytest` run with no environment variable will **NEVER** make an API call.

---

### 6. Database Structure

**ONE persistent SQLite file: `reclaim.db` in the project root.**
No in-memory + backup pattern. No separate dev/held-out files.

**New columns:**

`payment_events`:
- `instrument_type VARCHAR(30)` — `"mandate"` or `"one_off_card"`
- `dataset_version VARCHAR(20)` — e.g. `"v2"`

`decisions`:
- `run_id VARCHAR(36)` — UUID4 per evaluation run
- `condition VARCHAR(20)` — `"flag_off"` / `"flag_on"` / `"baseline_a"`
- `recommendation_source VARCHAR(30)` — `"llm_openai"`, `"heuristic"`, `"llm_fallback_all_failed"`

`outcomes`:
- `run_id VARCHAR(36)`
- `condition VARCHAR(20)`
- **NO `fold_id`** (see item 12 correction)

`evaluation_runs` (new table):
- `run_id`, `git_commit`, `config_json`, `seed`, `dataset_version`, `created_at`

---

### 7. Cross-Run / Cross-Condition Isolation

Composite uniqueness constraints:

```sql
-- decisions: one per (event, condition, run)
UNIQUE (event_id, condition, run_id)

-- outcomes: one per (event, condition, run)
UNIQUE (event_id, condition, run_id)

-- policy_verdicts: one per decision (preserved)
UNIQUE (decision_id)

-- actions_taken: globally unique idempotency key (preserved)
-- key derived from (event_id, condition, run_id, attempt_number)
UNIQUE (idempotency_key)
```

`PolicyEngine.evaluate_and_record()` queries prior retry attempts filtered by
`(event_id, condition, run_id)` — not just `event_id`.

---

### 8. Ground Truth Independence

`data/ground_truth.py` is the **SOLE** module generating synthetic recoverability
outcomes. **Zero imports** from `app/strategies/`, `app/llm_recommender.py`,
`app/policy_engine.py`, or `app/executor.py`.

Import direction: `app/` -> `data/ground_truth.py` (read-only lookup). Never the
reverse. Enforced by a CI test that fails if any `app/` import appears in
`data/ground_truth.py`.

---

### 9. Reason / Exception Categorization

Exactly **ONE** function:

```python
# app/audit.py
def classify_outcome_reason(
    outcome: Outcome,
    verdict: Optional[PolicyVerdict],
    event: PaymentEvent,
    root_cause_bucket: str,
) -> str:
```

Returns one of:
- `"policy_blocked"` — `verdict.allowed=False` (generic)
- `"instrument_ineligible"` — verdict blocked on `INSTRUMENT_INELIGIBLE` specifically
- `"attempted_and_failed"` — verdict allowed, action taken, not recovered
- `"not_attempted_stop"` — action was `stop` or `escalate_human`, no attempt made
- `"recovered"` — `outcome.recovered is True`

Used by: `eval/run_evaluation.py`, `eval/view_audit_trail.py`, any report generator.
Verified in CI: only one definition of `def classify_outcome_reason` in the codebase.

---

### 10. LLM Provider Failure Handling

**No bare `except: pass` anywhere.** Exact failure path when every provider fails:

1. OpenAI call fails -> `WARNING: openai_failed event_id={id} err={repr(e)}` -> fall
   through to Groq.
2. Groq fails after 3 retries -> `ERROR: groq_exhausted event_id={id} attempts=3
   final_err={repr(e)}` -> fall through.
3. All providers exhausted -> `LLMProviderExhausted` raised (named exception, not silent).
4. Caller in `pipeline.py` catches `LLMProviderExhausted`, logs
   `CRITICAL: llm_all_providers_failed event_id={id}`, creates Decision with
   `recommended_action="escalate_human"` and
   `recommendation_source="llm_fallback_all_failed"`, proceeds through normal Policy
   Engine path.
5. Audit trail marks `recommendation_source="llm_fallback_all_failed"` so the evaluator
   can distinguish these from genuine `escalate_human` recommendations.

---

### 11. Sampling Design [CORRECTED — dataset size scaled to 500 (FINAL)]

Stratified sampling with **HARD MINIMUM COUNTS**.

**Total pool: 500 records (Final scale).**
**`unknown` bucket hard minimum: 65 records** (enforced before proportional fill).

Target distribution:

| Bucket | Target count | Fraction |
|---|---|---|
| `soft_decline` | 150 | 30% |
| `insufficient_funds` | 120 | 24% |
| `network_error` | 65 | 13% |
| `hard_decline` | 50 | 10% |
| `risky` | 50 | 10% |
| `unknown` | **65 (hard min)** | 13% |

**FINAL DECISION ON DATASET SIZE:** The dataset is locked at 500 records. A pilot run with the real LLM against the 30 TRUE eligible `unknown` records (mandate + <= 7 days old) yielded 19 discordant pairs (63.3% discordance rate vs baseline). Since 19 is slightly below the asymptotic threshold of 25, we will keep the 500-record scale and simply use the **exact** binomial McNemar's test (`exact=True`) which is mathematically sound for small discordant counts. This avoids unnecessary regeneration and test-run churn.

Generator fills `unknown` to minimum first, then distributes remainder proportionally.

---

### 12. Evaluation Design [CORRECTED — execution model separated from statistics]

**ORIGINAL (WRONG):** Re-execute pipeline per bootstrap draw, writing DB rows per fold.
This would violate `UNIQUE(event_id, condition, run_id)` on duplicate event appearances
within a draw, and invoke the LLM up to 500x per event.

**CORRECTED:**

**Execution pass (once per event per condition):**
- `evaluate_and_execute()` runs exactly once per event per condition. No `fold_id`.
- `outcomes` has one row per `(event_id, condition, run_id)`. Only DB-writing step.

**Statistics pass (purely in-memory, no DB writes, no re-execution):**
```python
paired_table = [(flag_off_recovered[i], flag_on_recovered[i]) for i in events]

# McNemar's — once, on real paired contingency table
result = scipy.stats.mcnemar(contingency_table)

# Bootstrap CI — resample indices only, no pipeline re-execution
for _ in range(B=500):
    indices = rng.choice(n, size=n, replace=True)
    resample = [paired_table[i] for i in indices]
    # compute rate difference on resample
```

`fold_id` is **NOT** a column on `outcomes`.
Unique constraint: `UNIQUE(event_id, condition, run_id)`.

---

### 13. Statistical Test

**McNemar's test** (`scipy.stats.mcnemar`).

Justification: outcomes are paired (same event evaluated by both conditions), binary
(recovered or not), and conditions are not independent. McNemar's is correct for paired
binary data. A naive difference-in-proportions test assumes independence and is incorrect.

**Exact vs Approximate:** Based on the Phase 3 pilot yielding 19 discordant pairs (which is < 25), we will explicitly use the **exact binomial test** (`exact=True` in `scipy.stats.mcnemar`) rather than the chi-squared approximation (`exact=False`). The exact test is mathematically accurate for small numbers of discordant pairs and avoids the need to artificially inflate the dataset size just to reach the approximation threshold.

Report output:
- Point estimate: recovery rate difference (`flag_on` minus `flag_off`)
- McNemar's exact test p-value (no chi-squared statistic since we use the exact binomial method)
- 95% bootstrap confidence interval on the difference (percentile method, B=500)

---

### 14. Reproducibility Logging

Every evaluation run writes to **two locations**:

1. `evaluation_runs` table in `reclaim.db`:
   `run_id`, `git_commit`, `config_json`, `seed`, `dataset_version`, `created_at`

2. `eval/runs/<run_id>.json` — same fields, survives DB reset.

Both written within the same evaluation run function. If the file write fails, the DB
record still exists.

---

### 15. Honest Scope of "Recovery"

Disclosure in **TWO places** (so it cannot be lost):

1. **Module docstring at top of `data/ground_truth.py`:**
   > DISCLOSURE: Recovery outcomes in this system are determined by the synthetic
   > ground-truth model defined in this module, NOT by live bank/issuer authorization
   > behavior. Razorpay test-mode API calls are made to verify executor plumbing, but
   > the test-mode API does not perform real authorization. Reported INR recovered
   > figures reflect synthetic model outcomes only and should not be interpreted as
   > real-world authorization results.

2. **`README.md`** — dedicated "Scope and Honest Limitations" section, verbatim
   restatement, positioned BEFORE the results table.

---

### 16. Generation-Time Policy-Window Validation

`data/validate_dataset.py` runs immediately after dataset generation and prints:
- Fraction of payments within the 7-day policy age window (deliberately generated between 0 and 10 days so ~70% are eligible; tests the `AGE_CUTOFF_EXCEEDED` policy rule).
- Fraction eligible for automated retry by instrument type (mandate vs. one_off_card)
- Count per bucket vs. target — flags if any bucket is more than 10% off target
- Count of `unknown` records — exits code 1 if < 65

Exits code 1 on any constraint violation.
Runs automatically at the top of `eval/run_evaluation.py` before any strategy executes.

---

## Phase Gate Rules (Non-Negotiable)

Each phase boundary requires **real output** — not a summary claiming success:

- **End of Phase 1:** Full `validate_dataset.py` printed output + literal bucket counts.
- **End of Phase 2:** Schema DDL with composite constraints visible + instrument-eligibility test passing against a real fixture.
- **End of Phase 3:** Real trace of `LLMProviderExhausted -> escalate_human` with `recommendation_source="llm_fallback_all_failed"`, triggered genuinely.
- **End of Phase 4:** McNemar's output and bootstrap CI from a real trial run — numbers confirmed sane before full 500-record pass.
- **End of Phase 5:** Real `evaluation_runs` row and `eval/runs/<run_id>.json` file contents, confirming reproducibility logging fires.
