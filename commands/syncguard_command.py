# -*- coding: utf-8 -*-
"""Syncguard headless entry: open a cloud-workshared model, sync it, close it.

Invoked by the Syncguard tray agent (Phase 4) as::

    pyrevit run <abs path to this file> --revit=2025 --purge

Publishing is NOT done here -- it is already implemented server-side against the
ACC Data Management commands API and needs no Revit. Links are NOT reloaded here
either: Revit auto-reloads links to latest when it opens a cloud-workshared
model, and the sync is what commits that reloaded state into central. That is
the entire point of the feature.

*** NEW REVIT-API GROUND, FLAGGED FOR VALIDATION ***

INPUTS ARRIVE AS ENVIRONMENT VARIABLES, because ``pyrevit run`` accepts only
--revit / --purge / --allowdialogs / --import / --models / <model_file> and has
no channel for custom script arguments. Passing the model as ``<model_file>`` is
not an option either: that argument wants a local path, so the cloud model is
resolved from GUIDs in here instead.

    SYNCGUARD_PROJECT_GUID      required   ACC projectGuid
    SYNCGUARD_MODEL_GUID        required   ACC modelGuid
    SYNCGUARD_REGION            required   US | EMEA
    SYNCGUARD_EXPECTED_REVIT    required   the model's own revitProjectVersion
    SYNCGUARD_RESULT_JSON       required   absolute path for the result contract
    SYNCGUARD_PROGRESS_NDJSON   optional   append-only live progress file
    SYNCGUARD_COMMENT           optional   sync comment
    SYNCGUARD_LOCK_WAIT_SEC     optional   central-lock patience, default 300
    SYNCGUARD_COMPACT           optional   "1" to compact central

Two things learned by probing this harness that shape the code below:

  * ``pyrevit run`` must be given an ABSOLUTE script path. A relative one is
    copied verbatim into the journal's ScriptSource and Revit runs from its own
    temp working directory, so the script is silently never found -- no error,
    no output.
  * The journal sets ``SearchPaths`` to empty, so sys.path contains only
    pyrevitlib, site-packages and this file's own directory. The extension's
    lib/ is NOT importable without the bootstrap below. TEMP is also redirected
    into the per-run folder that --purge deletes, so nothing durable may be
    written there -- hence absolute output paths from the caller.
"""

import os
import sys
import time
import traceback

# --- bootstrap ------------------------------------------------------------
# Put the extension's lib/ on sys.path. __file__ is the real path in the
# extension (verified: pyrevit run does not copy the script), so deriving the
# extension root from it keeps this free of hardcoded or user-specific paths.
_HERE = os.path.dirname(os.path.abspath(__file__))
_EXT_ROOT = os.path.dirname(_HERE)
_LIB_DIR = os.path.join(_EXT_ROOT, u"lib")
if os.path.isdir(_LIB_DIR) and _LIB_DIR not in sys.path:
    sys.path.append(_LIB_DIR)

from pyrevit import DB, HOST_APP, framework

from easybim import syncguard as sg


ENV_PROJECT_GUID = u"SYNCGUARD_PROJECT_GUID"
ENV_MODEL_GUID = u"SYNCGUARD_MODEL_GUID"
ENV_REGION = u"SYNCGUARD_REGION"
ENV_EXPECTED_REVIT = u"SYNCGUARD_EXPECTED_REVIT"
ENV_RESULT_JSON = u"SYNCGUARD_RESULT_JSON"
ENV_PROGRESS_NDJSON = u"SYNCGUARD_PROGRESS_NDJSON"
ENV_COMMENT = u"SYNCGUARD_COMMENT"
ENV_LOCK_WAIT = u"SYNCGUARD_LOCK_WAIT_SEC"
ENV_COMPACT = u"SYNCGUARD_COMPACT"

DEFAULT_LOCK_WAIT_SEC = 300
MIN_REVIT_VERSION = 2023


