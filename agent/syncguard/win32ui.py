"""Tray icon and dialogs, via Win32 through ctypes.

Hand-rolled because the runtime is the CPython pyRevit ships, which is an
embedded distribution with no pip — so pystray and tkinter are both off the
table. See agent/syncguard/__init__.py for why that runtime was chosen.

This module is presentation only. Every decision lives in core.py, so the same
core runs headless on a shared workstation with no tray at all.

THREADING. The tray window is created on the main thread and that thread pumps
its messages. Core threads never touch it directly: `on_state_changed` and
`notify` marshal across with PostMessage, because calling Shell_NotifyIcon from
a foreign thread is a good way to get a silently dead icon. The takeover dialog
is the deliberate exception — it is created ON the calling core thread and pumps
its own loop there, which Win32 allows for a window whose thread owns it. That
keeps the blocking "may I take your desktop?" question synchronous for the
caller without a cross-thread handshake.
"""

import ctypes
import os
import threading
import webbrowser
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
gdi32 = ctypes.windll.gdi32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_SETFONT = 0x0030
WM_APP = 0x8000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400

TRAY_CALLBACK = WM_APP + 1
MSG_REFRESH = WM_APP + 2
MSG_BALLOON = WM_APP + 3

NIN_BALLOONUSERCLICK = WM_USER + 5

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING = 0x01, 0x02

TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED = 0x0, 0x800, 0x1, 0x8

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040

WS_CAPTION, WS_SYSMENU, WS_VISIBLE, WS_CHILD = 0x00C00000, 0x00080000, \
    0x10000000, 0x40000000
WS_OVERLAPPED = 0x00000000
WS_EX_TOPMOST, WS_EX_DLGMODALFRAME = 0x00000008, 0x00000001
BS_DEFPUSHBUTTON = 0x0001
ES_AUTOHSCROLL, ES_UPPERCASE = 0x0080, 0x0008
WS_BORDER, WS_TABSTOP = 0x00800000, 0x00010000
SW_HIDE = 0
SWP_NOSIZE, SWP_NOZORDER = 0x0001, 0x0004
COLOR_WINDOW = 5

# Menu command ids
ID_STATUS = 1
ID_CONNECT = 2
ID_RECHECK = 3
ID_PAUSE = 4
ID_LOGS = 5
ID_OPEN_WEB = 6
ID_QUIT = 7

ICON_BY_STATUS = {
    "idle": "ready.ico",
    "running": "busy.ico",
    "not_enrolled": "off.ico",
    "offline": "off.ico",
    "paused": "off.ico",
    "disabled": "off.ico",
}


def _icons_dir():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.restype = wintypes.HICON


