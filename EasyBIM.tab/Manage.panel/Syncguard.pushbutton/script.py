# -*- coding: utf-8 -*-
"""Syncguard: sync the open model with central and relinquish everything.

The interactive half of Syncguard. The headless half
(``commands/syncguard_command.py``) is driven by the tray agent, resolves cloud
GUIDs and opens the model itself; this button operates on whatever the engineer
already has open and prints to the pyRevit output window.

Both call the same core in ``lib/easybim/syncguard.py``, which is the point.
Beyond being a convenient way to do a chore, this button is the fault-isolation
tool for the automated path: it exercises the whole failure handler / sync
options / relinquish / verification sequence interactively and visibly, with no
``pyrevit run`` and no cloud open involved. If the button works and an automated
run does not, the fault is in the harness or the cloud open, not in the API
sequence.

No shebang on purpose -- this runs on IronPython 2.7, because the shared core
subclasses .NET interfaces from Python. See that module's docstring.
"""

__title__ = "Syncguard"
__doc__ = ("Sync the open model with central and relinquish all ownership.\n"
           "Reports warnings, timing and leftover workset ownership.")
__author__ = "EasyBIM Team"

from pyrevit import revit, script

from easybim import syncguard as sg
from easybim import ui as eb_ui


output = script.get_output()

LEVEL_PREFIX = {
    u"info": u"",
    u"warn": u"**warning:** ",
    u"error": u"**error:** ",
}


def echo(level, text):
    """Mirror recorder lines into the output window as they happen."""
    output.print_md(u"%s%s" % (LEVEL_PREFIX.get(level, u""), text))


def describe_target(facts):
    where = u"cloud model" if facts.get(u"isModelInCloud") else u"file-based model"
    return u"%s (%s)" % (facts.get(u"title") or u"this model", where)


def main():
    doc = revit.doc
    if doc is None:
        eb_ui.alert(u"No document is open.", title=u"Syncguard")
        return

    facts = sg.describe_document(doc)
    problems = sg.assert_syncable(doc)
    if problems:
        eb_ui.alert(
            u"This model cannot be synced:\n\n- %s"
            % u"\n- ".join(problems),
            title=u"Syncguard")
        return

    if not eb_ui.ask_yes_no(
            u"Sync %s with central and relinquish ALL of your ownership?\n\n"
            u"Worksets, borrowed elements, families, views and project "
            u"standards will all be released."
            % describe_target(facts),
            title=u"Syncguard"):
        return

    output.print_md(u"### Syncguard")
    output.print_md(u"Model: `%s`" % (facts.get(u"title") or u"?"))
    if facts.get(u"centralPath"):
        output.print_md(u"Central: `%s`" % facts.get(u"centralPath"))

    # No progress file and no lock deadline. The NDJSON stream exists for the
    # tray agent's live console; here the output window IS the console. And an
    # engineer sitting in front of Revit would rather keep waiting on a locked
    # central than be told the sync failed, so the wait is unbounded on purpose.
    recorder = sg.Recorder(echo=echo)
    recorder.declare_step(sg.STEP_SYNC_CENTRAL)

    try:
        with sg.FailureWatcher(recorder) as watcher:
            if not watcher.subscribed:
                output.print_md(
                    u"**warning:** sync-time failures will not be recorded.")
            watcher.set_phase(u"sync")
            recorder.set_step(sg.STEP_SYNC_CENTRAL, sg.STEP_RUNNING)

            sync_facts = sg.sync_document(doc, recorder, lock_wait_sec=None)

            recorder.set_step(
                sg.STEP_SYNC_CENTRAL, sg.STEP_DONE,
                u"Synced in %s" % sync_facts[u"duration"])
            watcher.set_phase(u"verify")
            verify = sg.sweep_workset_ownership(doc)
    except Exception as ex:
        verdict = sg.classify_exception(ex)
        output.print_md(u"---")
        output.print_md(u"### Sync failed")
        output.print_md(u"`%s`" % verdict[u"typeName"])
        output.print_md(verdict[u"message"])
        if verdict[u"status"] == sg.STATUS_NEEDS_ATTENTION:
            output.print_md(
                u"**This needs fixing before a retry can work.** %s"
                % sg.explain_reason(verdict[u"reason"], u""))
        else:
            output.print_md(u"This looks transient -- retrying may work.")
        return

    # -- report -----------------------------------------------------------
    output.print_md(u"---")
    if watcher.hard_errors:
        output.print_md(
            u"### Synced, but %d model error(s) could not be resolved"
            % len(watcher.hard_errors))
        for record in watcher.hard_errors[:20]:
            output.print_md(u"- %s" % record.get(u"text"))
        output.print_md(
            u"These need fixing in the model before an automated run will "
            u"come back clean.")
    elif recorder.warning_count():
        output.print_md(u"### Synced with %d warning(s) in %s"
                        % (recorder.warning_count(), sync_facts[u"duration"]))
    else:
        output.print_md(u"### Synced cleanly in %s" % sync_facts[u"duration"])

    if sync_facts[u"lockWaitCalls"]:
        output.print_md(
            u"Waited for the central lock (%d check(s))."
            % sync_facts[u"lockWaitCalls"])

    output.print_md(u"**Ownership after relinquish**")
    rows = [[kind,
             counts.get(u"total", u"?"),
             counts.get(u"ownedByMe", u"?"),
             counts.get(u"ownedByOthers", u"?")]
            for kind, counts in sorted(verify[u"worksets"].items())
            if u"error" not in counts]
    if rows:
        output.print_table(
            rows, columns=[u"Workset kind", u"Total", u"Mine", u"Others"])
    if verify[u"stillOwnedByMe"]:
        output.print_md(
            u"**warning:** %d workset(s) still show as owned by you:"
            % len(verify[u"stillOwnedByMe"]))
        for item in verify[u"stillOwnedByMe"][:20]:
            output.print_md(u"- %s / %s" % (item[u"kind"], item[u"name"]))
        output.print_md(
            u"Worth a second look, but note this reads a local cache the Revit "
            u"API documents as unreliable, so it can be a false positive.")
    else:
        output.print_md(u"Nothing still owned by you.")

    if watcher.dialogs:
        output.print_md(
            u"Dialogs seen during the run: %s"
            % u", ".join(sorted(set(
                d[u"dialogId"] for d in watcher.dialogs if d[u"dialogId"]))))


main()
