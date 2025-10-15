#!/usr/bin/env python3
"""
Macro recorder/runner for X11/desktop using pynput with per-event relative delays.

Highlights:
- Overwrite prompt on 'record' if the output file already exists.
- Unified positional PATH for both 'record' and 'run' (no -i/-o flags).
- 'play' renamed to 'run'.
- Bottom-right overlay countdown during 'run' (default). Disable with -d none.
- Auto-install missing dependencies (best effort), plus manual install guide below.
- Performance: countdown runs in its own lightweight thread; event timing is not slowed down.

Manual install quick guide:
    # Core dependency (all platforms)
    python3 -m pip install --upgrade pip
    python3 -m pip install pynput

    # Overlay (Tkinter) — needed for the default bottom-right dialog
    # Debian/Ubuntu:
    sudo apt-get update && sudo apt-get install -y python3-tk
    # Fedora/RHEL:
    sudo dnf install -y python3-tkinter
    # Arch/Manjaro:
    sudo pacman -S --noconfirm tk
    # openSUSE:
    sudo zypper install -y python3-tk
    # macOS (Python.org builds often include Tk; with Homebrew:)
    brew install tcl-tk   # ensure your Python uses this Tk (see brew caveats)

Usage examples:
    python3 macro.py record
    python3 macro.py record mymacro.json
    python3 macro.py run                    # default: -s 1.0 -d overlay (bottom-right)
    python3 macro.py run macro.json -s 1.5  # overlay still default
    python3 macro.py run macro.json -d none # disable countdown dialog
"""

import os
import sys
import time
import json
import argparse
import subprocess
import shutil
from pathlib import Path
from threading import Event, Thread
from typing import Optional, List, Tuple


# ------------------------ Dependency management ------------------------------

def _pip_install(pkg: str) -> bool:
    """Try to install a PyPI package using pip. Returns True if success."""
    try:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.returncode == 0
    except Exception:
        return False


def _try_import(modname: str):
    """Import a module by name, returning the module or None on failure."""
    try:
        return __import__(modname)
    except Exception:
        return None


def _try_install_tkinter() -> bool:
    """
    Best-effort installation of tkinter via system package manager (Linux).
    Returns True if tkinter import becomes available, False otherwise.
    Requires root privileges for system package managers.
    """
    if _try_import("tkinter") is not None:
        return True

    # Try system package managers (Linux) if root
    try:
        is_root = (os.geteuid() == 0)
    except Exception:
        is_root = False

    if not is_root:
        return False

    managers = [
        ("apt-get", ["apt-get", "update"], ["apt-get", "install", "-y", "python3-tk"]),
        ("dnf", None, ["dnf", "install", "-y", "python3-tkinter"]),
        ("zypper", None, ["zypper", "install", "-y", "python3-tk"]),
        ("pacman", None, ["pacman", "-S", "--noconfirm", "tk"]),
        ("apk", None, ["apk", "add", "tcl", "tk", "python3-tkinter"]),
    ]
    for name, pre, cmd in managers:
        if shutil.which(name):
            try:
                if pre:
                    subprocess.run(pre, check=False)
                subprocess.run(cmd, check=True)
                return _try_import("tkinter") is not None
            except Exception:
                continue
    return False


def ensure_dependencies(dialog_mode: str) -> str:
    """
    Ensure required modules are importable. Installs 'pynput' via pip if missing.
    For overlay dialog mode, also ensure 'tkinter' (best effort).
    Returns the possibly adjusted dialog mode (fallback to 'none' if Tk is unavailable).
    """
    # Ensure pynput (core)
    if _try_import("pynput") is None:
        if not _pip_install("pynput") or _try_import("pynput") is None:
            print("⚠️ Unable to auto-install 'pynput'. Please install it manually:\n"
                  "    python3 -m pip install pynput")
            sys.exit(1)

    # Overlay dependencies (tkinter)
    if dialog_mode == "overlay":
        if _try_import("tkinter") is None and not _try_install_tkinter():
            print("⚠️ 'tkinter' not available; disabling countdown dialog.")
            dialog_mode = "none"

    return dialog_mode


# ------------------------ Abort key configuration ----------------------------

ABORT_KEY_STR = "Key.esc"   # string form for storage and comparison
ABORT_KEY_HUMAN = "ESC"

