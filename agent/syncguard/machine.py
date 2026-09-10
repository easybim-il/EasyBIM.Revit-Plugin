"""Local probes: what Revit is installed, whether it's running, what's open.

Everything here is read-only inspection of the machine. It has to work without
launching Revit, which is what makes some of it indirect.

*** FINDINGS THAT SHAPE THIS FILE — read before changing it ***

WHAT IS OPEN comes from the Revit journal, filtered hard. The journal also
contains Revit's recent-files list, which on this machine was 1087 `CLD:` cloud
paths spanning a dozen unrelated projects. Reporting those as "open" would refuse
essentially every run, because the platform refuses an enqueue when the target
model appears in openModels. The genuine signal is the `Rvt.Attr.` attribute
block Revit writes when it actually opens a document; filtering on that yielded
exactly the eight models of one live session (a coordination host plus its seven
discipline links) instead of the whole machine's history.

It over-reports in two known ways, both accepted deliberately because a false
refusal costs one click while a missed collision costs a workset-ownership
tangle mid-sync:
  * links count as open, since a loaded link logs the same attribute block as
    the host. Syncing a model that is currently a link elsewhere is actually
    harmless, so this can cause a spurious refusal.
  * a model opened and then closed within the session still counts, because the
    journal does not clearly mark closes.
The refinement, if false refusals become annoying: the collaboration cache keeps
links under a `LinkedModels/` subfolder and the host local file directly in the
project folder, so host-vs-link is recoverable — but it needs a GUID-to-name
correlation this does not currently do.

openModels MUST BE MODEL-NAME FRAGMENTS, NOT GUIDS. The platform refuses with
`model.name.toLowerCase().includes(m.toLowerCase())`, so a GUID never matches and
a full `Autodesk Docs://project/name.rvt` path never matches either — only the
basename does.

AUTODESK SIGN-IN CANNOT BE KNOWN from outside Revit. What was checked:
  * `%LOCALAPPDATA%\\Autodesk\\Web Services\\LoginState.xml` records only a
    `LogoutDate`. On this machine it read 2026-08-18 while Revit was demonstrably
    signed in on 09-07, so it tracks the last explicit sign-out and would
    actively mislead.
  * `WebServicesCache.xml` under the licensing agent holds the identity (id,
    username, email) but is a stale cache — months old — with no validity signal.
  * The collaboration cache is keyed by Autodesk user id, which is a reliable
    *identity* signal.
So identity is knowable and validity is not. See SignInState for how that is
handled without wedging the platform.
"""

import ctypes
import glob
import os
import re
import time
from ctypes import wintypes

REVIT_INSTALL_GLOB = r"C:\Program Files\Autodesk\Revit *"
REVIT_JOURNAL_GLOB = (
    r"%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit *\Journals\journal.*.txt")
COLLAB_CACHE_GLOB = (
    r"%LOCALAPPDATA%\Autodesk\Revit\Autodesk Revit *\CollaborationCache")
WEB_SERVICES_CACHE_GLOB = (
    r"%LOCALAPPDATA%\Autodesk\Web Services\AdskLicensingAgent\*"
    r"\WebServicesCache.xml")

# A journal touched within this window belongs to a live session. Revit writes
# to it continuously, so this is generous rather than tight.
JOURNAL_LIVE_WINDOW_SEC = 15 * 60

# Only the tail of a journal is read: they reach hundreds of MB on a long
# session, and the models opened are logged as they happen.
JOURNAL_TAIL_BYTES = 6 * 1024 * 1024

_OPEN_MARKER = "Rvt.Attr.RevitBuildVersion"
_DOCS_PATH_RE = re.compile(
    r"(Autodesk Docs://.*?\.rvt) " + re.escape(_OPEN_MARKER))


def _expand(pattern):
    return os.path.expandvars(pattern)


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------

TH32CS_SNAPPROCESS = 0x00000002


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def running_process_names():
    """Lowercased executable names of running processes.

    ctypes rather than `tasklist` so a tray app never flashes a console window.
    """
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return set()
    names = set()
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return names
        while True:
            names.add(entry.szExeFile.decode("mbcs", "replace").lower())
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def running_pids_by_name(exe_name):
    """PIDs of running processes matching `exe_name` (case-insensitive)."""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return []
    wanted = exe_name.lower()
    pids = []
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return pids
        while True:
            if entry.szExeFile.decode("mbcs", "replace").lower() == wanted:
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return pids


def revit_pids():
    """PIDs of running Revit instances.

    Used to bound what a timeout is allowed to kill: only Revit processes that
    appeared after the agent launched its own run are fair game, so an
    engineer's own open Revit is never taken down.
    """
    return running_pids_by_name("Revit.exe")


def revit_running():
    return "revit.exe" in running_process_names()


# ---------------------------------------------------------------------------
# Installed versions
# ---------------------------------------------------------------------------

def installed_revit_versions():
    """Year strings for each installed Revit, e.g. ['2023', '2024', '2025'].

    Requires the actual executable: the install folders outlive an uninstall,
    and claiming a version we cannot launch would turn an enqueue into a
    mid-run failure.
    """
    versions = []
    for folder in glob.glob(REVIT_INSTALL_GLOB):
        year = os.path.basename(folder).split()[-1]
        if not year.isdigit():
            continue
        if os.path.isfile(os.path.join(folder, "Revit.exe")):
            versions.append(year)
    return sorted(set(versions))


