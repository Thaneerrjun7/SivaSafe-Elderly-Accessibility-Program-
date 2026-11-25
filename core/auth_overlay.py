"""
core/auth_overlay.py — Cinematic authentication overlay for single-window transition.

Sits on top of MainWindow as a child widget, covering the entire window.
Phases:
  1. AUTH    — shows the AuthGate widget (camera + password)
  2. WELCOME — cinematic "IDENTITY CONFIRMED" / "WELCOME BACK, SIR" typewriter
  3. REVEAL  — window resizes to full size, overlay fades out, main content appears
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QParallelAnimationGroup,
    QSequentialAnimationGroup, QRect, QEasingCurve, pyqtSignal,
    QByteArray, pyqtProperty, QPointF, QEvent,
)
from PyQt6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPen, QBrush,
    QRadialGradient, QLinearGradient, QPixmap,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel,
    QApplication, QStackedWidget, QHBoxLayout, QSizePolicy,
)

from core.motion import Motion


# ── Colours (match auth.py & ui.py) ──────────────────────────────────────────

_BG       = "#EBE7DF"
_PRIMARY  = "#376757"
_PRI_DIM  = "#7DAF9C"
_ACCENT   = "#8B5E3C"
_TEXT     = "#2D2926"
_TEXT_DIM = "#5D4037"
_GREEN    = "#376757"

# ── Auth window dimensions ───────────────────────────────────────────────────

AUTH_WIDTH  = 800
# 900, not 740. The camera sends a 4:3 picture and the preview is now shaped to
# match it rather than cropping it to fit, so height is what decides how large
# the person sees themselves: at 740 the column had only ~300px left over for
# the preview, which is a 400px-wide picture. Callers cap this against the
# screen (_transition_to already does), so a short display simply gets less.
AUTH_HEIGHT = 900      # approximate; content-dependent
# Password-only sign-in opens no camera, so the window does not need to hold a
# preview and would otherwise be mostly empty space above the password box.
AUTH_HEIGHT_PW = 430
# The recovery screens carry a handful of controls each, so the window shrinks
# to fit them rather than leaving a code box marooned in a camera-sized frame.
RECOVERY_WIDTH  = 560
RECOVERY_HEIGHT = 520
NEW_PW_HEIGHT   = 460


# ═════════════════════════════════════════════════════════════════════════════
#  Curtain — the fade primitive both transitions are built from
# ═════════════════════════════════════════════════════════════════════════════

class _Curtain(QWidget):
    """
    A frozen snapshot of a screen, painted at an animated opacity.

    This exists instead of QGraphicsOpacityEffect, which is what made the old
    transition drop frames.  An opacity effect re-renders its *entire source
    widget tree* into an offscreen buffer for every frame of the animation —
    during the reveal that tree was the whole auth screen, with a live orb
    timer ticking inside it, being resized on every tick as the window grew.
    Blitting one cached pixmap costs the same no matter how complex the screen
    behind it is, which is what keeps the fade at frame rate.

    Two extra tricks keep it cheap:

    * While every pixel of the curtain is painted (opaque snapshot, or a
      `below` snapshot underneath a partly-faded one) it sets
      `WA_OpaquePaintEvent`, so Qt skips repainting the widgets underneath
      entirely — the dashboard does not redraw once per frame of the grow.
    * `set_below()` freezes the destination screen into a pixmap too, so the
      cross-fade is two blits per frame rather than a full re-render of the
      dashboard.  `release_below()` hands the last of the fade back to the live
      widgets so nothing pops when the curtain finally goes.
    """

    def __init__(self, parent: QWidget, snapshot: QPixmap, bg: str):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._snapshot = snapshot if snapshot is not None else QPixmap()
        self._below = QPixmap()
        self._bg = QColor(bg)
        self._opacity = 1.0
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()

    # ── animated property ────────────────────────────────────────────────

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float):
        self._opacity = value
        self._sync_opaque()
        self.update()

    curtain_opacity = pyqtProperty(float, _get_opacity, _set_opacity)

    # ── what sits behind the curtain ─────────────────────────────────────

    def set_below(self, pixmap: QPixmap):
        """Freeze the destination screen into the curtain itself."""
        self._below = pixmap if pixmap is not None else QPixmap()
        self._sync_opaque()
        self.update()

    def release_below(self):
        """Drop the frozen destination so the live widgets finish the fade."""
        if self._below.isNull():
            return
        self._below = QPixmap()
        self._sync_opaque()
        self.update()

    def snapshot_below(self) -> QPixmap:
        """
        Grab whatever is underneath us.

        The curtain is a child of the widget being grabbed, so it has to step
        out of frame first — otherwise `grab()` renders the curtain into its
        own `below` pixmap and the fade goes nowhere.  Nothing is painted
        between the hide and the show: both only queue repaints, and no event
        loop turn happens in between.
        """
        host = self.parentWidget()
        if host is None:
            return QPixmap()
        self.hide()
        try:
            return host.grab()
        finally:
            self.show()
            self.raise_()

    def _sync_opaque(self):
        """
        Tell Qt whether we still cover every pixel.

        True while we are fully opaque, or whenever a frozen `below` fills the
        widget; false the moment real transparency starts, at which point the
        widgets underneath need one repaint to come back.
        """
        opaque = self._opacity >= 1.0 or not self._below.isNull()
        if self.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent) == opaque:
            return
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)
        if not opaque:
            host = self.parentWidget()
            if host is not None:
                host.update()

    # ── geometry + paint ─────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False

    def paintEvent(self, event):
        p = QPainter(self)
        if not self._below.isNull():
            p.drawPixmap(0, 0, self._below)
        p.setOpacity(max(0.0, min(1.0, self._opacity)))
        p.fillRect(self.rect(), self._bg)
        if not self._snapshot.isNull():
            dpr = self._snapshot.devicePixelRatio() or 1.0
            w = int(self._snapshot.width() / dpr)
            h = int(self._snapshot.height() / dpr)
            p.drawPixmap((self.width() - w) // 2, (self.height() - h) // 2,
                         self._snapshot)
        p.end()


# ═════════════════════════════════════════════════════════════════════════════
#  Recovery pages — shown when the password is genuinely lost
# ═════════════════════════════════════════════════════════════════════════════

def _field(placeholder: str, secret: bool = False, big: bool = False):
    from PyQt6.QtWidgets import QLineEdit
    f = QLineEdit()
    f.setPlaceholderText(placeholder)
    f.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if secret:
        f.setEchoMode(QLineEdit.EchoMode.Password)
    f.setFixedHeight(56 if big else 46)
    size = 26 if big else 14
    space = "letter-spacing: 10px;" if big else ""
    f.setStyleSheet(f"""
        QLineEdit {{
            background: rgba(255,255,255,0.85); color: {_TEXT};
            border: 2px solid rgba(133,116,72,0.30); border-radius: 12px;
            padding: 4px 12px; font-size: {size}px; {space}
        }}
        QLineEdit:focus {{ border: 2px solid {_PRIMARY}; }}
    """)
    return f


def _button(text: str, primary: bool = False):
    from core.motion import AnimatedButton
    b = AnimatedButton(text)
    b.setFixedHeight(46)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    bg = f"rgba(55,103,87,0.12)" if primary else "rgba(255,255,255,0.60)"
    fg = _PRIMARY if primary else _TEXT_DIM
    bd = _PRIMARY if primary else "rgba(133,116,72,0.30)"
    b.setStyleSheet(f"""
        QPushButton {{
            background: {bg}; color: {fg};
            border: 2px solid {bd}; border-radius: 12px;
            font-size: 14px; font-weight: bold; padding: 4px 18px;
        }}""")
    return b


class _RecoveryPage(QWidget):
    """
    "We have emailed you a code." A screen of its own, not a panel bolted
    under the password box — because at this point the password box is dead
    and this is the only way in.
    """

    verified = pyqtSignal()
    go_back = pyqtSignal()
    mail_result = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(44, 36, 44, 36)
        lay.setSpacing(14)
        lay.addStretch(1)

        title = QLabel("Getting you back in")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_PRIMARY}; background: transparent;")
        lay.addWidget(title)

        self._where = QLabel("")
        self._where.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._where.setWordWrap(True)
        self._where.setFont(QFont("Segoe UI", 11))
        self._where.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
        lay.addWidget(self._where)

        self._send_btn = _button("Email me a code", primary=True)
        self._send_btn.clicked.connect(self._on_send)
        lay.addWidget(self._send_btn)

        self._code = _field("● ● ● ● ● ●", big=True)
        self._code.setMaxLength(6)
        self._code.returnPressed.connect(self._on_verify)
        lay.addWidget(self._code)

        self._ok_btn = _button("Let me in", primary=True)
        self._ok_btn.clicked.connect(self._on_verify)
        lay.addWidget(self._ok_btn)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setFont(QFont("Segoe UI", 10))
        self._status.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
        lay.addWidget(self._status)

        self._back_btn = _button("Go back")
        self._back_btn.clicked.connect(self.go_back.emit)
        lay.addWidget(self._back_btn)
        lay.addStretch(1)

        self.mail_result.connect(self._on_mail_result)

    # ── behaviour ────────────────────────────────────────────────

    def refresh(self, locked_out: bool = False):
        from core import recovery, mailer
        addr = recovery.recovery_email()
        if not addr:
            self._where.setText(
                "No email address has been saved, so a code cannot be sent. "
                "Ask whoever set this computer up for help.")
            self._send_btn.setEnabled(False)
        elif not mailer.is_configured():
            self._where.setText(
                "SIVA has no email account to send from yet, so a code "
                "cannot be sent.")
            self._send_btn.setEnabled(False)
        else:
            self._where.setText(
                f"We can send a 6-digit code to  {recovery.mask_email(addr)}")
            self._send_btn.setEnabled(True)
        self._back_btn.setVisible(not locked_out)
        self._code.clear()
        self._say("")

    def _say(self, text: str, bad: bool = False):
        self._status.setText(text)
        col = "#B3261E" if bad else _TEXT_DIM
        self._status.setStyleSheet(f"color: {col}; background: transparent;")

    def _on_send(self):
        import threading
        from core import recovery, mailer
        if not recovery.can_send():
            wait = recovery.seconds_until_send_allowed()
            self._say(f"Too many codes already sent. Try again in "
                      f"{max(1, wait // 60)} minute(s).", True)
            return
        addr = recovery.recovery_email()
        code = recovery.issue_code()
        if code is None:
            self._say("Could not make a code just now.", True)
            return
        self._send_btn.setEnabled(False)
        self._say("Sending…")

        def work():
            ok, msg = mailer.send_recovery_code(addr, code)
            self.mail_result.emit(ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_mail_result(self, ok: bool, msg: str):
        self._send_btn.setEnabled(True)
        if ok:
            self._say("Check your email. The code works for 10 minutes.")
            self._code.setFocus()
        else:
            self._say(msg, True)

    def _on_verify(self):
        from core import recovery
        if recovery.verify_code(self._code.text()):
            self._say("")
            self.verified.emit()
        else:
            self._code.clear()
            self._say("That code is not right, or it has expired.", True)


class _NewPasswordPage(QWidget):
    """
    Straight after a good code: choose a new password.

    A correct code does not simply let you in. Needing one means the old
    password is gone, so the only sensible next step is setting one that will
    be remembered.
    """

    saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(44, 36, 44, 36)
        lay.setSpacing(14)
        lay.addStretch(1)

        title = QLabel("Choose a new password")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_PRIMARY}; background: transparent;")
        lay.addWidget(title)

        sub = QLabel("Pick something you will remember.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Segoe UI", 11))
        sub.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
        lay.addWidget(sub)

        self._pw1 = _field("New password", secret=True)
        self._pw2 = _field("Type it again", secret=True)
        self._pw2.returnPressed.connect(self._on_save)
        lay.addWidget(self._pw1)
        lay.addWidget(self._pw2)

        btn = _button("Save and go in", primary=True)
        btn.clicked.connect(self._on_save)
        lay.addWidget(btn)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setFont(QFont("Segoe UI", 10))
        self._status.setStyleSheet(f"color: #B3261E; background: transparent;")
        lay.addWidget(self._status)
        lay.addStretch(1)

    def focus_first(self):
        self._pw1.clear()
        self._pw2.clear()
        self._status.setText("")
        self._pw1.setFocus()

    def _on_save(self):
        from core.auth import _hash_password, _clear_lockout
        from core import recovery
        a, b = self._pw1.text().strip(), self._pw2.text().strip()
        if len(a) < 4:
            self._status.setText("Please use at least 4 characters.")
            return
        if a != b:
            self._status.setText("The two passwords do not match.")
            return
        h, s = _hash_password(a)
        cfg = recovery._read_config()
        cfg["app_password_hash"] = h
        cfg["app_password_salt"] = s
        if not recovery._write_config(cfg):
            self._status.setText("Could not save. Please try again.")
            return
        _clear_lockout()          # a new password ends the lockout
        self.saved.emit()


# ═════════════════════════════════════════════════════════════════════════════
#  Glowing Orb Widget — drawn for the welcome screen
# ═════════════════════════════════════════════════════════════════════════════

class _GlowOrb(QWidget):
    """A simple glowing green orb with breathing pulse."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 140)
        self._pulse = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._t = 0
        # Not started here: this page is built while the camera is still
        # running, and there is no reason to tick 33 times a second on the GUI
        # thread for a widget nobody is looking at yet.

    def start(self):
        if not self._tmr.isActive():
            self._tmr.start(30)

    def _tick(self):
        self._t += 1
        self._pulse = 0.5 + 0.5 * math.sin(self._t * 0.08)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.width() / 2, self.height() / 2
        base_r = 36
        pulse_r = base_r + 6 * self._pulse

        # Outer glow
        glow_col = QColor(_PRIMARY)
        glow_col.setAlphaF(0.12 + 0.08 * self._pulse)
        grad = QRadialGradient(QPointF(cx, cy), pulse_r * 1.8)
        grad.setColorAt(0.0, glow_col)
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), pulse_r * 1.8, pulse_r * 1.8)

        # Mid glow ring
        ring_col = QColor(_PRIMARY)
        ring_col.setAlphaF(0.18 + 0.10 * self._pulse)
        grad2 = QRadialGradient(QPointF(cx, cy), pulse_r * 1.3)
        grad2.setColorAt(0.0, ring_col)
        grad2.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(grad2))
        p.drawEllipse(QPointF(cx, cy), pulse_r * 1.3, pulse_r * 1.3)

        # Core orb
        core_col = QColor(_PRIMARY)
        core_col.setAlphaF(0.85 + 0.15 * self._pulse)
        p.setBrush(QBrush(core_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), pulse_r, pulse_r)

        # Inner bright spot
        bright = QColor("#7DAF9C")
        bright.setAlphaF(0.4 + 0.3 * self._pulse)
        inner_grad = QRadialGradient(QPointF(cx - 6, cy - 6), pulse_r * 0.6)
        inner_grad.setColorAt(0.0, bright)
        inner_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(inner_grad))
        p.drawEllipse(QPointF(cx, cy), pulse_r * 0.7, pulse_r * 0.7)

        # Checkmark
        check_col = QColor("#FFFFFF")
        check_col.setAlphaF(0.9)
        pen = QPen(check_col, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Checkmark path (relative to center)
        path = QPainterPath()
        s = pulse_r * 0.35
        path.moveTo(cx - s * 0.6, cy + s * 0.05)
        path.lineTo(cx - s * 0.1, cy + s * 0.5)
        path.lineTo(cx + s * 0.7, cy - s * 0.4)
        p.drawPath(path)

        p.end()

    def stop(self):
        self._tmr.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  Typewriter Label — reveals text character-by-character
# ═════════════════════════════════════════════════════════════════════════════

class _TypewriterLabel(QLabel):
    """Label that types out its text one character at a time."""

    typing_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._full_text = ""
        self._char_idx = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._next_char)

    def type_text(self, text: str, char_delay_ms: int = 35):
        """Start typing the given text."""
        self._full_text = text
        self._char_idx = 0
        self.setText("")
        self._tmr.start(char_delay_ms)

    def _next_char(self):
        self._char_idx += 1
        self.setText(self._full_text[:self._char_idx])
        if self._char_idx >= len(self._full_text):
            self._tmr.stop()
            self.typing_done.emit()

    def set_instant(self, text: str):
        self._tmr.stop()
        self._full_text = text
        self._char_idx = len(text)
        self.setText(text)


