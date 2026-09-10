"""Launch `pyrevit run`, follow its progress, decide what happened.

The three `pyrevit run` gotchas from Phase 3 are all honoured here, and all
three fail silently if broken, so none of them is safe to "tidy up":

  * THE SCRIPT PATH MUST BE ABSOLUTE. A relative path is copied verbatim into
    the journal's ScriptSource, Revit runs from its own temp working directory,
    and the script is never found — no error, no output, exit code 0.
  * %TEMP% INSIDE REVIT IS REDIRECTED to the per-run folder that --purge
    deletes, so every output path passed in must be absolute and outside TEMP.
    They go under the agent's own state directory.
  * SearchPaths IS EMPTY, so the script bootstraps its own imports and needs no
    --import flag.

TIMEOUTS ARE PHASE-AWARE, which is the whole reason step events exist in the
progress stream. Roughly 95% of a run's wall clock is the model open, and the
script is blocked inside OpenDocumentFile for all of it with nothing to report:
6m55s of a 7m20s run on a merely representative model, and the hub holds far
bigger ones. A flat silence window would kill healthy runs, so the window is
wide between `open-model: running` and `open-model: done`, and tight afterwards.

Silence is measured on SCRIPT events only, never on the agent's own keepalive
posts. Those keepalives exist to feed the server's 45-minute stale-claim
watchdog during a long quiet open; if they also counted as progress here, the
agent could never detect a hang at all.
"""

import glob
import json
import os
import subprocess
import time

from . import machine

# Silence windows, measured from the last event the SCRIPT produced.
OPEN_SILENCE_SEC = 40 * 60
DEFAULT_SILENCE_SEC = 10 * 60

# Revit can hang on teardown after a perfectly good sync. Once the result file
# is readable the outcome is already known, so wait only briefly for a clean
# exit and then kill — rather than waiting out the silence window and reporting
# a failure that did not happen.
RESULT_GRACE_SEC = 120

# Feeds the server watchdog and gives the live console something to show during
# a long open. Well inside the 45-minute stale-claim limit.
KEEPALIVE_SEC = 4 * 60

POLL_SEC = 0.5

PYREVIT_CANDIDATES = [
    r"C:\Program Files\pyRevit-Master\bin\pyrevit.exe",
    r"C:\Program Files\pyRevit-Master\bin\pyrevit",
    r"C:\Program Files (x86)\pyRevit-Master\bin\pyrevit.exe",
]

_CREATE_NO_WINDOW = 0x08000000


