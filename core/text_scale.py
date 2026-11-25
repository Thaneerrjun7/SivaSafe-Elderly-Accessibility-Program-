"""
Global text-size scaling for the SivaSafe interface.

Every font size in ui.py is baked in at widget-construction time, through two
different mechanisms — ``setFont(_make_font(size=N))`` in points, and
``setStyleSheet("font-size: Npx")`` in pixels — spread across roughly 180 call
sites. Rather than edit all of them, this module installs a single runtime
authority.

How it works
------------
A ``QApplication``-wide event filter watches four events on every widget:

    Polish       first contact — catches cards, dialogs and overlays that are
                 built long after startup
    Show         backstop, in case Polish fired mid-construction
    FontChange   a later setFont() call
    StyleChange  a later setStyleSheet() call

For each scalable attribute the widget carries two dynamic properties: the
*source* (the design-time value the application code asked for) and the
*output* (what we last wrote). When an event arrives we compare the widget's
current value against our stored output:

    equal      -> the change came from us; ignore it. This is what breaks the
                  setFont -> FontChange -> setFont recursion loop.
    different  -> application code just set a new design value; record it as
                  the new source, scale it, and write it back.

That second branch is why the dozen methods that restyle widgets after
construction (FileDropZone._update_style, AnimatedToggleBtn._update_ui,
MainWindow._style_nav_btns, _apply_state, ...) keep working with no changes.

Scope
-----
Text scales at the full factor. The boxes that *hold* text scale with it, or
large text clips inside ui.py's hard-coded pixel geometry. Panel widths and
layout margins scale at half rate so the window still fits a small laptop
screen.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QMargins, QObject, QPoint, QPropertyAnimation, QRectF,
    Qt, QTimer, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter
from PyQt6.QtWidgets import QApplication, QBoxLayout, QLabel, QLayout, QWidget

from core import prefs

# ═══════════════════════════════════════════════════════════════════════
# STEPS
# ═══════════════════════════════════════════════════════════════════════

# Four steps, topping out at 1.36.
#
# There was a fifth at 1.60 called "Largest". It was dropped: at that size the
# words were bigger than anyone needed and it cost the interface more than it
# gave — panels had to fold away, the quick actions went to a single column,
# and text began running out of the boxes drawn for it. "Larger" is a real
# increase, comfortably readable, and everything still fits around it.
STEPS = (0.85, 1.00, 1.18, 1.36)
LABELS = ("Small", "Default", "Large", "Larger")
DEFAULT_LEVEL = 1                      # STEPS[1] == 1.00 == today's size

_PREF_KEY = "text_scale_level"
_PREF_HC_KEY = "high_contrast_text"

# Qt's "no maximum set" sentinel.
_QWIDGETSIZE_MAX = (1 << 24) - 1

# At or below this, a fixed dimension is a control (a button, a row, an input)
# and scales fully. Above it, the dimension belongs to a panel or a canvas.
_SMALL_DIM = 200

# Dimensions this small are hairlines and separators; leave them alone.
_HAIRLINE = 2

# At or below this, a margin is the space beside a line of text and should
# keep pace with it. Above it, the margin is composition — a page inset — and
# grows at the chrome rate, because empty space does not become easier to read
# by getting larger.
_TEXT_ADJACENT = 32

# Top-level windows never grow past this share of the available screen.
_SCREEN_SHARE = 0.92

# How long the slider must sit still before the whole interface is rescaled.
_SETTLE_MS = 180

# Transition timings.
_SLIDE_IN_MS = 260      # curtain sweeps in from the left and covers everything
_SLIDE_OUT_MS = 300     # curtain sweeps off to the right, revealing the result
_REQUEST_MS = 90        # coalesces a fast drag into one transition
_CURTAIN_HOLD_MS = 260  # minimum time fully closed, so the message is readable
_CURTAIN_PT = 30        # base point size of the curtain message

# Bound once at import — the event filter compares against these on every
# event in the application, so the attribute lookups are worth avoiding.
_EV_POLISH = QEvent.Type.Polish
_EV_SHOW = QEvent.Type.Show
_EV_FONT = QEvent.Type.FontChange
_EV_STYLE = QEvent.Type.StyleChange


def _damped(f: float) -> float:
    """Half-rate factor, for panel widths and layout margins."""
    return 1.0 + (f - 1.0) * 0.5


def _gentle(f: float) -> float:
    """
    Chrome rate: the box around text grows enough to still look intentional
    next to the fully-grown text, but not 1:1 with it.

    Text always scales at the full factor. Growing every pixel of padding,
    radius and icon chrome at that same rate compounds: a button whose text
    is legitimately twice as large does not need a box twice as large too,
    and doing that anyway is exactly what turned single-line nav buttons and
    avatar circles into oversized pills, and pushed the medication card wide
    enough to need real horizontal scrolling. This runs at 30% of the text
    growth rate — visibly bigger at the largest step, never dominant.
    """
    return 1.0 + (f - 1.0) * 0.3


# The two rates below exist because the interface is not equally constrained
# on both axes, and pretending it is was what made large text look cramped.
#
# Sideways there is a hard limit: the screen. Downwards there is none — the
# pages scroll, and scrolling down is the one direction this interface is
# meant to move in. So height and vertical padding can afford to keep pace
# with the text, while width still has to be rationed.
#
# Both are deliberately inert at or below 1.0. Small and Default are the sizes
# the interface was drawn at and they stay exactly as they were drawn; all of
# this only starts once the reader has asked for something bigger.

def _vertical(f: float) -> float:
    """Room above and below text: keeps pace with the text itself."""
    return f if f > 1.0 else _gentle(f)


def _horizontal(f: float) -> float:
    """Room left and right of text: generous, but still rationed."""
    return 1.0 + (f - 1.0) * 0.65 if f > 1.0 else _gentle(f)


def _radius(f: float) -> float:
    """
    Corner rounding. Purely cosmetic, but it has to track the box or the
    interface slowly squares off as the text grows — a 20px radius reads as a
    soft card at the design size and as a barely-eased rectangle on a card
    half again as tall.
    """
    return f if f > 1.0 else _gentle(f)


# ═══════════════════════════════════════════════════════════════════════
# STYLESHEET REWRITER
# ═══════════════════════════════════════════════════════════════════════

# Longest property names first so the alternation cannot match a prefix.
#
# Only what the reader actually reads scales at the full rate. Everything
# else here is chrome — the box, not the words in it — and grows at the
# gentle rate instead. `min-width`/`min-height`/`max-width`/`max-height` sit
# with the box properties on purpose: in this stylesheet they are used to pin
# a control's own footprint (e.g. AnimatedToggleBtn's pill), not to reserve
# room for text.
_FULL_PROPS = (
    "font-size", "letter-spacing", "word-spacing",
)
# Room above and below the words.
_VERTICAL_PROPS = (
    "padding-top", "padding-bottom", "margin-top", "margin-bottom",
)
# Room left and right of the words.
_HORIZONTAL_PROPS = (
    "padding-right", "padding-left", "margin-right", "margin-left",
)
# min-/max-width and min-/max-height stay together on the chrome rate, and
# deliberately on the *same* rate as each other. In this stylesheet they pin a
# control's own footprint — a round icon, a toggle pill — rather than reserve
# room for text. Splitting them by axis, as the padding above is split, stops
# a square being square: the file-drop icon is a 36px circle set by all four
# of these at once, and growing its width and its height at different rates
# turned it into an oval.
_FOOTPRINT_PROPS = (
    "min-width", "min-height", "max-width", "max-height",
)
_RADIUS_PROPS = (
    "border-top-left-radius", "border-top-right-radius",
    "border-bottom-left-radius", "border-bottom-right-radius", "border-radius",
)

_RE_FULL = re.compile(
    r"\b(" + "|".join(_FULL_PROPS) + r")(\s*:)([^;}]*)", re.IGNORECASE)
_RE_VERTICAL = re.compile(
    r"\b(" + "|".join(_VERTICAL_PROPS) + r")(\s*:)([^;}]*)", re.IGNORECASE)
_RE_HORIZONTAL = re.compile(
    r"\b(" + "|".join(_HORIZONTAL_PROPS) + r")(\s*:)([^;}]*)", re.IGNORECASE)
_RE_RADIUS = re.compile(
    r"\b(" + "|".join(_RADIUS_PROPS) + r")(\s*:)([^;}]*)", re.IGNORECASE)
_RE_FOOTPRINT = re.compile(
    r"\b(" + "|".join(_FOOTPRINT_PROPS) + r")(\s*:)([^;}]*)", re.IGNORECASE)
# `padding: 6px 16px` carries both axes in one declaration, so it cannot go
# through either of the tables above. The lookahead is what keeps this from
# also matching the `padding-left` form, which is handled precisely.
_RE_BOX_SHORTHAND = re.compile(
    r"\b(padding|margin)(?!-)(\s*:)([^;}]*)", re.IGNORECASE)

# Lengths inside a declaration value. Deliberately excludes bare numbers so a
# stray `font-weight: 700` style value can never be touched.
_RE_LEN = re.compile(r"(-?\d*\.?\d+)\s*(px|pt)\b", re.IGNORECASE)


# ── High-contrast text ───────────────────────────────────────────────
#
# Only the `color` property is touched — never `background-color`, never
# `border-color`, never a gradient. The negative lookbehind is what keeps those
# out: `background-color` and `border-color` both have a character before
# `color` that a standalone property cannot have.
_RE_TEXT_COLOR = re.compile(r"(?<![-\w])color(\s*:\s*)([^;}\n]+)", re.IGNORECASE)
_RE_RGB_FUNC = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)", re.IGNORECASE)

# Anything at least this bright is text sitting on a dark fill — the white
# label on the emergency button, say — and must stay light or it disappears.
_HC_LIGHT_CUTOFF = 0.82


def _hc_replacement(value: str) -> str | None:
    """
    Black or white for a design colour, or None to leave it alone.

    This is a light interface: nearly all text sits on a pale surface, so
    nearly all of it goes to black. The exception is text that is already
    near-white, which is on a coloured fill and stays white. Alpha is dropped
    on purpose — a half-transparent label is low contrast by definition, and
    the whole point of the setting is to stop that.
    """
    v = value.strip().rstrip(";").strip()
    if not v or v.lower() in ("transparent", "none", "inherit"):
        return None

    m = _RE_RGB_FUNC.search(v)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    else:
        c = QColor(v)
        if not c.isValid():
            return None
        r, g, b = c.red(), c.green(), c.blue()

    # Perceived brightness, not raw average: the eye weights green heavily.
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#FFFFFF" if lum >= _HC_LIGHT_CUTOFF else "#000000"


@lru_cache(maxsize=1024)
def harden_qss(qss: str) -> str:
    """Push every text colour in a stylesheet to its high-contrast extreme."""
    if not qss or "color" not in qss.lower():
        return qss

    def _one(m):
        repl = _hc_replacement(m.group(2))
        return m.group(0) if repl is None else f"color{m.group(1)}{repl}"

    return _RE_TEXT_COLOR.sub(_one, qss)


@lru_cache(maxsize=1024)
def scale_qss(qss: str, factor: float) -> str:
    """
    Rewrite the length values of the text-bearing properties in a Qt
    stylesheet. `border` / `border-width` are deliberately left alone — the
    glassmorphic panels rely on 1px hairlines, and thickening them turns the
    whole interface into an outline drawing.

    Memoised: this runs once per widget on every scale change, and the
    interface reuses the same stylesheet text across dozens of nav buttons,
    cards and rows. The cache is what keeps a full rescale interactive.
    """
    if not qss or factor == 1.0:
        return qss

    def _sub(value: str, f: float) -> str:
        def _one(m):
            n = float(m.group(1)) * f
            unit = m.group(2)
            # Keep hairlines crisp.
            if abs(float(m.group(1))) <= _HAIRLINE:
                return m.group(0)
            return f"{round(n)}{unit}" if unit.lower() == "px" else f"{n:.1f}{unit}"
        return _RE_LEN.sub(_one, value)

    def _shorthand(m):
        """
        `padding` / `margin`, whose values are positional:

            one value     all four sides
            two values    vertical, horizontal
            three         top, horizontal, bottom
            four          top, right, bottom, left

        Each slot is scaled by the rate for the axis it belongs to. The
        one-value form is rewritten into the two-value form, because a single
        figure can no longer stand for both axes once they grow at different
        rates.
        """
        parts = m.group(3).split()
        if not parts:
            return m.group(0)
        if factor <= 1.0:
            # Below Default the axes have not parted company yet, so this stays
            # exactly what it was: one rate, and the value keeps its original
            # shape rather than being expanded into the two-value form.
            rate = (_gentle if m.group(1).lower() == "padding" else _damped)(factor)
            return m.group(1) + m.group(2) + _sub(m.group(3), rate)
        v, h = _vertical(factor), _horizontal(factor)
        if len(parts) == 1:
            parts = [_sub(parts[0], v), _sub(parts[0], h)]
        elif len(parts) == 2:
            parts = [_sub(parts[0], v), _sub(parts[1], h)]
        elif len(parts) == 3:
            parts = [_sub(parts[0], v), _sub(parts[1], h), _sub(parts[2], v)]
        elif len(parts) == 4:
            parts = [_sub(parts[0], v), _sub(parts[1], h),
                     _sub(parts[2], v), _sub(parts[3], h)]
        else:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)} {' '.join(parts)}"

    out = _RE_FULL.sub(
        lambda m: m.group(1) + m.group(2) + _sub(m.group(3), factor), qss)
    out = _RE_VERTICAL.sub(
        lambda m: m.group(1) + m.group(2) + _sub(m.group(3), _vertical(factor)), out)
    out = _RE_HORIZONTAL.sub(
        lambda m: m.group(1) + m.group(2) + _sub(m.group(3), _horizontal(factor)), out)
    out = _RE_FOOTPRINT.sub(
        lambda m: m.group(1) + m.group(2) + _sub(m.group(3), _gentle(factor)), out)
    out = _RE_RADIUS.sub(
        lambda m: m.group(1) + m.group(2) + _sub(m.group(3), _radius(factor)), out)
    out = _RE_BOX_SHORTHAND.sub(_shorthand, out)
    return out


# ═══════════════════════════════════════════════════════════════════════
# TRANSITION OVERLAY
# ═══════════════════════════════════════════════════════════════════════

class _CurtainOverlay(QWidget):
    """
    A plain full-cover panel, shown while the interface is rebuilt at the new
    size and taken away once it has settled.

    The rescale cannot be animated — it costs Qt a few hundred milliseconds of
    stylesheet repolishing, so there is no way to produce a new frame every
    16 ms. Rather than let the user watch the layout reflow, this simply hides
    the whole thing behind a message and reveals the finished result.

    Cheaper than cross-fading snapshots, too: nothing has to be photographed,
    which was about 200 ms of the old transition.
    """

    def __init__(self, parent: QWidget, text: str, bg: str, fg: str,
                 family: str, point_size: float):
        super().__init__(parent)
        # The curtain must never be rescaled or restyled by the engine itself.
        self.setProperty("_ts_ignore", True)
        self.setProperty("_ts_ignore_geom", True)
        self._text = text
        self._bg = QColor(bg)
        self._fg = QColor(fg)
        self._font = QFont(family, max(8, int(point_size)), QFont.Weight.Bold)
        self._font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        self._opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Opaque while it covers, so Qt skips repainting the interface (and the
        # continuously animating orb) underneath while the work is happening.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setGeometry(parent.rect())
        self.raise_()

    def paintEvent(self, _ev):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, self._bg)

        # A soft shading along the leading edge, so the panel reads as a drape
        # sweeping across the screen rather than a rectangle teleporting in.
        edge = max(24, r.width() // 24)
        grad = QLinearGradient(float(r.width() - edge), 0.0, float(r.width()), 0.0)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 46))
        p.fillRect(QRectF(r.width() - edge, 0, edge, r.height()), grad)

        p.setPen(self._fg)
        p.setFont(self._font)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


# ═══════════════════════════════════════════════════════════════════════
# THE SCALE AUTHORITY
# ═══════════════════════════════════════════════════════════════════════

class TextScale(QObject):
    """Singleton. Owns the current level and applies it to every widget."""

    changed = pyqtSignal(int)          # emits the new level
    contrast_changed = pyqtSignal(bool)  # high-contrast text on/off
    # Lets the application quiet its own continuous animations while a size
    # change is covered — anything repainting underneath competes with the
    # curtain's dissolve for the same frame budget.
    transition_started = pyqtSignal()
    transition_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._level = DEFAULT_LEVEL
        self._f = STEPS[DEFAULT_LEVEL]      # kept in sync; read by eventFilter
        self._high_contrast = False
        self._gen = 0
        self._app: QApplication | None = None
        self._base_app_qss = ""
        self._installed = False
        # Re-entrancy guard: our own setFont/setStyleSheet calls generate the
        # very events this object filters.
        self._busy = False
        # Set once any label has been forced to wrap. The event filter's fast
        # path is "nothing to do at 1.0"; that stops being true after a trip to
        # a larger size, because the hidden pages still carry wrapping we now
        # have to take back off them. See _apply_wrap.
        self._wrap_dirty = False
        self._pending: QTimer | None = None
        self._requested: int | None = None
        self._surface: QWidget | None = None
        self._transitioning = False
        self._anims: list = []          # keeps transition animations alive
        # Curtain appearance; the application overrides these to match itself.
        self._curtain_text = "RESIZING TEXT"
        self._curtain_bg = "#EBE7DF"
        self._curtain_fg = "#376757"
        self._curtain_family = "Segoe UI"

    # ── state ────────────────────────────────────────────────────────

    @property
    def level(self) -> int:
        return self._level

    @property
    def factor(self) -> float:
        return self._f

    @property
    def label(self) -> str:
        return LABELS[self._level]

    def pt(self, design_pt: float) -> float:
        """Scale a point size for text painted by hand in a paintEvent."""
        return design_pt * self.factor

    def px(self, design_px: float) -> int:
        """Scale a pixel dimension used in manual geometry calculations."""
        return int(round(design_px * self.factor))

    # ── lifecycle ────────────────────────────────────────────────────

    def install(self, app: QApplication, base_app_qss: str = "") -> None:
        """
        Load the saved level, apply it to the application stylesheet, and start
        watching for new widgets. Call this before the main window is built so
        the very first paint is already at the right size.
        """
        self._app = app
        self._base_app_qss = base_app_qss or app.styleSheet()

        saved = prefs.get(_PREF_KEY, DEFAULT_LEVEL)
        if isinstance(saved, int) and saved >= 0:
            # Clamped, not rejected. Anyone who had chosen the old fifth step
            # asked for the largest text there was; dropping them back to the
            # default because that step no longer exists would take away the
            # setting they went looking for. Give them the new largest.
            self._level = min(saved, len(STEPS) - 1)
            self._f = STEPS[self._level]

        # Read before the first widget exists, so a user who left high contrast
        # on never sees a flash of the normal colours — the sign-in screen
        # included, exactly as with the text size.
        self._high_contrast = bool(prefs.get(_PREF_HC_KEY, False))

        if not self._installed:
            app.installEventFilter(self)
            self._installed = True

        self._gen += 1
        self._apply_app_stylesheet()

    def set_level(self, level: int, persist: bool = True,
                  immediate: bool = True) -> None:
        """
        Change the text size.

        Rescaling every widget costs around half a second — most of it Qt
        repolishing stylesheets, which we cannot avoid. Dragging the slider
        would therefore stutter if each detent applied synchronously, so the
        control passes immediate=False: the factor changes at once (newly shown
        widgets are already correct, and the caller can repaint its own subtree
        for instant feedback) and the full pass runs once the drag settles.
        """
        level = max(0, min(len(STEPS) - 1, int(level)))
        if level == self._level:
            return
        self._level = level
        self._f = STEPS[level]
        if persist:
            prefs.set(_PREF_KEY, level)

        if immediate:
            self._cancel_pending()
            self.apply_all()
            self.changed.emit(level)
        else:
            self._schedule_apply()

    # ── high-contrast text ───────────────────────────────────────────

    @property
    def high_contrast(self) -> bool:
        return self._high_contrast

    def set_high_contrast(self, on: bool, persist: bool = True) -> None:
        """
        Turn high-contrast text on or off across the whole interface.

        It rides the same pass as the text size, for the same reason the text
        size uses it: one authority owns every stylesheet, so there is nothing
        to fight with and nothing to undo. Only the `color` property changes —
        backgrounds, borders, panels, icons, the orb and every animation are
        left exactly as they were.
        """
        on = bool(on)
        if on == self._high_contrast:
            return
        self._high_contrast = on
        if persist:
            prefs.set(_PREF_HC_KEY, on)

        self._cancel_pending()
        # Hidden widgets included, unlike a size change.
        #
        # A size change can leave the seven hidden pages alone because the
        # filter's Show handler catches each one on its way in. Contrast
        # cannot: at the default text size with the setting now off, that
        # filter is switched off too, so a page that was hardened while it was
        # hidden would never be told to go back — it would open in black text
        # and stay that way until the next restart. This runs rarely enough to
        # afford the full sweep.
        self.apply_all(include_hidden=True)
        self.contrast_changed.emit(on)

    # ── animated transition ──────────────────────────────────────────

    def request_level(self, level: int, surface: QWidget | None) -> None:
        """
        What the size control calls. Coalesces a fast drag into a single
        transition, then animates from the size in effect to the one chosen.
        """
        level = max(0, min(len(STEPS) - 1, int(level)))
        self._requested = level
        self._surface = surface
        self._cancel_pending()
        t = QTimer()
        t.setSingleShot(True)
        t.setInterval(_REQUEST_MS)
        t.timeout.connect(self._run_requested)
        self._pending = t
        t.start()

    def _run_requested(self) -> None:
        self._pending = None
        if self._requested is not None:
            self.animate_to(self._requested, self._surface)
            self._requested = None

    def configure_curtain(self, *, text: str | None = None, bg: str | None = None,
                          fg: str | None = None, family: str | None = None) -> None:
        """Let the application match the curtain to its own palette."""
        if text is not None:
            self._curtain_text = text
        if bg is not None:
            self._curtain_bg = bg
        if fg is not None:
            self._curtain_fg = fg
        if family is not None:
            self._curtain_family = family

    def animate_to(self, level: int, surface: QWidget | None = None) -> None:
        """
        Cover `surface` completely, rebuild it at the new size behind the
        cover, then dissolve the cover to reveal the finished layout.
        """
        level = max(0, min(len(STEPS) - 1, int(level)))
        if level == self._level:
            return

        usable = (surface is not None and surface.isVisible()
                  and surface.width() > 1 and surface.height() > 1)
        if not usable or self._transitioning:
            # Nothing to cover, or one already in flight — just apply.
            self.set_level(level)
            return

        self._transitioning = True
        try:
            curtain = _CurtainOverlay(
                surface, self._curtain_text, self._curtain_bg,
                self._curtain_fg, self._curtain_family,
                _CURTAIN_PT * STEPS[level],     # sized at the size being chosen
            )
        except Exception:
            self._transitioning = False
            self.set_level(level)
            return

        self.transition_started.emit()

        w, h = surface.width(), surface.height()
        curtain.setGeometry(-w, 0, w, h)
        curtain.show()
        curtain.raise_()

        def _finish():
            self._transitioning = False
            self._anims.clear()
            curtain.deleteLater()
            self.transition_finished.emit()

        def _open():
            # Sweeps off to the right, continuing the same direction of travel
            # rather than retreating the way it came.
            try:
                width = surface.width()
                curtain.raise_()
            except RuntimeError:
                _finish()
                return
            out = QPropertyAnimation(curtain, b"pos", self)
            out.setDuration(_SLIDE_OUT_MS)
            out.setStartValue(QPoint(0, 0))
            out.setEndValue(QPoint(width, 0))
            out.setEasingCurve(QEasingCurve.Type.InCubic)
            out.finished.connect(_finish)
            self._anims.append(out)
            out.start()

        def _covered():
            started = time.perf_counter()
            # The real rescale, entirely hidden behind the closed curtain.
            self.set_level(level)
            try:
                # The window may have grown with the setting; keep covering it.
                curtain.setGeometry(0, 0, surface.width(), surface.height())
                curtain.raise_()
            except RuntimeError:
                _finish()
                return
            # Stay closed a beat even when the work is quick — a curtain that
            # snaps open again immediately reads as a glitch, not a message.
            elapsed = (time.perf_counter() - started) * 1000
            QTimer.singleShot(max(0, int(_CURTAIN_HOLD_MS - elapsed)), _open)

        slide_in = QPropertyAnimation(curtain, b"pos", self)
        slide_in.setDuration(_SLIDE_IN_MS)
        slide_in.setStartValue(QPoint(-w, 0))
        slide_in.setEndValue(QPoint(0, 0))
        slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide_in.finished.connect(_covered)
        self._anims.append(slide_in)
        slide_in.start()

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending.stop()
            self._pending = None

    def _schedule_apply(self) -> None:
        self._cancel_pending()
        t = QTimer()
        t.setSingleShot(True)
        t.setInterval(_SETTLE_MS)
        t.timeout.connect(self._run_pending)
        self._pending = t
        t.start()

    def _run_pending(self) -> None:
        self._pending = None
        self.apply_all()
        self.changed.emit(self._level)

    def apply_subtree(self, root: QWidget) -> None:
        """
        Rescale one widget and its descendants. Used by the size control for
        instant feedback on itself while the global pass is still pending.
        """
        self._busy = True
        try:
            self._apply_widget_inner(root, claim=False)
            for w in root.findChildren(QWidget):
                self._apply_widget_inner(w, claim=False)
        finally:
            self._busy = False

    # ── application ──────────────────────────────────────────────────

    def _apply_app_stylesheet(self) -> None:
        if self._app is not None:
            self._app.setStyleSheet(scale_qss(self._base_app_qss, self.factor))

    def apply_all(self, include_hidden: bool = False) -> None:
        """Rescale every live widget. Idempotent."""
        self._gen += 1

        # Deliberately NOT re-applying the application-level stylesheet here.
        #
        # QApplication.setStyleSheet() forces Qt to repolish every widget in
        # the process, hidden ones included. Measured, that single call was
        # ~460 ms of a ~520 ms size change — 92% of the cost, and the reason
        # the transition stuttered. The only length in that sheet that scales
        # is the scrollbar width; its font-size rules belong to `glass-button`
        # and `glass-panel`, which no widget in the app actually uses. So it is
        # scaled once at install and left alone from then on.

        app = self._app or QApplication.instance()
        if app is None:
            return

        # Freeze painting on every top-level window so ~500 style
        # recalculations collapse into a single repaint instead of flickering
        # through the interface one widget at a time.
        tops = [w for w in app.topLevelWidgets() if w.isVisible()]
        for w in tops:
            w.setUpdatesEnabled(False)
        self._busy = True
        try:
            # Visible widgets only.
            #
            # Seven of the eight stacked pages are hidden at any moment, along
            # with every dialog and overlay that is not open. Restyling them
            # now is the bulk of the cost and none of it is on screen — and it
            # is not skipped work either: the Show handler in the event filter
            # rescales each one the moment it is displayed, because its
            # generation no longer matches. Windows are always processed so a
            # hidden dialog still gets its geometry right.
            for w in app.allWidgets():
                if include_hidden or w.isVisible() or w.isWindow():
                    self._apply_widget_inner(w, claim=True)
        finally:
            self._busy = False
            for w in tops:
                w.setUpdatesEnabled(True)
                w.update()

    # ── event filter ─────────────────────────────────────────────────

    def eventFilter(self, obj, ev):
        # This runs for every event in the application, so it has to stay
        # cheap. At the default level with high contrast off there is nothing
        # to do, so the filter costs two comparisons and gets out of the way
        # entirely.
        #
        # High contrast has to be part of that test, not just the scale factor.
        # Seven of the eight stacked pages are hidden when the setting is
        # toggled, and apply_all() deliberately skips hidden widgets — it is
        # this filter's Show handler that catches them on the way in. Left as a
        # bare `_f == 1.0` check, every page but the visible one stayed in the
        # normal colours at the default text size.
        if (self._f == 1.0 and not self._high_contrast
                and not self._wrap_dirty) or self._busy:
            return False
        t = ev.type()
        if t is _EV_POLISH:
            if isinstance(obj, QWidget):
                # Polish can arrive part-way through a constructor, before the
                # widget's own setStyleSheet() and setFixedHeight() calls have
                # run. So this pass deliberately does not claim the generation
                # — the Show pass still has to get a look at the finished
                # widget. Getting this wrong left the sidebar nav buttons and
                # the Emergency button at their design size.
                self._apply_widget(obj, claim=False)
        elif t is _EV_SHOW:
            if isinstance(obj, QWidget):
                self._apply_widget(obj)
        elif t is _EV_FONT or t is _EV_STYLE:
            if isinstance(obj, QWidget):
                self._restyle(obj)
        return False

    # ── per-widget work ──────────────────────────────────────────────

    def _restyle(self, w: QWidget) -> None:
        """
        Font or stylesheet changed after the fact. Never generation-gated:
        application code restyles widgets long after they are first polished
        (nav highlighting, state colours, the medication toggle), and those new
        stylesheets carry unscaled sizes. The source/output guard inside
        _apply_font and _apply_qss is what stops this looping.
        """
        if w.property("_ts_ignore"):
            self._contrast_only(w)
            return
        self._busy = True
        try:
            f = self._f
            changed = self._apply_font(w, f)
            changed |= self._apply_qss(w, f)
            if changed:
                w.updateGeometry()
        except RuntimeError:
            pass
        finally:
            self._busy = False

    def _apply_widget(self, w: QWidget, claim: bool = True) -> None:
        self._busy = True
        try:
            self._apply_widget_inner(w, claim)
        finally:
            self._busy = False

    def _contrast_only(self, w: QWidget) -> None:
        """
        Colour a widget that is excluded from scaling.

        `_ts_ignore` means "do not resize me" — the size-preview A / A on the
        Text Size control are that, deliberately fixed so they can show what
        small and large look like. Their text still has to follow the contrast
        setting, so this runs the ordinary stylesheet pass at factor 1.0:
        sizes come out untouched, colours do not.
        """
        # Saved and restored, not forced false: apply_all() holds this guard
        # across its whole loop, and dropping it half way would let the filter
        # start reacting to our own setStyleSheet calls.
        prev = self._busy
        self._busy = True
        try:
            self._apply_qss(w, 1.0)
        except RuntimeError:
            pass
        finally:
            self._busy = prev

    def _apply_widget_inner(self, w: QWidget, claim: bool) -> None:
        try:
            if w.property("_ts_ignore"):
                self._contrast_only(w)
                return
            if claim:
                if w.property("_ts_gen") == self._gen:
                    return
                w.setProperty("_ts_gen", self._gen)

            f = self.factor
            changed = self._apply_font(w, f)
            changed |= self._apply_qss(w, f)
            changed |= self._apply_wrap(w, f)
            if not w.property("_ts_ignore_geom"):
                self._apply_geometry(w, f)
                self._apply_layout(w.layout(), f)
            if changed:
                # A word-wrapped QLabel caches the height it needs at the old
                # font. Without this the chat bubbles and any other wrapping
                # text keep their old box and get cut off mid-sentence.
                w.updateGeometry()
                lay = w.layout()
                if lay is not None:
                    lay.invalidate()
        except RuntimeError:
            # The underlying C++ object was deleted mid-walk.
            pass

    def _apply_font(self, w: QWidget, f: float) -> bool:
        # A widget that never had setFont() called on it inherits its font from
        # its parent, which we have already scaled. Touching it here would
        # apply the factor a second time.
        if not w.testAttribute(Qt.WidgetAttribute.WA_SetFont):
            return False

        font = w.font()
        cur = font.pointSizeF()
        if cur <= 0:                       # pixel-sized font; not used today
            return False

        out = w.property("_ts_font_out")
        src = w.property("_ts_font_src")
        if out is None or src is None or abs(cur - float(out)) > 0.01:
            src = cur                      # a fresh design value
        src = float(src)

        new = round(src * f, 2)
        w.setProperty("_ts_font_src", src)
        w.setProperty("_ts_font_out", new)
        if abs(cur - new) > 0.01:
            font.setPointSizeF(new)
            w.setFont(font)
            return True
        return False

    def _apply_wrap(self, w: QWidget, f: float) -> bool:
        """
        Let long labels fall onto a second line once the text is enlarged.

        A QLabel that cannot wrap reports its whole unwrapped string as its
        minimum width, and Qt propagates that minimum all the way up the layout
        tree. One such label is enough to hold a page open wider than the
        window — which is what put a horizontal scrollbar on Settings at the
        *default* size once (see the note above the auth buttons in ui.py), and
        what makes every panel overflow sideways at the larger sizes. Wrapping
        is what lets the page grow downwards, into space we have, instead of
        sideways, into space we do not.

        Only above 1.0. Small and Default are left exactly as they were
        designed, including on the way back down from a larger size — a label
        that was wrapped for us is unwrapped again, while one the application
        asked to wrap keeps wrapping at every size.

        Opt out with the `_ts_nowrap` property, for text that has to stay on
        one line (the clock) or is short enough that wrapping it would only
        look odd.
        """
        if not isinstance(w, QLabel) or w.property("_ts_nowrap"):
            return False

        cur = w.wordWrap()
        out = w.property("_ts_wrap_out")
        src = w.property("_ts_wrap_src")
        # Same guard as the font and stylesheet passes: if the widget no longer
        # holds what we last wrote, application code set it, and that is the
        # new design value.
        if out is None or src is None or cur != bool(out):
            src = cur
        src = bool(src)

        new = src or f > 1.0
        w.setProperty("_ts_wrap_src", src)
        w.setProperty("_ts_wrap_out", new)
        if new != cur:
            w.setWordWrap(new)
            if new and not src:
                self._wrap_dirty = True
            return True
        return False

    def _apply_qss(self, w: QWidget, f: float) -> bool:
        cur = w.styleSheet()
        if not cur:
            return False

        out = w.property("_ts_qss_out")
        src = w.property("_ts_qss_src")
        if out is None or src is None or cur != out:
            src = cur

        new = scale_qss(src, f)
        if self._high_contrast:
            new = harden_qss(new)
        # `src` stays the pristine design stylesheet either way, so turning the
        # setting off recomputes the original colours from source rather than
        # trying to undo them — and a colour can never compound the way a size
        # would if the hardened text were mistaken for a new design value.
        w.setProperty("_ts_qss_src", src)
        w.setProperty("_ts_qss_out", new)
        if new != cur:
            w.setStyleSheet(new)
            return True
        return False

    # ── geometry ─────────────────────────────────────────────────────

    def _content_height(self, w: QWidget, design_h: int, f: float) -> int:
        """
        How tall a single-line control needs to be to hold its own (already
        full-scaled) font, plus whatever chrome the original design gave it —
        no more.

        `design_h` was sized, at 1.0x, for a font at `_ts_font_src` points. The
        gap between that height and that font's own line height is the
        designer's padding budget in pixels; carrying that same pixel budget
        forward at the new, larger font is what makes a nav pill or a chat
        input grow just enough to keep its text clear of the edge, rather than
        scaling the whole box 1:1 with the text and ending up oversized.
        """
        design_pt = w.property("_ts_font_src")
        if design_pt is None:
            return int(round(design_h * _gentle(f)))
        try:
            design_pt = float(design_pt)
            font_now = w.font()                # already scaled by _apply_font
            fm_now = QFontMetrics(font_now)
            design_font = QFont(font_now)
            design_font.setPointSizeF(design_pt)
            fm_design = QFontMetrics(design_font)
            chrome = max(0, design_h - fm_design.height())
            # The chrome grows with the text rather than being carried across
            # as a flat pixel count. Held flat, a 44px pill around 20px of text
            # became a 56px pill around 32px of text: the same padding in
            # pixels, half as much of it relative to the words, which is
            # precisely the "text crammed into a small box" this is meant to
            # avoid. Vertical room is the room we have.
            needed = fm_now.height() + int(round(chrome * _vertical(f)))
        except Exception:
            return int(round(design_h * _gentle(f)))
        # Bracketed between "never smaller than the original design" and
        # "never larger than scaling the whole box at the full text rate" —
        # content-driven sizing should land inside that range, not outside it.
        return max(design_h, min(needed, int(round(design_h * f))))

    def _apply_geometry(self, w: QWidget, f: float) -> None:
        """
        Three different things get scaled at three different rates:

          * A **square** fixed control (an avatar, a round icon button) is
            pure chrome — there is no text of its own to protect — so it
            grows at the gentle rate on both axes. This is what keeps a 48px
            avatar from becoming a 77px circle that shoves everything else in
            its row out past the edge of the panel.
          * A **single-line text control** (a nav button, a line edit) grows
            its height just enough to hold its own scaled font — see
            _content_height. Its own font is already growing at the full
            rate; the box only has to keep up with it, not race ahead of it.
          * Everything **wider than a control** — a fixed panel like the
            240px sidebar or the 400px activity log — grows at half rate, or
            the three-column layout stops fitting the window.
        """
        if w.isWindow():
            self._apply_window_geometry(w, f)
            return

        mnw, mxw = w.minimumWidth(), w.maximumWidth()
        mnh, mxh = w.minimumHeight(), w.maximumHeight()
        # Most widgets constrain nothing. Four cheap getters here save a dozen
        # property round trips per widget across a full rescale.
        if (mnw <= 0 and mnh <= 0
                and mxw >= _QWIDGETSIZE_MAX and mxh >= _QWIDGETSIZE_MAX):
            return

        fixed_w = 0 < mnw == mxw < _QWIDGETSIZE_MAX
        fixed_h = 0 < mnh == mxh < _QWIDGETSIZE_MAX
        square = fixed_w and fixed_h and mnw == mnh and mnw <= _SMALL_DIM
        has_font = w.testAttribute(Qt.WidgetAttribute.WA_SetFont)

        def _scaled(value: int, is_width: bool, is_height: bool) -> int:
            if value <= _HAIRLINE:
                return value
            if value <= _SMALL_DIM:
                if square:
                    return int(round(value * _gentle(f)))
                if is_height and has_font:
                    return self._content_height(w, value, f)
                if is_height:
                    return int(round(value * _vertical(f)))
                return int(round(value * _gentle(f)))
            if is_width and fixed_w:
                return int(round(value * _damped(f)))
            return value

        for name, is_width, is_height, getter, setter in (
            ("minh", False, True,  w.minimumHeight, w.setMinimumHeight),
            ("maxh", False, True,  w.maximumHeight, w.setMaximumHeight),
            ("minw", True,  False, w.minimumWidth,  w.setMinimumWidth),
            ("maxw", True,  False, w.maximumWidth,  w.setMaximumWidth),
        ):
            cur = getter()
            if cur <= 0 or cur >= _QWIDGETSIZE_MAX:
                continue                   # unset

            out = w.property(f"_ts_{name}_out")
            src = w.property(f"_ts_{name}_src")
            if out is None or src is None or cur != out:
                src = cur
            src = int(src)

            new = _scaled(src, is_width, is_height)
            w.setProperty(f"_ts_{name}_src", src)
            w.setProperty(f"_ts_{name}_out", new)
            if new != cur:
                setter(new)

    def _apply_window_geometry(self, w: QWidget, f: float) -> None:
        """
        Dialogs pinned with setFixedSize would clip their contents once the
        text inside grows, so they scale — but never past the screen.
        """
        screen = w.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        cap_w = int(avail.width() * _SCREEN_SHARE) if avail else _QWIDGETSIZE_MAX
        cap_h = int(avail.height() * _SCREEN_SHARE) if avail else _QWIDGETSIZE_MAX

        fixed_w = 0 < w.minimumWidth() == w.maximumWidth() < _QWIDGETSIZE_MAX
        fixed_h = 0 < w.minimumHeight() == w.maximumHeight() < _QWIDGETSIZE_MAX

        for name, getter, setter, is_fixed, cap in (
            ("wminh", w.minimumHeight, w.setMinimumHeight, fixed_h, cap_h),
            ("wmaxh", w.maximumHeight, w.setMaximumHeight, fixed_h, cap_h),
            ("wminw", w.minimumWidth,  w.setMinimumWidth,  fixed_w, cap_w),
            ("wmaxw", w.maximumWidth,  w.setMaximumWidth,  fixed_w, cap_w),
        ):
            cur = getter()
            if cur <= 0 or cur >= _QWIDGETSIZE_MAX:
                continue

            out = w.property(f"_ts_{name}_out")
            src = w.property(f"_ts_{name}_src")
            if out is None or src is None or cur != out:
                src = cur
            src = int(src)

            # A fixed dialog must fit its grown contents; a window that only
            # declares a minimum can afford the gentler factor.
            new = min(int(round(src * (f if is_fixed else _damped(f)))), cap)
            w.setProperty(f"_ts_{name}_src", src)
            w.setProperty(f"_ts_{name}_out", new)
            if new != cur:
                setter(new)

    # ── layouts ──────────────────────────────────────────────────────

    def _apply_layout(self, lay: QLayout | None, f: float) -> None:
        if lay is None:
            return
        try:
            # At and below Default this is the single damped rate it has always
            # been, so those two sizes are untouched. Above it the axes part
            # company: gaps between stacked rows open up with the text, gaps
            # across a row stay rationed.
            if f <= 1.0:
                vert = horz = _damped(f)
            else:
                vert, horz = _vertical(f), _horizontal(f)

            m = lay.contentsMargins()
            cur = (m.left(), m.top(), m.right(), m.bottom())
            out = lay.property("_ts_marg_out")
            src = lay.property("_ts_marg_src")
            if out is None or src is None or list(cur) != list(out):
                src = cur
            src = tuple(int(v) for v in src)
            rates = (horz, vert, horz, vert)        # left, top, right, bottom
            # Above a certain size a margin has stopped being room around the
            # words and become open space in the composition — a page inset,
            # not a gap beside a line of text. Those grow at the chrome rate
            # instead. Left at the text rate, an 80px page inset put another
            # 58px of nothing at the top and bottom of a page, which on a
            # short window is what pushed the content off the bottom of it.
            new = tuple(
                int(round(v * (r if v <= _TEXT_ADJACENT else _gentle(f))))
                for v, r in zip(src, rates))
            lay.setProperty("_ts_marg_src", list(src))
            lay.setProperty("_ts_marg_out", list(new))
            if new != cur:
                lay.setContentsMargins(QMargins(*new))

            sp = lay.spacing()
            if sp >= 0:
                out = lay.property("_ts_sp_out")
                src = lay.property("_ts_sp_src")
                if out is None or src is None or sp != out:
                    src = sp
                # A column's spacing separates rows, so it is vertical room; a
                # row's spacing separates columns. Anything else (a grid) is
                # charged the narrower rate, because it is at least partly
                # width.
                stacked = (isinstance(lay, QBoxLayout)
                           and lay.direction() in (
                               QBoxLayout.Direction.TopToBottom,
                               QBoxLayout.Direction.BottomToTop))
                new = int(round(int(src) * (vert if stacked else horz)))
                lay.setProperty("_ts_sp_src", int(src))
                lay.setProperty("_ts_sp_out", new)
                if new != sp:
                    lay.setSpacing(new)

            # Nested layouts added with addLayout() are not reachable from any
            # widget, so walk them from here.
            for i in range(lay.count()):
                item = lay.itemAt(i)
                child = item.layout() if item is not None else None
                if child is not None:
                    self._apply_layout(child, f)
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════
# MODULE-LEVEL ACCESS
# ═══════════════════════════════════════════════════════════════════════

_instance: TextScale | None = None


def instance() -> TextScale:
    global _instance
    if _instance is None:
        _instance = TextScale()
    return _instance


def install(app: QApplication, base_app_qss: str = "") -> TextScale:
    ts = instance()
    ts.install(app, base_app_qss)
    return ts


def factor() -> float:
    return instance().factor


def level() -> int:
    return instance().level


def pt(design_pt: float) -> float:
    return instance().pt(design_pt)


def px(design_px: float) -> int:
    return instance().px(design_px)


def gentle_factor() -> float:
    """The chrome growth rate at the current text size — see `_gentle`."""
    return _gentle(instance().factor)


def high_contrast() -> bool:
    return instance().high_contrast


def set_high_contrast(on: bool) -> None:
    instance().set_high_contrast(on)
