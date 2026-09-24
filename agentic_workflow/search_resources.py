"""Resolve supplied model layouts without changing search_tool files."""
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1] / 'search_tool'


def model_directory():
    for path in (TOOL_ROOT / 'model', TOOL_ROOT / 'models' / 'task_1_us_title_bullet'):
        if (path / 'config.json').is_file() and (
            any(path.glob('*.safetensors')) or any(path.glob('pytorch_model*.bin'))
        ):
            return path
    return TOOL_ROOT / 'model'
