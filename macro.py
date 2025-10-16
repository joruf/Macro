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
- Moves switch: -m on (default) keeps all mouse move events; -m off compacts to the last move
  before each non-move event (timing preserved).

Manual install quick guide:
    # Core dependency (all platforms)
    python3 -m pip install --upgrade pip
    python3 -m pip install pynput

    # Overlay (Tkinter) — needed for the default bottom-right dialog
    # Debian/Ubuntu:
    sudo apt-get update && sudo apt-get install -y python3-tk
    # Fedora/RHEL:
    sudo dnf install -y python3-tkinter
    # openSUSE:
    sudo zypper install -y python3-tk
    # Arch/Manjaro:
    sudo pacman -S --noconfirm tk
    # Alpine:
    sudo apk add tcl tk python3-tkinter
    # macOS (Python.org builds often include Tk; with Homebrew:)
    brew install tcl-tk   # ensure your Python uses this Tk (see brew caveats)

Usage examples:
    python3 macro.py record
    python3 macro.py record mymacro.json
    python3 macro.py record mymacro.json -m on     # keep all moves (default)
    python3 macro.py record mymacro.json -m off    # compact moves: keep only last move before clicks/keys
    python3 macro.py run
    python3 macro.py run macro.json -s 1.5
    python3 macro.py run macro.json -d none
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
        ("zypper", None, ["zypper", "install", "-y", "python3-tk"]),  # fixed: "-y" is a string
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
    """Tiny always-on-top overlay at the bottom-right that shows a countdown.

    All Tk operations are confined to the main thread. Other threads only push
    text updates onto a queue.
    """
    def __init__(self) -> None:
        """Prepare overlay state; actual Tk setup is done in mainloop()."""
        self._alive = Event()
        self._alive.set()
        self._text_queue: List[str] = []
        from threading import Lock
        self._q_lock = Lock()
        self._root = None
        self._label = None

    def update_text(self, text: str) -> None:
        """Queue a text update for the overlay label (thread-safe)."""
        with self._q_lock:
            self._text_queue.append(text)

    def close(self) -> None:
        """Request closing the overlay from any thread."""
        self._alive.clear()
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def mainloop(self) -> None:
        """Create Tk UI and enter mainloop. Must be called on the main thread."""
        try:
            import tkinter as tk
        except Exception:
            # Tk not available; just spin until someone calls close()
            while self._alive.is_set():
                time.sleep(0.1)
            return

        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        try:
            root.wm_attributes("-topmost", 1)
            root.wm_attributes("-alpha", 0.85)
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
        self._label = lbl
        lbl.pack()

        # Position bottom-right
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = lbl.winfo_reqwidth()
        h = lbl.winfo_reqheight()
        margin = 8
        x = max(0, sw - w - margin)
        y = max(0, sh - h - margin)
        root.geometry(f"{w}x{h}+{x}+{y}")

        def tick():
            if not self._alive.is_set():
                try:
                    root.destroy()
                except Exception:
                    pass
                return
            need_reflow = False
            with self._q_lock:
                if self._text_queue:
                    text = self._text_queue[-1]
                    self._text_queue.clear()
                    lbl.config(text=text)
                    need_reflow = True
            if need_reflow:
                root.update_idletasks()
                nw = lbl.winfo_reqwidth()
                nh = lbl.winfo_reqheight()
                nx = max(0, sw - nw - margin)
                ny = max(0, sh - nh - margin)
                root.geometry(f"{nw}x{nh}+{nx}+{ny}")
            root.after(250, tick)

        root.after(0, tick)
        try:
            root.mainloop()
        except Exception:
            pass


# ----------------------------- Recording -------------------------------------

def record_until_q(
    output_file: str = "macro.json",
    moves: str = "on",
) -> None:
    """
    Record mouse and keyboard events and store per-event delays ("dt") until ESC is pressed.

    Args:
        output_file: JSON path to write the recorded events.
        moves: 'on' to store all move events (default), 'off' to compact moves so that
               only the single last move before each non-move event is kept.
    """
    from pynput import mouse, keyboard

    out_path = Path(output_file)
    if out_path.exists():
        if not _confirm_overwrite(out_path):
            print("🚫 Recording cancelled (user declined to overwrite).")
            return

    events: List[dict] = []
    stop_evt = Event()
    last_abs = time.time()
    ABORT_KEY = _get_abort_key()

    def now_abs() -> float:
        """Return current absolute time.time() as float seconds."""
        return time.time()

    # Mouse callbacks
    def on_move(x, y):
        """Record a mouse move with relative dt."""
        nonlocal last_abs
        cur = now_abs()
        dt = cur - last_abs
        last_abs = cur
        events.append({"dt": dt, "type": "move", "x": x, "y": y})

    def on_click(x, y, button, pressed):
        """Record mouse click press/release with relative dt."""
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
        """Record a mouse scroll with relative dt."""
        nonlocal last_abs
        cur = now_abs()
        dt = cur - last_abs
        last_abs = cur
        events.append({"dt": dt, "type": "scroll", "dx": dx, "dy": dy})

    # Keyboard callbacks
    def on_press(key):
        """
        Record key press with relative dt. Abort immediately when ESC is pressed;
        do not record the abort key.
        """
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
        """Record key release with relative dt; skip the abort key."""
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

    # Optionally compact excessive mouse moves while preserving timing
    final_events = events
    if moves == "off":
        # Keep only the single last move before each non-move event
        final_events = compact_moves(events, keep_moves=1)

    out_path.write_text(json.dumps(final_events, indent=2))
    print(
        f"✅ Recorded {len(events)} raw events; wrote {len(final_events)} events "
        f"(moves='{moves}') → {out_path}"
    )


