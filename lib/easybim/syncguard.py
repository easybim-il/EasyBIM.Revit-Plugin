# -*- coding: utf-8 -*-
"""Worksharing core for Syncguard: sync a workshared model and relinquish everything.

Shared by two entry points that differ only in how they get a document and where
they report to:

  * ``commands/syncguard_command.py`` -- headless, driven by ``pyrevit run`` from
    the Syncguard tray agent. Resolves cloud GUIDs, opens the model itself, and
    writes the result JSON contract that the agent consumes.
  * ``EasyBIM.tab/Manage.panel/Syncguard.pushbutton`` -- interactive. Operates on
    the document the engineer already has open and prints to the pyRevit output
    window.

Everything that touches worksharing lives here so both paths exercise the same
code: the failure handler, the sync options, the two callbacks, the ownership
sweep and the error taxonomy.

*** NEW REVIT-API GROUND, FLAGGED FOR VALIDATION ***
Before this module the repo contained no worksharing, cloud-path, workset-config
or Revit-API-event code at all. Notes on the non-obvious decisions:

  * ENGINE: no shebang, so this runs on IronPython 2.7. That is not stylistic.
    Two of the classes below subclass .NET *interfaces* from Python, which needs
    CLR type synthesis; pyRevit fences that pattern off from its CPython engine
    (see pyrevitlib/pyrevit/revit/events.py). Verified working on IronPython
    2.7.12 under ``pyrevit run``.
  * RELINQUISH IS FOLDED INTO THE SYNC via SetRelinquishOptions rather than a
    separate WorksharingUtils.RelinquishOwnership call -- one operation, one
    less failure mode. RelinquishOptions(True) covers the complete member set
    (CheckedOutElements, UserWorksets, StandardWorksets, ViewWorksets,
    FamilyWorksets) and runs after the save-to-central, so modified elements are
    already committed and therefore releasable.
  * SaveLocalAfter IS LOAD-BEARING. Omitting it is the documented cause of
    CentralModelException "Local incompatible because it was closed without
    saving after synchronizing with central", which poisons the local cache for
    every later run.
  * THE LOCK CALLBACK IS NOT OPTIONAL for unattended runs. With no callback,
    Revit's default on a locked central is to wait *indefinitely* rather than
    raise -- an unbounded headless hang. See LockWaitCallback.
  * IFailuresPreprocessor IS DELIBERATELY NOT USED. It attaches to a transaction
    you own, and nobody owns SynchronizeWithCentral's internal transactions. The
    Application-level FailuresProcessing event is the only route.
"""

import codecs
import json
import os
import time

from pyrevit import DB, UI, framework


# ---------------------------------------------------------------------------
# Result vocabulary -- these strings are the Phase 4 contract. Do not rename.
# ---------------------------------------------------------------------------

STATUS_SUCCESS = u"success"
STATUS_WARNING = u"warning"
STATUS_FAILED = u"failed"
STATUS_NEEDS_ATTENTION = u"needs_attention"

STEP_OPEN_MODEL = u"open-model"
STEP_SYNC_CENTRAL = u"sync-central"

STEP_PENDING = u"pending"
STEP_RUNNING = u"running"
STEP_DONE = u"done"
STEP_FAILED = u"failed"

DEFAULT_COMMENT = u"Syncguard automated sync"

# ``lines`` is capped so a warning-heavy model cannot produce an unbounded
# result file. The cap is reported rather than silently applied.
MAX_LINES = 500

# The lock callback is invoked repeatedly by Revit for as long as central stays
# locked. Emitting a progress line on literally every call would flood ``lines``
# and bury everything useful, so lock-wait notices are throttled to this
# interval -- frequent enough that the agent never mistakes a legitimate wait
# for the silence that means a hang.
LOCK_NOTICE_INTERVAL_SEC = 15


