import sys
from pathlib import Path

# Puts product/ml/ on sys.path so tests can `import features...` and
# `import common...` as top-level packages, matching eda/test_gates.py's
# convention.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
