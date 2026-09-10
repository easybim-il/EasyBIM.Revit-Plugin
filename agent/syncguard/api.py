"""HTTP client for the five agent endpoints. Outbound only, stdlib only.

Expected non-2xx codes are returned rather than raised, because several of them
are normal control flow rather than errors:

    claim    -> 204  nothing queued for you; the common case
             -> 409  you already hold a run
    log      -> 409  the run already finished
    complete -> 409  already terminal; a retried complete must not rewrite an
                     outcome, so this is success from our side
    enroll   -> 401  bad / used / expired pairing code

Only `complete` retries persistently. A dropped heartbeat or claim costs nothing
because the next tick repeats it, but losing a completed run's outcome would
leave the card spinning until the server's stale-claim watchdog gives up 45
minutes later.
"""

import json
import time
import urllib.error
import urllib.request

TIMEOUT_DEFAULT = 30
TIMEOUT_LOG = 20


class ApiResult(object):
    """One HTTP outcome: a status code, a parsed body, or a transport error."""

    def __init__(self, status=0, data=None, error=None):
        self.status = status
        self.data = data or {}
        self.error = error

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def transport_failed(self):
        return self.status == 0

    def message(self):
        if self.error:
            return str(self.error)
        if isinstance(self.data, dict) and self.data.get("error"):
            return str(self.data["error"])
        return "HTTP %d" % self.status


class SyncguardApi(object):
    def __init__(self, base_url, token=None, logger=None):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self._log = logger or (lambda *_a, **_k: None)

    # -- plumbing ---------------------------------------------------------

    def _post(self, path, payload, authed=True, timeout=TIMEOUT_DEFAULT):
        url = self.base_url + path
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if authed:
            if not self.token:
                return ApiResult(error="not enrolled: no token")
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                status = response.getcode()
                if not raw:
                    return ApiResult(status, {})
                try:
                    return ApiResult(status, json.loads(raw.decode("utf-8")))
                except ValueError:
                    return ApiResult(status, {})
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:
                pass
            data = {}
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    data = {}
            return ApiResult(exc.code, data)
        except Exception as exc:
            # URLError, socket timeout, TLS failure, DNS, a closed laptop lid.
            return ApiResult(error=exc)

    def _post_with_retries(self, path, payload, attempts=5,
                           timeout=TIMEOUT_DEFAULT):
        delay = 2.0
        result = None
        for attempt in range(1, attempts + 1):
            result = self._post(path, payload, timeout=timeout)
            # Retry only transport failures and 5xx. A 4xx is a verdict.
            if not result.transport_failed and result.status < 500:
                return result
            if attempt < attempts:
                self._log("retrying %s in %.0fs (%s)"
                          % (path, delay, result.message()))
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        return result

    # -- endpoints --------------------------------------------------------

    def enroll(self, code, agent_id, machine_name, revit_versions,
               agent_version):
        # Uppercased to match the server, which normalises the code before
        # lookup; a lowercase paste would otherwise read as invalid.
        return self._post("/api/syncguard/agent/enroll", {
            "code": (code or "").strip().upper(),
            "agentId": agent_id,
            "machineName": machine_name,
            "revitVersions": revit_versions,
            "agentVersion": agent_version,
        }, authed=False)

    def heartbeat(self, snapshot, agent_version):
        return self._post("/api/syncguard/agent/heartbeat", {
            "revitVersions": snapshot.revit_versions,
            "autodeskSignedIn": snapshot.autodesk_signed_in,
            "autodeskUser": snapshot.autodesk_user,
            "revitRunning": snapshot.revit_running,
            "openModels": snapshot.open_models,
            "agentVersion": agent_version,
        })

    def claim(self):
        return self._post("/api/syncguard/agent/claim", {})

    def log(self, run_id, lines=None, step=None):
        payload = {}
        if lines:
            payload["lines"] = lines
        if step:
            payload["step"] = step
        if not payload:
            return ApiResult(200, {})
        return self._post("/api/syncguard/agent/runs/%s/log" % run_id,
                          payload, timeout=TIMEOUT_LOG)

    def complete(self, run_id, status, attention_reason=None, lines=None):
        payload = {"status": status}
        if attention_reason:
            payload["attentionReason"] = attention_reason
        if lines:
            payload["lines"] = lines
        return self._post_with_retries(
            "/api/syncguard/agent/runs/%s/complete" % run_id, payload)
