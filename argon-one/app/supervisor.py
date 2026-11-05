#!/usr/bin/python3
# Runs in the daemon container, which has no network. Keeps Argon's daemon running, reinstalls the
# shutdown hook the host loses at every boot, and applies updates the web service has downloaded.
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
from common import (APPLY_REQUEST, ARGON, BACKUPS, CONFIG, FILES, RESTART_REQUEST, RESULT_FILE, ROLLBACK_REQUEST, RUN,
                    STAGING, STATUS_FILE, now, syntax_error, version_of, write_json)

HOST_DEV = Path("/host-dev")
DEVICES = ("i2c-1", "gpiochip0")
HOOK_SOURCE = "/app/shutdown_hook.py"
HOOK_TARGET = "/host-shutdown/argon-shutdown.py"
HEALTH_DELAY = 8
KEEP_BACKUPS = 5

child = None
hook_installed = False
running = True


def stop(*_):
    global running
    running = False


def installed():
    return (ARGON / "argononed.py").exists()


def link_devices():
    # The host's /dev is mounted because a fixed device list stops the container on boards without
    # them. The container's cgroup rules still allow only I2C and GPIO.
    for name in DEVICES:
        target = Path("/dev") / name
        if (HOST_DEV / name).exists() and not target.is_symlink():
            target.symlink_to(HOST_DEV / name)


def case_present():
    """The button line floats without the case, so nothing starts unless the case's MCU answers."""
    import smbus

    link_devices()
    if not Path("/dev/gpiochip0").exists():
        return False
    try:
        bus = smbus.SMBus(1)
        try:
            bus.read_byte_data(0x1a, 0x80)
        finally:
            bus.close()
        return True
    except OSError:
        return False


def install_hook():
    global hook_installed
    if hook_installed:
        return
    try:
        shutil.copyfile(HOOK_SOURCE, HOOK_TARGET)
        os.chmod(HOOK_TARGET, 0o755)
        hook_installed = True
    except OSError as error:
        print(f"Shutdown hook not installed, the case will not cut power after a shutdown: {error}", flush=True)


def start_daemon():
    global child
    if installed() and case_present():
        install_hook()
        child = subprocess.Popen([sys.executable, "-u", "/etc/argon/argononed.py", "SERVICE"], cwd="/")


def stop_daemon():
    global child
    if child is None:
        return
    child.terminate()
    try:
        child.wait(5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    child = None


def restart_daemon():
    stop_daemon()
    start_daemon()


def health():
    """Returns (problem, fan speed): the daemon has both threads, holds the button line, reaches the case."""
    import gpiod
    import smbus

    if not case_present():
        return "no-hardware", None
    if child is None or child.poll() is not None:
        return "daemon", None
    try:
        if len(os.listdir(f"/proc/{child.pid}/task")) < 3:
            return "daemon", None
    except OSError:
        return "daemon", None
    try:
        with gpiod.Chip("/dev/gpiochip0") as chip:
            if chip.get_line_info(4).consumer != "argon":
                return "button", None
    except OSError:
        return "button", None
    try:
        bus = smbus.SMBus(1)
        try:
            return None, bus.read_byte_data(0x1a, 0x80)
        finally:
            bus.close()
    except OSError:
        return "case", None


def temperature():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def latest_backup():
    backups = sorted(path for path in BACKUPS.iterdir() if path.is_dir())
    return backups[-1] if backups else None


def write_status():
    problem, duty = health() if installed() else ("not-installed", None)
    backup = latest_backup()
    write_json(STATUS_FILE, {
        "time": now(),
        "temperature": temperature(),
        "duty": duty,
        "problem": problem,
        "version": version_of(ARGON),
        "previous_version": version_of(backup) if backup else None,
        "hook_installed": hook_installed,
    })


def install_from(source):
    for name in FILES.values():
        temporary = ARGON / (name + ".tmp")
        shutil.copyfile(source / name, temporary)
        os.chmod(temporary, 0o755 if name.endswith(".sh") else 0o644)
        os.replace(temporary, ARGON / name)


def snapshot(directory):
    directory.mkdir()
    for name in FILES.values():
        shutil.copyfile(ARGON / name, directory / name)


def clear_staging():
    for path in STAGING.iterdir():
        if path.name != ".gitkeep":
            path.unlink()


def checked_restart():
    restart_daemon()
    time.sleep(HEALTH_DELAY)
    problem = health()[0]
    # Without the case there is nothing to test the scripts on.
    return None if problem == "no-hardware" else problem


def apply_update():
    write_json(RESULT_FILE, {"state": "applying", "time": now()})
    missing = [name for name in FILES.values() if not (STAGING / name).exists()]
    invalid = [name for name in FILES.values() if name not in missing and syntax_error(STAGING / name)]
    if missing or invalid:
        clear_staging()
        write_json(RESULT_FILE, {"state": "rejected", "time": now(), "files": missing + invalid})
        return

    backup = None
    if installed():
        backup = BACKUPS / time.strftime("%Y%m%d-%H%M%S")
        snapshot(backup)
    install_from(STAGING)
    clear_staging()

    problem = checked_restart()
    if problem is None:
        for old in sorted(path for path in BACKUPS.iterdir() if path.is_dir())[:-KEEP_BACKUPS]:
            shutil.rmtree(old)
        write_json(RESULT_FILE, {"state": "applied", "time": now(), "version": version_of(ARGON)})
        return

    if backup is None:
        stop_daemon()
        for name in FILES.values():
            (ARGON / name).unlink(missing_ok=True)
        write_json(RESULT_FILE, {"state": "failed", "time": now(), "problem": problem})
        return
    install_from(backup)
    shutil.rmtree(backup)
    restored = checked_restart() is None
    write_json(RESULT_FILE, {"state": "rolled-back" if restored else "broken", "time": now(), "problem": problem,
                             "version": version_of(ARGON)})


def rollback():
    backup = latest_backup()
    if backup is None:
        write_json(RESULT_FILE, {"state": "no-backup", "time": now()})
        return
    write_json(RESULT_FILE, {"state": "applying", "time": now()})
    current = BACKUPS / ".before-rollback"
    shutil.rmtree(current, ignore_errors=True)
    snapshot(current)
    install_from(backup)
    problem = checked_restart()
    if problem is None:
        shutil.rmtree(backup)
        shutil.rmtree(current)
        write_json(RESULT_FILE, {"state": "restored", "time": now(), "version": version_of(ARGON)})
        return
    install_from(current)
    shutil.rmtree(current)
    restored = checked_restart() is None
    write_json(RESULT_FILE, {"state": "rollback-failed" if restored else "broken", "time": now(), "problem": problem,
                             "version": version_of(ARGON)})


def take(request):
    try:
        request.unlink()
        return True
    except FileNotFoundError:
        return False


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for directory in (ARGON, CONFIG, RUN, STAGING, BACKUPS):
        directory.mkdir(parents=True, exist_ok=True)
    start_daemon()
    last_status = 0
    last_start = time.time()
    while running:
        if take(APPLY_REQUEST):
            apply_update()
            last_status = 0
        if take(ROLLBACK_REQUEST):
            rollback()
            last_status = 0
        if take(RESTART_REQUEST):
            restart_daemon()
            last_status = 0
        if installed() and (child is None or child.poll() is not None) and time.time() - last_start > 15:
            last_start = time.time()
            start_daemon()
        if time.time() - last_status >= 10:
            write_status()
            last_status = time.time()
        time.sleep(1)
    stop_daemon()


if __name__ == "__main__":
    main()
