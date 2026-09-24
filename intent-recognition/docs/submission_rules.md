# Submission Rules

This document defines the participant submission requirements for the
TechJam Conversational E-Commerce Search Challenge.

## What Teams Must Submit

Each team must submit:

- one Python agent entry file exporting `Agent`
- any required local helper modules
- setup instructions
- a short report describing method, model choice, and limitations
- a disclosure of latency, token usage, and estimated model cost

## Required Interface

Your submission must export:

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        ...

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {
            "message": "Do you have a material preference?",
            "ask_attribute": "material",
            "recommendations": [{"parent_asin": "B000..."}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30}
        }
```

## Allowed Submission Contents

You may include:

- Python source files
- small local config files
- lightweight local assets required by your agent
- dependency manifest and install instructions

## Disallowed Submission Contents

Do not include:

- private evaluation data
- copied organizer-only files
- API keys or secrets
- code that requires privileged host access
- code that modifies evaluator files
- code that depends on undeclared external services for official final scoring

## Model Policy

Teams may prototype with any legally accessible LLM API or local model during
development.

For official final scoring, organizer policy may disable network access.
Therefore:

- your submission must clearly document whether it requires network access
- if your system has an offline fallback, describe it
- if your system cannot run without live credentials, say so explicitly

## Output Rules

Your `respond(...)` output must follow these rules:

- `message` must be a string
- `ask_attribute` must be one allowed attribute or `null`
- `recommendations` must be ordered best to worst
- only the first 10 valid unique `parent_asin` values are scored
- `usage` should report non-negative token counts when available

## Reproducibility Requirements

Your submission package must contain:

- exact Python version requirement if non-default
- dependency installation steps
- one command to run the agent in the official harness
- any non-obvious environment variables

If your code cannot be reproduced from the submitted bundle and instructions,
the organizer may treat the run as invalid.

## Recommended File Layout

```text
submission/
  agent.py
  requirements.txt
  README.md
  src/
```

## Final Notes

- The organizer reserves the right to run your submission under CPU, memory,
  timeout, and network restrictions.
- The organizer will score only the frozen official artifacts and the output
  produced by your submitted code in that environment.
