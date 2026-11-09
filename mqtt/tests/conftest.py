import sys
from pathlib import Path

# Reuse the core test fakes.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
