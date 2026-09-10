# EasyBIM PyRevit Extension

A shared PyRevit extension for the EasyBIM team. Supports Revit 2023 and above.

---

## For Revit Users (non-developers)

### First-time setup

1. Install [PyRevit](https://github.com/pyrevitlabs/pyRevit/releases) (latest release).
2. Open Revit → **pyRevit tab → pyRevit Settings → Extensions**.
3. Click **Add Extension from Repository** and paste:
   ```
   https://github.com/maximn-png/EasyBIM.Revit-Plugin
   ```
4. Click **Install** → restart Revit.

The **EasyBIM** tab will appear in your Revit ribbon.

### Getting updates

When someone on the team builds a new button and it gets approved, the update exists on GitHub but does **not** download to your machine automatically. You need to sync manually.

**The easiest way: double-click `sync.bat`**

There is a file called `sync.bat` in the plugin folder. Double-click it — it downloads the latest changes and tells you when it is done. Then go to Revit and click **pyRevit tab → Reload** and the new buttons will appear.

**Alternative: update from inside pyRevit**

You can also pull the latest changes without leaving Revit. Go to the **pyRevit tab → Update** — pyRevit downloads the latest changes for you. Then click **pyRevit tab → Reload** and the new buttons will appear.

The plugin folder is here:
```
%APPDATA%\pyRevit\Extensions\EasyBIM.extension
```

---

## For Developers

You do not need to be a professional programmer. Claude Code writes the code for you. Your job is to describe what the button should do, test it in Revit, and submit it for review.

### Before you start

You need two things from Maxim before you can do anything:

1. **Access to the repository** — the plugin code is private. Maxim needs to add your GitHub account. Send him your GitHub username (create a free account at github.com if you don't have one).
2. **A button to work on** — design the button or panel you will be building using Claude Design (claude.ai/design). Ask Maxim to share the current EasyBIM design file with you so you can work within the existing style, or create a new design from scratch if it is a completely new feature. This avoids two people accidentally working on the same thing at the same time. Approve the design with Maxim before writing any code.

### First-time setup

Install these four tools:

| Tool | What it is | Download |
|---|---|---|
| PyRevit | Loads the plugin into Revit | https://github.com/pyrevitlabs/pyRevit/releases |
| Git | Keeps your code in sync with GitHub | https://git-scm.com/download/win |
| VS Code | Code editor | https://code.visualstudio.com |
| Claude Code | AI that writes the code for you | https://claude.ai/download |

Once everything is installed, open PowerShell and run this one command — it downloads the plugin code to the exact folder where Revit will find it:

```powershell
git clone https://github.com/maximn-png/EasyBIM.Revit-Plugin "$env:APPDATA\pyRevit\Extensions\EasyBIM.extension"
```

> **Note:** the GitHub repository is named `EasyBIM.Revit-Plugin`, but the local folder must be named `EasyBIM.extension` — pyRevit only loads extension folders whose name ends with `.extension`. The clone command above handles this for you.

Then open Revit, go to **pyRevit tab → pyRevit Settings → Extensions**, find EasyBIM in the list, make sure it is enabled, save and restart Revit. You should see the EasyBIM tab in the ribbon.

### Building a button

**Step 1 — Sync and open**

Every time you sit down to work, run these commands first — even if you already have a branch from a previous session. The first two pull in any changes teammates have merged since you last worked. The third opens the plugin folder in VS Code.

```powershell
cd "$env:APPDATA\pyRevit\Extensions\EasyBIM.extension"
git fetch origin
git merge origin/main
code .
```

> **If you can't see new folders or buttons your teammates added** (for example, a new panel like `DBA.Panel`), it means your branch is behind `main`. Running `git fetch origin` followed by `git merge origin/main` will bring those changes onto your branch. New files merged into `main` do not appear on your branch automatically.

**Step 2 — Start a Claude Code session**

Inside VS Code, open Claude Code and describe what you want to build. Be specific — the more detail you give, the better the result. Paste your design from Claude Design.

**Step 2.5 — Create a development branch**

Before you write any code, create a branch for your work. A branch is your own private workspace — it keeps your changes separate from everyone else until you are ready to submit.

Open PowerShell and run:

```powershell
cd "$env:APPDATA\pyRevit\Extensions\EasyBIM.extension"
git checkout -b feature/name-of-your-button
```

Replace `name-of-your-button` with a short description of what you are building, using hyphens instead of spaces (for example: `feature/pipe-color-by-system` or `feature/room-area-tag`).

You are now on your own branch. Any changes Claude Code makes will stay here until you submit them for review.

**Step 3 — Test it in Revit**

Go to Revit and click **pyRevit tab → Reload**. Your new button appears in the ribbon right away. Click it and see if it does what you wanted. If something is wrong, go back to Claude Code, describe the issue, and reload again. Repeat until it works.

This loop — describe → reload → test → describe again — is the whole development process.

> **Note for developers:** Ideally your development folder and the folder Revit loads from should be the *same* folder. That is the whole point of the **edit → pyRevit Reload → test** loop: you edit files, and Revit runs those exact files — no copying, no build step, no separate deploy. If you develop in one location and Revit loads from another, your edits won't show up on reload and you'll waste time chasing changes that never reach Revit. Keep them as one and the same folder (the path under `%APPDATA%\pyRevit\Extensions\EasyBIM.extension`).

### What updates live vs. what requires a restart

This is important to understand so you don't waste time waiting for changes that won't appear.

**pyRevit Reload is enough for:**
- Script logic changes (`.py` files) — refreshed on every reload
- Tooltip and button title changes (`bundle.yaml`)

**Revit must be fully closed and reopened for:**
- Icon changes (`icon.png`)
- Adding or removing buttons or panels
- Any structural ribbon changes

**Why?** When Revit starts, it builds the ribbon and loads all `icon.png` files as bitmap objects into memory — once. When pyRevit reloads, it only refreshes the Python script engine. The ribbon UI already exists in Revit's memory and Revit provides no API to swap out button bitmaps on a live session. Icons are frozen until Revit restarts.

The practical rule: develop and iterate your script logic freely with pyRevit reload. Only work on icons when you are ready to commit a final design, and accept that each icon change needs a full Revit restart.

### Adding a new button (step by step)

1. **Pick a panel** — BIM Management, MEP Coordination, or Manage.
2. **Create a folder** — inside the panel folder, create `YourFeatureName.pushbutton`.
3. **Add `script.py`** — write your command logic. Always start with `#! python3`.
4. **Add `bundle.yaml`** — fill in title, tooltip, author, and keep `min_revit_ver: 2023`.
5. **Add to `.layout`** — open the panel's `.layout` file and add `YourFeatureName` on a new line.
6. **Add an icon** — place an `icon.png` (32×32 px) in your button folder. Remember: icon changes require a Revit restart to appear (see above).
7. **Test in Revit** — reload pyRevit and confirm the button works.

### Shared library

Put reusable code in `lib/easybim/` so other buttons can use it:

- `revit.py` — Revit API helpers (collectors, transactions)
- `ui.py` — alerts, dialogs, selection prompts
- `data.py` — parameter read/write utilities

Import in any script:
```python
from easybim import revit, ui, data
```

### Submitting your work

Once you are happy with your button, submit it for Maxim to review.

**Step 1 — Save your work to a branch**

A branch is your own private copy of the code on GitHub. It does not affect anyone else until Maxim approves it.

```powershell
cd "$env:APPDATA\pyRevit\Extensions\EasyBIM.extension"
git checkout -b feature/name-of-your-button
git add .
git commit -m "Add: short description of what the button does"
git push origin feature/name-of-your-button
```

Branch naming:
```
feature/short-description
fix/short-description
```

**Step 2 — Open a Pull Request**

Go to `https://github.com/maximn-png/EasyBIM.Revit-Plugin` — you will see a yellow banner with a **"Compare & pull request"** button. Click it, describe what your button does, and click **Create pull request**.

**Step 3 — Wait for Maxim's review**

Maxim will look at your code, may leave comments or ask for small changes, and when he is happy he will merge it. Once merged, the button goes live for the whole team on their next pyRevit sync and Revit reload.

### Rules

- Never commit directly to `main` — always open a PR.
- Keep each PR to one command or one fix.
- No hardcoded file paths or user-specific values.

---

## Folder Structure

```
EasyBIM.extension/
├── EasyBIM.tab/
│   ├── BIM Management.panel/      ← BIM coordination tools
│   ├── MEP Coordination.panel/    ← MEP-specific tools
│   └── Manage.panel/              ← data import/export and settings
│       └── ImportExcel.pushbutton/    ← example button (copy to start)
├── lib/
│   └── easybim/                   ← shared Python library
│       ├── revit.py
│       ├── ui.py
│       ├── data.py
│       └── syncguard.py
├── commands/                      ← headless scripts, no ribbon button
│   └── syncguard_command.py           ← run by `pyrevit run`, not clicked
├── tools/                         ← standalone utilities, run outside Revit
│   └── acc_issues_export.py           ← plain Python 3 + openpyxl
└── startup.py
```

**`commands/` vs `tools/` vs a pushbutton.** A pushbutton is clicked by a person
inside Revit. A `commands/` script runs *inside* Revit but with no UI, launched
from the command line as
`pyrevit run <absolute path to the script> --revit=2025 --purge` — that is how
automation drives Revit on a workstation. A `tools/` script never touches Revit
at all and runs under a normal Python 3 install. Shared logic still belongs in
`lib/easybim/` so a button and a headless command can use the same code.

---

## Why These Rules Exist

**Why can't I just push directly to main?**
The main branch is what everyone on the team runs. A bug there breaks Revit for the whole team simultaneously. The review step is a two-minute safety check that prevents this.

**Why branches?**
A branch lets you work freely without affecting anyone else. You can experiment, break things, and fix them — none of that touches the live version until you are ready and it has been reviewed.

**Why GitHub at all?**
GitHub keeps the full history of every change. If a button breaks something, we can immediately see exactly what changed and who changed it. It also makes sure everyone is always working from the same version of the code.

---

## Quick Reference

| What you want to do | How |
|---|---|
| Get access to the repo | Ask Maxim to add your GitHub username |
| Set up for the first time | Run the `git clone` command above, enable in PyRevit Settings |
| Sync before starting work | `git fetch origin` then `git merge origin/main` |
| Start building a button | Open folder in VS Code, describe to Claude Code |
| Test script changes | pyRevit tab → Reload (no restart needed) |
| Test icon changes | Close and reopen Revit |
| Submit your work | `git checkout -b`, `git add .`, `git commit`, `git push`, open PR on GitHub |
| Get the latest buttons (non-developer) | Double-click `sync.bat`, then pyRevit tab → Reload |
| Get help | Ask Maxim |
