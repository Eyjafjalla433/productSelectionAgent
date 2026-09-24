"""Readable, recordable terminal demo on the real catalog; no hidden target input."""
import argparse
import json
import time
from pathlib import Path

from .common import ROOT, write_json

SCENARIOS = {
    "override": ["I need a black dress under $50.", "Blue instead.", "No budget limit.", "Switch to shoes, not leather."],
    "clarify": ["Help me find something.", "A black dress under $50.", "Casual, cotton, and machine washable."],
    "browse": ["I'm looking for Basketball Men, but I'm still exploring.", "For that, what matters is: Drawstring closure; High quality mesh for maximum breathability to keep you cool.", "Prefer blue and under $60."],
    "dynamic4b": [
        "I'm looking for Basketball Men, but I'm still exploring.",
        "I want breathable mesh.",
        "Prefer blue and under $60.",
        "Those options are not quite right yet.",
        "I want a drawstring closure.",
    ],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="override")
    parser.add_argument("--orchestration-mode", choices=("adaptive", "score_compat"), default="score_compat")
    parser.add_argument("--pause", action="store_true", help="Press Enter between turns when recording")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to wait before each visible turn")
    parser.add_argument("--ids-only", action="store_true", help="Hide catalog titles in public recordings")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from agent import Agent
    print("Shopping Copilot | submitted CPU agent | real catalog | no GPU/API calls", flush=True)
    agent = Agent(orchestration_mode=args.orchestration_mode, trace_enabled=True)
    session = "demo_" + args.scenario
    agent.reset(session, {"preference_tags": ["comfort"], "purchase_frequency": "demo", "average_prior_rating": None,
                          "rating_style": "unspecified", "summary": "Demonstration profile: comfort is a weak preference."})
    for turn, message in enumerate(SCENARIOS[args.scenario], 1):
        if args.pause:
            input("\nPress Enter for next turn...")
        elif args.delay:
            time.sleep(args.delay)
        response = agent.respond(session, message, turn, 10)
        events = {event["stage"]: event["output"] for event in agent.trace[-1]["events"]}
        state = events["3_state"]
        print(f"\nTURN {turn} USER: {message}")
        print("STATE:", json.dumps({"version": state["state_version"], "hard": state["hard_constraints"],
              "soft": state["soft_preferences"], "exclude": state["exclusions"]}, ensure_ascii=False))
        print("4A:", events["4A_pre_policy"]["action"], events["4A_pre_policy"]["reason"])
        retrieval = events.get("2_retrieval_retry", events.get("2_retrieval"))
        if retrieval:
            print("RETRIEVAL:", retrieval["stats"], "returned:", retrieval["returned_count"])
            print("4B:", events["4B_post_policy"]["action"], events["4B_post_policy"]["reason"])
        else:
            print("RETRIEVAL: skipped by 4A")
        print("AGENT:", response["message"])
        print("ASK_ATTRIBUTE:", response["ask_attribute"], "| recommendations:", len(response["recommendations"]))
        for position, row in enumerate(response["recommendations"][:3], 1):
            product = agent.retriever.products[row["parent_asin"]]
            # Product metadata is not an invented natural-language explanation.
            if args.ids_only:
                print(f"  {position}. {row['parent_asin']} | catalog price={product.get('price')}")
            else:
                print(f"  {position}. {row['parent_asin']} | {str(product.get('title', ''))[:100]} | catalog price={product.get('price')}")
        print("FEEDBACK: version", events["3_feedback"]["state_version"], "actual questions", events["3_feedback"]["suggestions"]["clarification_count"])
    output = args.output or ROOT / "results" / f"demo-{args.scenario}.json"
    write_json(output, {"scenario": args.scenario, "orchestration_mode": args.orchestration_mode,
                        "source": "fixed visible demo messages; no target passed to Agent", "trace": agent.trace, "errors": agent.errors})
    print(f"\nSaved: {output}")
    if agent.errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
