import sys
from pathlib import Path

# Puts product/ml/ on sys.path so tests can `import export...`, `import
# features...` and `import train...` as top-level packages, matching
# ml/train/tests/conftest.py's convention.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
