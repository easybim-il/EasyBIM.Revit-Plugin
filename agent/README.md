# Syncguard agent

The workstation half of Syncguard. EasyBIM queues a job; this agent picks it up,
opens the cloud coordination model in Revit, syncs it, and reports back. The
platform publishes to ACC itself the moment the sync lands.

It has five jobs: **enroll** once, **heartbeat**, **claim** work, **run** it,
**report** the result. All traffic is outbound HTTPS — no inbound ports, no VPN,
nothing for IT to open.

---

## Install

Two commands, on the machine that will run the syncs.

```powershell
cd "%APPDATA%\pyRevit\Extensions\EasyBIM.extension"
powershell -ExecutionPolicy Bypass -File agent\install_task.ps1
```

That registers a logon scheduled task and starts it. A Syncguard icon appears in
the notification area, bottom-right, next to the clock.

Point it at a different EasyBIM with `-BaseUrl`:

```powershell
powershell -ExecutionPolicy Bypass -File agent\install_task.ps1 -BaseUrl https://epm.easybim.co.il
```

**No Python to install.** The agent runs on the CPython that pyRevit already
ships, and pyRevit is required anyway because the agent's whole job is to drive
`pyrevit run`.

**Why a scheduled task and not a service.** A Windows service runs in Session 0
and cannot start an interactive Revit at all. Revit's licence and the Autodesk
sign-in also live in the user profile, so the agent has to run as the signed-in
user.

## Connect it to EasyBIM

The icon is **grey** until the machine is connected.

1. In EasyBIM, open the project's Syncguard panel and choose **Add this
   computer**. It shows a short pairing code.
2. Click the Syncguard icon → **Connect to EasyBIM…**
3. Type the code and choose **Connect**.

The icon turns **green**. The computer now appears in the target-computer
picker.

> The code is typed into the **tray icon**, not into Revit. There is nothing to
> click in the Revit ribbon.

On a shared workstation with no one at the keyboard, enroll from the command
line instead:

```powershell
& "C:\Program Files\pyRevit-Master\bin\cengines\CPY3123\python.exe" agent\syncguard_agent.py --enroll ABC123
```

## What the icon colour means

| Colour | Meaning |
|---|---|
| **Green** | Connected, online, ready for work |
| **Blue** | A sync is running now |
| **Amber** | Needs attention — Autodesk sign-in lost, or the last run failed |
| **Grey** | Not connected, offline, paused, or disabled by an administrator |

Hover for detail. Click for the menu: recheck Autodesk sign-in, pause new jobs,
open the log folder, or quit.

## Before Revit takes over the screen

On a personal machine the agent warns first: a dialog naming the model, with a
countdown and a **Defer 10 min** button. It starts automatically when the
countdown ends, so an unattended machine is never stuck waiting for a click. A
run can be deferred twice, then it starts regardless — that keeps the total
delay inside the platform's 45-minute limit for a claimed job.

Headless mode never asks. That is deliberate: a shared workstation has nobody to
ask, which is the whole point of running one.

## Shared always-on workstation

```powershell
powershell -ExecutionPolicy Bypass -File agent\install_task.ps1 -Headless
```

Same code, no tray. The machine still needs to be logged in as a real user with
a Revit licence, ACC project membership, and no MFA prompt on Autodesk sign-in.

## Where things live

Everything is under `%APPDATA%\EasyBIM\Syncguard`:

```
config.json                    base URL, agent id, machine name
token.bin                      the bearer token, encrypted to the Windows user
agent.log                      what the agent did
runs\<runId>\result.json       the Revit script's verdict for that run
runs\<runId>\progress.ndjson   the live progress stream
runs\<runId>\pyrevit-console.log
```

Run artefacts must not live under `%TEMP%`: inside Revit, `%TEMP%` is redirected
to a per-run folder that `pyrevit run --purge` deletes, so a result written
there disappears before it can be read.

Useful one-liners:

```powershell
# what does the agent think it knows?
& "...\python.exe" agent\syncguard_agent.py --status

# tail the log
Get-Content "$env:APPDATA\EasyBIM\Syncguard\agent.log" -Wait -Tail 40
```

## Timeouts

Roughly 95% of a run is Revit opening the model, and it reports nothing while it
works — 6m55s of a 7m20s run on a mid-sized model, and bigger ones exist. So the
timeout is **phase-aware**, not a flat clock:

| Phase | Silence allowed |
|---|---|
| Opening the model | 40 min |
| Everything else | 10 min |

Silence means no progress from the Revit script. The agent's own keepalive
messages do not count, or it could never notice a hang.

These nest, innermost first, and the order matters:

```
central-lock wait 5 min  <  agent silence (40 / 10 min)  <  server gives up 45 min
```

## Troubleshooting

**Icon never appears.** Check the task ran: `Get-ScheduledTaskInfo "EasyBIM
Syncguard Agent"`. Then read `agent.log`.

**"That computer is offline" in EasyBIM.** The platform calls an agent offline
after 5 minutes without a heartbeat. Check the agent is running and can reach the
base URL in `config.json`.

**"Revit is open with … close it first".** Close that model on that machine. The
agent reports which cloud models the live Revit session has opened, and a run is
refused rather than colliding over workset ownership. The list over-reports
slightly: a model loaded only as a *link*, or opened and then closed during the
session, still counts. Refusing wrongly costs a click; colliding costs a tangled
model.

**"Revit could not start properly … It reported: …".** Revit never reached the
script, and the quoted text is the dialog it was sitting on. Two seen in
practice, both machine-level rather than anything about the model:

> The License Manager is not functioning or is improperly installed. Revit will
> shut down now.

> This action cannot be completed because the other program is busy.

Both trace to Autodesk licensing rather than Syncguard. The licensing service log
at `C:\ProgramData\Autodesk\AdskLicensingService\Log\AdskLicensingService.log`
shows the real cause:

```
timed-out after 30.0 sec(s) waiting for Agent to connect
```

`AdskLicensingAgent.exe` did not answer inside Revit's 30-second checkout window.
It gets worse with repetition, because each timeout makes the service launch
*another* agent, and several competing agents make the handshake fail more often.
If runs keep failing this way, check for duplicates:

```powershell
Get-Process AdskLicensingAgent, AdskAccessUIHost -ErrorAction SilentlyContinue |
    Group-Object ProcessName | Select-Object Name, Count
```

More than one of each means the pile-up has happened. Killing the extras (they
respawn on demand) or rebooting clears it. Note this is *not* caused by having
another Revit open — it reproduced with no other Revit running.

Note also that `taskkill /F` does **not** clear a Revit stuck on such a dialog:
it returns "the operation returned because the timeout period expired" and the
process stays. The agent presses the dialog's buttons first — Revit shuts down
once acknowledged — and only then terminates the process.

**Amber icon, "Autodesk sign-in needed".** Open Revit on that machine, sign in to
Autodesk, then click the icon → **Recheck Autodesk sign-in**. It also clears
itself after 20 minutes: sign-in state cannot be read from outside Revit, so the
agent assumes it is fine and only believes otherwise when a run actually fails
its pre-flight. A permanent "signed out" would wedge the machine, because the
platform refuses to queue work for an agent it thinks is signed out — and then no
run could ever prove otherwise.

**A run failed with "no output at all".** `pyrevit run` exits 0 having done
nothing when it cannot find the script, so this is reported as *needs attention*
rather than a retryable failure — retrying will fail identically. Check the
EasyBIM extension is fully checked out and pyRevit is installed.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File agent\install_task.ps1 -Uninstall
```

Then delete `%APPDATA%\EasyBIM\Syncguard` to remove the token and logs.
