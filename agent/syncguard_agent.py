"""Syncguard agent entry point.

    Tray (normal, a person's machine):
        pythonw.exe agent\\syncguard_agent.py

    Headless (a shared always-on workstation):
        python.exe agent\\syncguard_agent.py --headless

    One-shot admin actions:
        --enroll CODE            redeem a pairing code without the tray
        --base-url URL           point the agent at a different EasyBIM
        --status                 print current state and exit

See agent/README.md for installing the logon scheduled task.
"""

import argparse
import ctypes
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from syncguard import AGENT_VERSION, config, machine  # noqa: E402
from syncguard.core import AgentCore  # noqa: E402

LOG_MAX_BYTES = 2 * 1024 * 1024
_MUTEX_NAME = "Global\\EasyBIMSyncguardAgent"


class Log(object):
    """Tiny append log with a size cap.

    Not the `logging` module: nothing else in this repo uses it, and a single
    capped file is easier for a BIM engineer to read and send on.
    """

    def __init__(self, path, echo=False):
        self.path = path
        self.echo = echo
        self._lock = threading.Lock()

    def __call__(self, message):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = "%s  %s" % (stamp, message)
        if self.echo:
            try:
                print(line, flush=True)
            except Exception:
                pass
        with self._lock:
            try:
                if os.path.exists(self.path) and \
                        os.path.getsize(self.path) > LOG_MAX_BYTES:
                    os.replace(self.path, self.path + ".old")
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass


def _already_running():
    """Refuse a second instance.

    A logon scheduled task can fire more than once — a fast user switch, or a
    task re-registered while one is live — and two agents would both claim runs
    and fight over the same Revit.
    """
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return False
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="syncguard_agent",
        description="EasyBIM Syncguard workstation agent")
    parser.add_argument("--headless", action="store_true",
                        help="run with no tray icon (shared workstation)")
    parser.add_argument("--enroll", metavar="CODE",
                        help="redeem a pairing code and exit")
    parser.add_argument("--base-url", metavar="URL",
                        help="set the EasyBIM base URL and continue")
    parser.add_argument("--status", action="store_true",
                        help="print agent state as JSON and exit")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        print(AGENT_VERSION)
        return 0

    config.ensure_state_dir()
    log = Log(config.log_path(), echo=args.headless or bool(args.enroll)
              or args.status)

    if args.base_url:
        cfg = config.load_config()
        cfg["baseUrl"] = args.base_url.rstrip("/")
        config.save_config(cfg)
        log("base URL set to %s" % cfg["baseUrl"])

    if args.status:
        cfg = config.load_config()
        state = machine.take_snapshot(machine.SignInState())
        print(json.dumps({
            "agentVersion": AGENT_VERSION,
            "agentId": cfg["agentId"],
            "machineName": cfg["machineName"],
            "baseUrl": cfg["baseUrl"],
            "enrolled": bool(config.load_token()),
            "revitVersions": state.revit_versions,
            "revitRunning": state.revit_running,
            "openModels": state.open_models,
            "autodeskUser": state.autodesk_user,
            "stateDir": config.state_dir(),
        }, indent=2))
        return 0

    if args.enroll:
        core = AgentCore(logger=log)
        ok, message = core.enroll(args.enroll)
        print(message)
        return 0 if ok else 1

    if _already_running():
        log("another Syncguard agent is already running; exiting")
        return 0

    log("Syncguard agent %s starting (headless=%s)"
        % (AGENT_VERSION, args.headless))

    if args.headless:
        core = AgentCore(logger=log)
        core.start()
        stop = threading.Event()

        def _bye(*_args):
            log("shutdown requested")
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _bye)
            except Exception:
                pass
        if not core.enrolled:
            log("NOT ENROLLED — run with --enroll CODE, or use the tray")
        try:
            while not stop.is_set():
                stop.wait(3600)
        except KeyboardInterrupt:
            pass
        core.stop()
        return 0

    # Tray mode. The UI is built first so the core can call back into it, but
    # the core is what the UI reads state from — hence the holder indirection.
    from syncguard import win32ui
    win32ui.enable_dpi_awareness()

    holder = {"core": None}

    def _quit():
        ui.close()

    ui = win32ui.TrayUi(lambda: holder["core"], on_quit=_quit)
    core = AgentCore(ui=ui, logger=log)
    holder["core"] = core
    core.start()
    try:
        ui.run()
    finally:
        core.stop()
        log("Syncguard agent stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
