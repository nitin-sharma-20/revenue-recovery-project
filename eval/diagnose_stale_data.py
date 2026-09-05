"""
Final diagnostic: simulate fallback-only Strategy C on held-out split.
This tells us exactly what numbers the fallback produces, so we can compare
to the report and confirm whether the 'LLM-fixed' run was actually still fallback.
"""
import json, sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
os.environ['OPENAI_API_KEY'] = ''
os.environ['GOOGLE_API_KEY'] = ''
os.environ['GROQ_API_KEY'] = ''

from app.llm_recommender import recommend_action_heuristics
from data.generate_synthetic_data import get_ground_truth_outcome

with open('data/dataset.json', encoding='utf-8') as f:
    records = json.load(f)
with open('data/splits.json', encoding='utf-8') as f:
    splits = json.load(f)

held_ids = set(splits['held_out'])
held = [r for r in records if r['razorpay_payment_id'] in held_ids]

print(f"Held-out count: {len(held)}")
print()

total_recovered = 0
total_amount = 0.0
total_attempts = 0

print(f"{'Payment ID':<32} {'Bucket':<22} {'Fallback Action':<18} {'Rec?':<6} {'Amount INR'}")
print("-" * 95)

for r in held:
    bucket = r['expected_root_cause']
    rec = recommend_action_heuristics(
        root_cause_bucket=bucket,
        amount=r['amount'],
        error_code=r['failure_reason_code'],
        error_description=r['failure_reason_raw'],
        previous_attempts=0,
        hours_since_failure=720.0   # 30 days old
    )
    action = rec.action.value
    recovered, amount_rec, attempts = get_ground_truth_outcome(r['razorpay_payment_id'], action)
    total_recovered += int(recovered)
    total_amount += amount_rec
    total_attempts += attempts
    print(f"{r['razorpay_payment_id']:<32} {bucket:<22} {action:<18} {'YES' if recovered else 'NO':<6} {amount_rec:>12,.2f}")

print("-" * 95)
print(f"FALLBACK-ONLY TOTALS: recovered={total_recovered}, amount={total_amount:,.2f}, attempts={total_attempts}")
print()
print("CURRENT report.md Strategy C: recovered=3, amount=22346.01, attempts=9")
print()
if total_recovered == 3 and abs(total_amount - 22346.01) < 1:
    print(">>> CONFIRMED: fallback totals MATCH report.md Strategy C.")
    print("    Both prior runs were using fallback_heuristic (LLM never actually fired).")
    print("    The 'LLM-fixed' run silently fell back to heuristics on all 15 events.")
else:
    print(">>> fallback totals do NOT match — the current report.md may already have real LLM numbers.")
    print(f"    Expected if LLM active: different from fallback total {total_amount:,.2f}")