def format_duration(seconds):
    """Human duration for step messages, e.g. ``3m 51s``."""
    total = int(round(seconds or 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return u"%dh %dm %ds" % (hours, minutes, secs)
    if minutes:
        return u"%dm %ds" % (minutes, secs)
    return u"%ds" % (secs,)


def _utc_now():
    return unicode(time.strftime(u"%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _safe_text(value):
    try:
        return unicode(value)
    except Exception:
        try:
            return unicode(str(value), u"utf-8", u"replace")
        except Exception:
            return u"<unprintable>"


# ---------------------------------------------------------------------------
# Recorder -- builds the ``lines`` / ``steps`` halves of the contract
# ---------------------------------------------------------------------------

class Recorder(object):
    """Accumulates the result contract's ``lines`` and ``steps``.

    Also mirrors every line into an append-only NDJSON file when a path is
    given. That file is the only live progress channel that exists headlessly:
    pyRevit's ``print`` goes to its output window, which nothing captures under
    ``pyrevit run``, so without it a long sync looks identical to a hang.

    ``echo`` is an optional callable (the button passes a writer backed by the
    pyRevit output window) so the interactive path shows the same stream.
    """

    def __init__(self, progress_path=None, echo=None):
        self.lines = []
        self.lines_truncated = False
        self._steps = []
        self._progress_path = progress_path
        self._echo = echo

    # -- steps ------------------------------------------------------------

    def declare_step(self, key, status=STEP_PENDING, message=u""):
        """Register a step up front so one that never ran is still reported."""
        self._steps.append({u"key": key, u"status": status, u"message": message})
        self._emit(u"step", {u"key": key, u"status": status,
                             u"message": message})

    def set_step(self, key, status, message=None):
        for step in self._steps:
            if step[u"key"] == key:
                step[u"status"] = status
                if message is not None:
                    step[u"message"] = message
                self._emit(u"step", {u"key": key, u"status": status,
                                     u"message": step[u"message"]})
                return
        self._steps.append(
            {u"key": key, u"status": status, u"message": message or u""})
        self._emit(u"step", {u"key": key, u"status": status,
                             u"message": message or u""})

    def steps(self):
        return [dict(step) for step in self._steps]

    def fail_running_steps(self, message):
        """Mark any in-flight step failed, so an aborted run does not report a
        step as still ``running``."""
        for step in self._steps:
            if step[u"status"] == STEP_RUNNING:
                step[u"status"] = STEP_FAILED
                step[u"message"] = _safe_text(message)

    # -- lines ------------------------------------------------------------

    def line(self, level, text):
        """Record one log line. ``level`` is info | warn | error."""
        entry = {u"level": level, u"text": _safe_text(text)}
        if len(self.lines) < MAX_LINES:
            self.lines.append(entry)
        else:
            self.lines_truncated = True
        self._emit(u"line", entry)
        if self._echo:
            try:
                self._echo(entry[u"level"], entry[u"text"])
            except Exception:
                pass

    def info(self, text):
        self.line(u"info", text)

    def warn(self, text):
        self.line(u"warn", text)

    def error(self, text):
        self.line(u"error", text)

    def warning_count(self):
        return len([e for e in self.lines if e[u"level"] == u"warn"])

    def error_count(self):
        return len([e for e in self.lines if e[u"level"] == u"error"])

    def _emit(self, kind, payload):
        """Append one NDJSON record, flushed.

        Two kinds, matching the seam documented in SYNCGUARD_PLAN.md so the tray
        agent forwards rather than translates:

            {"t": ..., "kind": "line", "level": ..., "text": ...}
            {"t": ..., "kind": "step", "key": ..., "status": ..., "message": ...}

        Step events matter beyond tidiness: the agent's timeout has to be
        phase-aware, because the model open blocks for many minutes and emits
        nothing while it works. The open-model running/done transitions are the
        only signal that lets the agent tell a slow open from a hang.

        Opened and closed per record rather than holding a handle: events are
        rare, it avoids a Windows locking fight with the tailing agent, and
        everything up to the last event survives a taskkill -- so a timeout can
        report *where* the run hung.

        Best-effort by design -- progress reporting must never be the reason a
        sync fails.
        """
        if not self._progress_path:
            return
        try:
            folder = os.path.dirname(self._progress_path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            record = dict(payload)
            record[u"kind"] = kind
            record[u"t"] = _utc_now()
            with codecs.open(self._progress_path, u"a", u"utf-8") as handle:
                handle.write(json.dumps(
                    record, ensure_ascii=False, sort_keys=True) + u"\n")
                handle.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Callbacks -- .NET interfaces implemented in Python (IronPython only)
# ---------------------------------------------------------------------------

class LockWaitCallback(DB.ICentralLockedCallback):
    """Bounds how long a sync will wait for a locked central.

    With no callback installed, Revit waits indefinitely for the central lock
    instead of raising. Unattended that is an unbounded hang, and it is the most
    likely way an automated sync dies. Returning False past a deadline converts
    it into a prompt CentralModelContentionException, which the taxonomy below
    classifies as ``failed`` -- i.e. retry later, which is the truth.

    ``deadline_sec=None`` means wait forever. The interactive button passes that
    deliberately: an engineer sitting at their desk would rather keep waiting
    than be told the run failed. Only the headless entry sets a real deadline,
    and it must stay below the agent's silence timeout so the innermost bound
    fires first.
    """

    __namespace__ = "EasyBIMSyncguard"

    def __init__(self, recorder, deadline_sec=None):
        self._recorder = recorder
        self._deadline_sec = deadline_sec
        self._start = time.time()
        self._last_notice = 0.0
        self.wait_calls = 0
        self.gave_up = False

    def ShouldWaitForLockAvailability(self):
        self.wait_calls += 1
        waited = time.time() - self._start
        if self._deadline_sec is not None and waited >= self._deadline_sec:
            self.gave_up = True
            self._recorder.warn(
                u"central still locked after %s, giving up"
                % format_duration(waited))
            return False
        if self.wait_calls == 1 \
                or (waited - self._last_notice) >= LOCK_NOTICE_INTERVAL_SEC:
            self._last_notice = waited
            self._recorder.info(
                u"waiting for central lock (%s)" % format_duration(waited))
        return True


class CloudOpenCallback(DB.IOpenFromCloudCallback):
    """Answers Revit's open-time cloud conflict prompt without a human.

    Headless with no callback the journal's auto-action picks, and two of the
    four possible answers are actively harmful: DetachFromCentral produces a
    model that cannot sync at all, and KeepLocalChanges would commit whatever a
    previously crashed or timed-out run left behind in the local cache.

    Syncguard's answer is unambiguous -- discard the local and take the latest
    central state -- because the whole point of the feature is committing
    freshly reloaded link state, never resurrecting a stale local. Note this is
    exactly the state a timed-out run leaves behind, so it will be hit in
    practice rather than being theoretical.

    VersionArchived is the exception: nothing automatic can fix it, so cancel
    and let the caller report needs_attention.
    """

    __namespace__ = "EasyBIMSyncguard"

    def __init__(self, recorder):
        self._recorder = recorder
        self.conflicts = []
        self.cancelled_on = None

    def OnOpenConflict(self, scenario):
        name = _safe_text(scenario)
        self.conflicts.append(name)
        if scenario == DB.OpenConflictScenario.VersionArchived:
            self.cancelled_on = name
            self._recorder.error(
                u"cloud open conflict '%s' cannot be resolved automatically; "
                u"cancelling open" % name)
            return DB.OpenConflictResult.Cancel
        self._recorder.warn(
            u"cloud open conflict '%s' - discarding local changes and opening "
            u"the latest version" % name)
        return DB.OpenConflictResult.DiscardLocalChangesAndOpenLatestVersion


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class FailureWatcher(object):
    """Subscribes Revit's failure queue for the duration of a block.

    Revit's failure queue is separate from modal dialogs, so suppressing dialogs
    (which ``pyrevit run`` does by default) is not enough -- an unhandled
    failure during a sync can block it or silently roll it back.

    Use as a context manager so the handlers are always detached; a delegate
    still wired to a Revit event after the script scope is gone crashes Revit at
    shutdown. Strong references to the delegates are held for the same reason in
    reverse: without them the CLR-side wrapper can be collected mid-sync.

    DialogBoxShowing is recorded but never overridden. Dialog suppression is
    already handled by the journal, and blanket-cancelling dialogs risks
    cancelling something wanted; recording the ids is how we learn which ones
    actually appear.
    """

    def __init__(self, recorder):
        self.recorder = recorder
        self.warnings_recorded = 0
        self.hard_errors = []
        self.dialogs = []
        self.handler_errors = []
        self.failure_events = 0
        self.phase = u"open"
        self._app = None
        self._uiapp = None
        self._failure_handler = None
        self._dialog_handler = None

    def set_phase(self, phase):
        self.phase = phase

    def __enter__(self):
        from pyrevit import HOST_APP
        self._app = HOST_APP.app
        self._uiapp = HOST_APP.uiapp
        # FailuresProcessing is owned by the DB Application, not UIApplication.
        try:
            self._failure_handler = framework.EventHandler[
                DB.Events.FailuresProcessingEventArgs](self._on_failures)
            self._app.FailuresProcessing += self._failure_handler
        except Exception as ex:
            self._failure_handler = None
            self.recorder.warn(
                u"could not subscribe FailuresProcessing, sync-time failures "
                u"will go unrecorded: %s" % _safe_text(ex))
        try:
            if self._uiapp is not None:
                self._dialog_handler = framework.EventHandler[
                    UI.Events.DialogBoxShowingEventArgs](self._on_dialog)
                self._uiapp.DialogBoxShowing += self._dialog_handler
        except Exception as ex:
            self._dialog_handler = None
            self.recorder.warn(
                u"could not subscribe DialogBoxShowing: %s" % _safe_text(ex))
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        if self._failure_handler is not None:
            try:
                self._app.FailuresProcessing -= self._failure_handler
            except Exception:
                pass
            self._failure_handler = None
        if self._dialog_handler is not None:
            try:
                self._uiapp.DialogBoxShowing -= self._dialog_handler
            except Exception:
                pass
            self._dialog_handler = None
        return False

    @property
    def subscribed(self):
        return self._failure_handler is not None

    def _on_dialog(self, sender, args):
        try:
            self.dialogs.append({
                u"phase": self.phase,
                u"dialogId": _safe_text(getattr(args, u"DialogId", u"")),
                u"message": _safe_text(getattr(args, u"Message", u"") or u""),
            })
        except Exception:
            pass

    def _on_failures(self, sender, args):
        """Record, dismiss warnings, try to resolve errors, let the sync go on.

        Never raises: an exception escaping a Revit event handler is far worse
        than a missed log line.
        """
        try:
            self.failure_events += 1
            accessor = args.GetFailuresAccessor()
            severity_none = getattr(DB.FailureSeverity, u"None")
            if accessor.GetSeverity() == severity_none:
                args.SetProcessingResult(DB.FailureProcessingResult.Continue)
                return

            # Materialise the collection before mutating anything through the
            # accessor -- resolving or deleting invalidates a live enumeration.
            messages = list(accessor.GetFailureMessages())
            saw_warning = False
            resolved_any = False
            unresolvable = []

            for message in messages:
                record = self._describe(message)
                try:
                    severity = message.GetSeverity()
                except Exception:
                    severity = None

                if severity == DB.FailureSeverity.Warning:
                    saw_warning = True
                    self.warnings_recorded += 1
                    self.recorder.warn(u"[%s] %s" % (self.phase, record[u"text"]))
                    continue

                # Error or DocumentCorruption.
                if self._try_resolve(accessor, message):
                    resolved_any = True
                    record[u"resolved"] = True
                    self.recorder.warn(
                        u"[%s] resolved: %s" % (self.phase, record[u"text"]))
                else:
                    record[u"resolved"] = False
                    unresolvable.append(record)
                    self.hard_errors.append(record)
                    self.recorder.error(
                        u"[%s] unresolved: %s" % (self.phase, record[u"text"]))

            if saw_warning:
                try:
                    accessor.DeleteAllWarnings()
                except Exception as ex:
                    self.recorder.warn(
                        u"could not dismiss warnings: %s" % _safe_text(ex))

            # Continue, never ProceedWithRollBack: a silent rollback returns a
            # success-looking result with an unsynced model. Letting Revit's own
            # handling run means SynchronizeWithCentral raises something the
            # taxonomy can classify honestly.
            if unresolvable:
                args.SetProcessingResult(DB.FailureProcessingResult.Continue)
            elif resolved_any:
                args.SetProcessingResult(
                    DB.FailureProcessingResult.ProceedWithCommit)
            else:
                args.SetProcessingResult(DB.FailureProcessingResult.Continue)
        except Exception as ex:
            detail = _safe_text(ex)
            self.handler_errors.append(detail)
            try:
                self.recorder.warn(u"failure handler error: %s" % detail)
            except Exception:
                pass

    def _describe(self, message):
        record = {u"phase": self.phase}
        try:
            record[u"text"] = _safe_text(message.GetDescriptionText())
        except Exception:
            record[u"text"] = u"<no description>"
        try:
            record[u"severity"] = _safe_text(message.GetSeverity())
        except Exception:
            record[u"severity"] = u"unknown"
        try:
            record[u"elements"] = len(list(message.GetFailingElementIds()))
        except Exception:
            record[u"elements"] = None
        return record

    def _try_resolve(self, accessor, message):
        try:
            if not message.HasResolutions():
                return False
            if not accessor.IsFailureResolutionPermitted(message):
                return False
            accessor.ResolveFailure(message)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# The sync itself
# ---------------------------------------------------------------------------

def sync_document(doc, recorder, comment=None, lock_wait_sec=None,
                  compact=False):
    """Sync ``doc`` with central, relinquishing everything.

    Raises on failure so the caller can classify with ``classify_exception``.
    Returns a dict of facts about the sync for the diagnostics block.
    """
    comment = comment or DEFAULT_COMMENT
    lock_callback = LockWaitCallback(recorder, lock_wait_sec)

    transact_options = DB.TransactWithCentralOptions()
    lock_callback_installed = True
    try:
        transact_options.SetLockCallback(lock_callback)
    except Exception as ex:
        # Worth shouting about: without it a locked central blocks forever.
        lock_callback_installed = False
        recorder.warn(
            u"could not install central-lock callback; a locked central will "
            u"block until the caller times out: %s" % _safe_text(ex))

    sync_options = DB.SynchronizeWithCentralOptions()
    sync_options.Comment = comment
    sync_options.SaveLocalBefore = True
    sync_options.SaveLocalAfter = True
    sync_options.Compact = bool(compact)
    sync_options.SetRelinquishOptions(DB.RelinquishOptions(True))

    recorder.info(u"synchronizing with central (relinquishing all ownership)")
    started = time.time()
    doc.SynchronizeWithCentral(transact_options, sync_options)
    elapsed = time.time() - started
    recorder.info(u"sync completed in %s" % format_duration(elapsed))

    return {
        # Whole seconds: IronPython's float repr turns round(11.1, 1) into
        # 11.100000000000000 in the JSON. Millisecond precision already lives in
        # diagnostics.timings.
        u"elapsedSec": int(round(elapsed)),
        u"duration": format_duration(elapsed),
        u"comment": comment,
        u"saveLocalBefore": True,
        u"saveLocalAfter": True,
        u"compact": bool(compact),
        u"relinquishEverything": True,
        u"lockCallbackInstalled": lock_callback_installed,
        u"lockWaitCalls": lock_callback.wait_calls,
        u"lockGaveUp": lock_callback.gave_up,
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

WORKSET_KIND_NAMES = (
    u"UserWorkset", u"StandardWorkset", u"ViewWorkset", u"FamilyWorkset")

OWNERSHIP_CACHE_CAVEAT = (
    u"Workset ownership is read from a local cache which the Revit API "
    u"explicitly documents as not a reliable indication. Reported for "
    u"diagnosis only; it never sets the run status. If residue shows up here "
    u"repeatedly, add an explicit WorksharingUtils.RelinquishOwnership call -- "
    u"its RelinquishedItems counts are a real central round-trip.")


def sweep_workset_ownership(doc):
    """Per-kind workset ownership after a sync, as evidence relinquish behaved.

    Read-only, and never allowed to affect the run status.
    """
    per_kind = {}
    still_owned = []

    for kind_name in WORKSET_KIND_NAMES:
        kind = getattr(DB.WorksetKind, kind_name, None)
        if kind is None:
            continue
        total = 0
        mine = 0
        others = 0
        try:
            worksets = DB.FilteredWorksetCollector(doc).OfKind(kind).ToWorksets()
            for workset in worksets:
                total += 1
                try:
                    owner = _safe_text(workset.Owner or u"")
                except Exception:
                    owner = u""
                try:
                    editable = bool(workset.IsEditable)
                except Exception:
                    editable = False
                if editable:
                    mine += 1
                    still_owned.append({
                        u"kind": kind_name,
                        u"name": _safe_text(getattr(workset, u"Name", u"")),
                        u"owner": owner,
                    })
                elif owner:
                    others += 1
        except Exception as ex:
            per_kind[kind_name] = {u"error": _safe_text(ex)}
            continue
        per_kind[kind_name] = {
            u"total": total,
            u"ownedByMe": mine,
            u"ownedByOthers": others,
        }

    result = {
        u"worksets": per_kind,
        u"stillOwnedByMe": still_owned,
        u"cacheCaveat": OWNERSHIP_CACHE_CAVEAT,
    }
    try:
        result[u"isModifiedAfterSync"] = bool(doc.IsModified)
    except Exception:
        result[u"isModifiedAfterSync"] = None
    return result


def describe_document(doc):
    """Facts about the open document, for diagnostics and the assertions."""
    facts = {}

    def grab(key, getter):
        try:
            facts[key] = getter()
        except Exception as ex:
            facts[key] = u"ERROR: %s" % _safe_text(ex)

    grab(u"title", lambda: _safe_text(doc.Title))
    grab(u"pathName", lambda: _safe_text(doc.PathName or u""))
    grab(u"isWorkshared", lambda: bool(doc.IsWorkshared))
    grab(u"isModelInCloud", lambda: bool(doc.IsModelInCloud))
    grab(u"isDetached", lambda: bool(doc.IsDetached))
    grab(u"isFamilyDocument", lambda: bool(doc.IsFamilyDocument))
    grab(u"isLinked", lambda: bool(doc.IsLinked))
    grab(u"isModified", lambda: bool(doc.IsModified))

    def central_path():
        model_path = doc.GetWorksharingCentralModelPath()
        if model_path is None:
            return None
        return _safe_text(
            DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path))

    grab(u"centralPath", central_path)
    return facts


def assert_syncable(doc):
    """Return a list of reasons ``doc`` cannot be synced. Empty means fine.

    ``isDetached`` is checked deliberately, not as boilerplate: the ACC hub scan
    turned up a model that reports workshared=true but whose filename ends
    ``_detached``. Failing it here reports needs_attention instead of attempting
    an impossible sync.
    """
    problems = []
    try:
        if doc.IsFamilyDocument:
            problems.append(u"the document is a family, not a project")
    except Exception:
        pass
    try:
        if not doc.IsWorkshared:
            problems.append(u"the model is not workshared, so it has no central "
                            u"to sync with")
    except Exception:
        problems.append(u"could not determine whether the model is workshared")
    try:
        if doc.IsDetached:
            problems.append(u"the model is detached from central and can never "
                            u"be synced")
    except Exception:
        pass
    try:
        if doc.IsLinked:
            problems.append(u"the document is a link, not the host model")
    except Exception:
        pass
    try:
        if doc.GetWorksharingCentralModelPath() is None:
            problems.append(u"the model has no central location")
    except Exception:
        pass
    return problems


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

# A human must act; retrying cannot succeed.
NEEDS_ATTENTION_EXCEPTIONS = {
    u"RevitServerUnauthenticatedUserException": u"not_signed_in",
    u"UnauthenticatedException": u"not_signed_in",
    u"RevitServerUnauthorizedException": u"no_permission",
    u"CentralModelAccessDeniedException": u"no_permission",
    u"WrongUserException": u"wrong_user_local",
    u"CannotOpenBothCentralAndLocalException": u"session_conflict",
    u"FileArgumentNotFoundException": u"model_not_found",
    u"CentralModelMissingException": u"model_not_found",
    u"CorruptModelException": u"model_corrupt",
    u"ServerModelCorruptedException": u"model_corrupt",
    u"CentralModelVersionArchivedException": u"model_archived",
    u"OutdatedDirectlyOpenedCentralException": u"central_outdated",
    u"RevitServerCollaborationNotAvailableException": u"collab_unavailable",
    u"DisabledDisciplineException": u"unsupported_host",
    u"OptionalFunctionalityNotAvailableException": u"unsupported_host",
    u"TransmittedModelException": u"model_state",
    u"NotTransmittedModelException": u"model_state",
}

# Transient; a retry may well succeed.
FAILED_EXCEPTIONS = {
    u"CentralFileCommunicationException": u"network",
    u"RevitServerCommunicationException": u"network",
    u"NetworkCommunicationException": u"network",
    u"CentralModelContentionException": u"central_locked",
    u"RevitServerInternalException": u"server_error",
    u"ServerInternalException": u"server_error",
    u"InternalException": u"server_error",
    u"OperationCanceledException": u"cancelled",
    u"BackgroundTaskCancelledException": u"cancelled",
    u"InsufficientResourcesException": u"resources",
    u"FileAccessException": u"io",
    u"IOException": u"io",
    u"FileNotFoundException": u"io",
}

# CentralModelException's documented message set spans both buckets, so it is
# discriminated by phrase. Anything unmatched defaults to needs_attention:
# better a human glances at a retryable case than a retry loop hammering a
# corrupt central.
CENTRAL_TRANSIENT_PHRASES = (
    u"try again",
    u"an internal error happened on the central model",
    u"could not save all of the worksets",
    u"aborted by another user",
)

CENTRAL_ATTENTION_PHRASES = (
    (u"closed without saving after synchronizing", u"local_incompatible"),
    (u"username does not match", u"wrong_user_local"),
    (u"editable by someone else", u"ownership_conflict"),
    (u"have been relinquished", u"ownership_conflict"),
    (u"replaced by a local model", u"ownership_conflict"),
    (u"rolled back", u"ownership_conflict"),
    (u"corrupt", u"model_corrupt"),
)

# Human-readable, action-shaped explanations for attentionReason. Phrased so a
# BIM engineer reading the UI banner knows what to do, not just what broke.
REASON_EXPLANATIONS = {
    u"not_signed_in":
        u"Revit is not signed in to Autodesk on this machine. Sign in, then "
        u"run again.",
    u"no_permission":
        u"This Autodesk account does not have permission to sync this model. "
        u"Check the ACC project membership.",
    u"wrong_user_local":
        u"The local cache on this machine belongs to a different Autodesk "
        u"user. Clear the Revit collaboration cache and run again.",
    u"session_conflict":
        u"Revit cannot open this model because a conflicting copy is already "
        u"open in the same session. Close it first.",
    u"model_not_found":
        u"No cloud model matches the supplied project/model GUIDs. It may have "
        u"been moved or deleted in ACC.",
    u"model_corrupt":
        u"Revit reports the model or its central as corrupt. This needs a "
        u"human to inspect it before any retry.",
    u"model_archived":
        u"The requested model version has been archived in ACC and cannot be "
        u"opened.",
    u"central_outdated":
        u"The central model was opened directly and is out of date.",
    u"collab_unavailable":
        u"Cloud collaboration is not available for this model.",
    u"unsupported_host":
        u"This Revit installation cannot perform the sync (Revit LT, or a "
        u"disabled discipline).",
    u"model_state":
        u"The model is in an eTransmit state that prevents syncing.",
    u"unsupported_revit_version":
        u"The model was not saved in this release of Revit.",
    u"model_not_syncable":
        u"The model is not in a state that can be synced.",
    u"local_incompatible":
        u"The local cache is incompatible with central because a previous run "
        u"closed without saving. Clear the Revit collaboration cache for this "
        u"model and run again.",
    u"ownership_conflict":
        u"Element or workset ownership conflicts with another user and needs "
        u"human coordination before a retry can succeed.",
    u"unresolved_model_errors":
        u"The model has errors Revit could not resolve automatically. Open it "
        u"and fix them, then run again.",
    u"bad_input":
        u"Syncguard was invoked with missing or malformed configuration.",
    u"version_mismatch":
        u"The model moved to a different Revit version after this run was "
        u"queued. Re-queue it so the correct Revit version is used.",
    u"script_bug":
        u"Syncguard called the Revit API incorrectly. This is a bug in the "
        u"script, not a problem with the model.",
    u"central_model_error":
        u"Revit reported a central model error that needs a human to look at "
        u"it.",
}


def explain_reason(reason, fallback=u""):
    """Action-shaped sentence for a reason code, for ``attentionReason``."""
    return REASON_EXPLANATIONS.get(reason, fallback or reason)


def exception_type_name(ex):
    """Full .NET type name where available.

    Classified by name rather than an ``except`` chain because type identity
    across the CLR boundary is fragile, and CentralModelException needs message
    inspection regardless.
    """
    try:
        return _safe_text(ex.GetType().FullName)
    except Exception:
        pass
    try:
        return u"%s.%s" % (type(ex).__module__, type(ex).__name__)
    except Exception:
        return u"<unknown>"


def classify_exception(ex):
    """Map an exception to ``{status, reason, typeName, message}``."""
    type_name = exception_type_name(ex)
    short_name = type_name.split(u".")[-1]
    message = _safe_text(ex)
    lowered = message.lower()

    if short_name == u"CentralModelException":
        for phrase in CENTRAL_TRANSIENT_PHRASES:
            if phrase in lowered:
                return _verdict(STATUS_FAILED, u"sync_retry", type_name, message)
        for phrase, reason in CENTRAL_ATTENTION_PHRASES:
            if phrase in lowered:
                return _verdict(
                    STATUS_NEEDS_ATTENTION, reason, type_name, message)
        return _verdict(
            STATUS_NEEDS_ATTENTION, u"central_model_error", type_name, message)

    if short_name in NEEDS_ATTENTION_EXCEPTIONS:
        return _verdict(
            STATUS_NEEDS_ATTENTION, NEEDS_ATTENTION_EXCEPTIONS[short_name],
            type_name, message)

    if short_name in FAILED_EXCEPTIONS:
        return _verdict(
            STATUS_FAILED, FAILED_EXCEPTIONS[short_name], type_name, message)

    if short_name == u"InvalidOperationException":
        if u"not saved in current release" in lowered:
            return _verdict(
                STATUS_NEEDS_ATTENTION, u"unsupported_revit_version",
                type_name, message)
        return _verdict(
            STATUS_NEEDS_ATTENTION, u"model_not_syncable", type_name, message)

    if short_name == u"ArgumentException":
        return _verdict(
            STATUS_NEEDS_ATTENTION, u"script_bug", type_name, message)

    # Unknown: prefer failed. An unrecognised fault is more often transient than
    # a permanent human-fixable condition, and repeated failures get surfaced by
    # the agent anyway.
    return _verdict(STATUS_FAILED, u"unknown", type_name, message)


def _verdict(status, reason, type_name, message):
    return {
        u"status": status,
        u"reason": reason,
        u"typeName": type_name,
        u"message": message,
    }


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

def build_result(status, recorder, attention_reason=None, diagnostics=None):
    """Assemble the Phase 4 result contract.

    Only ``status``, ``attentionReason``, ``steps`` and ``lines`` are part of
    the agreed contract. ``diagnostics`` is additive and the agent is free to
    ignore it -- it is what makes a live run legible.
    """
    payload = {
        u"status": status,
        u"steps": recorder.steps(),
        u"lines": list(recorder.lines),
    }
    if status == STATUS_NEEDS_ATTENTION and attention_reason:
        payload[u"attentionReason"] = attention_reason
    diag = dict(diagnostics or {})
    if recorder.lines_truncated:
        diag[u"linesTruncated"] = True
        diag[u"linesCap"] = MAX_LINES
    payload[u"diagnostics"] = diag
    return payload


def write_result_json(path, payload):
    """Write the result atomically.

    Atomic because the headless entry writes it twice -- once as soon as the
    outcome is known, again after close -- and because the agent may be reading
    it. IronPython 2.7 has no ``os.replace``, hence remove-then-rename.
    """
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    temp_path = path + u".tmp"
    with codecs.open(temp_path, u"w", u"utf-8") as handle:
        handle.write(json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True))
    if os.path.exists(path):
        os.remove(path)
    os.rename(temp_path, path)
    return path