# ------------------------------ Runner ---------------------------------------

def run_macro(
    input_file: str = "macro.json",
    speed: float = 1.0,
    dialog: str = "overlay",
    hard_exit: bool = True,
) -> None:
    """
    Run (play back) a recorded macro from JSON, with an optional bottom-right countdown.
    On abort or finish, perform a robust teardown and (optionally) force a hard exit to
    guarantee the shell is returned even if third-party threads linger.

    Args:
        input_file: Path to the JSON file.
        speed: Playback speed multiplier.
        dialog: 'overlay' (default) or 'none'.
        hard_exit: If True, terminate the process via os._exit(0) after cleanup.
    """
    from pynput.mouse import Controller as MouseCtl, Button
    from pynput.keyboard import Controller as KeyCtl, Listener as KeyListener, Key
    import signal

    mouse_ctl, key_ctl = MouseCtl(), KeyCtl()
    stop_evt = Event()
    injected_keys: set[str] = set()
    ABORT_KEY = Key.esc
    overlay: Optional[_OverlayStatus] = None

    # --- Graceful signal handling -------------------------------------------
    def _graceful_stop(signum, frame):
        """Signal handler that requests a clean shutdown without tracebacks."""
        stop_evt.set()
        try:
            if overlay:
                overlay.close()
        except Exception:
            pass

    try:
        signal.signal(signal.SIGINT, _graceful_stop)
        signal.signal(signal.SIGTERM, _graceful_stop)
    except Exception:
        pass

    # Load and normalize data
    data = json.loads(Path(input_file).read_text())
    events_with_dt = _normalize_to_dt(data)
    total_scaled = sum(dt for dt, _ in events_with_dt) / max(speed, 1e-6)

    def on_press(key):
        """Abort the run if the user presses ESC (ignoring injected ESC events)."""
        try:
            if key == ABORT_KEY:
                if ABORT_KEY_STR in injected_keys:
                    return
                print(f"\n⏹ Aborting run ({ABORT_KEY_HUMAN} pressed)…")
                stop_evt.set()
                if overlay:
                    overlay.close()
                return False
        except Exception:
            pass

    # Explicitly manage the keyboard listener
    from pynput.keyboard import Listener as _KListener
    k_listener = _KListener(on_press=on_press)
    try:
        k_listener.daemon = True  # type: ignore[attr-defined]
    except Exception:
        pass
    k_listener.start()

    def _final_teardown() -> None:
        """Best-effort stop/join of resources; optionally hard-exit the process."""
        # Stop listener
        try:
            k_listener.stop()
        except Exception:
            pass
        try:
            k_listener.join(timeout=1.0)
        except Exception:
            pass
        # Close overlay
        try:
            if overlay:
                overlay.close()
        except Exception:
            pass
        # Help GC close low-level resources
        try:
            del mouse_ctl, key_ctl
        except Exception:
            pass
        # As a last resort, kill any known pynput threads and exit hard
        if hard_exit:
            try:
                import threading
                for t in threading.enumerate():
                    if t is threading.current_thread():
                        continue
                    name = getattr(t, "name", "").lower()
                    mod = getattr(t, "__module__", "")
                    # Mark suspicious threads as daemons to avoid blocking
                    if "pynput" in name or "pynput" in mod or "xlib" in name:
                        try:
                            t.daemon = True  # type: ignore[attr-defined]
                        except Exception:
                            pass
            except Exception:
                pass
            # Guaranteed return to shell
            os._exit(0)

    try:
        def playback_loop():
            """Execute the actual macro playback."""
            nonlocal overlay
            print(f"▶ Running {len(data)} events at {speed}x… (press {ABORT_KEY_HUMAN} to abort)")
            try:
                for dt, ev in events_with_dt:
                    if stop_evt.is_set():
                        break
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
                stop_evt.set()
                if overlay:
                    overlay.close()

        if dialog == "overlay" and total_scaled > 0:
            overlay = _OverlayStatus()

            def fmt_time(sec: float) -> str:
                """Format seconds as MM:SS or HH:MM:SS if hours are present."""
                sec = max(0, int(round(sec)))
                h, rem = divmod(sec, 3600)
                m, s = divmod(rem, 60)
                return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

            def countdown_loop(t_total: float):
                """Continuously update the overlay with remaining time until done or aborted."""
                t0 = time.time()
                while not stop_evt.is_set():
                    remaining = max(0.0, t_total - (time.time() - t0))
                    overlay.update_text(f"⏳ remaining {fmt_time(remaining)}")
                    if remaining <= 0.0:
                        break
                    time.sleep(0.5)
                if not stop_evt.is_set():
                    overlay.update_text("⏳ remaining 00:00")

            worker = Thread(target=playback_loop, daemon=True)
            countdown_thread = Thread(target=countdown_loop, args=(total_scaled,), daemon=True)
            worker.start()
            countdown_thread.start()

            # Tk mainloop runs on main thread; returns after overlay.close()
            overlay.mainloop()

            # Best-effort joins (non-blocking due to daemon=True)
            try:
                worker.join(timeout=0.5)
                countdown_thread.join(timeout=0.5)
            except Exception:
                pass

        else:
            # No overlay: run playback synchronously on this thread
            try:
                playback_loop()
            except KeyboardInterrupt:
                stop_evt.set()
                if overlay:
                    overlay.close()

        print("✅ Run finished.")

    finally:
        _final_teardown()


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
    """
    Convert a loaded JSON event list into a list of (dt, event) tuples.

    Supports legacy formats that had absolute 't' times by converting them
    into relative 'dt' between events.
    """
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