# ---------------------------------------------------------------------------
# Open models
# ---------------------------------------------------------------------------

def _live_journals():
    journals = []
    for path in glob.glob(_expand(REVIT_JOURNAL_GLOB)):
        try:
            age = time.time() - os.path.getmtime(path)
        except OSError:
            continue
        if age <= JOURNAL_LIVE_WINDOW_SEC:
            journals.append(path)
    return journals


def open_cloud_models():
    """Basenames of cloud models the live Revit session(s) have opened.

    Empty when Revit is not running: the platform only consults this alongside
    revitRunning, and a stale list would refuse runs for no reason.
    """
    if not revit_running():
        return []

    names = set()
    for journal in _live_journals():
        try:
            size = os.path.getsize(journal)
            with open(journal, "rb") as handle:
                if size > JOURNAL_TAIL_BYTES:
                    handle.seek(size - JOURNAL_TAIL_BYTES)
                blob = handle.read()
        except OSError:
            continue
        text = blob.decode("utf-8", "replace")
        for match in _DOCS_PATH_RE.finditer(text):
            full = match.group(1)
            base = full.rsplit("/", 1)[-1].strip()
            if base:
                names.add(base)
    return sorted(names)


# ---------------------------------------------------------------------------
# Autodesk identity
# ---------------------------------------------------------------------------

def autodesk_user():
    """Best-effort Autodesk identity, without launching Revit.

    The collaboration cache is preferred: its per-user folder name is the
    Autodesk user id, and it was verified to match exactly what the Revit API
    reports as LoginUserId. Falls back to the licensing agent's cached
    username, which is friendlier to read but can be months stale.
    """
    newest = None
    newest_mtime = -1.0
    for cache in glob.glob(_expand(COLLAB_CACHE_GLOB)):
        try:
            entries = os.listdir(cache)
        except OSError:
            continue
        for entry in entries:
            folder = os.path.join(cache, entry)
            if not os.path.isdir(folder):
                continue
            try:
                mtime = os.path.getmtime(folder)
            except OSError:
                continue
            if mtime > newest_mtime:
                newest_mtime = mtime
                newest = entry
    if newest:
        return newest

    for path in glob.glob(_expand(WEB_SERVICES_CACHE_GLOB)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                blob = handle.read(8192)
        except OSError:
            continue
        found = re.search(r"<UserName>([^<]+)</UserName>", blob)
        if found:
            return found.group(1).strip()
    return ""


class SignInState(object):
    """Optimistic-with-evidence Autodesk sign-in reporting.

    Validity genuinely cannot be read from outside Revit (see the module
    docstring), so the default is True and the only hard evidence is a run whose
    pre-flight failed on sign-in — the script checks IsLoggedIn and LoginUserId
    and returns needs_attention for exactly that.

    THE COOLDOWN IS NOT COSMETIC. The platform's enqueue route hard-refuses when
    autodeskSignedIn is false ("sign in there first"), so a stuck false means no
    run can ever be claimed — and therefore no run can ever prove sign-in works
    again. Reporting false permanently would wedge the agent after a single
    sign-in failure. So false always expires, and the tray offers an immediate
    clear once a human has actually signed in.
    """

    def __init__(self, cooldown_sec=20 * 60):
        self._cooldown_sec = cooldown_sec
        self._failed_at = None

    def report_signin_failure(self):
        self._failed_at = time.time()

    def clear(self):
        self._failed_at = None

    @property
    def signed_in(self):
        if self._failed_at is None:
            return True
        if time.time() - self._failed_at >= self._cooldown_sec:
            self._failed_at = None
            return True
        return False

    def seconds_remaining(self):
        if self._failed_at is None:
            return 0
        left = self._cooldown_sec - (time.time() - self._failed_at)
        return int(left) if left > 0 else 0


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class Snapshot(object):
    """One heartbeat's worth of machine state."""

    __slots__ = ("revit_versions", "autodesk_signed_in", "autodesk_user",
                 "revit_running", "open_models")

    def __init__(self, revit_versions, autodesk_signed_in, autodesk_user,
                 revit_running, open_models):
        self.revit_versions = revit_versions
        self.autodesk_signed_in = autodesk_signed_in
        self.autodesk_user = autodesk_user
        self.revit_running = revit_running
        self.open_models = open_models


def take_snapshot(sign_in_state):
    return Snapshot(
        revit_versions=installed_revit_versions(),
        autodesk_signed_in=sign_in_state.signed_in,
        autodesk_user=autodesk_user(),
        revit_running=revit_running(),
        open_models=open_cloud_models(),
    )


def model_is_open(model_name, open_models=None):
    """Does an open model collide with `model_name`?

    Mirrors the platform's own comparison so the agent's defensive re-check at
    claim time agrees with the enqueue-time refusal instead of contradicting it.
    """
    if not model_name:
        return None
    haystack = model_name.lower()
    for candidate in (open_models if open_models is not None
                      else open_cloud_models()):
        if candidate and candidate.lower() in haystack:
            return candidate
    return None
