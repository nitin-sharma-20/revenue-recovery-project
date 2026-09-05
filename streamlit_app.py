"""
streamlit_app.py — Reclaim AI Revenue Recovery Decision Engine Presentation UI

Presentation layer only. Reads existing data from SQLite databases and report files.
Does NOT modify or re-run any evaluations or database state.
"""

from pathlib import Path
import re
import pandas as pd
import streamlit as st

# Direct import of trace fetching logic (no duplicate query logic)
from eval.view_audit_trail import (
    fetch_trace_data,
    get_all_payment_ids,
    DEFAULT_DB_PATH,
    HELD_OUT_DB_PATH,
    fmt_ts
)

REPORT_PATH = Path(__file__).parent / "eval" / "report.md"
README_PATH = Path(__file__).parent / "README.md"

st.set_page_config(
    page_title="Reclaim — AI Revenue Recovery Decision Engine",
    page_icon="🛡️",
    layout="wide"
)

# --- Custom Premium UI Injection ---
st.markdown("""
<style>
/* Modern typography */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
    color: #F5F7FA;
}

/* Typography Hierarchy */
h1 {
    font-weight: 700 !important;
    letter-spacing: -1px;
    color: #F5F7FA !important;
    margin-bottom: 0.5rem !important;
}

h2 {
    font-weight: 600 !important;
    letter-spacing: -0.5px;
    color: #F5F7FA !important;
    border-bottom: 1px solid #20283A !important;
    padding-bottom: 12px;
    margin-top: 2rem !important;
    margin-bottom: 1.5rem !important;
}

h3 {
    font-weight: 600 !important;
    color: #F5F7FA !important;
}

p, div {
    font-weight: 400;
    line-height: 1.6;
    color: #9AA4B2;
}

strong {
    color: #F5F7FA !important;
    font-weight: 600 !important;
}

/* Fintech Cards & Surfaces (Expanders) */
div[data-testid="stExpander"] {
    background-color: #121826 !important;
    border: 1px solid #20283A !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    margin-bottom: 16px;
    transition: all 0.2s ease;
}

div[data-testid="stExpander"]:hover {
    border-color: #635BFF !important;
}

/* Financial Figures / Metrics */
[data-testid="stMetricValue"] {
    color: #F5F7FA !important;
    font-weight: 700 !important;
    font-size: 2rem !important;
    letter-spacing: -0.5px;
}

[data-testid="stMetricLabel"] {
    color: #667085 !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-size: 0.85rem !important;
}

/* Buttons */
.stButton button {
    background-color: #121826 !important;
    border: 1px solid #20283A !important;
    color: #F5F7FA !important;
    border-radius: 6px !important;
    font-weight: 500 !important;
    transition: all 0.2s ease;
}

.stButton button:hover {
    border-color: #635BFF !important;
    color: #F5F7FA !important;
}

.stButton button:active {
    background-color: #635BFF !important;
    border-color: #635BFF !important;
}

/* Tabs / Navigation */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background-color: transparent;
    border-bottom: 1px solid #20283A;
    padding-bottom: 0;
}

.stTabs [data-baseweb="tab"] {
    background-color: transparent;
    border: none;
    border-radius: 0;
    padding: 12px 16px !important;
    color: #9AA4B2 !important;
    font-weight: 500 !important;
    transition: color 0.2s ease;
}

.stTabs [aria-selected="true"] {
    color: #F5F7FA !important;
    border-bottom: 2px solid #635BFF !important;
    background-color: transparent !important;
}

/* Dataframes / Tables */
[data-testid="stTable"] table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border: 1px solid #20283A;
    border-radius: 8px;
    overflow: hidden;
}

[data-testid="stTable"] th {
    background-color: #0D111C !important;
    color: #667085 !important;
    font-weight: 500 !important;
    border-bottom: 1px solid #20283A !important;
    text-transform: uppercase;
    font-size: 0.8rem;
    letter-spacing: 0.5px;
    padding: 12px 16px;
}

[data-testid="stTable"] td {
    background-color: #121826 !important;
    color: #F5F7FA !important;
    border-bottom: 1px solid #20283A !important;
    padding: 12px 16px;
}

/* Success / Error Texts (Overriding standard colors for alerts) */
.stSuccess {
    background-color: rgba(34, 197, 94, 0.1) !important;
    color: #22C55E !important;
    border: 1px solid rgba(34, 197, 94, 0.2) !important;
}

.stError, .stWarning {
    background-color: rgba(239, 68, 68, 0.1) !important;
    color: #EF4444 !important;
    border: 1px solid rgba(239, 68, 68, 0.2) !important;
}

.stInfo {
    background-color: rgba(99, 91, 255, 0.1) !important;
    color: #7C75FF !important;
    border: 1px solid rgba(99, 91, 255, 0.2) !important;
}

hr {
    border-color: #20283A !important;
}

</style>
""", unsafe_allow_html=True)

