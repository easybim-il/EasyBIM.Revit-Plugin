"""The agent's five jobs: enroll, heartbeat, claim, run, report.

Headless by design. The tray is an optional presentation layer passed in as
`ui`, so a shared always-on workstation runs this exact code with no UI at all.

Two threads:
  * heartbeat — always running once enrolled, so the platform sees the machine
    as online even mid-run, and so `enabled: false` is honoured promptly.
  * work — claims and runs, one job at a time.

WHY THE IDLE BACKOFF IS CAPPED. The platform treats an agent as offline after 5
minutes without a heartbeat, and its enqueue route refuses outright for an
offline machine. So "back off when there is no work" cannot back off past that:
a textbook exponential backoff would make an idle agent quietly un-bookable.
The ceiling here stays comfortably inside the window.
"""

import threading
import time

from . import AGENT_VERSION, config, machine
from .api import SyncguardApi
from .runner import SyncRunner

# Heartbeat pacing. MAX must stay well under the platform's 5-minute online
# window — see the module docstring.
HEARTBEAT_MIN_SEC = 30
HEARTBEAT_MAX_SEC = 150
HEARTBEAT_BUSY_SEC = 60

# Log forwarding: batched, because one request per line would be absurd on a
# model that produces hundreds of warnings.
LOG_FLUSH_SEC = 3.0
LOG_BATCH_MAX = 25

# Desktop takeover. Total possible deferral stays under the server's 45-minute
# stale-claim limit, so a polite "not now" can never cost the run.
DEFER_SEC = 10 * 60
MAX_DEFERS = 2
TAKEOVER_WARNING_SEC = 20

STATUS_NOT_ENROLLED = "not_enrolled"
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_DISABLED = "disabled"
STATUS_PAUSED = "paused"
STATUS_OFFLINE = "offline"


class Job(object):
    """One claimed run."""

    def __init__(self, payload):
        self.run_id = payload.get("runId")
        self.model_name = payload.get("modelName") or ""
        self.acc_project_id = payload.get("accProjectId")
        self.item_id = payload.get("itemId")
        self.project_guid = payload.get("projectGuid")
        self.model_guid = payload.get("modelGuid")
        self.region = payload.get("region") or "US"
        self.revit_version = str(payload.get("revitVersion") or "")
        self.steps = payload.get("steps") or []
        self.work_dir = config.run_dir(self.run_id)
        self.comment = "Syncguard: %s" % (self.model_name or "automated sync")
        self.lock_wait_sec = 300


