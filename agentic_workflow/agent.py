"""Public workflow entry. Default search is delegated to search_tool."""
from shopping_agent import FinalAgent
from shopping_agent.search_adapter import SearchToolAdapter


class Agent(FinalAgent):
    def __init__(self, catalog_path=None, *, search_function=None, details_function=None, **kwargs):
        if catalog_path is None:
            kwargs['search_adapter'] = SearchToolAdapter(search_function, details_function)
        kwargs.setdefault('orchestration_mode', 'adaptive')
        super().__init__(catalog_path, **kwargs)