# Header & Tagline
st.title("🛡️ Reclaim — AI Revenue Recovery Decision Engine")
st.markdown(
    "> *Who should we recover, why, when, through what action, and when should we stop?*"
)
st.caption(
    "A bounded, auditable decision engine where AI recommends recovery actions, "
    "but a deterministic Policy Engine is the sole execution authority."
)
st.divider()

tab_results, tab_trace, tab_safety = st.tabs([
    "📊 Evaluation Results",
    "🔍 Lifecycle Trace Viewer",
    "🛡️ Safety Guardrail Demo"
])


# ==============================================================================
# TAB 1: RESULTS
# ==============================================================================
with tab_results:
    st.header("Comparative Strategy Evaluation")
    st.markdown(
        "Performance comparison of **Strategy A (Naive Baseline)**, "
        "**Strategy B (Rules-Only)**, and **Strategy C (Reclaim AI)** on the held-out split."
    )

    if not REPORT_PATH.exists():
        st.warning(
            "⚠️ `eval/report.md` not found. "
            "Run `python eval/run_held_out_eval.py` first to generate evaluation metrics."
        )
    else:
        # Parse metrics table directly from eval/report.md
        with open(REPORT_PATH, "r", encoding="utf-8") as f:
            report_text = f.read()

        # Extract markdown table under "## 1. Recovery Metrics"
        table_match = re.search(
            r"## 1\. Recovery Metrics\s*\n\n(\|[\s\S]*?)(?=\n\n|\n###|\n##|$)",
            report_text
        )

        if table_match:
            table_md = table_match.group(1).strip()
            lines = [l.strip() for l in table_md.split("\n") if l.strip()]
            if len(lines) >= 3:
                headers = [h.strip() for h in lines[0].split("|")[1:-1]]
                rows = []
                for row_line in lines[2:]:
                    cells = [c.strip() for c in row_line.split("|")[1:-1]]
                    if len(cells) == len(headers):
                        rows.append(cells)
                df = pd.DataFrame(rows, columns=headers)
                st.table(df)
            else:
                st.markdown(table_md)
        else:
            st.info("Metrics table raw view:")
            st.markdown(report_text[:1000])

        st.subheader("Strategy C Source Breakdown")
        st.write("• **LLM Decisions:** 15 (100.0%)")
        st.write("• **Fallback Heuristic:** 0 (0.0%)")

        st.divider()

        st.subheader("Honest Interpretation")
        st.markdown(
            """
Strategy C did **not** outperform Strategy B on recovery rate or total INR recovered on this 15-event held-out split. Strategy B recovered 7 payments for Rs. 33,011; Strategy C recovered 3 for Rs. 22,346.

However, Strategy C used **9 total retry attempts vs B's 21**, while recovering Rs. 22,346. Its **INR per intervention (Rs. 2,483) is 58% higher** than Strategy B's (Rs. 1,572). On a dataset where every retry has a real cost (Razorpay processing fee, customer friction, potential fraud risk from over-retrying), efficiency per attempt is a meaningful signal.

The LLM also disagreed with the rule engine on 1 of 15 events — choosing `switch_method` over `stop` for an expired card — and wrote event-specific causal reasoning for it. Whether that strategy would perform better at scale is a question this 15-event sample cannot answer.

The honest conclusion: **on this dataset, a well-tuned rule engine (Strategy B) is hard to beat in terms of raw recovery, but Strategy C's efficiency advantage and audit-grade reasoning are meaningful differentiators in a real-world deployment where every retry has a cost**.
            """
        )