class AgentCore(object):
    def __init__(self, ui=None, logger=None):
        self.ui = ui
        self._log = logger or (lambda *_a, **_k: None)
        self.cfg = config.load_config()
        self.sign_in = machine.SignInState()

        self._token = config.load_token()
        self.api = SyncguardApi(self.cfg["baseUrl"], self._token, self._log)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._has_work = False
        self._enabled = True
        self._paused = False
        self._online = False
        self._kind = None
        self._status = (STATUS_IDLE if self._token else STATUS_NOT_ENROLLED)
        self._current = None          # model name while running
        self._last_error = ""
        self._last_result = ""
        self._threads = []

    # -- state ------------------------------------------------------------

    @property
    def enrolled(self):
        return bool(self._token)

    @property
    def machine_name(self):
        return self.cfg.get("machineName") or "this computer"

    @property
    def base_url(self):
        return self.cfg.get("baseUrl")

    def snapshot_status(self):
        """A small dict the tray renders. Never blocks."""
        with self._lock:
            return {
                "status": self._status,
                "enrolled": bool(self._token),
                "machineName": self.machine_name,
                "baseUrl": self.base_url,
                "online": self._online,
                "enabled": self._enabled,
                "paused": self._paused,
                "kind": self._kind,
                "current": self._current,
                "signedIn": self.sign_in.signed_in,
                "signinCooldown": self.sign_in.seconds_remaining(),
                "lastError": self._last_error,
                "lastResult": self._last_result,
            }

    def _set(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, "_" + key, value)
        if self.ui:
            try:
                self.ui.on_state_changed()
            except Exception:
                pass

    # -- lifecycle --------------------------------------------------------

    def start(self):
        for target in (self._heartbeat_loop, self._work_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self):
        self._stop.set()

    def set_paused(self, paused):
        """Pause claiming only. Heartbeats continue so the machine still reads
        as online rather than looking broken."""
        self._set(paused=bool(paused))
        self._recompute_idle_status()

    def recheck_signin(self):
        """Clear a sign-in failure immediately, for when a human has just
        signed in and should not wait out the cooldown."""
        self.sign_in.clear()
        self._set(last_error="")
        return True

    # -- enrollment -------------------------------------------------------

    def enroll(self, code):
        """Redeem a pairing code. Returns (ok, message)."""
        code = (code or "").strip()
        if not code:
            return False, "Enter the code shown in EasyBIM."
        versions = machine.installed_revit_versions()
        result = self.api.enroll(
            code, self.cfg["agentId"], self.machine_name, versions,
            AGENT_VERSION)
        if result.ok and result.data.get("token"):
            token = result.data["token"]
            encrypted = config.save_token(token)
            self._token = token
            self.api.token = token
            if result.data.get("machineName"):
                self.cfg["machineName"] = result.data["machineName"]
                config.save_config(self.cfg)
            self._set(kind=result.data.get("kind"), last_error="",
                      status=STATUS_IDLE)
            self._log("enrolled as %s (kind=%s, token %s)"
                      % (self.machine_name, result.data.get("kind"),
                         "encrypted" if encrypted else "PLAINTEXT"))
            return True, "Connected to EasyBIM as %s." % self.machine_name
        if result.status == 401:
            return False, (result.data.get("error")
                           or "That code is invalid, already used, or expired.")
        if result.transport_failed:
            return False, ("Could not reach EasyBIM at %s. Check the address "
                           "and your connection." % self.base_url)
        return False, "Enrollment failed: %s" % result.message()

    # -- heartbeat --------------------------------------------------------

    def _heartbeat_loop(self):
        interval = HEARTBEAT_MIN_SEC
        while not self._stop.is_set():
            if not self._token:
                # Pick up an enrollment performed elsewhere. The documented way
                # to enroll a headless machine is `--enroll CODE` in a second
                # process while the agent runs as a scheduled task, so without
                # this re-read that agent would stay dormant until it restarted.
                found = config.load_token()
                if found:
                    self._log("picked up a token written by another process")
                    self._token = found
                    self.api.token = found
                    self._set(status=STATUS_IDLE, last_error="")
                else:
                    self._stop.wait(5)
                    continue
            snapshot = machine.take_snapshot(self.sign_in)
            result = self.api.heartbeat(snapshot, AGENT_VERSION)

            if result.ok:
                has_work = bool(result.data.get("hasWork"))
                enabled = result.data.get("enabled")
                self._set(online=True,
                          has_work=has_work,
                          enabled=True if enabled is None else bool(enabled),
                          kind=result.data.get("kind"),
                          last_error="")
                self._recompute_idle_status()
                if has_work:
                    interval = HEARTBEAT_MIN_SEC
                elif self._current:
                    interval = HEARTBEAT_BUSY_SEC
                else:
                    interval = min(int(interval * 1.5), HEARTBEAT_MAX_SEC)
            elif result.status == 401:
                # The token was revoked or the agent row was deleted.
                self._log("heartbeat unauthorized; clearing enrollment")
                config.clear_token()
                self._token = None
                self.api.token = None
                self._set(online=False, status=STATUS_NOT_ENROLLED,
                          last_error="This computer is no longer connected to "
                                     "EasyBIM. Connect it again.")
                interval = HEARTBEAT_MIN_SEC
            else:
                self._set(online=False, last_error=result.message())
                self._recompute_idle_status()
                interval = min(int(interval * 1.5), HEARTBEAT_MAX_SEC)

            self._stop.wait(interval)

    def _recompute_idle_status(self):
        with self._lock:
            if not self._token:
                new = STATUS_NOT_ENROLLED
            elif self._current:
                new = STATUS_RUNNING
            elif not self._enabled:
                new = STATUS_DISABLED
            elif self._paused:
                new = STATUS_PAUSED
            elif not self._online:
                new = STATUS_OFFLINE
            else:
                new = STATUS_IDLE
            changed = new != self._status
            self._status = new
        if changed and self.ui:
            try:
                self.ui.on_state_changed()
            except Exception:
                pass

    # -- work loop --------------------------------------------------------

    def _work_loop(self):
        while not self._stop.is_set():
            self._stop.wait(3)
            if self._stop.is_set():
                return
            with self._lock:
                ready = (self._token and self._enabled and not self._paused
                         and self._has_work and not self._current)
            if not ready:
                continue
            try:
                self._claim_and_run()
            except Exception as exc:
                self._log("work loop error: %r" % (exc,))
                self._set(last_error=str(exc))
            finally:
                self._set(current=None, has_work=False)
                self._recompute_idle_status()

    def _claim_and_run(self):
        result = self.api.claim()
        if result.status == 204:
            self._set(has_work=False)
            return
        if result.status == 409:
            # We already hold a run the server thinks is live; leave it to the
            # server's watchdog rather than starting a second Revit.
            self._log("claim conflict: agent already holds a run")
            return
        if not result.ok:
            self._set(last_error="Claim failed: %s" % result.message())
            return

        job = Job(result.data)
        if not job.run_id:
            return
        self._log("claimed run %s for %s" % (job.run_id, job.model_name))
        self._set(current=job.model_name, status=STATUS_RUNNING)
        self._run_job(job)

    # -- running one job --------------------------------------------------

    def _run_job(self, job):
        forwarder = _LogForwarder(self.api, job.run_id, self._log)
        forwarder.start()
        try:
            forwarder.step("open-revit", "running", "Starting Revit %s"
                           % job.revit_version)

            # Defensive re-check of the platform's enqueue refusal: a human can
            # open the target model in the seconds between enqueue and claim,
            # and colliding costs a workset-ownership tangle.
            #
            # Scoped to the TARGET MODEL, not to any running Revit. An earlier
            # version of this widened to "any Revit is open" after a run died
            # on "The License Manager is not functioning or is improperly
            # installed", which looked like two Revits fighting over a
            # single-user licence. That inference was wrong: the same failure
            # then reproduced with no other Revit running at all, and the
            # licensing service log showed the real cause both times —
            # "timed-out after 30.0 sec(s) waiting for Agent to connect",
            # i.e. AdskLicensingAgent.exe failing to answer inside Revit's
            # checkout window. Concurrency was incidental. Do not re-widen this
            # without evidence that concurrent Revits are themselves the fault.
            collision = machine.model_is_open(job.model_name)
            if collision:
                forwarder.line("error", "Revit is already open with %s on %s"
                               % (collision, self.machine_name))
                forwarder.step("open-revit", "failed", "Revit already open")
                forwarder.flush_now()
                self._complete(
                    job, "needs_attention",
                    "Revit is open with %s on %s — close it there, then run "
                    "again." % (job.model_name, self.machine_name),
                    forwarder)
                return

            if not self._await_takeover(job, forwarder):
                return

            runner = SyncRunner(
                job,
                on_line=forwarder.line,
                on_step=forwarder.step,
                on_keepalive=lambda phase, secs: forwarder.line(
                    "info", _keepalive_text(job, phase, secs)),
                logger=self._log)
            outcome = runner.execute()

            forwarder.flush_now()
            self._log("run %s finished: %s (%s)"
                      % (job.run_id, outcome.status, outcome.diagnostics))
            self._note_signin_evidence(outcome)
            self._complete(job, outcome.status, outcome.attention_reason,
                           forwarder, outcome.lines)
        finally:
            forwarder.stop()

    def _await_takeover(self, job, forwarder):
        """Warn before Revit steals the desktop; honour a defer.

        Headless (a shared workstation with nobody at the keyboard) proceeds
        immediately — there is no one to ask, and Mode B exists precisely for
        unattended runs.
        """
        if not self.ui or not getattr(self.ui, "can_prompt", False):
            return True
        for attempt in range(MAX_DEFERS + 1):
            last = attempt == MAX_DEFERS
            try:
                choice = self.ui.confirm_takeover(
                    job.model_name, TAKEOVER_WARNING_SEC, last)
            except Exception:
                return True
            if choice != "defer":
                return True
            if last:
                forwarder.line("warn", "Starting now — this run cannot be "
                                       "deferred any further.")
                return True
            minutes = int(DEFER_SEC / 60)
            forwarder.line("info", "Deferred %d minutes by the person at %s."
                           % (minutes, self.machine_name))
            forwarder.flush_now()
            deadline = time.time() + DEFER_SEC
            while time.time() < deadline:
                if self._stop.is_set():
                    return False
                # Keep feeding the server's watchdog while we wait, or the run
                # gets abandoned as stale for being polite.
                forwarder.line("info", "Waiting to start — deferred.")
                forwarder.flush_now()
                self._stop.wait(min(240, max(5, deadline - time.time())))
        return True

    def _note_signin_evidence(self, outcome):
        """The one hard signal that Autodesk sign-in is broken.

        The script's pre-flight checks IsLoggedIn and LoginUserId and returns
        needs_attention for exactly that, so a matching reason is treated as
        evidence and flips the heartbeat's optimistic default.
        """
        reason = (outcome.attention_reason or "").lower()
        if outcome.status == "needs_attention" and "sign" in reason \
                and "autodesk" in reason:
            self._log("run reported an Autodesk sign-in problem")
            self.sign_in.report_signin_failure()
            self._set(last_error="Revit is not signed in to Autodesk on this "
                                 "computer.")
            if self.ui:
                try:
                    self.ui.notify(
                        "Autodesk sign-in needed",
                        "Open Revit on this computer and sign in to Autodesk, "
                        "then choose 'Recheck Autodesk sign-in'.")
                except Exception:
                    pass

    def _complete(self, job, status, attention_reason, forwarder, lines=None):
        result = self.api.complete(job.run_id, status, attention_reason, lines)
        if result.ok:
            self._set(last_result="%s — %s" % (job.model_name, status))
            return
        if result.status == 409:
            # Already terminal. A retried complete must not rewrite an
            # outcome, so this is success from our side.
            self._log("run %s was already terminal" % job.run_id)
            self._set(last_result="%s — already reported" % job.model_name)
            return
        self._log("could not report run %s: %s" % (job.run_id, result.message()))
        self._set(last_error="Could not report the result: %s"
                             % result.message())


