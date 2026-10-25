"""Preview launcher: maps the preview host's PORT env to API_PORT and starts the API.

Used only by .claude/launch.json for the in-app Browser preview. The real server is
started via `python -m services.api.main` as usual.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The preview host assigns a free port via PORT; the API reads API_PORT.
port = os.environ.get("PORT")
if port:
    os.environ["API_PORT"] = port
# Avoid spawning the automation worker for a throwaway preview instance.
os.environ.setdefault("WORKER_AUTO_START", "0")

os.chdir(HERE)
sys.path.insert(0, str(HERE))

from services.api.main import main  # noqa: E402

if __name__ == "__main__":
    main()