# ==============================================================================
# TAB 2: TRACE VIEWER
# ==============================================================================
with tab_trace:
    st.header("End-to-End Lifecycle Trace Viewer")
    st.markdown(
        "Trace every decision, policy verdict, action, and recovery outcome "
        "back to the originating payment event."
    )

    col_db, col_filter = st.columns([2, 1])

    with col_db:
        db_choice = st.selectbox(
            "Select Database Split",
            options=["Held-Out Split (eval/held_out.db)", "Dev Split (reclaim.db)"],
            index=0
        )
        selected_db_path = HELD_OUT_DB_PATH if "held_out" in db_choice else DEFAULT_DB_PATH

    with col_filter:
        strategy_filter = st.selectbox(
            "Filter Strategy",
            options=["All Strategies (A, B, C)", "Strategy A", "Strategy B", "Strategy C"],
            index=0
        )
        strat_key = None if "All" in strategy_filter else strategy_filter.split()[-1]

    # Check DB existence
    if not selected_db_path.exists():
        st.warning(
            f"⚠️ Database not found at `{selected_db_path}`.\n\n"
            + ("Run `python eval/run_held_out_eval.py` first to populate held-out traces."
               if "held_out" in str(selected_db_path)
               else "Run `python eval/run_dev_eval.py` first to populate dev traces.")
        )
    else:
        # Load available IDs for convenience
        available_ids = get_all_payment_ids(selected_db_path)

        col_input, col_dropdown = st.columns([2, 2])
        with col_input:
            user_payment_id = st.text_input(
                "Enter Razorpay Payment ID",
                value="pay_synth_0097_6e39c0" if "pay_synth_0097_6e39c0" in available_ids else (available_ids[0] if available_ids else "")
            )
        with col_dropdown:
            if available_ids:
                selected_sample = st.selectbox(
                    "Or pick an existing Payment ID from database",
                    options=["-- Select --"] + available_ids,
                    index=0
                )
                if selected_sample != "-- Select --":
                    user_payment_id = selected_sample

        if user_payment_id:
            trace_data = fetch_trace_data(user_payment_id.strip(), selected_db_path)

            if trace_data.get("error"):
                st.error(f"❌ {trace_data['error']}")
            else:
                event = trace_data["event"]
                cls = trace_data["classification"]
                strategies = trace_data["strategies"]

                st.subheader(f"Payment Event: `{event['razorpay_payment_id']}`")

                # Event Overview Card
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Amount", f"₹{event['amount']:,.2f} {event['currency']}")
                c2.metric("Split Bucket", event['split_bucket'] or "—")
                c3.metric("Customer ID", event['customer_id'] or "—")
                c4.metric("Failed At", fmt_ts(event['created_at']))

                with st.expander("📌 Raw Event & Root Cause Classification", expanded=True):
                    col_ev, col_cls = st.columns(2)
                    with col_ev:
                        st.markdown("**Error Details**")
                        st.write(f"• **Error Code:** `{event['failure_reason_code'] or '—'}`")
                        st.write(f"• **Raw Error:** {event['failure_reason_raw'] or '—'}")
                        st.write(f"• **Order ID:** `{event['order_id'] or '—'}`")
                    with col_cls:
                        st.markdown("**Root Cause Classification**")
                        if cls:
                            st.write(f"• **Taxonomy Bucket:** `{cls['bucket']}`")
                            st.write(f"• **Classified By:** `{cls['classified_by']}`")
                            st.write(f"• **Classified At:** {fmt_ts(cls['created_at'])}")
                        else:
                            st.write("(No classification found)")

                # Display Strategies
                strats_to_display = ["A", "B", "C"] if strat_key is None else [strat_key]

                for s in strats_to_display:
                    s_data = strategies.get(s)
                    if not s_data:
                        st.info(f"No execution data for Strategy {s}")
                        continue

                    strat_titles = {
                        "A": "Strategy A (Naive Baseline — Blind Retry at +24h)",
                        "B": "Strategy B (Deterministic Rules-Only Policy)",
                        "C": "Strategy C (Reclaim — LLM Recommender + Policy Engine)"
                    }

                    with st.expander(f"🔹 {strat_titles.get(s, f'Strategy {s}')}", expanded=True):
                        col_dec, col_verd = st.columns(2)

                        with col_dec:
                            st.markdown("#### 1. Decision Recommendation")
                            st.write(f"• **Recommended Action:** `{s_data['recommended_action']}`")
                            st.write(f"• **Source:** `{s_data['source']}`")
                            st.write(f"• **Decided At:** {fmt_ts(s_data['decided_at'])}")
                            st.info(f"**Reasoning:** {s_data['reasoning']}")

                        with col_verd:
                            st.markdown("#### 2. Deterministic Policy Verdict")
                            verd = s_data.get("verdict")
                            if verd:
                                if verd["allowed"]:
                                    st.success(f"**Verdict:** ✅ APPROVED\n\n{verd['reason']}")
                                else:
                                    st.error(
                                        f"**Verdict:** 🚫 BLOCKED\n\n"
                                        f"• **Rule:** `{verd['rejection_rule'] or 'BLOCKED'}`\n\n"
                                        f"• **Reason:** {verd['reason']}"
                                    )
                                st.caption(f"Verdict timestamp: {fmt_ts(verd['created_at'])}")
                            else:
                                st.write("Policy Engine was not invoked.")

                        st.markdown("---")
                        col_act, col_out = st.columns(2)

                        with col_act:
                            st.markdown("#### 3. Action Executed")
                            actions = s_data.get("actions", [])
                            if not actions:
                                st.write("*(No action executed — verdict was blocked or non-executable)*")
                            else:
                                for a in actions:
                                    st.write(f"• **Action Type:** `{a['action_type']}`")
                                    st.write(f"• **Idempotency Key:** `{a['idempotency_key']}`")
                                    st.write(f"• **Executed At:** {fmt_ts(a['executed_at'])}")
                                    if a['razorpay_response']:
                                        st.caption(f"API Response: {a['razorpay_response']}")

                        with col_out:
                            st.markdown("#### 4. Recovery Outcome")
                            outcome = s_data.get("outcome")
                            if outcome:
                                if outcome["recovered"]:
                                    st.success(
                                        f"**Result:** ✅ RECOVERED\n\n"
                                        f"• **Amount Recovered:** ₹{outcome['amount_recovered']:,.2f}\n\n"
                                        f"• **Attempts Used:** {outcome['attempts_used']}"
                                    )
                                else:
                                    st.warning(
                                        f"**Result:** ❌ NOT RECOVERED\n\n"
                                        f"• **Amount Recovered:** ₹0.00\n\n"
                                        f"• **Attempts Used:** {outcome['attempts_used']}"
                                    )
                            else:
                                st.write("No outcome recorded.")