def _keepalive_text(job, phase, seconds):
    minutes = int(seconds / 60)
    if phase == "open-model":
        return ("Still opening %s — %d min so far. Large models take a while."
                % (job.model_name or "the model", minutes))
    return "Still working — %d min in %s." % (minutes, phase)


class _LogForwarder(object):
    """Batches lines and step changes to the log endpoint.

    Every successful call bumps the server's watchdog, so batching is a
    throughput measure, not a reason to go quiet: a step change flushes
    immediately because it is what the live step list renders.

    On a transport failure the batch is kept and retried, so a brief network
    blip does not silently lose the console. A 409 means the run already
    finished server-side, after which posting is pointless and stops.
    """

    def __init__(self, api, run_id, logger=None):
        self.api = api
        self.run_id = run_id
        self._log = logger or (lambda *_a, **_k: None)
        self._lines = []
        self._steps = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._closed = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
        self.flush_now()

    def line(self, level, text):
        if self._closed or not text:
            return
        with self._lock:
            self._lines.append({"level": level, "text": str(text)[:2000]})
            over = len(self._lines) >= LOG_BATCH_MAX
        if over:
            self.flush_now()

    def step(self, key, status, message=""):
        if self._closed or not key:
            return
        with self._lock:
            self._steps.append({"key": key, "status": status,
                                "message": message or ""})
        self.flush_now()

    def _take(self):
        with self._lock:
            lines, self._lines = self._lines, []
            steps, self._steps = self._steps, []
        return lines, steps

    def _restore(self, lines, steps):
        with self._lock:
            self._lines = lines + self._lines
            self._steps = steps + self._steps

    def flush_now(self):
        if self._closed:
            return
        lines, steps = self._take()
        if not lines and not steps:
            return
        # Steps go one per call so their order is preserved server-side; lines
        # ride along with the first.
        pending = steps or [None]
        for index, step in enumerate(pending):
            batch = lines if index == 0 else None
            result = self.api.log(self.run_id, batch, step)
            if result.status == 409:
                self._log("run %s already finished; stopping log forwarding"
                          % self.run_id)
                self._closed = True
                return
            if not result.ok:
                self._restore(lines if index == 0 else [],
                              list(pending[index:]) if steps else [])
                return

    def _loop(self):
        while not self._stop.is_set():
            self._stop.wait(LOG_FLUSH_SEC)
            try:
                self.flush_now()
            except Exception as exc:
                self._log("log flush error: %r" % (exc,))