# ═════════════════════════════════════════════════════════════════════════════
#  WelcomePage — cinematic "IDENTITY CONFIRMED" / "WELCOME BACK" sequence
# ═════════════════════════════════════════════════════════════════════════════

class _WelcomePage(QWidget):
    """Cinematic welcome animation shown after auth succeeds."""

    animation_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Spacer to vertically center
        layout.addStretch(3)

        # Orb
        orb_row = QHBoxLayout()
        orb_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._orb = _GlowOrb()
        orb_row.addWidget(self._orb)
        layout.addLayout(orb_row)

        layout.addSpacing(24)

        # Primary typewriter line
        self._line1 = _TypewriterLabel()
        self._line1.setFont(QFont("Consolas", 14, QFont.Weight.Bold))
        self._line1.setStyleSheet(f"color: {_PRIMARY}; background: transparent; letter-spacing: 3px;")
        layout.addWidget(self._line1)

        layout.addSpacing(12)

        # Secondary typewriter line
        self._line2 = _TypewriterLabel()
        self._line2.setFont(QFont("Segoe UI", 11))
        self._line2.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent; letter-spacing: 1px;")
        layout.addWidget(self._line2)

        layout.addStretch(4)

        # Subtle footer
        self._footer = QLabel("S . I . V . A . S . A . F . E")
        self._footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._footer.setFont(QFont("Consolas", 8))
        self._footer.setStyleSheet(f"color: {_PRI_DIM}; background: transparent; letter-spacing: 6px;")
        self._footer.setFixedHeight(30)
        layout.addWidget(self._footer)

    def start_animation(self):
        """Kick off the cinematic sequence."""
        self._orb.start()
        # Phase 1: Type "IDENTITY CONFIRMED"
        self._line1.type_text("IDENTITY CONFIRMED", char_delay_ms=32)
        self._line1.typing_done.connect(self._phase2)

    def _phase2(self):
        """After first line typed → pause → type second line."""
        QTimer.singleShot(500, self._type_welcome)

    def _type_welcome(self):
        self._line2.type_text("Welcome back, sir.", char_delay_ms=40)
        self._line2.typing_done.connect(self._phase3)

    def _phase3(self):
        """After second line → pause → signal done."""
        QTimer.singleShot(800, self._signal_done)

    def _signal_done(self):
        self.animation_done.emit()

    def cleanup(self):
        self._orb.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  AuthOverlay — the master overlay that orchestrates auth → welcome → reveal
