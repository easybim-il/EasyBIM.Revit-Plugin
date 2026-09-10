"""Agent identity, settings and credential storage.

State lives under %APPDATA%\\EasyBIM\\Syncguard — per-user, matching the
convention lib/easybim/coordination_settings.py documents for per-machine state,
and correct here for a second reason: the agent runs in a logon session whose
Autodesk sign-in and Revit licence are themselves per-profile.

    config.json     baseUrl, agentId, machineName        (plain, inspectable)
    token.bin       the bearer token, DPAPI-encrypted    (never plain if avoidable)
    runs/<runId>/   result.json + progress.ndjson        (absolute, outside TEMP)
    agent.log       rolling local log

`agentId` is generated once and kept for the life of the install: the platform
keys the agent row on it, so regenerating would orphan the enrollment.

Run artefacts must live here rather than anywhere under TEMP. Inside Revit,
%TEMP% is redirected to the per-run folder that `pyrevit run --purge` deletes, so
a result file written there vanishes before it can be read.
"""

import ctypes
import json
import os
import socket
import uuid
from ctypes import wintypes

DEFAULT_BASE_URL = "http://localhost:3002"

_APP_DIR_NAME = os.path.join("EasyBIM", "Syncguard")


def state_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, _APP_DIR_NAME)


def _path(name):
    return os.path.join(state_dir(), name)


def ensure_state_dir():
    folder = state_dir()
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return folder


def run_dir(run_id):
    """Absolute, stable directory for one run's artefacts."""
    folder = os.path.join(state_dir(), "runs", str(run_id))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return folder


def log_path():
    return _path("agent.log")


# ---------------------------------------------------------------------------
# DPAPI — encrypt the token to the Windows user
# ---------------------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _blob_bytes(blob):
    return ctypes.string_at(blob.pbData, blob.cbData)


_CRYPT32 = getattr(ctypes.windll, "crypt32", None)
_KERNEL32 = ctypes.windll.kernel32
_DESCRIPTION = "EasyBIM Syncguard agent token"


def _protect(raw):
    """DPAPI-encrypt, or return None if unavailable."""
    if _CRYPT32 is None:
        return None
    src, _keep = _blob(raw)
    out = _Blob()
    ok = _CRYPT32.CryptProtectData(
        ctypes.byref(src), _DESCRIPTION, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return _blob_bytes(out)
    finally:
        _KERNEL32.LocalFree(out.pbData)


def _unprotect(blob_bytes):
    if _CRYPT32 is None:
        return None
    src, _keep = _blob(blob_bytes)
    out = _Blob()
    ok = _CRYPT32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return _blob_bytes(out)
    finally:
        _KERNEL32.LocalFree(out.pbData)


_TOKEN_FILE = "token.bin"
_TOKEN_PLAIN_MARKER = b"PLAIN:"


def save_token(token):
    """Store the bearer token, DPAPI-encrypted where possible.

    Falls back to a marked plaintext file rather than refusing to enroll: a
    readable token in the user's own profile is the same exposure as most local
    credential caches, and a hard failure here would leave the agent unusable.
    """
    ensure_state_dir()
    raw = token.encode("utf-8")
    sealed = _protect(raw)
    with open(_path(_TOKEN_FILE), "wb") as handle:
        handle.write(sealed if sealed is not None else _TOKEN_PLAIN_MARKER + raw)
    return sealed is not None


def load_token():
    try:
        with open(_path(_TOKEN_FILE), "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if not data:
        return None
    if data.startswith(_TOKEN_PLAIN_MARKER):
        return data[len(_TOKEN_PLAIN_MARKER):].decode("utf-8", "replace")
    raw = _unprotect(data)
    if raw is None:
        return None
    return raw.decode("utf-8", "replace")


def clear_token():
    try:
        os.remove(_path(_TOKEN_FILE))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# config.json
# ---------------------------------------------------------------------------

def load_config():
    """Read config.json, filling in defaults and persisting a new agentId once."""
    data = {}
    try:
        with open(_path("config.json"), "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        data = {}

    changed = False
    if not data.get("agentId"):
        data["agentId"] = str(uuid.uuid4())
        changed = True
    if not data.get("machineName"):
        data["machineName"] = (
            os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown")
        changed = True
    if not data.get("baseUrl"):
        data["baseUrl"] = os.environ.get("SYNCGUARD_BASE_URL", DEFAULT_BASE_URL)
        changed = True
    if changed:
        save_config(data)
    return data


def save_config(data):
    ensure_state_dir()
    target = _path("config.json")
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(temp, target)
    return target
