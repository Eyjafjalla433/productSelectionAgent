# Shopping Copilot | TikTok TechJam 2026 Track 4

Shopping Copilot is a stateful conversational search agent for a frozen catalog
of 50,000 products. This walkthrough shows the submitted CPU pipeline:

User → Intent → State → Pre-policy → Retrieval → Ranking/Post-policy → Response
→ State feedback.

The demo covers asking for a missing category, retaining requirements across
turns, replacing a color, clearing a budget, switching product categories, and
asking for clarification after inspecting candidates.

Official public development results: Hit@10 98.5% (197/200), MRR 0.888375,
MTTC 3.205, Efficiency 0.779500, evaluator TechnicalScore 0.914913.
These are development-set results, not private-test results or overall judging scores.

Runtime: Python standard library + SQLite FTS5 on CPU. No GPU, Dense embeddings,
Qwen/LLM inference, hosted API or credentials are required. Model tokens and
API fees are zero; hardware/electricity costs are not estimated.

Source, setup and reproduction:
https://github.com/Zhang-Jingzhi/Shopping-Copilot-AI-Conversational-Search-and-Recommendations

Official participant kit:
https://github.com/TechJam2026/techjam-conversational-search/releases/tag/participant-kit

Data attribution: the organizer's frozen catalog is derived from Amazon Reviews
2023, published by UCSD McAuley Lab: https://amazon-reviews-2023.github.io/

Limitations include rule-based English parsing, lexical rather than verified
variant-level checks, a fixed question budget and no persistent profile learning.
Category checks can admit false positives, including dress shirts for a dress
request. The multi-turn state demonstration does not prove perfect relevance.
The component-4 baseline has slightly lower MRR with the same MTTC; the full
integration is slightly higher on the evaluator composite. We report the public
development-set limitation in our technical report.
