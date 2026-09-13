"""
Mute Current Application
-------------------------
Zwei einstellbare Hotkeys wirken auf die Lautstaerke des Programms im
aktuell aktiven (fokussierten) Fenster:

  - toggle_mute_key:      schaltet Stumm/Ton an um
  - fixed_volume_key:     setzt die Lautstaerke auf einen festen Prozentwert

Einstellungen liegen in mute_config.json (gleicher Ordner) und koennen
ueber das Tray-Icon -> "Einstellungen..." geaendert werden.
"""

import ctypes
import json
import logging
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import comtypes
import psutil
import win32api
import win32con
import win32gui
import win32process
from pycaw.pycaw import AudioUtilities
from PIL import Image, ImageDraw
import pystray

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(APP_DIR, "mute_config.json")
LOG_PATH = os.path.join(APP_DIR, "mute_debug.log")

logging.basicConfig(
    filename=LOG_PATH,
    filemode="w",
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("mute_app")
log.setLevel(logging.DEBUG)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    exe = sys.executable
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, APP_DIR, 1)

DEFAULT_CONFIG = {
    "toggle_mute_key": "f1",
    "fixed_volume_key": "f2",
    "fixed_volume_percent": 20,
}


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            config.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    config["fixed_volume_percent"] = max(0, min(100, int(config.get("fixed_volume_percent", 20))))
    return config


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def get_foreground_process():
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if not pid:
        return None
    try:
        return psutil.Process(pid)
    except psutil.Error:
        return None


def get_target_sessions():
    """Audio-Sessions, die zum aktiven Fenster gehoeren (inkl. Kindprozesse
    und weiterer Prozesse mit gleichem Namen, z.B. mehrere Spiel-Prozesse)."""
    proc = get_foreground_process()
    if proc is None:
        return [], None

    try:
        proc_name = proc.name().lower()
    except psutil.Error:
        return [], None

    pid_set = {proc.pid}
    try:
        for child in proc.children(recursive=True):
            pid_set.add(child.pid)
    except psutil.Error:
        pass

    try:
        for p in psutil.process_iter(["pid", "name"]):
            if p.info.get("name", "").lower() == proc_name:
                pid_set.add(p.info["pid"])
    except psutil.Error:
        pass

    matched = []
    for session in AudioUtilities.GetAllSessions():
        if session.Process and session.Process.pid in pid_set:
            matched.append(session)

    log.debug(
        "foreground=%s pid=%s candidate_pids=%s matched_sessions=%d",
        proc_name, proc.pid, sorted(pid_set), len(matched),
    )
    return matched, proc_name


def notify(icon, message):
    try:
        icon.notify(message, "Mute Current Application")
    except Exception:
        pass


def toggle_mute(icon):
    log.debug("toggle_mute hotkey fired")
    try:
        sessions, proc_name = get_target_sessions()
        if not sessions:
            log.debug("toggle_mute: no matching audio session found")
            return

        any_unmuted = any(s.SimpleAudioVolume.GetMute() == 0 for s in sessions)
        target_mute = 1 if any_unmuted else 0
        for s in sessions:
            s.SimpleAudioVolume.SetMute(target_mute, None)

        log.debug("toggle_mute: %s -> mute=%d", proc_name, target_mute)
        notify(icon, f"{proc_name}: {'Stumm' if target_mute else 'Ton an'}")
    except Exception:
        log.exception("toggle_mute failed")


def set_fixed_volume(icon, config):
    log.debug("set_fixed_volume hotkey fired")
    try:
        sessions, proc_name = get_target_sessions()
        if not sessions:
            log.debug("set_fixed_volume: no matching audio session found")
            return

        fixed_level = config["fixed_volume_percent"] / 100.0
        current_level = sessions[0].SimpleAudioVolume.GetMasterVolume()
        at_fixed_level = abs(current_level - fixed_level) < 0.01
        target_level = 1.0 if at_fixed_level else fixed_level

        for s in sessions:
            s.SimpleAudioVolume.SetMasterVolume(target_level, None)
            s.SimpleAudioVolume.SetMute(0, None)

        log.debug("set_fixed_volume: %s -> %d%%", proc_name, round(target_level * 100))
        notify(icon, f"{proc_name}: Lautstaerke {round(target_level * 100)}%")
    except Exception:
        log.exception("set_fixed_volume failed")


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN,
}

