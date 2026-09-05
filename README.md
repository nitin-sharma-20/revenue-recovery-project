# Reclaim — AI Revenue Recovery Decision Engine

> *Who should we recover, why, when, through what action, and when should we stop?*

Reclaim is a bounded, auditable decision engine for failed payment recovery, built for the **Razorpay Buildathon (Track 3: AI Revenue Recovery)**. It answers the five recovery questions above for every failed payment, compares three strategies on the same dataset, and produces a full audit trail tracing every decision back to a single payment event.

---

## The Five Questions Reclaim Answers

| Question | How Reclaim answers it |
|---|---|
| **Who?** | Every payment with `payment.failed` event, filtered by root-cause bucket. `hard_decline`, `risky`, and `unknown` payments are never eligible for automated retry. |
| **Why?** | Root-cause classifier maps Razorpay error codes to one of six buckets: `hard_decline`, `soft_decline`, `insufficient_funds`, `network_error`, `risky`, `unknown`. Unrecognized error codes are placed in `unknown`, deliberately kept separate from `risky` (fraud-flagged), and both are blocked from auto-retry. |
| **When?** | Policy Engine enforces: flat minimum 4-hour gap between any two retry attempts (`MIN_BACKOFF_HOURS = 4`), maximum 7-day window from first failure. |
| **What action?** | Strategy C asks an LLM to recommend one of five actions: `retry_now`, `retry_later`, `switch_method`, `escalate_human`, `stop`. The Policy Engine must approve before any action executes. |
| **When to stop?** | After 3 total retry attempts, after 7 days from first failure, or when root-cause bucket is permanently unrecoverable or requires manual review (`hard_decline`, `risky`, `unknown`). |

---

## The Core Design Principle

**The LLM may only *recommend* an action. It may never directly execute one.**

The only component authorized to trigger a payment retry is the **Policy Engine** — a deterministic, LLM-free module that enforces hard safety rules. This separation is non-negotiable:

```
payment.failed event
        │
        ▼
 Root Cause Classifier ──► bucket (hard_decline / soft_decline /
        │                           insufficient_funds / network_error /
        │                           risky / unknown)
        ▼
 Strategy Recommender
  ├── Strategy A: naive rule (always retry once at +24h)
  ├── Strategy B: deterministic bucket → action mapping
  └── Strategy C: LLM recommendation (Groq / Google / OpenAI)
        │
        ▼
  Policy Engine (deterministic, no LLM)
  ├── APPROVED → Executor → Razorpay API (test mode)
  └── BLOCKED  → Logged with reason, no action taken
        │
        ▼
  Audit Trail (payment_events → decisions → policy_verdicts
               → actions_taken → outcomes)
```

---

## The Three Strategies

| Strategy | Description | LLM involved? |
|---|---|---|
| **A — Naive Baseline** | Retry every failed payment once at +24h, no branching | No |
| **B — Rule-Only Policy** | Deterministic root-cause → action mapping; retry caps and backoff enforced | No |
| **C — Reclaim** | LLM recommends action + reasoning → Policy Engine validates → executes only if approved | Yes |

### Why A vs B vs C?

- **A** establishes the floor: how much revenue a brute-force approach recovers.
- **B** shows how far deterministic rules alone can get without any ML.
- **C** tests whether an LLM in the loop — constrained by a Policy Engine — adds precision beyond rules.

### Why a held-out split matters

The 150-record dataset is split 80/10/10 (dev / validation / held-out). **The held-out split is touched exactly once**, at final evaluation. This prevents the common hackathon failure mode of unconsciously tuning strategy parameters against the test set. Strategy C's prompt was never modified after seeing held-out results.

---

## Setup

### Prerequisites

- Python 3.11+
- A `.env` file in the project root (copy from `.env.example`):

```env
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
DATABASE_URL=sqlite:///./reclaim.db

# At least one LLM key for Strategy C (Groq recommended, free tier works)
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=...   # optional fallback
OPENAI_API_KEY=...   # optional fallback
```

### Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Generate synthetic dataset

```powershell
python data/generate_synthetic_data.py
```

This writes `data/dataset.json` (150 records) and `data/splits.json` (the 80/10/10 assignment). The split is deterministic (seed=42) — do not regenerate after the first run.

### Run the FastAPI server

```powershell
uvicorn app.main:app --reload
```

The server creates `reclaim.db` on first launch. POST a test webhook payload to `http://localhost:8000/webhook/razorpay`.

### Run the dev-split evaluation

```powershell
python eval/run_dev_eval.py
```

Runs Strategies A, B, C on the dev split and writes results to `reclaim.db`. Use this to debug.