def compact_moves(events: List[dict], keep_moves: int = 1) -> List[dict]:
    """
    Compact runs of consecutive 'move' events so that only the last N moves right
    before any non-move event (click/key/scroll) are kept. The total relative time
    before that non-move event is preserved by rolling the dropped dt into the first
    kept move (or into the subsequent non-move event if no move is kept).

    With keep_moves=1 this results in exactly one move (the last) before each non-move.

    Args:
        events: Original event list (must have 'dt' per event).
        keep_moves: Number of trailing move events to retain before each non-move (default 1).

    Returns:
        A new list of events with fewer 'move' entries but identical cumulative timing
        for all non-move events (click/key/scroll) and thus preserved key press durations.
    """
    if keep_moves is None or keep_moves < 0:
        keep_moves = 1

    out: List[dict] = []
    move_buf: List[dict] = []

    def flush_moves_before(next_event: Optional[dict]) -> None:
        """Flush the buffered moves, keeping only the last N and preserving total dt."""
        nonlocal out, move_buf
        if not move_buf:
            return
        total_dt = sum(float(ev.get("dt", 0.0)) for ev in move_buf)
        kept = move_buf[-keep_moves:] if keep_moves > 0 else []
        dropped = move_buf[:-keep_moves] if keep_moves > 0 else move_buf
        dropped_dt = sum(float(ev.get("dt", 0.0)) for ev in dropped)

        if kept:
            kept_first = dict(kept[0])
            kept_first["dt"] = float(kept_first.get("dt", 0.0)) + dropped_dt
            out.extend([kept_first, *kept[1:]])
        else:
            if next_event is not None:
                next_event["dt"] = float(next_event.get("dt", 0.0)) + total_dt
            else:
                # End-of-stream idle time does not affect playback; drop it.
                pass
        move_buf.clear()

    for ev in events:
        et = ev.get("type")
        if et == "move":
            move_buf.append(dict(ev))
            continue
        ev_copy = dict(ev)
        flush_moves_before(ev_copy)
        out.append(ev_copy)

    flush_moves_before(next_event=None)
    return out


# ------------------------------ CLI ------------------------------------------

def _build_argparser():
    """Build the command-line parser for recording and running macros."""
    p = argparse.ArgumentParser(description="Macro recorder/runner with per-event delays.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help=f"Record until you press {ABORT_KEY_HUMAN}.")
    r.add_argument("path", nargs="?", default="macro.json", help="Output JSON path.")
    r.add_argument(
        "-m", "--moves",
        choices=("on", "off"),
        default="on",
        help="Mouse move recording: 'on' keeps all moves (default), 'off' compacts to the last move before each non-move."
    )

    rn = sub.add_parser("run", help="Run a recorded macro from JSON.")
    rn.add_argument("path", nargs="?", default="macro.json", help="Input JSON path.")
    rn.add_argument("-s", "--speed", type=float, default=1.0, help="Run speed multiplier.")
    rn.add_argument(
        "-d", "--dialog",
        choices=("overlay", "none"),
        default="overlay",
        help="Countdown display: overlay (default) or none."
    )
    rn.add_argument(
        "--no-hard-exit",
        dest="hard_exit",
        action="store_false",
        help="Do best-effort cleanup and return via sys.exit instead of os._exit.",
    )

    return p


def main():
    """Entry point for the command-line interface."""
    args = _build_argparser().parse_args()

    if args.cmd == "record":
        _ = ensure_dependencies("none")  # only pynput
        record_until_q(
            output_file=args.path,
            moves=args.moves,
        )

    elif args.cmd == "run":
        dialog_mode = ensure_dependencies(args.dialog)
        run_macro(input_file=args.path, speed=args.speed, dialog=dialog_mode, hard_exit=args.hard_exit)


if __name__ == "__main__":
    main()

