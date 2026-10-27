"""Emoji Float: cute emoji drift up from the left side of the taskbar while you type.

The faster you type, the more they float. A little critter lives on the taskbar, leaves and
snow pile up, and the theme can follow the seasons (and holidays) on its own.
Only the timing of key presses is used. Which keys you press is never stored.

Run:  pythonw emoji_float.pyw     (theme, pause and quit live on the tray icon)
"""
import ctypes
import ctypes.wintypes as wt
import math
import os
import random
import sys
import time
from collections import deque
from datetime import date

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QSettings
from PyQt6.QtGui import (QPainter, QPixmap, QFont, QColor, QRadialGradient, QLinearGradient, QPainterPath,
                         QIcon, QAction, QActionGroup)
from PyQt6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu

WIDTH, HEIGHT = 340, 480        # overlay size; its bottom edge sits on the bottom of the screen
MAX_PARTICLES = 160
MAX_PILE = 40
GRAVITY = 60                    # px/s² for things that flutter back down

# Each theme: tray icon, glow colour, critter (and which way its emoji faces), emoji that can
# settle into a pile, whether ❄️ builds a snowdrift, and moods of (min keys/sec, name, emoji pool)
THEMES = {
    "Spring": {
        "icon": "🌸", "glow": (255, 140, 200), "critter": "🐇", "faces": "left",
        "settle": {"🌸"}, "drift": False,
        "moods": [
            (0.0, "sleepy clouds", ["☁️", "☁️", "🫧"]),
            (2.0, "blooming",      ["🌸", "🌷", "🌼", "🌺", "🌸"]),
            (4.5, "in love",       ["💕", "💗", "🩷", "💓", "❤️"]),
            (7.0, "pink party!",   ["💖", "💞", "🩷", "✨", "🦋", "🌸", "💗"]),
        ],
    },
    "Fall": {
        "icon": "🍁", "glow": (255, 150, 60), "critter": "🐿️", "faces": "left",
        "settle": {"🍁", "🍂"}, "drift": False,
        "moods": [
            (0.0, "crisp breeze",         ["🍂", "🍃", "☁️"]),
            (2.0, "leaf pile",            ["🍁", "🍂", "🍁", "🌰", "🍄"]),
            (4.5, "cozy café",            ["☕", "🥧", "🍎", "🧣", "🍯", "🥞"]),
            (7.0, "pumpkin spice party!", ["🎃", "🐿️", "🍁", "☕", "🥞", "🧡", "✨", "🍂"]),
        ],
    },
    "Winter": {
        "icon": "❄️", "glow": (150, 200, 255), "critter": "🐧", "faces": "left",
        "settle": set(), "drift": True,
        "moods": [
            (0.0, "quiet snowfall",   ["❄️", "🌨️", "☁️"]),
            (2.0, "snow day",         ["❄️", "⛄", "🧊", "❄️", "🌨️"]),
            (4.5, "sweater weather",  ["🧣", "🧤", "🧶", "☕", "🍪", "🥛"]),
            (7.0, "christmas magic!", ["🎄", "🎁", "⭐", "🦌", "🎅", "🔔", "❄️", "✨"]),
        ],
    },
    "Halloween": {
        "icon": "🎃", "glow": (170, 100, 255), "critter": "🐈‍⬛", "faces": "left",
        "settle": {"🍬", "🍭"}, "drift": False,
        "moods": [
            (0.0, "spooky mist",    ["👻", "☁️", "🕸️"]),
            (2.0, "trick or treat", ["🎃", "🍬", "🍭", "🍫", "🍬"]),
            (4.5, "haunted",        ["👻", "🦇", "🕷️", "🌕", "🔮"]),
            (7.0, "monster mash!",  ["🎃", "👻", "🦇", "💀", "🧙", "🍬", "✨", "🌙"]),
        ],
    },
    "Valentine": {
        "icon": "💌", "glow": (255, 90, 140), "critter": "🧸", "faces": "left",
        "settle": {"🌹"}, "drift": False,
        "moods": [
            (0.0, "daydreaming",      ["☁️", "💭", "🫧"]),
            (2.0, "love notes",       ["💌", "🌹", "💐", "🍫", "🌹"]),
            (4.5, "smitten",          ["💘", "💝", "💗", "💋", "🥰"]),
            (7.0, "head over heels!", ["💖", "💞", "💘", "✨", "🌹", "🧸", "💕"]),
        ],
    },
}
AUTO = "Auto"
BIG_AND_SLOW = {"☁️", "🫧", "🌨️", "💭"}
TUMBLY = {"🍁", "🍂", "🍃", "❄️", "🌸", "🍬", "🍭", "🌹"}   # these spin as they drift


