"""Self-contained fake-catalog demo for the conversational shopping Agent.

Examples:
    python -m mvp.demo --case dress_en
    python -m mvp.demo
    python -m mvp.demo --web --port 8000
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from shopping_agent.model_provider import create_model_provider

from .audit import verify_audit
from .server import AgentRuntime, ApiError, create_server


DEMO_CATALOG = Path(__file__).resolve().parent / "demo_data" / "catalog.jsonl"
DEMO_CASES: dict[str, dict[str, Any]] = {
    "dress_en": {
        "label": "Blue cotton dress under $50",
        "prompts": (
            "I need a blue cotton dress under $50.",
            "No polyester, and suitable for summer.",
            "Compare #1 and #2",
            "Finalize my selection",
        ),
    },
    "running_shoes": {
        "label": "English: breathable running shoes under $100",
        "prompts": (
            "I need breathable running shoes under $100.",
            "Prefer lightweight mesh, and no leather.",
            "Compare #1 and #2.",
            "Finalize my selection.",
        ),
    },
    "commuter_bag": {
        "label": "English: black commuter laptop backpack",
        "prompts": (
            "I need a black backpack under $70 for commuting.",
            "It must fit a laptop, and avoid leather.",
            "Compare the first and second.",
            "Finalize my selection.",
        ),
    },
}
DEMO_WEB_SCENARIOS = tuple(
    {
        "id": name,
        "label": case["label"],
        "title": case["prompts"][0],
        "followups": list(case["prompts"][1:]),
    }
    for name, case in DEMO_CASES.items()
)


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "price unknown"


def _values(mapping: dict[str, Any]) -> str:
    if not mapping:
        return "—"
    return ", ".join(f"{key}={value}" for key, value in mapping.items())


class TerminalDemo:
    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self.session_id = ""
        self.start_session()

    def start_session(self) -> None:
        session = self.runtime.new_session()
        self.session_id = session["session_id"]
        print(f"\n[NEW SESSION] {self.session_id} · {session['data_disclosure']}")

    def send(self, message: str) -> dict[str, Any] | None:
        print(f"\nYOU > {message}")
        try:
            result = self.runtime.chat(self.session_id, message)
        except ApiError as exc:
            print(f"AGENT ERROR [{exc.code}] > {exc}")
            return None
        self.render(result)
        return result

    @staticmethod
    def render(result: dict[str, Any]) -> None:
        assistant = result.get("assistant", {})
        print(f"AGENT > {assistant.get('message', '')}")
        receipt = result.get("receipt", {})
        print(f"  hard: {_values(receipt.get('hard', {}))}")
        print(f"  soft: {_values(receipt.get('soft', {}))}")
        print(f"  excluded: {_values(receipt.get('excluded', {}))}")
        for product in result.get("products", ()):
            rating = product.get("rating")
            rating_text = f"★ {rating}" if rating is not None else "rating unknown"
            print(
                f"\n  #{product['rank']} {product['title']}\n"
                f"     {_money(product.get('price'))} · {rating_text} · ASIN {product['parent_asin']}"
            )
            advice = product.get("advice") or {}
            for row in (advice.get("pros") or ())[:2]:
                print(f"     + {row['text']} [{row['source']}: {row['evidence']}]")
            for row in (advice.get("cons") or ())[:2]:
                print(f"     - {row['text']} [{row['source']}: {row['evidence']}]")
        selection = result.get("selection_state", {})
        print(
            f"\n  selection: {selection.get('status', 'draft')} · "
            f"{selection.get('selection_count', 0)}/{selection.get('max_selections', 3)}"
        )
        usage = assistant.get("usage") or {}
        print(
            "  model tokens: "
            f"{int(usage.get('prompt_tokens') or 0) + int(usage.get('completion_tokens') or 0)}"
        )

    def handoff(self) -> None:
        handoff = self.runtime.selection_handoff(self.session_id)
        summary = {
            "status": handoff["status"],
            "requirements": handoff["requirements"],
            "decision": handoff["decision"],
            "products": [
                {
                    "parent_asin": row["parent_asin"],
                    "title": row["title"],
                    "price": row["price"],
                    "advice": row["advice"],
                }
                for row in handoff["selected_products"]
            ],
        }
        print(json.dumps(summary, ensure_ascii=True, indent=2))

    def audit(self) -> None:
        audit = self.runtime.audit(self.session_id)
        errors = verify_audit(audit)
        print(
            json.dumps(
                {
                    "turn_count": audit["turn_count"],
                    "head_sha256": audit["integrity"]["head"],
                    "verification_errors": errors,
                },
                indent=2,
            )
        )

    def run_case(self, name: str, *, reset: bool = True) -> None:
        case = DEMO_CASES[name]
        if reset:
            self.start_session()
        print(f"\n=== CASE: {case['label']} ===")
        for prompt in case["prompts"]:
            if self.send(prompt) is None:
                break


HELP = """
Commands:
  /cases                 list scripted cases
  /run dress_en          reset and run a complete case
  /select 1 2            shortlist displayed ranks
  /compare 1 2           compare and shortlist ranks
  /reject 2              record negative feedback
  /finalize              finalize the current shortlist
  /handoff               print the bounded B/D handoff
  /audit                 verify the local audit chain
  /new                   start a clean session
  /help                  show this help
  /quit                  exit