def _get_abort_key():
    """Import and return the pynput Key object for ESC lazily."""
    from pynput import keyboard
    return keyboard.Key.esc


# ------------------------ Bottom-right Overlay -------------------------------

class _OverlayStatus:
    """Tiny always-on-top overlay at the bottom-right that shows a countdown."""

    def __init__(self) -> None:
        """
        Create a small overlay near the bottom-right edge.
        """
        self._alive = Event()
        self._alive.set()
        self._text_queue: List[str] = []
        self._thread: Optional[Thread] = None
        self._ready = Event()

        # Start the Tk UI in a background thread to avoid blocking the runner.
        self._thread = Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def update_text(self, text: str) -> None:
        """Queue a text update for the overlay label."""
        self._text_queue.append(text)

    def close(self) -> None:
        """Close the overlay."""
        self._alive.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ---- Internal: Tk thread ------------------------------------------------
    def _run_tk(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            self._ready.set()
            return

        root = tk.Tk()
        root.overrideredirect(True)  # borderless
        try:
            root.wm_attributes("-topmost", 1)
            root.wm_attributes("-alpha", 0.85)  # slight transparency
        except Exception:
            pass

        lbl = tk.Label(
            root,
            text="",
            font=("DejaVu Sans", 10),
            bg="#222222",
            fg="#DDDDDD",
            padx=10,
            pady=4,
        )
        lbl.pack()

        # Compute initial geometry (bottom-right, a few px above border)
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = lbl.winfo_reqwidth()
        h = lbl.winfo_reqheight()
        margin = 8
        x = max(0, sw - w - margin)
        y = max(0, sh - h - margin)
        root.geometry(f"{w}x{h}+{x}+{y}")

        self._ready.set()

        def tick():
            if not self._alive.is_set():
                try:
                    root.destroy()
                except Exception:
                    pass
                return
            if self._text_queue:
                text = self._text_queue[-1]
                self._text_queue.clear()
                lbl.config(text=text)
                # Reflow if size changed
                root.update_idletasks()
                nw = lbl.winfo_reqwidth()
                nh = lbl.winfo_reqheight()
                nx = max(0, sw - nw - margin)
                ny = max(0, sh - nh - margin)
                root.geometry(f"{nw}x{nh}+{nx}+{ny}")
            root.after(250, tick)  # subtle updates

        tick()
        try:
            root.mainloop()
        except Exception:
            pass


# ----------------------------- Recording -------------------------------------

def record_until_q(output_file: str = "macro.json") -> None:
    """
    Record mouse and keyboard events and store per-event delays ("dt") until ESC is pressed.

    Args:
        output_file: JSON path to write the recorded events.
    """
    from pynput import mouse, keyboard

    out_path = Path(output_file)
    if out_path.exists():
        if not _confirm_overwrite(out_path):
            print("🚫 Recording cancelled (user declined to overwrite).")
            return

    events: List[dict] = []
    stop_evt = Event()
    start_time = time.time()
    last_abs = start_time
    ABORT_KEY = _get_abort_key()

    def now_abs() -> float:
        """Return current absolute time.time() as float seconds."""
        return time.time()

    # Mouse callbacks
    def on_move(x, y):
        nonlocal last_abs
        cur = now_abs()
        dt = cur - last_abs
        last_abs = cur
        events.append({"dt": dt, "type": "move", "x": x, "y": y})

    def on_click(x, y, button, pressed):
        nonlocal last_abs
        cur = now_abs()
        dt = cur - last_abs
        last_abs = cur
        events.append({
            "dt": dt,
            "type": "click",
            "x": x,
            "y": y,
            "button": getattr(button, "name", str(button)).split(".")[-1],
            "pressed": bool(pressed)
        })

    def on_scroll(x, y, dx, dy):
        nonlocal last_abs
        cur = now_abs()
        dt = cur - last_abs
        last_abs = cur
        events.append({"dt": dt, "type": "scroll", "dx": dx, "dy": dy})

    # Keyboard callbacks
    def on_press(key):
        """Stop immediately when ESC is pressed; do not record the abort key."""
        nonlocal last_abs
        try:
            if key == ABORT_KEY:
                print(f"⏹ Stopping recording ({ABORT_KEY_HUMAN} pressed)…")
                stop_evt.set()
                return False
        except Exception:
            pass
        key_str = _key_to_str(key)
        cur = now_abs()
        dt = cur - last_abs
        last_abs = cur
        events.append({"dt": dt, "type": "key", "key": key_str, "pressed": True})

    def on_release(key):
        """Handle key releases, skip the abort key."""
        if stop_evt.is_set():
            return False
        key_str = _key_to_str(key)
        if key_str == ABORT_KEY_STR:
            return
        nonlocal last_abs
        cur = time.time()
        dt = cur - last_abs
        last_abs = cur
        events.append({"dt": dt, "type": "key", "key": key_str, "pressed": False})

    m_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
    k_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    m_listener.start()
    k_listener.start()

    print(f"🎙 Recording… Press {ABORT_KEY_HUMAN} at any time to stop.")
    try:
        while not stop_evt.is_set():
            time.sleep(0.01)
    finally:
        m_listener.stop()
        k_listener.stop()
        m_listener.join()
        k_listener.join()

    out_path.write_text(json.dumps(events, indent=2))
    print(f"✅ Recorded {len(events)} events → {out_path}")


# ------------------------------ Runner ---------------------------------------

def run_macro(input_file: str = "macro.json", speed: float = 1.0,
              dialog: str = "overlay") -> None:
    """
    Run (play back) a recorded macro from JSON, with a subtle bottom-right countdown.

    Args:
        input_file: Path to the JSON file to read events from.
        speed: Playback speed multiplier. 1.0 = real-time; 2.0 = twice as fast.
        dialog: 'overlay' (default) or 'none'.
    """
    from pynput.mouse import Controller as MouseCtl, Button
    from pynput.keyboard import Controller as KeyCtl, Listener as KeyListener, Key

    data = json.loads(Path(input_file).read_text())
    events_with_dt = _normalize_to_dt(data)

    # Pre-compute total scaled runtime ONCE
    total_scaled = sum(dt for dt, _ in events_with_dt) / max(speed, 1e-6)

    mouse_ctl, key_ctl = MouseCtl(), KeyCtl()
    stop_evt = Event()
    injected_keys: set[str] = set()
    ABORT_KEY = Key.esc

    # Bottom-right overlay: separate lightweight thread (no per-event callbacks)
    overlay: Optional[_OverlayStatus] = None
    if dialog == "overlay" and total_scaled > 0:
        try:
            overlay = _OverlayStatus()
        except Exception:
            overlay = None  # continue silently without overlay

    # Start countdown thread (independent of event timing)
    countdown_thread = None
    if overlay:
        def fmt_time(sec: float) -> str:
            sec = max(0, int(round(sec)))
            h, rem = divmod(sec, 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

        def countdown_loop(t_total: float):
            t0 = time.time()
            while not stop_evt.is_set():
                remaining = max(0.0, t_total - (time.time() - t0))
                overlay.update_text(f"⏳ remaining {fmt_time(remaining)}")
                if remaining <= 0.0:
                    break
                time.sleep(0.5)
            if not stop_evt.is_set():
                overlay.update_text("⏳ remaining 00:00")

        countdown_thread = Thread(target=countdown_loop, args=(total_scaled,), daemon=True)
        countdown_thread.start()

    # ESC listener (ignores injected ESC)
    def on_press(key):
        try:
            if key == ABORT_KEY:
                if ABORT_KEY_STR in injected_keys:
                    return
                print(f"\n⏹ Aborting run ({ABORT_KEY_HUMAN} pressed)…")
                stop_evt.set()
                return False
        except Exception:
            pass

    k_listener = KeyListener(on_press=on_press)
    k_listener.start()

    print(f"▶ Running {len(data)} events at {speed}x… (press {ABORT_KEY_HUMAN} to abort)")

    try:
        for dt, ev in events_with_dt:
            if stop_evt.is_set():
                break

            # Wait the scaled per-event delay — minimal overhead, no UI ticks here
            scaled = dt / max(speed, 1e-6)
            _wait_with_abort(scaled, stop_evt)
            if stop_evt.is_set():
                break

            et = ev["type"]
            if et == "move":
                mouse_ctl.position = (ev["x"], ev["y"])
            elif et == "click":
                btn_name = ev.get("button", "left")
                btn = getattr(Button, btn_name, Button.left)
                if ev.get("pressed", True):
                    mouse_ctl.press(btn)
                else:
                    mouse_ctl.release(btn)
            elif et == "scroll":
                mouse_ctl.scroll(ev.get("dx", 0), ev.get("dy", 0))
            elif et == "key":
                key_obj = _str_to_key(ev["key"])
                if ev.get("key") == ABORT_KEY_STR:
                    injected_keys.add(ABORT_KEY_STR)
                try:
                    if ev.get("pressed", True):
                        key_ctl.press(key_obj)
                    else:
                        key_ctl.release(key_obj)
                finally:
                    if ev.get("key") == ABORT_KEY_STR and not ev.get("pressed", True):
                        injected_keys.discard(ABORT_KEY_STR)
    finally:
        if overlay:
            overlay.close()
        if countdown_thread:
            countdown_thread.join(timeout=1.0)
        injected_keys.clear()
        k_listener.stop()
        k_listener.join()

    if stop_evt.is_set():
        print("🛑 Run aborted by user.")
    else:
        print("✅ Run finished.")


# ------------------------ Utility functions ----------------------------------

def _confirm_overwrite(path: Path) -> bool:
    """Prompt the user to confirm overwriting an existing file. Returns True if confirmed."""
    while True:
        try:
            ans = input(f"⚠️ File '{path}' already exists. Overwrite? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", ""):
            return False
        print("Please answer 'y' or 'n'.")


def _key_to_str(key) -> str:
    """Convert pynput key object to a compact string representation."""
    try:
        if hasattr(key, "char") and key.char is not None:
            return key.char
    except Exception:
        pass
    return str(key)  # e.g., 'Key.enter' or 'Key.esc'


def _str_to_key(s: str):
    """Convert stored key string back to a pynput key or character."""
    from pynput.keyboard import Key
    if s.startswith("Key."):
        name = s.split(".", 1)[1]
        return getattr(Key, name, s)
    if len(s) == 1:
        return s
    return s


def _normalize_to_dt(data: List[dict]) -> List[Tuple[float, dict]]:
    """Convert a loaded JSON event list into a list of (dt, event) tuples."""
    result: List[Tuple[float, dict]] = []
    if not data:
        return result
    if "dt" in data[0]:
        for ev in data:
            result.append((max(0.0, float(ev.get("dt", 0.0))), ev))
        return result
    prev_t = 0.0
    for ev in data:
        t = float(ev.get("t", prev_t))
        dt = max(0.0, t - prev_t)
        prev_t = t
        ev2 = dict(ev)
        ev2.pop("t", None)
        ev2["dt"] = dt
        result.append((dt, ev2))
    return result


def _wait_with_abort(seconds: float, stop_evt: Event) -> None:
    """Wait up to 'seconds' in small increments, returning early if stop_evt is set."""
    end = time.time() + max(0.0, seconds)
    while not stop_evt.is_set():
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 0.005))


# ------------------------------ CLI ------------------------------------------

def _build_argparser():
    """Build the command-line parser for recording and running macros."""
    p = argparse.ArgumentParser(description="Macro recorder/runner with per-event delays.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help=f"Record until you press {ABORT_KEY_HUMAN}.")
    r.add_argument("path", nargs="?", default="macro.json", help="Output JSON path.")

    rn = sub.add_parser("run", help="Run a recorded macro from JSON.")
    rn.add_argument("path", nargs="?", default="macro.json", help="Input JSON path.")
    rn.add_argument("-s", "--speed", type=float, default=1.0, help="Run speed multiplier.")
    rn.add_argument(
        "-d", "--dialog",
        choices=("overlay", "none"),
        default="overlay",
        help="Countdown display: overlay (default) or none."
    )

    return p


def main():
    """Entry point for the command-line interface."""
    args = _build_argparser().parse_args()

    if args.cmd == "record":
        _ = ensure_dependencies("none")  # only pynput
        record_until_q(output_file=args.path)

    elif args.cmd == "run":
        dialog_mode = ensure_dependencies(args.dialog)
        run_macro(input_file=args.path, speed=args.speed, dialog=dialog_mode)


if __name__ == "__main__":
    main()

