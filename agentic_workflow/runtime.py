"""Product-facing runtime using the external US search tool."""
from .agent import Agent
from mvp.server import AgentRuntime

SEARCH_SCENARIOS = (
    {'id': 'precise', 'label': 'Find a dress',
     'title': 'I need a blue cotton dress.',
     'followups': ['Show me more dresses.', 'Compare #1 and #2.', 'Finalize my selection.']},
    {'id': 'explore', 'label': 'Explore shoes',
     'title': 'I am browsing running shoes.',
     'followups': ['Prefer lightweight mesh.', 'Compare #1 and #2.']},
    {'id': 'override', 'label': 'Change my mind',
     'title': 'I need a black dress.',
     'followups': ['Blue instead.', 'Switch to running shoes.']},
)


def create_runtime(**agent_options):
    return AgentRuntime(Agent(trace_enabled=True, **agent_options),
                        orchestration_mode='adaptive', scenarios=SEARCH_SCENARIOS)