# ═════════════════════════════════════════════════════════════════════════════

class AuthOverlay(QWidget):
    """
    Full-window overlay on top of MainWindow.
    Manages: auth → cinematic welcome → resize + fadeout → reveal main content.

    Usage in main():
        win = MainWindow(...)
        overlay = AuthOverlay(win, config_dir=...)
        overlay.ready.connect(lambda: start_siva(ui))
        overlay.cancelled.connect(lambda: sys.exit(0))
        win.show()
        overlay.begin()
    """

    # Called when auth is complete and overlay is fully gone
    ready = pyqtSignal()
    # Called when auth is cancelled / window closed
    cancelled = pyqtSignal()

    def __init__(self, parent: QWidget, config_dir: Path, mode: str = None):
        super().__init__(parent)
        self._config_dir = config_dir
        self._mode = mode
        self._phase = "auth"      # auth → welcome → reveal

        # Fill the parent completely
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background: {_BG};")

        # Keep on top of all sibling widgets
        self.raise_()

        # ── Stack: page 0 = auth, page 1 = welcome ──────────────────────
        self._stack = QStackedWidget(self)

        # Auth page
        from core.auth import AuthGate
        self._auth = AuthGate(config_dir=config_dir, parent=self, mode=mode)
        self._stack.addWidget(self._auth)

        # Recovery pages — reached when the password is genuinely lost
        self._recovery = _RecoveryPage(self)
        self._stack.addWidget(self._recovery)
        self._new_pw = _NewPasswordPage(self)
        self._stack.addWidget(self._new_pw)

        # Welcome page
        self._welcome = _WelcomePage(self)
        self._stack.addWidget(self._welcome)

        self._stack.setCurrentWidget(self._auth)

        self._auth.recovery_needed.connect(self._show_recovery)
        self._recovery.verified.connect(self._show_new_password)
        self._recovery.go_back.connect(self._back_to_auth)
        self._new_pw.saved.connect(self._on_new_password_saved)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        # ── Signals ──────────────────────────────────────────────────────
        self._auth.auth_completed.connect(self._on_auth_success)

        # ── Keep ourselves sized to parent ───────────────────────────────
        if parent:
            self.setGeometry(0, 0, parent.width(), parent.height())

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def needs_password_reset(self) -> bool:
        """
        Did a face scan rescue a user who could not remember their password?

        Read by main.py once the dashboard is up. Exposed as a property rather
        than folded into `ready`, which is a bare pyqtSignal() that main.py
        already connects to — changing its signature would break that.
        """
        try:
            return bool(getattr(self._auth, "needs_password_reset", False))
        except RuntimeError:
            return False

    def begin(self):
        """Start the authentication flow."""
        self.show()
        self.raise_()
        self._auth.start()

    # ── Phase transitions ────────────────────────────────────────────────

    # ── moving between sign-in screens ───────────────────────────────────
    #
    # Each screen is a different shape, so changing screen resizes the window.
    # That is the same move the reveal makes on the way to the dashboard —
    # curtain up, window animates, curtain cross-fades into what is now
    # underneath — so the whole sign-in reads as one continuous thing rather
    # than a series of dialogs.

    def _transition_to(self, page: QWidget, target_w: int, target_h: int,
                       after=None):
        parent = self.parentWidget()
        if parent is None:
            self._stack.setCurrentWidget(page)
            if after:
                after()
            return

        top = parent.window()
        self._drop_curtain()
        self._curtain = _Curtain(parent, self.grab(), _BG)
        self._stack.setCurrentWidget(page)
        self._pending_after = after

        screen = QApplication.primaryScreen().availableGeometry()
        try:
            from core import text_scale
            grow = 1.0 + (text_scale.factor() - 1.0) * 0.5
            target_w = int(target_w * grow)
            target_h = int(target_h * grow)
        except Exception:
            pass
        target_w = min(target_w, int(screen.width() * 0.94))
        target_h = min(target_h, int(screen.height() * 0.94))

        rect = QRect(screen.x() + (screen.width() - target_w) // 2,
                     screen.y() + (screen.height() - target_h) // 2,
                     target_w, target_h)

        anim = QPropertyAnimation(top, b"geometry", self)
        anim.setDuration(460)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.setStartValue(top.geometry())
        anim.setEndValue(rect)
        anim.finished.connect(lambda: QTimer.singleShot(30, self._fade_into_page))
        anim.start()
        self._page_anim = anim

    def _fade_into_page(self):
        curtain = getattr(self, "_curtain", None)
        if curtain is None:
            self._finish_page_transition()
            return
        curtain.set_below(curtain.snapshot_below())
        fade = QPropertyAnimation(curtain, b"curtain_opacity", self)
        fade.setDuration(360)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.valueChanged.connect(self._on_fade_value)
        fade.finished.connect(self._finish_page_transition)
        fade.start()
        self._page_fade = fade

    def _finish_page_transition(self):
        self._drop_curtain()
        after = getattr(self, "_pending_after", None)
        self._pending_after = None
        if after:
            after()

    # ── recovery flow ────────────────────────────────────────────────────

    def _show_recovery(self):
        """Guessing is over, or the user asked — go to the code screen."""
        from core import auth as _auth_mod
        locked = _auth_mod.recovery_required()
        self._recovery.refresh(locked_out=locked)
        self._auth.cleanup()          # the camera is not needed here
        self._transition_to(self._recovery, RECOVERY_WIDTH, RECOVERY_HEIGHT)

    def _back_to_auth(self):
        self._transition_to(self._auth, AUTH_WIDTH, AUTH_HEIGHT)

    def _show_new_password(self):
        self._transition_to(self._new_pw, RECOVERY_WIDTH, NEW_PW_HEIGHT,
                            after=self._new_pw.focus_first)

    def _on_new_password_saved(self):
        """New password set — straight into the welcome cinematic."""
        self._phase = "welcome"
        self._transition_to(self._welcome, AUTH_WIDTH, AUTH_HEIGHT,
                            after=self._start_welcome)

    def _start_welcome(self):
        self._welcome.animation_done.connect(self._begin_reveal)
        self._welcome.start_animation()

    def _on_auth_success(self):
        """Auth confirmed → crossfade to welcome page."""
        self._phase = "welcome"

        # Freeze the auth screen, then release the camera immediately: its
        # frames were still arriving on the GUI thread through the old fade.
        self._curtain = _Curtain(self, self._auth.grab(), _BG)
        self._auth.cleanup()

        # Swap the page underneath the curtain, then dissolve the curtain.
        self._stack.setCurrentWidget(self._welcome)

        fade = QPropertyAnimation(self._curtain, b"curtain_opacity", self)
        fade.setDuration(300)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.finished.connect(self._show_welcome)
        fade.start()
        self._fade_out_anim = fade  # prevent GC

    def _show_welcome(self):
        """Curtain gone → start the cinematic."""
        self._drop_curtain()

        self._welcome.animation_done.connect(self._begin_reveal)
        self._welcome.start_animation()

    def _drop_curtain(self):
        curtain = getattr(self, "_curtain", None)
        if curtain is None:
            return
        self._curtain = None
        try:
            host = curtain.parentWidget()
            if host is not None:
                host.removeEventFilter(curtain)
            curtain.hide()
            curtain.deleteLater()
        except RuntimeError:
            pass

    def _begin_reveal(self):
        """Welcome done → resize window + fade overlay out."""
        self._phase = "reveal"

        parent = self.parentWidget()
        if not parent:
            self.ready.emit()
            return

        # Calculate the target window geometry (MainWindow's full size)
        screen = QApplication.primaryScreen().availableGeometry()
        target_w, target_h = 1400, 900
        try:
            from ui import _DEFAULT_W, _DEFAULT_H
            target_w, target_h = _DEFAULT_W, _DEFAULT_H
        except ImportError:
            pass

        # Apply text scale factor if available
        try:
            from core import text_scale
            grow = 1.0 + (text_scale.factor() - 1.0) * 0.5
            target_w = min(int(target_w * grow), int(screen.width() * 0.94))
            target_h = min(int(target_h * grow), int(screen.height() * 0.94))
        except Exception:
            pass

        target_x = screen.x() + (screen.width() - target_w) // 2
        target_y = screen.y() + (screen.height() - target_h) // 2
        target_rect = QRect(target_x, target_y, target_w, target_h)

        # Get the top-level window (MainWindow) for the resize animation
        top = parent.window()

        # ── Hand the screen over to a static curtain ─────────────────
        #
        # The orb timer stops *before* the snapshot is taken, so nothing is
        # still animating behind the curtain: from here to the end of the
        # reveal the only thing that repaints is the curtain itself.  The live
        # overlay is hidden outright, which also takes its whole widget tree
        # out of the per-frame relayout the growing window would otherwise
        # force on it.
        self._welcome.cleanup()
        self._curtain = _Curtain(parent, self.grab(), _BG)
        self.hide()

        # ── Step 1: grow the window, under an opaque curtain ─────────
        geom_anim = QPropertyAnimation(top, b"geometry", self)
        geom_anim.setDuration(560)
        geom_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        geom_anim.setStartValue(top.geometry())
        geom_anim.setEndValue(target_rect)
        # One turn of the event loop so the dashboard's layout settles at the
        # final size before it is photographed for the cross-fade.
        geom_anim.finished.connect(lambda: QTimer.singleShot(40, self._fade_in_dashboard))
        geom_anim.start()
        self._geom_anim = geom_anim  # prevent GC

    def _fade_in_dashboard(self):
        """Window is at full size → cross-fade the curtain into the dashboard."""
        curtain = getattr(self, "_curtain", None)
        if curtain is None:
            self._on_reveal_done()
            return

        # Freeze the dashboard too, so each frame is two pixmap blits instead
        # of a full repaint of every panel on the home page.
        curtain.set_below(curtain.snapshot_below())

        fade = QPropertyAnimation(curtain, b"curtain_opacity", self)
        fade.setDuration(460)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        # Near the end, hand the last of the fade to the live dashboard: the
        # frozen copy is a frame or two stale by then, and swapping it back in
        # while the curtain is still faintly visible hides the change.
        fade.valueChanged.connect(self._on_fade_value)
        fade.finished.connect(self._on_reveal_done)
        fade.start()
        self._fade_anim = fade  # prevent GC

    def _on_fade_value(self, value):
        if value <= 0.3:
            curtain = getattr(self, "_curtain", None)
            if curtain is not None:
                curtain.release_below()

    def _on_reveal_done(self):
        """Curtain fully transparent → clean up and signal ready."""
        self._drop_curtain()
        self._welcome.cleanup()
        self.hide()
        self.ready.emit()

        # Schedule deletion
        self.deleteLater()

    # ── Keep sized to parent ─────────────────────────────────────────────

    def resizeEvent(self, event):
        """Stay full-size with parent."""
        super().resizeEvent(event)
