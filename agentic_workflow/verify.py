"""Run workflow regressions with the description dependency, without search assets."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='agentic-workflow-') as directory:
        target = Path(directory) / 'agentic_workflow'
        shutil.copytree(root, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        # Copy the dependency for integration tests; never import or modify the
        # original shared package while running this isolated suite.
        description = root.parent / 'description_module'
        shutil.copytree(description, Path(directory) / 'description_module',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        # -I excludes inherited PYTHONPATH and user site packages. Only the
        # isolated directory is explicitly added; no original repo paths.
        script = '''
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import agentic_workflow
suite = unittest.TestSuite()
for path in ('tests', 'shopping_agent/tests', 'mvp/tests'):
    suite.addTests(unittest.TestLoader().discover(str(Path('agentic_workflow') / path)))
result = unittest.TextTestRunner(verbosity=1).run(suite)
assert not Path('reference').exists()
for module in list(sys.modules.values()):
    path = getattr(module, '__file__', None)
    if path:
        assert 'reference' not in Path(path).parts, path
raise SystemExit(not result.wasSuccessful())
'''
        return subprocess.run([sys.executable, '-I', '-B', '-c', script], cwd=directory).returncode


if __name__ == '__main__':
    raise SystemExit(main())
