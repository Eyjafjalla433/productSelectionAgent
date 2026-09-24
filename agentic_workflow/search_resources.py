"""Resolve supplied model layouts without changing search_tool files."""
from pathlib import Path
import os
import re
import warnings

TOOL_ROOT = Path(__file__).resolve().parents[1] / 'search_tool'


def configure_allocator():
    """Repair the known local boolean syntax typo before torch initializes CUDA."""
    key = 'PYTORCH_CUDA_ALLOC_CONF'
    value = os.environ.get(key, '')
    corrected = re.sub(r'\bexpandable_segments\s*=\s*(True|False)\b',
                       r'expandable_segments:\1', value)
    if corrected != value:
        os.environ[key] = corrected
        warnings.warn('Normalized expandable_segments syntax in this process only.', RuntimeWarning)


def model_directory():
    for path in (TOOL_ROOT / 'model', TOOL_ROOT / 'models' / 'task_1_us_title_bullet'):
        if (path / 'config.json').is_file() and (
            any(path.glob('*.safetensors')) or any(path.glob('pytorch_model*.bin'))
        ):
            return path
    return TOOL_ROOT / 'model'