def seasonal_theme(today):
    m, d = today.month, today.day
    if m == 10 and d >= 24:
        return "Halloween"
    if m == 2 and 7 <= d <= 14:
        return "Valentine"
    if m in (12, 1):
        return "Winter"
    if m in (9, 10, 11):
        return "Fall"
    return "Spring"


# --- Win32 low-level keyboard hook -------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
HWND_TOPMOST = wt.HWND(-1)
SWP_QUIET = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE


class KeyCounter:
    """Counts key presses per second. Key codes are only used to ignore auto-repeat."""

    def __init__(self):
        self.presses = deque()
        self.total = 0
        self._down = set()
        self._proc = HOOKPROC(self._callback)  # keep a reference so it isn't garbage collected
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)

    def _callback(self, code, wparam, lparam):
        if code == 0:
            vk = ctypes.cast(lparam, ctypes.POINTER(wt.DWORD))[0]
            if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                if vk not in self._down:
                    self._down.add(vk)
                    self.presses.append(time.monotonic())
                    self.total += 1
            elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                self._down.discard(vk)
        return user32.CallNextHookEx(self._hook, code, wparam, lparam)

    def rate(self, window=1.5):
        now = time.monotonic()
        while self.presses and now - self.presses[0] > window:
            self.presses.popleft()
        return len(self.presses) / window

    def close(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


class Stats:
    """Today's keys, top speed and party count, kept across restarts until midnight."""

    def __init__(self, settings):
        self.settings = settings
        self.day = date.today().isoformat()
        same_day = settings.value("stats/day") == self.day
        self.keys = settings.value("stats/keys", 0, type=int) if same_day else 0
        self.top_wpm = settings.value("stats/top_wpm", 0, type=int) if same_day else 0
        self.parties = settings.value("stats/parties", 0, type=int) if same_day else 0

    def roll_over(self):
        today = date.today().isoformat()
        if today != self.day:
            self.day, self.keys, self.top_wpm, self.parties = today, 0, 0, 0

    def save(self):
        for key, value in (("day", self.day), ("keys", self.keys), ("top_wpm", self.top_wpm),
                           ("parties", self.parties)):
            self.settings.setValue(f"stats/{key}", value)


# --- Emoji rendering -----------------------------------------------------------------------------
_sprites = {}


def sprite(emoji):
    """Render an emoji once to a crisp transparent pixmap and reuse it."""
    if emoji not in _sprites:
        pix = QPixmap(128, 128)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        font = QFont("Segoe UI Emoji")
        font.setPixelSize(96)
        p.setFont(font)
        p.drawText(QRectF(0, 0, 128, 128), Qt.AlignmentFlag.AlignCenter, emoji)
        p.end()
        _sprites[emoji] = pix
    return _sprites[emoji]


def draw_emoji(p, emoji, x, y, size, tilt=0.0, alpha=1.0, sx=1.0, sy=1.0):
    p.save()
    p.setOpacity(alpha)
    p.translate(QPointF(x, y))
    p.rotate(tilt)
    p.scale(sx, sy)
    pix = sprite(emoji)
    p.drawPixmap(QRectF(-size / 2, -size / 2, size, size), pix, QRectF(pix.rect()))
    p.restore()


def mood_for(moods, speed):
    level = 0
    for i, (threshold, _, _) in enumerate(moods):
        if speed >= threshold:
            level = i
    return level


class Particle:
    def __init__(self, emoji, x, y, speed, size=None, settles=False):
        self.emoji = emoji
        self.x0, self.y = x, y
        self.vy = -(30 + min(speed, 12) * 8 + random.uniform(0, 25))
        self.vx = random.uniform(-1, 1) * min(speed, 12) * 4   # a little burst when typing fast
        self.size = size or 18 + random.uniform(0, 10) + min(speed, 10) * 1.3
        if emoji in BIG_AND_SLOW:
            self.size *= 1.4
            self.vy *= 0.6
        self.spin = random.choice((-1, 1)) * random.uniform(40, 120) if emoji in TUMBLY else 0.0
        self.sway_amp = random.uniform(6, 18)
        self.sway_freq = random.uniform(0.5, 1.3)
        self.phase = random.uniform(0, math.tau)
        self.age = 0.0
        self.life = min(random.uniform(3.0, 5.0), (y - 20) / -self.vy)
        # settlers pop up, then flutter back down onto the taskbar instead of fading away
        self.settles = settles
        self.landed = False
        if settles:
            self.vy = -random.uniform(70, 130)
            self.ground = HEIGHT - random.uniform(11, 17)
            self.life = 99.0

    def x(self):
        return self.x0 + math.sin(self.phase + self.age * self.sway_freq * math.tau) * self.sway_amp

    def tilt(self):
        return math.sin(self.phase + self.age * 2.0) * 12 + self.spin * self.age

    def step(self, dt):
        self.age += dt
        if self.settles:
            self.vy = min(self.vy + GRAVITY * dt, 45)   # gentle terminal velocity, like a leaf
            if self.vy > 0 and self.y >= self.ground:
                self.landed = True
                return False
        self.y += self.vy * dt
        self.x0 += self.vx * dt
        self.vx *= 0.97
        return self.age < self.life

    def draw(self, p):
        t = self.age / self.life
        alpha = min(1.0, self.age / 0.3) * (1.0 if t < 0.6 else max(0.0, (1 - t) / 0.4))
        pop = min(1.0, 0.4 + self.age / 0.25 * 0.6)
        draw_emoji(p, self.emoji, self.x(), self.y, self.size * pop, self.tilt(), alpha)


class PilePiece:
    """A leaf (or petal, or candy) resting on the taskbar until the wind takes it."""

    def __init__(self, particle):
        self.emoji = particle.emoji
        self.x, self.y = particle.x(), particle.ground
        self.tilt = particle.tilt()
        self.size = particle.size * 0.8
        self.age = 0.0
        self.life = random.uniform(25, 45)
        self.wind = random.choice((-1, 1)) * random.uniform(40, 80)

    def blow_away(self):
        self.age = max(self.age, self.life - 3)

    def step(self, dt):
        self.age += dt
        gust = self.age - (self.life - 3)
        if gust > 0:
            self.x += self.wind * gust * dt
            self.y -= 8 * dt
            self.tilt += self.wind * 3 * dt
        return self.age < self.life

    def draw(self, p):
        alpha = min(1.0, (self.life - self.age) / 3)
        draw_emoji(p, self.emoji, self.x, self.y, self.size, self.tilt, alpha)


class SnowDrift:
    """Snowflakes that land build a soft drift along the taskbar, which slowly melts."""
    STEP = 10

    def __init__(self):
        self.heights = [0.0] * (WIDTH // self.STEP + 1)

    def add(self, x):
        i = int(x // self.STEP)
        for offset, amount in ((0, 3.0), (-1, 1.8), (1, 1.8), (-2, 0.7), (2, 0.7)):
            if 0 <= i + offset < len(self.heights):
                self.heights[i + offset] = min(16, self.heights[i + offset] + amount)

    def step(self, dt):
        self.heights = [max(0.0, h - 0.05 * dt) for h in self.heights]

    def clear(self):
        self.heights = [0.0] * len(self.heights)

    def draw(self, p):
        if max(self.heights) < 0.5:
            return
        n = len(self.heights)
        pts = [QPointF(i * self.STEP, HEIGHT - h * min(1.0, i / 4, (n - 1 - i) / 4))
               for i, h in enumerate(self.heights)]
        path = QPainterPath(QPointF(0, HEIGHT))
        path.lineTo(pts[0])
        for a, b in zip(pts, pts[1:]):
            path.quadTo(a, (a + b) / 2)
        path.lineTo(pts[-1])
        path.lineTo(QPointF(WIDTH, HEIGHT))
        path.closeSubpath()
        fill = QLinearGradient(0, HEIGHT - 16, 0, HEIGHT)
        fill.setColorAt(0, QColor(255, 255, 255, 240))
        fill.setColorAt(1, QColor(215, 232, 255, 230))
        p.fillPath(path, fill)


class Critter:
    """A little buddy on the taskbar: naps when idle, sits while you type, zooms when you're fast."""
    SIZE = 38

    def __init__(self):
        self.x = 60.0
        self.direction = 1
        self.state = "sit"
        self.idle = 0.0
        self.t = 0.0
        self.zzz = 0.0

    def step(self, dt, speed, puff):
        self.t += dt
        self.idle = self.idle + dt if speed < 0.3 else 0.0
        state = "sleep" if self.idle > 4 else "run" if speed >= 4.5 else "sit"
        if self.state == "sleep" and state != "sleep":
            puff("❗", self.x, self.ground - self.SIZE * 0.7, 16)
        self.state, self.speed = state, speed

        if state == "run":
            self.x += self.direction * (40 + speed * 12) * dt
            if not 24 < self.x < WIDTH - 24:
                self.x = min(max(self.x, 24), WIDTH - 24)
                self.direction *= -1
        elif state == "sit" and random.random() < dt * 0.15:
            self.direction *= -1   # look around
        elif state == "sleep":
            self.zzz -= dt
            if self.zzz <= 0:
                self.zzz = 2.2
                puff("💤", self.x + 10 * self.direction, self.ground - self.SIZE * 0.6, 14)

    def draw(self, p, theme):
        y, tilt, sx, sy = self.ground, 0.0, 1.0, 1.0
        if self.state == "sleep":
            sy = 0.86 + 0.03 * math.sin(self.t * 1.5)
            y += self.SIZE * 0.07
        elif self.state == "sit":
            sx = sy = 1 + 0.025 * math.sin(self.t * 2.5)
        else:
            y -= abs(math.sin(self.t * 12)) * (9 if self.speed >= 7 else 4)
            tilt = math.sin(self.t * 14) * 8
        facing_right = theme["faces"] == "right"
        if (self.direction > 0) != facing_right:
            sx = -sx
        draw_emoji(p, theme["critter"], self.x, y, self.SIZE, tilt, 1.0, sx, sy)


# --- Overlay window ------------------------------------------------------------------------------
class Overlay(QWidget):
    def __init__(self, keys, stats):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.keys = keys
        self.stats = stats
        self.counted = 0
        self.theme = THEMES["Fall"]
        self.particles = []
        self.pile = []
        self.drift = SnowDrift()
        self.critter = Critter()
        self.speed = 0.0
        self.level = 0
        self.spawn_debt = 0.0
        self.paused = False

        screen = QApplication.primaryScreen()
        full, avail = screen.geometry(), screen.availableGeometry()
        self.taskbar_h = (full.bottom() - avail.bottom()) or 48
        self.critter.ground = HEIGHT - self.taskbar_h / 2
        self.setGeometry(full.left() + 4, full.bottom() - HEIGHT + 1, WIDTH, HEIGHT)

        self.last = time.monotonic()
        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(16)
        # the taskbar likes to jump on top of us when clicked, so gently reclaim the top spot
        QTimer(self, timeout=self.stay_on_top).start(1000)

    def stay_on_top(self):
        user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_QUIET)

    def set_theme(self, theme):
        if theme is not self.theme:
            self.theme = theme
            self.pile.clear()
            self.drift.clear()

    def mood(self):
        return self.theme["moods"][self.level][1]

    def wpm(self):
        return round(self.speed * 12)   # 5 characters per word

    def tick(self):
        now = time.monotonic()
        dt, self.last = min(now - self.last, 0.1), now

        target = 0.0 if self.paused else self.keys.rate()
        self.speed += (target - self.speed) * min(1.0, dt * 3)

        moods = self.theme["moods"]
        level = mood_for(moods, self.speed)
        if level == len(moods) - 1 and self.level < level:
            self.stats.parties += 1
        self.level = level
        self.stats.roll_over()
        self.stats.keys += self.keys.total - self.counted
        self.counted = self.keys.total
        self.stats.top_wpm = max(self.stats.top_wpm, self.wpm())

        if not self.paused:
            rate = 0.25 if self.speed < 0.3 else 1 + self.speed * 2.2   # idle: a lone drifter now and then
            self.spawn_debt += rate * dt
            while self.spawn_debt >= 1 and len(self.particles) < MAX_PARTICLES:
                self.spawn_debt -= 1
                self.spawn()
            self.spawn_debt = min(self.spawn_debt, 1)

        alive = []
        for pt in self.particles:
            if pt.step(dt):
                alive.append(pt)
            elif pt.landed:
                self.land(pt)
        self.particles = alive
        self.pile = [piece for piece in self.pile if piece.step(dt)]
        self.drift.step(dt)
        self.critter.step(dt, self.speed, self.puff)
        self.update()

    def spawn(self):
        moods = self.theme["moods"]
        # blend in a few of the previous mood's emoji so moods melt into each other
        pool = moods[self.level - 1][2] if self.level and random.random() < 0.2 else moods[self.level][2]
        emoji = random.choice(pool)
        can_settle = emoji in self.theme["settle"] or (self.theme["drift"] and emoji == "❄️")
        x = random.uniform(24, WIDTH - 24)
        y = HEIGHT - self.taskbar_h / 2 + random.uniform(-8, 8)
        self.particles.append(Particle(emoji, x, y, self.speed, settles=can_settle and random.random() < 0.35))

    def puff(self, emoji, x, y, size):
        self.particles.append(Particle(emoji, x, y, 0, size=size))

    def land(self, particle):
        if self.theme["drift"]:
            self.drift.add(particle.x())
            return
        self.pile.append(PilePiece(particle))
        resting = [piece for piece in self.pile if piece.age < piece.life - 3]
        if len(resting) > MAX_PILE:
            resting[0].blow_away()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.speed > 4:  # soft themed glow rising from the taskbar when you're on fire
            r, g, b = self.theme["glow"]
            glow = QRadialGradient(QPointF(WIDTH / 2, HEIGHT), WIDTH * 0.6)
            glow.setColorAt(0, QColor(r, g, b, int(min(90, (self.speed - 4) * 20))))
            glow.setColorAt(1, QColor(r, g, b, 0))
            p.fillRect(self.rect(), glow)
        self.drift.draw(p)
        for piece in self.pile:
            piece.draw(p)
        self.critter.draw(p, self.theme)
        for pt in self.particles:
            pt.draw(p)
        p.end()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    settings = QSettings("FunProjects", "EmojiFloat")
    keys = KeyCounter()
    stats = Stats(settings)
    overlay = Overlay(keys, stats)
    overlay.show()
    overlay.stay_on_top()

    tray = QSystemTrayIcon(app)
    menu = QMenu()
    choice = settings.value("theme", AUTO)
    if choice != AUTO and choice not in THEMES:
        choice = AUTO

    def current_theme_name():
        return seasonal_theme(date.today()) if choice == AUTO else choice

    def apply_theme():
        name = current_theme_name()
        overlay.set_theme(THEMES[name])
        tray.setIcon(QIcon(sprite(THEMES[name]["icon"])))

    def choose(name):
        nonlocal choice
        choice = name
        settings.setValue("theme", name)
        apply_theme()

    theme_menu = menu.addMenu("Theme")
    theme_group = QActionGroup(theme_menu)
    for name, label in [(AUTO, "✨  Auto (follows the seasons)")] + [(n, f"{t['icon']}  {n}") for n, t in THEMES.items()]:
        action = QAction(label, theme_menu, checkable=True, checked=name == choice)
        action.triggered.connect(lambda _, n=name: choose(n))
        theme_group.addAction(action)
        theme_menu.addAction(action)
        if name == AUTO:
            theme_menu.addSeparator()
    apply_theme()
    QTimer(app, timeout=lambda: choice == AUTO and apply_theme()).start(10 * 60 * 1000)

    # Launch at login via the per-user Run key (no admin needed). Re-saved on every start so the
    # entry follows the exe if it gets moved.
    run_key = QSettings(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run",
                        QSettings.Format.NativeFormat)

    def launch_command():
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'

    def set_startup(on):
        if on:
            run_key.setValue("EmojiFloat", launch_command())
        else:
            run_key.remove("EmojiFloat")
        run_key.sync()

    startup = QAction("Start with Windows", menu, checkable=True, checked=run_key.contains("EmojiFloat"))
    if startup.isChecked():
        set_startup(True)
    startup.toggled.connect(set_startup)

    pause = QAction("Pause", menu, checkable=True)
    pause.toggled.connect(lambda on: setattr(overlay, "paused", on))
    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(startup)
    menu.addAction(pause)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    def refresh_tooltip():
        name = current_theme_name()
        auto = " (auto)" if choice == AUTO else ""
        tray.setToolTip(f"{THEMES[name]['icon']} {name}{auto} · {overlay.mood()}\n"
                        f"Today: {stats.keys:,} keys · top {stats.top_wpm} wpm · {stats.parties} 🎉")
    refresh_tooltip()
    QTimer(app, timeout=refresh_tooltip).start(500)
    QTimer(app, timeout=stats.save).start(30 * 1000)
    tray.show()

    app.aboutToQuit.connect(stats.save)
    app.aboutToQuit.connect(keys.close)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
