# 🖱️ Keyboard & Mouse Macro Recorder / Runner for X11 & Desktop Systems

A simple yet powerful **macro recorder and player** for Linux (X11) and other desktop systems using [pynput](https://pypi.org/project/pynput/).
It records all mouse and keyboard activity with precise per-event timing, and replays them in real time (or at any adjustable speed).

> ⚙️ The script runs entirely locally – no external servers, no telemetry, no dependencies beyond Python.

---

## ✨ Features

- 🎙 Record all keyboard and mouse actions until you press ESC
→ You can control how mouse movements are stored using -m on|off:
  • -m on (default) – record all mouse movements with full precision
  • -m off – compact movements, keeping only the last move before each click or key event 
- ▶️ **Run (play back)** the recorded macro in real-time or at custom speed (`-s`)
- 💾 **JSON-based format** – easy to inspect and edit manually
- ⏳ **Live countdown overlay** (bottom-right corner, enabled by default)  
  Toggle it using `-d overlay` (default) or disable it with `-d none`
- ⏳ **Live countdown overlay** (bottom-right corner) shows remaining runtime
- 🚫 **Abort anytime** by pressing **ESC** during playback
- 🔁 **Backward-compatible** with legacy macro files using `"t"` instead of `"dt"`
- 🧩 **Auto-installs dependencies** if missing (best effort)
- 🛡 **Cross-platform:** works on most Linux desktop environments, macOS, and Windows (with Python + Tk)

---

**Usage**

# Record
```bash
python3 macro.py record test.json
```

# Record compact moves (keep only last move before clicks/keys)
```bash
python3 macro.py record test.json -m off
```

# Playback
```bash
python3 macro.py run test.json
```

# Faster Playback
```bash
python3 macro.py run test.json -s 1.5
```

# Playback Without Overlay
```bash
python3 macro.py run test.json -d none
```

---

## 📦 Dependencies

| Component | Purpose | Install via |
|------------|----------|-------------|
| `pynput` | low-level mouse/keyboard input capture and replay | `pip install pynput` |
| `tkinter` | (default) overlay countdown dialog | system package manager (see below) |

---

### 🧰 Manual Installation (all platforms)

#### 1. Core dependency
```bash
python3 -m pip install --upgrade pip
python3 -m pip install pynput
```

### 📦 Installation Requirements for the Timer Overlay

The overlay uses **Tkinter**. Install it once, depending on your platform:

#### Linux

**Debian/Ubuntu**
```bash
sudo apt-get update && sudo apt-get install -y python3-tk
```

**Fedora/RHEL**
```bash
sudo dnf install -y python3-tkinter
```

**openSUSE**
```bash
sudo zypper install -y python3-tk
```

**Arch/Manjaro**
```bash
sudo pacman -S --noconfirm tk
```



