import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from app.models import PaymentEvent
from app.recommenders.llm_triage import LangChainLLMClient
from app.recommenders.protocol import LLMProviderExhausted
from app.recommenders.rule_based import rule_based_recommend

def run_pilot():
    load_dotenv()
    
    dataset_path = Path("data/dataset.json")
    with open(dataset_path, "r", encoding="utf-8") as f:
        records = json.load(f)
        
    # Find TRUE eligible unknown records:
    # 1. unknown bucket
    # 2. mandate instrument
    # 3. <= 7 days old
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    
    eligible_records = []
    for r in records:
        if r["bucket"] != "unknown" or r["instrument_type"] != "mandate":
            continue
        created_at = datetime.fromisoformat(r["created_at"])
        if created_at >= cutoff:
            eligible_records.append(r)
    
    print(f"Found {len(eligible_records)} TRUE eligible unknown records.")
    
    llm_client = LangChainLLMClient(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        groq_api_key=os.getenv("GROQ_API_KEY")
    )
    
    discordant_pairs = 0
    results = []
    
    print("Running LLM pilot sequentially (1 worker) with delays to respect Groq OTPM limits...\n")
    
    for idx, record in enumerate(eligible_records, 1):
        # 1. Deterministic heuristic (flag_off)
        action_off, reasoning_off = rule_based_recommend("unknown", record["amount"], 0)
        
        # 2. Real LLM triage (flag_on)
        try:
            # We slow down to avoid hitting the 1000 OTPM limit constantly
            if idx > 1:
                time.sleep(3) 

            action_on, reasoning_on, source = llm_client.recommend(
                root_cause_bucket="unknown",
                amount=record["amount"],
                error_code=record["failure_reason_code"],
                error_description=record["failure_reason_raw"],
                previous_attempts=0,
                hours_since_failure=24.0 # rough estimate for pilot
            )
        except LLMProviderExhausted as e:
            action_on = "escalate_human"
            reasoning_on = "Fallback: All configured LLM providers failed or timed out."
            source = "llm_fallback_all_failed"
        except Exception as e:
            action_on = "escalate_human"
            reasoning_on = f"Unexpected error: {e}"
            source = "error"
            
        is_discordant = action_off != action_on
        if is_discordant:
            discordant_pairs += 1
            
        res = {
            "id": record["id"],
            "action_off": action_off,
            "action_on": action_on,
            "reasoning_on": reasoning_on,
            "source": source,
            "error_msg": record["failure_reason_raw"]
        }
        results.append(res)
        
        # Print raw per-record output as requested
        status_str = "SUCCESS" if source.startswith("llm_") else "FALLBACK"
        print(f"Record {res['id']:3d} | {status_str:8s} | action_off: {action_off:15s} | action_on: {action_on:15s} | Discordant: {is_discordant}")

    print(f"\n--- Pilot Results ---")
    print(f"Total TRUE eligible: {len(eligible_records)}")
    print(f"Discordant pairs: {discordant_pairs}")
    
    print("\n--- Real LLM Outputs ---")
    valid_results = [r for r in results if r["source"].startswith("llm_")]
    
    if not valid_results:
        print("No successful LLM calls.")
    
    for i, res in enumerate(valid_results[:5]): # show up to 5 real examples
        print(f"\nExample {i+1} (Action: {res['action_on']}, Source: {res['source']})")
        print(f"Error Context: {res['error_msg']}")
        print(f"Reasoning: {res['reasoning_on']}")

if __name__ == "__main__":
    run_pilot()
