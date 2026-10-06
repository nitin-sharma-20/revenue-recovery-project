"""
app/recommenders/__init__.py

Exports the two recommender factories used by eval/run_evaluation.py:

    make_baseline_a_recommender()  -> RecommenderFn
    make_ablation_recommender(llm_triage_enabled: bool, llm_client: LLMClientProtocol)
                                   -> RecommenderFn

Each factory returns a callable with the signature:
    (event: PaymentEvent, db: Session) -> (action: str, reasoning: str, source: str)

That callable is injected into evaluate_and_execute() in pipeline.py.
NOTHING in this package calls PolicyEngine or Executor directly.
"""

# Phase 2/3 stub — factories implemented in baseline_a.py, rule_based.py, llm_triage.py
