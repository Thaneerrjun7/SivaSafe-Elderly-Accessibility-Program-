"""
The SivaSafe alarm — a window that rings until it is answered.

A toast is easy to miss: it slides in, waits a few seconds and files itself
away. For the reminders that actually matter, a reminder can be switched to
alarm mode instead, and it arrives here — a window that puts itself on top of
whatever is on screen, rings, and stays until the person either snoozes it or
dismisses it.

This runs as its own process, launched by reminder_fire.py. That is deliberate:
the alarm has to work whether or not SivaSafe itself is open, and a crash in
the main app must not be able to swallow someone's medication reminder.

Run it by hand to see it:
    python actions/alarm_window.py --message "Take your evening tablets"
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QElapsedTimer
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen,
)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

# ── SivaSafe design tokens (mirrors ui.py, class C) ──────────────────────
BG = "#EBE7DF"
SURFACE = "#F2F0EB"
ON_SURFACE = "#2D2926"
ON_SURFACE_VAR = "#5D4037"
PRIMARY = "#376757"
PRIMARY_CTR = "#7DAF9C"
SECONDARY = "#8B5E3C"
SECONDARY_BTN_BG = "#F0E4CE"
TERTIARY = "#D4AF37"
WHITE = "#FFFFFF"

SNOOZE_MINUTES = 5

# How long it rings before falling silent. Someone who is out of the house
# should not come home to an alarm that has been sounding for six hours — but
# the window stays up, so they still find out they missed it.
RING_SECONDS = 300

_FRAME_MS = 33          # ~30fps; the second hand sweeps rather than ticks


def _text_scale() -> float:
    """
    Honour the text size chosen in Settings.

    Read straight from the preferences file rather than through
    core.text_scale — that module installs an application-wide event filter
    and expects the full SivaSafe app around it, which is far more than a
    single alarm window in its own process needs.
    """
    steps = (0.85, 1.00, 1.18, 1.36)
    try:
        data = json.loads(
            (BASE_DIR / "config" / "preferences.json").read_text(encoding="utf-8"))
        return steps[int(data.get("text_scale_level", 1))]
    except Exception:
        return 1.0


class ClockFace(QWidget):
    """
    An analog clock showing the real time, ringed by a pulse while the alarm
    sounds. Movement is what catches the eye from across a room, so the second
    hand sweeps continuously instead of stepping once a second.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ringing = True
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(_FRAME_MS)

    def set_ringing(self, ringing: bool):
        """Stop the pulse when the alarm falls silent; the clock keeps time."""
        self._ringing = ringing
        # No animation left to drive once it is silent — 30fps of repainting a
        # static face is not worth it while this window sits open for hours.
        self._timer.setInterval(_FRAME_MS if ringing else 1000)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        cx, cy = self.width() / 2, self.height() / 2
        r = side / 2 - side * 0.10        # room outside the face for the pulse

        now = datetime.now()
        beat = (self._clock.elapsed() % 2000) / 2000.0   # one pulse per 2s

        # ── Pulse: two rings expanding out of the face and fading ────────
        if self._ringing:
            for offset in (0.0, 0.5):
                phase = (beat + offset) % 1.0
                grow = r + phase * (side * 0.11)
                alpha = int(150 * (1.0 - phase) ** 1.6)
                if alpha <= 2:
                    continue
                pen = QPen(QColor(212, 175, 55, alpha))     # TERTIARY
                pen.setWidthF(max(1.5, side * 0.014))
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), grow, grow)

        # ── Face ─────────────────────────────────────────────────────────
        p.setPen(QPen(QColor(PRIMARY), max(2.0, side * 0.022)))
        p.setBrush(QBrush(QColor(SURFACE)))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # ── Hour marks ───────────────────────────────────────────────────
        for i in range(12):
            ang = math.radians(i * 30)
            sin_a, cos_a = math.sin(ang), math.cos(ang)
            outer = r * 0.88
            inner = r * (0.74 if i % 3 == 0 else 0.80)
            p.setPen(QPen(QColor(PRIMARY if i % 3 == 0 else ON_SURFACE_VAR),
                          max(1.5, side * (0.018 if i % 3 == 0 else 0.010)),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx + inner * sin_a, cy - inner * cos_a),
                       QPointF(cx + outer * sin_a, cy - outer * cos_a))

        # ── Hands ────────────────────────────────────────────────────────
        # Fractional positions, so the hour hand sits between marks the way a
        # real one does rather than jumping on the hour.
        secs = now.second + now.microsecond / 1_000_000
        mins = now.minute + secs / 60
        hours = (now.hour % 12) + mins / 60

        def hand(angle_deg: float, length: float, width: float, colour: str):
            ang = math.radians(angle_deg)
            p.setPen(QPen(QColor(colour), max(2.0, side * width),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            # A short tail past the centre — it balances the hand visually.
            p.drawLine(QPointF(cx - r * 0.12 * math.sin(ang),
                               cy + r * 0.12 * math.cos(ang)),
                       QPointF(cx + r * length * math.sin(ang),
                               cy - r * length * math.cos(ang)))

        hand(hours * 30, 0.48, 0.030, ON_SURFACE)
        hand(mins * 6, 0.68, 0.022, ON_SURFACE)
        hand(secs * 6, 0.76, 0.012, SECONDARY)

        # Centre pin
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(PRIMARY)))
        p.drawEllipse(QPointF(cx, cy), side * 0.026, side * 0.026)