NAMED_VK = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}


def parse_hotkey(spec):
    """Wandelt z.B. 'ctrl+f2' oder 'f5' in (modifiers, vk_code) um."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("leere Tastenkombination")

    key_part = parts[-1]
    mods = 0
    for p in parts[:-1]:
        if p not in MODIFIER_NAMES:
            raise ValueError(f"unbekannter Modifier '{p}'")
        mods |= MODIFIER_NAMES[p]

    vk = None
    m = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key_part)
    if m:
        vk = 0x70 + (int(m.group(1)) - 1)
    elif key_part in NAMED_VK:
        vk = NAMED_VK[key_part]
    elif len(key_part) == 1 and (key_part.isalpha() or key_part.isdigit()):
        vk = ord(key_part.upper())

    if vk is None:
        raise ValueError(f"unbekannte Taste '{key_part}'")

    return mods, vk


WM_APPLY_HOTKEYS = win32con.WM_APP + 1


class HotkeyManager:
    """Verwaltet globale Hotkeys ueber die native RegisterHotKey-API.

    Ein verstecktes Fenster mit eigener Nachrichtenschleife empfaengt
    WM_HOTKEY unabhaengig davon, welches Fenster gerade den Fokus hat.
    """

    CLASS_NAME = "MuteCurrentApplicationHiddenWindow"

    def __init__(self, callbacks_by_slot):
        self.callbacks_by_slot = callbacks_by_slot  # {"toggle_mute_key": fn, "fixed_volume_key": fn}
        self.hwnd = None
        self._registered_ids = {}  # id -> slot name
        self._next_id = 1
        self._pending_config = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Hotkey-Fenster konnte nicht erstellt werden")

    def _run(self):
        comtypes.CoInitialize()
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc
        wc.lpszClassName = self.CLASS_NAME
        wc.hInstance = win32api.GetModuleHandle(None)
        try:
            atom = win32gui.RegisterClass(wc)
        except Exception:
            atom = self.CLASS_NAME
        self.hwnd = win32gui.CreateWindow(
            atom, "MuteCurrentApplicationHotkeys", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )
        self._ready.set()
        win32gui.PumpMessages()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_HOTKEY:
            slot = self._registered_ids.get(wparam)
            if slot:
                try:
                    self.callbacks_by_slot[slot]()
                except Exception:
                    log.exception("hotkey callback for '%s' failed", slot)
            return 0
        if msg == WM_APPLY_HOTKEYS:
            self._apply_pending()
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _apply_pending(self):
        for hotkey_id in list(self._registered_ids.keys()):
            ctypes.windll.user32.UnregisterHotKey(self.hwnd, hotkey_id)
        self._registered_ids.clear()

        config = self._pending_config
        for slot in ("toggle_mute_key", "fixed_volume_key"):
            spec = config[slot]
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError as e:
                log.error("Hotkey '%s' (%s) ungueltig: %s", spec, slot, e)
                continue
            hotkey_id = self._next_id
            self._next_id += 1
            ok = ctypes.windll.user32.RegisterHotKey(self.hwnd, hotkey_id, mods | MOD_NOREPEAT, vk)
            if ok:
                self._registered_ids[hotkey_id] = slot
                log.info("registered %s='%s' (mods=0x%X vk=0x%02X)", slot, spec, mods, vk)
            else:
                err = ctypes.windll.kernel32.GetLastError()
                log.error(
                    "RegisterHotKey fuer %s='%s' fehlgeschlagen (evtl. von einem anderen Programm belegt, Fehlercode %d)",
                    slot, spec, err,
                )

    def apply(self, config):
        self._pending_config = config
        win32gui.PostMessage(self.hwnd, WM_APPLY_HOTKEYS, 0, 0)

    def shutdown(self):
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)


def make_icon_image():
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(8, 24), (24, 24), (38, 12), (38, 52), (24, 40), (8, 40)], fill=(40, 180, 90, 255))
    d.line([(46, 20), (58, 44)], fill=(200, 40, 40, 255), width=6)
    d.line([(58, 20), (46, 44)], fill=(200, 40, 40, 255), width=6)
    return img


class SettingsUI:
    """Tkinter-Einstellungsfenster, laeuft auf dem Tk-Hauptthread."""

    def __init__(self, root, config, on_save):
        self.root = root
        self.config = config
        self.on_save = on_save

    def open(self):
        self.root.after(0, self._build)

    def _build(self):
        win = tk.Toplevel(self.root)
        win.title("Mute Current Application - Einstellungen")
        win.resizable(False, False)
        win.attributes("-topmost", True)

        pad = {"padx": 10, "pady": 6}

        ttk.Label(win, text="Taste fuer Stumm/Ton-Umschalten:").grid(row=0, column=0, sticky="w", **pad)
        mute_var = tk.StringVar(value=self.config["toggle_mute_key"])
        ttk.Entry(win, textvariable=mute_var, width=20).grid(row=0, column=1, **pad)

        ttk.Label(win, text="Taste fuer festen Lautstaerkewert:").grid(row=1, column=0, sticky="w", **pad)
        fixed_key_var = tk.StringVar(value=self.config["fixed_volume_key"])
        ttk.Entry(win, textvariable=fixed_key_var, width=20).grid(row=1, column=1, **pad)

        ttk.Label(win, text="Fester Lautstaerkewert (%):").grid(row=2, column=0, sticky="w", **pad)
        volume_var = tk.StringVar(value=str(self.config["fixed_volume_percent"]))
        ttk.Entry(win, textvariable=volume_var, width=20).grid(row=2, column=1, **pad)

        hint = ttk.Label(
            win,
            text="Beispiele fuer Tastennamen: f1, f13, ctrl+f2, alt+m",
            foreground="#666666",
        )
        hint.grid(row=3, column=0, columnspan=2, sticky="w", padx=10)

        def save():
            new_mute_key = mute_var.get().strip().lower()
            new_fixed_key = fixed_key_var.get().strip().lower()
            try:
                new_volume = int(volume_var.get().strip())
            except ValueError:
                messagebox.showerror("Ungueltig", "Lautstaerkewert muss eine Zahl zwischen 0 und 100 sein.", parent=win)
                return
            if not (0 <= new_volume <= 100):
                messagebox.showerror("Ungueltig", "Lautstaerkewert muss zwischen 0 und 100 liegen.", parent=win)
                return
            if not new_mute_key or not new_fixed_key:
                messagebox.showerror("Ungueltig", "Beide Tasten muessen gesetzt sein.", parent=win)
                return

            new_config = {
                "toggle_mute_key": new_mute_key,
                "fixed_volume_key": new_fixed_key,
                "fixed_volume_percent": new_volume,
            }
            try:
                self.on_save(new_config)
            except Exception as e:
                messagebox.showerror("Fehler", f"Hotkeys konnten nicht gesetzt werden:\n{e}", parent=win)
                return
            self.config = new_config
            win.destroy()

        btn_frame = ttk.Frame(win)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(10, 10))
        ttk.Button(btn_frame, text="Speichern", command=save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Abbrechen", command=win.destroy).pack(side="left", padx=5)

        win.grab_set()
        win.focus_force()


def main():
    if not is_admin():
        log.info("not running as admin, relaunching elevated")
        try:
            relaunch_as_admin()
        except Exception:
            log.exception("elevation failed, continuing without admin rights")
        else:
            return

    log.info("running as admin=%s", is_admin())

    config = load_config()
    save_config(config)

    root = tk.Tk()
    root.withdraw()

    icon_holder = {}

    def on_save(new_config):
        nonlocal config
        hotkeys.apply(new_config)
        config = new_config
        save_config(config)
        settings_ui.config = config

    settings_ui = SettingsUI(root, config, on_save)

    def open_settings(icon=None, item=None):
        settings_ui.config = config
        settings_ui.open()

    def quit_app(icon=None, item=None):
        icon.stop()
        root.after(0, root.quit)

    menu = pystray.Menu(
        pystray.MenuItem("Einstellungen...", open_settings, default=True),
        pystray.MenuItem("Beenden", quit_app),
    )

    icon = pystray.Icon("mute_current_application", make_icon_image(), "Mute Current Application", menu)
    icon_holder["icon"] = icon

    hotkeys = HotkeyManager({
        "toggle_mute_key": lambda: toggle_mute(icon),
        "fixed_volume_key": lambda: set_fixed_volume(icon, config),
    })
    hotkeys.apply(config)

    threading.Thread(target=icon.run, daemon=True).start()

    root.mainloop()
    hotkeys.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("fatal error in main")
        raise
