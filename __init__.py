import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent
root_dir = backend_dir.parent
for d in [str(root_dir), str(backend_dir)]:
    if d not in sys.path:
        sys.path.insert(0, d)
