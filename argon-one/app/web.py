#!/usr/bin/python3
# Runs in the web container, without access to the hardware. It downloads Argon's scripts into
# staging and asks the daemon, through files in the run folder, to apply them or restart.
import difflib
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, "/app")
from common import (APPLY_REQUEST, ARGON, CONFIG, FILES, RESTART_REQUEST, RESULT_FILE, ROLLBACK_REQUEST, RUN, STAGING,
                    STATUS_FILE, now, read_json, syntax_error, version_of)

SERVER = os.environ.get("ARGON_SERVER", "https://download.argon40.com")
FAN_FILE = CONFIG / "argononed.conf"
UNITS_FILE = CONFIG / "argonunits.conf"
DEFAULT_POINTS = [[55, 30], [60, 55], [65, 100]]
CHECK_EVERY = 24 * 3600
INDEX = (Path(__file__).parent / "index.html").read_bytes()

lock = threading.Lock()
state = {"available": None, "checked_at": None, "check_error": None, "busy_since": None}


def write_fan(points):
    lines = ["#", "# Argon Fan Speed Configuration (CPU): temperature=speed%", "#"]
    lines += [f"{temperature}={speed}" for temperature, speed in sorted(points)]
    FAN_FILE.write_text("\n".join(lines) + "\n")


def read_fan():
    points = []
    try:
        for line in FAN_FILE.read_text().splitlines():
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*=\s*(\d+)\s*", line)
            if match:
                points.append([round(float(match[1])), int(match[2])])
    except OSError:
        pass
    return sorted(points) or DEFAULT_POINTS


def read_units():
    try:
        match = re.search(r"^temperature=\"?([CF])", UNITS_FILE.read_text(), re.MULTILINE)
    except OSError:
        match = None
    return match[1] if match else "C"


def write_units(units):
    UNITS_FILE.write_text(f"#\n# Argon Unit Configuration\n#\ntemperature={units}\n")


def request_file(path):
    path.touch()


def clear_staging():
    for path in STAGING.iterdir():
        if path.name != ".gitkeep":
            path.unlink()


def fetch():
    """Downloads and syntax checks every file. Raises ValueError naming the file at the first problem."""
    clear_staging()
    for remote, name in FILES.items():
        try:
            with urllib.request.urlopen(f"{SERVER}/scripts/{remote}", timeout=60) as response:
                content = response.read()
        except OSError as error:
            clear_staging()
            raise ValueError(f"download:{remote}") from error
        if not content:
            clear_staging()
            raise ValueError(f"empty:{remote}")
        (STAGING / name).write_bytes(content)
        if syntax_error(STAGING / name):
            clear_staging()
            raise ValueError(f"syntax:{remote}")


def compare():
    changed, diff = [], []
    for name in FILES.values():
        new = (STAGING / name).read_text(errors="replace").splitlines()
        try:
            old = (ARGON / name).read_text(errors="replace").splitlines()
        except OSError:
            old = []
        if old != new:
            lines = list(difflib.unified_diff(old, new, f"current/{name}", f"new/{name}", lineterm=""))
            changed.append({"file": name, "lines": sum(1 for line in lines[2:] if line[:1] in "+-")})
            diff += lines
    return changed, "\n".join(diff)[:200000]


def check_for_update():
    with lock:
        try:
            fetch()
        except ValueError as error:
            state.update(available=None, check_error=str(error), checked_at=now())
            return
        changed, diff = compare()
        state.update(check_error=None, checked_at=now())
        if changed:
            state["available"] = {"version": version_of(STAGING), "files": changed, "diff": diff}
        else:
            state["available"] = None
            clear_staging()


def install_first_time():
    with lock:
        fetch()
        state["busy_since"] = now()
        request_file(APPLY_REQUEST)


def background():
    if not UNITS_FILE.exists():
        write_units("C")
    if not FAN_FILE.exists():
        write_fan(DEFAULT_POINTS)
    while not (ARGON / "argononed.py").exists():
        try:
            state["check_error"] = None
            install_first_time()
            time.sleep(15)
        except (ValueError, OSError) as error:
            state["check_error"] = str(error)
        time.sleep(60)
    while True:
        time.sleep(CHECK_EVERY)
        check_for_update()


def status():
    argon = read_json(STATUS_FILE)
    result = read_json(RESULT_FILE)
    busy = state["busy_since"]
    if busy and result.get("state") != "applying" and result.get("time", 0) >= busy:
        state["busy_since"] = busy = None
    if busy and now() - busy > 120:
        state["busy_since"] = busy = None
    return {
        "time": now(),
        "argon": argon,
        "online": now() - argon.get("time", 0) < 45,
        "points": read_fan(),
        "units": read_units(),
        "update": {
            "available": state["available"],
            "checked_at": state["checked_at"],
            "error": state["check_error"],
            "busy": bool(busy) or result.get("state") == "applying",
            "result": result,
        },
    }


def valid_points(points):
    if not isinstance(points, list) or not 1 <= len(points) <= 8:
        return False
    for point in points:
        if not (isinstance(point, list) and len(point) == 2 and all(type(value) is int for value in point)):
            return False
        if not (0 <= point[0] <= 100 and 30 <= point[1] <= 100):
            return False
    return len({point[0] for point in points}) == len(points)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, code, body, content_type="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.reply(200, INDEX, "text/html; charset=utf-8")
        elif path == "/api/status":
            self.reply(200, status())
        else:
            self.reply(404, {"error": "not-found"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.reply(403, {"error": "origin"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 65536 or "application/json" not in self.headers.get("Content-Type", ""):
                return self.reply(400, {"error": "request"})
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "request"})

        path = urlsplit(self.path).path
        if path == "/api/fan":
            if not valid_points(body.get("points")):
                return self.reply(400, {"error": "points"})
            write_fan(body["points"])
            request_file(RESTART_REQUEST)
        elif path == "/api/units":
            if body.get("units") not in ("C", "F"):
                return self.reply(400, {"error": "units"})
            write_units(body["units"])
        elif path == "/api/update/check":
            if state["busy_since"]:
                return self.reply(409, {"error": "busy"})
            check_for_update()
        elif path == "/api/update/apply":
            with lock:
                if not state["available"]:
                    return self.reply(409, {"error": "nothing-to-install"})
                state.update(available=None, busy_since=now())
                request_file(APPLY_REQUEST)
        elif path == "/api/update/rollback":
            with lock:
                state.update(available=None, busy_since=now())
                clear_staging()
                request_file(ROLLBACK_REQUEST)
        else:
            return self.reply(404, {"error": "not-found"})
        self.reply(200, status())


def main():
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    for directory in (CONFIG, RUN, STAGING):
        directory.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=background, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), Handler).serve_forever()


if __name__ == "__main__":
    main()
