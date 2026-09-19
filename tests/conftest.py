import sys
from pathlib import Path

# Ensure package is on sys.path for test runner
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