# ==============================================================================
# TAB 3: SAFETY DEMO
# ==============================================================================
with tab_safety:
    st.header("Worked Example: The Blocked Hallucination")
    st.markdown(
        "Demonstrating Reclaim's core safety invariant: **The LLM never touches the execution API. "
        "The deterministic Policy Engine blocks out-of-policy recommendations unconditionally.**"
    )

    st.subheader("What Happened")
    st.write(
        "During evaluation, the classifier encountered an unmapped error code and classified it into "
        "the **`unknown`** bucket. The LLM analyzed the error description, reasoned that the failure appeared "
        "transient, and recommended an immediate retry (`retry_now`)."
    )
    st.write(
        "Because `unknown`, `risky`, and `hard_decline` buckets are strictly prohibited from automated retries, "
        "the deterministic Policy Engine intercepted and blocked the recommendation."
    )

    st.subheader("Full Lifecycle Trace")

    st.code(
        """======================================================================
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
======================================================================""",
        language="text"
    )

    st.subheader("Why This Matters")
    st.markdown(
        """
- **Hallucination Containment:** Unrecognized errors could be silent bank security flags, fraud anomalies, or unrecoverable accounts. Allowing an AI model to guess and retry blindly risks merchant fees, gateway penalties, and user friction.
- **Zero Direct LLM Execution:** The LLM's recommendation is treated as untrusted input until validated by the Policy Engine.
- **Definitive Audit Record:** Even blocked decisions are recorded with full rationale, rejection category (`BUCKET_BLOCKED`), and zero side-effects.
        """
    )