class AlarmWindow(QWidget):
    """The alarm itself: rings, waits, snoozes or is dismissed."""

    def __init__(self, message: str, subtitle: str = "Reminder",
                 reminder_id: str = "", kind: str = "reminder"):
        super().__init__()
        self._message = message
        self._reminder_id = reminder_id
        self._kind = kind
        self._scale = _text_scale()
        self._drag_from = None
        self._sound_on = False

        self.setWindowTitle("SivaSafe Alarm")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        icon_file = BASE_DIR / "assets" / "sivasafe.png"
        if icon_file.exists():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_file)))

        self._build(message, subtitle)
        self._size_and_centre()

        self._ring_timer = QTimer(self)
        self._ring_timer.setSingleShot(True)
        self._ring_timer.timeout.connect(self._silence)

        self._snooze_timer = QTimer(self)
        self._snooze_timer.setSingleShot(True)
        self._snooze_timer.timeout.connect(self._wake_from_snooze)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_digital)
        self._clock_timer.start(1000)

    # ── construction ─────────────────────────────────────────────────────

    def _fs(self, px: float) -> int:
        """A design pixel size at the user's chosen text size."""
        return max(9, round(px * self._scale))

    def _build(self, message: str, subtitle: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 32)
        root.setSpacing(0)

        # Header
        header = QLabel("⏰  SIVASAFE ALARM")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet(
            f"color: {PRIMARY}; background: transparent; letter-spacing: 3px;"
            f"font-size: {self._fs(15)}px; font-weight: bold;")
        root.addWidget(header)
        # The clock/time/message group floats in the middle of the card,
        # between these two stretches, with the buttons pinned to the bottom.
        root.addStretch(1)

        # Clock
        self._clock_face = ClockFace()
        dial = round(210 * min(self._scale, 1.15))
        self._clock_face.setFixedSize(dial, dial)
        clock_row = QHBoxLayout()
        clock_row.addStretch()
        clock_row.addWidget(self._clock_face)
        clock_row.addStretch()
        root.addLayout(clock_row)
        root.addSpacing(10)

        # Digital time
        self._time_lbl = QLabel(datetime.now().strftime("%I:%M %p").lstrip("0"))
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_lbl.setStyleSheet(
            f"color: {ON_SURFACE}; background: transparent;"
            f"font-size: {self._fs(38)}px; font-weight: bold;")
        root.addWidget(self._time_lbl)
        root.addSpacing(16)

        # The message — the whole reason the window is here, so it is the
        # largest thing on it after the time.
        self._msg_lbl = QLabel(message)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_lbl.setStyleSheet(
            f"color: {ON_SURFACE}; background: transparent;"
            f"font-size: {self._fs(26)}px; font-weight: bold;")
        root.addWidget(self._msg_lbl)
        root.addSpacing(6)

        self._sub_lbl = QLabel(subtitle)
        self._sub_lbl.setWordWrap(True)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setStyleSheet(
            f"color: {ON_SURFACE_VAR}; background: transparent;"
            f"font-size: {self._fs(16)}px;")
        root.addWidget(self._sub_lbl)
        root.addStretch(1)
        root.addSpacing(20)

        # Buttons
        btns = QHBoxLayout()
        btns.setSpacing(14)

        self._snooze_btn = QPushButton(f"😴  Snooze {SNOOZE_MINUTES} min")
        self._snooze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._snooze_btn.setMinimumHeight(self._fs(64))
        self._snooze_btn.setStyleSheet(f"""
            QPushButton {{
                background: {SECONDARY_BTN_BG};
                color: {SECONDARY};
                font-size: {self._fs(19)}px;
                font-weight: bold;
                border: 2px solid rgba(139,94,60,0.35);
                border-radius: 16px;
                padding: 0 10px;
            }}
            QPushButton:hover  {{ background: #E6D5B8; }}
            QPushButton:pressed{{ background: #D8C3A0; }}
        """)
        self._snooze_btn.clicked.connect(self._snooze)

        self._dismiss_btn = QPushButton("✓  Dismiss")
        self._dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss_btn.setMinimumHeight(self._fs(64))
        self._dismiss_btn.setStyleSheet(f"""
            QPushButton {{
                background: {PRIMARY};
                color: {WHITE};
                font-size: {self._fs(19)}px;
                font-weight: bold;
                border: none;
                border-radius: 16px;
                padding: 0 10px;
            }}
            QPushButton:hover  {{ background: #2C5446; }}
            QPushButton:pressed{{ background: #234337; }}
        """)
        self._dismiss_btn.clicked.connect(self._dismiss)

        btns.addWidget(self._snooze_btn, 1)
        btns.addWidget(self._dismiss_btn, 1)
        root.addLayout(btns)

    def _size_and_centre(self):
        w = round(520 * min(self._scale, 1.25))
        h = round(660 * min(self._scale, 1.25))
        screen = QApplication.primaryScreen().availableGeometry()
        # Never taller than the screen it opens on.
        h = min(h, int(screen.height() * 0.94))
        w = min(w, int(screen.width() * 0.94))
        self.resize(w, h)
        self.move(screen.x() + (screen.width() - w) // 2,
                  screen.y() + (screen.height() - h) // 2)

    # ── the card itself is painted, because the window is frameless ──────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 28, 28)
        p.fillPath(path, QBrush(QColor(BG)))
        p.setPen(QPen(QColor(PRIMARY_CTR), 2))
        p.drawPath(path)

    # ── dragging, since there is no title bar to grab ────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = (event.globalPosition().toPoint()
                               - self.frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _event):
        self._drag_from = None

    def keyPressEvent(self, event):
        # Escape snoozes rather than closes: there is no way to shut this
        # window without making a choice about the reminder behind it.
        if event.key() == Qt.Key.Key_Escape:
            self._snooze()
        else:
            super().keyPressEvent(event)

    # ── ringing ──────────────────────────────────────────────────────────

    def start(self):
        self.show()
        self._raise_to_front()
        self._ring()

    def _raise_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        # WindowStaysOnTopHint gets the window drawn above the others, but not
        # the keyboard focus, and a fullscreen app can still be the active
        # window in front of it. This is the Windows-level nudge that makes it
        # the foreground window as well.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass

    def _ring(self):
        self._start_sound()
        self._clock_face.set_ringing(True)
        self._sub_lbl.setText(self._subtitle_text())
        self._ring_timer.start(RING_SECONDS * 1000)

    def _subtitle_text(self) -> str:
        base = "Medication" if self._kind == "medication" else "Reminder"
        return f"{base} · {datetime.now().strftime('%A, %d %B')}"

    def _start_sound(self):
        if sys.platform != "win32":
            return
        try:
            import winsound
            wav = BASE_DIR / "assets" / "sounds" / "alarm.wav"
            if wav.exists():
                winsound.PlaySound(
                    str(wav),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            self._sound_on = True
        except Exception as e:
            print(f"[Alarm] Could not play the alarm sound: {e}")

    def _stop_sound(self):
        if not self._sound_on or sys.platform != "win32":
            return
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        self._sound_on = False

    def _silence(self):
        """Rung long enough. Go quiet, but stay on screen."""
        self._stop_sound()
        self._clock_face.set_ringing(False)
        self._sub_lbl.setText(
            f"{self._subtitle_text()}  ·  still waiting for you")

    def _tick_digital(self):
        self._time_lbl.setText(datetime.now().strftime("%I:%M %p").lstrip("0"))

    # ── answering ────────────────────────────────────────────────────────

    def _snooze(self):
        self._stop_sound()
        self._ring_timer.stop()
        self._clock_face.set_ringing(False)
        self.hide()
        self._snooze_timer.start(SNOOZE_MINUTES * 60 * 1000)
        self._record_snooze()

    def _wake_from_snooze(self):
        self._msg_lbl.setText(self._message)
        self.start()

    def _dismiss(self):
        self._stop_sound()
        self._ring_timer.stop()
        self._snooze_timer.stop()
        self._record_dismiss()
        self.close()
        QApplication.quit()

    # ── writing back to the store, so the app agrees with the alarm ──────

    def _record_snooze(self):
        if not self._reminder_id or self._kind != "reminder":
            return
        try:
            from actions.reminder_store import snooze_reminder
            snooze_reminder(self._reminder_id, SNOOZE_MINUTES)
        except Exception as e:
            print(f"[Alarm] Could not record the snooze: {e}")

    def _record_dismiss(self):
        if not self._reminder_id or self._kind != "reminder":
            return
        try:
            from actions.reminder_store import mark_reminder_fired
            mark_reminder_fired(self._reminder_id)
        except Exception as e:
            print(f"[Alarm] Could not record the dismissal: {e}")


def show_alarm(message: str, subtitle: str = "Reminder",
               reminder_id: str = "", kind: str = "reminder") -> int:
    """Open the alarm and block until it is dismissed. Returns an exit code."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # hidden during snooze ≠ finished

    # Atkinson Hyperlegible is the app's reading font; use it here too if the
    # system has it, so the alarm does not look like a different program.
    for family in ("Atkinson Hyperlegible Next", "Atkinson Hyperlegible"):
        if family in QFontDatabase.families():
            app.setFont(QFont(family))
            break

    win = AlarmWindow(message, subtitle, reminder_id, kind)
    win.start()
    return app.exec()


def main():
    ap = argparse.ArgumentParser(description="SivaSafe alarm window")
    ap.add_argument("--message", default="Reminder")
    ap.add_argument("--subtitle", default="Reminder")
    ap.add_argument("--id", dest="reminder_id", default="")
    ap.add_argument("--kind", default="reminder",
                    choices=("reminder", "medication"))
    args = ap.parse_args()
    sys.exit(show_alarm(args.message, args.subtitle,
                        args.reminder_id, args.kind))


if __name__ == "__main__":
    main()
