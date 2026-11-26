"""Sales Command Centre.

From the repository root:

    python3 main.py

The process checks dependencies and configuration, prepares the local database,
builds the dashboard if needed, serves it on localhost, and opens a browser.
Press Ctrl+C to stop.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from watson.bootstrap import ensure_runtime

ensure_runtime(ROOT)

from watson.runtime import main

if __name__ == "__main__":
    main()