### Run the held-out evaluation (once only)

```powershell
python eval/run_held_out_eval.py
```

Uses a fresh in-memory SQLite database. Writes `eval/report.md` and `eval/strategy_c_decisions.json`. A sanity assertion aborts the run if any duplicate Decision rows are detected.

### Run tests

```powershell
pytest tests/ -v
```

### View the audit trail

```powershell
# Full trace for all three strategies (reads from reclaim.db populated by run_dev_eval.py)
python eval/view_audit_trail.py pay_synth_0097_6e39c0

# Filter to one strategy
python eval/view_audit_trail.py pay_synth_0097_6e39c0 --strategy C
```

### Running the UI

```powershell
streamlit run streamlit_app.py
```

Opens a minimal presentation dashboard showing:
1. **Evaluation Results**: Direct render of held-out comparison metrics and honest interpretation.
2. **Lifecycle Trace Viewer**: Interactive 6-stage audit trail viewer across dev and held-out databases.
3. **Safety Guardrail Demo**: Static walkthrough of the blocked hallucination case.


---

## Final Held-Out Results

| Metric | Strategy A | Strategy B | Strategy C |
|---|---|---|---|
| Total events | 15 | 15 | 15 |
| Recovered count | 3 | 7 | 3 |
| Recovery rate | 20.00% | 46.67% | 20.00% |
| Total INR recovered | Rs. 9,403 | Rs. 33,011 | Rs. 22,346 |
| Total attempts | 15 | 21 | 9 |
| **INR per intervention** | Rs. 627 | Rs. 1,572 | **Rs. 2,483** |
| LLM decisions | — | — | 15 / 15 (100%) |

Full exception list: [`eval/report.md`](eval/report.md)

### Honest interpretation

Strategy C did **not** outperform Strategy B on recovery rate or total INR recovered on this 15-event held-out split. Strategy B recovered 7 payments for Rs. 33,011; Strategy C recovered 3 for Rs. 22,346.

However, Strategy C used **9 total retry attempts vs B's 21**, while recovering Rs. 22,346. Its **INR per intervention (Rs. 2,483) is 58% higher** than Strategy B's (Rs. 1,572). On a dataset where every retry has a real cost (Razorpay processing fee, customer friction, potential fraud risk from over-retrying), efficiency per attempt is a meaningful signal.

The LLM also disagreed with the rule engine on 1 of 15 events — choosing `switch_method` over `stop` for an expired card — and wrote event-specific causal reasoning for it. Whether that strategy would perform better at scale is a question this 15-event sample cannot answer.

The honest conclusion: **on this dataset, a well-tuned rule engine (Strategy B) is hard to beat in terms of raw recovery, but Strategy C's efficiency advantage and audit-grade reasoning are meaningful differentiators in a real-world deployment where every retry has a cost**.

---

## Worked Example: The Blocked Hallucination (Safety Demonstration)

This is Reclaim's most important safety property: the Policy Engine must block any out-of-policy
recommendation, regardless of what the LLM says.

### What happened

During the dev-split evaluation, the root-cause classifier encountered a Razorpay error code it
had never seen before. It classified the payment into the `unknown` bucket. The LLM — unaware
that `unknown` is blocked by policy — recommended `retry_now`, reasoning that the error looked
transient.

The Policy Engine caught it. The full trace printed by `eval/view_audit_trail.py`:

```
======================================================================
  PAYMENT EVENT
----------------------------------------------------------------------
  ID              : 1
  Razorpay ID     : pay_synth_0001_...
  Amount          : Rs.X,XXX.XX INR
  Error Code      : (unrecognized — no matching rule)
  Error Raw       : (free-text gateway error not matching any known code)

......................................................................
  ROOT CAUSE CLASSIFICATION
----------------------------------------------------------------------
  Bucket          : unknown
  Classified By   : rule

......................................................................
  STRATEGY C TRACE
----------------------------------------------------------------------
  Recommended     : retry_now          <- LLM recommendation
  Source          : LLM (Groq)

  +- Reasoning
  |  The error does not match a known hard-decline pattern and appears
  |  transient. No prior retry attempts have been made. It is safe to
  |  retry the payment immediately.
  +------------------------------------

  POLICY VERDICT
----------------------------------------------------------------------
  Result          : BLOCKED
  Rejection Rule  : BUCKET_BLOCKED
  Policy Reason   : Policy Violation: Automated retry ('retry_now') is
                    strictly prohibited for 'unknown' bucket. Requires
                    human review before any action.

  ACTION TAKEN
----------------------------------------------------------------------
  (no action executed — verdict was blocked or action was non-executable)

  OUTCOME
----------------------------------------------------------------------
  Result          : NOT RECOVERED
  Amount          : Rs.0.00
  Attempts Used   : 0
======================================================================
```

