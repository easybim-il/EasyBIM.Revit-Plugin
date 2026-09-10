"""Syncguard tray agent — the workstation half of Syncguard.

Five jobs: enroll once, heartbeat, claim work, run it, report the result.
Outbound HTTPS only, so no inbound ports and no firewall work.

Runs on the CPython that pyRevit already ships (see agent/README.md), because
pyRevit is a hard dependency anyway — the agent's whole purpose is to shell out
to `pyrevit run`. That keeps installation to one unit and adds no second runtime
for a BIM engineer to maintain. Consequences of that choice, visible throughout:
stdlib only (no pip in an embedded distribution), so HTTP is urllib and the tray
UI is Win32 via ctypes rather than pystray or tkinter.

Started by a logon scheduled task, never a Windows service: Session 0 isolation
means a service cannot launch an interactive Revit, and both Revit licensing and
the Autodesk sign-in live in the user profile.

The core is headless. The tray is presentation layered on top, so a shared
always-on workstation runs the same code invisibly with `--headless`.
"""

AGENT_VERSION = "0.1.0"