Any other text is sent to the shopping Agent as a normal conversation turn.
""".strip()


def run_terminal(runtime: AgentRuntime, scripted_case: str | None = None) -> None:
    demo = TerminalDemo(runtime)
    if scripted_case:
        demo.run_case(scripted_case, reset=False)
        demo.handoff()
        demo.audit()
        return

    print("\nSHOW ME YOUR AGENT · FAKE CATALOG TERMINAL DEMO")
    print(HELP)
    while True:
        try:
            text = input("\nshop> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        if not text:
            continue
        command, _, argument = text.partition(" ")
        command = command.casefold()
        if command in {"/quit", "/exit"}:
            print("Bye.")
            return
        if command == "/help":
            print(HELP)
        elif command == "/cases":
            for name, case in DEMO_CASES.items():
                print(f"  {name}: {case['label']}")
        elif command == "/run":
            if argument not in DEMO_CASES:
                print(f"Unknown case. Choose: {', '.join(DEMO_CASES)}")
            else:
                demo.run_case(argument)
        elif command == "/new":
            demo.start_session()
        elif command == "/handoff":
            demo.handoff()
        elif command == "/audit":
            demo.audit()
        elif command == "/select":
            if not argument:
                print("Usage: /select 1 2")
            else:
                demo.send("Select " + " and ".join(f"#{value}" for value in argument.split()))
        elif command == "/compare":
            if not argument:
                print("Usage: /compare 1 2")
            else:
                demo.send("Compare " + " and ".join(f"#{value}" for value in argument.split()))
        elif command == "/reject":
            if not argument:
                print("Usage: /reject 2")
            else:
                demo.send("I don't like " + " and ".join(f"#{value}" for value in argument.split()))
        elif command == "/finalize":
            demo.send("Finalize my selection")
        else:
            demo.send(text)


def run_web(runtime: AgentRuntime, host: str, port: int) -> None:
    server = create_server(runtime, host, port)
    print(f"Show Me Your Agent fake-catalog web demo: http://{host}:{port}")
    print(f"Catalog: {DEMO_CATALOG} ({runtime.agent.catalog_size} products)")
    print(runtime.health()["data_disclosure"])
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", action="store_true", help="launch the browser UI instead of the terminal")
    parser.add_argument("--case", choices=tuple(DEMO_CASES), help="run one scripted terminal case and exit")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model-provider", choices=("off", "local", "deepseek"), default="off")
    parser.add_argument("--model")
    parser.add_argument("--model-base-url")
    parser.add_argument("--model-timeout", type=float, default=20.0)
    args = parser.parse_args()

    if args.list_cases:
        for name, case in DEMO_CASES.items():
            print(f"{name}: {case['label']}")
        return
    if args.web and args.case:
        parser.error("--web and --case cannot be used together")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        provider = create_model_provider(
            args.model_provider,
            base_url=args.model_base_url,
            model=args.model,
            timeout_seconds=args.model_timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))
    runtime = AgentRuntime.create(
        DEMO_CATALOG,
        orchestration_mode="adaptive",
        provider=provider,
        scenarios=DEMO_WEB_SCENARIOS,
    )
    if args.web:
        run_web(runtime, args.host, args.port)
    else:
        run_terminal(runtime, args.case)


if __name__ == "__main__":
    main()