def find_pyrevit_cli():
    for candidate in PYREVIT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    for pattern in (r"C:\Program Files\pyRevit*\bin\pyrevit.exe",
                    r"C:\Program Files\pyRevit*\bin\pyrevit"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    return None


def find_sync_script():
    """Absolute path to commands/syncguard_command.py in this repo.

    Derived from __file__ so the agent works from any checkout location without
    a configured path to get wrong — and absolute, because a relative one would
    fail silently.
    """
    agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(agent_dir)
    script = os.path.join(repo_root, "commands", "syncguard_command.py")
    return script if os.path.isfile(script) else None


class NdjsonTail(object):
    """Incremental reader for the script's progress file.

    Consumes only up to the last newline, so a record still being written is
    left for the next read instead of being parsed half-formed. A complete but
    unparseable line is skipped — being killed mid-write is precisely the case
    this file exists to survive.
    """

    def __init__(self, path):
        self.path = path
        self._offset = 0
        self.skipped = 0

    def read_new(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size <= self._offset:
            return []
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self._offset)
                blob = handle.read()
        except OSError:
            return []
        cut = blob.rfind(b"\n")
        if cut == -1:
            return []
        self._offset += cut + 1
        events = []
        for raw in blob[:cut + 1].split(b"\n"):
            if not raw.strip():
                continue
            try:
                events.append(json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                self.skipped += 1
        return events


class Outcome(object):
    def __init__(self, status, attention_reason=None, lines=None,
                 diagnostics=None):
        self.status = status
        self.attention_reason = attention_reason
        self.lines = lines or []
        self.diagnostics = diagnostics or {}


def _kill_tree(pid):
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       creationflags=_CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
    except Exception:
        pass


def _kill_revit(pids, log):
    """Kill Revit processes this run started, and report what blocked them.

    `taskkill /F` alone is not sufficient — see machine.force_kill. Any dialog
    text recovered on the way out is the most useful diagnostic there is for a
    run that produced no output.
    """
    messages = []
    for pid in pids:
        try:
            gone, dialog_text = machine.force_kill(pid)
            messages.extend(dialog_text)
            log("killed Revit %s (gone=%s)%s"
                % (pid, gone,
                   (" blocked on: %s" % "; ".join(dialog_text))
                   if dialog_text else ""))
        except Exception as exc:
            log("could not kill Revit %s: %r" % (pid, exc))
    return messages


def _read_result(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


class SyncRunner(object):
    """Runs one job to completion and reports what happened.

    Callbacks are invoked as events arrive:
        on_line(level, text)
        on_step(key, status, message)
        on_keepalive(phase, seconds_in_phase)
    """

    def __init__(self, job, on_line=None, on_step=None, on_keepalive=None,
                 logger=None):
        self.job = job
        self._on_line = on_line or (lambda *_a: None)
        self._on_step = on_step or (lambda *_a: None)
        self._on_keepalive = on_keepalive or (lambda *_a: None)
        self._log = logger or (lambda *_a, **_k: None)
        self.phase = "startup"
        self._phase_started = time.time()
        self.blocking_dialogs = []

    def _set_phase(self, phase):
        if phase != self.phase:
            self.phase = phase
            self._phase_started = time.time()

    def _silence_window(self):
        return OPEN_SILENCE_SEC if self.phase == "open-model" \
            else DEFAULT_SILENCE_SEC

    def execute(self):
        job = self.job
        cli = find_pyrevit_cli()
        script = find_sync_script()
        if not cli:
            return Outcome(
                "needs_attention",
                "The pyRevit command-line tool was not found on this computer. "
                "Install pyRevit, then run again.",
                [{"level": "error", "text": "pyrevit CLI not found"}])
        if not script:
            return Outcome(
                "needs_attention",
                "The Syncguard Revit script is missing from this computer's "
                "EasyBIM extension. Update the extension, then run again.",
                [{"level": "error",
                  "text": "commands/syncguard_command.py not found"}])

        result_path = os.path.join(job.work_dir, "result.json")
        progress_path = os.path.join(job.work_dir, "progress.ndjson")
        console_path = os.path.join(job.work_dir, "pyrevit-console.log")
        for stale in (result_path, progress_path):
            try:
                os.remove(stale)
            except OSError:
                pass

        env = dict(os.environ)
        env.update({
            "SYNCGUARD_PROJECT_GUID": str(job.project_guid or ""),
            "SYNCGUARD_MODEL_GUID": str(job.model_guid or ""),
            "SYNCGUARD_REGION": str(job.region or ""),
            "SYNCGUARD_EXPECTED_REVIT": str(job.revit_version or ""),
            "SYNCGUARD_RESULT_JSON": result_path,
            "SYNCGUARD_PROGRESS_NDJSON": progress_path,
            "SYNCGUARD_COMMENT": job.comment,
        })
        if job.lock_wait_sec:
            env["SYNCGUARD_LOCK_WAIT_SEC"] = str(job.lock_wait_sec)

        command = [cli, "run", script,
                   "--revit=%s" % job.revit_version, "--purge"]
        self._log("launching: %s" % " ".join(command))
        self._on_line("info", "Starting Revit %s" % job.revit_version)

        # Only Revit processes that appear after launch may be killed, so a
        # timeout never takes down a Revit the engineer had open themselves.
        revit_before = set(machine.revit_pids())

        try:
            console = open(console_path, "wb")
        except OSError:
            console = None
        try:
            process = subprocess.Popen(
                command, env=env, stdout=console or subprocess.DEVNULL,
                stderr=subprocess.STDOUT, creationflags=_CREATE_NO_WINDOW,
                cwd=job.work_dir)
        except Exception as exc:
            if console:
                console.close()
            return Outcome(
                "failed", None,
                [{"level": "error",
                  "text": "could not start pyrevit: %s" % exc}])

        tail = NdjsonTail(progress_path)
        last_event_at = time.time()
        last_keepalive = time.time()
        result_seen_at = None
        killed_reason = None
        self._set_phase("startup")
        script_started = False

        try:
            while True:
                events = tail.read_new()
                if events:
                    last_event_at = time.time()
                    if not script_started:
                        script_started = True
                        # The script is executing, so Revit is genuinely up.
                        # That is the only honest signal for open-revit: done.
                        self._on_step("open-revit", "done", "Revit is running")
                    for event in events:
                        self._dispatch(event)

                if result_seen_at is None and os.path.isfile(result_path):
                    result_seen_at = time.time()
                    self._log("result file appeared")

                exited = process.poll() is not None
                if exited:
                    break

                now = time.time()
                if result_seen_at and now - result_seen_at > RESULT_GRACE_SEC:
                    killed_reason = "teardown"
                    self._log("result readable but process still alive; killing")
                    break

                silence = now - last_event_at
                if silence > self._silence_window():
                    killed_reason = "silence"
                    break

                if now - last_keepalive >= KEEPALIVE_SEC:
                    last_keepalive = now
                    self._on_keepalive(self.phase, now - self._phase_started)

                time.sleep(POLL_SEC)
        finally:
            if killed_reason:
                _kill_tree(process.pid)
                new_revit = set(machine.revit_pids()) - revit_before
                if new_revit:
                    self._log("killing Revit started by this run: %s"
                              % sorted(new_revit))
                    self.blocking_dialogs = _kill_revit(sorted(new_revit),
                                                        self._log)
                try:
                    process.wait(timeout=30)
                except Exception:
                    pass
            if console:
                try:
                    console.close()
                except Exception:
                    pass

        # Drain anything written between the last poll and exit.
        for event in tail.read_new():
            self._dispatch(event)

        exit_code = process.poll()
        result = _read_result(result_path)
        progress_bytes = 0
        try:
            progress_bytes = os.path.getsize(progress_path)
        except OSError:
            progress_bytes = 0

        diagnostics = {
            "exitCode": exit_code,
            "killed": killed_reason,
            "progressBytes": progress_bytes,
            "skippedBadLines": tail.skipped,
            "workDir": job.work_dir,
        }
        return self._classify(result, killed_reason, exit_code, progress_bytes,
                              console_path, diagnostics)

    # -- event handling ---------------------------------------------------

    def _dispatch(self, event):
        kind = event.get("kind")
        if kind == "step":
            key = event.get("key") or ""
            status = event.get("status") or ""
            message = event.get("message") or ""
            if key == "open-model" and status == "running":
                self._set_phase("open-model")
            elif key == "open-model" and status in ("done", "failed"):
                self._set_phase("post-open")
            # `pending` is the script declaring its step list up front; the
            # platform already has it from the claim, and forwarding it would
            # reset a step the agent has already advanced.
            if status and status != "pending":
                self._on_step(key, status, message)
        elif kind in (None, "line"):
            level = event.get("level") or "info"
            text = event.get("text")
            if text:
                self._on_line(level, text)

    # -- verdict ----------------------------------------------------------

    def _console_tail(self, path, limit=1500):
        try:
            with open(path, "rb") as handle:
                size = os.path.getsize(path)
                if size > limit:
                    handle.seek(size - limit)
                return handle.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def _classify(self, result, killed_reason, exit_code, progress_bytes,
                  console_path, diagnostics):
        # A readable result is the contract, and it outranks a rough teardown:
        # the sync either happened or it did not, and the result file knows.
        if result:
            status = result.get("status") or "failed"
            reason = result.get("attentionReason")
            lines = []
            if killed_reason == "teardown":
                lines.append({
                    "level": "warn",
                    "text": "Revit did not exit after the sync finished; it was "
                            "closed by the agent. The sync itself completed.",
                })
            diagnostics["resultDiagnostics"] = result.get("diagnostics") or {}
            return Outcome(status, reason, lines, diagnostics)

        if killed_reason == "silence":
            where = {
                "open-model": "while opening the model",
                "post-open": "after opening the model",
                "startup": "while starting Revit",
            }.get(self.phase, "during the run")
            minutes = int(self._silence_window() / 60)
            lines = [{"level": "error",
                      "text": "No progress for %d minutes %s — Revit stopped "
                              "responding and was closed."
                              % (minutes, where)}]
            if self.blocking_dialogs:
                diagnostics["blockingDialogs"] = list(self.blocking_dialogs)
                for text in self.blocking_dialogs:
                    lines.append({"level": "error",
                                  "text": "Revit was showing: %s" % text})

            # Silence during startup with nothing written at all means Revit
            # never reached the script. When Revit left a dialog behind, that
            # dialog IS the diagnosis and a human should see it verbatim —
            # these have been licence-checkout and COM-busy faults, which need
            # the machine seen to rather than a blind retry.
            if self.phase == "startup" and progress_bytes == 0:
                if self.blocking_dialogs:
                    return Outcome(
                        "needs_attention",
                        "Revit could not start properly on this computer and "
                        "never ran the sync. It reported: “%s”"
                        % self.blocking_dialogs[0],
                        lines, diagnostics)
                return Outcome(
                    "needs_attention",
                    "Revit started but never ran the Syncguard script on this "
                    "computer, and left no explanation. Check pyRevit and the "
                    "EasyBIM extension on that machine.",
                    lines, diagnostics)
            return Outcome("failed", None, lines, diagnostics)

        if progress_bytes > 0:
            # The script ran and reported, then died before writing a verdict.
            return Outcome(
                "failed", None,
                [{"level": "error",
                  "text": "Revit exited without writing a result (exit code "
                          "%s). The run crashed part-way." % exit_code}],
                diagnostics)

        # Nothing at all: no result, no progress. Exit code disambiguates.
        # `pyrevit run` exits 0 having done nothing when it cannot find the
        # script — the absolute-path trap — which is a configuration fault that
        # will fail identically forever, so it must not be reported as a
        # retryable failure.
        console = self._console_tail(console_path)
        if exit_code == 0:
            lines = [{"level": "error",
                      "text": "Revit produced no output at all. The Syncguard "
                              "script was not run."}]
            if console:
                lines.append({"level": "info", "text": console})
            return Outcome(
                "needs_attention",
                "Syncguard could not run the Revit script on this computer. "
                "This is a setup problem on the agent, not something a retry "
                "will fix — check the EasyBIM extension and pyRevit install.",
                lines, diagnostics)

        lines = [{"level": "error",
                  "text": "Revit failed to start (exit code %s)." % exit_code}]
        if console:
            lines.append({"level": "info", "text": console})
        return Outcome("failed", None, lines, diagnostics)