def _env(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _default_progress_path():
    """Per-machine state, so %APPDATA% rather than the repo or TEMP.

    Matches the convention lib/easybim/coordination_settings.py documents, and
    TEMP is unusable here because --purge deletes it.
    """
    base = os.environ.get(u"APPDATA") or _EXT_ROOT
    return os.path.join(base, u"EasyBIM", u"Syncguard", u"last-run.ndjson")


def _reset_progress(path):
    """Truncate the progress file so each run starts clean.

    The Recorder appends, and a tailing consumer must not see a previous run's
    lines mixed into this one.
    """
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        handle = open(path, u"w")
        handle.close()
    except Exception:
        pass


def _resolve_region(name):
    """Map ``US``/``EMEA`` to the API's region constant.

    Looked up rather than hardcoded: only CloudRegionUS and CloudRegionEMEA
    exist today and Autodesk adds regions in release notes. Falls back to the
    raw string, which is what the API actually takes.
    """
    cleaned = (name or u"").strip()
    attr = u"CloudRegion" + cleaned.upper()
    return getattr(DB.ModelPathUtils, attr, None) or cleaned


def _open_options():
    """OpenOptions for a cloud-workshared sync.

    OpenAllWorksets is mandatory rather than cosmetic: link instances live on
    worksets, so a closed workset means that link never loads and the sync would
    commit an incomplete link state -- the opposite of what Syncguard is for.
    Workset open/closed state is independent of editability, so this takes no
    ownership. The cost is peak memory and open time, which is accepted.

    DoNotDetach matters just as much: a detached model cannot sync at all, which
    would silently defeat the feature.

    ``OpenEditable`` is left at its default deliberately -- it is undocumented,
    and if it does what its name suggests it would check out worksets.
    """
    workset_config = DB.WorksetConfiguration(
        DB.WorksetConfigurationOption.OpenAllWorksets)
    options = DB.OpenOptions()
    options.DetachFromCentralOption = DB.DetachFromCentralOption.DoNotDetach
    options.Audit = False
    try:
        options.DoNotLoadLinks = False
    except Exception:
        # Older API surface; loading links is the default anyway.
        pass
    options.SetOpenWorksetsConfiguration(workset_config)
    return options


def _open_model(cloud_path, recorder, diagnostics):
    """Open the cloud model, preferring OpenDocumentFile.

    OpenDocumentFile is documented to open "from disk or cloud" and to open the
    document "into memory but not make it visible to the user in any way" -- so
    the document is not the ACTIVE document and Document.Close(False) is legal.
    That matters beyond tidiness: owning teardown is what lets the agent tell
    "sync finished, exited cleanly" apart from "Revit died mid-sync". With
    OpenAndActivateDocument the active document can never be closed by the API
    at all, so close could only ever be attempted and refused.

    Returns (doc, method_name).
    """
    cloud_callback = sg.CloudOpenCallback(recorder)
    diagnostics[u"openMethodTried"] = u"OpenDocumentFile"

    try:
        doc = HOST_APP.app.OpenDocumentFile(
            cloud_path, _open_options(), cloud_callback)
        diagnostics[u"openMethod"] = u"OpenDocumentFile"
        diagnostics[u"cloudOpenConflicts"] = list(cloud_callback.conflicts)
        return doc, u"OpenDocumentFile"
    except Exception as ex:
        verdict = sg.classify_exception(ex)
        diagnostics[u"openDocumentFileError"] = {
            u"typeName": verdict[u"typeName"],
            u"message": verdict[u"message"],
        }
        diagnostics[u"cloudOpenConflicts"] = list(cloud_callback.conflicts)
        recorder.warn(
            u"OpenDocumentFile failed (%s: %s)"
            % (verdict[u"typeName"], verdict[u"message"]))

        if cloud_callback.cancelled_on:
            # We cancelled the open ourselves over an unresolvable cloud
            # conflict. The fallback would hit exactly the same wall.
            recorder.error(
                u"open cancelled on cloud conflict '%s'; not retrying"
                % cloud_callback.cancelled_on)
            raise

        # Fall back, and record what happened either way. If this succeeds where
        # OpenDocumentFile failed on authentication, that is a much more serious
        # finding than a worksharing quirk -- it would mean cloud sign-in needs a
        # UI document, which reshapes the tray agent.
        recorder.info(u"falling back to OpenAndActivateDocument")
        diagnostics[u"openMethodTried"] = u"OpenAndActivateDocument"
        uiapp = HOST_APP.uiapp
        if uiapp is None:
            recorder.error(
                u"no UIApplication available for the fallback open")
            raise
        uidoc = uiapp.OpenAndActivateDocument(
            cloud_path, _open_options(), False)
        diagnostics[u"openMethod"] = u"OpenAndActivateDocument"
        diagnostics[u"openFallbackUsed"] = True
        recorder.warn(
            u"opened via OpenAndActivateDocument; the document is active so it "
            u"cannot be closed by the API and Revit teardown is left to the "
            u"pyrevit run journal")
        return uidoc.Document, u"OpenAndActivateDocument"


def _close_model(doc, method, recorder, diagnostics):
    """Close the document. Never allowed to change the run status."""
    if method == u"OpenAndActivateDocument":
        diagnostics[u"closed"] = False
        diagnostics[u"closeNote"] = (
            u"the active document cannot be closed via the Revit API "
            u"(Document.Close and UIDocument.SaveAndClose both refuse); Revit "
            u"is terminated by the pyrevit run journal instead")
        recorder.info(u"skipping close: the active document cannot be closed")
        return
    try:
        doc.Close(False)
        diagnostics[u"closed"] = True
        recorder.info(u"model closed")
    except Exception as ex:
        diagnostics[u"closed"] = False
        diagnostics[u"closeError"] = sg._safe_text(ex)
        recorder.warn(
            u"close failed, leaving teardown to Revit: %s" % sg._safe_text(ex))


def main():
    started = time.time()
    timings = {}
    diagnostics = {u"timings": timings}

    # Stage one: resolve the output paths BEFORE validating anything else. If
    # the result path itself is what's missing there is nowhere to write the
    # verdict, so the progress file becomes the only record. The agent's reading
    # of that: no result AND no progress = configuration fault (needs_attention,
    # since it will fail identically forever); missing result WITH a populated
    # progress file = real crash (failed).
    result_path = _env(ENV_RESULT_JSON)
    progress_path = _env(ENV_PROGRESS_NDJSON) or _default_progress_path()
    _reset_progress(progress_path)

    recorder = sg.Recorder(progress_path=progress_path)
    recorder.declare_step(sg.STEP_OPEN_MODEL)
    recorder.declare_step(sg.STEP_SYNC_CENTRAL)

    diagnostics[u"resultPath"] = result_path or None
    diagnostics[u"progressPath"] = progress_path

    doc = None
    open_method = None
    status = sg.STATUS_FAILED
    attention_reason = None

    def finalize():
        """Write the result contract. Safe to call more than once."""
        payload = sg.build_result(
            status, recorder, attention_reason, diagnostics)
        if not result_path:
            recorder.error(
                u"%s is not set, so there is nowhere to write the result; the "
                u"progress file is the only record of this run"
                % ENV_RESULT_JSON)
            return None
        try:
            return sg.write_result_json(result_path, payload)
        except Exception as ex:
            recorder.error(
                u"could not write the result file %s: %s"
                % (result_path, sg._safe_text(ex)))
            return None

    try:
        app = HOST_APP.app
        recorder.info(u"Revit %s started (%s)" % (
            sg._safe_text(app.VersionNumber), sg._safe_text(app.VersionName)))

        # -- stage two: the rest of the configuration ---------------------
        project_guid = _env(ENV_PROJECT_GUID)
        model_guid = _env(ENV_MODEL_GUID)
        region_name = _env(ENV_REGION)
        expected_revit = _env(ENV_EXPECTED_REVIT)
        comment = _env(ENV_COMMENT, sg.DEFAULT_COMMENT)
        compact = _env(ENV_COMPACT, u"0") in (u"1", u"true", u"True", u"yes")
        try:
            lock_wait_sec = int(_env(ENV_LOCK_WAIT, DEFAULT_LOCK_WAIT_SEC))
        except Exception:
            lock_wait_sec = DEFAULT_LOCK_WAIT_SEC

        missing = [name for name, value in (
            (ENV_PROJECT_GUID, project_guid),
            (ENV_MODEL_GUID, model_guid),
            (ENV_REGION, region_name),
            (ENV_EXPECTED_REVIT, expected_revit),
            (ENV_RESULT_JSON, result_path),
        ) if not value]
        if missing:
            status = sg.STATUS_NEEDS_ATTENTION
            # Name the variables. explain_reason() alone returns the generic
            # sentence, which tells whoever reads the banner nothing actionable.
            attention_reason = u"%s Missing: %s." % (
                sg.explain_reason(u"bad_input"), u", ".join(missing))
            diagnostics[u"missingEnv"] = missing
            recorder.error(
                u"missing required configuration: %s" % u", ".join(missing))
            return

        diagnostics[u"projectGuid"] = project_guid
        diagnostics[u"modelGuid"] = model_guid
        diagnostics[u"regionRequested"] = region_name
        diagnostics[u"expectedRevit"] = expected_revit
        diagnostics[u"lockWaitSec"] = lock_wait_sec
        diagnostics[u"compact"] = compact

        # -- pre-flight ---------------------------------------------------
        running_version = sg._safe_text(app.VersionNumber)
        diagnostics[u"revitVersion"] = running_version

        try:
            too_old = int(running_version) < MIN_REVIT_VERSION
        except Exception:
            too_old = False
        if too_old:
            status = sg.STATUS_NEEDS_ATTENTION
            attention_reason = (
                u"Syncguard supports Revit %d and newer; this is Revit %s."
                % (MIN_REVIT_VERSION, running_version))
            recorder.error(attention_reason)
            return

        # Version guard, hard refuse. Dialogs are suppressed under pyrevit run,
        # so a newer Revit would auto-dismiss the upgrade prompt and SILENTLY,
        # IRREVERSIBLY upgrade a coordination model. Nothing is touched on a
        # mismatch. Note the expected version is snapshotted when the run is
        # queued, so this also fires when someone upgrades the model in ACC
        # between enqueue and execution -- correct behaviour, and the message
        # says so, because the fix is to re-queue rather than to debug a script.
        if running_version != expected_revit:
            status = sg.STATUS_NEEDS_ATTENTION
            attention_reason = (
                u"This model expects Revit %s but Revit %s was launched. "
                u"Nothing was opened or changed. If the model was upgraded in "
                u"ACC after this run was queued, re-queue it so the correct "
                u"Revit version is used."
                % (expected_revit, running_version))
            recorder.error(attention_reason)
            return

        # Sign-in. Both are checked: LoginUserId is documented empty when not
        # signed in, and IsLoggedIn has historically returned true on a stale
        # token.
        try:
            is_logged_in = bool(app.IsLoggedIn)
        except Exception:
            is_logged_in = False
        try:
            login_user_id = sg._safe_text(app.LoginUserId or u"")
        except Exception:
            login_user_id = u""
        diagnostics[u"signedIn"] = is_logged_in
        diagnostics[u"loginUserId"] = login_user_id
        try:
            diagnostics[u"revitUsername"] = sg._safe_text(app.Username or u"")
        except Exception:
            diagnostics[u"revitUsername"] = u""

        if not is_logged_in or not login_user_id:
            status = sg.STATUS_NEEDS_ATTENTION
            attention_reason = sg.explain_reason(u"not_signed_in")
            recorder.error(
                u"Revit is not signed in to Autodesk (IsLoggedIn=%s, "
                u"LoginUserId=%r)" % (is_logged_in, login_user_id))
            return
        recorder.info(u"signed in to Autodesk as %s" % login_user_id)

        # -- resolve the cloud path ---------------------------------------
        stage = time.time()
        region = _resolve_region(region_name)
        diagnostics[u"regionResolved"] = sg._safe_text(region)
        cloud_path = DB.ModelPathUtils.ConvertCloudGUIDsToCloudPath(
            region,
            framework.Guid(project_guid),
            framework.Guid(model_guid))

        # Cheap round-trip check: catches a bad region string before burning a
        # long open on it.
        round_trip = {}
        try:
            round_trip[u"isCloudPath"] = bool(cloud_path.CloudPath)
            round_trip[u"region"] = sg._safe_text(cloud_path.Region)
            round_trip[u"projectGuid"] = sg._safe_text(
                cloud_path.GetProjectGUID())
            round_trip[u"modelGuid"] = sg._safe_text(cloud_path.GetModelGUID())
        except Exception as ex:
            round_trip[u"error"] = sg._safe_text(ex)
        diagnostics[u"cloudPathRoundTrip"] = round_trip
        try:
            diagnostics[u"userVisiblePath"] = sg._safe_text(
                DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(cloud_path))
        except Exception:
            pass
        timings[u"resolveMs"] = int((time.time() - stage) * 1000)
        recorder.info(u"resolved cloud path for region %s"
                      % sg._safe_text(region))

        # -- open, sync -----------------------------------------------------
        # The failure handler must be subscribed BEFORE the open, because Revit
        # posts failures while loading links too.
        with sg.FailureWatcher(recorder) as watcher:
            diagnostics[u"failureHandlerSubscribed"] = watcher.subscribed
            watcher.set_phase(u"open")

            recorder.set_step(sg.STEP_OPEN_MODEL, sg.STEP_RUNNING)
            stage = time.time()
            doc, open_method = _open_model(cloud_path, recorder, diagnostics)
            timings[u"openMs"] = int((time.time() - stage) * 1000)

            facts = sg.describe_document(doc)
            diagnostics[u"document"] = facts

            problems = sg.assert_syncable(doc)
            if not facts.get(u"isModelInCloud"):
                problems.append(
                    u"the opened model is not a cloud model")
            if problems:
                recorder.set_step(
                    sg.STEP_OPEN_MODEL, sg.STEP_FAILED,
                    u"; ".join(problems))
                status = sg.STATUS_NEEDS_ATTENTION
                attention_reason = (
                    u"This model cannot be synced: %s." % u"; ".join(problems))
                recorder.error(attention_reason)
                return

            model_title = facts.get(u"title") or u"model"
            recorder.set_step(
                sg.STEP_OPEN_MODEL, sg.STEP_DONE, model_title)
            recorder.info(
                u"opened %s via %s in %s"
                % (model_title, open_method,
                   sg.format_duration(timings[u"openMs"] / 1000.0)))

            watcher.set_phase(u"sync")
            recorder.set_step(sg.STEP_SYNC_CENTRAL, sg.STEP_RUNNING)
            stage = time.time()
            sync_facts = sg.sync_document(
                doc, recorder,
                comment=comment,
                lock_wait_sec=lock_wait_sec,
                compact=compact)
            timings[u"syncMs"] = int((time.time() - stage) * 1000)
            diagnostics[u"sync"] = sync_facts
            recorder.set_step(
                sg.STEP_SYNC_CENTRAL, sg.STEP_DONE,
                u"Synced in %s" % sync_facts[u"duration"])

            watcher.set_phase(u"verify")
            stage = time.time()
            diagnostics[u"verify"] = sg.sweep_workset_ownership(doc)
            timings[u"verifyMs"] = int((time.time() - stage) * 1000)

            diagnostics[u"failureEvents"] = watcher.failure_events
            diagnostics[u"warningsRecorded"] = watcher.warnings_recorded
            diagnostics[u"hardErrors"] = list(watcher.hard_errors)
            diagnostics[u"dialogs"] = list(watcher.dialogs)
            diagnostics[u"handlerErrors"] = list(watcher.handler_errors)

            if watcher.hard_errors:
                # The sync itself returned, but Revit could not resolve real
                # errors in the model. A human has to fix those before a retry
                # means anything -- this is the "missing required parameter"
                # case the UI offers Resolve for.
                status = sg.STATUS_NEEDS_ATTENTION
                first = watcher.hard_errors[0].get(u"text") or u"unknown error"
                attention_reason = (
                    u"%s (%d unresolved model error(s) during the sync)"
                    % (first, len(watcher.hard_errors)))
            elif recorder.warning_count():
                status = sg.STATUS_WARNING
            else:
                status = sg.STATUS_SUCCESS

    except Exception as ex:
        verdict = sg.classify_exception(ex)
        status = verdict[u"status"]
        diagnostics[u"exception"] = {
            u"typeName": verdict[u"typeName"],
            u"reason": verdict[u"reason"],
            u"message": verdict[u"message"],
            u"traceback": sg._safe_text(traceback.format_exc()),
        }
        if status == sg.STATUS_NEEDS_ATTENTION:
            attention_reason = sg.explain_reason(
                verdict[u"reason"], verdict[u"message"])
        recorder.error(
            u"%s: %s" % (verdict[u"typeName"], verdict[u"message"]))
        recorder.fail_running_steps(verdict[u"message"])
    finally:
        timings[u"totalMs"] = int((time.time() - started) * 1000)
        diagnostics[u"status"] = status

        # Write the verdict BEFORE closing. The sync is the outcome; close is
        # cleanup. The agent honours a readable result rather than waiting out
        # its silence timeout, so having the result on disk already makes a
        # teardown hang harmless.
        finalize()

        if doc is not None:
            _close_model(doc, open_method, recorder, diagnostics)
            timings[u"totalMs"] = int((time.time() - started) * 1000)
            # Rewrite so the close outcome is captured too. Atomic, so a crash
            # between the two writes still leaves the first verdict intact.
            finalize()


main()
