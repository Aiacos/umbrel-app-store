import json
import os
import re
import subprocess
import time
from pathlib import Path

DATA = Path(os.environ.get("ARGON_DATA", "/data"))
ARGON = DATA / "argon"
CONFIG = DATA / "config"
RUN = DATA / "run"
STAGING = DATA / "staging"
BACKUPS = DATA / "backups"

STATUS_FILE = RUN / "status.json"
RESULT_FILE = RUN / "update-result.json"
RESTART_REQUEST = RUN / "restart-request"
APPLY_REQUEST = RUN / "apply-request"
ROLLBACK_REQUEST = RUN / "rollback-request"

# Official file name -> name the daemon imports.
FILES = {
    "argononed.py": "argononed.py",
    "argonsysinfo.py": "argonsysinfo.py",
    "argonregister.py": "argonregister.py",
    "argonstatus.py": "argonstatus.py",
    "argondashboard.py": "argondashboard.py",
    "argon-status.sh": "argon-status.sh",
    "argon-versioninfo.sh": "argon-versioninfo.sh",
    "argon-unitconfig.sh": "argon-unitconfig.sh",
    "argonone-fanconfig.sh": "argonone-fanconfig.sh",
    "argonpowerbutton-libgpiod.py": "argonpowerbutton.py",
}


def write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data))
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def version_of(directory):
    try:
        text = (directory / "argon-versioninfo.sh").read_text()
    except OSError:
        return None
    match = re.search(r'^VERSIONINFO="(.*)"', text, re.MULTILINE)
    return match.group(1) if match else None


def syntax_error(path):
    """Checks a downloaded file without running it."""
    path = Path(path)
    if path.suffix == ".py":
        try:
            compile(path.read_text(), str(path), "exec")
        except (SyntaxError, ValueError, UnicodeDecodeError) as error:
            return str(error)
    elif subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode != 0:
        return "shell syntax"
    return None


def now():
    return int(time.time())
