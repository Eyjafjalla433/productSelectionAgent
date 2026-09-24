"""Read-only checks; never initializes or writes into search_tool."""
from pathlib import Path
import importlib.util
from .search_resources import model_directory


def check_search_tool():
    root = Path(__file__).resolve().parents[1] / 'search_tool'
    errors = []
    for relative in ('artifacts/products.parquet', 'artifacts/retrieval_index'):
        if not (root / relative).exists():
            errors.append(f'Missing search_tool resource: {root / relative}')
    model = model_directory()
    if not (model / 'config.json').is_file():
        errors.append(f'Missing model config: {model}')
    if not any(model.glob('*.safetensors')) and not any(model.glob('pytorch_model*.bin')):
        errors.append(f'Missing model weights: {model}')
    for module in ('bm25s', 'numpy', 'pandas', 'torch', 'transformers', 'pyarrow'):
        if importlib.util.find_spec(module) is None:
            errors.append(f'Missing Python dependency: {module}')
    return errors


if __name__ == '__main__':
    problems = check_search_tool()
    print('\n'.join(problems) if problems else 'Search tool resources found')
    raise SystemExit(bool(problems))