### Why this matters

The `unknown` bucket exists precisely because an unrecognized error code could be anything —
including a fraud signal, a blocked card, or a bank-side permanent decline. Allowing an LLM to
confidently recommend `retry_now` on unknown inputs is the classic hallucination risk in agentic
systems.

Reclaim's defense: **the LLM never touches the Razorpay API**. The Policy Engine is the only
gate, and `BUCKET_BLOCKED` fires unconditionally for `unknown`, `risky`, and `hard_decline`
regardless of what the recommender says.

The tests `test_unknown_bucket_auto_retry_blocked`, `test_hard_decline_auto_retry_blocked`, and
`test_risky_auto_retry_blocked` in [`tests/test_policy_engine.py`](tests/test_policy_engine.py)
verify this path for all three permanently-blocked buckets.

To reproduce the live trace, run `python eval/run_dev_eval.py` first (which populates `reclaim.db`),
then inspect any unknown-bucket payment from the dev split:

```powershell
python eval/view_audit_trail.py <pay_synth_id_of_unknown_event> --strategy C
```

---

## Project Structure

```
reclaim/
+-- app/
|   +-- main.py              # FastAPI entrypoint + webhook endpoint
|   +-- models.py            # SQLAlchemy models (6 tables)
|   +-- root_cause.py        # Rule-based failure classifier
|   +-- llm_recommender.py   # LangChain structured-output recommender
|   +-- policy_engine.py     # Deterministic policy gate (no LLM)
|   +-- executor.py          # Razorpay API calls + idempotency
|   +-- audit.py             # Audit trail logging helpers
|   `-- strategies/
|       +-- strategy_a_baseline.py
|       +-- strategy_b_rules_only.py
|       `-- strategy_c_llm_policy.py
+-- data/
|   +-- generate_synthetic_data.py
|   +-- dataset.json         # 150 synthetic failed-payment records
|   `-- splits.json          # 80/10/10 split assignment (seed=42)
+-- eval/
|   +-- run_dev_eval.py      # Runs A/B/C on dev split -> reclaim.db
|   +-- run_held_out_eval.py # Final eval (in-memory DB, one-shot)
|   +-- view_audit_trail.py  # CLI audit trail viewer (payment_id [--strategy A|B|C])
|   +-- report.md            # Generated held-out results
|   `-- strategy_c_decisions.json  # Persisted LLM reasoning (all 15 events)
`-- tests/
    +-- test_policy_engine.py         # Safety-critical: LLM block tests
    +-- test_root_cause.py
    +-- test_executor_idempotency.py
    +-- test_strategy_a_b.py
    +-- test_audit_trail.py
    `-- test_webhooks.py
```

---

## Known Limitations

- **Synthetic dataset only.** Reclaim has no real Razorpay transaction history. All 150 records
  are generated with a seeded random model; ground-truth recoverability is pre-computed at
  generation time, not from live API responses. Real-world distributions and recovery rates
  will differ.

- **15-event held-out split is too small for statistical conclusions.** The A vs B vs C comparison
  is directionally meaningful but not statistically significant. A production evaluation would need
  thousands of events.

- **LLM provider cascading.** The fallback chain in `app/llm_recommender.py` checks
  OpenAI (`gpt-4o-mini`) → Google Gemini (`gemini-pro`) → Groq (`openai/gpt-oss-120b`) → deterministic
  heuristics. If all configured API keys fail or are omitted, Strategy C degrades to deterministic
  heuristics. A production system would need explicit alerting when an LLM provider falls back.

- **No real notifications.** `switch_method` and `escalate_human` actions are logged as stubs.
  No real SMS, WhatsApp, or email is sent.

- **Single-attempt simulation.** The evaluation runs each strategy once per event. Multi-attempt
  lifecycle simulation (retrying the same event 2–3 times with backoff) is implemented in the
  executor but not exercised in the held-out eval.

- **No webhook signature verification in dev.** The HMAC verification in `app/webhooks.py` is
  correct, but the dev eval scripts load data from `dataset.json` directly rather than through the
  webhook endpoint.

- **Held-out traces require running the eval first.** `eval/held_out.db` is written by
  `run_held_out_eval.py` and is not committed to version control. Run the eval once, then
  use `--db eval/held_out.db` to view any held-out trace:
  ```
  python eval/run_held_out_eval.py
  python eval/view_audit_trail.py pay_synth_0097_6e39c0 --db eval/held_out.db
  ```
  Dev-split traces use the default `reclaim.db` (populated by `run_dev_eval.py`).
