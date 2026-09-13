import sys
from pathlib import Path

# Puts product/ml/ on sys.path so tests can `import train...`, `import
# features...` and `import common...` as top-level packages, matching
# ml/features/tests/conftest.py's convention.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
