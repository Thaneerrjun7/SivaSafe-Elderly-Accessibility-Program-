from __future__ import annotations

import json
import math
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPointF,
    QPropertyAnimation, QRect, QRectF, QSequentialAnimationGroup, QSize, Qt,
    QTimer, QUrl, pyqtProperty, pyqtSignal, QDate,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent,
    QFont, QFontDatabase, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut,
)
from core.motion import Motion
from core import permissions, prefs, text_scale
from PyQt6.QtWidgets import (
    QApplication, QBoxLayout, QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpacerItem, QSpinBox, QStackedWidget, QTextEdit,
    QVBoxLayout, QWidget, QStyledItemDelegate, QStyle, QTableView
)

# Reminder persistence
try:
    from actions.reminder_store import (
        load_reminders, save_reminder as store_save_reminder,
        toggle_reminder as store_toggle_reminder,
        delete_reminder as store_delete_reminder,
        get_all_sorted as store_get_all_sorted,
        get_active_reminders as store_get_active,
    )
    _HAS_REMINDER_STORE = True
except ImportError:
    _HAS_REMINDER_STORE = False


# ═══════════════════════════════════════════════════════════════════════
# PATH HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_OS = platform.system()


# ═══════════════════════════════════════════════════════════════════════
# DESIGN TOKENS — FROM STITCH EXPORT
# ═══════════════════════════════════════════════════════════════════════


from PyQt6.QtWidgets import QGraphicsDropShadowEffect


from core.motion import (AnimatedButton, CardButton, ExpandableCard,
                         AnimatedScrollArea, fit_wrapped_labels)
class C:
    """SIVASAFE design colour tokens — warm glassmorphic palette."""
    BG             = "#EBE7DF"
    SURFACE        = "#F2F0EB"
    ON_SURFACE     = "#2D2926"
    ON_SURFACE_VAR = "#5D4037"
    PRIMARY        = "#376757"
    PRIMARY_CTR    = "#7DAF9C"
    SECONDARY      = "#8B5E3C"
    SECONDARY_CTR  = "#E6D5B8"
    TERTIARY       = "#D4AF37"
    ERROR          = "#BA1A1A"
    WHITE          = "#FFFFFF"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


# ═══════════════════════════════════════════════════════════════════════
# FONT SETUP
# ═══════════════════════════════════════════════════════════════════════

_FONT_BODY = "Segoe UI"       # fallback if Atkinson not found
_FONT_HUD  = "Consolas"       # fallback if JetBrains Mono not found

def _init_fonts():
    global _FONT_BODY, _FONT_HUD
    families = QFontDatabase.families()
    # Atkinson Hyperlegible Next
    for name in ("Atkinson Hyperlegible Next", "Atkinson Hyperlegible"):
        if name in families:
            _FONT_BODY = name
            break
    else:
        for fb in ("Segoe UI", "Helvetica Neue", "Arial"):
            if fb in families:
                _FONT_BODY = fb
                break
    # JetBrains Mono
    if "JetBrains Mono" in families:
        _FONT_HUD = "JetBrains Mono"
    else:
        for fb in ("Consolas", "Courier New", "monospace"):
            if fb in families:
                _FONT_HUD = fb
                break


# ═══════════════════════════════════════════════════════════════════════
# LAYOUT CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

_DEFAULT_W, _DEFAULT_H = 1400, 900
_MIN_W, _MIN_H         = 1000, 700
_LEFT_W                = 240
_RIGHT_W               = 400      # activity log, before any text scaling
_TOP_H                 = 64
_QWIDGETSIZE_MAX       = (1 << 24) - 1    # Qt's "no maximum set" sentinel
_BOTTOM_H              = 100
_PAGE_MARGIN           = 24
_GAP                   = 16


# ═══════════════════════════════════════════════════════════════════════
# MASTER STYLESHEET — APPLIED ONCE
# ═══════════════════════════════════════════════════════════════════════

SIVASAFE_STYLESHEET = f"""
/* ── Global ── */
QMainWindow, QWidget#centralWidget {{
    background: {C.BG};
}}/* ── Glass Panel ── */
QWidget[class="glass-panel"] {{
    background: rgba(255,255,255,0.25);
    border: 1px solid rgba(255,255,255,0.5);
    border-radius: 24px;
}}/* ── Glass Button ── */
QPushButton[class="glass-button"] {{
    background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
        stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
    border: 1px solid rgba(255,255,255,0.7);
    border-radius: 16px;
    padding: 14px 28px;
    color: {C.ON_SURFACE_VAR};
    font-weight: bold;
    font-size: 24px;
}}QPushButton[class="glass-button"]:hover {{
    background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
        stop:0 rgba(255,255,255,230), stop:1 rgba(255,255,255,140));
}}/* ── Scrollbar ── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    border: none;
    margin: 4px 0;
}}QScrollBar::handle:vertical {{
    background: rgba(139,94,60,0.4);
    border-radius: 4px;
    min-height: 30px;
}}QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}QScrollArea {{
    border: none;
    background: transparent;
}}"""


# ═══════════════════════════════════════════════════════════════════════
# FILE HELPERS
# ═══════════════════════════════════════════════════════════════════════

_FILE_ICONS = {
    "image": ("🖼", "#00d4ff"), "video": ("🎬", "#ff6b00"),
    "audio": ("🎵", "#cc44ff"), "pdf": ("📄", "#ff4444"),
    "word": ("📝", "#4488ff"), "excel": ("📊", "#44bb44"),
    "code": ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx": ("📊", "#ff6622"), "text": ("📃", "#aaaaaa"),
    "data": ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg", "ico"], "image"),
    **dict.fromkeys(["mp4", "avi", "mov", "mkv", "wmv", "flv", "webm", "m4v"], "video"),
    **dict.fromkeys(["mp3", "wav", "ogg", "m4a", "aac", "flac", "wma", "opus"], "audio"),
    **dict.fromkeys(["pdf"], "pdf"),
    **dict.fromkeys(["doc", "docx"], "word"),
    **dict.fromkeys(["xls", "xlsx", "ods"], "excel"),
    **dict.fromkeys(["ppt", "pptx"], "pptx"),
    **dict.fromkeys(["py", "js", "ts", "jsx", "tsx", "html", "css", "java", "c", "cpp",
                     "cs", "go", "rs", "rb", "php", "swift", "kt", "sh", "sql", "lua"], "code"),
    **dict.fromkeys(["zip", "rar", "tar", "gz", "7z", "bz2", "xz"], "archive"),
    **dict.fromkeys(["txt", "md", "rst", "log"], "text"),
    **dict.fromkeys(["csv", "tsv", "json", "xml"], "data"),
}

_ACCEPTED_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "bmp", "webp",
    "mp4", "mov", "avi", "mkv",
    "mp3", "wav", "m4a", "ogg",
    "pdf", "doc", "docx", "txt", "xlsx", "pptx",
}

# The Safety Check page takes the same list plus the formats a suspicious thing
# actually arrives in: a saved email, a saved web page, a phone screenshot.
# Kept separate rather than widened, because the activity-log drop zone hands
# its file to the file_processor tool, which has no branch for these.
_SAFETY_EXTENSIONS = _ACCEPTED_EXTENSIONS | {
    "eml", "html", "htm", "tiff", "tif", "heic", "csv", "json", "md",
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024**2:
        return f"{size/1024:.1f} KB"
    elif size < 1024**3:
        return f"{size/1024**2:.1f} MB"
    else:
        return f"{size/1024**3:.1f} GB"


# ═══════════════════════════════════════════════════════════════════════
# HELPER: GLASS SHADOW
# ═══════════════════════════════════════════════════════════════════════

def _glass_shadow(widget: QWidget, blur: int = 40, dy: int = 12,
                  color: QColor | None = None):
    """Attach a QGraphicsDropShadowEffect simulating glassmorphism shadow."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(color or QColor(31, 38, 135, 20))
    widget.setGraphicsEffect(eff)


def _overlay_dims(container: QWidget, w: int, h: int) -> tuple[int, int]:
    """
    Overlays are centred by hand in resizeEvent rather than by a layout, so
    their box has to grow with the text inside it. Never larger than the
    container, or the panel loses its edges.
    """
    f = text_scale.factor()
    return (min(int(w * f), container.width()),
            min(int(h * f), container.height()))


def _place_overlay(ov: QWidget, container: QWidget, w: int, h: int) -> None:
    """
    Centre a floating overlay in *container*, sized to hold its own content.

    `w` and `h` are the design size and act as a floor: an overlay never comes
    out smaller than it was drawn. What they are not is a ceiling, because a
    layout handed less room than its minimum does not compress — it lays its
    children out anyway and they overlap and hang off the edge. Asking the
    overlay what it needs is the only thing that reliably keeps the words apart
    at every text size, in a way a constant per overlay cannot.

    The container is still the last word. An overlay that cannot fit is
    expected to scroll its own body rather than grow off the screen.
    """
    ow, oh = _overlay_dims(container, w, h)
    need = ov.sizeHint()
    ow = max(ow, need.width())
    oh = max(oh, need.height())
    ow = min(ow, container.width())
    oh = min(oh, container.height())
    ov.setGeometry((container.width() - ow) // 2,
                   (container.height() - oh) // 2,
                   ow, oh)


def _make_font(family: str | None = None, size: int = 24,
               bold: bool = False) -> QFont:
    f = QFont(family or _FONT_BODY, size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    return f


# ═══════════════════════════════════════════════════════════════════════
# ORB WIDGET — CENTRE CANVAS PAINTABLE
# ═══════════════════════════════════════════════════════════════════════

class OrbWidget(QWidget):
    """Glassmorphic orb with 4 animated rings, crosshairs, and state-driven speeds."""

    # How much of the height it is allowed to fill, and the hard ceiling.
    _HEIGHT_SHARE = 0.80
    _MAX_PX = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Low on purpose. The orb is Expanding, so with room to spare it still
        # fills whatever the page gives it and nothing looks any different at
        # the sizes it was drawn for — the floor only comes into play under
        # pressure. What it used to do was refuse to go below 400px in either
        # direction, which made the orb the most rigid thing on the page: as
        # the text grew, everything around it had to give way to a decoration.
        # It is the one element here with nothing to read in it, so it should
        # be the first to yield, not the last. The painting already handles
        # being small — the rings are drawn from min(w, h) and the wordmark is
        # capped against the ring radius.
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # State
        self.state    = "INITIALISING"
        self.muted    = False
        self.speaking = False

        # Ring rotation angles (degrees)
        self._ring1 = 0.0   # CW 60s
        self._ring2 = 0.0   # CCW 35s
        self._ring3 = 0.0   # CW 80s
        self._ring4 = 0.0   # CW 20s

        # Speeds (degrees per frame at 16ms)
        # 360 / (60 * 62.5) = ~0.096 deg/frame for 60s
        self._speed1 = 360.0 / (60.0 * 62.5)
        self._speed2 = -360.0 / (35.0 * 62.5)
        self._speed3 = 360.0 / (80.0 * 62.5)
        self._speed4 = 360.0 / (20.0 * 62.5)

        # Target speeds for smooth transitions
        self._tgt_speed1 = self._speed1
        self._tgt_speed2 = self._speed2
        self._tgt_speed3 = self._speed3
        self._tgt_speed4 = self._speed4

        # Core pulse
        self._pulse_phase = 0.0
        self._pulse_speed = 2.0 * math.pi / (4.0 * 62.5)  # 4000ms
        self._tgt_pulse_speed = self._pulse_speed

        self._tick = 0
        self._blink = True
        self._blink_tick = 0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def set_orb_state(self, state: str):
        """Update orb state and smoothly transition ring speeds."""
        self.state = state
        self.speaking = (state == "SPEAKING")

        fps = 62.5
        if state == "LISTENING":
            self._tgt_speed1 = 360.0 / (30.0 * fps)
            self._tgt_speed2 = -360.0 / (35.0 * fps)
            self._tgt_speed3 = 360.0 / (80.0 * fps)
            self._tgt_speed4 = 360.0 / (10.0 * fps)
            self._tgt_pulse_speed = 2.0 * math.pi / (2.0 * fps)
        elif state == "THINKING":
            self._tgt_speed1 = 360.0 / (60.0 * fps)
            self._tgt_speed2 = -360.0 / (15.0 * fps)
            self._tgt_speed3 = 360.0 / (40.0 * fps)
            self._tgt_speed4 = 360.0 / (20.0 * fps)
            self._tgt_pulse_speed = 2.0 * math.pi / (0.8 * fps)
        elif state == "SPEAKING":
            self._tgt_speed1 = 360.0 / (20.0 * fps)
            self._tgt_speed2 = -360.0 / (35.0 * fps)
            self._tgt_speed3 = 360.0 / (80.0 * fps)
            self._tgt_speed4 = 360.0 / (12.0 * fps)
            self._tgt_pulse_speed = 2.0 * math.pi / (0.9 * fps)
        elif state == "ERROR":
            self._tgt_speed1 = 360.0 / (120.0 * fps)
            self._tgt_speed2 = -360.0 / (120.0 * fps)
            self._tgt_speed3 = 360.0 / (120.0 * fps)
            self._tgt_speed4 = 360.0 / (120.0 * fps)
            self._tgt_pulse_speed = 2.0 * math.pi / (0.6 * fps)
        else:  # IDLE / AWAITING_INPUT / etc.
            self._tgt_speed1 = 360.0 / (60.0 * fps)
            self._tgt_speed2 = -360.0 / (35.0 * fps)
            self._tgt_speed3 = 360.0 / (80.0 * fps)
            self._tgt_speed4 = 360.0 / (20.0 * fps)
            self._tgt_pulse_speed = 2.0 * math.pi / (4.0 * fps)

    def _step(self):
        self._tick += 1
        # Smooth speed transitions
        lerp = 0.05
        self._speed1 += (self._tgt_speed1 - self._speed1) * lerp
        self._speed2 += (self._tgt_speed2 - self._speed2) * lerp
        self._speed3 += (self._tgt_speed3 - self._speed3) * lerp
        self._speed4 += (self._tgt_speed4 - self._speed4) * lerp
        self._pulse_speed += (self._tgt_pulse_speed - self._pulse_speed) * lerp

        # Advance rotations
        self._ring1 = (self._ring1 + self._speed1) % 360
        self._ring2 = (self._ring2 + self._speed2) % 360
        self._ring3 = (self._ring3 + self._speed3) % 360
        self._ring4 = (self._ring4 + self._speed4) % 360
        self._pulse_phase += self._pulse_speed

        # Blink for state label dot
        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0

        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        # Compact on purpose. The orb used to take the smaller of its two
        # dimensions outright, so on a tall page it grew until something else
        # stopped it — a decoration setting the size of the page around it.
        # Backing off to four fifths, and capping it, keeps it clearly the
        # centrepiece without it becoming the largest thing on screen, and
        # leaves the quick actions beneath it room to breathe.
        orb_size = min(W, H * self._HEIGHT_SHARE, self._MAX_PX)
        r_outer = orb_size / 2

        # Background radial gradients (canvas-wide)
        p.fillRect(self.rect(), qcol(C.BG))
        # Green tint top-left
        rg1 = QRadialGradient(QPointF(W * 0.1, H * 0.2), max(W, H) * 0.5)
        rg1.setColorAt(0, QColor(55, 103, 87, 15))
        rg1.setColorAt(1, QColor(55, 103, 87, 0))
        p.fillRect(self.rect(), QBrush(rg1))
        # Brown tint bottom-right
        rg2 = QRadialGradient(QPointF(W * 0.9, H * 0.8), max(W, H) * 0.5)
        rg2.setColorAt(0, QColor(139, 94, 60, 13))
        rg2.setColorAt(1, QColor(139, 94, 60, 0))
        p.fillRect(self.rect(), QBrush(rg2))
        # Gold tint centre
        rg3 = QRadialGradient(QPointF(cx, cy), max(W, H) * 0.7)
        rg3.setColorAt(0, QColor(212, 175, 55, 8))
        rg3.setColorAt(1, QColor(212, 175, 55, 0))
        p.fillRect(self.rect(), QBrush(rg3))

        # ── Orb background radial ──
        orb_bg = QRadialGradient(QPointF(cx, cy), r_outer)
        orb_bg.setColorAt(0, QColor(139, 94, 60, 38))
        orb_bg.setColorAt(0.6, QColor(139, 94, 60, 13))
        orb_bg.setColorAt(1.0, QColor(139, 94, 60, 0))
        p.setBrush(QBrush(orb_bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2))

        # ── Crosshairs (static) ──
        cross_col = QColor(139, 94, 60, 51)  # 20% opacity
        p.setPen(QPen(cross_col, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2))
        p.drawLine(QPointF(cx, cy - r_outer), QPointF(cx, cy + r_outer))
        p.drawLine(QPointF(cx - r_outer, cy), QPointF(cx + r_outer, cy))

        # Corner diamonds at N/S/E/W
        dm = 5
        for dx, dy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            px, py = cx + dx * r_outer, cy + dy * r_outer
            diamond = QPainterPath()
            diamond.moveTo(px, py - dm)
            diamond.lineTo(px + dm, py)
            diamond.lineTo(px, py + dm)
            diamond.lineTo(px - dm, py)
            diamond.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(139, 94, 60, 102)))
            p.drawPath(diamond)

        # ── Ring 1: Outer dashed (inset 4px) ──
        r1 = r_outer - 4
        pen1 = QPen(QColor(139, 94, 60, 102), 4, Qt.PenStyle.DashLine)  # 40%
        pen1.setDashPattern([6, 4])
        p.setPen(pen1)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        p.translate(cx, cy)
        p.rotate(self._ring1)
        p.drawEllipse(QRectF(-r1, -r1, r1 * 2, r1 * 2))
        p.restore()

        # ── Ring 2: Broken ring (inset 10px, top+left transparent) ──
        r2 = r_outer - 20
        p.save()
        p.translate(cx, cy)
        p.rotate(self._ring2)
        p.setPen(QPen(QColor(139, 94, 60, 128), 8))  # 50%
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect2 = QRectF(-r2, -r2, r2 * 2, r2 * 2)
        # Draw two arcs (bottom-right and top-right quadrant equivalent)
        p.drawArc(rect2, int(-45 * 16), int(135 * 16))
        p.drawArc(rect2, int(135 * 16), int(135 * 16))
        p.restore()

        # ── Ring 3: Conic spoke pattern (inset 40px) ──
        r3 = r_outer - 50
        p.save()
        p.translate(cx, cy)
        p.rotate(self._ring3)
        spoke_pen = QPen(QColor(139, 94, 60, 102), 3)  # 40%
        p.setPen(spoke_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Draw 24 spokes as short arcs
        for i in range(24):
            angle = i * 15
            p.drawArc(QRectF(-r3, -r3, r3 * 2, r3 * 2),
                       int(angle * 16), int(2 * 16))
        # Thin circle border
        p.setPen(QPen(QColor(139, 94, 60, 51), 1))
        p.drawEllipse(QRectF(-r3, -r3, r3 * 2, r3 * 2))
        p.restore()

        # ── Ring 4: Inner broken ring (inset 70px) ──
        r4 = r_outer - 80
        p.save()
        p.translate(cx, cy)
        p.rotate(self._ring4)
        p.setPen(QPen(QColor(139, 94, 60, 153), 4))  # 60%
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect4 = QRectF(-r4, -r4, r4 * 2, r4 * 2)
        # Bottom+right transparent = draw top+left arcs
        p.drawArc(rect4, int(45 * 16), int(135 * 16))
        p.drawArc(rect4, int(225 * 16), int(90 * 16))
        p.restore()

        # ── Core Glow ──
        core_r = r_outer * 0.50
        pulse_val = (math.sin(self._pulse_phase) + 1) / 2  # 0→1
        scale = 1.0 + 0.05 * pulse_val
        opacity = 0.8 + 0.2 * pulse_val
        actual_r = core_r * scale

        # Glow shadow
        glow_col = QColor(139, 94, 60, int(102 * opacity))
        for i in range(5):
            gr = actual_r + (5 - i) * 16
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(139, 94, 60, int(10 * (5 - i) * opacity))))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))

        # Core fill
        core_grad = QRadialGradient(QPointF(cx, cy), actual_r)
        core_grad.setColorAt(0, QColor(139, 94, 60, int(115 * opacity)))
        core_grad.setColorAt(0.7, QColor(139, 94, 60, int(38 * opacity)))
        core_grad.setColorAt(1.0, QColor(139, 94, 60, 0))
        p.setBrush(QBrush(core_grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - actual_r, cy - actual_r,
                              actual_r * 2, actual_r * 2))

        # Core borders
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(139, 94, 60, 128), 2))
        p.drawEllipse(QRectF(cx - actual_r, cy - actual_r,
                              actual_r * 2, actual_r * 2))
        inner_r = actual_r - 4
        p.setPen(QPen(QColor(139, 94, 60, 77), 1))
        p.drawEllipse(QRectF(cx - inner_r, cy - inner_r,
                              inner_r * 2, inner_r * 2))

        # ── "S.I.V.A" text ──
        # Painted by hand rather than laid out, so it has to ask for the scale
        # itself. The box grows with the letters, capped to the widget so the
        # wordmark can never spill out of the ring.
        p.setPen(QPen(QColor(139, 94, 60, int(255 * opacity)), 1))
        # Capped against the ring radius as well as the scale factor: the orb
        # shrinks when the surrounding page grows, and an uncapped wordmark
        # would spill straight over the rings and the state pill below.
        siva_pt = min(48 * text_scale.factor(), actual_r * 0.34)
        siva_font = QFont(_FONT_BODY, 48, QFont.Weight.Bold)
        siva_font.setPointSizeF(max(8.0, siva_pt))
        siva_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 120)
        p.setFont(siva_font)
        tw = min(siva_pt * 8.4, self.width())
        th = siva_pt * 1.6
        p.drawText(QRectF(cx - tw / 2, cy - th / 2, tw, th),
                   Qt.AlignmentFlag.AlignCenter, "S.I.V.A")

        p.end()


# ═══════════════════════════════════════════════════════════════════════
# CHAT BUBBLE AREA — ACTIVITY LOG
# ═══════════════════════════════════════════════════════════════════════

class _WrappingRow(QWidget):
    """
    A plain container that keeps its wrapping labels from being cut off.

    For rows built out of bare nested layouts, which have no widget of their
    own to notice that the text inside them needs a second line.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        pol = self.sizePolicy()
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        lay = self.layout()
        return lay.heightForWidth(w) if lay is not None else super().heightForWidth(w)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if fit_wrapped_labels(self):
            self.updateGeometry()


class _ChatBubble(QFrame):
    """A full-width chat card showing a message."""

    # The message inside wraps, so how tall this card needs to be depends on
    # how wide it is. A plain QFrame does not advertise that, so the column
    # holding it never asks — it hands the card the height of an unwrapped
    # line and the message is cut off part-way down.
    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        lay = self.layout()
        return lay.heightForWidth(w) if lay is not None else super().heightForWidth(w)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if fit_wrapped_labels(self):
            self.updateGeometry()

    def __init__(self, text: str, is_user: bool, timestamp: str = "", parent=None):
        super().__init__(parent)
        self._text = text
        self._is_user = is_user
        self._ts = timestamp
        pol = self.sizePolicy()
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

        # Determine styling based on sender
        bg_color = "rgba(139,94,60,230)" if is_user else "rgba(255,255,255,200)"
        self.setStyleSheet(f"background: {bg_color}; border-radius: 16px; border: 1px solid rgba(0,0,0,0.05);")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(12)

        sender = "You" if is_user else "SIVA"
        
        hdr = QLabel(f"{sender} • {timestamp}" if timestamp else sender)
        hdr.setFont(_make_font(size=16, bold=True))
        hdr_color = "rgba(255, 255, 255, 200)" if is_user else C.ON_SURFACE_VAR
        hdr.setStyleSheet(f"color: {hdr_color}; background: transparent; border: none;")
        lay.addWidget(hdr)

        txt = QLabel(text)
        txt.setFont(_make_font(size=24))
        txt_color = "white" if is_user else C.ON_SURFACE
        txt.setStyleSheet(f"color: {txt_color}; background: transparent; border: none;")
        txt.setWordWrap(True)
        txt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(txt)


class _SystemMessage(QWidget):
    """A small centred system message (SYS:, ERR:, FILE:)."""

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        lay = self.layout()
        return lay.heightForWidth(w) if lay is not None else super().heightForWidth(w)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if fit_wrapped_labels(self):
            self.updateGeometry()

    def __init__(self, text: str, color: str = C.SECONDARY, parent=None):
        super().__init__(parent)
        pol = self.sizePolicy()
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        lbl = QLabel(text)
        lbl.setFont(_make_font(_FONT_HUD, 16))
        lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lay.addStretch()
        lay.addWidget(lbl)
        lay.addStretch()


class ChatBubbleArea(AnimatedScrollArea):
    """Scrollable chat bubble area with animated item reveal and gradient overlays."""

    _sig = pyqtSignal(object)

    # How many bubbles stay in the layout. Older ones scroll out of reach and
    # are on the Chat History page in full.
    _MAX_LIVE = 50

    def __init__(self, parent=None):
        super().__init__(parent, show_gradients=True,
                         gradient_color=(235, 231, 223),  # Match warm BG
                         gradient_height=50)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch()
        self.setWidget(container)

        self._sig.connect(self._add_message)
        self._count = 0
        
        self._rendering_history = True
        self._messages_data = self._load_history()
        # Only the tail is built. _add_message trims the layout back to
        # _MAX_LIVE anyway, so rendering the whole history meant constructing
        # 500 bubbles to keep 50 — and because the trim's deleteLater() cannot
        # run until the event loop starts, all 450 discarded ones sat parented
        # to the container, unlaid-out, until the first turn of the loop.
        # `_messages_data` still holds everything; it is what gets saved, and
        # the Chat History page reads the file itself.
        for msg in self._messages_data[-self._MAX_LIVE:]:
            self.skip_animation_for_next_add()
            self._add_message(msg, save=False)
        self._rendering_history = False
        # Mark all history items as revealed so they don't re-animate
        self.mark_all_revealed()

    def _chat_file_path(self):
        d = Path.home() / ".siva"
        d.mkdir(parents=True, exist_ok=True)
        return d / "chat_history.json"

    def _load_history(self):
        try:
            p = self._chat_file_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                # Migrate legacy strings
                migrated = []
                for item in data[-500:]:
                    if isinstance(item, str):
                        migrated.append({
                            "date": "Legacy Sessions",
                            "day": "Previous",
                            "time": "",
                            "sender": "Unknown",
                            "text": item,
                            "raw": item
                        })
                    else:
                        migrated.append(item)
                return migrated
        except Exception:
            pass
        return []

    def _save_history(self):
        try:
            p = self._chat_file_path()
            from core.safe_store import write_json
            write_json(p, self._messages_data[-500:], indent=2)
        except Exception:
            pass
            
    def clear_history(self):
        self._messages_data = []
        self._save_history()
        # Clear UI
        while self._count > 0:
            item = self._layout.itemAt(0)
            if item and item.widget():
                item.widget().deleteLater()
                self._layout.removeItem(item)
                self._count -= 1
            else:
                break

    def append_log(self, text: str):
        """Thread-safe log append — preserves write_log() API."""
        self._sig.emit(text)

    def _add_message(self, data_or_text, save: bool = True):
        from datetime import datetime
        if isinstance(data_or_text, str):
            tl = data_or_text.strip()
            if not tl:
                return
            sender = "SYS"
            msg = tl
            if tl.startswith("You:"):
                sender = "You"
                msg = tl[4:].strip()
            elif tl.startswith("SIVA:"):
                sender = "SIVA"
                msg = tl[5:].strip()
            elif tl.startswith("SYS:"):
                sender = "SYS"
                msg = tl[4:].strip()
            
            now = datetime.now()
            msg_obj = {
                "date": now.strftime("%d %b %Y"),  # e.g. 21 Jul 2026
                "day": now.strftime("%A"),         # e.g. Tuesday
                "time": now.strftime("%I:%M %p"),
                "sender": sender,
                "text": msg,
                "raw": tl
            }
        else:
            msg_obj = data_or_text
            tl = msg_obj.get("raw", "")
            msg = msg_obj.get("text", "")
            sender = msg_obj.get("sender", "SYS")

        if not tl:
            return

        if save and not self._rendering_history:
            self._messages_data.append(msg_obj)
            self._save_history()

        ts = msg_obj.get("time", "")

        if sender == "You":
            w = _ChatBubble(msg, is_user=True)
        elif tl.startswith("SIVA:"):
            msg = tl[5:].strip()
            w = _ChatBubble(msg, is_user=False, timestamp=ts)
        elif tl.startswith("SYS:") or tl.startswith("["):
            w = _SystemMessage(tl, C.SECONDARY)
        elif tl.startswith("ERR"):
            w = _SystemMessage(tl, C.ERROR)
        elif tl.startswith("FILE:"):
            w = _SystemMessage(tl, C.PRIMARY)
        else:
            w = _SystemMessage(tl, C.ON_SURFACE_VAR)

        # Insert before the stretch
        self._layout.insertWidget(self._layout.count() - 1, w)
        self._count += 1

        # Keep last _MAX_LIVE messages
        while self._count > self._MAX_LIVE:
            item = self._layout.itemAt(0)
            if item and item.widget():
                item.widget().deleteLater()
                self._layout.removeItem(item)
                self._count -= 1
            else:
                break

        # Auto-scroll to bottom (skip animation for auto-appended items)
        if not self._rendering_history:
            self.skip_animation_for_next_add()
            QTimer.singleShot(50, self._scroll_bottom)

    def _scroll_bottom(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


# ═══════════════════════════════════════════════════════════════════════
# FILE DROP ZONE
# ═══════════════════════════════════════════════════════════════════════

class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    _DESIGN_MIN_H = 80
    _DESIGN_MAX_H = 110

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # The layout centres its children, which means each one is given its
        # preferred width rather than the panel's — so the caption can wrap
        # where you would not expect it to, and needs its height measured like
        # anything else that wraps.
        if fit_wrapped_labels(self):
            self.updateGeometry()

    def __init__(self, parent=None, accept: set[str] | None = None):
        super().__init__(parent)
        # Per-instance, not the module global. The activity-log zone feeds the
        # file_processor tool, which has no idea what to do with an .eml — so
        # the Safety Check page widens its own list without widening that one.
        self._accept = accept or _ACCEPTED_EXTENSIONS
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # _apply_density owns this widget's height, so the generic scaler must
        # keep off it — otherwise both would write it and each would read the
        # other's value back as a new design height.
        self.setProperty("_ts_ignore_geom", True)
        self.setMinimumHeight(self._DESIGN_MIN_H)
        self.setMaximumHeight(self._DESIGN_MAX_H)
        self._current_file: str | None = None
        self._hovering = False
        self._drag_over = False
        self._error_msg = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_lbl = QLabel("⬆")
        self._icon_lbl.setFont(_make_font(size=24))
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet(f"""
            QLabel {{
                background: rgba(139,94,60,0.1);
                color: {C.SECONDARY};
                border-radius: 24px;
                min-width: 36px; max-width: 36px;
                min-height: 36px; max-height: 36px;
            }}""")
        lay.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        self._title_lbl = QLabel("Drop file to attach")
        self._title_lbl.setFont(_make_font(size=12, bold=True))
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title_lbl)

        self._sub_lbl = QLabel("IMAGES • VIDEO • AUDIO • DOCS")
        self._sub_lbl.setFont(_make_font(_FONT_HUD, 9))
        self._sub_lbl.setStyleSheet("color: rgba(139,94,60,0.7); background: transparent;")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._sub_lbl)

        self._error_lbl = QLabel("")
        self._error_lbl.setFont(_make_font(size=11))
        self._error_lbl.setStyleSheet(f"color: {C.ERROR}; background: transparent;")
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl.hide()
        lay.addWidget(self._error_lbl)

        self._apply_density()
        text_scale.instance().changed.connect(lambda _l: self._apply_density())

        self._update_style()

    def _apply_density(self):
        """
        Thin the drop zone out as the text grows.

        It sits at the bottom of the activity log and is the least important
        thing in that column — a convenience for attaching a file, not
        something anyone reads. Left to grow with everything else it began
        taking a third of the panel away from the conversation above it. The
        list of accepted formats is the first thing to go: it is reference
        text nobody needs in front of them, and the zone accepts the same
        files whether or not it is on screen.
        """
        f = text_scale.factor()
        self._sub_lbl.setVisible(f <= 1.0)
        # The box grows at the chrome rate, not with the text. Its own labels
        # are still fully scaled and legible; what stops growing is the empty
        # room around them, which is all this panel is made of.
        chrome = 1.0 + (f - 1.0) * 0.3
        self.setMinimumHeight(int(round(self._DESIGN_MIN_H * chrome)))
        self.setMaximumHeight(int(round(self._DESIGN_MAX_H * chrome)))

    def _update_style(self):
        if self._drag_over:
            bg = "rgba(255,255,255,0.50)"
            border_col = "rgba(139,94,60,0.50)"
        elif self._hovering:
            bg = "rgba(255,255,255,0.40)"
            border_col = "rgba(139,94,60,0.40)"
        elif self._current_file:
            bg = "rgba(55,103,87,0.08)"
            border_col = "rgba(55,103,87,0.40)"
        else:
            bg = "rgba(255,255,255,0.30)"
            border_col = "rgba(139,94,60,0.30)"

        self.setStyleSheet(f"""
            FileDropZone {{
                background: {bg};
                border: 2px dashed {border_col};
                border-radius: 16px;
                padding: 16px;
            }}""")

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._title_lbl.setText("Drop file to attach")
        self._sub_lbl.setText("IMAGES • VIDEO • AUDIO • DOCS")
        self._sub_lbl.show()
        self._error_lbl.hide()
        self._update_style()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True
            self._update_style()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self._update_style()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._try_set_file(path)
        self._update_style()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True
        self._update_style()

    def leaveEvent(self, e):
        self._hovering = False
        self._update_style()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for SIVA", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp);;"
            "Documents (*.pdf *.docx *.txt *.pptx);;"
            "Data (*.xlsx *.json *.xml *.csv);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a);;"
            "Video (*.mp4 *.avi *.mov *.mkv)",
        )
        if path:
            self._try_set_file(path)

    def _try_set_file(self, path: str):
        ext = Path(path).suffix.lower().lstrip(".")
        if ext not in self._accept:
            self._error_lbl.setText(f"Unsupported file type: .{ext}")
            self._error_lbl.show()
            QTimer.singleShot(3000, self._error_lbl.hide)
            return
        self._error_lbl.hide()
        self._current_file = path
        p = Path(path)
        self._title_lbl.setText(f"📎 {p.name}")
        self._sub_lbl.setText(f"{_file_category(p).upper()} · {_fmt_size(p.stat().st_size)}")
        self._update_style()
        self.file_selected.emit(path)


# ═══════════════════════════════════════════════════════════════════════
# WIFI DISCONNECTED OVERLAY
# ═══════════════════════════════════════════════════════════════════════

class _WifiSignalWidget(QWidget):
    """Animated WiFi signal icon with pulsing rings and a red slash."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(180, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

        # Pulse animation
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)  # ~33fps
        self._phase = 0.0

    def _tick(self):
        self._phase += 0.04
        self._pulse = (math.sin(self._phase) + 1.0) / 2.0  # 0..1
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy = self.width() / 2, self.height() / 2 + 20

        # Draw concentric WiFi arcs (3 rings)
        for i, (radius, thickness) in enumerate([(70, 8), (50, 7), (30, 6)]):
            alpha = int(80 + 100 * self._pulse) if i < 2 else int(120 + 80 * self._pulse)
            pen = QPen(QColor(139, 94, 60, alpha), thickness)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            p.drawArc(rect, 45 * 16, 90 * 16)  # upper arc

        # Centre dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(139, 94, 60, int(160 + 60 * self._pulse))))
        p.drawEllipse(QPointF(cx, cy), 8, 8)

        # Red diagonal slash
        slash_pen = QPen(QColor(186, 26, 26, int(180 + 60 * self._pulse)), 5)
        slash_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(slash_pen)
        p.drawLine(int(cx - 40), int(cy - 50), int(cx + 40), int(cy + 30))

        p.end()


class WiFiDisconnectedOverlay(QWidget):
    """Full-screen overlay shown when SIVA loses connectivity."""

    _OW, _OH = 540, 480

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            WiFiDisconnectedOverlay {{
                background: rgba(235,231,223,0.97);
                border: 1px solid rgba(255,255,255,0.6);
                border-radius: 28px;
            }}""")
        _glass_shadow(self, blur=60, dy=20)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 36)
        layout.setSpacing(0)

        # ── Close (X) button — top right ──
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = AnimatedButton("✕")
        close_btn.setFont(_make_font(size=16, bold=True))
        close_btn.setFixedSize(40, 40)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(186,26,26,0.08);
                color: {C.ERROR};
                border: none;
                border-radius: 20px;
            }}
            QPushButton:hover {{ background: rgba(186,26,26,0.18); }}
        """)
        close_btn.clicked.connect(self._dismiss)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        # ── Animated WiFi icon ──
        wifi_icon = _WifiSignalWidget()
        layout.addWidget(wifi_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(8)

        # ── Title ──
        title = QLabel("Connection Lost")
        title.setFont(_make_font(size=28, bold=True))
        title.setStyleSheet(f"color: {C.ERROR}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)

        # ── Subtitle ──
        sub = QLabel(
            "WiFi has been disconnected. AI services\n"
            "are currently unavailable. SIVA will reconnect\n"
            "automatically when a network is available."
        )
        sub.setFont(_make_font(size=14))
        sub.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(28)

        # ── Reconnect WiFi button ──
        reconnect_btn = AnimatedButton("  📶   Reconnect WiFi")
        reconnect_btn.setFont(_make_font(size=16, bold=True))
        reconnect_btn.setFixedHeight(56)
        reconnect_btn.setMinimumWidth(280)
        reconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reconnect_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY};
                color: {C.WHITE};
                border: none;
                border-radius: 28px;
                padding: 0 24px;
                font-size: 16px;
            }}
            QPushButton:hover {{ background: #2d5648; }}
        """)
        reconnect_btn.clicked.connect(self._reconnect_wifi)
        layout.addWidget(reconnect_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)

        # ── Status label ──
        self._status = QLabel("")
        self._status.setFont(_make_font(size=12, bold=True))
        self._status.setStyleSheet(f"color: {C.PRIMARY}; background: transparent;")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)
        layout.addStretch()

    def _dismiss(self):
        self.hide()
        self.closed.emit()

    def _reconnect_wifi(self):
        """Open Windows WiFi quick-settings panel so the user sees nearby networks."""
        self._status.setText("Opening WiFi settings…")
        try:
            import subprocess
            # Opens the Windows WiFi quick-settings flyout directly
            # ms-availablenetworks: launches the "Manage Wi-Fi connections" flyout
            subprocess.Popen(
                ['explorer.exe', 'ms-availablenetworks:'],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception:
            try:
                # Fallback: open the full WiFi settings page
                import subprocess
                subprocess.Popen(
                    ['explorer.exe', 'ms-settings:network-wifi'],
                    creationflags=0x08000000,
                )
            except Exception:
                self._status.setText("Could not open WiFi settings.")


# ═══════════════════════════════════════════════════════════════════════
# SETUP OVERLAY — RESTYLED FOR GLASS THEME
# ═══════════════════════════════════════════════════════════════════════

class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(235,231,223,0.97);
                border: 1px solid rgba(255,255,255,0.6);
                border-radius: 24px;
            }}""")
        _glass_shadow(self, blur=60, dy=20)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(12)

        title = QLabel("⚙  INITIALISATION REQUIRED")
        title.setFont(_make_font(size=28, bold=True))
        title.setStyleSheet(f"color: {C.PRIMARY}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("Configure S.I.V.A.S.A.F.E before first boot.")
        sub.setFont(_make_font(size=24))
        sub.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sub)
        layout.addSpacing(12)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: rgba(139,94,60,0.3);")
        layout.addWidget(sep)

        key_lbl = QLabel("GEMINI API KEY")
        key_lbl.setFont(_make_font(_FONT_HUD, 20, bold=True))
        key_lbl.setStyleSheet(f"color: {C.SECONDARY}; background: transparent;")
        layout.addWidget(key_lbl)

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(_make_font(_FONT_HUD, 24))
        self._key_input.setFixedHeight(64)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.6);
                color: {C.ON_SURFACE};
                border: 2px solid rgba(139,94,60,0.3);
                border-radius: 12px;
                padding: 8px 16px;
                font-size: 24px;
            }}QLineEdit:focus {{ border: 2px solid {C.PRIMARY}; }}""")
        layout.addWidget(self._key_input)
        layout.addSpacing(16)

        os_lbl = QLabel("OPERATING SYSTEM")
        os_lbl.setFont(_make_font(_FONT_HUD, 20, bold=True))
        os_lbl.setStyleSheet(f"color: {C.SECONDARY}; background: transparent;")
        layout.addWidget(os_lbl)

        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        det_lbl = QLabel(f"Auto-detected: {det_name}")
        det_lbl.setFont(_make_font(size=22))
        det_lbl.setStyleSheet(f"color: {C.PRIMARY}; background: transparent;")
        layout.addWidget(det_lbl)

        os_row = QHBoxLayout()
        os_row.setSpacing(12)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")]:
            btn = AnimatedButton(label)
            btn.setFont(_make_font(size=24, bold=True))
            btn.setFixedHeight(64)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(16)

        init_btn = AnimatedButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(_make_font(size=26, bold=True))
        init_btn.setFixedHeight(72)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY};
                color: white;
                border: none;
                border-radius: 16px;
                font-size: 26px;
            }}""")
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        for k, btn in self._os_btns.items():
            if k == key:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRIMARY};
                        color: white;
                        border: none;
                        border-radius: 12px;
                        font-size: 24px;
                    }}""")
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgba(255,255,255,0.5);
                        color: {C.ON_SURFACE_VAR};
                        border: 1px solid rgba(255,255,255,0.6);
                        border-radius: 12px;
                        font-size: 24px;
                    }}""")

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 2px solid {C.ERROR}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


# ═══════════════════════════════════════════════════════════════════════
# REMOTE KEY OVERLAY — RESTYLED
# ═══════════════════════════════════════════════════════════════════════

class Toast(QWidget):
    """
    A short message across the top of the window: did that work or not.

    It exists because the app's only way of reporting a result was a small
    line of text under a button, which is how a mail failure went unnoticed
    for a whole session. This appears over everything, states the outcome in
    words, and goes away on its own.

    Colour is never the only signal — success and failure carry a different
    symbol as well as a different edge, because a good number of older users
    cannot reliably tell the two colours apart.
    """

    _LIFETIME_MS = 11_000        # long enough to read twice, unhurried
    _FADE_MS = 260

    def __init__(self, text: str, ok: bool = True, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        edge = C.PRIMARY if ok else C.ERROR
        self.setStyleSheet(f"""
            Toast {{
                background: #FDFBF7;
                border: 2px solid {edge};
                border-left: 10px solid {edge};
                border-radius: 16px;
            }}""")
        _glass_shadow(self, blur=44, dy=10, color=QColor(0, 0, 0, 70))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(12)

        mark = QLabel("✓" if ok else "⚠")
        mark.setFont(_make_font(size=20, bold=True))
        mark.setStyleSheet(f"color: {edge}; background: transparent; border: none;")
        lay.addWidget(mark, alignment=Qt.AlignmentFlag.AlignTop)

        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setFont(_make_font(size=14, bold=True))
        self._label.setStyleSheet(
            f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        lay.addWidget(self._label, stretch=1)

        close = AnimatedButton("✕")
        close.setFixedSize(34, 34)
        close.setFont(_make_font(size=14, bold=True))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ON_SURFACE_VAR};
                border: none; border-radius: 17px;
            }}
            QPushButton:hover {{ background: rgba(0,0,0,0.08); color: {C.ON_SURFACE}; }}""")
        close.clicked.connect(self.dismiss)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)

        self._op = QGraphicsOpacityEffect(self)
        self._op.setOpacity(0.0)
        self.setGraphicsEffect(self._op)
        self._anim = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def preferred_size(self, avail_w: int) -> tuple[int, int]:
        """As wide as it needs, never wider than the window can hold."""
        w = min(text_scale.px(560), max(text_scale.px(300), avail_w - text_scale.px(80)))
        lay = self.layout()
        h = lay.heightForWidth(w) if lay.hasHeightForWidth() else self.sizeHint().height()
        return w, max(text_scale.px(58), h)

    def appear(self):
        self.show()
        self.raise_()
        self._fade(0.0, 1.0)
        self._timer.start(self._LIFETIME_MS)

    def dismiss(self):
        self._timer.stop()
        self._fade(self._op.opacity(), 0.0, then=self._gone)

    def _gone(self):
        self.hide()
        self.deleteLater()

    def _fade(self, frm: float, to: float, then=None):
        anim = QPropertyAnimation(self._op, b"opacity", self)
        anim.setDuration(self._FADE_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(frm)
        anim.setEndValue(to)
        if then:
            anim.finished.connect(then)
        anim.start()
        self._anim = anim          # keep it alive


class RecoveryEmailOverlay(QWidget):
    """
    "SIVA needs an email address" — shown over the dashboard when none is set.

    Every user must have one, because it is the only way back in once the
    password is genuinely forgotten. So this reappears on every launch until
    an address exists.

    Built to be impossible to miss, which for this user matters more than
    politeness: it dims the whole dashboard behind it and sits in a card with
    a heavy coloured border, rather than floating as one more pale panel among
    the pale panels it is drawn on top of. The dimmed backdrop also swallows
    clicks, so a stray press cannot send the card behind a page.

    It still offers "Remind me next time" rather than blocking the dashboard.
    Trapping someone behind a screen that needs the internet — on the very
    launch where they might not have any — would create exactly the lock-out
    this feature exists to prevent.
    """

    closed = pyqtSignal()
    # Mail goes out on a worker thread; this brings the answer back to the GUI
    # thread, which is the only one allowed to touch these widgets.
    _mail_done = pyqtSignal(bool, str)

    # Design-size card. The real one is this scaled by the text setting, then
    # clamped to whatever the window can actually spare.
    _CARD_W = 560
    _CARD_MIN_H = 380

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mail_done.connect(self._on_mail_done)

        # The overlay itself is the dimmer: it covers the whole central widget
        # so nothing behind it competes for attention. Painted by hand rather
        # than by a stylesheet — a type selector has to match the class name
        # Qt sees, and when it silently does not, the result is an overlay with
        # no dimming at all and no error to say why.

        self._card = QWidget(self)
        self._card.setObjectName("recoveryCard")
        self._card.setStyleSheet(f"""
            QWidget#recoveryCard {{
                background: #FDFBF7;
                border: 3px solid {C.PRIMARY};
                border-radius: 26px;
            }}""")
        _glass_shadow(self._card, blur=80, dy=24, color=QColor(0, 0, 0, 90))

        outer = QVBoxLayout(self._card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header band: the loudest part of the card ────────────
        header = QWidget()
        header.setObjectName("recoveryHeader")
        header.setStyleSheet(f"""
            QWidget#recoveryHeader {{
                background: {C.PRIMARY};
                border-top-left-radius: 23px;
                border-top-right-radius: 23px;
            }}""")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 18, 24, 18)
        hl.setSpacing(14)

        icon = QLabel("\u2709")
        icon.setFont(_make_font(size=26, bold=True))
        icon.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        hl.addWidget(icon)

        htitle = QLabel("One thing first")
        htitle.setFont(_make_font(size=22, bold=True))
        htitle.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        hl.addWidget(htitle)
        hl.addStretch()
        self._header = header
        outer.addWidget(header)

        # ── Body, inside a scroller ──────────────────────────────
        #
        # At the largest text size this content is taller than a small window,
        # and clipping the buttons off the bottom would leave no way to finish.
        body_scroll = AnimatedScrollArea(self._card, show_gradients=False)
        body_scroll.setWidgetResizable(True)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_scroll.setStyleSheet("background: transparent; border: none;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        msg = QLabel(
            "SIVA needs an email address for you.\n\n"
            "If you ever forget your password, this is how you get back in. "
            "Without it, there is no way to unlock SIVA.")
        msg.setWordWrap(True)
        msg.setFont(_make_font(size=15))
        msg.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        lay.addWidget(msg)

        field_qss = f"""
            QLineEdit {{
                background: #FFFFFF; color: {C.ON_SURFACE};
                border: 2px solid rgba(133,116,72,0.45); border-radius: 12px;
                padding: 6px 14px;
            }}
            QLineEdit:focus {{ border: 3px solid {C.PRIMARY}; }}"""

        self._email = QLineEdit()
        self._email.setPlaceholderText("your email address")
        self._email.setMinimumHeight(54)
        self._email.setFont(_make_font(size=16))
        self._email.setStyleSheet(field_qss)
        self._email.returnPressed.connect(self._on_primary)
        lay.addWidget(self._email)

        # Filled, not outlined: on a page of pale glass panels a ghost button
        # is the easiest thing on screen to overlook.
        self._primary = AnimatedButton("Save this email")
        self._primary.setMinimumHeight(56)
        self._primary.setFont(_make_font(size=16, bold=True))
        self._primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY}; color: #FFFFFF;
                border: 2px solid {C.PRIMARY}; border-radius: 14px;
            }}
            QPushButton:hover {{ background: {C.SECONDARY}; border-color: {C.SECONDARY}; }}
            QPushButton:disabled {{ background: rgba(55,103,87,0.35); border-color: transparent; }}""")
        self._primary.clicked.connect(self._on_primary)
        lay.addWidget(self._primary)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setFont(_make_font(size=14))
        self._status.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        lay.addWidget(self._status)

        later = AnimatedButton("Remind me next time")
        later.setMinimumHeight(44)
        later.setFont(_make_font(size=14))
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        later.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ON_SURFACE_VAR};
                border: none; text-decoration: underline;
            }}
            QPushButton:hover {{ color: {C.PRIMARY}; }}""")
        later.clicked.connect(self._on_later)
        lay.addWidget(later)
        lay.addStretch()

        self._body = body
        body_scroll.setWidget(body)
        outer.addWidget(body_scroll, stretch=1)


        # The card is placed by hand, so it has to be told when the text size
        # moves — nothing else will resize it.
        try:
            text_scale.instance().changed.connect(self._on_scale_changed)
        except Exception:
            pass

    # ── geometry ─────────────────────────────────────────────────

    def _on_scale_changed(self, _level=None):
        # One turn later, so the new fonts are in before we measure them.
        QTimer.singleShot(0, self._place_card)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 16, 12, 170))
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_card()

    def _place_card(self):
        """
        Centre the card and grow it to whatever the text now needs.

        The height has to come from the *content*, not from the card: the body
        sits in a scroll area, whose own sizeHint is a small default and says
        nothing about what is inside it. Asking the card left it at its
        minimum with the Save button scrolled out of sight — for this user, a
        button they have to find by scrolling may as well not be there.

        heightForWidth, not sizeHint, because the message wraps: how tall it
        is depends entirely on how wide it is allowed to be.
        """
        try:
            pad = text_scale.px(48)
            want_w = text_scale.px(self._CARD_W)
            w = max(240, min(want_w, self.width() - pad))

            inner_w = max(100, w - 6)          # minus the 3px border each side
            lay = self._body.layout()
            if lay is not None and lay.hasHeightForWidth():
                body_h = lay.heightForWidth(inner_w)
            else:
                body_h = self._body.sizeHint().height()
            needed = self._header.sizeHint().height() + body_h + 6

            h = max(text_scale.px(self._CARD_MIN_H), needed)
            h = min(h, self.height() - pad)     # never taller than the window
            self._card.setGeometry((self.width() - w) // 2,
                                   (self.height() - h) // 2, w, h)
        except RuntimeError:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._place_card()
        self._email.setFocus()

    def mousePressEvent(self, event):
        # Swallowed on purpose: a click on the dimmed area must not fall
        # through to the dashboard, and must not dismiss the card either —
        # "Remind me next time" is the only way to put it away.
        event.accept()

    # ── behaviour ────────────────────────────────────────────────

    def _say(self, text, bad=False):
        self._status.setText(text)
        self._status.setStyleSheet(
            f"color: {C.ERROR if bad else C.PRIMARY}; background: transparent; border: none;")
        self._place_card()

    def _on_primary(self):
        """
        Save the address, then send a message to it. Nothing to type back.

        This is the very first screen a new user meets, so it asks for one
        thing and takes one press. A six-digit code fetched from an inbox is
        exactly the sort of step that ends with an older user giving up before
        they have a recovery address at all — which is the outcome this whole
        feature exists to prevent.
        """
        from core import mailer, recovery
        addr = self._email.text().strip()
        if not mailer.looks_like_email(addr):
            self._say("That does not look like an email address.", True)
            return

        if not recovery.set_recovery_email(addr):
            self._say("Could not save that. Please try again.", True)
            return

        if not mailer.is_configured():
            # Nothing to send with yet. Saving the address unverified still
            # beats having none at all.
            self._say("✓ Saved. Codes will come to this address.")
            QTimer.singleShot(1200, self._finish)
            return

        self._say("✓ Saved. Sending you a message so you can check it arrives…")
        self._primary.setEnabled(False)

        import threading

        def work():
            ok, m = mailer.send_test_email(addr)
            self._mail_done.emit(ok, m)

        threading.Thread(target=work, daemon=True).start()

    def _on_mail_done(self, ok, msg):
        self._primary.setEnabled(True)
        if ok:
            self._say("✓ Saved. Look for a message from SIVA in your email.")
        else:
            # The address is already saved; this only reports that the check
            # message could not go out.
            self._say(f"Saved, but the test message did not send: {msg}", True)
        QTimer.singleShot(2600, self._finish)

    def _on_later(self):
        self._finish()

    def _finish(self):
        self.hide()
        self.closed.emit()
        self.deleteLater()


class PermissionOverlay(QWidget):
    """
    "Let SIVA use the camera?" — Allow or Cancel, asked once.

    The card the whole permission system is made of. It is deliberately built
    like RecoveryEmailOverlay above rather than as a QDialog: the app's own
    overlays dim the window behind them, follow the text-size setting and are
    reachable by the layout audit, and a QDialog would be none of those things.

    Nothing here decides anything. It shows the wording that
    `core/permissions.py` holds for one capability and emits what the person
    pressed; storing the answer is the caller's job, because only Allow is
    stored and that rule belongs in one place.
    """

    answered = pyqtSignal(bool)

    _CARD_W = 560
    _CARD_MIN_H = 340

    def __init__(self, perm, parent=None):
        super().__init__(parent)
        self._perm = perm
        self._done = False

        # Focusable so Escape reaches keyPressEvent — without this the key goes
        # to whatever had focus on the page underneath, which is still live.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._card = QWidget(self)
        self._card.setObjectName("permCard")
        self._card.setStyleSheet(f"""
            QWidget#permCard {{
                background: #FDFBF7;
                border: 3px solid {C.PRIMARY};
                border-radius: 26px;
            }}""")
        _glass_shadow(self._card, blur=80, dy=24, color=QColor(0, 0, 0, 90))

        outer = QVBoxLayout(self._card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header band ──────────────────────────────────────────
        header = QWidget()
        header.setObjectName("permHeader")
        header.setStyleSheet(f"""
            QWidget#permHeader {{
                background: {C.PRIMARY};
                border-top-left-radius: 23px;
                border-top-right-radius: 23px;
            }}""")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 18, 24, 18)
        hl.setSpacing(14)

        icon = QLabel(perm.icon)
        icon.setFont(_make_font(size=26, bold=True))
        icon.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        hl.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)

        htitle = QLabel(perm.title)
        htitle.setWordWrap(True)
        htitle.setFont(_make_font(size=21, bold=True))
        htitle.setStyleSheet("color: #FFFFFF; background: transparent; border: none;")
        hl.addWidget(htitle, stretch=1)
        self._header = header
        outer.addWidget(header)

        # ── Body, inside a scroller ──────────────────────────────
        #
        # Same reason as the recovery card: at the largest text size this is
        # taller than a small window, and clipping Allow off the bottom would
        # leave the person with no way to say yes.
        #
        # Plain, for the reason set out on TourOverlay's scroller below: this
        # card is created and destroyed on every ask, and the animated version
        # leaves a timer running over widgets that may already be gone.
        body_scroll = QScrollArea(self._card)
        body_scroll.setWidgetResizable(True)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        body_scroll.viewport().setStyleSheet("background: transparent;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        msg = QLabel(perm.body)
        msg.setWordWrap(True)
        msg.setFont(_make_font(size=16))
        msg.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        lay.addWidget(msg)

        note = QLabel(permissions.FOOTNOTE)
        note.setWordWrap(True)
        note.setFont(_make_font(size=13))
        note.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        lay.addWidget(note)

        # Allow is filled and Cancel is outlined, so the two are told apart by
        # shape as well as by colour — the rule the Toast already follows.
        self._allow = AnimatedButton("Allow")
        self._allow.setMinimumHeight(56)
        self._allow.setFont(_make_font(size=17, bold=True))
        self._allow.setCursor(Qt.CursorShape.PointingHandCursor)
        self._allow.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY}; color: #FFFFFF;
                border: 2px solid {C.PRIMARY}; border-radius: 14px;
            }}
            QPushButton:hover {{ background: {C.SECONDARY}; border-color: {C.SECONDARY}; }}""")
        self._allow.clicked.connect(lambda: self._finish(True))
        lay.addWidget(self._allow)

        self._cancel = AnimatedButton("Cancel")
        self._cancel.setMinimumHeight(50)
        self._cancel.setFont(_make_font(size=15, bold=True))
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.70); color: {C.ON_SURFACE_VAR};
                border: 2px solid rgba(133,116,72,0.35); border-radius: 14px;
            }}
            QPushButton:hover {{ color: {C.ON_SURFACE}; border-color: {C.SECONDARY}; }}""")
        self._cancel.clicked.connect(lambda: self._finish(False))
        lay.addWidget(self._cancel)
        lay.addStretch()

        self._body = body
        body_scroll.setWidget(body)
        outer.addWidget(body_scroll, stretch=1)

        try:
            text_scale.instance().changed.connect(self._on_scale_changed)
        except Exception:
            pass

    # ── geometry ─────────────────────────────────────────────────

    def _on_scale_changed(self, _level=None):
        QTimer.singleShot(0, self._place_card)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 16, 12, 170))
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_card()

    def _place_card(self):
        """Centre the card and grow it to whatever the wording now needs.

        heightForWidth on the body's layout, not the card's sizeHint: the body
        is in a scroll area whose hint is a small constant and says nothing
        about what is inside it, and the message wraps — how tall it is depends
        entirely on how wide it is allowed to be.
        """
        try:
            pad = text_scale.px(48)
            want_w = text_scale.px(self._CARD_W)
            w = max(240, min(want_w, self.width() - pad))

            inner_w = max(100, w - 6)          # minus the 3px border each side
            lay = self._body.layout()
            if lay is not None and lay.hasHeightForWidth():
                body_h = lay.heightForWidth(inner_w)
            else:
                body_h = self._body.sizeHint().height()
            needed = self._header.sizeHint().height() + body_h + 6

            h = max(text_scale.px(self._CARD_MIN_H), needed)
            h = min(h, self.height() - pad)
            self._card.setGeometry((self.width() - w) // 2,
                                   (self.height() - h) // 2, w, h)
        except RuntimeError:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._place_card()
        self._allow.setFocus()

    def mousePressEvent(self, event):
        # Swallowed, not treated as Cancel. A press on the dimmed area must not
        # reach the page underneath, and it must not answer the question either:
        # a permission that got silently refused because someone clicked past
        # the card is indistinguishable, from the outside, from a broken button.
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._finish(False)
            return
        super().keyPressEvent(event)

    # ── behaviour ────────────────────────────────────────────────

    def _finish(self, allowed: bool):
        # Guarded: Escape during the closing animation, or a double press on
        # Allow, would otherwise emit twice and run the pending action twice.
        if self._done:
            return
        self._done = True
        self.hide()
        self.answered.emit(bool(allowed))
        self.deleteLater()


class TourStep:
    """One stop on the first-run walkthrough."""

    __slots__ = ("page", "anchor", "title", "body", "ask")

    def __init__(self, page: str | None, anchor: str | None,
                 title: str, body: str, ask: str | None = None):
        self.page = page
        self.anchor = anchor
        self.title = title
        self.body = body
        self.ask = ask


# The walkthrough, in order.
#
# Twelve stops, which is about as much as anyone will sit through in one go —
# and the first four are the three system permissions, so the person has met the
# Allow/Cancel card before any feature is explained rather than being ambushed
# by one later. `anchor=None` means there is nothing to point at and the card is
# simply centred; that is right for the two that are about the system as a whole
# rather than about a control on screen.
TOUR_STEPS: tuple[TourStep, ...] = (
    TourStep(
        "home", None,
        "Hello — this is SIVA",
        "SIVA is here to help you use this computer by asking, instead of "
        "hunting for things.\n\n"
        "This tour takes about a minute. You can stop it at any point, and you "
        "can watch it again later from Settings.",
    ),
    TourStep(
        "home", None,
        "First, a few permissions",
        "SIVA asks before it does anything on your behalf. Each thing is asked "
        "for once, in plain words, and then never again.\n\n"
        "Here is the first one. Press Allow if you are happy for SIVA to use "
        "the camera, or Cancel if you would rather not — you can change your "
        "mind later either way.",
        ask=permissions.CAMERA,
    ),
    TourStep(
        "home", "talk",
        "Speaking to SIVA",
        "This is the microphone permission. SIVA only listens while you are "
        "holding Talk, or while Listen is switched on.\n\n"
        "Nothing is recorded when both are off.",
        ask=permissions.MICROPHONE,
    ),
    TourStep(
        "home", "quick",
        "Letting SIVA press the buttons",
        "The last of the three. This is what lets SIVA open a program, or type "
        "something out for you, when you ask it to.\n\n"
        "Everything else — WhatsApp, your email, your files — asks separately, "
        "the first time you use it.",
        ask=permissions.AUTOMATION,
    ),
    TourStep(
        "home", "orb",
        "SIVA's face",
        "The circle in the middle shows what SIVA is doing, and the words "
        "underneath say it plainly: sleeping, listening, thinking or speaking.\n\n"
        "If it says Sleeping, it is not hearing you. Press Talk or Listen to "
        "wake it.",
    ),
    TourStep(
        "home", "chat",
        "Talk to it, or type to it",
        "Hold the Talk button and say what you want, then let go. Or switch "
        "Listen on and just speak.\n\n"
        "If you would rather not talk out loud, type into this box instead. "
        "SIVA answers the same way either way, and everything that happens is "
        "written down in the list above it.",
    ),
    TourStep(
        "home", "quick",
        "The buttons on this page",
        "These do the most common jobs in one press — the weather, your email, "
        "a photo of the screen, WhatsApp.\n\n"
        "Here is what happens the first time you press one of them.",
        ask=permissions.WHATSAPP,
    ),
    TourStep(
        "home", None,
        "Things you can ask for",
        "Say it the way you would say it to a person:\n\n"
        "  •  \"What is the weather tomorrow?\"\n"
        "  •  \"Remind me to take my tablets at eight.\"\n"
        "  •  \"Message Aravind on WhatsApp and say I am running late.\"\n"
        "  •  \"Read out what is on my screen.\"\n"
        "  •  \"Is this message a scam?\"\n"
        "  •  \"Open my photos.\"  ·  \"Turn the volume down.\"\n\n"
        "You do not have to get the words exactly right.",
    ),
    TourStep(
        "home", None,
        "Things SIVA cannot do",
        "It is worth knowing these before you need them.\n\n"
        "  •  It cannot make a telephone call for you.\n"
        "  •  It cannot sign in to your bank, and it will never move money "
        "or buy anything.\n"
        "  •  Its safety check is an opinion, not proof. If it says a "
        "message looks fine, that is still not a promise.\n"
        "  •  It needs the internet to think. With no connection it can "
        "only do the things stored on this computer.\n\n"
        "When SIVA is unsure, it says so rather than guessing.",
    ),
    TourStep(
        "reminders", "reminders_form",
        "Reminders",
        "Type what you want to be reminded of and the time, and press Set "
        "Reminder. The list beside it shows everything coming up.\n\n"
        "Reminders are handed to Windows, so they still arrive even if SIVA is "
        "closed. You can ask SIVA to set one for you instead of filling this "
        "in.",
    ),
    TourStep(
        "health", "medications",
        "Your medicines",
        "Add each medicine once, with the times you take it, and SIVA will "
        "remind you every day without being asked again.\n\n"
        "This page also shows what you have already taken today.",
    ),
    TourStep(
        "settings", "text_size",
        "Settings — and this tour again",
        "If the words are too small, drag this slider. Everything in SIVA grows "
        "with it.\n\n"
        "Further down this page is a list of everything SIVA is allowed to do, "
        "where you can turn any of it off. Right at the bottom, under Developer "
        "Settings, is the button that plays this tour again.\n\n"
        "That is everything. Press Finish and SIVA is ready.",
    ),
)


class TourOverlay(QWidget):
    """
    The first-run walkthrough: one control lit, everything else dimmed.

    A coach-mark tour rather than a page of instructions, because the target
    user is being asked to learn where things are, and a screenshot in a manual
    is not where they will be looking when they need it. Each step darkens the
    whole window except for the one real control it is talking about, and puts a
    card of plain words beside it.

    Two properties are worth stating because they are what keep this safe to add
    to a finished interface:

    · **It is placed by hand, outside every page layout.** A child of the central
      widget with its own geometry contributes no minimum width to anything, so
      nothing in here can widen a page or start it scrolling sideways — the one
      hard requirement this interface has.

    · **It explains; it does not press anything.** Navigating to a page is the
      only thing it does to the app. The permission cards it raises are real and
      the answers are really stored, but no feature is fired — a walkthrough
      that opened WhatsApp and called the vision model would be a walkthrough
      that depends on the network, the quota and a third-party app all behaving
      at the moment somebody is watching.
    """

    finished = pyqtSignal()

    _CARD_W = 460          # design width; scaled and then clamped to the window
    _GAP = 18              # between the lit control and the card
    _INFLATE = 8           # how far past the control the lit area reaches
    _RADIUS = 16.0

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self._i = 0
        self._hole: QRect | None = None
        self._done = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._card = QWidget(self)
        self._card.setObjectName("tourCard")
        self._card.setStyleSheet(f"""
            QWidget#tourCard {{
                background: #FDFBF7;
                border: 3px solid {C.PRIMARY};
                border-radius: 22px;
            }}""")
        _glass_shadow(self._card, blur=70, dy=20, color=QColor(0, 0, 0, 100))

        outer = QVBoxLayout(self._card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Body in a scroller, footer outside it: at the largest text size a step
        # can be taller than a 700px window, and Next scrolled off the bottom
        # would leave no way forward.
        #
        # A plain QScrollArea, not the AnimatedScrollArea the rest of the app
        # uses, and this is not a style choice. Its reveal animation installs a
        # graphics effect on each child and clears it again 260 ms later; this
        # card is rebuilt on every Next and destroyed the moment somebody
        # presses Finish, so that timer routinely lands on a widget that is
        # already gone — which is an access violation, not a Python exception,
        # and so not something core/crash_guard.py can save the app from. It
        # also sidesteps the fit_wrapped_labels ratchet, which the animated
        # version applies unguarded to its whole content tree. The animation was
        # buying nothing here anyway: the card is small and arrives all at once.
        body_scroll = QScrollArea(self._card)
        body_scroll.setWidgetResizable(True)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        body_scroll.viewport().setStyleSheet("background: transparent;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        blay = QVBoxLayout(body)
        blay.setContentsMargins(26, 22, 26, 18)
        blay.setSpacing(10)

        self._counter = QLabel("")
        self._counter.setFont(_make_font(_FONT_HUD, 12, bold=True))
        self._counter.setStyleSheet(
            f"color: {C.SECONDARY}; background: transparent; border: none; "
            "letter-spacing: 2px;")
        blay.addWidget(self._counter)

        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._title.setFont(_make_font(size=21, bold=True))
        self._title.setStyleSheet(
            f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        blay.addWidget(self._title)

        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setFont(_make_font(size=15))
        self._text.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        blay.addWidget(self._text)
        blay.addStretch()

        self._body = body
        body_scroll.setWidget(body)
        outer.addWidget(body_scroll, stretch=1)

        # ── Footer: always on screen ─────────────────────────────
        footer = QWidget()
        footer.setStyleSheet("background: transparent;")
        flay = QHBoxLayout(footer)
        flay.setContentsMargins(26, 0, 26, 20)
        flay.setSpacing(10)

        self._skip = AnimatedButton("Skip the tour")
        self._skip.setMinimumHeight(44)
        self._skip.setFont(_make_font(size=13))
        self._skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ON_SURFACE_VAR};
                border: none; text-decoration: underline;
            }}
            QPushButton:hover {{ color: {C.PRIMARY}; }}""")
        self._skip.clicked.connect(lambda: self._end())
        flay.addWidget(self._skip)
        flay.addStretch()

        self._back = AnimatedButton("Back")
        self._back.setMinimumHeight(48)
        self._back.setFont(_make_font(size=14, bold=True))
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.70); color: {C.ON_SURFACE_VAR};
                border: 2px solid rgba(133,116,72,0.35); border-radius: 12px;
                padding: 4px 18px;
            }}
            QPushButton:hover {{ color: {C.ON_SURFACE}; border-color: {C.SECONDARY}; }}
            QPushButton:disabled {{ color: rgba(93,64,55,0.35);
                                    border-color: rgba(133,116,72,0.15); }}""")
        self._back.clicked.connect(lambda: self._goto(self._i - 1))
        flay.addWidget(self._back)

        self._next = AnimatedButton("Next")
        self._next.setMinimumHeight(48)
        self._next.setFont(_make_font(size=15, bold=True))
        self._next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY}; color: #FFFFFF;
                border: 2px solid {C.PRIMARY}; border-radius: 12px;
                padding: 4px 26px;
            }}
            QPushButton:hover {{ background: {C.SECONDARY}; border-color: {C.SECONDARY}; }}""")
        self._next.clicked.connect(self._advance)
        flay.addWidget(self._next)

        self._footer = footer
        outer.addWidget(footer)

        try:
            text_scale.instance().changed.connect(self._on_scale_changed)
        except Exception:
            pass

    # ── stepping ─────────────────────────────────────────────────

    def begin(self):
        self.show()
        self.raise_()
        self.setFocus()
        self._goto(0)

    def _advance(self):
        if self._i >= len(TOUR_STEPS) - 1:
            self._end()
        else:
            self._goto(self._i + 1)

    def _goto(self, i: int):
        i = max(0, min(i, len(TOUR_STEPS) - 1))
        self._i = i
        step = TOUR_STEPS[i]

        if step.page:
            try:
                self._win._go_to_page(step.page)
            except Exception:
                pass

        self._counter.setText(f"STEP {i + 1} OF {len(TOUR_STEPS)}")
        self._title.setText(step.title)
        self._text.setText(step.body)
        self._back.setEnabled(i > 0)
        self._next.setText("Finish" if i == len(TOUR_STEPS) - 1 else "Next")

        # A page change is not laid out until the next turn of the event loop,
        # so measuring the control now would measure where it used to be.
        QTimer.singleShot(0, self._resync)
        if step.ask:
            QTimer.singleShot(0, lambda k=step.ask: self._ask(k))

    def _ask(self, key: str):
        """Raise the real permission card over the tour."""
        try:
            self._win.ask_permission(key)
        except Exception:
            pass

    def _end(self):
        if self._done:
            return
        self._done = True
        # Recorded even when it was skipped. Someone who has said "not now" once
        # should not be handed the same tour on every launch; the button in
        # Settings is how it comes back.
        permissions.set_walkthrough_done(True)
        self.hide()
        self.finished.emit()
        self.deleteLater()

    # ── geometry ─────────────────────────────────────────────────

    def _on_scale_changed(self, _level=None):
        QTimer.singleShot(0, self._resync)

    def _resync(self):
        try:
            self._compute_hole()
            self._place_card()
            self.update()
        except RuntimeError:
            pass

    def _compute_hole(self):
        """Where the lit area is, in this overlay's own coordinates.

        Anything that does not resolve to a visible, sized widget leaves the
        hole empty and the card centred, rather than raising. The sidebar folds
        on a narrow canvas and the activity log is hidden on four of the pages,
        so a missing control is a normal state of the interface, not a fault.
        """
        step = TOUR_STEPS[self._i]
        self._hole = None
        if not step.anchor:
            return

        try:
            target = self._win.tour_anchor(step.anchor)
        except Exception:
            target = None
        if target is None:
            return
        try:
            if not target.isVisible() or target.width() < 8 or target.height() < 8:
                return
            container = self.parentWidget()
            if container is None:
                return
            # Via the common ancestor, not mapToGlobal: the overlay is a sibling
            # of what it points at, so there is no direct mapping between them.
            tl = target.mapTo(container, QPoint(0, 0)) - self.pos()
        except RuntimeError:
            return

        pad = text_scale.px(self._INFLATE)
        r = QRect(tl, target.size()).adjusted(-pad, -pad, pad, pad)
        r = r.intersected(self.rect())
        if r.width() > 8 and r.height() > 8:
            self._hole = r

    def _place_card(self):
        """Size the card to its words, then put it where it does not cover them."""
        cw, ch = self.width(), self.height()
        if cw <= 0 or ch <= 0:
            return
        gap = text_scale.px(self._GAP)

        w = max(240, min(text_scale.px(self._CARD_W), cw - gap * 2))
        inner_w = max(100, w - 6)
        lay = self._body.layout()
        if lay is not None and lay.hasHeightForWidth():
            body_h = lay.heightForWidth(inner_w)
        else:
            body_h = self._body.sizeHint().height()
        h = body_h + self._footer.sizeHint().height() + 6
        h = max(text_scale.px(180), h)
        h = min(h, ch - gap * 2) if ch > gap * 2 else ch

        r = self._hole
        if r is None:
            x, y = (cw - w) // 2, (ch - h) // 2
        else:
            below, above = ch - r.bottom(), r.top()
            if below >= h + gap:
                y = r.bottom() + gap
            elif above >= h + gap:
                y = r.top() - gap - h
            elif below >= above:
                # Neither side has the whole card — the orb takes most of the
                # page on its own — so it goes to whichever side has more room
                # and the clamp below pushes it back on screen. It ends up
                # covering an edge of the lit area rather than its middle,
                # which centring it would do.
                y = ch - h
            else:
                y = 0
            x = r.center().x() - w // 2

        # The last word, always. Everything above is a preference; this is what
        # guarantees the card cannot hang off an edge at any text size.
        x = max(0, min(x, cw - w))
        y = max(0, min(y, ch - h))
        self._card.setGeometry(x, y, w, h)

    # ── painting and input ───────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        r = self._hole
        if r is not None:
            path.addRoundedRect(QRectF(r), self._RADIUS, self._RADIUS)
        p.fillPath(path, QColor(20, 16, 12, 165))

        if r is not None:
            p.setPen(QPen(qcol(C.PRIMARY), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r), self._RADIUS, self._RADIUS)
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resync()

    def mousePressEvent(self, event):
        # The dim is not decoration — it is there so there is exactly one thing
        # to look at. Letting a press through would mean the person can wander
        # off mid-explanation into a page the card is no longer describing.
        event.accept()

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key.Key_Escape:
            self._end()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._advance()
        elif k == Qt.Key.Key_Left:
            self._goto(self._i - 1)
        else:
            super().keyPressEvent(event)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for phone pairing + key fallback."""

    closed = pyqtSignal()
    _OW, _OH = 460, 520

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(235,231,223,0.97);
                border: 1px solid rgba(255,255,255,0.6);
                border-radius: 24px;
            }}""")
        _glass_shadow(self, blur=60, dy=20)

        self._expiry = time.time() + expiry_secs
        self._on_new_key = None
        self._auto_login_url = auto_login_url
        self._manual_url = manual_url or url

        # The card is a scroller, and the box it is placed in is sized from
        # what is inside it (see sizeHint below).
        #
        # It used to be a plain layout in a box fixed at _OW × _OH. The content
        # is taller than 520px even at the default text size, and a layout
        # given less room than its minimum does not shrink — it overflows. That
        # is what put the QR code on top of the line of text underneath it. The
        # same fixed width clipped the scan hint to its middle, because a label
        # that cannot wrap is as wide as its longest line and there were 470px
        # of it in 396px of card.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = AnimatedScrollArea(self, show_gradients=False)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        # _WrappingRow, not a plain QWidget: it answers heightForWidth and
        # measures its wrapped labels on every resize. Without that the card
        # reports the height of unwrapped text, the scroller believes it and
        # gives the labels less room than the words need, and the wrapping
        # above trades a sentence cut off at the sides for one cut off at the
        # bottom.
        self._body = _WrappingRow()
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll)

        lay = QVBoxLayout(self._body)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(8)

        title = QLabel("📱  REMOTE ACCESS")
        title.setFont(_make_font(size=28, bold=True))
        title.setStyleSheet(f"color: {C.PRIMARY}; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(139,94,60,0.3);")
        lay.addWidget(sep)

        # QR code
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 12px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)
        self._update_qr(auto_login_url)

        hint = QLabel("Scan with phone camera to connect instantly")
        hint.setFont(_make_font(size=22))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: rgba(139,94,60,0.3);")
        lay.addWidget(sep2)

        manual_lbl = QLabel("Or enter manually:")
        manual_lbl.setFont(_make_font(size=20))
        manual_lbl.setWordWrap(True)
        manual_lbl.setStyleSheet(f"color: {C.SECONDARY}; background: transparent;")
        lay.addWidget(manual_lbl)

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(_make_font(_FONT_HUD, 18))
        self._url_lbl.setWordWrap(True)
        self._url_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont(_FONT_HUD, 32, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.SECONDARY};
            background: rgba(255,255,255,0.5);
            border: 1px solid rgba(139,94,60,0.3);
            border-radius: 12px;
            padding: 12px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(_make_font(_FONT_HUD, 20))
        self._timer_lbl.setWordWrap(True)
        self._timer_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        new_btn = AnimatedButton("NEW KEY")
        new_btn.setFixedHeight(56)
        new_btn.setFont(_make_font(size=22, bold=True))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.5);
                color: {C.PRIMARY};
                border: 1px solid {C.PRIMARY_CTR};
                border-radius: 12px;
            }}""")
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = AnimatedButton("DISMISS")
        close_btn.setFixedHeight(56)
        close_btn.setFont(_make_font(size=22, bold=True))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C.ON_SURFACE_VAR};
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 12px;
            }}""")
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def sizeHint(self) -> QSize:
        """
        What the card needs, not what it will get.

        The scroller inside is happy at any size, so the default hint would be
        far too small to read the key through. Report the content's own size
        instead and let `_place_overlay` cap it to the window — which is the
        one case where the scroller earns its place.
        """
        need = self._body.sizeHint()
        frame = self._scroll.frameWidth() * 2
        w = max(need.width(), int(self._OW * text_scale.factor()))
        # Measured at the width it will actually get, so the answer already
        # counts the lines the hint text folds onto.
        lay = self._body.layout()
        h = need.height()
        if lay is not None and lay.hasHeightForWidth():
            h = max(h, lay.heightForWidth(w - frame))
        return QSize(w + frame, h + frame)

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(_make_font(size=18))
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(_make_font(size=16))

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.PRIMARY};
            background: rgba(55,103,87,0.1);
            border: 2px solid rgba(55,103,87,0.4);
            border-radius: 12px;
            padding: 12px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont(_FONT_HUD, 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            f"color: {C.PRIMARY}; background: rgba(55,103,87,0.1); border-radius: 12px;"
        )
        self._timer_lbl.setText("Phone connected — SIVA ready")
        self._timer_lbl.setStyleSheet(f"color: {C.PRIMARY}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.SECONDARY};
                    background: rgba(255,255,255,0.5);
                    border: 1px solid rgba(139,94,60,0.3);
                    border-radius: 12px;
                    padding: 12px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.ON_SURFACE_VAR}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


# ═══════════════════════════════════════════════════════════════════════
# HAMBURGER SLIDE-OUT DRAWER
# ═══════════════════════════════════════════════════════════════════════

class SidebarShell(QWidget):
    """
    The frame around the left navigation column, and the thing the hamburger
    animates.

    It carries the glass panel styling and the drop shadow; the column itself
    sits inside as a plain child, keeps its natural width, and is positioned by
    hand against the shell's right edge. So as the shell narrows, the column
    slides out to the left and is clipped by the shell.

    Why not simply let a layout do it: a layout would *squash* the column
    instead of moving it, compressing every button toward zero width, and each
    frame of that would make every word-wrapped label in the column recompute
    its height-for-width (see the notes on QLabel height caching elsewhere in
    this file). Positioning one child by hand costs nothing per frame.
    """

    def __init__(self, inner: QWidget):
        super().__init__()
        self.setObjectName("leftSidebar")
        self.setStyleSheet("""
            QWidget#leftSidebar {
                background: rgba(255,255,255,0.20);
                border: 1px solid rgba(255,255,255,0.5);
                border-radius: 24px;
            }
        """)
        _glass_shadow(self, blur=40, dy=12)

        # The width is animated and driven by hand, so the text scaler must not
        # own it — it would treat a mid-animation width as the new design size
        # and bake it in. The column inside is still scaled normally, and
        # _sync_column_width() re-reads it whenever the text size changes.
        self.setProperty("_ts_ignore_geom", True)

        self._inner = inner
        inner.setParent(self)
        inner.show()
        self.setFixedWidth(self.column_width())

    def column_width(self) -> int:
        """The column's own width — _LEFT_W, as scaled by the text engine."""
        w = self._inner.maximumWidth()
        return w if 0 < w < 16777215 else _LEFT_W

    def set_column_visible(self, on: bool):
        """
        Out of frame, the column is hidden — the shell is not.

        Hiding the shell instead would make the body layout drop the 16px gap
        beside it, which lands as a 16px jump on the last frame of an
        animation that is moving barely a pixel per frame by then. Keeping the
        shell (at zero width) keeps every gap constant, so the slide finishes
        where it looks like it is going to. The column still has to be hidden
        rather than merely clipped, or its buttons stay in the tab order and
        keyboard focus lands on a control nobody can see.
        """
        self._inner.setVisible(on)

    def set_shadow_enabled(self, on: bool):
        """
        A drop shadow makes Qt render the whole subtree offscreen every time it
        repaints. Harmless while still; during the slide it would mean
        re-rendering the entire nav column on every frame, so it is switched
        off for the duration. Disabled, never replaced — installing a new
        effect would delete this one, which is how a dangling effect wrapper
        took the app down once before.
        """
        eff = self.graphicsEffect()
        if eff is not None:
            eff.setEnabled(on)

    def sync_width(self):
        """
        Match the shell to the column it holds.

        The text engine owns the column's width and will have grown it by the
        time everything is polished, which is after this shell was built. Left
        alone the shell would keep its construction width and clip the column.
        Skipped while collapsed — zero is a width we chose.
        """
        if self.maximumWidth() <= 0:
            return
        cw = self.column_width()
        if self.maximumWidth() != cw:
            self.setFixedWidth(cw)
        self._place_inner()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_inner()

    def _place_inner(self):
        cw = self.column_width()
        # Anchored right, so a narrowing shell pushes it off to the left.
        self._inner.setGeometry(self.width() - cw, 0, cw, self.height())


class HamburgerDrawer(QWidget):
    """Slide-out navigation drawer triggered by the ☰ button."""

    page_selected = pyqtSignal(str)   # emits page key
    _DRAWER_W = 280

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedWidth(0)
        self.setStyleSheet("background: transparent;")
        self._expanded = False
        # Width is animated and re-set from the parent's resizeEvent, so the
        # scaler must not own this geometry. _drawer_w() applies the factor at
        # the point of use instead.
        self.setProperty("_ts_ignore_geom", True)

        # Semi-transparent backdrop
        self._backdrop = QWidget(parent)
        self._backdrop.setStyleSheet("background: rgba(0,0,0,0.35);")
        self._backdrop.hide()
        self._backdrop.mousePressEvent = lambda _: self.toggle()

        # Drawer panel
        self._panel = QWidget(self)
        self._panel.setProperty("_ts_ignore_geom", True)
        self._panel.setObjectName("drawerPanel")
        self._panel.setStyleSheet(f"""
            QWidget#drawerPanel {{
                background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                    stop:0 rgba(242,240,235,0.97), stop:1 rgba(235,231,223,0.97));
                border-right: 1px solid rgba(255,255,255,0.6);
            }}""")
        _glass_shadow(self._panel, blur=50, dy=0)

        panel_lay = QVBoxLayout(self._panel)
        panel_lay.setContentsMargins(20, 24, 20, 24)
        panel_lay.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        hdr_lbl = QLabel("≡  NAVIGATION")
        hdr_lbl.setFont(_make_font(_FONT_HUD, 13, bold=True))
        hdr_lbl.setStyleSheet(f"color: {C.SECONDARY}; background: transparent; letter-spacing: 3px;")
        hdr.addWidget(hdr_lbl)
        hdr.addStretch()
        close_btn = AnimatedButton("✕")
        close_btn.setFont(_make_font(size=16, bold=True))
        close_btn.setFixedSize(36, 36)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(186,26,26,0.08);
                color: {C.ERROR};
                border: none;
                border-radius: 18px;
            }}""")
        close_btn.clicked.connect(self.toggle)
        hdr.addWidget(close_btn)
        panel_lay.addLayout(hdr)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background: rgba(139,94,60,0.15); max-height: 1px; border: none;")
        panel_lay.addWidget(div)
        panel_lay.addSpacing(8)

        # Nav items
        self._nav_items = [
            ("home",          "🏠", "Homepage"),
            ("notifications", "🔔", "Notifications"),
            ("reminders",     "⏰", "Reminders"),
            ("tasklists",     "📋", "Task Lists"),
            ("notes",         "📝", "Notes"),
            ("safety",        "🛡", "Safety Check"),
        ]
        self._drawer_btns: dict[str, QPushButton] = {}
        for key, icon, label in self._nav_items:
            btn = AnimatedButton(f"  {icon}   {label}")
            btn.setFont(_make_font(size=14, bold=True))
            btn.setFixedHeight(50)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C.ON_SURFACE_VAR};
                    border: none;
                    border-radius: 14px;
                    text-align: left;
                    padding-left: 16px;
                    font-size: 14px;
                }}""")
            btn.clicked.connect(lambda _, k=key: self._on_nav(k))
            self._drawer_btns[key] = btn
            panel_lay.addWidget(btn)

        panel_lay.addStretch()

        # Version label at bottom
        ver = QLabel("S.I.V.A.S.A.F.E  •  MARK 1")
        ver.setFont(_make_font(_FONT_HUD, 10))
        ver.setStyleSheet(f"color: rgba(93,64,55,0.4); background: transparent;")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_lay.addWidget(ver)

        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _on_nav(self, key: str):
        self._highlight(key)
        self.page_selected.emit(key)
        # Auto close after selection
        QTimer.singleShot(180, self.toggle)

    def _highlight(self, active_key: str):
        for key, btn in self._drawer_btns.items():
            if key == active_key:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRIMARY};
                        color: white;
                        border: none;
                        border-radius: 14px;
                        text-align: left;
                        padding-left: 16px;
                        font-size: 14px;
                    }}""")
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        color: {C.ON_SURFACE_VAR};
                        border: none;
                        border-radius: 14px;
                        text-align: left;
                        padding-left: 16px;
                        font-size: 14px;
                    }}""")

    def _drawer_w(self) -> int:
        """Panel width, widened to fit the current text size."""
        return text_scale.px(self._DRAWER_W)

    def toggle(self):
        self._expanded = not self._expanded
        parent = self.parent()
        dw = self._drawer_w()
        if self._expanded:
            self._backdrop.setGeometry(0, 0, parent.width(), parent.height())
            self._backdrop.show()
            self._backdrop.raise_()
            self.raise_()
            self._anim.setStartValue(0)
            self._anim.setEndValue(dw)
            self.setFixedHeight(parent.height())
            self._panel.setFixedSize(dw, parent.height())
            self.show()
        else:
            self._backdrop.hide()
            self._anim.setStartValue(dw)
            self._anim.setEndValue(0)
        self._anim.start()
        if not self._expanded:
            QTimer.singleShot(260, self.hide)

    def update_geometry(self, pw: int, ph: int):
        """Call from parent's resizeEvent."""
        if self._expanded:
            self.setFixedHeight(ph)
            self._panel.setFixedSize(self._drawer_w(), ph)
            self._backdrop.setGeometry(0, 0, pw, ph)


# ═══════════════════════════════════════════════════════════════════════
# REMINDERS PAGE — FULL CENTRE STACK PAGE
# ═══════════════════════════════════════════════════════════════════════

# How often the reminders list looks to see whether a row has fallen due.
# Nothing is rebuilt on a tick that finds nothing to do, so this is only the
# resolution of the flip from "upcoming" to "already given", not a refresh rate.
_ROW_FLIP_CHECK_MS = 15_000


class AlarmModeButton(QPushButton):
    """
    The per-item choice between a notification and an alarm.

    Every row in the UPCOMING list carries one. Off — the default — means the
    reminder arrives as a Windows notification, which is easy to miss on
    purpose: it is the right weight for most things. On means it rings the
    alarm window instead, which puts itself on top of whatever is on screen
    and stays there until it is snoozed or dismissed.

    Pressing it only writes a flag. The scheduled task behind the reminder
    fires actions/reminder_fire.py, which reads that flag at the moment it
    runs, so the change applies to the very next firing with no trip through
    the Windows scheduler and nothing to wait for.

    It is labelled as well as iconed. An unlabelled bell would leave the
    person this app is for guessing which state means which.
    """

    _BASE_HEIGHT = 44

    # Raised when the record behind this row has gone — the list is showing
    # something the store no longer has, and needs rebuilding.
    went_stale = pyqtSignal()

    def __init__(self, item_id: str, kind: str, on: bool, parent=None,
                 muted: bool = False):
        super().__init__(parent)
        self.setObjectName("alarmModeBtn")
        self._item_id = item_id
        self._kind = kind
        self._on = bool(on)
        # Rows whose time has been and gone are washed out. A medication keeps
        # its button there — today's dose has gone, tomorrow's has not — but at
        # full strength the gold was the brightest thing in a section that is
        # deliberately the quietest, and pulled the eye straight to it.
        self._muted = bool(muted)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self._BASE_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._flip)
        self._apply_state()

    # The two labels are different lengths, and the padding lives in the
    # stylesheet where QPushButton's own sizeHint does not account for it —
    # so the row squeezed this button until "Notify" lost its last letters.
    # Measured from the *wider* of the two states, not the current one, so
    # the button also does not jump width when it is flipped.
    _H_PADDING = 14        # matches `padding: 0 14px` in both stylesheets below
    _LABELS = ("🔔  Alarm", "🔕  Notify")

    def _sync_min_width(self):
        fm = self.fontMetrics()
        widest = max(fm.horizontalAdvance(t) for t in self._LABELS)
        # padding both sides, 1px border both sides, and a little slack so a
        # font that measures narrow still has somewhere to go.
        self.setMinimumWidth(widest + 2 * self._H_PADDING + 2 + 8)

    def changeEvent(self, event):
        super().changeEvent(event)
        # Text size is a setting in this app, so the measurement has to follow
        # it rather than being taken once at build time.
        if event.type() == QEvent.Type.FontChange:
            self._sync_min_width()

    def _apply_state(self):
        self._sync_min_width()
        if self._on:
            self.setText("🔔  Alarm")
            self.setToolTip("Rings an alarm window until you snooze or "
                            "dismiss it.\nClick for a quiet notification "
                            "instead.")
            self.setAccessibleName("Alarm on. Click to switch to a notification.")
            fill, edge, ink = ((C.TERTIARY, "#B8952C", "#3E3100")
                               if not self._muted
                               else ("rgba(212, 175, 55, 0.34)",
                                     "rgba(184, 149, 44, 0.45)",
                                     "rgba(62, 49, 0, 0.62)"))
            style = f"""
                QPushButton#alarmModeBtn {{
                    background: {fill};
                    color: {ink};
                    border: 1px solid {edge};
                    border-radius: 12px;
                    padding: 0 14px;
                    font-size: 15px;
                    font-weight: bold;
                }}
                QPushButton#alarmModeBtn:hover   {{ background: #E0C158; }}
                QPushButton#alarmModeBtn:pressed {{ background: #C09E2C; }}
            """
        else:
            self.setText("🔕  Notify")
            self.setToolTip("Shows a notification when it is due.\n"
                            "Click to ring an alarm instead.")
            self.setAccessibleName("Notification only. Click to switch to an alarm.")
            style = f"""
                QPushButton#alarmModeBtn {{
                    background: rgba(255, 255, 255, {0.30 if self._muted else 0.55});
                    color: {'rgba(93, 64, 55, 0.60)' if self._muted else C.ON_SURFACE_VAR};
                    border: 1px solid rgba(139,94,60,{0.18 if self._muted else 0.30});
                    border-radius: 12px;
                    padding: 0 14px;
                    font-size: 15px;
                    font-weight: bold;
                }}
                QPushButton#alarmModeBtn:hover   {{ background: rgba(255,255,255,0.85); }}
                QPushButton#alarmModeBtn:pressed {{ background: rgba(139,94,60,0.12); }}
            """
        self.setStyleSheet(style)

    def _flip(self):
        wanted = not self._on
        try:
            if self._kind == "medication":
                from actions.medication_store import set_medication_alarm
                result = set_medication_alarm(self._item_id, wanted)
            else:
                from actions.reminder_store import set_reminder_alarm
                result = set_reminder_alarm(self._item_id, wanted)
        except Exception as e:
            print(f"[Reminder] Could not change the alarm setting: {e}")
            return

        if result is None:
            # Nothing in the store has this id any more. Editing a medication
            # re-saves it under a new one, so a row built before that edit is
            # pressing against a record that no longer exists. Rebuild the
            # list rather than leaving a button that refuses to switch on.
            print(f"[Reminder] {self._item_id} is no longer stored — "
                  f"refreshing the list.")
            self.went_stale.emit()
            return

        self._on = result
        # Restyle in place rather than rebuilding the list: the row the person
        # just pressed stays exactly where it is under their finger.
        self._apply_state()

        # A medication taken twice a day is one stored record shown as two
        # rows, and the alarm flag belongs to the record. Without this, the
        # row they did not press keeps showing the state it had before.
        try:
            for sibling in self.window().findChildren(AlarmModeButton):
                if (sibling is not self
                        and sibling._item_id == self._item_id
                        and sibling._kind == self._kind
                        and sibling._on != self._on):
                    sibling._on = self._on
                    sibling._apply_state()
        except RuntimeError:
            pass


class RemindersPage(QWidget):
    """Full reminders management page with Bento Grid, merging reminders and medications."""

    reminder_created = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 30, 40, 30)
        root.setSpacing(24)

        # ── Header ──
        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        
        icon_lbl = QLabel("🔔")
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setStyleSheet("background: rgba(139,94,60,0.15); color: #8b5e3c; border-radius: 24px; font-size: 22px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(icon_lbl)
        
        title = QLabel("REMINDERS")
        title.setWordWrap(True)
        title.setFont(_make_font(_FONT_HUD, 26, bold=True))
        title.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; letter-spacing: 4px;")
        hdr.addWidget(title)
        hdr.addStretch()

        # Until now this was the only list in the app you could not remove
        # anything from — tasks have a bin, medications have delete, chat
        # history has Clear All. `delete_reminder` has been in the store the
        # whole time and nothing ever called it. What removal there was
        # happened on its own, twenty hours after a reminder was due, which is
        # no help at all when the wrong one is on screen now.
        #
        # Deliberately not the ERROR red the Clear All on chat history uses:
        # red means *overdue* on this page since the contrast fix, and a red
        # button beside red text reads as part of the same warning.
        self._clear_btn = AnimatedButton("Clear Passed")
        self._clear_btn.setFont(_make_font(size=16, bold=True))
        self._clear_btn.setMinimumHeight(44)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setToolTip(
            "Remove every reminder whose time has already gone by.")
        self._clear_btn.setAccessibleName(
            "Clear reminders that have already passed")
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.55);
                color: {C.SECONDARY};
                border: 1px solid rgba(139,94,60,0.35);
                border-radius: 12px;
                padding: 8px 20px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.85); }}""")
        self._clear_btn.clicked.connect(self._clear_passed)
        hdr.addWidget(self._clear_btn)
        root.addLayout(hdr)

        # ── Body: Bento Grid Layout ──
        # Two cards side by side need width the centre column no longer has
        # once the text is large, so past the middle size they stack instead
        # and the page itself scrolls. QBoxLayout.setDirection() (applied in
        # _apply_responsive_layout) is what lets this flip live when the text
        # size changes, rather than being frozen at whatever size the page
        # happened to be built at.
        body_host = QWidget()
        body_host.setStyleSheet("background: transparent;")
        body = QHBoxLayout(body_host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(24)

        # LEFT COLUMN (New Reminder)
        left_col = QVBoxLayout()
        
        form_card = QFrame()
        form_card.setObjectName("remFormCard")
        form_card.setStyleSheet("""
            QFrame#remFormCard {
                background: rgba(255, 255, 255, 0.40);
                border: 1px solid rgba(255, 255, 255, 0.30);
                border-radius: 24px;
            }
        """)
        _glass_shadow(form_card, blur=30, dy=8)
        self._form_card = form_card     # the walkthrough spotlights this

        form_lay = QVBoxLayout(form_card)
        form_lay.setContentsMargins(24, 16, 24, 16)
        form_lay.setSpacing(12)
        
        form_title = QLabel("NEW REMINDER")
        form_title.setFont(_make_font(_FONT_HUD, 18, bold=True))
        form_title.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; letter-spacing: 2px;")
        form_lay.addWidget(form_title)
        
        # Message input
        msg_lbl = QLabel("Message")
        msg_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; font-size: 16px; font-weight: bold; background: transparent;")
        form_lay.addWidget(msg_lbl)
        
        self._msg_input = QLineEdit()
        self._msg_input.setPlaceholderText("What should I remind you about?")
        self._msg_input.setFixedHeight(48)
        self._msg_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.70);
                color: {C.ON_SURFACE};
                border: 1px solid rgba(139,94,60,0.20);
                border-radius: 12px;
                padding: 0 16px;
                font-size: 16px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRIMARY}; }}
        """)
        form_lay.addWidget(self._msg_input)
        
        # Date picker
        date_lbl = QLabel("Date")
        date_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; font-size: 16px; font-weight: bold; background: transparent;")
        form_lay.addWidget(date_lbl)
        
        self._calendar = QDateEdit()
        self._calendar.setCalendarPopup(True)
        self._calendar.setDate(QDate.currentDate())
        self._calendar.setFixedHeight(48)
        from pathlib import Path
        base = Path(__file__).resolve().parent
        down_arrow_svg = (base / "assets_arrow_down.svg").as_posix()
        up_arrow_svg = (base / "assets_arrow_up.svg").as_posix()

        self._calendar.setStyleSheet(f"""
            QDateEdit {{
                background: rgba(255, 255, 255, 0.70);
                color: {C.ON_SURFACE};
                border: 1px solid rgba(139,94,60,0.20);
                border-radius: 8px;
                padding: 0 16px;
                font-size: 18px;
                font-weight: bold;
            }}
            QDateEdit:focus {{ border: 1px solid {C.PRIMARY}; }}
            QDateEdit::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 32px;
                border-left: none;
            }}
            QDateEdit::down-arrow {{
                image: url("{down_arrow_svg}");
                width: 14px; height: 14px;
            }}
        """)
        
        # Apply the exact same nice styling to the popup calendar widget
        popup = self._calendar.calendarWidget()
        popup.setGridVisible(False)
        popup.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        # Make the popup window translucent to support rounded corners without black edges
        popup.window().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.window().setWindowFlags(popup.window().windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        
        bg_color = "#fffcd6"
        
        popup.setStyleSheet(f"""
            QCalendarWidget {{ background: {bg_color}; border-radius: 12px; border: 1px solid rgba(139,94,60,0.3); }}
            QCalendarWidget QToolButton {{ color: {C.ON_SURFACE}; font-weight: bold; font-size: 18px; background: transparent; border: none; padding: 4px; }}
            QCalendarWidget QToolButton:hover {{ background: rgba(139,94,60,0.1); border-radius: 8px; }}
            QCalendarWidget QMenu {{ background: {bg_color}; color: {C.ON_SURFACE}; border: 1px solid rgba(139,94,60,0.2); font-size: 16px; border-radius: 8px; }}
            QCalendarWidget QSpinBox {{ background: transparent; border: none; font-size: 18px; font-weight: bold; }}
            QCalendarWidget QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 24px; }}
            QCalendarWidget QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; }}
            QCalendarWidget QSpinBox::up-arrow {{ image: url("{up_arrow_svg}"); width: 14px; height: 14px; }}
            QCalendarWidget QSpinBox::down-arrow {{ image: url("{down_arrow_svg}"); width: 14px; height: 14px; }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {bg_color}; border-top-left-radius: 12px; border-top-right-radius: 12px; }}
            QCalendarWidget QTableView {{ background: {bg_color}; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }}
            QCalendarWidget QAbstractItemView {{
                color: {C.ON_SURFACE};
                background: {bg_color};
                selection-background-color: #d4af37;
                selection-color: #554300;
                font-size: 18px;
            }}
            QCalendarWidget QAbstractItemView::item {{ border-radius: 8px; margin: 2px; }}
            QCalendarWidget QAbstractItemView:enabled {{ color: {C.ON_SURFACE}; background: {bg_color}; }}
            QCalendarWidget QAbstractItemView:disabled {{ color: rgba(139,94,60,0.3); background: {bg_color}; }}
            QCalendarWidget QHeaderView {{ background: {bg_color}; border: none; }}
            QCalendarWidget QHeaderView::section {{ background-color: {bg_color}; color: {C.ON_SURFACE_VAR}; border: none; font-size: 16px; font-weight: bold; }}
        """)
        form_lay.addWidget(self._calendar)
        
        # Time picker
        time_lbl = QLabel("Time")
        time_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; font-size: 16px; font-weight: bold; background: transparent;")
        form_lay.addWidget(time_lbl)
        
        time_lay = QHBoxLayout()
        time_lay.setSpacing(8)
        
        self._hh_input = QLineEdit()
        self._hh_input.setText("12")
        self._hh_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hh_input.setFixedHeight(44)
        self._hh_input.setMaximumWidth(60)
        
        self._mm_input = QLineEdit()
        self._mm_input.setText("00")
        self._mm_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mm_input.setFixedHeight(44)
        self._mm_input.setMaximumWidth(60)
        
        self._ampm = AnimatedDropDown(["AM", "PM"], bg_color="rgba(255, 255, 255, 0.70)", border="1px solid rgba(139,94,60,0.20)", height=44)
        
        for w in (self._hh_input, self._mm_input):
            w.setStyleSheet(f"""
                QLineEdit {{
                    background: rgba(255, 255, 255, 0.70);
                    color: {C.ON_SURFACE};
                    border: 1px solid rgba(139,94,60,0.20);
                    border-radius: 8px;
                    font-size: 20px;
                    font-weight: bold;
                    padding: 0px;
                }}
                QLineEdit:focus {{ border: 1px solid {C.PRIMARY}; }}
            """)
            
        time_colon = QLabel(":")
        time_colon.setStyleSheet(f"color: {C.ON_SURFACE}; font-size: 24px; font-weight: bold; background: transparent;")
        
        time_lay.addWidget(self._hh_input)
        time_lay.addWidget(time_colon)
        time_lay.addWidget(self._mm_input)
        time_lay.addWidget(self._ampm)
        form_lay.addLayout(time_lay)
        
        form_lay.addSpacing(8)
        
        # Set Reminder Button
        self._save_btn = QPushButton("⏰ Set Reminder")
        self._save_btn.setFixedHeight(48)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.setStyleSheet("""
            QPushButton {
                background: #c8e6c9;
                color: #1b5e20;
                font-size: 18px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover { background: #a5d6a7; }
            QPushButton:pressed { background: #81c784; }
        """)
        self._save_btn.clicked.connect(self._save_reminder)
        form_lay.addWidget(self._save_btn)
        
        left_col.addWidget(form_card)
        
        # RIGHT COLUMN (Upcoming List)
        right_col = QVBoxLayout()
        
        list_card = QFrame()
        list_card.setObjectName("remListCard")
        list_card.setStyleSheet("""
            QFrame#remListCard {
                background: rgba(255, 255, 255, 0.40);
                border: 1px solid rgba(255, 255, 255, 0.30);
                border-radius: 24px;
            }
        """)
        _glass_shadow(list_card, blur=30, dy=8)
        
        list_lay = QVBoxLayout(list_card)
        list_lay.setContentsMargins(24, 24, 24, 24)
        list_lay.setSpacing(16)
        
        list_hdr = QHBoxLayout()
        upcoming_title = QLabel("ALL REMINDERS")
        upcoming_title.setFont(_make_font(_FONT_HUD, 18, bold=True))
        upcoming_title.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; letter-spacing: 2px;")
        self._count_lbl = QLabel("0 items")
        self._count_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; font-size: 16px; background: transparent;")
        list_hdr.addWidget(upcoming_title)
        list_hdr.addStretch()
        list_hdr.addWidget(self._count_lbl)
        
        # Draw a bottom border for header
        hdr_wrapper = QFrame()
        hdr_wrapper.setLayout(list_hdr)
        hdr_wrapper.setStyleSheet("""
            QFrame { border-bottom: 1px solid rgba(139,94,60,0.20); padding-bottom: 8px; }
            QLabel { border: none; }
        """)
        list_lay.addWidget(hdr_wrapper)
        
        # Scroll area for items (animated reveal + gradients)
        self._scroll = AnimatedScrollArea(
            show_gradients=True,
            gradient_color=(235, 231, 223),  # Match warm BG
            gradient_height=50
        )
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 8, 8, 8)
        self._list_layout.setSpacing(12)
        self._list_layout.addStretch()
        
        self._scroll.setWidget(self._list_container)
        list_lay.addWidget(self._scroll)
        
        right_col.addWidget(list_card)

        body.addLayout(left_col, 1)
        body.addLayout(right_col, 1)
        self._body = body
        self._body_stretch_added = False

        page_scroll = AnimatedScrollArea(self, show_gradients=False)
        page_scroll.setWidgetResizable(True)
        page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_scroll.setWidget(body_host)
        root.addWidget(page_scroll, stretch=1)

        self._apply_responsive_layout()
        text_scale.instance().changed.connect(self._on_scale_changed)

        # Set by refresh_list: the moment the topmost upcoming row falls due,
        # and the day the list was built for. _check_flip watches both.
        self._next_flip = None
        self._built_date = None
        self._flip_timer = QTimer(self)
        self._flip_timer.timeout.connect(self._check_flip)

        self.refresh_list()

    def _on_scale_changed(self, _level):
        try:
            self._apply_responsive_layout()
        except RuntimeError:
            pass

    def showEvent(self, event):
        """
        Reload every time the page comes to the front.

        The list was built once and then rebuilt only when a reminder was
        added from this page — so a medication added, edited or deleted over
        on the Health page left rows here describing records that had changed
        underneath them. Editing re-saves a medication under a *new* id, so
        such a row was pressing its alarm button against an id the store no
        longer had. Pages live in a stacked widget, so this fires on every
        navigation to Reminders and costs one JSON read.
        """
        super().showEvent(event)
        self.refresh_list()
        self._flip_timer.start(_ROW_FLIP_CHECK_MS)

    def hideEvent(self, event):
        """Nothing to re-flag on a page nobody is looking at."""
        super().hideEvent(event)
        self._flip_timer.stop()

    def _check_flip(self):
        """
        Rebuild only when a row has actually changed sides.

        Refreshing on every tick would re-run the list's reveal animation
        every few seconds, so the timer checks against the moment recorded by
        refresh_list and does nothing until it arrives. The date check catches
        midnight, when today's medication rows have to roll over to the new
        day even though no individual time has passed.
        """
        from datetime import datetime
        now = datetime.now()
        due = self._next_flip is not None and now >= self._next_flip
        if due or now.date() != self._built_date:
            self.refresh_list()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    # Below this, halving the centre column between the two cards leaves the
    # list narrower than a single reminder row's own floor (icon + wrapped
    # message + wrapped time + the alarm button). That is true even at the
    # default text size on the app's own default window — this app's stock
    # 1400px window leaves the centre column at ~680px once the side panels
    # take their share — so the stacking decision has to watch the page's
    # actual width, not only the text-size factor.
    #
    # Measured rather than guessed: with the two cards side by side, every row
    # gets at least its minimumSizeHint from 970px up, and is squeezed below
    # that. It squeezes silently — the scroll area resizes its contents to the
    # viewport, so a row that does not fit clips its own text instead of
    # producing a scrollbar, and the button ends up reading "Al…". The old
    # value of 720 predates the button and was already ~110px optimistic.
    _MIN_ROW_WIDTH = 980

    def _apply_responsive_layout(self):
        """
        Flip the form/list arrangement between side-by-side and stacked, live
        — whenever the text gets large enough to need it, or the window gets
        narrow enough to need it. QBoxLayout.setDirection() re-flows the same
        two sub-layouts in place — nothing is rebuilt or reparented, so this
        is cheap enough to call on every size change and every resize.
        """
        stacked = (text_scale.factor() > 1.18
                   or self.width() < self._MIN_ROW_WIDTH)
        if stacked:
            self._body.setDirection(QBoxLayout.Direction.TopToBottom)
            # The form must keep its natural height — sharing the column
            # evenly is what collapses its fields on top of each other. The
            # list gets a workable floor and the page scrolls past it.
            self._body.setStretch(0, 0)
            self._body.setStretch(1, 0)
            if not self._body_stretch_added:
                self._body_stretch_added = True
                self._body.addStretch()
            self._scroll.setMinimumHeight(text_scale.px(180))
        else:
            self._body.setDirection(QBoxLayout.Direction.LeftToRight)
            self._body.setStretch(0, 1)
            self._body.setStretch(1, 1)
            if self._body_stretch_added:
                item = self._body.takeAt(self._body.count() - 1)
                del item
                self._body_stretch_added = False
            self._scroll.setMinimumHeight(0)

    def _save_reminder(self):
        text = self._msg_input.text().strip()
        if not text:
            return
            
        hh_str = self._hh_input.text().strip()
        mm_str = self._mm_input.text().strip()
        ampm = self._ampm._current_text
        
        try:
            hh = int(hh_str)
            mm = int(mm_str)
            if ampm == "PM" and hh < 12: hh += 12
            if ampm == "AM" and hh == 12: hh = 0
            
            d = self._calendar.date().toPyDate()
            from datetime import time
            t = time(hh, mm)
        except Exception:
            self._hh_input.setText("12")
            self._mm_input.setText("00")
            return
            
        from datetime import datetime, date
        from actions.reminder import reminder
        date_str = str(d)
        time_str = str(t)[:5]
        
        try:
            reminder(parameters={"task_name": "", "message": text, "date": date_str, "time": time_str})
        except Exception as e:
            print("Failed to set reminder:", e)
            
        self._msg_input.clear()
        self.refresh_list()
        self.reminder_created.emit()

    def refresh_list(self):
        while self._list_layout.count():
            w = self._list_layout.takeAt(0).widget()
            if w:
                # hide() immediately, rather than leaving it to whenever
                # deleteLater()'s scheduled deletion actually runs — taking a
                # widget out of the layout stops it being positioned, but it
                # stays visible at its last geometry until then. A rebuild
                # that adds a new card into roughly the same spot before that
                # happens paints both at once: old text ghosting behind new.
                w.hide()
                w.deleteLater()
            
        items = []
        
        # Load Reminders
        try:
            from actions.reminder_store import load_reminders
            reminders = load_reminders()
            for r in reminders:
                dt_str = f"{r.get('date')} {r.get('time')}"
                try:
                    from datetime import datetime
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                    items.append({
                        "type": "reminder",
                        "id": r.get("id", ""),
                        "alarm": bool(r.get("alarm", False)),
                        "snoozed": bool(r.get("snoozed", False)),
                        "title": r.get("message", "Reminder"),
                        "subtitle": "System Reminder",
                        "dt": dt,
                        "icon": "💧" if "water" in r.get("message", "").lower() else "📅"
                    })
                except Exception: pass
        except Exception as e: print("Rem error:", e)
        
        # Load Medications
        try:
            from actions.medication_store import (load_medications,
                                                  parse_time_of_day)
            meds = load_medications()
            from datetime import datetime, time
            today = datetime.now().date()

            for m in meds:
                times = m.get("times", [])
                for t_str in times:
                    # The dose time is free text, so it is whatever was typed
                    # — "5pm" as readily as "08:00 AM". strptime with a single
                    # format silently dropped everything it did not recognise,
                    # which is why a medication could be stored and still never
                    # appear in this list.
                    try:
                        parsed = parse_time_of_day(t_str)
                        if parsed is None:
                            continue
                        dt = datetime.combine(today, time(parsed[0], parsed[1]))
                        items.append({
                            "type": "medication",
                            "id": m.get("id", ""),
                            "alarm": bool(m.get("alarm", False)),
                            "title": f"{m.get('name', 'Meds')}",
                            "subtitle": m.get("dosage", ""),
                            "dt": dt,
                            "icon": "💊",
                            "taken": m.get("taken_today", False)
                        })
                    except Exception: pass
        except Exception as e: print("Med error:", e)
        
        # What is still to come goes first, soonest at the top. What has
        # already been given sinks below it, most recent first — it is there
        # to be checked, not worked through, so it should not be the first
        # thing in a list whose job is what happens next.
        from datetime import datetime
        now = datetime.now()
        upcoming = sorted((i for i in items if i["dt"] >= now),
                          key=lambda x: x["dt"])
        earlier = sorted((i for i in items if i["dt"] < now),
                         key=lambda x: x["dt"], reverse=True)

        # Appended in order, then the stretch that pins them to the top.
        #
        # This used to insert at count() - 1, to sit each card in front of the
        # stretch added in __init__ — but the clear-out above takes the whole
        # layout apart, stretch included. With the layout empty, count() - 1
        # is -1, which Qt reads as "append", and from the second card on it
        # was a real index, putting cards in front of ones already placed. The
        # list came out shuffled, so sorting it made no difference to what was
        # on screen.
        for item in upcoming + earlier:
            self._list_layout.addWidget(self._make_item_card(item))
        self._list_layout.addStretch()

        if earlier and upcoming:
            self._count_lbl.setText(
                f"{len(upcoming)} upcoming · {len(earlier)} earlier")
        elif earlier:
            self._count_lbl.setText(f"{len(earlier)} earlier")
        else:
            self._count_lbl.setText(f"{len(upcoming)} upcoming")

        # When the next row is due to change sides. The list re-flags itself
        # at that moment rather than on a fixed tick, so a reminder crossing
        # its time while the page is open does not sit there still claiming
        # to be upcoming — and nothing is rebuilt on the ticks in between.
        self._next_flip = upcoming[0]["dt"] if upcoming else None
        self._built_date = now.date()

    # Three looks, from one warm palette.
    #
    # Reminders take the green the rest of the app leads with; medications
    # take amber, so a dose never reads as an errand at a glance. Anything
    # whose time has been and gone drops to a neutral stone — it keeps the
    # same shape but stops competing with what is still to come, which is
    # what the list is mostly for.
    _ROW_LOOKS = {
        "reminder": {
            "edge":     C.PRIMARY,                    # 4px left rule
            "card":     "rgba(255, 255, 255, 0.70)",
            "hover":    "rgba(55, 103, 87, 0.06)",
            "border":   "rgba(55, 103, 87, 0.18)",
            "chip_bg":  "rgba(55, 103, 87, 0.10)",
            "chip_brd": "rgba(55, 103, 87, 0.22)",
            "title":    C.ON_SURFACE,
            "sub":      C.ON_SURFACE_VAR,
            "time":     C.PRIMARY,
        },
        "medication": {
            "edge":     "#9A6B2F",
            "card":     "rgba(255, 255, 255, 0.70)",
            "hover":    "rgba(154, 107, 47, 0.07)",
            "border":   "rgba(154, 107, 47, 0.20)",
            "chip_bg":  "#FBF3E5",
            "chip_brd": "rgba(154, 107, 47, 0.28)",
            "title":    C.ON_SURFACE,
            "sub":      C.ON_SURFACE_VAR,
            "time":     "#8A5D22",
        },
        # Past rows carry a dimmed version of their own family colour rather
        # than a shared grey. The strong signal that something has already
        # happened is the washed-out card and the status pill; keeping a trace
        # of the green or the amber means a dose still cannot be mistaken for
        # an errand halfway down the list.
        "past_reminder": {
            "edge":     "rgba(55, 103, 87, 0.38)",
            "card":     "rgba(255, 255, 255, 0.32)",
            "hover":    "rgba(55, 103, 87, 0.05)",
            "border":   "rgba(55, 103, 87, 0.14)",
            "chip_bg":  "rgba(55, 103, 87, 0.07)",
            "chip_brd": "rgba(55, 103, 87, 0.18)",
            "title":    "rgba(45, 41, 38, 0.55)",
            "sub":      "rgba(93, 64, 55, 0.50)",
            "time":     "rgba(55, 103, 87, 0.55)",
        },
        "past_medication": {
            "edge":     "rgba(154, 107, 47, 0.40)",
            "card":     "rgba(255, 255, 255, 0.32)",
            "hover":    "rgba(154, 107, 47, 0.05)",
            "border":   "rgba(154, 107, 47, 0.16)",
            "chip_bg":  "rgba(154, 107, 47, 0.08)",
            "chip_brd": "rgba(154, 107, 47, 0.20)",
            "title":    "rgba(45, 41, 38, 0.55)",
            "sub":      "rgba(93, 64, 55, 0.50)",
            "time":     "rgba(138, 93, 34, 0.62)",
        },
    }

    def _row_look(self, kind: str, past: bool) -> dict:
        return self._ROW_LOOKS[f"past_{kind}" if past else kind]

    def _status_chip(self, text: str, look: dict) -> QLabel:
        """The small pill that says what already happened to a row."""
        chip = QLabel(text)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setMinimumHeight(44)
        chip.setStyleSheet(f"""
            background: {look['chip_bg']};
            color: {look['sub']};
            border: 1px solid {look['chip_brd']};
            border-radius: 12px;
            padding: 0 14px;
            font-size: 15px;
            font-weight: bold;
        """)
        return chip

    def _make_item_card(self, item):
        card = QFrame()
        is_med = (item["type"] == "medication")

        # Compared against the full timestamp, not just the date. A reminder
        # set for six o'clock this evening is still "Today" at eight, and was
        # being shown as though it had yet to happen.
        from datetime import datetime
        now = datetime.now()
        past = item["dt"] < now

        look = self._row_look(item["type"], past)

        card.setStyleSheet(f"""
            QFrame {{
                background: {look['card']};
                border: 1px solid {look['border']};
                border-left: 4px solid {look['edge']};
                border-radius: 12px;
            }}
            QFrame:hover {{ background: {look['hover']}; }}
        """)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(16)

        # Icon
        icon_box = QLabel(item["icon"])
        icon_box.setFixedSize(48, 48)
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setStyleSheet(
            f"background: {look['chip_bg']}; font-size: 24px;"
            f"border: 1px solid {look['chip_brd']}; border-radius: 24px;")
        lay.addWidget(icon_box)

        # Text details
        text_lay = QVBoxLayout()
        text_lay.setSpacing(4)

        title_lbl = QLabel(item["title"])
        title_lbl.setWordWrap(True)
        if is_med and item.get("taken"):
            title_lbl.setText(f"<s>{item['title']}</s>")
            title_lbl.setStyleSheet("color: rgba(31, 27, 19, 0.5); font-size: 18px; font-weight: bold; background: transparent; border: none;")
        else:
            title_lbl.setStyleSheet(f"color: {look['title']}; font-size: 18px; font-weight: bold; background: transparent; border: none;")

        sub_lbl = QLabel(item["subtitle"])
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {look['sub']}; font-size: 15px; background: transparent; border: none;")

        text_lay.addWidget(title_lbl)
        text_lay.addWidget(sub_lbl)
        lay.addLayout(text_lay)
        lay.addStretch()

        # Time details
        time_lay = QVBoxLayout()
        time_lay.setSpacing(4)

        time_str = item["dt"].strftime("%I:%M %p")

        if item["dt"].date() > now.date():
            day_str = "Tomorrow"
        elif item["dt"].date() < now.date():
            day_str = "Earlier"
        elif past:
            # A medication comes round again; a one-off reminder does not.
            day_str = "Next tomorrow" if is_med else "Earlier today"
        else:
            day_str = "Today"

        time_lbl = QLabel(time_str)
        time_lbl.setStyleSheet(f"color: {look['time']}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        # Wrapping (not just the title/subtitle) is what lets this card's true
        # floor shrink below its natural one-line width — without it, "11:46
        # PM" alone was enough to force the card wider than a narrow list
        # column, and that showed up as real horizontal scroll capability.
        time_lbl.setWordWrap(True)

        if item.get("snoozed"):
            day_str = f"Snoozed · {day_str}"

        day_lbl = QLabel(day_str)
        day_lbl.setStyleSheet(f"color: {look['sub']}; font-size: 14px; background: transparent; border: none;")
        day_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        day_lbl.setWordWrap(True)

        time_lay.addWidget(time_lbl)
        time_lay.addWidget(day_lbl)
        lay.addLayout(time_lay)

        # The right-hand control. A reminder that has already been given has
        # nothing left to choose — switching it between a notification and an
        # alarm would change nothing that can still happen — so it reports
        # what it did instead of offering a button that does nothing. A
        # medication keeps its button: today's dose has gone, tomorrow's has
        # not.
        if past and not is_med:
            lay.addWidget(
                self._status_chip(
                    "🔔 Alarm rang" if item.get("alarm") else "✓ Notified",
                    look),
                0, Qt.AlignmentFlag.AlignVCenter)
        elif item.get("id"):
            # Needs an id to write the choice against — anything stored before
            # this feature existed has one, but a malformed row might not.
            alarm_btn = AlarmModeButton(item["id"], item["type"],
                                        item.get("alarm", False), card,
                                        muted=past)
            # Rebuild on the next turn of the event loop, not inside the
            # button's own click handler — refresh_list() deletes the very
            # widget that is mid-signal.
            alarm_btn.went_stale.connect(
                lambda: QTimer.singleShot(0, self.refresh_list))
            lay.addWidget(alarm_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Remove this one. Reminders only: a medication is a standing
        # prescription that comes round again tomorrow, so a bin on its row
        # would be asking whether today's dose is being dropped or the whole
        # prescription. That belongs on the Health page, where it already is.
        if not is_med and item.get("id"):
            del_btn = AnimatedButton("🗑")
            del_btn.setFont(_make_font(size=18))
            # 44px, not the 30px the tasks page uses. Same user, and a target
            # that small is hard to hit with an unsteady hand — this is the
            # one control on the card that cannot be undone.
            del_btn.setFixedSize(44, 44)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip(f"Delete this reminder — {item['title']}")
            del_btn.setAccessibleName(f"Delete reminder: {item['title']}")
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid rgba(139,94,60,0.25);
                    border-radius: 12px;
                }}
                QPushButton:hover {{
                    background: rgba(186,26,26,0.10);
                    border: 1px solid rgba(186,26,26,0.40);
                }}""")
            del_btn.clicked.connect(
                lambda _=False, rid=item["id"]: self._delete_reminder(rid))
            lay.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        return card

    def _delete_reminder(self, reminder_id: str):
        """Remove one reminder. No confirmation — it is a single row, the
        label says which one, and the tasks page sets the same expectation."""
        if not _HAS_REMINDER_STORE:
            return
        try:
            store_delete_reminder(reminder_id)
        except Exception as e:
            print(f"[Reminders] Could not delete {reminder_id}: {e}")
        # Next turn of the event loop, not inline: refresh_list() deletes the
        # very button whose click is still being delivered.
        QTimer.singleShot(0, self.refresh_list)

    def _passed_reminders(self) -> list[dict]:
        """Reminders whose time has already gone by. Medications excluded —
        they recur, so 'passed' does not mean finished with."""
        if not _HAS_REMINDER_STORE:
            return []
        from datetime import datetime
        now = datetime.now()
        out = []
        for r in load_reminders():
            try:
                dt = datetime.strptime(
                    f"{r.get('date', '')} {r.get('time', '')}", "%Y-%m-%d %H:%M")
            except Exception:
                # Unparseable rows are left alone rather than swept up: this
                # cannot tell whether they have passed.
                continue
            if dt <= now:
                out.append(r)
        return out

    def _clear_passed(self):
        """Remove every reminder already due — the bulk one, so it asks first."""
        from PyQt6.QtWidgets import QMessageBox

        box_qss = f"""
            QMessageBox {{ background: {C.BG}; }}
            QLabel {{ color: {C.ON_SURFACE}; font-size: 16px; }}
            QPushButton {{
                background: rgba(133,116,72,0.1);
                color: {C.ON_SURFACE};
                border-radius: 8px;
                padding: 8px 16px;
                border: none;
                min-width: 80px;
            }}"""

        gone = self._passed_reminders()
        if not gone:
            note = QMessageBox(self)
            note.setWindowTitle("Clear Passed")
            note.setText("Nothing to clear.")
            note.setInformativeText(
                "Every reminder you have is still to come.")
            note.setStandardButtons(QMessageBox.StandardButton.Ok)
            note.setStyleSheet(box_qss)
            note.exec()
            return

        # Named, not just counted. "Delete 3 reminders?" asks the user to
        # trust a number they cannot check.
        names = "\n".join(f"  • {r.get('message', 'Reminder')}" for r in gone[:8])
        if len(gone) > 8:
            names += f"\n  • …and {len(gone) - 8} more"

        ask = QMessageBox(self)
        ask.setWindowTitle("Clear Passed")
        ask.setText(f"Remove {len(gone)} reminder"
                    f"{'s' if len(gone) != 1 else ''} that have already passed?")
        ask.setInformativeText(f"{names}\n\nThis cannot be undone. "
                               "Reminders still to come are kept.")
        ask.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        ask.setDefaultButton(QMessageBox.StandardButton.No)
        ask.setStyleSheet(box_qss)
        if ask.exec() != QMessageBox.StandardButton.Yes:
            return

        for r in gone:
            rid = r.get("id", "")
            if not rid:
                continue
            try:
                store_delete_reminder(rid)
            except Exception as e:
                print(f"[Reminders] Could not delete {rid}: {e}")
        self.refresh_list()

class HomeBackButton(QWidget):
    clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(50)
        
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(10, 0, 10, 0)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        self._arrow = QLabel("⬅")
        self._arrow.setFont(_make_font(size=24, bold=True))
        self._arrow.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; border: none;")
        
        self._text = QLabel("Back to Home")
        self._text.setFont(_make_font(size=16, bold=True))
        self._text.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        
        self.lay.addWidget(self._arrow)
        self.lay.addWidget(self._text)
        
        self._hover_offset = 0.0
        from PyQt6.QtCore import QPropertyAnimation, pyqtProperty
        self._anim = QPropertyAnimation(self, b"hoverOffset")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @pyqtProperty(float)
    def hoverOffset(self):
        return self._hover_offset

    @hoverOffset.setter
    def hoverOffset(self, val):
        self._hover_offset = val
        self.lay.setContentsMargins(int(10 - val), 0, 10, 0)

    def enterEvent(self, e):
        self._anim.setStartValue(self._hover_offset)
        self._anim.setEndValue(10.0)
        self._anim.start()
        self._text.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; border: none;")
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.setStartValue(self._hover_offset)
        self._anim.setEndValue(0.0)
        self._anim.start()
        self._text.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        super().leaveEvent(e)
        
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class PlaceholderPage(QWidget):
    """Placeholder page for Notifications, Task Lists, Notes."""

    def __init__(self, icon: str, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        # In a scroll area, like every other page. This one had none, so on a
        # short window at the larger text sizes its contents were simply cut
        # off with no way to reach them — the icon lost its bottom half and
        # the description ran out of the page.
        scroll = AnimatedScrollArea(self, show_gradients=False)
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(40, 80, 40, 80)
        lay.addStretch()

        icon_lbl = QLabel(icon)
        icon_lbl.setFont(_make_font(size=48))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(icon_lbl)

        t = QLabel(title.upper())
        t.setFont(_make_font(_FONT_HUD, 22, bold=True))
        t.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; letter-spacing: 3px;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)

        s = QLabel(subtitle)
        s.setFont(_make_font(size=14))
        s.setStyleSheet(f"color: {C.SECONDARY}; background: transparent;")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setWordWrap(True)
        lay.addWidget(s)

        lay.addStretch()

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)


# ═══════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════



# ==========================================
# Tasks Page
# ==========================================
class TasksPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background: transparent;")
        
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(40, 40, 40, 40)
        main_lay.setSpacing(20)
        
        # Header
        title = QLabel("Tasks")
        title.setFont(_make_font(size=28, bold=True))
        title.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent;")
        main_lay.addWidget(title)
        
        # Content Layout — the list and the New Task form sit side by side
        # while there is width for it, and stack once the text is large.
        # setDirection() in _apply_responsive_layout flips this live.
        content_lay = QHBoxLayout()
        content_lay.setSpacing(30)
        
        # Left side: List
        left_col = QVBoxLayout()
        
        scroll = AnimatedScrollArea(
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )
        
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(12)
        scroll.setWidget(self._list_widget)
        
        left_col.addWidget(scroll)
        content_lay.addLayout(left_col, 2)
        self._list_scroll = scroll
        
        # Right side: Form
        right_col = QVBoxLayout()
        right_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        form_card = QWidget()
        form_card.setStyleSheet(f"""
            QWidget {{
                background: rgba(255,255,255,0.4);
                border: 1px solid rgba(139,94,60,0.2);
                border-radius: 16px;
            }}""")
        form_lay = QVBoxLayout(form_card)
        form_lay.setContentsMargins(20, 20, 20, 20)
        form_lay.setSpacing(16)
        
        frm_title = QLabel("New Task")
        frm_title.setFont(_make_font(size=18, bold=True))
        frm_title.setStyleSheet(f"color: {C.ON_SURFACE}; border: none; background: transparent;")
        form_lay.addWidget(frm_title)
        
        self._task_input = QLineEdit()
        self._task_input.setPlaceholderText("Task description...")
        self._task_input.setFont(_make_font(size=12))
        self._task_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 8px;
                padding: 10px;
                color: {C.ON_SURFACE};
            }}""")
        form_lay.addWidget(self._task_input)
        
        self._is_daily_cb = QCheckBox("Reset Everyday (Daily Routine)")
        self._is_daily_cb.setFont(_make_font(size=11))
        self._is_daily_cb.setStyleSheet(f"""
            QCheckBox {{ color: {C.ON_SURFACE}; border: none; background: transparent; }}QCheckBox::indicator {{ width: 18px; height: 18px; border: 2px solid {C.PRIMARY}; border-radius: 4px; }}QCheckBox::indicator:checked {{ background: {C.PRIMARY}; }}""")
        form_lay.addWidget(self._is_daily_cb)
        
        self._notes_input = QTextEdit()
        self._notes_input.setPlaceholderText("Notes (Optional)...")
        self._notes_input.setFont(_make_font(size=11))
        self._notes_input.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 8px;
                padding: 8px;
                color: {C.ON_SURFACE};
            }}""")
        form_lay.addWidget(self._notes_input)
        
        self._status_lbl = QLabel("")
        self._status_lbl.setFont(_make_font(size=11, bold=True))
        self._status_lbl.setStyleSheet(f"border: none; background: transparent;")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setWordWrap(True)
        form_lay.addWidget(self._status_lbl)
        
        self._add_btn = AnimatedButton("  ➕  Add Task")
        self._add_btn.setFont(_make_font(size=13, bold=True))
        self._add_btn.setFixedSize(200, 44)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY};
                color: {C.WHITE};
                border-radius: 22px;
                border: none;
            }}""")
        self._add_btn.clicked.connect(self._on_add)
        form_lay.addWidget(self._add_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        
        right_col.addWidget(form_card)
        content_lay.addLayout(right_col, 1)
        self._content_lay = content_lay
        self._content_stretch_added = False

        # Always inside a scroll area: harmless when the row layout already
        # fits (nothing to scroll then), essential once it stacks and runs
        # taller than the window.
        content_host = QWidget()
        content_host.setStyleSheet("background: transparent;")
        content_host.setLayout(content_lay)
        page_scroll = AnimatedScrollArea(self, show_gradients=False)
        page_scroll.setWidgetResizable(True)
        page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_scroll.setWidget(content_host)
        main_lay.addWidget(page_scroll, stretch=1)

        self._apply_responsive_layout()
        text_scale.instance().changed.connect(self._on_scale_changed)

        self.refresh_list()

    def _on_scale_changed(self, _level):
        try:
            self._apply_responsive_layout()
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    # See RemindersPage._MIN_ROW_WIDTH — same two-column shape, same floor.
    _MIN_ROW_WIDTH = 720

    def _apply_responsive_layout(self):
        """Flip list/form between side-by-side and stacked, live. See
        RemindersPage._apply_responsive_layout for why setDirection()."""
        stacked = (text_scale.factor() > 1.18
                   or self.width() < self._MIN_ROW_WIDTH)
        if stacked:
            self._content_lay.setDirection(QBoxLayout.Direction.TopToBottom)
            self._content_lay.setStretch(0, 0)
            self._content_lay.setStretch(1, 0)
            if not self._content_stretch_added:
                self._content_stretch_added = True
                self._content_lay.addStretch()
            # Stacked, the list needs a floor of its own — sharing the column
            # by stretch is what crushes the form below it.
            self._list_scroll.setMinimumHeight(text_scale.px(160))
        else:
            self._content_lay.setDirection(QBoxLayout.Direction.LeftToRight)
            self._content_lay.setStretch(0, 2)
            self._content_lay.setStretch(1, 1)
            if self._content_stretch_added:
                item = self._content_lay.takeAt(self._content_lay.count() - 1)
                del item
                self._content_stretch_added = False
            self._list_scroll.setMinimumHeight(0)

    def refresh_list(self):
        while self._list_layout.count():
            w = self._list_layout.takeAt(0).widget()
            if w:
                # hide() immediately, rather than leaving it to whenever
                # deleteLater()'s scheduled deletion actually runs — taking a
                # widget out of the layout stops it being positioned, but it
                # stays visible at its last geometry until then. A rebuild
                # that adds a new card into roughly the same spot before that
                # happens paints both at once: old text ghosting behind new.
                w.hide()
                w.deleteLater()
            
        try:
            from actions.task_store import load_tasks
            tasks = load_tasks()
        except Exception:
            tasks = []
            
        if not tasks:
            lbl = QLabel("No tasks available.")
            lbl.setFont(_make_font(size=12))
            lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR};")
            self._list_layout.addWidget(lbl)
            return
            
        for t in reversed(tasks):
            self._list_layout.addWidget(self._make_task_card(t))
            
    def _make_task_card(self, task: dict):
        tid = task.get("id", "")
        text = task.get("text", "")
        done = task.get("done", False)
        is_daily = task.get("is_daily", False)
        notes = task.get("notes", "")
        
        card = ExpandableCard()
        card.setStyleSheet(f"""
            QWidget {{
                background: rgba(255,255,255,0.6);
                border: 1px solid rgba(139,94,60,0.2);
                border-radius: 12px;
            }}""")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 12)
        
        top_row = QHBoxLayout()
        cb = QCheckBox()
        cb.setChecked(done)
        cb.setStyleSheet(f"""
            QCheckBox::indicator {{ width: 22px; height: 22px; border: 2px solid {C.PRIMARY}; border-radius: 6px; }}QCheckBox::indicator:checked {{ background: {C.PRIMARY}; }}""")
        cb.toggled.connect(lambda _, x=tid: self._toggle_task(x))
        top_row.addWidget(cb)
        
        lbl = QLabel(text)
        lbl.setFont(_make_font(size=14, bold=not done))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"border: none; background: transparent; color: {C.ON_SURFACE_VAR if done else C.ON_SURFACE}; text-decoration: {'line-through' if done else 'none'};")
        top_row.addWidget(lbl, stretch=1)
        
        if is_daily:
            badge = QLabel("DAILY")
            badge.setFont(_make_font(size=9, bold=True))
            # Dark ink on the gold tint, not gold on it — the same 1.7:1
            # problem the overdue reminders had, and the same fix the alarm
            # badge above already uses.
            badge.setStyleSheet("color: #3E3100; border: 1px solid rgba(184,149,44,0.55); border-radius: 4px; padding: 2px 6px; background: rgba(212,175,55,0.18);")
            top_row.addWidget(badge)
            
        del_btn = AnimatedButton("🗑")
        del_btn.setFont(_make_font(size=14))
        del_btn.setFixedSize(30, 30)
        del_btn.setStyleSheet("border: none; background: transparent;")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda _, x=tid: self._delete_task(x))
        top_row.addWidget(del_btn)
        
        lay.addLayout(top_row)
        
        if notes:
            notes_lbl = QLabel(f"Notes: {notes}")
            notes_lbl.setFont(_make_font(size=11))
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet(f"color: rgba(93,64,55,0.7); border: none; background: transparent; margin-left: 32px;")
            lay.addWidget(notes_lbl)
            if hasattr(card, 'setContentWidget'):
                card.setContentWidget(notes_lbl, 60, 110)
        elif hasattr(card, 'setContentWidget'):
            card.setMinimumHeight(60)
            card.setMaximumHeight(60)
            
        return card

    def _on_add(self):
        text = self._task_input.text().strip()
        if not text:
            self._show_msg("Please enter a task description.", False)
            return
            
        try:
            from actions.task_store import save_task
            save_task(text, self._is_daily_cb.isChecked(), self._notes_input.toPlainText().strip())
            self._show_msg("✓ Task added successfully!", True)
            self._task_input.clear()
            self._notes_input.clear()
            self._is_daily_cb.setChecked(False)
            self.refresh_list()
        except Exception as e:
            self._show_msg(f"Failed to save task: {e}", False)
            
    def _show_msg(self, text, success=True):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {C.PRIMARY if success else C.ERROR}; border: none; background: transparent;")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(6000, self._status_lbl.clear)
        
    def _toggle_task(self, tid):
        try:
            from actions.task_store import toggle_task
            toggle_task(tid)
            self.refresh_list()
        except Exception:
            pass
            
    def _delete_task(self, tid):
        try:
            from actions.task_store import delete_task
            delete_task(tid)
            self.refresh_list()
        except Exception:
            pass


# ==========================================
# Safety Check Page (scam / AI-content screening)
# ==========================================

# The colours for each band, plus a fallback label. The label a verdict
# actually carries comes from the engine (Verdict.chip), because "scam" and
# "made by AI" are different claims that share a colour.
#
# Symbol *and* colour, never colour alone — the same reasoning already written
# into Toast: a user who cannot separate green from amber must still be able
# to read the answer.
_SAFETY_LOOKS = {
    "SAFE":    ("✓  NOTHING OBVIOUSLY WRONG", "#1B5E20", "#C8E6C9"),
    "CAUTION": ("⚠  BE CAREFUL",              "#554300", "#ffe088"),
    "DANGER":  ("⛔  THIS LOOKS LIKE A SCAM",  "#7f1d1d", "#ffd0d0"),
    "NONE":    ("—  NOT CHECKED YET",         "#4d4635", "#eae1d4"),
}

_SEVERITY_LOOKS = {
    "high":   ("● HIGH",   "#7f1d1d", "#ffd0d0"),
    "medium": ("● MEDIUM", "#554300", "#ffe088"),
    "low":    ("● LOW",    "#4d4635", "#eae1d4"),
}


def _refit_wrapped(host) -> bool:
    """Re-measure the wrapping labels under `host`, from scratch.

    `fit_wrapped_labels` stores its answer as a *minimum* height, and
    QLabel.heightForWidth will not return less than the minimum it already
    has. That is a ratchet: measure one label once while its column is still
    narrow — which is exactly what happens on the first layout pass, before
    the page has been given its real width — and the inflated height is
    permanent, because every later measurement is floored by the wrong one.
    It showed up here as a two-line disclaimer occupying 682 pixels.

    Clearing the minimums first makes the measurement honest. Callers guard on
    a width change so this cannot chase its own tail.
    """
    for lbl in host.findChildren(QLabel):
        if lbl.wordWrap():
            lbl.setMinimumHeight(0)
    return fit_wrapped_labels(host)


class _FindingRow(QFrame):
    """One observation: what was seen, and why it matters.

    The `why` line is not decoration. The whole argument for this feature is
    that a bare verdict hands the judgement back to the person least able to
    make it, so a row without its explanation would defeat the point.
    """

    def __init__(self, finding, parent=None):
        super().__init__(parent)
        label, fg, bg = _SEVERITY_LOOKS.get(
            getattr(finding, "severity", "medium"), _SEVERITY_LOOKS["medium"])
        self.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.55);
                border: none;
                border-left: 4px solid {fg};
                border-radius: 10px;
            }}""")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        chip = QLabel(label)
        chip.setFont(_make_font(size=10, bold=True))
        chip.setStyleSheet(
            f"color: {fg}; background: {bg}; border: none;"
            "border-radius: 8px; padding: 3px 10px;")
        chip.setProperty("_ts_nowrap", True)
        chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)

        what = QLabel(getattr(finding, "what", ""))
        what.setWordWrap(True)
        what.setFont(_make_font(size=13, bold=True))
        what.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        lay.addWidget(what)

        why = QLabel(getattr(finding, "why", ""))
        why.setWordWrap(True)
        why.setFont(_make_font(size=12))
        why.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        lay.addWidget(why)


class SafetyResultCard(QFrame):
    """Where the verdict is shown: what I found, why, and what to do about it.

    The disclaimer is built once in __init__ and never hidden or cleared. The
    report promises the system presents these results as indicative rather than
    authoritative; making that a permanent child rather than something
    `show_verdict` remembers to add is how it stays true after later edits.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.9);
                border: 1px solid rgba(139,94,60,0.2);
                border-radius: 20px;
            }""")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        self._header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._header.setSpacing(12)
        self._chip = QLabel()
        self._chip.setFont(_make_font(size=13, bold=True))
        self._chip.setProperty("_ts_nowrap", True)
        self._chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._header.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self._conf = QLabel()
        self._conf.setWordWrap(True)
        self._conf.setFont(_make_font(size=11))
        self._conf.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        self._header.addWidget(self._conf, 1, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(self._header)

        self._headline = QLabel()
        self._headline.setWordWrap(True)
        self._headline.setFont(_make_font(size=18, bold=True))
        self._headline.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        lay.addWidget(self._headline)

        self._found_title = self._section_title("What I found")
        lay.addWidget(self._found_title)
        self._found_box = QVBoxLayout()
        self._found_box.setSpacing(10)
        lay.addLayout(self._found_box)

        self._todo_title = self._section_title("What you should do")
        lay.addWidget(self._todo_title)
        self._todo_box = QVBoxLayout()
        self._todo_box.setSpacing(8)
        lay.addLayout(self._todo_box)

        self._checked = QLabel()
        self._checked.setWordWrap(True)
        self._checked.setFont(_make_font(size=11))
        self._checked.setStyleSheet(
            "color: rgba(93,64,55,0.75); background: transparent; border: none;")
        lay.addWidget(self._checked)

        self._degraded = QLabel(
            "My online check could not run, so this is from the offline checks only.")
        self._degraded.setWordWrap(True)
        self._degraded.setFont(_make_font(size=11, bold=True))
        self._degraded.setStyleSheet(
            "color: #554300; background: #ffe088; border: none;"
            "border-radius: 8px; padding: 8px 12px;")
        self._degraded.hide()
        lay.addWidget(self._degraded)

        # Absorbs the slack in one place. The card is stretched to the height of
        # the column it shares, and without this the extra space is divided
        # evenly between every child — which drifts the verdict away from the
        # headline and the headline away from the reasons, so nothing reads as
        # belonging together.
        lay.addStretch()

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: rgba(139,94,60,0.2); border: none;")
        lay.addWidget(divider)

        self._disclaimer = QLabel()
        self._disclaimer.setWordWrap(True)
        self._disclaimer.setFont(_make_font(size=12, bold=True))
        self._disclaimer.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        try:
            from actions.content_safety import DISCLAIMER
            self._disclaimer.setText(DISCLAIMER)
        except Exception:
            self._disclaimer.setText(
                "This is a guide, not a guarantee. When money, passwords or one-time "
                "codes are involved, check with someone you trust before you act.")
        lay.addWidget(self._disclaimer)

        self.clear()

    def _section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(_make_font(size=14, bold=True))
        lbl.setStyleSheet(f"color: {C.SECONDARY}; background: transparent; border: none;")
        return lbl

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # Wrapped labels cache their height-for-width; after a width change they
        # keep the old box and clip mid-sentence unless re-measured.
        if ev.size().width() != ev.oldSize().width():
            if _refit_wrapped(self):
                self.updateGeometry()

    def set_stacked(self, stacked: bool):
        self._header.setDirection(QBoxLayout.Direction.TopToBottom if stacked
                                  else QBoxLayout.Direction.LeftToRight)

    @staticmethod
    def _empty(box):
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w:
                # hide() before deleteLater(), or the old rows keep painting at
                # their last geometry until the deletion is actually serviced.
                w.hide()
                w.deleteLater()

    def clear(self):
        self._empty(self._found_box)
        self._empty(self._todo_box)
        label, fg, bg = _SAFETY_LOOKS["NONE"]
        self._chip.setText(label)
        self._chip.setStyleSheet(
            f"color: {fg}; background: {bg}; border: none;"
            "border-radius: 12px; padding: 10px 18px;")
        self._conf.setText("")
        self._headline.setText(
            "Paste a message, drop in a file, or let me look at your screen.")
        self._checked.setText("")
        self._degraded.hide()
        self._found_title.hide()
        self._todo_title.hide()

    def show_verdict(self, v):
        self._empty(self._found_box)
        self._empty(self._todo_box)

        label, fg, bg = _SAFETY_LOOKS.get(getattr(v, "band", "NONE"), _SAFETY_LOOKS["NONE"])
        self._chip.setText(getattr(v, "chip", "") or label)
        self._chip.setStyleSheet(
            f"color: {fg}; background: {bg}; border: none;"
            "border-radius: 12px; padding: 10px 18px;")
        self._conf.setText(f"How sure I am: {getattr(v, 'confidence', 'low')}")
        self._headline.setText(getattr(v, "headline", ""))

        findings = list(getattr(v, "findings", []))
        self._found_title.setVisible(bool(findings))
        for f in findings:
            self._found_box.addWidget(_FindingRow(f))

        advice = list(getattr(v, "what_to_do", []))
        self._todo_title.setVisible(bool(advice))
        for i, step in enumerate(advice, 1):
            row = QLabel(f"{i}.  {step}")
            row.setWordWrap(True)
            row.setFont(_make_font(size=13))
            row.setStyleSheet(
                f"color: {C.ON_SURFACE}; background: transparent; border: none;")
            self._todo_box.addWidget(row)

        checked = list(getattr(v, "checked", []))
        self._checked.setText("I looked at: " + " · ".join(checked) if checked else "")
        self._degraded.setVisible(bool(getattr(v, "degraded", False)))

        # Next turn, not now: the rows were created a moment ago and have not
        # been given their width yet, so measuring here measures nothing.
        QTimer.singleShot(0, lambda: (_refit_wrapped(self), self.updateGeometry()))


class SafetyPage(QWidget):
    """Show SIVA something suspicious and get an explained answer back.

    Objective 4 of the proposal was a content-detection module. This is the
    page that makes it usable at the point the user actually needs it: paste a
    message, drop a picture, or point at the screen. The engine lives in
    actions/content_safety.py and knows nothing about Qt.
    """

    _result_sig = pyqtSignal(object)
    _error_sig = pyqtSignal(str)
    _step_sig = pyqtSignal(str)

    # Two ~380px columns plus spacing and margins. Wider than Tasks (720)
    # because the findings carry real sentences, not one-line rows.
    _MIN_ROW_WIDTH = 860

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background: transparent;")
        self._busy = False

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(40, 40, 40, 40)
        main_lay.setSpacing(20)

        title = QLabel("Safety Check")
        title.setFont(_make_font(size=28, bold=True))
        title.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent;")
        main_lay.addWidget(title)

        subtitle = QLabel(
            "Show me a message, a link, an email or a picture and I will tell you "
            "what looks wrong with it — and why.")
        subtitle.setWordWrap(True)
        subtitle.setFont(_make_font(size=13))
        subtitle.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        main_lay.addWidget(subtitle)

        self._content_lay = QHBoxLayout()
        self._content_lay.setSpacing(30)
        self._content_stretch_added = False

        # ── left: what to check ────────────────────────────────────────
        left_col = QVBoxLayout()
        left_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        left_col.setSpacing(16)

        input_card = QWidget()
        input_card.setStyleSheet("""
            QWidget {
                background: rgba(255,255,255,0.4);
                border: 1px solid rgba(139,94,60,0.2);
                border-radius: 16px;
            }""")
        form = QVBoxLayout(input_card)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(14)

        form.addWidget(self._label("Paste the message or the web address"))

        self._text_input = QTextEdit()
        self._text_input.setPlaceholderText(
            "Paste the text message, email or link here…")
        self._text_input.setFont(_make_font(size=12))
        self._text_input.setMinimumHeight(140)
        # A QTextEdit expands vertically by default, which here meant it ate
        # every spare pixel in the column and pushed "Check this", the drop
        # zone and the screen button off the bottom of the page. Someone who
        # has just pasted a message needs to see the button that acts on it.
        self._text_input.setMaximumHeight(240)
        self._text_input.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Preferred)
        self._text_input.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 8px;
                padding: 10px;
                color: {C.ON_SURFACE};
            }}""")
        form.addWidget(self._text_input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._check_btn = AnimatedButton("🛡  Check this")
        self._check_btn.setFixedHeight(44)
        self._check_btn.setFont(_make_font(size=13, bold=True))
        self._check_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY}; color: white;
                border: none; border-radius: 10px; padding: 0 22px;
            }}
            QPushButton:disabled {{ background: rgba(55,103,87,0.4); }}""")
        self._check_btn.clicked.connect(self._check_pasted_text)
        btn_row.addWidget(self._check_btn)

        self._clear_btn = AnimatedButton("Clear")
        self._clear_btn.setFixedHeight(44)
        self._clear_btn.setFont(_make_font(size=13))
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.6); color: {C.ON_SURFACE_VAR};
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 10px; padding: 0 18px;
            }}""")
        self._clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        form.addLayout(btn_row)

        form.addWidget(self._label("Or give me a file"))
        self._drop = FileDropZone(accept=_SAFETY_EXTENSIONS)
        self._drop.file_selected.connect(self._check_file)
        form.addWidget(self._drop)

        self._screen_btn = AnimatedButton("📸  Check what is on my screen")
        self._screen_btn.setFixedHeight(44)
        self._screen_btn.setFont(_make_font(size=13))
        self._screen_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.6); color: {C.ON_SURFACE};
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 10px; padding: 0 18px;
            }}
            QPushButton:disabled {{ color: rgba(45,41,38,0.4); }}""")
        self._screen_btn.clicked.connect(self._check_screen)
        form.addWidget(self._screen_btn)
        form.addStretch()          # keep the controls together at the top

        left_col.addWidget(input_card)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFont(_make_font(size=12, bold=True))
        self._status.setStyleSheet(f"color: {C.SECONDARY}; background: transparent;")
        left_col.addWidget(self._status)

        self._content_lay.addLayout(left_col, 1)

        # ── right: the answer ──────────────────────────────────────────
        right_col = QVBoxLayout()
        right_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._result_card = SafetyResultCard()
        right_col.addWidget(self._result_card)
        self._content_lay.addLayout(right_col, 1)

        content_host = QWidget()
        content_host.setStyleSheet("background: transparent;")
        content_host.setLayout(self._content_lay)
        self._page_scroll = AnimatedScrollArea(self, show_gradients=False)
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page_scroll.setWidget(content_host)
        main_lay.addWidget(self._page_scroll, stretch=1)

        self._result_sig.connect(self._on_result)
        self._error_sig.connect(self._on_error)
        self._step_sig.connect(self._status.setText)

        self._apply_responsive_layout()
        text_scale.instance().changed.connect(self._on_scale_changed)

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setFont(_make_font(size=14, bold=True))
        lbl.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        return lbl

    # ── responsive ─────────────────────────────────────────────────────

    def _on_scale_changed(self, _level):
        try:
            self._apply_responsive_layout()
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()
        # Clear the wrapped-label minimums before the enclosing
        # AnimatedScrollArea re-measures them (core/motion.py:467). Its own
        # pass floors each label at whatever it was last given, so without
        # this a single early measurement at a narrow width is never undone.
        if event.size().width() != event.oldSize().width():
            _refit_wrapped(self)

    def _apply_responsive_layout(self):
        """Flip the two columns between side-by-side and stacked, live.

        A layout decision taken only in __init__ freezes at whatever size the
        app started at — so this runs from resizeEvent and from the text-scale
        signal as well.
        """
        stacked = (text_scale.factor() > 1.18
                   or self.width() < self._MIN_ROW_WIDTH)
        if stacked:
            self._content_lay.setDirection(QBoxLayout.Direction.TopToBottom)
            self._content_lay.setStretch(0, 0)
            self._content_lay.setStretch(1, 0)
            if not self._content_stretch_added:
                self._content_stretch_added = True
                self._content_lay.addStretch()
        else:
            self._content_lay.setDirection(QBoxLayout.Direction.LeftToRight)
            self._content_lay.setStretch(0, 1)
            self._content_lay.setStretch(1, 1)
            if self._content_stretch_added:
                item = self._content_lay.takeAt(self._content_lay.count() - 1)
                del item
                self._content_stretch_added = False
        self._result_card.set_stacked(stacked)

    # ── running a check ────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._busy = busy
        for b in (self._check_btn, self._screen_btn, self._clear_btn):
            b.setEnabled(not busy)

    def _start(self, job):
        """Run `job` off the GUI thread and deliver the result through signals.

        The engine makes a network call that can take seconds; doing it here
        would freeze the window, and a frozen window is indistinguishable from
        a broken one to the user this is built for.
        """
        if self._busy:
            return
        self._set_busy(True)
        self._status.setText("Checking…")

        def work():
            try:
                v = job()
            except Exception as e:
                self._error_sig.emit(str(e))
                return
            self._result_sig.emit(v)

        threading.Thread(target=work, daemon=True, name="SafetyCheck").start()

    def _engine(self):
        from actions import content_safety
        return content_safety

    def _check_pasted_text(self):
        text = self._text_input.toPlainText().strip()
        if not text:
            self._status.setText("Paste something in first, or drop me a file.")
            return
        cs = self._engine()
        self._start(lambda: cs.check_text(text, on_step=self._step_sig.emit))

    def _check_file(self, path: str):
        cs = self._engine()
        self._start(lambda: cs.check_file(path, on_step=self._step_sig.emit))

    def _check_screen(self):
        """Photograph the screen, then hand the bytes to a worker.

        The capture itself must stay on the GUI thread — it hides the window,
        pumps the event queue and waits for the compositor to catch up.
        """
        win = self.window()
        try:
            png = win._capture_behind_window(win._grab_screen_png)
        except Exception as e:
            self._on_error(f"I could not photograph the screen — {e}")
            return
        cs = self._engine()
        self._start(lambda: cs.check_image(png, kind="screen",
                                           on_step=self._step_sig.emit))

    # ── results ────────────────────────────────────────────────────────

    def _on_result(self, verdict):
        self._set_busy(False)
        self._status.setText("")
        self._result_card.show_verdict(verdict)
        self._reveal_result()

    def _reveal_result(self):
        """Bring the answer into view.

        Once the columns stack — at the larger text sizes, or on a narrow
        window — the result sits below the input box, off the bottom of the
        screen. Without this the user presses Check, the page appears not to
        respond, and they press it again. Deferred a turn so the scroll area
        has the rebuilt card's real height to work with.

        Scrolls to the card's *top* rather than using ensureWidgetVisible: the
        card is routinely taller than the viewport, and "make it visible" then
        settles on its bottom edge — which puts the verdict and the headline,
        the two things the user came for, off the top of the screen.
        """
        def _go():
            try:
                host = self._page_scroll.widget()
                top = self._result_card.mapTo(host, QPointF(0, 0).toPoint()).y()
                self._page_scroll.verticalScrollBar().setValue(max(0, top - 16))
            except (RuntimeError, AttributeError):
                pass
        QTimer.singleShot(0, _go)

    def _on_error(self, msg: str):
        self._set_busy(False)
        self._status.setText(f"The check did not finish — {msg}")
        try:
            self.window().show_toast(f"Safety check failed — {msg}", ok=False)
        except (AttributeError, RuntimeError):
            pass

    def _clear(self):
        self._text_input.clear()
        self._drop.clear_file()
        self._status.setText("")
        self._result_card.clear()

    # ── entry points used from elsewhere ───────────────────────────────

    def preload(self, path: str | None = None, text: str | None = None,
                run: bool = True):
        """Arrive on this page with something already loaded."""
        if text:
            self._text_input.setPlainText(text)
        if path:
            self._drop._try_set_file(path)      # emits file_selected → checks it
            return
        if text and run:
            self._check_pasted_text()

    def show_verdict(self, verdict):
        """Render a verdict computed somewhere else — the voice tool's answer."""
        self._set_busy(False)
        self._status.setText("")
        self._result_card.show_verdict(verdict)
        self._reveal_result()


# ==========================================
# Health Status Page (Medication Tracker)
# ==========================================
class HealthStatusPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background: transparent;")
        
        # The whole page scrolls, as on Reminders and Tasks. This page was the
        # one that scrolled only its medication list, with the title and the
        # status badge pinned outside it — so on a short window at the larger
        # text sizes there was nowhere for the header to go, and the layout
        # squeezed it to nothing rather than letting the page scroll. Both
        # labels were being given zero height.
        content_host = QWidget()
        content_host.setStyleSheet("background: transparent;")
        main_lay = QVBoxLayout(content_host)
        main_lay.setContentsMargins(40, 40, 40, 40)
        main_lay.setSpacing(32)

        page_scroll = AnimatedScrollArea(self, show_gradients=False)
        page_scroll.setWidgetResizable(True)
        page_scroll.setWidget(content_host)
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(page_scroll, stretch=1)

        # Header Layout
        #
        # A QBoxLayout so the axis can turn: past the middle text size the
        # title and the status badge stop sharing a line and the badge drops
        # underneath. Side by side they were fighting over a row too narrow
        # for both, and the loser was the page title — "Health Status" wrapped
        # onto a second line it was never given the height for.
        header_lay = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._header_lay = header_lay
        header_left = QVBoxLayout()
        header_left.setSpacing(8)
        
        title = QLabel("Health Status")
        title.setWordWrap(True)
        title.setStyleSheet("color: #1f1b13; font-size: 32px; font-weight: bold; background: transparent; padding-top: 4px;")
        header_left.addWidget(title)
        
        header_lay.addLayout(header_left)
        header_lay.addStretch()
        
        self._status_lbl = QLabel("")
        # A badge, so it stays on one line. Left to wrap it breaks after the
        # tick and reports a height the header row does not give it, which
        # clips the words inside their own coloured pill.
        self._status_lbl.setProperty("_ts_nowrap", True)
        self._status_lbl.setStyleSheet("color: #554300; font-size: 16px; font-weight: bold; background: #ffe088; padding: 16px 24px; border-radius: 12px;")
        header_lay.addWidget(self._status_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        self._apply_header_axis()
        text_scale.instance().changed.connect(lambda _l: self._apply_header_axis())

        main_lay.addLayout(header_lay)
        
        # Add Med Btn
        add_btn = QPushButton("➕ Add Medication")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedSize(220, 56)
        add_btn.setStyleSheet("""
            QPushButton {
                background: #C8E6C9;
                color: #1B5E20;
                border-radius: 16px;
                font-size: 18px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover { background: #A5D6A7; }
        """)
        add_btn.clicked.connect(self._open_add_dialog)
        self._add_med_btn = add_btn      # the walkthrough spotlights this
        main_lay.addWidget(add_btn)
        
        # List
        scroll = AnimatedScrollArea(
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )
        
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(16)
        scroll.setWidget(self._list_widget)
        # The page scrolls as a whole now, so the list inside it must ask for
        # the height it needs rather than sit in its own little viewport and
        # scroll separately — two nested scrollbars for one column of cards.
        scroll.setMinimumHeight(text_scale.px(240))
        main_lay.addWidget(scroll)
        self._cards_stacked = None     # forces the first refresh_list() below
        self.refresh_list()

        # Each card's row-vs-stacked arrangement is decided fresh at build
        # time (see _make_med_card) — rebuild them when the size changes, or
        # existing cards stay in whichever arrangement fit the size the page
        # happened to be built at.
        text_scale.instance().changed.connect(self._on_scale_changed)

    # Same shape as RemindersPage/TasksPage: a card needs roughly this much
    # width for the avatar, toggle and name to share a line without forcing
    # the card wider than its column.
    _MIN_ROW_WIDTH = 620

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._maybe_relayout_cards()

    def _on_scale_changed(self, _level):
        try:
            self._maybe_relayout_cards()
        except RuntimeError:
            pass

    def _maybe_relayout_cards(self):
        # refresh_list() tears down and rebuilds every card, which is too
        # costly to run on every pixel of a window drag — only pay for it
        # when the row-vs-stacked decision actually flips.
        stacked = text_scale.factor() > 1.18 or self.width() < self._MIN_ROW_WIDTH
        if stacked != self._cards_stacked:
            self._cards_stacked = stacked
            self.refresh_list()

    def refresh_list(self):
        while self._list_layout.count():
            w = self._list_layout.takeAt(0).widget()
            if w:
                # hide() immediately, rather than leaving it to whenever
                # deleteLater()'s scheduled deletion actually runs — taking a
                # widget out of the layout stops it being positioned, but it
                # stays visible at its last geometry until then. A rebuild
                # that adds a new card into roughly the same spot before that
                # happens paints both at once: old text ghosting behind new.
                w.hide()
                w.deleteLater()

        try:
            from actions.medication_store import load_medications
            meds = load_medications()
        except Exception:
            meds = []

        for m in meds:
            self._list_layout.addWidget(self._make_med_card(m))

        self.update_status_badge()

    def update_status_badge(self):
        try:
            from actions.medication_store import load_medications
            meds = load_medications()
        except Exception:
            meds = []
            
        if not meds:
            self._status_lbl.setText("No medications tracked.")
            self._status_lbl.setStyleSheet("color: #4d4635; background: #eae1d4; padding: 16px 24px; border-radius: 12px;")
            return
            
        all_taken = all(m.get("taken_today", False) for m in meds)
        if all_taken:
            self._status_lbl.setText("✓ All Medications Taken")
            self._status_lbl.setStyleSheet("color: #1B5E20; background: #C8E6C9; padding: 16px 24px; border-radius: 12px;")
        else:
            self._status_lbl.setText("⚠ Medications Pending")
            self._status_lbl.setStyleSheet("color: #554300; background: #ffe088; padding: 16px 24px; border-radius: 12px;")
            
    def _apply_header_axis(self):
        lay = getattr(self, "_header_lay", None)
        if lay is None:
            return
        try:
            lay.setDirection(
                QBoxLayout.Direction.TopToBottom if text_scale.factor() > 1.18
                else QBoxLayout.Direction.LeftToRight)
        except RuntimeError:
            pass

    def _make_med_card(self, med: dict):
        mid = med.get("id", "")
        name = med.get("name", "")
        dosage = med.get("dosage", "")
        freq = med.get("frequency", "")
        times = ", ".join(med.get("times", []))
        taken = med.get("taken_today", False)
        
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.9);
                border-radius: 20px;
                border: none;
            }
        """)

        # Row mode (small/default text) keeps everything on one line, as
        # before. Past the middle text size the avatar, toggle, edit and
        # delete controls no longer leave room for the name and frequency
        # text on the same line without forcing the card wider than its
        # column — so name/dosage move to their own row and the actions move
        # to a third. Decided fresh every time this card is built, which is
        # why refresh_list() is reconnected to run again on a text-size
        # change: a card built at one size does not silently stay wrong at
        # another. Same criterion as _maybe_relayout_cards, which decides
        # when refresh_list() actually needs to run.
        stacked = text_scale.factor() > 1.18 or self.width() < self._MIN_ROW_WIDTH
        outer = QVBoxLayout(card) if stacked else QHBoxLayout(card)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16 if stacked else 24)

        top_row = QHBoxLayout() if stacked else outer
        top_row.setSpacing(16 if stacked else 24)

        toggle_btn = AnimatedToggleBtn(is_taken=taken)
        def on_toggle():
            toggle_btn.toggle_state()
            self._toggle_med(mid)
        toggle_btn.clicked.connect(on_toggle)
        top_row.addWidget(toggle_btn)

        avatar = QLabel("⚕️")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(64, 64)
        avatar.setStyleSheet("""
            background: #efe7da;
            border: 1px solid #d0c5af;
            border-radius: 32px;
            font-size: 28px;
            color: #745853;
        """)
        top_row.addWidget(avatar)

        title_lay = QHBoxLayout()
        title_lay.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel(name)
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet("color: #1f1b13; font-size: 22px; font-weight: bold; background: transparent; border: none;")
        title_lay.addWidget(name_lbl)

        dosage_lbl = QLabel(f"{dosage}")
        dosage_lbl.setWordWrap(True)
        dosage_lbl.setStyleSheet("color: #ffffff; background: #745853; border-radius: 14px; font-size: 14px; padding: 4px 16px; font-weight: bold;")
        title_lay.addWidget(dosage_lbl)
        title_lay.addStretch()

        if stacked:
            top_row.addLayout(title_lay, 1)
            outer.addLayout(top_row)
        else:
            info_lay = QVBoxLayout()
            info_lay.setSpacing(6)
            info_lay.addLayout(title_lay)

        det_lbl = QLabel(f"Frequency: {freq} | Times: {times}")
        det_lbl.setWordWrap(True)
        det_lbl.setStyleSheet("color: #4d4635; font-size: 16px; background: transparent; border: none;")

        edit_btn = QPushButton("✎")
        edit_btn.setFixedSize(44, 44)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setStyleSheet("""
            QPushButton { color: #7f7663; font-size: 22px; background: transparent; border-radius: 22px; border: none; }
            QPushButton:hover { background: #eae1d4; color: #735c00; }
        """)
        edit_btn.clicked.connect(lambda _, m=med: self._open_add_dialog(m))

        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(44, 44)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet("""
            QPushButton { color: #7f7663; font-size: 22px; background: transparent; border-radius: 22px; border: none; }
            QPushButton:hover { background: #ffdad6; color: #ba1a1a; }
        """)
        del_btn.clicked.connect(lambda _, x=mid: self._delete_med(x))

        if stacked:
            outer.addWidget(det_lbl)
            actions_lay = QHBoxLayout()
            actions_lay.setSpacing(8)
            actions_lay.addStretch()
            actions_lay.addWidget(edit_btn)
            actions_lay.addWidget(del_btn)
            outer.addLayout(actions_lay)
        else:
            info_lay.addWidget(det_lbl)
            outer.addLayout(info_lay, stretch=1)
            actions_lay = QHBoxLayout()
            actions_lay.setSpacing(8)
            actions_lay.addWidget(edit_btn)
            actions_lay.addWidget(del_btn)
            outer.addLayout(actions_lay)

        return card
        
    def _toggle_med(self, mid):
        try:
            from actions.medication_store import toggle_medication_taken
            toggle_medication_taken(mid)
            self.update_status_badge()
        except Exception: pass
        
    def _delete_med(self, mid):
        try:
            from actions.medication_store import delete_medication, get_medication
            # Unschedule before deleting, while the times are still readable —
            # otherwise the recurring task outlives the medication and goes on
            # announcing a prescription that is no longer being taken.
            med = get_medication(mid)
            if med:
                try:
                    from actions.med_schedule import unschedule_medication
                    unschedule_medication(med)
                except Exception as e:
                    print(f"[Medication] Could not unschedule: {e}")
            delete_medication(mid)
            self.refresh_list()
        except Exception: pass

    def _open_add_dialog(self, med=None):
        d = AddMedicationDialog(self, med=med)
        if d.exec():
            try:
                from actions.medication_store import save_medication, delete_medication
                from actions.med_schedule import (schedule_medication,
                                                  unschedule_medication)
                # An edit is a delete and a re-save under a fresh id, so the
                # alarm choice has to be carried across by hand or every edit
                # would quietly turn an alarm back into a notification.
                alarm = bool(med.get("alarm", False)) if med else False
                if med:
                    try:
                        unschedule_medication(med)
                    except Exception as e:
                        print(f"[Medication] Could not unschedule: {e}")
                    delete_medication(med.get("id"))
                saved = save_medication(d.name, d.dosage, d.freq, [d.time],
                                        ["Everyday"], d.duration, alarm=alarm)
                try:
                    schedule_medication(saved)
                except Exception as e:
                    print(f"[Medication] Could not schedule: {e}")
                self.refresh_list()
            except Exception as e:
                print(e)

class AnimatedToggleBtn(QPushButton):
    # Design-time (1.0x) pixel values. The animation targets these scaled by
    # the gentle chrome rate, never the raw values, so the pill keeps up with
    # the current text size instead of freezing at its 1.0x footprint.
    _BASE_H = 40
    _BASE_W_SMALL = 40
    _BASE_W_TAKEN = 110

    def __init__(self, is_taken=False, parent=None):
        super().__init__(parent)
        self.is_taken = is_taken
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Height and width are both driven by hand in _update_ui (the width
        # because it animates between two states; the height so it can use
        # the same gentle-chrome pixel values as the width). The generic
        # engine's font/stylesheet scaling still applies normally — only its
        # Python-level geometry pass is skipped.
        self.setProperty("_ts_ignore_geom", True)

        self.anim = QPropertyAnimation(self, b"minimumWidth")
        self.anim.setDuration(250)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self.anim2 = QPropertyAnimation(self, b"maximumWidth")
        self.anim2.setDuration(250)
        self.anim2.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # A pure text-size change (no click) still has to move this pill to
        # its new size — nothing else will, since the generic pass ignores it.
        text_scale.instance().changed.connect(self._on_scale_changed)

        self._update_ui(animate=False)

    def _on_scale_changed(self, _level):
        try:
            self._update_ui(animate=False)
        except RuntimeError:
            pass

    def toggle_state(self):
        self.is_taken = not self.is_taken
        self._update_ui(animate=True)

    def _update_ui(self, animate=False):
        g = text_scale.gentle_factor()
        h = int(round(self._BASE_H * g))
        w_small = int(round(self._BASE_W_SMALL * g))
        w_taken = int(round(self._BASE_W_TAKEN * g))
        self.setFixedHeight(h)

        if self.is_taken:
            self.setText("✔ Taken")
            self.setStyleSheet("""
                QPushButton {
                    color: #2E7D32;
                    font-size: 16px;
                    font-weight: bold;
                    background: rgba(46, 125, 50, 0.1);
                    border: 2px solid #2E7D32;
                    border-radius: 20px;
                }
            """)
            start_w, end_w = w_small, w_taken
        else:
            self.setText("")
            self.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 2px solid #a39a89;
                    border-radius: 20px;
                }
                QPushButton:hover { background: rgba(163, 154, 137, 0.1); }
            """)
            start_w, end_w = w_taken, w_small

        if animate:
            self.anim.setStartValue(start_w)
            self.anim.setEndValue(end_w)
            self.anim.start()
            self.anim2.setStartValue(start_w)
            self.anim2.setEndValue(end_w)
            self.anim2.start()
        else:
            self.setMinimumWidth(end_w)
            self.setMaximumWidth(end_w)

class AddMedicationDialog(QDialog):
    def __init__(self, parent=None, med=None):
        super().__init__(parent)
        self.med = med
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(480, 720)
        
        # Main background container
        container = QFrame(self)
        container.setStyleSheet(f"""
            QFrame#MainContainer {{
                background: {C.BG};
                border-radius: 24px;
                border: 1px solid rgba(255,255,255,0.5);
            }}
        """)
        container.setObjectName("MainContainer")
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(container)
        
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 24, 0, 24)
        lay.setSpacing(16)
        
        # Header
        header_widget = QWidget()
        header_lay = QHBoxLayout(header_widget)
        header_lay.setContentsMargins(24, 0, 24, 0)
        title_text = " ✎ Edit Medication" if med else " ➕ Add Medication"
        title = QLabel(title_text)
        title.setStyleSheet("color: #735c00; font-size: 24px; font-weight: bold; background: transparent; border: none;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("color: #735c00; font-size: 20px; font-weight: bold; background: transparent; border: none;")
        close_btn.clicked.connect(self.reject)
        header_lay.addWidget(title)
        header_lay.addStretch()
        header_lay.addWidget(close_btn)
        lay.addWidget(header_widget)
        
        lay.addSpacing(16)
        
        # Scroll Area
        scroll = AnimatedScrollArea(
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        
        form_widget = QWidget()
        form_widget.setStyleSheet("background: transparent;")
        form_lay = QVBoxLayout(form_widget)
        # 24px left margin, 16px right margin (+ 8px scrollbar = 24px total right margin)
        form_lay.setContentsMargins(24, 0, 16, 0)
        form_lay.setSpacing(16)
        
        olive_color = "#735c00"
        label_style = f"color: {olive_color}; font-size: 16px; font-weight: bold; background: transparent; border: none;"
        input_style = """
            QLineEdit {
                background: rgba(255, 255, 255, 0.7);
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 16px;
                padding: 0 24px;
                font-size: 18px;
                color: #1f1b13;
            }
        """
        
        def _add_field(label_text, placeholder, is_combo=False, combo_items=None):
            lbl = QLabel(label_text)
            lbl.setStyleSheet(label_style)
            form_lay.addWidget(lbl)
            
            if is_combo:
                combo = AnimatedDropDown(
                    combo_items,
                    bg_color="rgba(255, 255, 255, 0.7)",
                    border="1px solid rgba(139,94,60,0.3)",
                    text_color="#1f1b13"
                )
                form_lay.addWidget(combo)
                return combo
            else:
                inp = QLineEdit()
                inp.setMinimumHeight(56)
                inp.setPlaceholderText(placeholder)
                inp.setStyleSheet(input_style)
                form_lay.addWidget(inp)
                return inp
                
        self._name = _add_field("Medication Name:", "e.g. Aspirin")
        self._dosage = _add_field("Dosage (e.g. 1 pill, 20mg):", "e.g. 1 pill, 20mg")
        self._freq = _add_field("Frequency:", "", is_combo=True, combo_items=["Daily", "Twice Daily", "Weekly", "As Needed"])
        self._time = _add_field("Time of Day:", "e.g. 08:00 AM")
        self._duration = _add_field("Duration / End Date:", "e.g. Continuous, or 2026-08-01")
        
        if self.med:
            self._name.setText(self.med.get("name", ""))
            self._dosage.setText(self.med.get("dosage", ""))
            if self.med.get("frequency"):
                self._freq.setCurrentText(self.med.get("frequency"))
            times = self.med.get("times", [])
            if times:
                self._time.setText(times[0])
            self._duration.setText(self.med.get("duration", ""))
        
        form_lay.addStretch()
        scroll.setWidget(form_widget)
        lay.addWidget(scroll)
        
        # Footer Actions
        footer_widget = QWidget()
        footer_lay = QHBoxLayout(footer_widget)
        footer_lay.setContentsMargins(24, 0, 24, 0)
        footer_lay.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(120, 56)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 2px solid #745853;
                border-radius: 16px;
                color: #745853;
                font-size: 18px;
                font-weight: bold;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        
        save_btn = QPushButton("Save")
        save_btn.setFixedSize(140, 56)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #d4af37;
                border: none;
                border-radius: 16px;
                color: #554300;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover { background: #e9c349; }
        """)
        save_btn.clicked.connect(self.accept)
        
        footer_lay.addWidget(cancel_btn)
        footer_lay.addWidget(save_btn)
        lay.addWidget(footer_widget)

    @property
    def name(self): return self._name.text()
    @property
    def dosage(self): return self._dosage.text()
    @property
    def freq(self): return self._freq.currentText()
    @property
    def time(self): return self._time.text()
    @property
    def duration(self): return self._duration.text()


# ==========================================
# Settings & Memory Page
# ==========================================
class AnimatedDropDown(QWidget):
    def __init__(self, items, parent=None, bg_color="rgba(253, 251, 247, 0.85)", border="none", text_color="#1f1b13", height=60):
        super().__init__(parent)
        self._items = items
        self._current_text = items[0] if items else ""
        self._is_open = False
        
        self.olive_color = "#857448"
        self.card_bg = bg_color
        self.card_border = border
        self.text_color = text_color
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        
        self._main_pill = QFrame()
        self._main_pill.setObjectName("MainPill")
        self._main_pill.setMinimumHeight(height)
        self._main_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._main_pill.mouseReleaseEvent = self._toggle_open
        
        main_pill_lay = QHBoxLayout(self._main_pill)
        main_pill_lay.setContentsMargins(20, 0, 20, 0)
        self._main_label = QLabel(self._current_text)
        self._main_label.setStyleSheet(f"color: {self.text_color}; font-size: 18px; font-weight: bold; background: transparent; border: none;")
        self._arrow_label = QLabel("v")
        self._arrow_label.setStyleSheet(f"color: {self.olive_color}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        
        main_pill_lay.addWidget(self._main_label)
        main_pill_lay.addStretch()
        main_pill_lay.addWidget(self._arrow_label)
        
        lay.addWidget(self._main_pill)
        
        # Dropdown container
        self._container = QFrame()
        self._container.setObjectName("DropContainer")
        self._container.setMaximumHeight(0)
        self._container.setMinimumHeight(0)
        
        self._container_lay = QVBoxLayout(self._container)
        self._container_lay.setContentsMargins(0, 0, 0, 0)
        self._container_lay.setSpacing(0)
        
        self._options = []
        for item in items:
            opt = QFrame()
            opt.setMinimumHeight(50)
            opt.setCursor(Qt.CursorShape.PointingHandCursor)
            opt.mouseReleaseEvent = lambda e, t=item: self._select_item(t)
            opt_lay = QHBoxLayout(opt)
            opt_lay.setContentsMargins(20, 0, 20, 0)
            
            lbl = QLabel(item)
            lbl.setStyleSheet(f"color: {self.text_color}; font-size: 18px; background: transparent; border: none;")
            
            opt_lay.addWidget(lbl)
            opt_lay.addStretch()
            
            self._container_lay.addWidget(opt)
            self._options.append((item, opt, lbl))
            
        lay.addWidget(self._container)
        
        # maximumHeight drives the open/close animation; keep the scaler off it.
        self._container.setProperty("_ts_ignore_geom", True)
        self._anim = QPropertyAnimation(self._container, b"maximumHeight")
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        self._update_styles()
        
    def currentText(self):
        return self._current_text
        
    def setCurrentText(self, text):
        for item in self._items:
            if item.lower() == text.lower():
                self._current_text = item
                self._main_label.setText(item)
                self._update_styles()
                break

    def _toggle_open(self, event):
        self._is_open = not self._is_open
        self._update_styles()
        
        if self._is_open:
            # We show all items EXCEPT the current one.
            # Read the row height off a live option rather than assuming 50 —
            # the text scaler grows those rows, and a hardcoded 50 here would
            # open the dropdown too short and clip the last item.
            row_h = self._options[0][1].minimumHeight() if self._options else 50
            target_h = (len(self._items) - 1) * row_h
            self._anim.setStartValue(self._container.height())
            self._anim.setEndValue(target_h)
        else:
            self._anim.setStartValue(self._container.height())
            self._anim.setEndValue(0)
        self._anim.start()

    def _select_item(self, text):
        self.setCurrentText(text)
        self._toggle_open(None)

    def _update_styles(self):
        if self._is_open:
            self._main_pill.setStyleSheet(f"""
                QFrame#MainPill {{
                    background: {self.card_bg};
                    border-top-left-radius: 16px;
                    border-top-right-radius: 16px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                    border: {self.card_border};
                    border-bottom: none;
                }}
            """)
            self._arrow_label.setText("^")
            self._container.setStyleSheet(f"""
                QFrame#DropContainer {{
                    background: {self.card_bg};
                    border-bottom-left-radius: 16px;
                    border-bottom-right-radius: 16px;
                    border: {self.card_border};
                    border-top: none;
                }}
            """)
        else:
            self._main_pill.setStyleSheet(f"""
                QFrame#MainPill {{
                    background: {self.card_bg};
                    border-radius: 16px;
                    border: {self.card_border};
                }}
            """)
            self._arrow_label.setText("v")
            self._container.setStyleSheet(f"QFrame#DropContainer {{ background: {self.card_bg}; border-radius: 0px; border: none; }}")
            
        # Determine visible options
        visible_options = []
        for item, opt, lbl in self._options:
            if item == self._current_text:
                opt.hide()
            else:
                opt.show()
                visible_options.append(opt)
                
        # Apply styles to visible options
        for i, opt in enumerate(visible_options):
            is_last = (i == len(visible_options) - 1)
            radius_css = "border-bottom-left-radius: 16px; border-bottom-right-radius: 16px;" if is_last else ""
            opt.setStyleSheet(f"background: transparent; border: none; {radius_css}")

class _DetentSlider(QSlider):
    """
    Horizontal slider that snaps to a small number of detents and jumps
    straight to wherever it is clicked, rather than paging one step at a time.
    An older user should be able to tap the far end and get there.
    """

    # Handle box: 30px wide plus a 2px border on each side. Kept in step with
    # the stylesheet below — the hit-test maths needs the real outer width.
    _HANDLE_W = 34

    def __init__(self, steps: int, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._steps = max(2, steps)
        self.setRange(0, self._steps - 1)
        self.setSingleStep(1)
        self.setPageStep(1)
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The control is the user's fixed reference point for what "big" and
        # "small" mean, so it must not resize along with everything else.
        self.setProperty("_ts_ignore", True)
        self.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: rgba(139,94,60,0.18);
                height: 8px;
                border-radius: 4px;
            }}
            QSlider::sub-page:horizontal {{
                background: {C.PRIMARY};
                height: 8px;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {C.WHITE};
                border: 2px solid {C.PRIMARY};
                width: 30px;
                height: 30px;
                margin: -11px 0px;
                border-radius: 15px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {C.SURFACE};
                border: 2px solid {C.PRIMARY_CTR};
            }}
        """)

    # ── geometry helpers ─────────────────────────────────────────────

    def _span(self) -> float:
        return max(1.0, float(self.width() - self._HANDLE_W))

    def _detent_x(self, index: int) -> float:
        return self._HANDLE_W / 2 + self._span() * (index / (self._steps - 1))

    def _value_at(self, x: float) -> int:
        pos = min(max(x - self._HANDLE_W / 2, 0.0), self._span())
        return round(pos / self._span() * (self._steps - 1))

    # ── interaction ──────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(e.position().x()))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(e.position().x()))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    # ── detent dots ──────────────────────────────────────────────────

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(C.SECONDARY, 110))
        cy = self.height() / 2
        handle_cx = self._detent_x(self.value())
        for i in range(self._steps):
            cx = self._detent_x(i)
            # Skip the dot the handle is sitting on, so it does not show
            # through as a smudge in the middle of the knob.
            if abs(cx - handle_cx) < self._HANDLE_W / 2:
                continue
            p.drawEllipse(QPointF(cx, cy), 3.0, 3.0)
        p.end()


class TextSizeSlider(QWidget):
    """
    The Text Size control: a small A on the left, a large A on the right, and a
    bar between them with five stops. Moving it resizes the text everywhere in
    the app straight away, and the choice is remembered.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ── A ————— A row ──
        row = QHBoxLayout()
        row.setSpacing(18)

        small_a = QLabel("A")
        small_a.setFont(_make_font(size=14, bold=True))
        small_a.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
        small_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        small_a.setFixedWidth(30)
        # The two A's are the scale the user reads the slider against. If they
        # grew with everything else the control would say nothing.
        small_a.setProperty("_ts_ignore", True)
        row.addWidget(small_a)

        self._slider = _DetentSlider(len(text_scale.STEPS))
        self._slider.setValue(text_scale.level())
        self._slider.valueChanged.connect(self._on_changed)
        row.addWidget(self._slider, stretch=1)

        big_a = QLabel("A")
        big_a.setFont(_make_font(size=30, bold=True))
        big_a.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        big_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big_a.setFixedWidth(40)
        big_a.setProperty("_ts_ignore", True)
        row.addWidget(big_a)

        lay.addLayout(row)

        # ── Current step name ──
        self._step_lbl = QLabel(text_scale.instance().label)
        self._step_lbl.setFont(_make_font(size=15, bold=True))
        self._step_lbl.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; border: none;")
        self._step_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._step_lbl)

        # ── Live preview ──
        # No special handling: this label is scaled by the same pass that
        # scales the rest of the app, so it always shows the truth.
        self._preview = QLabel("Your reminders and medicines will look like this.")
        self._preview.setFont(_make_font(size=15))
        self._preview.setWordWrap(True)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(f"""
            QLabel {{
                color: {C.ON_SURFACE};
                background: rgba(255,255,255,0.55);
                border: 1px solid rgba(139,94,60,0.15);
                border-radius: 14px;
                padding: 14px 18px;
            }}
        """)
        lay.addWidget(self._preview)

    def _transition_surface(self) -> QWidget | None:
        """The widget to photograph and zoom — the whole app canvas."""
        win = self.window()
        cw = getattr(win, "centralWidget", None)
        return (cw() if callable(cw) else None) or win

    def _on_changed(self, value: int):
        ts = text_scale.instance()
        # The size name updates instantly under the user's hand; the interface
        # itself grows through an animated transition once the drag settles.
        self._step_lbl.setText(text_scale.LABELS[value])
        ts.request_level(value, self._transition_surface())


# What the dropdown shows, against what `os_system` is allowed to be.
#
# These are two different vocabularies and they do not line up: the middle one
# reads "macOS" but every consumer — config.is_mac(), computer_control._get_os(),
# reminder, send_message, med_schedule — tests `== "mac"`. Saving the label
# lower-cased writes "macos", which matches none of them, and a Mac then falls
# through every branch to the Linux path: ctrl instead of command for copy and
# paste, and no scheduled reminders at all.
_OS_LABEL_TO_KEY = {"Windows": "windows", "macOS": "mac", "Linux": "linux"}
_OS_KEY_TO_LABEL = {v: k for k, v in _OS_LABEL_TO_KEY.items()}


class SettingsPage(QWidget):
    # Mail is sent on a worker thread; this carries the result back to the GUI
    # thread, where a QLabel may legally be touched.
    _mail_result = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background: transparent;")
        self._mail_result.connect(self._on_mail_result)
        
        scroll = AnimatedScrollArea(
            self,
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )
        
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        main_lay = QVBoxLayout(container)
        main_lay.setContentsMargins(40, 40, 40, 40)
        main_lay.setSpacing(24)
        
        olive_color = "#857448"
        card_bg = "rgba(253, 251, 247, 0.85)"
        
        title = QLabel("System Settings")
        title.setWordWrap(True)
        title.setFont(_make_font(size=28, bold=True))
        title.setStyleSheet(f"color: {C.ON_SURFACE};")
        main_lay.addWidget(title)

        # --- TEXT SIZE SECTION (first, it is the one people come here for) ---
        text_card = QWidget()
        text_card.setStyleSheet(f"""
            QWidget {{
                background: rgba(244, 239, 230, 0.9);
                border: 1px solid rgba(133,116,72,0.15);
                border-radius: 20px;
            }}
        """)
        text_lay = QVBoxLayout(text_card)
        text_lay.setContentsMargins(24, 24, 24, 24)
        text_lay.setSpacing(12)

        text_hdr_lay = QHBoxLayout()
        text_icon = QLabel("🔠")
        text_icon.setFont(_make_font(size=22, bold=True))
        text_icon.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        text_hdr_lay.addWidget(text_icon)

        text_title = QLabel("Text Size")
        text_title.setFont(_make_font(size=20, bold=True))
        text_title.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        text_hdr_lay.addWidget(text_title)
        text_hdr_lay.addStretch()
        text_lay.addLayout(text_hdr_lay)

        text_sub = QLabel("Drag towards the big A to make every word in the app larger.")
        text_sub.setFont(_make_font(size=14))
        text_sub.setWordWrap(True)
        text_sub.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        text_lay.addWidget(text_sub)

        self._text_size_slider = TextSizeSlider()
        text_lay.addWidget(self._text_size_slider)

        self._text_card = text_card      # the walkthrough spotlights this
        main_lay.addWidget(text_card)

        main_lay.addSpacing(16)

        # --- HIGH CONTRAST TEXT (directly below Text Size) ---
        hc_card = QWidget()
        hc_card.setStyleSheet(f"""
            QWidget {{
                background: rgba(244, 239, 230, 0.9);
                border: 1px solid rgba(133,116,72,0.15);
                border-radius: 20px;
            }}
        """)
        hc_lay = QVBoxLayout(hc_card)
        hc_lay.setContentsMargins(24, 24, 24, 24)
        hc_lay.setSpacing(12)

        hc_hdr = QHBoxLayout()
        hc_icon = QLabel("◐")
        hc_icon.setFont(_make_font(size=22, bold=True))
        hc_icon.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        hc_hdr.addWidget(hc_icon)

        hc_title = QLabel("High Contrast Text")
        hc_title.setFont(_make_font(size=20, bold=True))
        hc_title.setWordWrap(True)
        hc_title.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        hc_hdr.addWidget(hc_title)
        hc_hdr.addStretch()

        self._hc_btn = AnimatedButton()
        self._hc_btn.setFont(_make_font(size=16, bold=True))
        self._hc_btn.setFixedSize(120, 48)
        self._hc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hc_btn.clicked.connect(self._on_high_contrast_clicked)
        hc_hdr.addWidget(self._hc_btn)
        hc_lay.addLayout(hc_hdr)

        hc_sub = QLabel(
            "Makes every word in the app as dark as it can be, for easier "
            "reading. Nothing else changes — the buttons, pictures and colours "
            "of the app stay exactly the same.")
        hc_sub.setFont(_make_font(size=14))
        hc_sub.setWordWrap(True)
        hc_sub.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        hc_lay.addWidget(hc_sub)

        main_lay.addWidget(hc_card)
        self._refresh_hc_btn()

        main_lay.addSpacing(16)

        # --- OS SECTION ---
        os_label = QLabel("Computer Type")
        os_label.setFont(_make_font(size=14, bold=True))
        os_label.setStyleSheet(f"color: {olive_color};")
        main_lay.addWidget(os_label)
        
        self._os_combo = AnimatedDropDown(["Windows", "macOS", "Linux"])
        main_lay.addWidget(self._os_combo)
        
        main_lay.addSpacing(10)
        
        # --- API KEY SECTION ---
        api_label = QLabel("Gemini System Code")
        api_label.setFont(_make_font(size=14, bold=True))
        api_label.setStyleSheet(f"color: {olive_color};")
        main_lay.addWidget(api_label)
        
        api_lay = QHBoxLayout()
        api_lay.setSpacing(12)
        
        self._api_input = QLineEdit()
        self._api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_input.setMinimumHeight(60)
        self._api_input.setFont(_make_font(size=16))
        self._api_input.setStyleSheet(f"""
            QLineEdit {{
                background: {card_bg};
                border: none;
                border-radius: 16px;
                padding: 10px 20px;
                color: {C.ON_SURFACE};
            }}
            QLineEdit:focus {{
                border: 1px solid rgba(133,116,72,0.3);
            }}
        """)
        api_lay.addWidget(self._api_input)
        
        # Says "Show" in words, not just an eye. The field is dots by default,
        # and a bare 👁 on a 60px square reads as decoration — somebody who
        # wants to check the code they pasted in has no reason to think that
        # square is the way to see it.
        self._reveal_btn = AnimatedButton("👁  Show")
        self._reveal_btn.setMinimumSize(132, 60)
        self._reveal_btn.setFont(_make_font(size=15, bold=True))
        self._reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reveal_btn.setStyleSheet(f"""
            QPushButton {{
                background: {card_bg};
                color: {C.ON_SURFACE};
                border-radius: 16px;
                border: none;
                padding: 0 14px;
            }}
        """)
        self._reveal_btn.setToolTip("Show or hide the code you have typed in")
        self._reveal_btn.setAccessibleName("Show the Gemini system code")
        self._reveal_btn.clicked.connect(self._toggle_api)
        api_lay.addWidget(self._reveal_btn)
        main_lay.addLayout(api_lay)

        api_hint = QLabel(
            "This is the code that lets SIVA think. Press Show to check it, "
            "then Save.")
        api_hint.setFont(_make_font(size=13))
        api_hint.setWordWrap(True)
        api_hint.setStyleSheet(f"color: {C.ON_SURFACE_VAR};")
        main_lay.addWidget(api_hint)

        self._api_msg = QLabel("")
        self._api_msg.setFont(_make_font(size=13, bold=True))
        self._api_msg.setWordWrap(True)
        self._api_msg.setStyleSheet(f"color: {C.ON_SURFACE_VAR};")
        self._api_msg.hide()
        main_lay.addWidget(self._api_msg)

        main_lay.addSpacing(16)

        # --- SAVE BUTTON ---
        save_btn = AnimatedButton("Save Gemini ID")
        save_btn.setMinimumHeight(56)
        save_btn.setFont(_make_font(size=16, bold=True))
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: #D9B340; 
                color: {C.ON_SURFACE}; 
                border-radius: 16px; 
                padding: 10px 40px; 
                border: none;
            }}
        """)
        save_btn.clicked.connect(self._save_config)
        save_lay = QHBoxLayout()
        save_lay.addWidget(save_btn)
        save_lay.addStretch()
        main_lay.addLayout(save_lay)
        
        main_lay.addSpacing(16)
        
        # --- MEMORY SECTION ---
        mem_card = QWidget()
        mem_card.setStyleSheet(f"""
            QWidget {{
                background: rgba(244, 239, 230, 0.9);
                border: 1px solid rgba(133,116,72,0.15);
                border-radius: 20px;
            }}
        """)
        mem_lay = QVBoxLayout(mem_card)
        mem_lay.setContentsMargins(24, 24, 24, 24)
        mem_lay.setSpacing(12)
        
        mem_header_lay = QHBoxLayout()
        mem_icon = QLabel("⚙")
        mem_icon.setFont(_make_font(size=22, bold=True))
        mem_icon.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        mem_header_lay.addWidget(mem_icon)
        
        mem_title = QLabel("What System Remembers")
        mem_title.setWordWrap(True)
        mem_title.setFont(_make_font(size=20, bold=True))
        mem_title.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        mem_header_lay.addWidget(mem_title)
        mem_header_lay.addStretch()
        mem_lay.addLayout(mem_header_lay)
        
        self._mem_list = QTextEdit()
        self._mem_list.setReadOnly(True)
        self._mem_list.setMinimumHeight(120)
        self._mem_list.setFont(_make_font(size=16))
        # Remove background and borders for the text area itself so it blends into the card
        self._mem_list.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                border: none;
                color: {C.ON_SURFACE};
            }}""")
        mem_lay.addWidget(self._mem_list)
        
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(12)
        
        refresh_btn = AnimatedButton("Refresh")
        refresh_btn.setMinimumHeight(44)
        refresh_btn.setFont(_make_font(size=14, bold=True))
        refresh_btn.setStyleSheet(f"""
            QPushButton {{ 
                background: rgba(133,116,72,0.1); 
                color: {olive_color}; 
                border-radius: 12px; 
                border: none; 
            }}
        """)
        refresh_btn.clicked.connect(self._load_memory)
        btn_lay.addWidget(refresh_btn)
        
        clear_mem_btn = AnimatedButton("Clear")
        clear_mem_btn.setMinimumHeight(44)
        clear_mem_btn.setFont(_make_font(size=14, bold=True))
        clear_mem_btn.setStyleSheet(f"""
            QPushButton {{ 
                background: rgba(186,26,26,0.06); 
                color: {C.ERROR}; 
                border-radius: 12px; 
                border: none; 
            }}
        """)
        clear_mem_btn.clicked.connect(self._clear_memory)
        btn_lay.addWidget(clear_mem_btn)
        
        mem_lay.addLayout(btn_lay)
        main_lay.addWidget(mem_card)

        # --- SIGN IN SECURITY (last card — found by scrolling down) ---
        auth_card = QWidget()
        auth_card.setStyleSheet(f"""
            QWidget {{
                background: rgba(244, 239, 230, 0.9);
                border: 1px solid rgba(133,116,72,0.15);
                border-radius: 20px;
            }}
        """)
        auth_lay = QVBoxLayout(auth_card)
        auth_lay.setContentsMargins(24, 24, 24, 24)
        auth_lay.setSpacing(12)

        auth_hdr = QHBoxLayout()
        auth_icon = QLabel("🔒")
        auth_icon.setFont(_make_font(size=22, bold=True))
        auth_icon.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        auth_hdr.addWidget(auth_icon)
        auth_title = QLabel("Sign In Security")
        auth_title.setFont(_make_font(size=20, bold=True))
        auth_title.setWordWrap(True)
        auth_title.setStyleSheet(f"color: {olive_color}; border: none; background: transparent;")
        auth_hdr.addWidget(auth_title)
        auth_hdr.addStretch()
        auth_lay.addLayout(auth_hdr)

        auth_sub = QLabel("How SIVA checks it is you when it starts up.")
        auth_sub.setFont(_make_font(size=14))
        auth_sub.setWordWrap(True)
        auth_sub.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        auth_lay.addWidget(auth_sub)

        # Face recognition is never offered on its own — see core/prefs.py.
        #
        # The description is a word-wrapped QLabel inside the button, not a
        # second line of the button's own text. QPushButton does not wrap, so
        # its minimumSizeHint is the full unwrapped string — measured at
        # 1252px for the first option, which propagated up the layout and gave
        # the whole Settings page a horizontal scrollbar at the default text
        # size. Same trap as the wide-QLabel one, wearing a button.
        self._auth_btns = {}
        self._auth_btn_lbls = {}
        for key, title, detail in (
            (prefs.AUTH_FACE_PASSWORD, "Face ID + Password",
             "Look at the camera. If it does not know you, type your password."),
            (prefs.AUTH_PASSWORD, "Password only",
             "No camera. Type your password to get in."),
            (prefs.AUTH_OFF, "No sign in at all",
             "SIVA opens straight away. Anyone at this computer can open it."),
        ):
            btn = CardButton()
            btn.setMinimumHeight(64)
            # Caps how far a long line can push the page open. Precedent:
            # AnimatedScrollArea.setMinimumWidth(160).
            btn.setMinimumWidth(160)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._on_auth_mode_clicked(k))

            b_lay = QVBoxLayout(btn)
            b_lay.setContentsMargins(16, 10, 16, 10)
            b_lay.setSpacing(2)
            t_lbl = QLabel(title)
            t_lbl.setFont(_make_font(size=14, bold=True))
            d_lbl = QLabel(detail)
            d_lbl.setFont(_make_font(size=13))
            d_lbl.setWordWrap(True)
            for lbl in (t_lbl, d_lbl):
                # Otherwise the label eats the click and the button never fires.
                lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                b_lay.addWidget(lbl)

            self._auth_btns[key] = btn
            self._auth_btn_lbls[key] = (t_lbl, d_lbl)
            auth_lay.addWidget(btn)

        self._auth_note = QLabel("")
        self._auth_note.setFont(_make_font(size=13))
        self._auth_note.setWordWrap(True)
        auth_lay.addWidget(self._auth_note)

        self._build_security_groups(auth_lay, olive_color)

        self._security_card = auth_card
        main_lay.addWidget(auth_card)
        self._refresh_auth_btns()

        main_lay.addSpacing(16)
        self._build_permissions_card(main_lay, olive_color)
        main_lay.addSpacing(16)
        self._build_developer_card(main_lay, olive_color)

        main_lay.addStretch()

        self._scroll = scroll          # Part 4 scrolls this card into view
        scroll.setWidget(container)
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(scroll)

    # ═════════════════════════════════════════════════════════════
    # PERMISSIONS + DEVELOPER SETTINGS — the last two cards
    # ═════════════════════════════════════════════════════════════

    def _settings_card(self, olive: str, icon: str, title: str, sub: str):
        """A card in this page's house style. Returns (card, layout)."""
        card = QWidget()
        card.setStyleSheet(f"""
            QWidget {{
                background: rgba(244, 239, 230, 0.9);
                border: 1px solid rgba(133,116,72,0.15);
                border-radius: 20px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)

        hdr = QHBoxLayout()
        icon_lbl = QLabel(icon)
        icon_lbl.setFont(_make_font(size=22, bold=True))
        icon_lbl.setStyleSheet(
            f"color: {olive}; border: none; background: transparent;")
        hdr.addWidget(icon_lbl)
        title_lbl = QLabel(title)
        title_lbl.setFont(_make_font(size=20, bold=True))
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"color: {olive}; border: none; background: transparent;")
        hdr.addWidget(title_lbl, stretch=1)
        lay.addLayout(hdr)

        sub_lbl = QLabel(sub)
        sub_lbl.setFont(_make_font(size=14))
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        lay.addWidget(sub_lbl)
        return card, lay

    def _build_permissions_card(self, main_lay, olive: str):
        """Everything SIVA has been allowed to do, and a way to take it back.

        The counterpart to "asked once and never again": without somewhere to
        change the answer, "once" means "forever", and a permission granted by a
        mis-click would be unreachable.

        Each row is one tall button holding wrapped labels rather than a name
        with a control beside it. A right-hand control needs a fixed width, and
        thirteen rows of fixed-width control plus non-wrapping name is exactly
        the shape that gave this page a horizontal scrollbar once already (see
        the note on the sign-in buttons above). It is also a much larger thing
        to press, which suits who is pressing it.
        """
        card, lay = self._settings_card(
            olive, "🔐", "What SIVA is allowed to do",
            "SIVA asks the first time it needs one of these, and then never "
            "again. Press any of them here to change your mind.")

        # Allow everything, in one press. Thirteen separate cards is the right
        # pace for somebody meeting SIVA for the first time, and the wrong one
        # for somebody who has already decided — setting a machine up, or
        # showing it to a room. The single "off" switch lives in Developer
        # Settings as "Forget every permission"; this is its opposite, and it
        # sits here because this is the page listing what would change.
        self._allow_all_btn = self._sec_button("✓   Allow everything", "primary")
        self._allow_all_btn.clicked.connect(self._on_allow_all_clicked)
        lay.addWidget(self._allow_all_btn)

        self._allow_all_msg = self._sec_status(lay)

        self._sec_divider(lay)

        self._perm_rows: dict[str, tuple] = {}
        for key in permissions.ORDER:
            perm = permissions.describe(key)
            if perm is None:
                continue

            btn = CardButton()
            btn.setMinimumHeight(64)
            btn.setMinimumWidth(160)      # caps how far a long line can push
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _=False, k=key: self._on_permission_row_clicked(k))

            b_lay = QVBoxLayout(btn)
            b_lay.setContentsMargins(16, 10, 16, 10)
            b_lay.setSpacing(2)

            name_lbl = QLabel(f"{perm.icon}  {perm.name}")
            name_lbl.setFont(_make_font(size=14, bold=True))
            detail_lbl = QLabel(perm.settings_line)
            detail_lbl.setFont(_make_font(size=13))
            state_lbl = QLabel("")
            state_lbl.setFont(_make_font(size=13, bold=True))
            for lbl in (name_lbl, detail_lbl, state_lbl):
                lbl.setWordWrap(True)
                # Otherwise the label eats the click and the button never fires.
                lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                b_lay.addWidget(lbl)

            self._perm_rows[key] = (btn, name_lbl, detail_lbl, state_lbl)
            lay.addWidget(btn)

        self._perm_card = card
        self._perm_fit_w = -1
        main_lay.addWidget(card)
        self.refresh_permission_rows()
        try:
            # Bigger text means taller lines, so the measured heights are stale
            # the moment the size changes.
            text_scale.instance().changed.connect(
                lambda _l: self._queue_permission_refit())
        except Exception:
            pass

    def _refit_permission_rows(self):
        """Re-measure the wrapped labels in the permission rows, honestly.

        Without this the rows come out three times taller than their own text —
        measured, 339px to hold 133px of words, and thirteen of them.

        `fit_wrapped_labels`, which this page's scroll area runs over its whole
        content tree on every resize, stores each label's height as a *minimum*,
        and QLabel.heightForWidth never returns less than the minimum it already
        has. Measure once while the column is still narrow — which is exactly
        what the first layout pass does — and the inflated answer is permanent,
        because every later measurement is floored by the wrong one. Three
        wrapped labels in a row compounds it three times over. `_refit_wrapped`
        clears the minimums first, which is the only way the number comes back
        down.

        Idempotent at a settled width: clearing and re-measuring the same text
        at the same width gives the same answer, reports no change, and asks
        for no further layout. That is what makes it safe to run more than once.
        """
        self._perm_refit_queued = False
        # The whole scroll content, not just the permissions card.
        #
        # The ratchet described above is not special to those thirteen rows —
        # AnimatedScrollArea runs fit_wrapped_labels over its entire content
        # tree, so every wrapping label on this page gets a minimum height
        # measured on the first pass, while the column is still narrow, and
        # nothing ever lowers it again. Clearing only this one card left the
        # rest of the page holding those first-pass numbers: single lines of
        # help text reserving several hundred pixels each, which is the empty
        # space between the code box, its hint and the Save button.
        host = getattr(self, "_scroll", None)
        host = host.widget() if host is not None else None
        if host is None:
            host = getattr(self, "_perm_card", None)
        if host is None:
            return
        try:
            if _refit_wrapped(host):
                host.updateGeometry()
                lay = host.layout()
                if lay is not None:
                    lay.invalidate()
        except RuntimeError:
            pass

    def _queue_permission_refit(self):
        """Re-fit once this round of layout has finished, and once more after.

        Never inline. AnimatedScrollArea re-fits the whole content tree from its
        own resize handler, and whichever of the two runs last decides the
        heights — so a re-fit done during the same pass can be overwritten by a
        measurement taken at a width that has since changed. The second, later
        pass is the one that catches the deferred clamp the scroll area queues
        for itself; it costs nothing when the first already got it right.
        """
        if getattr(self, "_perm_refit_queued", False):
            return
        self._perm_refit_queued = True
        QTimer.singleShot(0, self._refit_permission_rows)
        QTimer.singleShot(80, self._refit_permission_rows)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        scroll = getattr(self, "_scroll", None)
        host = scroll.widget() if scroll is not None else None
        if host is None:
            return
        # Only on a real width change, as _refit_wrapped's docstring requires:
        # re-fitting changes heights, which can change the scrollbar, which
        # changes the width — unguarded, that chases its own tail.
        w = host.width()
        if w == getattr(self, "_perm_fit_w", -1):
            return
        self._perm_fit_w = w
        self._queue_permission_refit()

    def refresh_permission_rows(self):
        """Redraw each row from the store. Called after any answer."""
        self._refresh_allow_all_btn()
        rows = getattr(self, "_perm_rows", None)
        if not rows:
            return
        for key, (btn, name_lbl, detail_lbl, state_lbl) in rows.items():
            try:
                allowed = permissions.is_granted(key)
            except Exception:
                allowed = False
            # A tick and a cross as well as a colour: a good number of older
            # users cannot reliably tell these two colours apart, which is the
            # same reason the Toast carries a symbol.
            state_lbl.setText("✓  Allowed — press to turn this off" if allowed
                              else "✕  Not allowed — SIVA will ask when it needs this")
            state_lbl.setStyleSheet(
                f"color: {C.PRIMARY if allowed else C.ON_SURFACE_VAR}; "
                "background: transparent; border: none;")
            for lbl in (name_lbl, detail_lbl):
                lbl.setStyleSheet(
                    f"color: {C.ON_SURFACE if allowed else C.ON_SURFACE_VAR}; "
                    "background: transparent; border: none;")
            try:
                btn.setAccessibleName(
                    f"{permissions.describe(key).name} — "
                    f"{'allowed' if allowed else 'not allowed'}")
            except Exception:
                pass
        # The state line just changed length, so the heights measured for the
        # old wording no longer apply.
        self._queue_permission_refit()

    def _refresh_allow_all_btn(self):
        """Dim the button once there is nothing left for it to do.

        Also clears the line underneath, because that line describes the last
        press — and after a row is switched off by hand, or Developer Settings
        clears the lot, it is describing a state that no longer exists.
        """
        btn = getattr(self, "_allow_all_btn", None)
        if btn is None:
            return
        try:
            remaining = [k for k in permissions.ORDER
                         if not permissions.is_granted(k)]
        except Exception:
            remaining = list(permissions.ORDER)
        btn.setEnabled(bool(remaining))
        btn.setText("✓   Allow everything" if remaining
                    else "✓   Everything is allowed")
        msg = getattr(self, "_allow_all_msg", None)
        if msg is not None:
            msg.setText("")

    def _on_allow_all_clicked(self):
        """Grant every capability at once.

        No confirmation card, unlike its opposite in Developer Settings. That
        one is asked for because it destroys answers somebody gave; this one
        adds them, every row below it says plainly what changed, and any single
        one can be pressed straight back off. A dialog in front of it would only
        be in the way of the thing it is for.
        """
        before = permissions.granted_keys()
        missing = [k for k in permissions.ORDER if k not in before]
        if not missing:
            self._allow_all_msg.setText("Everything is already allowed.")
            return

        failed = [k for k in missing if not permissions.grant(k)]
        self.refresh_permission_rows()

        if failed:
            self._allow_all_msg.setText(
                f"⚠  Could not save {len(failed)} of them. Check that SIVA is "
                "allowed to write to its own folder.")
            return

        n = len(missing)
        self._allow_all_msg.setText(
            f"✓  Allowed {n} more — SIVA can now do all "
            f"{len(permissions.ORDER)} of these.")
        try:
            self.window().show_toast(
                f"All {len(permissions.ORDER)} permissions allowed. "
                "SIVA will not ask again.")
        except (AttributeError, RuntimeError):
            pass

    def _on_permission_row_clicked(self, key: str):
        win = self.window()
        if permissions.is_granted(key):
            permissions.revoke(key)
            self.refresh_permission_rows()
            try:
                win.show_toast(
                    f"{permissions.describe(key).name} — turned off. "
                    "SIVA will ask again the next time it needs this.")
            except (AttributeError, RuntimeError):
                pass
            return
        try:
            win.ask_permission(key)
        except (AttributeError, RuntimeError):
            pass

    def _build_developer_card(self, main_lay, olive: str):
        """The demo button — last card on the page, as asked for.

        It exists so the first run can be shown to someone on a machine that has
        already had its first run. Clearing the permissions is half the point:
        a walkthrough where every card is already answered demonstrates nothing.
        """
        card, lay = self._settings_card(
            olive, "🛠", "Developer Settings",
            "Only needed for showing SIVA to somebody. Nothing here changes how "
            "SIVA works day to day.")

        demo = self._sec_button("▶   Show the first-time walkthrough", "primary")
        demo.clicked.connect(self._on_demo_clicked)
        lay.addWidget(demo)

        demo_note = QLabel(
            "Puts SIVA back to how it is on the very first day: every "
            "permission is forgotten, and the twelve-step tour plays from the "
            "beginning. Your reminders, medicines and settings are not touched.")
        demo_note.setFont(_make_font(size=13))
        demo_note.setWordWrap(True)
        demo_note.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        lay.addWidget(demo_note)

        self._sec_divider(lay)

        forget = self._sec_button("Forget every permission", "danger")
        forget.clicked.connect(self._on_forget_permissions_clicked)
        lay.addWidget(forget)

        forget_note = QLabel(
            "The same clearing out, without the tour. SIVA will ask again the "
            "next time it needs any of them.")
        forget_note.setFont(_make_font(size=13))
        forget_note.setWordWrap(True)
        forget_note.setStyleSheet(
            f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        lay.addWidget(forget_note)

        self._dev_status = self._sec_status(lay)

        self._dev_card = card
        main_lay.addWidget(card)

    def _confirm_reset(self, title: str, detail: str) -> bool:
        """Ask before clearing, and name what is about to go.

        Same shape as Clear Passed on the reminders page: a destructive button
        on a page somebody is browsing has to say what it will do before it
        does it.
        """
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(detail)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton("Yes, clear them", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setStyleSheet(f"""
            QMessageBox {{ background: {C.BG}; }}
            QLabel {{ color: {C.ON_SURFACE}; font-size: 14px; }}
            QPushButton {{
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 6px;
                padding: 6px 14px;
                color: {C.ON_SURFACE};
            }}""")
        box.exec()
        return box.clickedButton() is yes

    def _on_demo_clicked(self):
        n = len(permissions.granted_keys())
        if not self._confirm_reset(
                "Show the walkthrough",
                f"This forgets {n} permission{'' if n == 1 else 's'} SIVA has "
                "been given and plays the twelve-step tour from the start.\n\n"
                "Your reminders, medicines and settings are not touched."):
            return
        win = self.window()
        try:
            win.reset_first_run(run_tour=True)
        except (AttributeError, RuntimeError):
            return
        self._dev_status.setText("")

    def _on_forget_permissions_clicked(self):
        n = len(permissions.granted_keys())
        if not self._confirm_reset(
                "Forget every permission",
                f"SIVA has been given {n} permission{'' if n == 1 else 's'}. "
                "This forgets all of them.\n\n"
                "It will ask again the next time it needs any of them."):
            return
        permissions.reset_all()
        self.refresh_permission_rows()
        self._dev_status.setText("✓  All permissions forgotten.")

    # ═════════════════════════════════════════════════════════════
    # SECURITY GROUPS — password, recovery email, face
    # ═════════════════════════════════════════════════════════════

    def _sec_divider(self, lay):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: rgba(133,116,72,0.20); border: none; max-height: 1px;")
        lay.addWidget(line)

    def _sec_heading(self, lay, text: str, colour: str):
        lbl = QLabel(text)
        lbl.setFont(_make_font(size=16, bold=True))
        # Wrapped so the heading cannot set a minimum width for the whole
        # column at the larger text sizes — a non-wrapping label propagates
        # its full single-line width upward and scrolls the page sideways.
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {colour}; border: none; background: transparent;")
        lay.addWidget(lbl)

    def _sec_field(self, lay, placeholder: str, secret: bool = False) -> QLineEdit:
        f = QLineEdit()
        f.setPlaceholderText(placeholder)
        f.setMinimumHeight(48)
        f.setFont(_make_font(size=14))
        if secret:
            f.setEchoMode(QLineEdit.EchoMode.Password)
        f.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.75);
                color: {C.ON_SURFACE};
                border: 1px solid rgba(133,116,72,0.30);
                border-radius: 12px;
                padding: 6px 14px;
            }}
            QLineEdit:focus {{ border: 2px solid {C.PRIMARY}; }}
        """)
        lay.addWidget(f)
        return f

    def _restyle_sec_button(self, b, kind: str = "normal"):
        """Repaint one of these buttons in a different weight.

        Split out of `_sec_button` because the face confirm button is shared
        between re-recording (ordinary) and forgetting (destructive), and has
        to change colour with whichever one armed it.
        """
        if kind == "danger":
            bg, fg, bd = "rgba(186,26,26,0.08)", C.ERROR, C.ERROR
        elif kind == "primary":
            bg, fg, bd = "rgba(55,103,87,0.12)", C.PRIMARY, C.PRIMARY
        else:
            bg, fg, bd = "rgba(255,255,255,0.60)", C.ON_SURFACE_VAR, "rgba(133,116,72,0.30)"
        b.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {fg};
                border: 2px solid {bd}; border-radius: 12px;
                padding: 6px 18px;
            }}""")

    def _sec_button(self, text: str, kind: str = "normal") -> AnimatedButton:
        b = AnimatedButton(text)
        b.setMinimumHeight(46)
        b.setFont(_make_font(size=14, bold=True))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restyle_sec_button(b, kind)
        return b

    def _sec_status(self, lay) -> QLabel:
        s = QLabel("")
        s.setFont(_make_font(size=13))
        s.setWordWrap(True)
        s.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        lay.addWidget(s)
        return s

    def _build_security_groups(self, lay, olive: str):
        # ── a. Change password ───────────────────────────────────
        self._sec_divider(lay)
        self._sec_heading(lay, "🔑  Your password", olive)

        self._reset_banner = QLabel("")
        self._reset_banner.setFont(_make_font(size=14, bold=True))
        self._reset_banner.setWordWrap(True)
        self._reset_banner.setStyleSheet(f"""
            color: {C.ERROR};
            background: rgba(186,26,26,0.08);
            border: 2px solid {C.ERROR};
            border-radius: 12px;
            padding: 12px;
        """)
        self._reset_banner.hide()
        lay.addWidget(self._reset_banner)

        # Folded away until asked for. Three password boxes sitting open is a
        # standing invitation to fill them in, on a page most people open to
        # do something else entirely — and for the person this app is for, a
        # form that is visible reads as a form that wants answering.
        self._pw_toggle = self._sec_button("Change my password", "primary")
        self._pw_toggle.clicked.connect(self._on_change_password_clicked)
        lay.addWidget(self._pw_toggle)

        self._pw_panel = QWidget()
        self._pw_panel.setStyleSheet("background: transparent; border: none;")
        pp = QVBoxLayout(self._pw_panel)
        pp.setContentsMargins(0, 8, 0, 0)
        pp.setSpacing(10)
        self._pw_current = self._sec_field(pp, "Your password now", secret=True)
        self._pw_new     = self._sec_field(pp, "New password", secret=True)
        self._pw_again   = self._sec_field(pp, "Type the new password again", secret=True)
        save_pw = self._sec_button("Save my new password", "primary")
        save_pw.clicked.connect(self._on_change_password)
        pp.addWidget(save_pw)
        lay.addWidget(self._pw_panel)
        # Hidden AFTER addWidget — reparenting clears the explicit-hide flag,
        # the same trap the email panel below documents.
        self._pw_panel.hide()

        self._pw_msg = self._sec_status(lay)

        # ── b. The user's own email address ──────────────────────
        #
        # Only ever one email idea on this page. SIVA's own sending account
        # used to sit right below this, which read as though the user were
        # being asked for two addresses — and it put an app password, which
        # grants full access to a mailbox, on a screen an older adult browses.
        # It lives in config/api_keys.json now, beside the Gemini keys.
        self._sec_divider(lay)
        self._sec_heading(lay, "✉  Your email address", olive)

        why = QLabel("If you forget your password, SIVA sends a code here so "
                     "you can get back in.")
        why.setFont(_make_font(size=13))
        why.setWordWrap(True)
        why.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")
        lay.addWidget(why)

        self._email_state = QLabel("")
        self._email_state.setFont(_make_font(size=14))
        self._email_state.setWordWrap(True)
        self._email_state.setStyleSheet(
            f"color: {C.ON_SURFACE}; border: none; background: transparent;")
        lay.addWidget(self._email_state)

        row = QHBoxLayout()
        row.setSpacing(12)
        self._email_set_btn = self._sec_button("Set my email", "primary")
        self._email_set_btn.clicked.connect(self._on_change_email_clicked)
        row.addWidget(self._email_set_btn)
        self._email_change_btn = self._sec_button("Change this email")
        self._email_change_btn.clicked.connect(self._on_change_email_clicked)
        row.addWidget(self._email_change_btn)
        # No password for a test: it proves the plumbing to an address already
        # on file and gives nothing away to someone who should not be here.
        self._email_test_btn = self._sec_button("Send a test email")
        self._email_test_btn.clicked.connect(self._on_test_email)
        row.addWidget(self._email_test_btn)
        lay.addLayout(row)

        self._email_panel = QWidget()
        self._email_panel.setStyleSheet("background: transparent; border: none;")
        ep = QVBoxLayout(self._email_panel)
        ep.setContentsMargins(0, 8, 0, 0)
        ep.setSpacing(10)
        # Address, password, save. No code to fetch and type back.
        #
        # Sending a code here and asking for it back is how a developer proves
        # an address is real, and it is meaningless to the person this app is
        # for: they would have to leave SIVA, find their inbox, read a number,
        # come back and type it — and many simply stop at that point. The code
        # only ever guarded against a typo anyway; the password below is what
        # actually stops someone redirecting recovery to their own inbox.
        # A typo is caught better by sending a message and asking them to look.
        self._email_new = self._sec_field(ep, "your email address")
        self._email_pw  = self._sec_field(ep, "Your password, to confirm it is you", secret=True)
        save_email = self._sec_button("Save this email", "primary")
        save_email.clicked.connect(self._on_save_email)
        ep.addWidget(save_email)
        lay.addWidget(self._email_panel)
        # AFTER addWidget, not before: adding a widget to a layout reparents
        # it, and Qt drops the explicit-hide flag when a widget is reparented.
        # Hidden first, this whole form came back the moment the page was
        # shown — which is what made the section look like two rival forms.
        self._email_panel.hide()

        self._email_msg = self._sec_status(lay)

        # ── c. Face ID ───────────────────────────────────────────
        self._sec_divider(lay)
        self._sec_heading(lay, "😊  Your face", olive)
        self._face_state = QLabel("")
        self._face_state.setFont(_make_font(size=14))
        self._face_state.setWordWrap(True)
        self._face_state.setStyleSheet(
            f"color: {C.ON_SURFACE}; border: none; background: transparent;")
        lay.addWidget(self._face_state)

        frow = QHBoxLayout()
        frow.setSpacing(12)
        rerec = self._sec_button("Record my face again", "primary")
        rerec.clicked.connect(lambda: self._on_face_action_clicked("rerecord"))
        frow.addWidget(rerec)
        forget = self._sec_button("Forget my face", "danger")
        forget.clicked.connect(lambda: self._on_face_action_clicked("forget"))
        frow.addWidget(forget)
        lay.addLayout(frow)

        # Same fold as the password section above: the box only appears once
        # one of the two buttons has been pressed, and it says which action it
        # is about to confirm. A password box standing open under two buttons
        # gives no clue which one it belongs to.
        self._face_panel = QWidget()
        self._face_panel.setStyleSheet("background: transparent; border: none;")
        fp = QVBoxLayout(self._face_panel)
        fp.setContentsMargins(0, 8, 0, 0)
        fp.setSpacing(10)
        self._face_pw = self._sec_field(
            fp, "Your password, to confirm it is you", secret=True)
        # Enter in the password box does the same as pressing the button.
        self._face_pw.returnPressed.connect(self._on_face_confirm)
        self._face_confirm = self._sec_button("Confirm", "primary")
        self._face_confirm.clicked.connect(self._on_face_confirm)
        fp.addWidget(self._face_confirm)
        lay.addWidget(self._face_panel)
        self._face_panel.hide()          # after addWidget, as above

        self._face_pending = None
        self._face_msg = self._sec_status(lay)

        self._refresh_security_state()
        
        self._load_config()
        self._load_memory()
        
    # ── High contrast text ───────────────────────────────────────

    def _on_high_contrast_clicked(self):
        text_scale.set_high_contrast(not text_scale.high_contrast())
        self._refresh_hc_btn()

    def _refresh_hc_btn(self):
        """The button says what it *is*, not what it would do — an ON/OFF
        state is what an older user reads correctly at a glance."""
        on = text_scale.high_contrast()
        self._hc_btn.setText("ON" if on else "OFF")
        self._hc_btn.setToolTip(
            "High contrast text is on — press to turn it off" if on
            else "Press to turn high contrast text on")
        if on:
            self._hc_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRIMARY};
                    color: #FFFFFF;
                    border: 2px solid {C.PRIMARY};
                    border-radius: 14px;
                }}""")
        else:
            self._hc_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.60);
                    color: {C.ON_SURFACE_VAR};
                    border: 2px solid rgba(133,116,72,0.35);
                    border-radius: 14px;
                }}""")

    # ── Sign in security ─────────────────────────────────────────

    def _on_auth_mode_clicked(self, key: str):
        prefs.set_auth_mode(key)
        self._refresh_auth_btns()

    def _refresh_auth_btns(self):
        current = prefs.auth_mode()
        for key, btn in self._auth_btns.items():
            chosen = (key == current)
            off = (key == prefs.AUTH_OFF)
            if chosen and off:
                border, bg, fg = C.ERROR, "rgba(186,26,26,0.10)", C.ERROR
            elif chosen:
                border, bg, fg = C.PRIMARY, "rgba(55,103,87,0.12)", C.PRIMARY
            else:
                border, bg, fg = "rgba(133,116,72,0.30)", "rgba(255,255,255,0.55)", C.ON_SURFACE_VAR
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    color: {fg};
                    border: 2px solid {border};
                    border-radius: 14px;
                    text-align: left;
                }}""")
            # The title and detail are child QLabels, and a `QPushButton {...}`
            # rule does not select them, so they are coloured directly.
            for lbl in self._auth_btn_lbls.get(key, ()):
                lbl.setStyleSheet(
                    f"color: {fg}; background: transparent; border: none;")

        # Said plainly rather than hidden behind a dialog: the setting was
        # asked for, so it is given — but what it costs is stated.
        if current == prefs.AUTH_OFF:
            self._auth_note.setText(
                "⚠  Sign in is switched off. SIVA will open with no check at "
                "all, and anyone who can reach this computer can read your "
                "reminders, messages and health information.\n"
                "Takes effect the next time you open SIVA.")
            self._auth_note.setStyleSheet(
                f"color: {C.ERROR}; border: none; background: transparent;")
        else:
            self._auth_note.setText("Takes effect the next time you open SIVA.")
            self._auth_note.setStyleSheet(
                f"color: {C.ON_SURFACE_VAR}; border: none; background: transparent;")

    # ── security handlers ────────────────────────────────────────

    def _sec_say(self, label: QLabel, text: str, ok: bool = True):
        colour = C.PRIMARY if ok else C.ERROR
        label.setText(text)
        label.setStyleSheet(
            f"color: {colour}; border: none; background: transparent;")

    def _read_secrets(self) -> dict:
        from core import recovery
        return recovery._read_config()

    def _write_secrets(self, cfg: dict) -> bool:
        from core import recovery
        return recovery._write_config(cfg)

    def _password_ok(self, typed: str) -> bool:
        from core.auth import _verify_password
        cfg = self._read_secrets()
        return _verify_password(typed or "",
                                cfg.get("app_password_hash", ""),
                                cfg.get("app_password_salt", ""))

    def _has_password_set(self) -> bool:
        return bool(self._read_secrets().get("app_password_hash"))

    def showEvent(self, event):
        """
        Re-read everything each time the page is opened.

        This used to run only while the page was being built — during startup,
        before the user had entered anything. So a recovery email set from the
        prompt on the dashboard left "Send a test email" disabled from
        construction, and pressing it did nothing at all, silently. Anything
        shown here can change while the page is off screen.
        """
        super().showEvent(event)
        try:
            self._refresh_security_state()
        except (RuntimeError, AttributeError):
            pass

    def _refresh_security_state(self):
        from core import recovery

        addr = recovery.recovery_email()
        has = recovery.has_recovery_email()
        if has:
            # Set, so stop asking: say what it is and offer the two things a
            # user might actually want to do with it.
            self._email_state.setText(f"Codes go to:  {recovery.mask_email(addr)}")
            self._email_state.setStyleSheet(
                f"color: {C.ON_SURFACE}; border: none; background: transparent;")
        else:
            self._email_state.setText(
                "⚠  No email set yet. Without one, a forgotten password "
                "cannot be recovered.")
            self._email_state.setStyleSheet(
                f"color: {C.ERROR}; border: none; background: transparent;")

        # One button, not a wall of fields, until they ask for it.
        self._email_set_btn.setVisible(not has)
        self._email_change_btn.setVisible(has)
        self._email_test_btn.setVisible(has)
        self._email_test_btn.setEnabled(has)

        embedded = (Path(__file__).resolve().parent / "config" / "face_embedding.npy")
        if embedded.exists():
            self._face_state.setText("Your face is recorded. SIVA can recognise you.")
        else:
            self._face_state.setText(
                "No face recorded. SIVA will ask for your password instead.")

    # a. password ------------------------------------------------

    def _on_change_password_clicked(self):
        """Open or close the password form."""
        showing = not self._pw_panel.isVisible()
        self._pw_panel.setVisible(showing)
        self._pw_toggle.setText(
            "Cancel" if showing else "Change my password")
        self._sec_say(self._pw_msg, "", True)
        for f in (self._pw_current, self._pw_new, self._pw_again):
            f.clear()
        if not showing:
            return
        # Nothing to check against on a machine with no password yet, and
        # asking for one that does not exist is a dead end.
        self._pw_current.setVisible(self._has_password_set())
        (self._pw_current if self._has_password_set()
         else self._pw_new).setFocus()

    def _close_password_panel(self):
        self._pw_panel.hide()
        self._pw_toggle.setText("Change my password")

    def _on_change_password(self):
        from core.auth import _hash_password
        cur = self._pw_current.text()
        new = self._pw_new.text().strip()
        again = self._pw_again.text().strip()

        if self._has_password_set() and not self._password_ok(cur):
            self._sec_say(self._pw_msg, "That is not your current password.", False)
            return
        if len(new) < 4:
            self._sec_say(self._pw_msg, "Please choose at least 4 characters.", False)
            return
        if new != again:
            self._sec_say(self._pw_msg, "The two new passwords do not match.", False)
            return

        h, s = _hash_password(new)
        cfg = self._read_secrets()
        cfg["app_password_hash"] = h
        cfg["app_password_salt"] = s
        if not self._write_secrets(cfg):
            self._sec_say(self._pw_msg, "Could not save. Please try again.", False)
            return

        for f in (self._pw_current, self._pw_new, self._pw_again):
            f.clear()
        # A new password means the old lockout is meaningless.
        try:
            from core.auth import _clear_lockout
            _clear_lockout()
        except Exception:
            pass
        self._reset_banner.hide()
        self._close_password_panel()      # fold away; it has done its job
        self._sec_say(self._pw_msg, "✓ Saved. Use this password next time.")

    # b. recovery email ------------------------------------------

    def _on_change_email_clicked(self):
        showing = not self._email_panel.isVisible()
        self._email_panel.setVisible(showing)
        if not showing:
            return
        # The password box only makes sense once there is a password to check
        # against. It is still required whenever one exists — including the
        # very first time an address is set — because otherwise anyone at an
        # unlocked machine could point recovery at their own inbox and take
        # the account with the next "forgot password".
        self._email_pw.setVisible(self._has_password_set())
        self._email_new.setText("")

        self._email_pw.setText("")
        self._email_new.setFocus()

    def _on_save_email(self):
        """
        Save the address, then send a message to it so they can see it works.

        Deliberately no code to type back. Asking an older user to leave the
        app, find their inbox, read six digits and return is where that flow
        loses people — and the code was only ever protecting against a
        mistyped address. A message they can go and look for does that job in
        a way that needs no explaining, and the password above is what
        actually stops the address being redirected by someone else.
        """
        from core import mailer, recovery
        addr = self._email_new.text().strip()

        # Changing where recovery mail goes needs the password: otherwise
        # anyone at an unlocked machine could point it at their own inbox and
        # take the account with the next "forgot password".
        if self._has_password_set() and not self._password_ok(self._email_pw.text()):
            self._sec_say(self._email_msg, "That is not your password.", False)
            return
        if not mailer.looks_like_email(addr):
            self._sec_say(self._email_msg,
                          "That does not look like an email address.", False)
            return

        if not recovery.set_recovery_email(addr):
            self._sec_say(self._email_msg, "Could not save. Please try again.", False)
            return

        self._email_pw.clear()
        self._email_new.clear()
        self._email_panel.hide()          # fold away; it has done its job
        self._refresh_security_state()
        self._sec_say(self._email_msg, "✓ Saved.")

        # Prove it, without asking them to do anything technical.
        if mailer.is_configured():
            self._sec_say(self._email_msg,
                          "✓ Saved. Sending a message so you can check it arrives…")
            self._send_mail_async(lambda: mailer.send_test_email(addr),
                                  self._email_msg)
        else:
            try:
                self.window().show_toast(
                    f"Saved. Codes will go to {recovery.mask_email(addr)}", ok=True)
            except (AttributeError, RuntimeError):
                pass

    def _on_test_email(self):
        from core import mailer, recovery
        addr = recovery.recovery_email()
        if not addr:
            self._sec_say(self._email_msg, "No email is set yet.", False)
            return
        self._sec_say(self._email_msg, "Sending a test message…")
        self._send_mail_async(lambda: mailer.send_test_email(addr), self._email_msg)

    def _send_mail_async(self, fn, label: QLabel):
        """
        Mail on a worker thread, result back on the GUI thread.

        An SMTP connection can sit for twenty seconds on a bad network. Doing
        that inline would freeze the whole interface for the duration.
        """
        import threading

        # Set before the thread starts, not after: a fast failure can arrive
        # first, and the result would then be written to whichever label
        # happened to be targeted last.
        self._mail_target = label

        def work():
            try:
                ok, msg = fn()
            except Exception as e:
                ok, msg = False, f"Could not send: {e}"
            self._mail_result.emit(bool(ok), str(msg))

        threading.Thread(target=work, daemon=True).start()

    def _on_mail_result(self, ok: bool, msg: str):
        label = getattr(self, "_mail_target", None) or self._email_msg
        self._sec_say(label, ("✓ " if ok else "") + msg, ok)
        # And loudly, because the small line under the buttons is exactly what
        # made a silent failure go unnoticed.
        try:
            self.window().show_toast(msg, ok=ok)
        except (AttributeError, RuntimeError):
            pass

    # c. face ----------------------------------------------------

    def _face_paths(self):
        cfg_dir = Path(__file__).resolve().parent / "config"
        return cfg_dir / "face_embedding.npy", cfg_dir / "api_keys.json"

    def _forget_face_files(self) -> bool:
        emb, _ = self._face_paths()
        try:
            emb.unlink(missing_ok=True)
        except Exception:
            return False
        cfg = self._read_secrets()
        cfg.pop("face_model_hash", None)
        return self._write_secrets(cfg)

    def _on_face_action_clicked(self, which: str):
        """Reveal the confirm box for one of the two face buttons.

        Pressing the same button again folds it back; pressing the other one
        swaps which action is armed rather than stacking a second box.
        """
        if self._face_pending == which and self._face_panel.isVisible():
            self._close_face_panel()
            return

        self._face_pending = which
        rerecording = (which == "rerecord")
        self._face_confirm.setText(
            "Yes — record my face again" if rerecording
            else "Yes — forget my face")
        # Rebuilt each time: the danger colour belongs to the destructive one
        # only, and this button is shared between the two.
        self._restyle_sec_button(
            self._face_confirm, "primary" if rerecording else "danger")

        needs_pw = self._has_password_set()
        self._face_pw.setVisible(needs_pw)
        self._face_pw.clear()
        self._face_panel.show()
        self._sec_say(
            self._face_msg,
            "Type your password, then press the button below."
            if needs_pw else
            "Press the button below to confirm.", True)
        if needs_pw:
            self._face_pw.setFocus()

    def _close_face_panel(self):
        self._face_panel.hide()
        self._face_pw.clear()
        self._face_pending = None
        self._sec_say(self._face_msg, "", True)

    def _on_face_confirm(self):
        if self._face_pending == "rerecord":
            self._on_rerecord_face()
        elif self._face_pending == "forget":
            self._on_forget_face()

    def _on_forget_face(self):
        if self._has_password_set() and not self._password_ok(self._face_pw.text()):
            self._sec_say(self._face_msg, "That is not your password.", False)
            return
        if not self._forget_face_files():
            self._sec_say(self._face_msg, "Could not remove the face data.", False)
            return
        self._close_face_panel()
        self._refresh_security_state()
        self._sec_say(self._face_msg,
                      "✓ Face data removed. SIVA will ask for your password.")

    def _on_rerecord_face(self):
        # Without the password check, anyone at an unlocked machine could
        # enrol their own face and keep permanent access.
        if self._has_password_set() and not self._password_ok(self._face_pw.text()):
            self._sec_say(self._face_msg, "That is not your password.", False)
            return
        if not self._forget_face_files():
            self._sec_say(self._face_msg, "Could not clear the old face data.", False)
            return
        self._close_face_panel()
        self._sec_say(self._face_msg,
                      "Restarting SIVA so it can record your face again…")
        QTimer.singleShot(900, self._restart_app)

    def _restart_app(self):
        """
        Close and reopen — an ordinary restart, not a reset.

        Everything is kept: settings, reminders, tasks, memory and chat
        history. Only the face data was removed, because that is the thing
        being re-recorded. Going out through core.shutdown is what guarantees
        it — that path saves the chat log before the process ends.
        """
        import os, subprocess, sys
        try:
            base = Path(__file__).resolve().parent
            subprocess.Popen(
                [sys.executable, str(base / "scripts" / "relaunch.py"),
                 str(os.getpid())],
                cwd=str(base),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
        except Exception as e:
            self._sec_say(self._face_msg,
                          f"Could not restart automatically: {e}. "
                          "Please close SIVA and open it again.", False)
            return
        from core import shutdown as _shutdown
        _shutdown.shutdown(reason="restarting to record your face",
                           ui=self.window())

    # ── called from main.py after a face rescue ──────────────────

    def force_password_reset(self):
        """Land the user on the password box with an unmissable reason why."""
        self._reset_banner.setText(
            "Your face let you in, but the password did not work. "
            "Please set one you will remember.")
        self._reset_banner.show()
        # The form is folded away by default, and this is the one path that
        # arrives needing it already open — being sent to set a password and
        # shown only a button to press is exactly the dead end this flow
        # exists to avoid.
        if not self._pw_panel.isVisible():
            self._on_change_password_clicked()
        # After the layout has settled, or the card is not where we think.
        QTimer.singleShot(0, self._scroll_to_security)

    def _scroll_to_security(self):
        try:
            self._scroll.ensureWidgetVisible(self._reset_banner, 0, 40)
            # _pw_current is hidden when there is no password to check
            # against, and focusing a hidden widget puts the caret nowhere.
            (self._pw_current if self._pw_current.isVisible()
             else self._pw_new).setFocus()
        except (RuntimeError, AttributeError):
            pass

    def _toggle_api(self):
        hidden = self._api_input.echoMode() == QLineEdit.EchoMode.Password
        self._api_input.setEchoMode(
            QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        # The label has to follow the state, or the button claims "Show" while
        # the code is already showing.
        self._reveal_btn.setText("🙈  Hide" if hidden else "👁  Show")
        self._reveal_btn.setAccessibleName(
            "Hide the Gemini system code" if hidden
            else "Show the Gemini system code")


    def _say_api(self, msg: str, ok: bool):
        """One status line under the code box. Same shape as `_sec_say`."""
        try:
            self._api_msg.setText(msg)
            self._api_msg.setStyleSheet(
                f"color: {C.PRIMARY if ok else '#B3261E'}; "
                "background: transparent; border: none;")
            self._api_msg.show()
        except (AttributeError, RuntimeError):
            pass

    def _load_config(self):
        try:
            from pathlib import Path
            import sys
            import json
            base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
            cfg_path = base_dir / "config" / "api_keys.json"
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                self._api_input.setText(cfg.get("gemini_api_key", ""))

                # AnimatedDropDown is not a QComboBox — it has no count() or
                # itemText(). The index loop that used to be here raised
                # AttributeError on every single open, was swallowed whole, and
                # left the dropdown showing its first item ("Windows") whatever
                # was actually saved.
                saved_os = str(cfg.get("os_system", "windows")).strip().lower()
                self._os_combo.setCurrentText(
                    _OS_KEY_TO_LABEL.get(saved_os, "Windows"))
        except Exception as e:
            # Silence here is what makes the box look empty when it is really
            # unread — and an unread box is one Save away from wiping the file.
            print("[SIVA] Could not read config/api_keys.json:", e)
            self._say_api(
                "Could not read the saved code. Do not press Save — it would "
                "write over what is stored.", ok=False)


    def _save_config(self):
        try:
            from pathlib import Path
            import sys
            import json
            base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
            cfg_path = base_dir / "config" / "api_keys.json"
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            else:
                cfg = {}
            # An empty box must never overwrite a working code. The field is
            # filled from this same file at build time, so "empty" here means
            # either the load failed or somebody cleared it by accident — and
            # in both cases writing it through leaves an app that cannot think,
            # with the only copy of the code gone.
            typed = self._api_input.text().strip()
            if not typed:
                if cfg.get("gemini_api_key"):
                    self._say_api(
                        "The code box is empty, so nothing was saved — the code "
                        "already stored has been left alone. Press Show to see it.",
                        ok=False)
                    return
                self._say_api("Type your Gemini code in first.", ok=False)
                return

            cfg["gemini_api_key"] = typed
            # Mapped, never lower-cased: see _OS_LABEL_TO_KEY above for why
            # "macOS".lower() is the wrong thing to write.
            cfg["os_system"] = _OS_LABEL_TO_KEY.get(
                self._os_combo.currentText().strip(), "windows")
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
            self._say_api("✓  Saved.", ok=True)

            btn = self.sender()
            if btn:
                # Fixed, not `btn.text()` — pressing Save twice inside the
                # 1.5s window would otherwise capture "Saved!" as the original
                # and leave the button stuck on it.
                original_text = "Save Gemini ID"
                btn.setText("Saved!")
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRIMARY}; 
                        color: {C.WHITE}; 
                        border-radius: 16px; 
                        padding: 10px 40px; 
                        border: none;
                    }}
                """)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(1500, lambda: self._reset_save_btn(btn, original_text))
        except Exception as e:
            print("Failed to save config", e)
            self._say_api("Could not save. Nothing was changed.", ok=False)


    def _reset_save_btn(self, btn, text):
        btn.setText(text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: #D9B340; 
                color: {C.ON_SURFACE}; 
                border-radius: 16px; 
                padding: 10px 40px; 
                border: none;
            }}
        """)
            
    def _load_memory(self):
        try:
            from memory.memory_manager import load_memory
            mem = load_memory()
            lines = []
            for cat, items in mem.items():
                if items:
                    lines.append(f"[{cat.upper()}]")
                    for k, v in items.items():
                        lines.append(f"  {k}: {v}")
                    lines.append("")
            
            if lines:
                self._mem_list.setText("\n".join(lines))
            else:
                # Use the exact text requested from the image
                self._mem_list.setText("Your memory box is currently ready to learn. As you interact, things it remembers about your preferences will appear here for a smoother experience.")
        except Exception as e:
            self._mem_list.setText(f"Error loading memory: {e}")
            
    def _clear_memory(self):
        from PyQt6.QtWidgets import QMessageBox
        
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Memory")
        msg.setText("Are you sure you want to delete all memory?")
        msg.setInformativeText("This action cannot be undone. SIVA will forget everything about you.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        
        msg.setStyleSheet(f"""
            QMessageBox {{ background: {C.BG}; }}
            QLabel {{ color: {C.ON_SURFACE}; font-size: 16px; }}
            QPushButton {{
                background: rgba(133,116,72,0.1);
                color: {C.ON_SURFACE};
                border-radius: 8px;
                padding: 8px 16px;
                border: none;
                min-width: 80px;
            }}
        """)
        
        ans = msg.exec()
        if ans == QMessageBox.StandardButton.Yes:
            try:
                from memory.memory_manager import MEMORY_PATH
                MEMORY_PATH.unlink(missing_ok=True)
                self._load_memory()
            except Exception as e:
                # Never silently: the user has just been told their data is
                # gone. If it is not, they have to be the one who finds out.
                traceback.print_exc()
                self._mem_list.setText(f"Could not delete the memory: {e}")

# ==========================================
# Chat History Page
# ==========================================
class ChatHistoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background: transparent;")

        self._container = QWidget(self)
        
        # ── MASTER VIEW (Dates) ──
        self._master = AnimatedScrollArea(
            self._container,
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )
        self._master_content = QWidget()
        self._master_content.setStyleSheet("background: transparent;")
        self._master_lay = QVBoxLayout(self._master_content)
        self._master_lay.setContentsMargins(40, 40, 40, 40)
        self._master_lay.setSpacing(24)
        
        # Title always on its own line, the two buttons in a row beneath it.
        # Simpler than sharing one line below a size threshold, and it never
        # needs to react to a text-size change: there is no arrangement to
        # flip, so it cannot be caught frozen in the wrong one.
        title = QLabel("Chat History")
        title.setWordWrap(True)
        title.setFont(_make_font(size=36, bold=True))
        title.setStyleSheet(f"color: {C.ON_SURFACE};")
        self._master_lay.addWidget(title)

        header_row = QHBoxLayout()
        header_row.addStretch()

        refresh_btn = AnimatedButton("Refresh")
        refresh_btn.setFont(_make_font(size=18, bold=True))
        refresh_btn.setStyleSheet(f"QPushButton {{ background: {C.SECONDARY}; color: {C.WHITE}; border-radius: 12px; padding: 12px 24px; border: none; }}")
        refresh_btn.clicked.connect(self.refresh_list)
        header_row.addWidget(refresh_btn)

        clear_btn = AnimatedButton("Clear All")
        clear_btn.setFont(_make_font(size=18, bold=True))
        clear_btn.setStyleSheet(f"QPushButton {{ background: {C.ERROR}; color: {C.WHITE}; border-radius: 12px; padding: 12px 24px; border: none; }}")
        clear_btn.clicked.connect(self._clear_history)
        header_row.addWidget(clear_btn)

        self._master_lay.addLayout(header_row)

        self._cards_lay = QVBoxLayout()
        self._cards_lay.setSpacing(16)
        self._master_lay.addLayout(self._cards_lay)
        self._master_lay.addStretch()
        self._master.setWidget(self._master_content)
        
        # ── DETAIL VIEW (Messages) ──
        self._detail = QWidget(self._container)
        self._detail.setStyleSheet(f"background: {C.BG};")
        self._detail_lay = QVBoxLayout(self._detail)
        self._detail_lay.setContentsMargins(40, 40, 40, 40)
        self._detail_lay.setSpacing(20)
        
        d_header = QHBoxLayout()
        back_btn = AnimatedButton("⬅ Back to Dates")
        back_btn.setFont(_make_font(size=20, bold=True))
        back_btn.setStyleSheet(f"QPushButton {{ background: {C.PRIMARY}; color: {C.WHITE}; border-radius: 12px; padding: 12px 24px; border: none; }}")
        back_btn.clicked.connect(self._slide_out)
        d_header.addWidget(back_btn)
        
        d_header.addStretch()
        self._detail_title = QLabel("")
        self._detail_title.setWordWrap(True)
        self._detail_title.setFont(_make_font(size=28, bold=True))
        self._detail_title.setStyleSheet(f"color: {C.ON_SURFACE};")
        d_header.addWidget(self._detail_title)
        self._detail_lay.addLayout(d_header)
        
        self._detail_scroll = AnimatedScrollArea(
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )
        self._detail_content = QWidget()
        self._detail_content.setStyleSheet("background: transparent;")
        self._msg_lay = QVBoxLayout(self._detail_content)
        self._msg_lay.setContentsMargins(0, 0, 0, 0)
        self._msg_lay.setSpacing(16)
        self._msg_lay.addStretch()
        self._detail_scroll.setWidget(self._detail_content)
        self._detail_lay.addWidget(self._detail_scroll)
        
        self._detail.hide()
        self.refresh_list()

        # The date cards stack their count/arrow row under the date past the
        # middle text size (see refresh_list) — a decision made fresh on
        # every rebuild, so it has to run again whenever the size changes.
        text_scale.instance().changed.connect(self._on_scale_changed)

    def _on_scale_changed(self, _level):
        # Only if the page is actually on screen. Rebuilt while hidden, the
        # cards are laid out against whatever size this page last happened to
        # have, and nothing corrects them on the way back in — they arrive
        # holding widths from a different text size and hang over the edge.
        # Deferring also means one rebuild when the page is next opened
        # instead of one per size change while it sits in the background.
        if self.isVisible():
            try:
                self.refresh_list()
            except RuntimeError:
                pass
        else:
            self._stale = True

    def _fit_panes(self):
        """
        Both panes fill the page. Positioned by hand rather than by a layout
        because the detail pane slides in over the master, and a layout would
        fight the animation for its geometry.
        """
        self._container.setGeometry(0, 0, self.width(), self.height())
        self._master.setGeometry(0, 0, self.width(), self.height())
        if self._detail.isVisible():
            self._detail.setGeometry(0, 0, self.width(), self.height())

    def showEvent(self, event):
        super().showEvent(event)
        # Also on show, not only on resize. This page is one of eight in a
        # stack, so it spends most of its life hidden and misses the resizes
        # that happen meanwhile; hand-placed panes have no layout to catch
        # them up on the way back in. It used to merely look stale for a
        # frame, but the scroll area now pins its content to the viewport, so
        # a stale viewport is baked into the content width and the cards
        # inside are squeezed to nothing.
        self._fit_panes()
        if getattr(self, "_stale", False):
            self._stale = False
            try:
                self.refresh_list()
            except RuntimeError:
                pass
            self._fit_panes()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_panes()
            
    def _slide_in(self, date_str, messages):
        self._detail_title.setText(date_str)
        # Clear old messages
        while self._msg_lay.count() > 1:
            item = self._msg_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Reset reveal state so new messages animate in
        self._detail_scroll.reset_revealed()
                
        # Populate messages
        for msg in messages:
            sender = msg.get("sender", "SYS")
            text = msg.get("text", "")
            time_str = msg.get("time", "")
            
            b = QFrame()
            # High contrast colors for elderly
            bg_color = "rgba(139,94,60,0.15)" if sender == "You" else "rgba(45,86,72,0.15)"
            b.setStyleSheet(f"background: {bg_color}; border-radius: 16px; border: 1px solid rgba(0,0,0,0.05);")
            l = QVBoxLayout(b)
            l.setContentsMargins(24, 16, 24, 16)
            
            hdr = QLabel(f"{sender} • {time_str}" if time_str else sender)
            hdr.setFont(_make_font(size=16, bold=True))
            hdr.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
            l.addWidget(hdr)
            
            txt = QLabel(text)
            txt.setFont(_make_font(size=24))  # Large readable font
            txt.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
            txt.setWordWrap(True)
            txt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            l.addWidget(txt)
            
            self._msg_lay.insertWidget(self._msg_lay.count() - 1, b)
            
        self._detail.setGeometry(self.width(), 0, self.width(), self.height())
        self._detail.show()
        
        self._anim = Motion.anim(self._detail, "pos", 400, QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(QPointF(self.width(), 0))
        self._anim.setEndValue(QPointF(0, 0))
        self._anim.start()
        
    def _slide_out(self):
        self._anim = Motion.anim(self._detail, "pos", 300, QEasingCurve.Type.InCubic)
        self._anim.setStartValue(QPointF(0, 0))
        self._anim.setEndValue(QPointF(self.width(), 0))
        self._anim.finished.connect(self._detail.hide)
        self._anim.start()
        
    def refresh_list(self):
        # Clear cards
        while self._cards_lay.count():
            item = self._cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        d = Path.home() / ".siva" / "chat_history.json"
        if not d.exists():
            lbl = QLabel("No chat history available.")
            lbl.setFont(_make_font(size=24))
            lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR};")
            self._cards_lay.addWidget(lbl)
            return
            
        try:
            data = json.loads(d.read_text(encoding="utf-8"))
        except Exception:
            lbl = QLabel("Failed to load history.")
            lbl.setFont(_make_font(size=24))
            self._cards_lay.addWidget(lbl)
            return
            
        if not data:
            return

        # Group by date
        grouped = {}
        for item in data:
            if isinstance(item, str):
                date_key = "Legacy Sessions"
                day = "Previous"
                msg_obj = {"sender": "Unknown", "text": item, "time": ""}
            else:
                date_key = item.get("date", "Unknown Date")
                day = item.get("day", "")
                msg_obj = item
                
            if date_key not in grouped:
                grouped[date_key] = {"day": day, "messages": []}
            grouped[date_key]["messages"].append(msg_obj)
            
        # Create cards (newest first)
        for date_key in reversed(list(grouped.keys())):
            day = grouped[date_key]["day"]
            messages = grouped[date_key]["messages"]
            
            # CardButton, not AnimatedButton: this is a container of labels,
            # and a plain QPushButton ignores the layout inside it when it
            # measures itself.
            btn = CardButton()
            # Minimum rather than fixed: at the larger text sizes the date
            # wraps onto a second line and a pinned height would cut it off.
            btn.setMinimumHeight(120)
            # And a floor on the width, or the card collapses to nothing while
            # the page is being re-measured — the labels inside wrap now, so
            # there is no long unbreakable string holding it open any more.
            # Same guard, same reason, as the auth buttons on Settings.
            btn.setMinimumWidth(160)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.7);
                    border: 1px solid rgba(139,94,60,0.3);
                    border-radius: 20px;
                    text-align: left;
                }}
            """)
            
            # "03 Aug 2026" plus the message count only share a line while the
            # text is small; past the middle size the count drops below.
            stacked = text_scale.factor() > 1.18
            lay = QVBoxLayout(btn) if stacked else QHBoxLayout(btn)
            lay.setContentsMargins(32, 16, 32, 16) if stacked \
                else lay.setContentsMargins(32, 0, 32, 0)

            info_lay = QVBoxLayout()
            info_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            info_lay.setSpacing(4)

            d_lbl = QLabel(date_key)
            d_lbl.setFont(_make_font(size=28, bold=True))
            d_lbl.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; border: none;")
            info_lay.addWidget(d_lbl)
            
            if day:
                day_lbl = QLabel(day)
                day_lbl.setFont(_make_font(size=20))
                day_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
                info_lay.addWidget(day_lbl)
                
            lay.addLayout(info_lay)
            if not stacked:
                lay.addStretch()

            count_row = QHBoxLayout() if stacked else lay

            count_lbl = QLabel(f"{len(messages)} messages")
            count_lbl.setFont(_make_font(size=20))
            count_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; border: none;")
            count_row.addWidget(count_lbl)

            arrow = QLabel("➔")
            arrow.setFont(_make_font(size=32, bold=True))
            arrow.setStyleSheet(f"color: {C.SECONDARY}; background: transparent; border: none;")
            count_row.addWidget(arrow)

            if stacked:
                count_row.addStretch()
                lay.addLayout(count_row)
            
            btn.clicked.connect(lambda _, dk=date_key, ms=messages: self._slide_in(dk, ms))
            self._cards_lay.addWidget(btn)

    def _clear_history(self):
        from PyQt6.QtWidgets import QMessageBox
        ans = QMessageBox.question(self, "Clear History", "Delete all chat history?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            try:
                d = Path.home() / ".siva" / "chat_history.json"
                if d.exists():
                    d.unlink()
                self.refresh_list()
            except Exception:
                pass


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    # The safety tool runs in an executor thread and needs three things to
    # happen on the GUI thread. Signals rather than QTimer.singleShot: a timer
    # started from a plain worker thread is created in *that* thread, which has
    # no Qt event loop, so it never fires and the call is silently lost. A
    # queued signal connection is the mechanism that actually crosses threads —
    # the same one _log_sig already relies on.
    _safety_verdict_sig = pyqtSignal(object)
    _safety_open_sig    = pyqtSignal(object, object, bool)
    _capture_req_sig    = pyqtSignal(object)
    # Same reasoning again, for permissions. The tool dispatcher asks from an
    # executor thread and blocks on the answer; the card itself may only be
    # built here. `_tour_sig` is the walkthrough's equivalent.
    _permission_sig     = pyqtSignal(str, object)
    _tour_sig           = pyqtSignal(bool)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("S.I.V.A.S.A.F.E — MARK 1")
        # The window sizes itself from the text setting in
        # _apply_scaled_window_size, so the generic scaler must keep off it.
        self.setProperty("_ts_ignore_geom", True)
        self._apply_scaled_window_size()
        _ts = text_scale.instance()
        _ts.changed.connect(lambda _l: self._apply_scaled_window_size())
        # The orb repaints continuously; while the curtain is sweeping it is
        # entirely hidden, and letting it keep drawing just steals frames from
        # the sweep.
        _ts.transition_started.connect(self._pause_orb)
        _ts.transition_finished.connect(self._resume_orb)

        self.on_text_command   = None
        self.on_remote_clicked = None
        self._continuous_listen = False
        self._talk_pressed = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        # One permission card at a time — a second request waits its turn
        # rather than drawing on top of the first.
        self._perm_overlay: PermissionOverlay | None = None
        self._perm_queue: list[tuple[str, object]] = []
        self._tour: TourOverlay | None = None
        self._after_tour = None
        self._active_nav       = "home"
        import time
        self._start_time = time.time()
        self._wifi_overlay: WiFiDisconnectedOverlay | None = None
        self._wifi_overlay_dismissed = False
        self._wifi_disconnect_polls = 0
        self._wifi_timer = QTimer(self)
        self._wifi_timer.setInterval(self._WIFI_CHECK_MS)
        self._wifi_timer.timeout.connect(self._check_wifi_connection)
        self._wifi_timer.start()

        # Central widget
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top Bar ──
        root.addWidget(self._build_top_bar())

        # ── Body ──
        body_container = QWidget()
        body_container.setStyleSheet("background: transparent;")
        body = QHBoxLayout(body_container)
        body.setContentsMargins(_PAGE_MARGIN, _PAGE_MARGIN, _PAGE_MARGIN, 0)
        body.setSpacing(_GAP)
        # Held because the layout mode moves the activity log between this and
        # the centre column — see _apply_layout_mode.
        self._body_lay = body
        self._layout_mode = self._LAYOUT_WIDE
        self._mode_applied = False
        self._mode_busy = False
        self._log_in_centre = False
        self._auto_collapsed = False

        self._left_panel = self._build_left_sidebar()
        body.addWidget(self._left_panel, stretch=0)

        # Sidebar starts in frame every launch; the collapsed state is not
        # remembered on purpose. A text-size change moves the column's own
        # width, so the shell has to be told to follow it.
        self._sidebar_collapsed = False
        self._sidebar_anim = None
        text_scale.instance().changed.connect(self._sync_sidebar_width)
        # One turn later the first polish pass has run and the column knows its
        # real, scaled width.
        QTimer.singleShot(0, self._sync_sidebar_width)
        # Toasts are placed by hand too, so they need the same nudge.
        text_scale.instance().changed.connect(
            lambda *_: QTimer.singleShot(0, self._place_toasts))

        self._centre_stack = self._build_centre_canvas()
        
        centre_container = QWidget()
        c_lay = QVBoxLayout(centre_container)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(0)
        
        self._global_back_btn = HomeBackButton()
        self._global_back_btn.clicked.connect(lambda: self._drawer_nav("home"))
        self._global_back_btn.hide()
        
        c_lay.addWidget(self._global_back_btn)
        c_lay.addWidget(self._centre_stack, stretch=1)
        self._centre_lay = c_lay

        self._centre_holder = centre_container
        body.addWidget(centre_container, stretch=1)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addWidget(body_container, stretch=1)

        # ── Bottom Pill Bar ──
        root.addWidget(self._build_bottom_pill_bar())

        # ── Timers ──
        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(5000)
        self._tick_clock()

        # ── Signals ──
        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._safety_verdict_sig.connect(self._show_safety_verdict)
        self._safety_open_sig.connect(self.open_safety_check)
        self._capture_req_sig.connect(self._serve_capture_request)
        self._permission_sig.connect(self._serve_permission_request)
        self._tour_sig.connect(self.start_walkthrough)

        # ── Config Check ──
        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        # ── Shortcuts ──
        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)

        # ── Hamburger Drawer ──
        self._hamburger = HamburgerDrawer(central)
        self._hamburger.page_selected.connect(self._drawer_nav)
        self._hamburger.hide()

        # ── Reminder Refresh Timer (every 30s) ──
        self._rem_timer = QTimer(self)
        self._rem_timer.timeout.connect(self._refresh_sidebar_reminders)
        self._rem_timer.timeout.connect(self._update_bell_dot)
        self._rem_timer.start(30000)
        self._refresh_sidebar_reminders()
        self._update_bell_dot()

    def closeEvent(self, event):
        """
        The X button really does close SivaSafe — and answers instantly.

        Two things matter here, and the second one is subtle.

        First: nothing used to handle this at all. The app left it to Qt's
        quitOnLastWindowClosed, which never fired while the dashboard server
        and the live session were running, so the process stayed resident with
        its window gone.

        Second: the shutdown must NOT run inline here. A click on the title-bar
        X is delivered while Windows is inside its own non-client modal loop
        for that button, holding internal user32 locks. Terminating the process
        from inside that loop can deadlock against the audio and asyncio
        threads, and the window then stops pumping messages — which is exactly
        what makes Windows put up "not responding" and offer to end the task.
        Programmatic close() never hits this, which is why it tested clean.

        So: accept, hide so the click is answered at once, then shut down.

        The shutdown runs inline rather than through QTimer.singleShot(). A
        deferral does not help here: while the title-bar button is being
        tracked, Windows runs its own message loop, which pumps Qt's timers
        too — so the callback fires inside the modal loop anyway. Worse, if
        that loop is stalled the timer may not fire at all. core.shutdown ends
        with TerminateProcess, which is kernel-serviced and works from any
        context, so nothing here needs the event loop to still be turning.
        """
        from core import shutdown as _shutdown
        if _shutdown.is_shutting_down():
            event.accept()
            return
        event.accept()
        self.hide()          # the window disappears immediately
        _shutdown.shutdown(reason="window closed", ui=self)

    def _pause_orb(self):
        hud = getattr(self, "hud", None)
        if hud is not None:
            try:
                hud._tmr.stop()
            except RuntimeError:
                pass

    def _resume_orb(self):
        hud = getattr(self, "hud", None)
        if hud is not None:
            try:
                hud._tmr.start(16)
            except RuntimeError:
                pass

    def _apply_scaled_window_size(self):
        """
        Bigger text needs a bigger canvas.

        Above Default the window grows at the same rate as the words, not half
        of it. Growing the text 60% while growing its container 30% is what
        made the interface feel like it was being squeezed rather than
        enlarged, and the canvas is the outermost container of the lot.

        It resizes; it never maximises. Maximising was tried and had to be
        taken out: sign-in shrinks this same window to 800×740 and lays the
        auth overlay over it, and `resize()` does nothing to a maximised
        window. The window stayed full-screen while the overlay sized itself
        for the small one, so the sign-in screen appeared with the dashboard
        showing underneath it. Anything here that ignores a later resize
        breaks that handover.

        Small and Default keep the half rate they were built with.
        """
        g = QApplication.primaryScreen().availableGeometry()
        f = text_scale.factor()
        grow = f if f > 1.0 else 1.0 + (f - 1.0) * 0.5
        w = min(int(_DEFAULT_W * grow), int(g.width() * 0.94))
        h = min(int(_DEFAULT_H * grow), int(g.height() * 0.94))
        self.setMinimumSize(min(_MIN_W, w), min(_MIN_H, h))
        if self.isMaximized() or self.isFullScreen():
            return
        self.resize(w, h)
        self.move(g.x() + (g.width() - w) // 2,
                  g.y() + (g.height() - h) // 2)

    # ═════════════════════════════════════════════════════════════
    # TOP BAR
    # ═════════════════════════════════════════════════════════════

    def _build_top_bar(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(_TOP_H)
        w.setStyleSheet(f"""
            QWidget {{
                background: rgba(255,255,255,0.30);
                border-bottom: 1px solid rgba(255,255,255,0.5);
            }}""")
        _glass_shadow(w, blur=30, dy=8)

        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(12)

        # Left: Hamburger menu button
        self._hamburger_btn = AnimatedButton("☰")
        self._hamburger_btn.setFont(_make_font(size=20, bold=True))
        self._hamburger_btn.setFixedSize(44, 44)
        self._hamburger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hamburger_btn.setStyleSheet(self._hamburger_qss(False))
        self._hamburger_btn.setToolTip("Hide the menu")
        self._hamburger_btn.clicked.connect(self._toggle_hamburger)
        lay.addWidget(self._hamburger_btn)

        # Icon + Title
        icon_lbl = QLabel("🤖")
        icon_lbl.setFont(_make_font(size=24))
        icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(icon_lbl)
        self._top_icon_lbl = icon_lbl

        title = QLabel("S.I.V.A.S.A.F.E")
        title.setFont(_make_font(size=22, bold=True))
        # The top bar is a fixed height, so a second line would be clipped
        # rather than shown. Both this and the clock stay on one line and are
        # given room by the bar itself instead.
        title.setProperty("_ts_nowrap", True)
        title.setStyleSheet(f"color: {C.PRIMARY}; background: transparent; letter-spacing: 2px;")
        lay.addWidget(title)
        self._top_title_lbl = title

        lay.addStretch()

        # Centre: Nav buttons
        self._top_nav_btns = []
        for btn_text, btn_icon, key in [("Chat History", "🕐", "chat"), ("Settings", "⚙", "settings")]:
            btn = AnimatedButton(f"  {btn_icon}  {btn_text}")
            btn.setFont(_make_font(size=13, bold=True))
            btn.setFixedHeight(40)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._top_nav_btns.append((btn, btn_icon, btn_text))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                        stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                    border: 1px solid rgba(255,255,255,0.7);
                    border-radius: 12px;
                    padding: 6px 16px;
                    color: {C.ON_SURFACE_VAR};
                }}""")
            btn.clicked.connect(lambda _, k=key: self._go_to_page(k))
            lay.addWidget(btn)

        lay.addSpacing(16)

        # Notification bell
        bell_btn = AnimatedButton("🔔")
        bell_btn.setFont(_make_font(size=18))
        bell_btn.setFixedSize(44, 44)
        bell_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bell_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                    stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                border: 1px solid rgba(255,255,255,0.7);
                border-radius: 22px;
                color: {C.SECONDARY};
            }}""")
        bell_btn.clicked.connect(self._show_notifications)
        lay.addWidget(bell_btn)

        # Red dot on bell
        self._bell_dot = QLabel(bell_btn)
        self._bell_dot.setFixedSize(10, 10)
        self._bell_dot.setStyleSheet(f"""
            background: {C.ERROR};
            border-radius: 5px;
            border: 2px solid {C.SURFACE};
        """)
        self._bell_dot.move(30, 4)
        self._bell_dot.hide()

        lay.addSpacing(12)

        # Clock
        self._clock_lbl = QLabel("...")
        self._clock_lbl.setProperty("_ts_nowrap", True)
        self._clock_lbl.setFont(_make_font(_FONT_HUD, 13, bold=True))
        self._clock_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        lay.addWidget(self._clock_lbl)

        self._apply_top_bar_density()
        text_scale.instance().changed.connect(
            lambda _l: self._apply_top_bar_density())

        return w

    # ── top bar density ──────────────────────────────────────────────
    #
    # The bar is one row, at a fixed height, with no scroll area and nowhere
    # to wrap — so what it carries has to thin out as the words grow. Measured
    # at the largest size it was asking for 2742px of the window's width, more
    # than the whole rest of the interface put together, and the two widest
    # items in it were the app's own name and the date.
    #
    # Nothing is actually lost at any step. Both nav buttons keep their full
    # labels in the sidebar and the drawer, the app name is on the window
    # title and the drawer footer, and the date is on the Health page. What is
    # dropped here is the duplicate, in the one place that cannot afford it.

    _CLOCK_FMT = (
        "%a %b %d %Y // %I:%M %p",   # 0 — Small, Default: exactly as designed
        "%a %b %d // %I:%M %p",      # 1 — Large: the year goes
        "%b %d  ·  %I:%M %p",        # 2 — Larger: weekday goes
        "%I:%M %p",                  # 3 — Largest: the time, nothing else
    )

    def _top_bar_density(self) -> int:
        f = text_scale.factor()
        if f <= 1.00:
            return 0
        if f <= 1.18:
            return 1
        if f <= 1.36:
            return 2
        return 3

    def _apply_top_bar_density(self):
        d = self._top_bar_density()

        # The robot and the full stops in the name are decoration; they are the
        # first thing to go and the last thing anyone would miss. The stops
        # cost more than they look: at 22pt with 2px of letter-spacing they
        # were 465px of the bar all by themselves, wider than either button.
        self._top_icon_lbl.setVisible(d == 0)
        self._top_title_lbl.setText(
            "S.I.V.A.S.A.F.E" if d == 0 else "SIVASAFE")
        self._top_title_lbl.setVisible(d <= 2)

        # Icon-only past the middle size. The tooltip and the sidebar keep the
        # words available for anyone who needs them.
        for btn, icon, text in self._top_nav_btns:
            btn.setText(f"  {icon}  {text}" if d <= 1 else icon)
            btn.setToolTip(text)

        self._tick_clock()

    def _tick_clock(self):
        now = datetime.now()
        self._clock_lbl.setText(
            now.strftime(self._CLOCK_FMT[self._top_bar_density()]).upper()
        )

    # ═════════════════════════════════════════════════════════════
    # LEFT SIDEBAR
    # ═════════════════════════════════════════════════════════════

    def _build_left_sidebar(self) -> QWidget:
        # `w` is the navigation column itself. The glass panel styling and the
        # shadow now live on the SidebarShell wrapped around it at the end of
        # this method, because that is the part whose width the hamburger
        # animates. The column keeps its own fixed width so the text engine
        # goes on scaling it exactly as before.
        w = QWidget()
        w.setFixedWidth(_LEFT_W)

        outer = QVBoxLayout(w)
        outer.setContentsMargins(14, 16, 14, 16)
        outer.setSpacing(10)

        # Everything above Emergency scrolls. At the larger text sizes this
        # column is taller than the window, and without a scroll area Qt
        # silently crushes the cards into one another instead.
        side_scroll = AnimatedScrollArea(w, show_gradients=False)
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_col = QWidget()
        side_col.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(side_col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ── System Health Card ──
        health = QWidget()
        health.setStyleSheet("""
            QWidget#healthCard {
                background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                    stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                border: 1px solid rgba(255,255,255,0.7);
                border-radius: 16px;
                padding: 16px;
            }
        """)
        health.setObjectName("healthCard")
        hlay = QHBoxLayout(health)
        hlay.setContentsMargins(10, 10, 10, 10)
        hlay.setSpacing(10)

        avatar = QLabel("😊")
        avatar.setFont(_make_font(size=24))
        avatar.setFixedSize(48, 48)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"""
            background: rgba(55,103,87,0.10);
            border: 2px solid rgba(55,103,87,0.30);
            border-radius: 24px;
        """)
        hlay.addWidget(avatar)

        hinfo = QVBoxLayout()
        hinfo.setSpacing(4)
        h_title = QLabel("System Health")
        h_title.setFont(_make_font(size=14, bold=True))
        h_title.setWordWrap(True)   # wraps rather than clipping at large text
        h_title.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        hinfo.addWidget(h_title)

        self._health_status = QLabel("● OPTIMAL")
        self._health_status.setFont(_make_font(size=11, bold=True))
        self._health_status.setStyleSheet(f"color: {C.PRIMARY}; background: transparent;")
        hinfo.addWidget(self._health_status)

        hlay.addLayout(hinfo)
        lay.addWidget(health)

        # ── Navigation ──
        self._nav_btns: dict[str, QPushButton] = {}
        nav_items = [
            ("home", "🏠", "Home"),
            ("health", "❤", "Health Status"),
            ("remote", "📡", "Remote Control"),
        ]
        for key, icon, label in nav_items:
            btn = AnimatedButton(f"  {icon}   {label}")
            btn.setFont(_make_font(size=13, bold=True))
            btn.setFixedHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._nav_click(k))
            self._nav_btns[key] = btn
            lay.addWidget(btn)
        self._style_nav_btns()

        # ── Reminders (TASK_QUEUE) ──
        rem_card = QWidget()
        rem_card.setStyleSheet("""
            QWidget#remCard {
                background: rgba(255,255,255,0.40);
                border: 1px solid rgba(255,255,255,0.60);
                border-radius: 16px;
            }
        """)
        rem_card.setObjectName("remCard")
        rlay = QVBoxLayout(rem_card)
        rlay.setContentsMargins(12, 12, 12, 12)
        rlay.setSpacing(8)

        # Clickable header row
        rem_hdr_row = QHBoxLayout()
        rem_header = AnimatedButton("≡  REMINDERS")
        rem_header.setFont(_make_font(_FONT_HUD, 12, bold=True))
        rem_header.setCursor(Qt.CursorShape.PointingHandCursor)
        rem_header.setStyleSheet(f"""
            QPushButton {{
                color: {C.SECONDARY};
                background: transparent;
                border: none;
                letter-spacing: 2px;
                text-align: left;
                padding: 0;
            }}""")
        rem_header.clicked.connect(lambda: self._go_to_page("reminders"))
        rem_hdr_row.addWidget(rem_header)

        rem_add_btn = AnimatedButton("+")
        rem_add_btn.setFont(_make_font(size=16, bold=True))
        rem_add_btn.setFixedSize(28, 28)
        rem_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rem_add_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(55,103,87,0.10);
                color: {C.PRIMARY};
                border: none;
                border-radius: 14px;
            }}""")
        rem_add_btn.clicked.connect(lambda: self._go_to_page("reminders"))
        rem_hdr_row.addWidget(rem_add_btn)
        rlay.addLayout(rem_hdr_row)

        # Dynamic reminder items (populated from store)
        self._reminder_list = QVBoxLayout()
        self._reminder_list.setSpacing(8)
        rlay.addLayout(self._reminder_list)

        # "View All" link
        view_all = AnimatedButton("View All →")
        view_all.setFont(_make_font(size=10))
        view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all.setStyleSheet(f"""
            QPushButton {{
                color: {C.PRIMARY};
                background: transparent;
                border: none;
                text-align: right;
                padding: 0;
            }}""")
        view_all.clicked.connect(lambda: self._go_to_page("reminders"))
        rlay.addWidget(view_all, alignment=Qt.AlignmentFlag.AlignRight)

        # Make the entire card clickable to open Reminders page
        rem_card.setCursor(Qt.CursorShape.PointingHandCursor)
        rem_card.mousePressEvent = lambda event: self._go_to_page("reminders")

        lay.addWidget(rem_card)

        # ── Stretch ──
        lay.addStretch()

        side_scroll.setWidget(side_col)
        outer.addWidget(side_scroll, stretch=1)

        # Emergency and Shut Down stay pinned below the scrolling column —
        # they must never scroll out of reach.
        lay = outer

        # ── Emergency Button ──
        emergency_btn = AnimatedButton("📞  EMERGENCY")
        emergency_btn.setFont(_make_font(size=15, bold=True))
        emergency_btn.setFixedHeight(48)
        emergency_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        emergency_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(186,26,26,0.10);
                color: {C.ERROR};
                border: 2px solid rgba(186,26,26,0.40);
                border-radius: 14px;
                font-size: 15px;
            }}""")
        emergency_btn.clicked.connect(self._on_emergency)
        lay.addWidget(emergency_btn)

        # ── Logout Button ──
        logout_btn = AnimatedButton("⏻  Shut Down")
        logout_btn.setFont(_make_font(size=13, bold=True))
        logout_btn.setFixedHeight(40)
        logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.40);
                color: {C.ON_SURFACE_VAR};
                border: 1px solid rgba(255,255,255,0.60);
                border-radius: 14px;
            }}""")
        logout_btn.clicked.connect(self._on_logout)
        lay.addWidget(logout_btn)

        self._left_column = w
        return SidebarShell(w)

    def _add_reminder_item(self, text: str, done: bool = False, overdue: bool = False,
                            date_str: str = "", time_str: str = ""):
        # A container rather than a bare layout, so the row can measure its own
        # wrapped text. The sidebar is the narrowest column in the app, so a
        # reminder is the most likely thing to need a second line, and there
        # was nothing here that could notice it had not been given one.
        holder = _WrappingRow()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # Text + time column
        col = QVBoxLayout()
        col.setSpacing(1)

        lbl = QLabel(text)
        lbl.setFont(_make_font(size=11, bold=overdue))
        lbl.setWordWrap(True)
        if overdue:
            # Was C.TERTIARY, the palette's gold. Gold is a fill colour here —
            # it works under the dark ink of the alarm badge and fails as ink
            # itself: 1.7:1 against this card, against the 4.5:1 that ordinary
            # text needs. On the one screen meant for people who do not see
            # well, the reminders already past their time were the least
            # legible thing on it.
            #
            # Red rather than a darker gold because the notifications page
            # already reads overdue as red, down to the 🔴 beside it, and the
            # two lists show the same reminders.
            lbl.setStyleSheet(f"color: {C.ERROR}; background: transparent;")
        else:
            lbl.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent;")
        col.addWidget(lbl)

        if date_str or time_str:
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                friendly = dt.strftime("%b %d • %I:%M %p")
            except Exception:
                friendly = f"{date_str} {time_str}".strip()
            time_lbl = QLabel(friendly)
            time_lbl.setFont(_make_font(_FONT_HUD, 9))
            # One colour for both states, at an opacity that can actually be
            # read: 0.45 was 2.2:1 and 0.85 is 5.5:1. The date is the whole
            # point of a reminder, and it was the faintest text in the app.
            # Which of them is overdue is carried by the title above.
            time_lbl.setStyleSheet(
                "color: rgba(93,64,55,0.85); background: transparent;")
            col.addWidget(time_lbl)

        row.addLayout(col)
        row.addStretch()

        self._reminder_list.addWidget(holder)

    def _style_nav_btns(self):
        for key, btn in self._nav_btns.items():
            if key == self._active_nav:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRIMARY};
                        color: white;
                        border: 1px solid {C.PRIMARY_CTR};
                        border-radius: 12px;
                        text-align: left;
                        padding-left: 14px;
                        font-size: 13px;
                    }}""")
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                            stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                        border: 1px solid rgba(255,255,255,0.7);
                        border-radius: 12px;
                        color: {C.ON_SURFACE_VAR};
                        text-align: left;
                        padding-left: 14px;
                        font-size: 13px;
                    }}""")

    _PAGE_MAP = {
        "home": 0, "health": 1, "reminders": 2,
        "notifications": 3, "tasklists": 4, "notes": 5,
        "chat": 6, "settings": 7, "safety": 8,
    }

    def _nav_click(self, key: str):
        if key == "remote":
            self._open_remote()
            return
        self._active_nav = key
        self._style_nav_btns()
        self._centre_stack.setCurrentIndex(self._PAGE_MAP.get(key, 0))
        self._global_back_btn.setVisible(key != "home")

        # The log is hidden on the complex pages, which changes how much width
        # the page needs, which can change the layout mode — so re-pick it
        # rather than only re-measuring the log.
        self._right_panel.setVisible(self._log_wanted())
        self._update_responsive_panels()
        self._apply_log_width()

    # ═════════════════════════════════════════════════════════════
    # SIDEBAR COLLAPSE  (the hamburger)
    # ═════════════════════════════════════════════════════════════
    #
    # Pressing ☰ slides the whole navigation column out of frame to the left
    # and gives its width back to the page; pressing it again brings it in.
    # It applies to every centre page — Home, Reminders, Health, Tasks and the
    # rest — because there is one sidebar shared by all of them.
    #
    # The state is deliberately NOT persisted: the app always opens with the
    # sidebar in frame, and it only ever leaves on a press.

    _SIDEBAR_MS = 300

    def _toggle_hamburger(self):
        # A press is the user's decision and outranks the automatic fold: it
        # is not undone until the canvas changes shape and the mode is picked
        # again.
        self._auto_collapsed = False
        self._set_sidebar_collapsed(not getattr(self, "_sidebar_collapsed", False))

    def _set_sidebar_collapsed(self, collapsed: bool):
        shell = getattr(self, "_left_panel", None)
        if shell is None:
            return

        prev = getattr(self, "_sidebar_anim", None)
        if prev is not None:
            prev.stop()                      # mid-flight press: take it over
        self._sidebar_collapsed = collapsed

        start  = shell.width()
        target = 0 if collapsed else shell.column_width()
        if not collapsed:
            shell.set_column_visible(True)
        shell.set_shadow_enabled(False)

        group = QParallelAnimationGroup(self)

        def slide(widget, frm, to):
            # Both bounds together: with stretch=0 the layout gives a panel
            # exactly its min/max, so moving only one of them does nothing.
            for prop in (b"minimumWidth", b"maximumWidth"):
                anim = QPropertyAnimation(widget, prop, self)
                anim.setDuration(self._SIDEBAR_MS)
                anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
                anim.setStartValue(frm)
                anim.setEndValue(to)
                group.addAnimation(anim)

        slide(shell, start, target)

        # On Home the freed strip goes to the activity log instead of
        # stretching the page: the log grows leftward by exactly what the
        # sidebar gives up, so the centre column keeps its width and simply
        # travels left. Same group, same curve, so the two edges move as one.
        rp = getattr(self, "_right_panel", None)
        if rp is not None:
            log_to = self._log_base_width() + self._log_bonus()
            if log_to != rp.width():
                slide(rp, rp.width(), log_to)

        group.finished.connect(self._on_sidebar_settled)
        group.start()
        self._sidebar_anim = group

        self._update_hamburger_btn()

    def _on_sidebar_settled(self):
        shell = getattr(self, "_left_panel", None)
        if shell is None:
            return
        self._sidebar_anim = None
        shell.set_shadow_enabled(True)
        shell.set_column_visible(not self._sidebar_collapsed)
        # Land on the exact figures rather than wherever the easing curve's
        # last step rounded to.
        self._apply_log_width()
        # The log may now fit where it did not before — but not on the pages
        # that hide it on purpose, or collapsing the sidebar on Reminders
        # would drag the log back in on top of the space we just freed.
        if self._log_wanted():
            self._update_responsive_panels()

    def _sync_sidebar_width(self):
        """Follow a text-size change: both panel widths just moved."""
        if getattr(self, "_sidebar_anim", None) is not None:
            return
        shell = getattr(self, "_left_panel", None)
        if shell is not None and not getattr(self, "_sidebar_collapsed", False):
            shell.sync_width()
        self._apply_log_width()

    # ── activity log width ───────────────────────────────────────
    #
    # Home hands the collapsed sidebar's strip to the activity log rather than
    # to the page: the orb and the quick actions keep their size and just move
    # left, and the log grows leftward to show more of each line. Every other
    # page keeps the old behaviour and lets the page have the space.

    def _log_base_width(self) -> int:
        """
        The log's width with the sidebar in frame, at the current text size.

        At and below Default this is the 50% rate the text engine uses for
        panel widths, unchanged — those sizes are left exactly as designed.

        Above it the log grows with the text instead. It is the one panel on
        the page that exists to be read, so cramming 60% larger words into a
        30% wider column is the wrong trade: the lines break more often, each
        message gets taller, and less of the conversation is visible than
        before the reader asked for bigger text. The width it takes comes off
        the orb, which has nothing to read in it.
        """
        f = text_scale.factor()
        if f <= 1.0:
            return int(round(_RIGHT_W * (1.0 + (f - 1.0) * 0.5)))

        base = int(round(_RIGHT_W * f))

        # Above Default the log may also claim a share of the window.
        #
        # The centre column takes whatever is left over, and on Home most of
        # what it takes is empty: the orb paints a circle of min(width,
        # height) and the height is the smaller of the two, so at a 1600px
        # window it was sitting in an 880px-wide box drawing a 432px circle
        # and leaving roughly 450px of bare canvas either side. That space is
        # worth more to the conversation than to the margin around a
        # decoration. Measured against the window, which does not depend on
        # this figure, so there is no circle to resolve.
        cw = self.centralWidget()
        if cw is not None and cw.width() > 0:
            base = max(base, int(cw.width() * self._LOG_SHARE))
        return base

    def _log_bonus(self) -> int:
        """What the sidebar is currently lending the log. Home only."""
        if not getattr(self, "_sidebar_collapsed", False):
            return 0
        if getattr(self, "_active_nav", "home") != "home":
            return 0
        # Only when the fold was a choice. This is the affordance for pressing
        # ☰ on a canvas that could have held all three columns: the freed
        # strip goes to the log rather than stretching the page. When the
        # layout folded the column itself, it did so because the room was not
        # there — handing the strip to the log then pushes the page straight
        # back off the right-hand edge, which is exactly what it was folded to
        # prevent.
        if getattr(self, "_layout_mode", self._LAYOUT_WIDE) != self._LAYOUT_WIDE:
            return 0
        shell = getattr(self, "_left_panel", None)
        return shell.column_width() if shell is not None else 0

    def _apply_log_width(self):
        """Set the log to its width for the current page, state and text size."""
        rp = getattr(self, "_right_panel", None)
        if rp is None or getattr(self, "_sidebar_anim", None) is not None:
            return
        if getattr(self, "_layout_mode", self._LAYOUT_WIDE) == self._LAYOUT_STACKED:
            # Stacked, the log spans the page and the layout owns its width.
            # A fixed width here would pin it to a column that no longer
            # exists, leaving it stranded against the left edge.
            rp.setMinimumWidth(0)
            rp.setMaximumWidth(_QWIDGETSIZE_MAX)
            return
        want = self._log_base_width() + self._log_bonus()
        # A range, not a pin. At its preferred width the log is what it has
        # always been; the give is there so that on a canvas which fits three
        # columns by a hair, the last few pixels come out of the log — whose
        # text wraps — instead of the whole panel hanging over the right edge.
        # It is the most compressible thing in the row, so it should be the
        # one that yields.
        if rp.maximumWidth() != want:
            rp.setMaximumWidth(want)
            rp.setMinimumWidth(max(0, want - self._LOG_GIVE))

    # ── the button's two states ──────────────────────────────────
    #
    # In frame: the plain ☰ it has always been.
    # Out of frame: a filled, tinted button showing ▸ — it reads as "bring it
    # back", and being solid rather than glass makes it obvious at a glance
    # which state you are in, which matters for the user this app is for.

    def _hamburger_qss(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: {C.PRIMARY};
                    border: 1px solid {C.PRIMARY};
                    border-radius: 12px;
                    color: #FFFFFF;
                }}"""
        return f"""
            QPushButton {{
                background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                    stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                border: 1px solid rgba(255,255,255,0.7);
                border-radius: 12px;
                color: {C.ON_SURFACE_VAR};
            }}"""

    def _update_hamburger_btn(self):
        btn = getattr(self, "_hamburger_btn", None)
        if btn is None:
            return
        out = getattr(self, "_sidebar_collapsed", False)
        btn.setText("▸" if out else "☰")
        btn.setStyleSheet(self._hamburger_qss(out))
        btn.setToolTip("Show the menu" if out else "Hide the menu")

    def _drawer_nav(self, key: str):
        """Handle page selection from the hamburger drawer."""
        self._go_to_page(key)

    def _go_to_page(self, key: str):
        """Navigate to a centre-stack page by key name."""
        self._active_nav = key
        self._style_nav_btns()
        self._centre_stack.setCurrentIndex(self._PAGE_MAP.get(key, 0))
        self._global_back_btn.setVisible(key != "home")

        self._right_panel.setVisible(self._log_wanted())
        self._update_responsive_panels()
        self._apply_log_width()

    def open_safety_check(self, path: str | None = None, text: str | None = None,
                          run: bool = True):
        """Go to the Safety Check page, optionally with something already loaded."""
        self._go_to_page("safety")
        try:
            self._safety_page.preload(path=path, text=text, run=run)
        except (AttributeError, RuntimeError):
            pass

    def _show_safety_verdict(self, verdict):
        """GUI-thread end of `_safety_verdict_sig`."""
        self._go_to_page("safety")
        try:
            self._safety_page.show_verdict(verdict)
        except (AttributeError, RuntimeError):
            pass

    def _serve_capture_request(self, box: dict):
        """GUI-thread end of `_capture_req_sig`.

        Hiding the window, pumping the event queue and waiting for the
        compositor can only be done from the thread that owns the window, so
        the worker asks for it and blocks on the event in `box`.
        """
        try:
            box["png"] = self._capture_behind_window(self._grab_screen_png)
        except Exception as e:
            box["err"] = e
        finally:
            box["done"].set()

    def _on_check_safety_clicked(self):
        """The 'Is this safe?' button under the activity-log drop zone."""
        path = self._drop_zone.current_file()
        if path:
            self.open_safety_check(path=path)
        else:
            self.open_safety_check()

    def _refresh_sidebar_reminders(self):
        """Reload sidebar reminder list from the JSON store."""
        if not _HAS_REMINDER_STORE:
            return

        # Clear existing items
        while self._reminder_list.count():
            item = self._reminder_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_reminder_layout(item.layout())

        reminders = store_get_active()
        now = datetime.now()

        # Show up to 3 upcoming reminders in sidebar
        for rem in reminders[:3]:
            done = rem.get("done", False)
            msg = rem.get("message", "Reminder")
            date_str = rem.get("date", "")
            time_str = rem.get("time", "")

            overdue = False
            try:
                target = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                overdue = target < now
            except Exception:
                pass

            self._add_reminder_item(msg, done=done, overdue=overdue,
                                     date_str=date_str, time_str=time_str)

        if not reminders:
            empty_lbl = QLabel("No upcoming reminders")
            empty_lbl.setFont(_make_font(size=10))
            empty_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._reminder_list.addWidget(empty_lbl)

    def _clear_reminder_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_reminder_layout(item.layout())

    def refresh_reminders(self):
        """Public method — called from main.py after AI sets a reminder."""
        self._refresh_sidebar_reminders()
        if hasattr(self, '_reminders_page'):
            self._reminders_page.refresh_list()

    # ═════════════════════════════════════════════════════════════
    # CENTRE CANVAS (QStackedWidget)
    # ═════════════════════════════════════════════════════════════

    def _build_centre_canvas(self) -> QStackedWidget:
        stack = QStackedWidget()
        stack.setStyleSheet("background: transparent;")

        # Page 0 — Orb
        orb_page = QWidget()
        orb_page.setStyleSheet("background: transparent;")
        # A QBoxLayout rather than a QVBoxLayout purely so the axis can be
        # turned. Stacked on a narrow canvas the orb moves to sit beside the
        # quick actions instead of above them, which is one setDirection()
        # call — no reparenting, and the same two widgets either way.
        olay = QBoxLayout(QBoxLayout.Direction.TopToBottom, orb_page)
        olay.setContentsMargins(0, 0, 0, 0)
        olay.setSpacing(0)
        self._orb_layout = olay



        # Orb widget
        self.hud = OrbWidget()
        olay.addWidget(self.hud, stretch=1)



        # The state pill lives in the bottom bar (_build_bottom_pill_bar), not
        # here. A second copy used to sit under the orb; because it was built
        # first, _build_bottom_pill_bar's assignment to self._state_lbl won,
        # and this one was never updated again — it showed AWAITING_INPUT for
        # the whole session while the real state changed below it.

        # Quick Actions Grid
        grid_w = QWidget()
        main_quick_lay = QVBoxLayout(grid_w)
        main_quick_lay.setContentsMargins(40, 20, 40, 40)
        main_quick_lay.setSpacing(10)
        
        # Three across only fits while the labels are small. Past the middle
        # text size, drop to two across and use more rows, rather than letting
        # "Weather (Web)" and "Screenshot" clip. A grid, not built-once rows,
        # so _relayout_quick_actions can move the same buttons to new cells
        # live when the text size changes, instead of this being frozen at
        # whatever size the page happened to be built at.
        self._quick_grid = QGridLayout()
        self._quick_grid.setSpacing(10)
        main_quick_lay.addLayout(self._quick_grid)
        self._quick_grid_w = grid_w      # the walkthrough spotlights this
        self._quick_btns: list[QPushButton] = []

        actions = [
            ("🌤", "Weather", "what's the weather"),
            # LOCAL: so the URL can be built at click time. As a fixed
            # "URL:...?q=weather" this opened a generic weather page; the link
            # now names the place SIVA already knows you are in. Cache-only —
            # see web_weather_url — so the click stays instant.
            ("⛅", "Weather (Web)", "LOCAL:weather_web"),
            ("🌐", "Google", "URL:https://www.google.com"),
            # LOCAL: rather than a sentence for the model to interpret. Both of
            # these have to hide this window before the picture is taken, which
            # only the window itself can do — and neither needs the assistant's
            # judgement to decide what was meant.
            ("📸", "Screenshot", "LOCAL:screenshot"),
            ("👁", "Read Screen", "LOCAL:read_screen"),
            ("🛡", "Is this safe?", "LOCAL:safety"),
            ("📂", "Files", "open file manager"),
            # URL: rather than the gmail_navigator tool. This opens the user's
            # own browser, already signed in, so it works with no setup at all.
            # The tool drives a separate SIVA-only Chrome profile that has to be
            # signed into once per machine — right for reading mail aloud, wrong
            # for a button whose whole job is "show me my email, now".
            ("📧", "Gmail", "URL:https://mail.google.com"),
            ("assets/whatsapp.png", "WhatsApp", "open whatsapp"),
        ]
        
        for i, (icon, name, cmd) in enumerate(actions):
            if icon.endswith(".png"):
                from PyQt6.QtGui import QIcon
                from PyQt6.QtCore import QSize
                btn = AnimatedButton(f"  {name}")
                btn.setIcon(QIcon(str(BASE_DIR / icon)))
                btn.setIconSize(QSize(20, 20))
            else:
                btn = AnimatedButton(f"{icon} {name}")
                
            btn.setFont(_make_font(size=12, bold=True))
            btn.setFixedHeight(40)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.4);
                    border: 1px solid rgba(139,94,60,0.2);
                    border-radius: 12px;
                    color: {C.ON_SURFACE_VAR};
                    padding: 0px 10px;
                }}
                QPushButton:hover {{ background: rgba(255,255,255,0.8); }}
            """)
            btn.clicked.connect(lambda _, c=cmd: self._quick_action(c))
            self._quick_btns.append(btn)

        self._relayout_quick_actions()
        text_scale.instance().changed.connect(
            lambda _l: self._relayout_quick_actions())

        olay.addWidget(grid_w)

        stack.addWidget(orb_page)

        # Page 1 — Health Status
        self._health_page = HealthStatusPage()
        stack.addWidget(self._health_page)

        # Page 2 — Reminders (full page)
        self._reminders_page = RemindersPage()
        self._reminders_page.reminder_created.connect(self._refresh_sidebar_reminders)
        stack.addWidget(self._reminders_page)

        # Page 3 — Notifications (placeholder)
        stack.addWidget(PlaceholderPage(
            "🔔", "Notifications",
            "Coming soon — view your notification history and alerts"
        ))

        # Page 4 — Task Lists
        self._tasks_page = TasksPage()
        stack.addWidget(self._tasks_page)

        # Page 5 — Notes (placeholder)
        stack.addWidget(PlaceholderPage(
            "📝", "Notes",
            "Coming soon — quick notes and voice memos"
        ))
        
        # Page 6 — Chat History
        self._chat_history_page = ChatHistoryPage()
        stack.addWidget(self._chat_history_page)
        
        # Page 7 — Settings
        self._settings_page = SettingsPage()
        stack.addWidget(self._settings_page)

        # Page 8 — Safety Check. Appended last on purpose: every index above is
        # positional, so adding anywhere else renumbers pages that other code
        # reaches by number.
        self._safety_page = SafetyPage()
        stack.addWidget(self._safety_page)

        return stack

    # ═════════════════════════════════════════════════════════════
    # RIGHT PANEL — ACTIVITY LOG
    # ═════════════════════════════════════════════════════════════

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"""
            QWidget {{
                background: rgba(255,255,255,0.25);
                border-left: 1px solid rgba(255,255,255,0.5);
            }}""")

        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        # Header
        header = QHBoxLayout()
        h_title = QLabel("ACTIVITY LOG")
        h_title.setFont(_make_font(_FONT_HUD, 16, bold=True))
        h_title.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; letter-spacing: 2px; border: none;")
        header.addWidget(h_title)
        header.addStretch()
        h_icon = QLabel("📋")
        h_icon.setFont(_make_font(size=18))
        h_icon.setStyleSheet("""
            background: rgba(255,255,255,0.4);
            border-radius: 12px;
            padding: 6px;
            border: none;
        """)
        header.addWidget(h_icon)
        lay.addLayout(header)

        # Chat bubbles
        self._log = ChatBubbleArea()
        lay.addWidget(self._log, stretch=1)

        # Chat input row
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("Type a command...")
        self._chat_input.setFont(_make_font(size=14))
        self._chat_input.setFixedHeight(44)
        self._chat_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.60);
                color: {C.ON_SURFACE};
                border: 1px solid rgba(139,94,60,0.25);
                border-radius: 12px;
                padding: 6px 16px;
                font-size: 14px;
            }}QLineEdit:focus {{
                border: 1px solid {C.PRIMARY};
            }}""")
        self._chat_input.returnPressed.connect(self._on_chat_submit)
        input_row.addWidget(self._chat_input)

        send_btn = AnimatedButton("➤")
        send_btn.setFont(_make_font(size=16, bold=True))
        send_btn.setFixedSize(44, 44)
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRIMARY};
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 16px;
            }}""")
        send_btn.clicked.connect(self._on_chat_submit)
        input_row.addWidget(send_btn)

        lay.addLayout(input_row)

        # File drop zone
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        # Sits below the zone rather than inside it: the whole surface of the
        # zone is already a click target, and its height is owned by
        # _apply_density. Hidden until there is actually a file to check.
        self._safety_btn = AnimatedButton("🛡  Is this safe?")
        self._safety_btn.setFixedHeight(36)
        self._safety_btn.setFont(_make_font(size=12, bold=True))
        self._safety_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.55); color: {C.SECONDARY};
                border: 1px solid rgba(139,94,60,0.35);
                border-radius: 10px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.8); }}""")
        self._safety_btn.hide()
        self._safety_btn.clicked.connect(self._on_check_safety_clicked)
        lay.addWidget(self._safety_btn)

        # ── Shell ────────────────────────────────────────────────────
        #
        # The log's width is animated on Home when the sidebar collapses, so
        # MainWindow owns it (_apply_log_width) rather than the text engine —
        # the scaler records whatever width it finds as the new design width,
        # so a widened log would be grown again on the next text-size change,
        # and again after that. The panel inside is left untouched, so every
        # other scaled property, its own layout margins included, still works
        # exactly as before.
        shell = QWidget()
        shell.setProperty("_ts_ignore_geom", True)
        shell.setFixedWidth(_RIGHT_W)
        s_lay = QHBoxLayout(shell)
        s_lay.setContentsMargins(0, 0, 0, 0)
        s_lay.setSpacing(0)
        s_lay.addWidget(w)
        _glass_shadow(shell, blur=20, dy=0)

        self._right_inner = w
        return shell

    # ═════════════════════════════════════════════════════════════
    # BOTTOM PILL BAR
    # ═════════════════════════════════════════════════════════════

    def _build_bottom_pill_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(180)
        bar.setStyleSheet("background: transparent;")

        main_lay = QVBoxLayout(bar)
        main_lay.setContentsMargins(0, 0, 0, 16)
        main_lay.setSpacing(12)

        # --- State label pill (Moved here) ---
        state_pill = QWidget()
        state_pill.setStyleSheet("""
            background: rgba(255,255,255,0.25);
            border: 1px solid rgba(255,255,255,0.4);
            border-radius: 20px;
        """)
        s_lay = QHBoxLayout(state_pill)
        s_lay.setContentsMargins(16, 8, 16, 8)
        self._state_lbl = QLabel("💤  Sleeping")
        self._state_lbl.setFont(_make_font(_FONT_HUD, 13, bold=True))
        self._state_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent; letter-spacing: 1px; border: none;")
        s_lay.addWidget(self._state_lbl)
        
        state_row = QHBoxLayout()
        state_row.addStretch()
        state_row.addWidget(state_pill)
        state_row.addStretch()
        main_lay.addLayout(state_row)
        self._state_pill = state_pill    # the walkthrough spotlights this

        # --- Buttons ---
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()

        # Pill container (Glowing, Circular, Glassmorphism)
        pill = QWidget()
        pill.setStyleSheet("""
            QWidget#pillBar {
                background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                    stop:0 rgba(255,255,255,180), stop:1 rgba(255,255,255,100));
                border: 2px solid rgba(255,255,255,0.70);
                border-radius: 48px;
            }
        """)
        pill.setObjectName("pillBar")
        
        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(40)
        glow.setColor(QColor(255, 255, 255, 160))
        glow.setOffset(0, 0)
        pill.setGraphicsEffect(glow)

        play = QHBoxLayout(pill)
        play.setContentsMargins(16, 10, 16, 10)
        play.setSpacing(24)

        # Remote button
        remote_btn = self._make_pill_icon_btn("📱", "Remote")
        remote_btn.clicked.connect(self._open_remote)
        play.addWidget(remote_btn)

        # Talk + Listen pill
        tl_pill = QFrame()
        tl_pill.setObjectName("tlPill")
        tl_pill.setStyleSheet("""
            QFrame#tlPill {
                background: rgba(255,255,255,0.70);
                border: 1px solid rgba(255,255,255,0.90);
                border-radius: 30px;
            }
        """)
        tl_lay = QHBoxLayout(tl_pill)
        tl_lay.setContentsMargins(6, 6, 6, 6)
        tl_lay.setSpacing(0)

        self._talk_btn = AnimatedButton("  🎙  Talk")
        self._talk_btn.setFont(_make_font(size=15, bold=True))
        self._talk_btn.setFixedHeight(48)
        self._talk_btn.setMinimumWidth(120)
        self._talk_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._talk_btn.pressed.connect(self._on_talk_pressed)
        self._talk_btn.released.connect(self._on_talk_released)
        tl_lay.addWidget(self._talk_btn)

        self._listen_btn = AnimatedButton("  👂  Listen")
        self._listen_btn.setFont(_make_font(size=15, bold=True))
        self._listen_btn.setFixedHeight(48)
        self._listen_btn.setMinimumWidth(120)
        self._listen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._listen_btn.clicked.connect(self._on_listen_click)
        tl_lay.addWidget(self._listen_btn)

        play.addWidget(tl_pill)
        self._tl_pill = tl_pill          # the walkthrough spotlights this

        # Quiet button
        self._quiet_btn = self._make_pill_icon_btn("🔇", "Quiet")
        self._quiet_btn.clicked.connect(self._toggle_mute)
        play.addWidget(self._quiet_btn)
        
        self._style_talk_listen()

        outer.addWidget(pill)
        outer.addStretch()
        main_lay.addLayout(outer)

        return bar

    def _make_pill_icon_btn(self, icon: str, label: str) -> QPushButton:
        btn = AnimatedButton(f"{icon}\n{label}")
        btn.setFont(_make_font(size=12, bold=True))
        btn.setFixedSize(80, 64)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                    stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                border: 1px solid rgba(255,255,255,0.7);
                border-radius: 32px;
                color: {C.ON_SURFACE_VAR};
            }}""")
        return btn

    @property
    def _muted(self) -> bool:
        return not (self._continuous_listen or self._talk_pressed)

    def _style_talk_listen(self):
        if self._muted:
            # Both inactive when muted
            self._talk_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C.ON_SURFACE_VAR};
                    border-radius: 24px;
                    border: none;
                    font-size: 15px;
                }}""")
            self._listen_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C.ON_SURFACE_VAR};
                    border-radius: 24px;
                    border: none;
                    font-size: 15px;
                }}""")
            self._quiet_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRIMARY};
                    color: white;
                    border-radius: 32px;
                    border: none;
                }}""")
        else:
            talk_bg = C.PRIMARY if self._talk_pressed else "transparent"
            talk_fg = "white" if self._talk_pressed else C.ON_SURFACE_VAR
            listen_bg = C.PRIMARY if self._continuous_listen else "transparent"
            listen_fg = "white" if self._continuous_listen else C.ON_SURFACE_VAR
            
            self._talk_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {talk_bg};
                    color: {talk_fg};
                    border-radius: 24px;
                    border: none;
                    font-size: 15px;
                }}""")
            self._listen_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {listen_bg};
                    color: {listen_fg};
                    border-radius: 24px;
                    border: none;
                    font-size: 15px;
                }}""")

            self._quiet_btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0 y1:0, x2:1 y2:1,
                        stop:0 rgba(255,255,255,200), stop:1 rgba(255,255,255,100));
                    border: 1px solid rgba(255,255,255,0.7);
                    border-radius: 32px;
                    color: {C.ON_SURFACE_VAR};
                }}""")

    # ═════════════════════════════════════════════════════════════
    # ACTIONS & CALLBACKS
    # ═════════════════════════════════════════════════════════════

    def _mic_allowed(self) -> bool:
        """Ask for the microphone, and report whether it is already allowed.

        The state is deliberately left alone on the first press. Talk is a
        press-and-hold, so switching the microphone on underneath a card that
        has not been answered yet would mean the app started listening while
        still asking whether it may — which is the one thing that would make
        this gate a lie. After Allow, the next press works normally.
        """
        if permissions.is_granted(permissions.MICROPHONE):
            return True
        self.ask_permission(permissions.MICROPHONE)
        return False

    def _on_talk_pressed(self):
        if not self._mic_allowed():
            return
        self._talk_pressed = True
        self._continuous_listen = False
        self.hud.muted = self._muted
        self._style_talk_listen()
        self._apply_state("MIC_ON")

    def _on_talk_released(self):
        self._talk_pressed = False
        self.hud.muted = self._muted
        self._style_talk_listen()
        if self._muted:
            self._apply_state("MIC_OFF")

    def _on_listen_click(self):
        # Switching listening *off* never needs permission — only turning it on.
        if not self._continuous_listen and not self._mic_allowed():
            return
        self._continuous_listen = not self._continuous_listen
        self._talk_pressed = False
        self.hud.muted = self._muted
        self._style_talk_listen()
        if self._continuous_listen:
            self._apply_state("MIC_ON")
            self._log.append_log("SYS: Continuous listening active.")
        else:
            self._apply_state("MIC_OFF")
            self._log.append_log("SYS: Microphone muted.")

    def _on_chat_submit(self):
        """Handle typed chat input — send to AI via on_text_command."""
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self._log.append_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command,
                args=(text,),
                daemon=True,
            ).start()

    def _on_emergency(self):
        from PyQt6.QtWidgets import QMessageBox
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("⚠ EMERGENCY")
        msg_box.setText("Are you sure you want to contact emergency services?")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        
        btn_yes = msg_box.addButton("Yes, Contact Now", QMessageBox.ButtonRole.DestructiveRole)
        btn_no = msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        
        msg_box.setStyleSheet(f"""
            QMessageBox {{ background: {C.BG}; }}
            QLabel {{ color: {C.ON_SURFACE}; font-weight: bold; font-size: 14px; }}
            QPushButton {{
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(139,94,60,0.3);
                border-radius: 6px;
                padding: 6px 14px;
                color: {C.ON_SURFACE};
                font-weight: bold;
            }}
            QPushButton:hover {{ background: rgba(139,94,60,0.1); }}
        """)
        
        # Style the 'Yes' button directly
        btn_yes.setStyleSheet(f"""
            QPushButton {{
                background: rgba(186,26,26,0.15);
                border: 1px solid rgba(186,26,26,0.5);
                color: {C.ERROR};
            }}
            QPushButton:hover {{ background: rgba(186,26,26,0.3); }}
        """)
        
        msg_box.exec()
        if msg_box.clickedButton() == btn_yes:
            if self.on_text_command:
                threading.Thread(
                    target=self.on_text_command,
                    args=("[EMERGENCY] User pressed the emergency button. Contact emergency services immediately.",),
                    daemon=True,
                ).start()
            self._log.append_log("SYS: ⚠ EMERGENCY triggered.")

    def _on_logout(self):
        """The Shut Down button — the same route as the title-bar X.

        It used to send the text "shutdown_siva" to the model as if the user
        had typed it, and wait for the model to decide to call the tool. That
        put the network, the live session and a model's judgement between a
        person pressing Shut Down and the app closing: rate-limited, offline,
        or a session that had dropped, and the button did nothing at all — no
        message, no close, nothing to press instead. Quitting is the one
        action that must never depend on a working internet connection.

        close() raises the same closeEvent the X button does, so the two are
        now the same path by construction rather than by two copies of it.
        """
        self.close()

    def _on_file_selected(self, path: str):
        self._current_file = path
        self._safety_btn.show()
        p = Path(path)
        cat = _file_category(p)
        size = _fmt_size(p.stat().st_size)
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._continuous_listen = False
        self._talk_pressed = False
        self.hud.muted = self._muted
        self._style_talk_listen()
        self._apply_state("MIC_OFF")
        self._log.append_log("SYS: Microphone muted.")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _apply_state(self, state: str):
        self.hud.set_orb_state(state)
        self.hud.speaking = (state == "SPEAKING")

        # Update state label
        if state in ["PROCESSING", "THINKING", "CONNECTING"]:
            import random
            state_text = random.choice([
                "🧠 Processing your request...",
                "⌛ Loading...",
                "📡 Connecting to server...",
                "🤔 Thinking..."
            ])
            self._state_lbl.setText(state_text)
        else:
            label_map = {
                "MIC_OFF": "🔇 Mic Off",
                "MIC_ON": "🎙️ Mic On",
                "LISTENING": "👂 Listening...",
                "SEARCHING": "🌐 Accessing the Internet...",
                "SLEEPING":  "💤 Sleeping",
                "SPEAKING":  "🗣️ Speaking...",
                "ERROR":     "⚠️ Error",
            }
            self._state_lbl.setText(label_map.get(state, f"ℹ️ {state}"))

        # Wi-Fi is checked independently of the assistant's conversational
        # state. An idle or reconnecting live session is not a Wi-Fi failure.

    # ── toasts ───────────────────────────────────────────────────

    def show_toast(self, text: str, ok: bool = True):
        """Say whether something worked, across the top of the window."""
        try:
            cw = self.centralWidget()
            if cw is None:
                return None
            toasts = getattr(self, "_toasts", None)
            if toasts is None:
                toasts = self._toasts = []
            t = Toast(text, ok=ok, parent=cw)
            t.destroyed.connect(lambda *_: self._forget_toast(t))
            toasts.append(t)
            self._place_toasts()
            t.appear()
            return t
        except RuntimeError:
            return None

    def _forget_toast(self, t):
        toasts = getattr(self, "_toasts", None)
        if not toasts:
            return
        try:
            toasts.remove(t)
        except ValueError:
            pass
        self._place_toasts()

    def _place_toasts(self):
        """Stack them down from the top, so a second never hides the first."""
        cw = self.centralWidget()
        toasts = getattr(self, "_toasts", None)
        if cw is None or not toasts:
            return
        y = text_scale.px(18)
        alive = []
        for t in list(toasts):
            try:
                w, h = t.preferred_size(cw.width())
                t.setGeometry((cw.width() - w) // 2, y, w, h)
                t.raise_()
                y += h + text_scale.px(10)
                alive.append(t)
            except RuntimeError:
                pass                      # already deleted; drop it
        self._toasts = alive

    def prompt_recovery_email_if_missing(self):
        """
        No recovery email on file → ask for one, over the dashboard.

        Called once the dashboard is up. Returns True if it put something on
        screen, so the caller knows whether anything happened.
        """
        try:
            from core import recovery
            if recovery.has_recovery_email():
                return False
        except Exception:
            return False

        cw = self.centralWidget()
        ov = RecoveryEmailOverlay(cw)
        # Full-bleed: the overlay *is* the dimmer, and it centres its own card.
        ov.setGeometry(cw.rect())
        ov.closed.connect(lambda: setattr(self, "_email_overlay", None))
        self._email_overlay = ov
        ov.show()
        ov.raise_()
        return True

    # ═════════════════════════════════════════════════════════════
    # PERMISSIONS
    # ═════════════════════════════════════════════════════════════
    #
    # Everything that needs an answer comes through `ask_permission`. It is
    # callback-shaped rather than blocking on purpose: a blocking ask on the GUI
    # thread means a nested event loop, and this process has a websocket, an
    # audio callback and uvicorn all running underneath it — re-entering the
    # event loop from inside a button handler is how that becomes a hang nobody
    # can reproduce. Worker threads get the blocking form, which is safe because
    # they are not the thread that has to keep drawing.

    def ask_permission(self, key: str, then=None):
        """Ask for one capability, then call ``then(allowed)``.

        Already allowed → straight through, but still on the next turn of the
        event loop, so a caller cannot accidentally depend on the answer being
        synchronous in one case and not the other.
        """
        if permissions.is_granted(key):
            if then:
                QTimer.singleShot(0, lambda: then(True))
            return

        perm = permissions.describe(key)
        if perm is None:                  # unknown key: nothing to ask about
            if then:
                QTimer.singleShot(0, lambda: then(True))
            return

        self._perm_queue.append((key, then))
        self._pump_permission_queue()

    def require_permission(self, key: str, action):
        """Run ``action`` only once this capability is allowed."""
        self.ask_permission(key, lambda ok: action() if ok else None)

    def _pump_permission_queue(self):
        if self._perm_overlay is not None or not self._perm_queue:
            return
        key, then = self._perm_queue.pop(0)

        # It may have been granted while this one sat in the queue — two
        # buttons pressed in quick succession can both want the same card.
        if permissions.is_granted(key):
            if then:
                then(True)
            self._pump_permission_queue()
            return

        perm = permissions.describe(key)
        cw = self.centralWidget()
        ov = PermissionOverlay(perm, cw)
        ov.setGeometry(cw.rect())
        ov.answered.connect(lambda ok, k=key, cb=then: self._on_permission_answer(k, ok, cb))
        self._perm_overlay = ov
        ov.show()
        ov.raise_()
        ov.setFocus()

    def _on_permission_answer(self, key: str, allowed: bool, then):
        # Only "yes" is written. A refusal blocks this attempt and nothing more
        # — see the note at the top of core/permissions.py.
        if allowed:
            permissions.grant(key)
        self._perm_overlay = None
        try:
            self._log.append_log(
                f"SYS: Permission {'allowed' if allowed else 'not given'} — {key}")
        except (AttributeError, RuntimeError):
            pass
        if then:
            then(bool(allowed))
        self._refresh_permission_rows()
        self._pump_permission_queue()

    def _serve_permission_request(self, key: str, box):
        """GUI-thread end of `_permission_sig` — see SivaUI.request_permission.

        The worker is sitting on ``box["done"]``; every path out of here has to
        set it, or the tool call waits out its whole timeout for nothing.
        """
        def answer(ok: bool):
            box["ok"] = bool(ok)
            box["done"].set()

        try:
            self.ask_permission(key, answer)
        except Exception:
            answer(False)

    def _refresh_permission_rows(self):
        """Keep the Settings list in step with what was just answered."""
        page = getattr(self, "_settings_page", None)
        if page is None:
            return
        try:
            page.refresh_permission_rows()
        except (AttributeError, RuntimeError):
            pass

    # ═════════════════════════════════════════════════════════════
    # FIRST-RUN WALKTHROUGH
    # ═════════════════════════════════════════════════════════════

    def tour_anchor(self, name: str):
        """The real control a tour step points at, or None.

        Resolved by name and only when the step is reached, never held as a
        reference: several of these are rebuilt when the layout mode changes,
        and a stored pointer to a deleted widget is how a tour would take the
        whole app down (§7 gotcha 1, wearing different clothes).
        """
        lookup = {
            "talk":           lambda: self._tl_pill,
            "quick":          lambda: self._quick_grid_w,
            "orb":            lambda: self.hud,
            "state":          lambda: self._state_pill,
            "chat":           lambda: self._chat_input,
            "log":            lambda: self._right_panel,
            "reminders_form": lambda: self._reminders_page._form_card,
            "medications":    lambda: self._health_page._add_med_btn,
            "text_size":      lambda: self._settings_page._text_card,
            "permissions":    lambda: self._settings_page._perm_card,
            "developer":      lambda: self._settings_page._dev_card,
        }
        getter = lookup.get(name)
        if getter is None:
            return None
        try:
            return getter()
        except (AttributeError, RuntimeError):
            return None

    def start_walkthrough(self, force: bool = False):
        """Play the tour now. `force` is unused — kept so the queued signal and
        the direct call have the same signature."""
        if self._tour is not None:
            return
        cw = self.centralWidget()
        if cw is None:
            return
        # Home first, whatever page they were on, so step one lands somewhere
        # recognisable.
        try:
            self._go_to_page("home")
        except Exception:
            pass
        # Guarded, because this is the very first thing that happens on a first
        # launch. A tour that cannot be built must cost the tour and nothing
        # else — never the dashboard behind it, and never whatever was queued
        # to run after it.
        try:
            tour = TourOverlay(self, cw)
            tour.setGeometry(cw.rect())
            tour.finished.connect(self._on_tour_finished)
            self._tour = tour
            tour.begin()
        except Exception as e:
            print(f"[SIVA] Walkthrough could not start: {e}")
            self._tour = None
            self._on_tour_finished()

    def _on_tour_finished(self):
        self._tour = None
        after, self._after_tour = self._after_tour, None
        if after:
            # One beat later, so the tour is off the screen before whatever was
            # waiting for it appears. Two cards arriving together is the thing
            # the recovery-email path already goes out of its way to avoid.
            QTimer.singleShot(600, after)

    def maybe_start_walkthrough(self, then=None):
        """Play the tour if this looks like a first run. Returns True if it did.

        `then` is run afterwards either way, so a caller can queue something
        behind it without having to know whether it ran.
        """
        try:
            due = not permissions.walkthrough_done()
        except Exception:
            due = False
        # Never over the API-key setup card: that one has to be answered before
        # anything in the app works, and _on_setup_done calls back here.
        if due and getattr(self, "_overlay", None) is not None:
            due = False
        if not due:
            if then:
                then()
            return False
        self._after_tour = then
        self.start_walkthrough()
        return True

    def reset_first_run(self, run_tour: bool = True):
        """Back to a blank slate — what the demo button in Settings calls."""
        permissions.reset_all()
        permissions.set_walkthrough_done(False)
        self._refresh_permission_rows()
        try:
            self._log.append_log("SYS: Permissions cleared — first-run state restored.")
        except (AttributeError, RuntimeError):
            pass
        if run_tour:
            self.start_walkthrough()

    # Read the Windows Wi-Fi adapter state. This deliberately does not probe
    # the internet: blocked DNS or a firewall is not disconnected Wi-Fi.
    _WIFI_CHECK_MS = 5_000
    _NET_CHECK_TTL = 15.0  # retained for the unused legacy helper below

    def _network_is_down(self) -> bool:
        """
        Is the machine actually offline?

        One UDP-port TCP connect to a public resolver, which either answers in
        a few milliseconds or is genuinely unreachable. Cached, so going in and
        out of idle cannot turn into a stream of network calls, and given a
        short timeout because this runs on the GUI thread — a moment's pause
        when the network really is down is a fair price for not lying to the
        user about it when it is not.
        """
        now = time.time()
        if now - getattr(self, "_net_checked_at", 0.0) < self._NET_CHECK_TTL:
            return getattr(self, "_net_down", False)
        self._net_checked_at = now
        import socket
        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=0.6):
                self._net_down = False
        except OSError:
            self._net_down = True
        return self._net_down

    @staticmethod
    def _windows_wifi_state(output: str) -> bool | None:
        """Return connected/disconnected, or None when Windows gives no state."""
        for line in output.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip().lower() == "state":
                state = value.strip().lower()
                if state == "connected":
                    return True
                if state in {"disconnected", "disconnected by user", "not ready"}:
                    return False
                return None
        return None

    @staticmethod
    def _windows_adapter_wifi_state(output: str) -> bool | None:
        """Fallback for ``netsh interface`` when WLAN details are unavailable."""
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            state, name = parts[1].lower(), " ".join(parts[3:]).lower()
            if "wi-fi" in name or "wifi" in name or "wlan" in name:
                if state == "connected":
                    return True
                if state == "disconnected":
                    return False
        return None

    def _wifi_connected(self) -> bool | None:
        """Ask Windows about the physical Wi-Fi link; never test the internet."""
        if platform.system() != "Windows":
            return None
        try:
            import subprocess
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if not result.returncode:
            state = self._windows_wifi_state(result.stdout)
            if state is not None:
                return state

        # Detailed WLAN information is sometimes restricted by device policy.
        # The adapter list is still enough to tell whether the Wi-Fi link is up.
        try:
            result = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True, text=True, timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode:
            return None
        return self._windows_adapter_wifi_state(result.stdout)

    def _check_wifi_connection(self):
        """Show reconnect only after Windows confirms a stable disconnect."""
        connected = self._wifi_connected()
        if connected is True:
            self._wifi_disconnect_polls = 0
            self._wifi_overlay_dismissed = False
            self._hide_wifi_overlay()
            return
        if connected is not False:
            return                              # cannot determine: never alarm

        # Roaming between access points can briefly report "disconnected".
        # Require two readings before interrupting the user.
        self._wifi_disconnect_polls += 1
        if (self._wifi_disconnect_polls >= 2
                and not self._wifi_overlay_dismissed):
            self._show_wifi_overlay()

    def _show_wifi_overlay(self):
        if self._wifi_overlay and self._wifi_overlay.isVisible():
            return
        cw = self.centralWidget()
        self._wifi_overlay = WiFiDisconnectedOverlay(cw)
        _place_overlay(self._wifi_overlay, cw,
                       WiFiDisconnectedOverlay._OW, WiFiDisconnectedOverlay._OH)
        self._wifi_overlay.closed.connect(self._on_wifi_overlay_closed)
        self._wifi_overlay.show()
        self._wifi_overlay.raise_()

    def _hide_wifi_overlay(self):
        if self._wifi_overlay and self._wifi_overlay.isVisible():
            self._wifi_overlay.hide()
            self._wifi_overlay.deleteLater()
            self._wifi_overlay = None

    def _on_wifi_overlay_closed(self):
        self._wifi_overlay_dismissed = True
        self._wifi_overlay = None

    # ── Remote ──

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw = self.centralWidget()
        ov = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                              expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        _place_overlay(ov, cw, RemoteKeyOverlay._OW, RemoteKeyOverlay._OH)
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    # ── Config / Setup ──

    def _show_notifications(self):
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
            QWidget, QFrame, QGraphicsDropShadowEffect,
        )
        from datetime import datetime, timedelta

        d = QDialog(self)
        d.setWindowTitle("Notifications")
        d.setFixedSize(460, 560)
        d.setStyleSheet(f"""
            QDialog {{
                background: {C.BG};
            }}
        """)

        root_lay = QVBoxLayout(d)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── Header bar ──
        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet(f"""
            background: qlineargradient(x1:0 y1:0, x2:1 y2:0,
                stop:0 rgba(55,103,87,0.08), stop:1 rgba(139,94,60,0.06));
            border-bottom: 1px solid rgba(139,94,60,0.15);
        """)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        title_icon = QLabel("🔔")
        title_icon.setFont(_make_font(size=20))
        title_icon.setStyleSheet("background: transparent;")
        h_lay.addWidget(title_icon)

        title = QLabel("Notifications")
        title.setFont(_make_font(size=18, bold=True))
        title.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent;")
        h_lay.addWidget(title)
        h_lay.addStretch()

        # Count badge
        notif_count = self._count_total_notifications()
        if notif_count > 0:
            badge = QLabel(str(notif_count))
            badge.setFont(_make_font(_FONT_HUD, 11, bold=True))
            badge.setFixedSize(28, 28)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"""
                background: {C.PRIMARY};
                color: white;
                border-radius: 14px;
            """)
            h_lay.addWidget(badge)

        root_lay.addWidget(header)

        # ── Scrollable content ──
        scroll = AnimatedScrollArea(
            show_gradients=True,
            gradient_color=(235, 231, 223),
            gradient_height=50
        )

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_lay = QVBoxLayout(content)
        c_lay.setContentsMargins(16, 12, 16, 16)
        c_lay.setSpacing(12)

        now = datetime.now()
        has_any = False

        # ═══════ SECTION 1: UPCOMING REMINDERS ═══════
        upcoming_reminders = []
        past_reminders = []
        
        if _HAS_REMINDER_STORE:
            all_reminders = store_get_all_sorted()
            for rem in all_reminders:
                date_str = rem.get("date", "")
                time_str = rem.get("time", "")
                is_done = rem.get("done", False)
                try:
                    target = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                except Exception:
                    target = now
                
                if is_done or target < now:
                    past_reminders.append((target, rem))
                else:
                    upcoming_reminders.append((target, rem))
                    
            upcoming_reminders.sort(key=lambda x: x[0])
            past_reminders.sort(key=lambda x: x[0], reverse=True)

        if upcoming_reminders:
            has_any = True
            c_lay.addWidget(self._notif_section_header("⏰", "Upcoming Reminders", f"{len(upcoming_reminders)} active"))

            for target_dt, rem in upcoming_reminders:
                msg = rem.get("message", "Reminder")
                date_str = rem.get("date", "")
                time_str = rem.get("time", "")

                delta = target_dt - now
                if delta.total_seconds() < 3600:
                    urgency = "soon"
                    mins = int(delta.total_seconds() / 60)
                    time_label = f"In {mins} min" if mins > 0 else "Now"
                    icon = "🟠"
                elif delta.total_seconds() < 86400:
                    urgency = "today"
                    time_label = f"Today at {time_str}"
                    icon = "🟡"
                else:
                    urgency = "later"
                    try:
                        time_label = target_dt.strftime("%a, %d %b · %I:%M %p")
                    except Exception:
                        time_label = f"{date_str} {time_str}"
                    icon = "🔵"

                card = self._notif_reminder_card(icon, msg, time_label, urgency)
                c_lay.addWidget(card)
                
        if past_reminders:
            has_any = True
            if upcoming_reminders:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: rgba(139,94,60,0.15); border: none;")
                c_lay.addWidget(sep)
                
            c_lay.addWidget(self._notif_section_header("📜", "Past Reminders", f"{len(past_reminders)} earlier"))
            
            # Show up to 15 past reminders
            for target_dt, rem in past_reminders[:15]:
                msg = rem.get("message", "Reminder")
                date_str = rem.get("date", "")
                time_str = rem.get("time", "")
                
                if rem.get("done", False):
                    urgency = "done"
                    icon = "✅"
                    time_label = f"Completed"
                else:
                    urgency = "overdue"
                    icon = "🔴"
                    try:
                        time_label = f"Was {target_dt.strftime('%a, %d %b · %I:%M %p')}"
                    except Exception:
                        time_label = f"Was {date_str} {time_str}"
                        
                card = self._notif_reminder_card(icon, msg, time_label, urgency)
                c_lay.addWidget(card)

        # ═══════ EMPTY STATE ═══════
        if not has_any:
            empty_container = QWidget()
            empty_container.setMinimumHeight(300)
            e_lay = QVBoxLayout(empty_container)
            e_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e_lay.setSpacing(12)

            empty_icon = QLabel("🔕")
            empty_icon.setFont(_make_font(size=48))
            empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_icon.setStyleSheet("background: transparent;")
            e_lay.addWidget(empty_icon)

            empty_title = QLabel("All clear!")
            empty_title.setFont(_make_font(size=16, bold=True))
            empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_title.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent;")
            e_lay.addWidget(empty_title)

            empty_sub = QLabel("No notifications or upcoming reminders")
            empty_sub.setFont(_make_font(size=12))
            empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_sub.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
            e_lay.addWidget(empty_sub)

            c_lay.addWidget(empty_container)

        c_lay.addStretch()
        scroll.setWidget(content)
        root_lay.addWidget(scroll)
        d.exec()

    def _count_total_notifications(self) -> int:
        """Count only active/upcoming reminders."""
        count = 0
        if _HAS_REMINDER_STORE:
            active = store_get_active()
            count += sum(1 for r in active if not r.get("done", False))
        return count

    def _notif_section_header(self, icon: str, title: str, subtitle: str) -> QWidget:
        """Create a styled section header widget."""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setFont(_make_font(size=16))
        icon_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setFont(_make_font(size=14, bold=True))
        title_lbl.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent;")
        lay.addWidget(title_lbl)

        lay.addStretch()

        sub_lbl = QLabel(subtitle)
        sub_lbl.setFont(_make_font(_FONT_HUD, 10))
        sub_lbl.setStyleSheet(f"color: {C.ON_SURFACE_VAR}; background: transparent;")
        lay.addWidget(sub_lbl)

        return w

    def _notif_reminder_card(self, icon: str, message: str, time_label: str, urgency: str) -> QWidget:
        """A reminder notification card styled like a chat bubble."""
        card = QWidget()

        if urgency == "overdue":
            bg = "rgba(186,26,26,0.06)"
            border = "rgba(186,26,26,0.2)"
            time_color = C.ERROR
        elif urgency == "soon":
            bg = "rgba(212,175,55,0.08)"
            border = "rgba(212,175,55,0.25)"
            time_color = "#B8860B"
        else:
            bg = "rgba(255,255,255,0.7)"
            border = "rgba(139,94,60,0.12)"
            time_color = C.ON_SURFACE_VAR

        card.setStyleSheet(f"""
            QWidget {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
        """)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # Icon
        icon_lbl = QLabel(icon)
        icon_lbl.setFont(_make_font(size=18))
        icon_lbl.setFixedWidth(28)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        # Text column
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.setContentsMargins(0, 0, 0, 0)

        msg_lbl = QLabel(message)
        msg_lbl.setFont(_make_font(size=13))
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {C.ON_SURFACE}; background: transparent; border: none;")
        text_col.addWidget(msg_lbl)

        time_lbl = QLabel(time_label)
        time_lbl.setFont(_make_font(_FONT_HUD, 10, bold=True))
        time_lbl.setStyleSheet(f"color: {time_color}; background: transparent; border: none;")
        text_col.addWidget(time_lbl)

        lay.addLayout(text_col, 1)

        return card

    def _notif_sys_card(self, icon: str, text: str, timestamp: str, is_error: bool) -> QWidget:
        """A system notification card styled like a chat bubble (left-aligned, SIVA style)."""
        outer = QWidget()
        outer.setStyleSheet("background: transparent;")
        o_lay = QHBoxLayout(outer)
        o_lay.setContentsMargins(0, 2, 0, 2)
        o_lay.setSpacing(0)

        bubble_wrap = QWidget()
        b_lay = QVBoxLayout(bubble_wrap)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(3)

        bubble = QWidget()
        if is_error:
            bg_color = "rgba(186,26,26,0.06)"
            border_color = "rgba(186,26,26,0.15)"
            text_color = C.ERROR
        else:
            bg_color = "rgba(255,255,255,0.7)"
            border_color = "rgba(139,94,60,0.1)"
            text_color = C.ON_SURFACE

        bubble.setStyleSheet(f"""
            QWidget {{
                background: {bg_color};
                border: 1px solid {border_color};
                border-radius: 12px;
                border-top-left-radius: 4px;
            }}
        """)
        bub_lay = QHBoxLayout(bubble)
        bub_lay.setContentsMargins(12, 10, 12, 10)
        bub_lay.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setFont(_make_font(size=13))
        icon_lbl.setFixedWidth(20)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        bub_lay.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        msg_lbl = QLabel(text)
        msg_lbl.setFont(_make_font(size=12))
        msg_lbl.setWordWrap(True)
        msg_lbl.setMaximumWidth(340)
        msg_lbl.setStyleSheet(f"color: {text_color}; background: transparent; border: none;")
        bub_lay.addWidget(msg_lbl)

        b_lay.addWidget(bubble)

        if timestamp:
            ts_lbl = QLabel(timestamp)
            ts_lbl.setFont(_make_font(_FONT_HUD, 10, bold=True))
            ts_lbl.setStyleSheet(f"color: rgba(139,94,60,0.6); background: transparent; padding-left: 8px;")
            b_lay.addWidget(ts_lbl)

        o_lay.addWidget(bubble_wrap)
        o_lay.addStretch()

        return outer
        
    def _update_bell_dot(self):
        if self._count_total_notifications() > 0:
            self._bell_dot.show()
        else:
            self._bell_dot.hide()
            
    def _relayout_quick_actions(self):
        """Move the quick-action buttons to 3, 2 or 1 across, live."""
        # One across at the largest size. Two columns of buttons whose labels
        # are 60% larger was the single widest thing on Home — measured, the
        # page was asking for 1002px on that account alone, which pushed the
        # activity log past the right-hand edge on a 1600px screen. A single
        # column is also the plainer thing to read, and it is what the layout
        # wants anyway once the page stacks.
        f = text_scale.factor()
        per_row = 3 if f <= 1.18 else 2
        # Stacked, the actions sit in a column beside the orb rather than
        # spanning the page, so there is only room for one.
        if getattr(self, "_layout_mode", self._LAYOUT_WIDE) == self._LAYOUT_STACKED:
            per_row = 1
        for btn in self._quick_btns:
            self._quick_grid.removeWidget(btn)      # addWidget alone won't move it
        for i, btn in enumerate(self._quick_btns):
            self._quick_grid.addWidget(btn, i // per_row, i % per_row)

    # What each quick action is actually about to do. The buttons reach past
    # the assistant straight to the thing itself — a press on WhatsApp opens
    # WhatsApp, it does not ask SIVA to — so the tool gate in main.py never sees
    # them and they have to be gated here as well.
    #
    # "Is this safe?" is absent on purpose: it only opens a page. What the page
    # then does needs permission, and asks for it there.
    _QUICK_PERMS = {
        "open whatsapp":     permissions.WHATSAPP,
        "open file manager": permissions.FILES,
        "what's the weather": permissions.LOCATION,
        "LOCAL:screenshot":  permissions.SCREEN,
        "LOCAL:read_screen": permissions.SCREEN,
        "LOCAL:weather_web": permissions.WEB,
    }

    def _quick_action(self, cmd: str):
        key = self._QUICK_PERMS.get(cmd)
        if key is None and cmd.startswith("URL:"):
            key = permissions.WEB
        if key and not permissions.is_granted(key):
            self.require_permission(key, lambda: self._run_quick_action(cmd))
            return
        self._run_quick_action(cmd)

    def _run_quick_action(self, cmd: str):
        if cmd.startswith("URL:"):
            import webbrowser
            webbrowser.open(cmd[4:])
            return

        if cmd.startswith("LOCAL:"):
            handler = {
                "screenshot":  self._do_screenshot,
                "read_screen": self._do_read_screen,
                "weather_web": self._do_weather_web,
                "safety":      self.open_safety_check,
            }.get(cmd[6:])
            if handler:
                handler()
            return

        if "for " in cmd or cmd.endswith(" "):
            self._chat_input.setText(cmd)
            self._chat_input.setFocus()
        else:
            if self.on_text_command:
                self.on_text_command(cmd)

    def _do_weather_web(self):
        """Open a full forecast in the browser — the other weather button.

        Its counterpart asks SIVA and gets a spoken answer; this one hands you
        the whole week on a page. Reads only the location cache, never the
        network, so it cannot stall the window.
        """
        import webbrowser
        try:
            from actions.weather_report import web_weather_url
            url = web_weather_url()
        except Exception:
            url = "https://www.google.com/search?q=weather"
        webbrowser.open(url)

    # ── screen capture ───────────────────────────────────────────────────────
    #
    # Both buttons below photograph what is *behind* SIVA. The window steps out
    # of the way for the moment the picture is taken and comes straight back —
    # without that, every screenshot is a picture of this dashboard, which is
    # the one thing on screen the user did not need a picture of.

    # Long enough for Windows to repaint what the window was covering. Below
    # about 200ms the shot can still contain a ghost of the hidden window.
    _HIDE_SETTLE = 0.28

    _READ_SCREEN_PROMPT = (
        "Read out what is on this screen. Say what it is showing, then read "
        "the important text on it aloud."
    )

    def _capture_behind_window(self, take):
        """Hide SIVA, run ``take()``, put SIVA back. Returns take()'s result."""
        visible = self.isVisible()
        if visible:
            self.hide()
            QApplication.processEvents()   # make the hide happen now, not later
            time.sleep(self._HIDE_SETTLE)
        try:
            return take()
        finally:
            if visible:
                self.show()
                self.raise_()
                self.activateWindow()

    def _grab_screen_png(self) -> bytes:
        """Full-resolution PNG of the primary screen."""
        try:
            import mss
            import mss.tools
            with mss.mss() as sct:
                mons = sct.monitors      # [0] is every screen combined, [1..] each one
                shot = sct.grab(mons[1] if len(mons) > 1 else mons[0])
                return mss.tools.to_png(shot.rgb, shot.size)
        except Exception:
            import io
            import pyautogui
            buf = io.BytesIO()
            pyautogui.screenshot().save(buf, format="PNG")
            return buf.getvalue()

    def _do_screenshot(self):
        """Take the shot and save it — no snipping tool, no region to drag."""
        try:
            png = self._capture_behind_window(self._grab_screen_png)
        except Exception as e:
            self._log.append_log(f"SYS: Screenshot failed — {e}")
            self.show_toast(f"Screenshot failed — {e}", ok=False)
            return

        folder = Path.home() / "Pictures" / "SIVA Screenshots"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"siva_{datetime.now():%Y%m%d_%H%M%S}.png"
            path.write_bytes(png)
        except Exception as e:
            self._log.append_log(f"SYS: Screenshot could not be saved — {e}")
            self.show_toast(f"Screenshot could not be saved — {e}", ok=False)
            return

        # On the clipboard as well as on disk, so it behaves like the Windows
        # snip it replaces and can be pasted straight into a message.
        pix = QPixmap()
        if pix.loadFromData(png, "PNG"):
            QApplication.clipboard().setPixmap(pix)

        self._log.append_log(
            f"SYS: Screenshot saved — {path.name} (also copied to clipboard)")
        # The picture is taken with the window hidden, so without this the
        # button looks like it did nothing at all: the screen flickers, SIVA
        # comes back, and the only evidence is a line in the activity log the
        # user was not looking at.
        self.show_toast(
            f"Screenshot taken — saved as {path.name} in Pictures › "
            f"SIVA Screenshots, and copied to the clipboard.")

    def _do_read_screen(self):
        """Have SIVA look at the screen and say what is on it."""
        try:
            from actions.screen_processor import analyze_image, capture_screen
        except Exception as e:
            self._log.append_log(f"SYS: Screen reader unavailable — {e}")
            return

        try:
            image, mime = self._capture_behind_window(capture_screen)
        except Exception as e:
            self._log.append_log(f"SYS: Could not read the screen — {e}")
            return

        self._log.append_log("SYS: Reading the screen…")
        # Off the GUI thread: the vision session can take a few seconds to
        # connect on the first call, and it speaks the answer itself. The
        # window is already back on screen by this point.
        threading.Thread(
            target=analyze_image,
            args=(image, mime, self._READ_SCREEN_PROMPT, self),
            daemon=True,
        ).start()

    def write_log(self, text: str):
        """Thread-safe log line. Mirrors SivaUI.write_log so this window can be
        handed to modules that speak back through a `player`."""
        self._log_sig.emit(text)

    def _check_config(self) -> bool:
        if not API_FILE.exists():
            return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        _place_overlay(ov, cw, 520, 560)
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. SIVA online.")
        # A genuinely fresh install reaches the dashboard through this card, not
        # through _on_auth_ready — which ran while this was still up and
        # deliberately declined to start a tour underneath it.
        QTimer.singleShot(700, self.maybe_start_walkthrough)

    # ── Resize ──

    # The narrowest the centre page may become. Design pixels.
    #
    # This grows at the chrome rate, not the panel rate. What sits in the
    # middle of Home is the orb, which is elastic, and the quick actions,
    # which answer bigger text by moving to fewer columns rather than by
    # getting wider. Neither needs width at the rate the words grow, and
    # charging them for it evicted the activity log while there was still
    # room for it — measured, the log was being dropped on a 1600px canvas by
    # a four-pixel margin.
    _CENTRE_MIN_W = 440

    # ── how many columns the canvas can hold ─────────────────────────
    #
    # Driven by measured width, never by the text size on its own. That
    # distinction is the whole point: on a large display the three-column
    # design survives at every text size and nothing is rearranged, and the
    # fallbacks below only appear on a canvas that genuinely cannot seat
    # them. The design is the default and stays the default wherever it fits.

    _LAYOUT_WIDE = 0        # three columns — the interface as designed
    _LAYOUT_COMPACT = 1     # nav column out of frame; page and log share it
    _LAYOUT_STACKED = 2     # one column: log on top, orb beside the actions

    # A window dragged along a boundary would otherwise flip between two
    # layouts on alternate pixels. Coming back up costs a little more than
    # staying put.
    _MODE_HYSTERESIS = 48

    # A little headroom over the measured centre minimum, so a canvas that
    # only just fits three columns folds to two rather than seating them a
    # hair short and pushing the log over the edge. Small on purpose: the
    # minimum it sits on top of is now measured rather than estimated, and
    # anything larger folds the nav column on canvases that could have held
    # it — which is a change to the design, not a fix.
    _CENTRE_SLACK = 16

    # How much of its width the activity log will surrender before the layout
    # is judged not to fit three columns at all.
    _LOG_GIVE = 72

    # The share of the window the log may claim above Default. Applies only
    # above Default — Small and Default keep the fixed column they were drawn
    # with.
    _LOG_SHARE = 0.34

    _LOG_HIDDEN_PAGES = ("reminders", "health", "chat", "safety")

    def _log_wanted(self) -> bool:
        """Whether this page shows the activity log at all."""
        return getattr(self, "_active_nav", "home") not in self._LOG_HIDDEN_PAGES

    def _width_needed(self, with_sidebar: bool) -> int:
        grow = 1.0 + (text_scale.factor() - 1.0) * 0.3
        # _CENTRE_MIN_W is a nominal figure, and Qt's own floor for the centre
        # column comes out above it — a stacked widget reports the widest of
        # all its pages, and the margins and gaps around it are scaled too.
        # Taking the nominal figure at face value picked the three-column
        # layout on a canvas that then could not seat it, and the log ended up
        # about fifty pixels over the right edge rather than folding. Measure
        # the page that is actually on screen, and keep a margin over it.
        # Deliberately the nominal figure, not the minimum the centre column
        # actually reports. Qt's own floor for it is far higher — a stacked
        # widget answers with the widest of all eight pages, most of which are
        # not on screen — and folding the nav column whenever *that* would not
        # fit takes the three-column design away on canvases where it looks
        # perfectly good. The layout is allowed to seat the centre a little
        # under its stated minimum; what it must not do is push the log off
        # the edge, and that is handled where the log's width is set.
        need = (int(self._CENTRE_MIN_W * grow)
                + self._CENTRE_SLACK + _PAGE_MARGIN * 2)
        if self._log_wanted():
            need += self._log_base_width() + _GAP
        if with_sidebar:
            lp = getattr(self, "_left_panel", None)
            need += (lp.column_width() if lp is not None else _LEFT_W) + _GAP
        return need

    def _choose_layout_mode(self) -> int:
        avail = self.centralWidget().width()
        cur = getattr(self, "_layout_mode", self._LAYOUT_WIDE)
        bump = self._MODE_HYSTERESIS
        if avail >= self._width_needed(True) + (0 if cur == self._LAYOUT_WIDE else bump):
            return self._LAYOUT_WIDE
        if not self._log_wanted():
            # Nothing left to fold away — the page already has the canvas.
            return self._LAYOUT_COMPACT
        if avail >= self._width_needed(False) + (bump if cur == self._LAYOUT_STACKED else 0):
            return self._LAYOUT_COMPACT
        return self._LAYOUT_STACKED

    def _update_responsive_panels(self):
        """Re-pick the layout for the canvas we actually have."""
        if getattr(self, "_mode_busy", False):
            return
        rp = getattr(self, "_right_panel", None)
        lp = getattr(self, "_left_panel", None)
        if rp is None or lp is None:
            return
        self._apply_layout_mode(self._choose_layout_mode())

    def _apply_layout_mode(self, mode: int):
        if mode == getattr(self, "_layout_mode", self._LAYOUT_WIDE) \
                and getattr(self, "_mode_applied", False):
            return
        self._mode_busy = True
        try:
            self._layout_mode = mode
            self._mode_applied = True
            rp = self._right_panel
            stacked = mode == self._LAYOUT_STACKED

            # ── where the log lives ──
            # Stacked, it leaves the right-hand column and goes on top of the
            # page, full width: at that size the conversation is the thing
            # being read, and a column narrow enough to sit beside the page
            # would break every line in half.
            in_centre = getattr(self, "_log_in_centre", False)
            if stacked and not in_centre:
                self._body_lay.removeWidget(rp)
                self._centre_lay.insertWidget(1, rp, stretch=2)
                self._log_in_centre = True
            elif not stacked and in_centre:
                self._centre_lay.removeWidget(rp)
                self._body_lay.addWidget(rp, stretch=0)
                self._log_in_centre = False
                # Straight back to a column width, here and now. Leaving it to
                # _apply_log_width below would not do it: un-folding the nav
                # column starts an animation, and that function stands aside
                # while one is running. The log would spend those 300ms with
                # no width constraint at all — which, back in a row layout,
                # means taking the whole body and flattening the page beside
                # it. Visible as a flash every time the window is widened.
                rp.setFixedWidth(self._log_base_width() + self._log_bonus())

            # ── the orb ──
            # Beside the quick actions rather than above them, and small. It
            # is the one thing on the page with nothing to read in it.
            self._orb_layout.setDirection(
                QBoxLayout.Direction.LeftToRight if stacked
                else QBoxLayout.Direction.TopToBottom)
            self._relayout_quick_actions()
            if stacked:
                self.hud.setMaximumWidth(text_scale.px(150))
                self.hud.setMaximumHeight(text_scale.px(150))
            else:
                self.hud.setMaximumWidth(16777215)
                self.hud.setMaximumHeight(16777215)

            # ── the nav column ──
            # Folded away by anything narrower than three columns; the ☰ still
            # brings it back by hand, and doing so is not overridden until the
            # canvas changes shape again.
            want_collapsed = mode != self._LAYOUT_WIDE
            if want_collapsed and not self._sidebar_collapsed:
                self._auto_collapsed = True
                self._set_sidebar_collapsed(True)
            elif not want_collapsed and self._sidebar_collapsed \
                    and getattr(self, "_auto_collapsed", False):
                self._auto_collapsed = False
                self._set_sidebar_collapsed(False)

            rp.setVisible(self._log_wanted())
        finally:
            self._mode_busy = False
        self._apply_log_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_panels()
        cw = self.centralWidget()
        if hasattr(self, '_overlay') and self._overlay and self._overlay.isVisible():
            _place_overlay(self._overlay, cw, 520, 560)
        if hasattr(self, '_remote_overlay') and self._remote_overlay and self._remote_overlay.isVisible():
            _place_overlay(self._remote_overlay, cw,
                           RemoteKeyOverlay._OW, RemoteKeyOverlay._OH)
        if getattr(self, '_email_overlay', None) is not None:
            try:
                self._email_overlay.setGeometry(cw.rect())
            except RuntimeError:
                self._email_overlay = None
        # Both of these are full-bleed and centre their own card, so they only
        # need to be told the new size of the window they cover.
        for attr in ('_perm_overlay', '_tour'):
            ov = getattr(self, attr, None)
            if ov is not None:
                try:
                    ov.setGeometry(cw.rect())
                except RuntimeError:
                    setattr(self, attr, None)
        if getattr(self, '_toasts', None):
            self._place_toasts()
        if hasattr(self, '_hamburger'):
            self._hamburger.update_geometry(cw.width(), cw.height())
        if hasattr(self, '_wifi_overlay') and self._wifi_overlay and self._wifi_overlay.isVisible():
            _place_overlay(self._wifi_overlay, cw,
                           WiFiDisconnectedOverlay._OW,
                           WiFiDisconnectedOverlay._OH)


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API — SivaUI (PRESERVED IDENTICALLY)
# ═══════════════════════════════════════════════════════════════════════

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
        # Reached only if Qt's own last-window-closed quit fires first; the X
        # button goes through MainWindow.closeEvent. Same destination either way.
        from core import shutdown as _shutdown
        _shutdown.shutdown(reason="event loop ended")
    def protocol(self, *_):
        pass


class SivaUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._app.setStyleSheet(SIVASAFE_STYLESHEET)
        _init_fonts()
        # Load the saved text size and start watching for widgets before the
        # window is built, so the first paint is already at the right size —
        # no flash of default-size text on launch.
        text_scale.install(self._app, SIVASAFE_STYLESHEET)
        text_scale.instance().configure_curtain(
            text="RESIZING TEXT", bg=C.BG, fg=C.PRIMARY, family=_FONT_BODY)
        try:
            self._win = MainWindow(face_path)
            self._win.show()
        except Exception as e:
            print(f"[SIVA UI] ❌ Full UI failed: {e}. Launching fallback.")
            self._win = self._fallback_window()
            self._win.show()
        self.root = _RootShim(self._app)

    def _fallback_window(self) -> MainWindow:
        """Minimal fallback with just Talk + Emergency."""
        win = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(win)
        win._ready = True
        win._continuous_listen = False
        win._talk_pressed = False
        win.setWindowTitle("S.I.V.A.S.A.F.E — FALLBACK")
        win.resize(600, 400)
        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        win.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lbl = QLabel("S.I.V.A.S.A.F.E\nFallback Mode")
        lbl.setFont(_make_font(size=32, bold=True))
        lbl.setStyleSheet(f"color: {C.PRIMARY};")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)
        return win

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def refresh_reminders(self):
        """Refresh the sidebar reminders and the Reminders page list."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._win.refresh_reminders)

    def open_safety_check(self, path: str | None = None, text: str | None = None,
                          run: bool = True):
        """Open the Safety Check page. Safe to call from any thread."""
        self._win._safety_open_sig.emit(path, text, run)

    def show_safety_verdict(self, verdict):
        """Render a verdict the content_safety tool has already computed.

        Emitted rather than called: the tool runs in an executor thread, and
        touching widgets from there is what turns a working demo into a crash.
        A queued signal hands it to the GUI thread.
        """
        self._win._safety_verdict_sig.emit(verdict)

    def request_permission(self, key: str) -> bool:
        """Ask the user for one capability. Blocks until they answer.

        For the tool dispatcher, which runs in an executor thread — so the wait
        costs nothing anybody can see, and the card is still built on the GUI
        thread where it is legal to build it. Exactly the shape of
        ``capture_screen_full`` below, and for the same reason.

        The timeout is generous because there is a person reading the card, not
        a machine answering it. Running out is treated as "not given": the tool
        is refused and nothing is written, so the next attempt asks again.
        """
        box: dict = {"done": threading.Event()}
        self._win._permission_sig.emit(key, box)
        if not box["done"].wait(timeout=120.0):
            return False
        return bool(box.get("ok"))

    def start_walkthrough(self, force: bool = False):
        """Run the first-run tour. Safe to call from any thread."""
        self._win._tour_sig.emit(bool(force))

    def capture_screen_full(self) -> bytes:
        """Full-resolution PNG of the screen with SIVA's window out of the way.

        Blocks the calling worker until the GUI thread has taken the picture.
        Callers are already off the GUI thread — the tool dispatcher runs this
        in an executor — so the wait costs nothing the user can see.
        """
        box: dict = {"done": threading.Event()}
        self._win._capture_req_sig.emit(box)
        if not box["done"].wait(timeout=15.0):
            raise TimeoutError("the screen capture did not finish")
        if "err" in box:
            raise box["err"]
        return box["png"]