def enable_dpi_awareness():
    """Crisp text on a scaled display. Best-effort across Windows versions."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


_gui_font = None


def _font():
    global _gui_font
    if _gui_font is None:
        # Segoe UI so the dialogs do not look like 1995.
        _gui_font = gdi32.CreateFontW(
            -15, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
    return _gui_font


def _set_font(hwnd):
    user32.SendMessageW(hwnd, WM_SETFONT, _font(), 1)


class _ModalWindow(object):
    """A small custom window that pumps its own loop until it has an answer.

    Built from predefined control classes (STATIC / BUTTON / EDIT) rather than
    a dialog template, because there is no resource file to put a template in.
    """

    _class_registered = False
    _class_name = "EasyBIMSyncguardDialog"
    _keep_alive = []

    def __init__(self, title, width=460, height=190):
        self._result = None
        self._done = False
        self._controls = {}
        self._timer_cb = None
        self._proc = WNDPROC(self._wndproc)
        _ModalWindow._keep_alive.append(self._proc)

        cls_name = "%s%d" % (self._class_name, id(self))
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = cls_name
        wc.hbrBackground = user32.GetSysColorBrush(COLOR_WINDOW)
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        user32.RegisterClassExW(ctypes.byref(wc))
        self._wc = wc  # keep alive
        self._cls_name = cls_name

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = int((screen_w - width) / 2)
        y = int((screen_h - height) / 3)
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_DLGMODALFRAME, cls_name, title,
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
            x, y, width, height, None, None,
            kernel32.GetModuleHandleW(None), None)
        user32.SetForegroundWindow(self.hwnd)

    # -- building ---------------------------------------------------------

    def label(self, text, x, y, w, h):
        hwnd = user32.CreateWindowExW(
            0, "STATIC", text, WS_CHILD | WS_VISIBLE, x, y, w, h,
            self.hwnd, None, kernel32.GetModuleHandleW(None), None)
        _set_font(hwnd)
        return hwnd

    def button(self, text, cmd_id, x, y, w, h, default=False):
        style = WS_CHILD | WS_VISIBLE | WS_TABSTOP
        if default:
            style |= BS_DEFPUSHBUTTON
        hwnd = user32.CreateWindowExW(
            0, "BUTTON", text, style, x, y, w, h, self.hwnd,
            wintypes.HMENU(cmd_id),
            kernel32.GetModuleHandleW(None), None)
        _set_font(hwnd)
        self._controls[cmd_id] = hwnd
        return hwnd

    def edit(self, x, y, w, h, upper=False):
        style = WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL
        if upper:
            style |= ES_UPPERCASE
        hwnd = user32.CreateWindowExW(
            0, "EDIT", "", style, x, y, w, h, self.hwnd, None,
            kernel32.GetModuleHandleW(None), None)
        _set_font(hwnd)
        self._edit = hwnd
        user32.SetFocus(hwnd)
        return hwnd

    def edit_text(self):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(self._edit, buf, 256)
        return buf.value

    def set_label(self, hwnd, text):
        user32.SetWindowTextW(hwnd, text)

    def on_timer(self, seconds, callback):
        self._timer_cb = callback
        user32.SetTimer(self.hwnd, 1, int(seconds * 1000), None)

    # -- loop -------------------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_COMMAND:
            self.on_command(int(wparam) & 0xFFFF)
            return 0
        if msg == WM_TIMER:
            if self._timer_cb:
                self._timer_cb()
            return 0
        if msg == WM_CLOSE:
            self.on_close()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def on_command(self, cmd_id):
        self.finish(cmd_id)

    def on_close(self):
        self.finish(None)

    def finish(self, result):
        self._result = result
        self._done = True
        user32.KillTimer(self.hwnd, 1)
        user32.DestroyWindow(self.hwnd)
        # Wake this thread's loop so it re-tests _done. Deliberately NOT
        # PostQuitMessage: when this dialog is opened from the tray's own
        # thread, a WM_QUIT would tear down the tray's message loop and take
        # the whole agent's UI with it.
        user32.PostThreadMessageW(kernel32.GetCurrentThreadId(), 0, 0, 0)

    def run(self):
        msg = wintypes.MSG()
        while not self._done:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterClassW(self._cls_name,
                                kernel32.GetModuleHandleW(None))
        return self._result


class _TakeoverDialog(_ModalWindow):
    """'Revit is about to open' warning, with a countdown and a defer.

    Auto-starts when the countdown expires: a run must not stall because nobody
    is looking at the screen, which is the common case even on a personal
    machine.
    """

    def __init__(self, model_name, seconds, last_chance):
        super().__init__("EasyBIM Syncguard", 470, 205)
        self._left = seconds
        self.label(
            "Revit is about to open on this computer to sync:", 18, 16, 430, 20)
        self.label(model_name or "a coordination model", 18, 38, 430, 20)
        self.label(
            "It will take over the screen for several minutes. Save your work.",
            18, 68, 430, 20)
        self._count = self.label("", 18, 96, 430, 20)
        self.button("Start now", ID_STATUS, 250, 128, 92, 30, default=True)
        if not last_chance:
            self.button("Defer 10 min", ID_CONNECT, 350, 128, 100, 30)
        else:
            self.label("Cannot be deferred again.", 18, 132, 220, 20)
        self._tick()
        self.on_timer(1, self._tick)

    def _tick(self):
        if self._left <= 0:
            self.finish(ID_STATUS)
            return
        self.set_label(self._count, "Starting automatically in %d seconds…"
                       % self._left)
        self._left -= 1

    def on_command(self, cmd_id):
        self.finish(cmd_id)

    def on_close(self):
        # Closing the window is not consent to lose the run; treat it as start.
        self.finish(ID_STATUS)


class _CodeDialog(_ModalWindow):
    def __init__(self, machine_name, base_url):
        super().__init__("Connect to EasyBIM", 470, 215)
        self.label("In EasyBIM, open the project's Syncguard panel and choose",
                   18, 14, 430, 18)
        self.label("\"Add this computer\". Type the code it shows:",
                   18, 34, 430, 18)
        self.edit(18, 62, 200, 28, upper=True)
        self.label("This computer: %s" % machine_name, 18, 100, 430, 18)
        self.label("Server: %s" % base_url, 18, 120, 430, 18)
        self.button("Connect", ID_CONNECT, 250, 150, 92, 30, default=True)
        self.button("Cancel", ID_QUIT, 350, 150, 92, 30)
        self._value = None

    def on_command(self, cmd_id):
        if cmd_id == ID_CONNECT:
            self._value = self.edit_text()
        self.finish(cmd_id)

    def value(self):
        return self._value


def message_box(text, title="EasyBIM Syncguard", warning=False):
    flags = 0x40 | 0x1000  # MB_ICONINFORMATION | MB_SETFOREGROUND
    if warning:
        flags = 0x30 | 0x1000
    user32.MessageBoxW(None, text, title, flags)


class TrayUi(object):
    """The tray icon, its menu, and the dialogs it opens."""

    can_prompt = True

    def __init__(self, core_holder, on_quit):
        self._core_holder = core_holder
        self._on_quit = on_quit
        self._icons = {}
        self._current_icon = None
        self._proc = WNDPROC(self._wndproc)
        self._pending_balloon = None
        self._balloon_lock = threading.Lock()
        self._prompted_enrollment = False

        cls_name = "EasyBIMSyncguardTray"
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = cls_name
        user32.RegisterClassExW(ctypes.byref(wc))
        self._wc = wc
        self._cls_name = cls_name
        self.hwnd = user32.CreateWindowExW(
            0, cls_name, "EasyBIM Syncguard", WS_OVERLAPPED, 0, 0, 0, 0,
            None, None, kernel32.GetModuleHandleW(None), None)
        user32.ShowWindow(self.hwnd, SW_HIDE)

        self._nid = NOTIFYICONDATAW()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._nid.hWnd = self.hwnd
        self._nid.uID = 1
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = TRAY_CALLBACK
        self._nid.hIcon = self._icon_for("not_enrolled")
        self._nid.szTip = "EasyBIM Syncguard"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))

    # -- icon / tooltip ---------------------------------------------------

    def _icon_for(self, status):
        name = ICON_BY_STATUS.get(status, "off.ico")
        core = self._core_holder()
        if core:
            state = core.snapshot_status()
            # Attention outranks the plain status: a lost sign-in or a failed
            # last run is the thing worth a glance at the taskbar.
            if state["enrolled"] and (not state["signedIn"]
                                      or state["lastError"]):
                name = "warn.ico"
        if name not in self._icons:
            path = os.path.join(_icons_dir(), name)
            handle = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not handle:
                handle = user32.LoadIconW(None, 32512)  # IDI_APPLICATION
            self._icons[name] = handle
        return self._icons[name]

    def _tooltip(self, state):
        if not state["enrolled"]:
            return "EasyBIM Syncguard — not connected. Click to connect."
        bits = ["EasyBIM Syncguard — %s" % state["machineName"]]
        if state["status"] == "running":
            bits.append("Syncing %s" % (state["current"] or "a model"))
        elif state["status"] == "disabled":
            bits.append("Disabled by an administrator")
        elif state["status"] == "paused":
            bits.append("Paused")
        elif not state["online"]:
            bits.append("Cannot reach EasyBIM")
        else:
            bits.append("Ready")
        if not state["signedIn"]:
            bits.append("Autodesk sign-in needed")
        return "\n".join(bits)[:127]

    def on_state_changed(self):
        """Called from core threads — marshal to the UI thread."""
        user32.PostMessageW(self.hwnd, MSG_REFRESH, 0, 0)

    def notify(self, title, text, warning=False):
        with self._balloon_lock:
            self._pending_balloon = (title, text, warning)
        user32.PostMessageW(self.hwnd, MSG_BALLOON, 0, 0)

    def _refresh(self):
        core = self._core_holder()
        if not core:
            return
        state = core.snapshot_status()
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.hIcon = self._icon_for(state["status"])
        self._nid.szTip = self._tooltip(state)
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

        # First-run nudge. A tester went looking in the Revit ribbon for where
        # to type the pairing code, so the tray has to say so unprompted.
        if not state["enrolled"] and not self._prompted_enrollment:
            self._prompted_enrollment = True
            self.notify("Syncguard is not connected yet",
                        "Click here to enter the code from EasyBIM.")

    def _show_balloon(self):
        with self._balloon_lock:
            pending = self._pending_balloon
            self._pending_balloon = None
        if not pending:
            return
        title, text, warning = pending
        self._nid.uFlags = NIF_INFO | NIF_ICON | NIF_TIP | NIF_MESSAGE
        self._nid.szInfoTitle = title[:63]
        self._nid.szInfo = text[:255]
        self._nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    # -- menu -------------------------------------------------------------

    def _show_menu(self):
        core = self._core_holder()
        if not core:
            return
        state = core.snapshot_status()
        menu = user32.CreatePopupMenu()

        headline = {
            "not_enrolled": "Not connected to EasyBIM",
            "idle": "Ready — %s" % state["machineName"],
            "running": "Syncing %s" % (state["current"] or "a model"),
            "offline": "Cannot reach EasyBIM",
            "paused": "Paused",
            "disabled": "Disabled by an administrator",
        }.get(state["status"], state["status"])
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS, headline)
        if not state["signedIn"]:
            mins = int((state["signinCooldown"] + 59) / 60)
            user32.AppendMenuW(
                menu, MF_STRING | MF_GRAYED, ID_STATUS,
                "  Autodesk sign-in needed (retrying in %d min)" % mins)
        elif state["lastError"]:
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS,
                               "  %s" % state["lastError"][:70])
        elif state["lastResult"]:
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS,
                               "  Last: %s" % state["lastResult"][:70])
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

        if state["enrolled"]:
            user32.AppendMenuW(menu, MF_STRING, ID_RECHECK,
                               "Recheck Autodesk sign-in")
            flags = MF_STRING | (MF_CHECKED if state["paused"] else 0)
            user32.AppendMenuW(menu, flags, ID_PAUSE, "Pause new jobs")
        else:
            user32.AppendMenuW(menu, MF_STRING, ID_CONNECT,
                               "Connect to EasyBIM…")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN_WEB, "Open EasyBIM")
        user32.AppendMenuW(menu, MF_STRING, ID_LOGS, "Open log folder")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit Syncguard")

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)
        choice = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0,
            self.hwnd, None)
        user32.DestroyMenu(menu)
        if choice:
            self._on_menu(int(choice))

    def _on_menu(self, cmd_id):
        core = self._core_holder()
        if not core:
            return
        if cmd_id == ID_CONNECT:
            self.prompt_enrollment()
        elif cmd_id == ID_RECHECK:
            core.recheck_signin()
            self.notify("Sign-in re-check",
                        "Syncguard will try the next job normally.")
        elif cmd_id == ID_PAUSE:
            state = core.snapshot_status()
            core.set_paused(not state["paused"])
        elif cmd_id == ID_LOGS:
            from . import config
            try:
                os.startfile(config.ensure_state_dir())
            except Exception:
                pass
        elif cmd_id == ID_OPEN_WEB:
            try:
                webbrowser.open(core.base_url)
            except Exception:
                pass
        elif cmd_id == ID_QUIT:
            self._on_quit()

    def prompt_enrollment(self):
        core = self._core_holder()
        if not core:
            return
        dialog = _CodeDialog(core.machine_name, core.base_url)
        choice = dialog.run()
        if choice != ID_CONNECT:
            return
        code = (dialog.value() or "").strip()
        if not code:
            return
        ok, message = core.enroll(code)
        message_box(message, warning=not ok)
        self._refresh()

    # -- called from a core thread ----------------------------------------

    def confirm_takeover(self, model_name, seconds, last_chance=False):
        """Blocking warning before Revit steals the desktop.

        Runs on the caller's (core) thread with its own message pump, so the
        answer comes back synchronously. Returns "start" or "defer".
        """
        try:
            dialog = _TakeoverDialog(model_name, seconds, last_chance)
            choice = dialog.run()
        except Exception:
            return "start"
        return "defer" if choice == ID_CONNECT else "start"

    # -- window -----------------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event in (WM_LBUTTONUP, WM_RBUTTONUP):
                self._show_menu()
            elif event == NIN_BALLOONUSERCLICK:
                core = self._core_holder()
                if core and not core.enrolled:
                    self.prompt_enrollment()
            return 0
        if msg == MSG_REFRESH:
            self._refresh()
            return 0
        if msg == MSG_BALLOON:
            self._show_balloon()
            return 0
        if msg == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def close(self):
        user32.DestroyWindow(self.hwnd)

    def run(self):
        self._refresh()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
