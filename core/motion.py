from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QObject, QByteArray
import math

class Motion:
    """
    SIVA Shared Motion Vocabulary
    All UI animations must route through this file to ensure iPadOS-like consistency.
    """
    
    # Global flag for low-spec or accessibility
    REDUCED_MOTION = False
    
    # Easing Curves
    MICRO_EASE = QEasingCurve.Type.OutCubic
    HOVER_EASE = QEasingCurve.Type.OutQuart
    EXPAND_EASE = QEasingCurve.Type.OutExpo
    
    # Base Durations (ms)
    DUR_MICRO = 150
    DUR_HOVER_IN = 180
    DUR_HOVER_OUT = 240
    DUR_EXPAND = 300
    DUR_FADE = 250
    
    @classmethod
    def duration(cls, base_ms: int) -> int:
        if cls.REDUCED_MOTION:
            return int(base_ms * 0.4)  # Shorten by 60%
        return base_ms

    @classmethod
    def anim(cls, target: QObject, prop: str, duration_base: int, ease: QEasingCurve.Type) -> QPropertyAnimation:
        """Helper to create a standard QPropertyAnimation."""
        anim = QPropertyAnimation(target, QByteArray(prop.encode('utf-8')))
        anim.setDuration(cls.duration(duration_base))
        anim.setEasingCurve(ease)
        return anim


from PyQt6.QtWidgets import QPushButton, QGraphicsDropShadowEffect, QFrame
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt, QPointF

class AnimatedButton(QPushButton):
    """A button that uses QPropertyAnimation for hover and press instead of CSS."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self._shadow = None
        self._ensure_shadow()

    def _ensure_shadow(self):
        """
        Return a live drop-shadow effect, rebuilding it if anything replaced or
        destroyed the one we installed.

        Qt deletes a widget's existing QGraphicsEffect the moment a new one is
        set on it. AnimatedScrollArea's reveal animation does exactly that to
        every item it shows, which left this button holding a dangling wrapper.
        Touching it then raised RuntimeError, and an unhandled exception inside
        a Qt event handler makes PyQt call qFatal() — the whole application
        aborts. That is what was killing SivaSafe on hover.

        Returns None if the widget currently has some other effect installed,
        in which case the caller skips its animation rather than fight over it.
        """
        try:
            if self._shadow is not None:
                self._shadow.blurRadius()          # raises if C++ side is gone
                if self.graphicsEffect() is self._shadow:
                    return self._shadow
        except RuntimeError:
            self._shadow = None

        existing = self.graphicsEffect()
        if existing is not None and existing is not self._shadow:
            return None                            # someone else owns it

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(0)
        self._shadow.setColor(QColor(0, 0, 0, 0))
        self._shadow.setOffset(0, 4)
        self.setGraphicsEffect(self._shadow)
        return self._shadow

    def _hover_to(self, blur, alpha, duration):
        shadow = self._ensure_shadow()
        if shadow is None:
            return
        self._hover_anim = Motion.anim(shadow, "blurRadius", duration, Motion.HOVER_EASE)
        self._hover_anim.setStartValue(shadow.blurRadius())
        self._hover_anim.setEndValue(blur)

        self._col_anim = Motion.anim(shadow, "color", duration, Motion.HOVER_EASE)
        self._col_anim.setStartValue(shadow.color())
        self._col_anim.setEndValue(QColor(0, 0, 0, alpha))

        from PyQt6.QtCore import QParallelAnimationGroup
        self._ag = QParallelAnimationGroup(self)
        self._ag.addAnimation(self._hover_anim)
        self._ag.addAnimation(self._col_anim)
        self._ag.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hover_to(16.0, 60, Motion.DUR_HOVER_IN)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover_to(0.0, 0, Motion.DUR_HOVER_OUT)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        shadow = self._ensure_shadow()
        if shadow is None:
            return
        self._press_anim = Motion.anim(shadow, "offset", Motion.DUR_MICRO, Motion.MICRO_EASE)
        self._press_anim.setStartValue(QPointF(0, 4))
        self._press_anim.setEndValue(QPointF(0, 1))
        self._press_anim.start()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        shadow = self._ensure_shadow()
        if shadow is None:
            return
        self._release_anim = Motion.anim(shadow, "offset", Motion.DUR_MICRO, Motion.MICRO_EASE)
        self._release_anim.setStartValue(shadow.offset())
        self._release_anim.setEndValue(QPointF(0, 4))
        self._release_anim.start()


class CardButton(AnimatedButton):
    """
    A button that is really a card: it carries a layout of labels rather than
    button text.

    QPushButton measures itself from its own text, icon and style and ignores
    any layout placed inside it — both `sizeHint` and `minimumSizeHint` are
    overridden to do exactly that. So a card built this way cannot grow to fit
    its own contents: it keeps whatever minimum height it was given, and the
    labels inside are squeezed into what is left. At the larger text sizes
    that is what cut the dates on Chat History clean in half.

    Handing the measurements back to the layout is the whole fix. Hints rather
    than explicit minimums, deliberately, so the text-size engine can go on
    scaling this button's geometry without the two of them writing over each
    other's values.
    """

    def sizeHint(self):
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self):
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()

    def hasHeightForWidth(self) -> bool:
        lay = self.layout()
        return lay.hasHeightForWidth() if lay is not None else False

    def heightForWidth(self, w: int) -> int:
        lay = self.layout()
        return lay.heightForWidth(w) if lay is not None else super().heightForWidth(w)


class ExpandableCard(QFrame):
    """A card that expands to reveal hidden content on click."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._content_widget = None
        self._base_height = 0
        self._expanded_height = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # min/maxHeight are animated on expand, so the text scaler must not
        # own this card's geometry — setContentWidget applies the factor.
        self.setProperty("_ts_ignore_geom", True)

    def setContentWidget(self, widget, base_height, expanded_height):
        from core import text_scale
        f = text_scale.factor()
        self._content_widget = widget
        self._base_height = int(base_height * f)
        self._expanded_height = int(expanded_height * f)
        self._content_widget.setVisible(False)
        self.setMinimumHeight(self._base_height)
        self.setMaximumHeight(self._base_height)
    
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not self._content_widget:
            return
        
        self._expanded = not self._expanded
        
        target_h = self._expanded_height if self._expanded else self._base_height
        start_h = self.height()
        
        from PyQt6.QtCore import QParallelAnimationGroup
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        
        self._ag = QParallelAnimationGroup(self)
        
        anim1 = Motion.anim(self, "minimumHeight", Motion.DUR_EXPAND, Motion.EXPAND_EASE)
        anim1.setStartValue(start_h)
        anim1.setEndValue(target_h)
        self._ag.addAnimation(anim1)
        
        anim2 = Motion.anim(self, "maximumHeight", Motion.DUR_EXPAND, Motion.EXPAND_EASE)
        anim2.setStartValue(start_h)
        anim2.setEndValue(target_h)
        self._ag.addAnimation(anim2)
        
        if self._expanded:
            self._content_widget.setVisible(True)
            op = QGraphicsOpacityEffect(self._content_widget)
            self._content_widget.setGraphicsEffect(op)
            op_anim = Motion.anim(op, "opacity", Motion.DUR_EXPAND, Motion.EXPAND_EASE)
            op_anim.setStartValue(0.0)
            op_anim.setEndValue(1.0)
            self._ag.addAnimation(op_anim)
        else:
            op = QGraphicsOpacityEffect(self._content_widget)
            self._content_widget.setGraphicsEffect(op)
            op_anim = Motion.anim(op, "opacity", int(Motion.DUR_EXPAND * 0.5), Motion.MICRO_EASE)
            op_anim.setStartValue(1.0)
            op_anim.setEndValue(0.0)
            op_anim.finished.connect(lambda: self._content_widget.setVisible(False))
            self._ag.addAnimation(op_anim)
            
        self._ag.start()


# ═══════════════════════════════════════════════════════════════════════
# ANIMATED SCROLL AREA — Viewport-based item reveal + gradient overlays
# ═══════════════════════════════════════════════════════════════════════

from PyQt6.QtWidgets import QScrollArea, QWidget, QGraphicsOpacityEffect, QLabel
from PyQt6.QtGui import QPainter, QLinearGradient
from PyQt6.QtCore import QTimer, QParallelAnimationGroup, QEvent


def fit_wrapped_labels(host: QWidget) -> bool:
    """
    Give every wrapping label under `host` the height its text needs at the
    width it has just been given.

    Answering heightForWidth is not enough on its own. Nested layouts settle
    height and width in separate passes, and a wrapped label kept landing a
    line or two short of its own text — the end of a long message simply
    disappearing off the bottom of the card holding it. Rather than keep
    trying to win that negotiation, each label is told outright how tall it
    has to be. Measured from the real text at the real width, so it is right
    at every font size, and it re-runs whenever either one changes.

    Returns whether anything moved, so the caller can updateGeometry() once.
    """
    changed = False
    for lbl in host.findChildren(QLabel):
        if not lbl.wordWrap():
            continue
        w = lbl.width()
        if w <= 0:
            continue
        need = lbl.heightForWidth(w)
        if need > 0 and lbl.minimumHeight() != need:
            # The text-size engine must not also own this: it would read the
            # measured height as a design value and grow it again on every
            # size change, compounding each time.
            lbl.setProperty("_ts_ignore_geom", True)
            lbl.setMinimumHeight(need)
            changed = True
    return changed


class AnimatedScrollArea(QScrollArea):
    """
    Drop-in replacement for QScrollArea with:
      1. Per-item reveal animation (scale 0.7→1 + opacity 0→1) when items scroll into viewport
      2. Top/bottom gradient overlays that fade based on scroll position
    
    Port of the React AnimatedList component to PyQt6.
    """

    # ── Class-level constants ──
    MAX_CONCURRENT_ANIMS = 8       # Cap simultaneous animations
    REVEAL_DURATION = 200          # ms, matches React component
    THROTTLE_MS = 16               # ~60fps scroll check
    GRADIENT_HEIGHT = 60           # px for edge gradients

    def __init__(self, parent=None, show_gradients=True,
                 gradient_color=(255, 255, 255), gradient_height=60,
                 scrollbar_style=None):
        super().__init__(parent)
        self._show_gradients = show_gradients
        self._gradient_color = gradient_color
        self._gradient_height = gradient_height

        # Track which widgets have already been revealed
        self._revealed: set[int] = set()
        # Active animation group references (prevent GC)
        self._active_anims: list[QParallelAnimationGroup] = []
        # Skip flag for programmatic inserts
        self._skip_next = False
        # Guards the deferred width re-check in _resettle.
        self._reclamp_queued = False
        # Width surrendered by the self-correction in _deferred_clamp.
        self._extra_reserve = 0

        # Gradient opacity values (0.0–1.0)
        self._top_grad_opacity = 0.0
        self._bottom_grad_opacity = 1.0

        # Throttle timer for viewport checks
        self._scroll_throttle = QTimer(self)
        self._scroll_throttle.setSingleShot(True)
        self._scroll_throttle.setInterval(self.THROTTLE_MS)
        self._scroll_throttle.timeout.connect(self._check_viewport)

        # Connect scrollbar value changes
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # Default scrollbar styling (can be overridden)
        base_style = scrollbar_style or """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
            QScrollBar::handle:vertical { background: rgba(139,94,60,0.2); border-radius: 3px; min-height: 40px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """
        self.setStyleSheet(base_style)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        # ScrollBarAlwaysOff hides the bar; it does not stop the scrolling.
        # The range is still there, so a trackpad or shift-wheel still drags
        # the content sideways and the part that is off to the right cannot be
        # reached any other way. _clamp_width is what actually removes it —
        # see the method for why capping the maximum is enough.
        # A page's content must never be able to shove the surrounding layout
        # apart. Without an explicit minimum, Qt propagates the content's
        # minimumSizeHint upward — one wide heading at the largest text size is
        # then enough to push the centre column out from under the side panels.
        # Cap what we advertise and let the page scroll instead.
        self.setMinimumWidth(160)

    # ── No sideways scrolling, ever ──

    def _clamp_width(self):
        """
        Pin the content to the width of the viewport.

        With setWidgetResizable(True), Qt sizes the content to
        `max(viewport width, content minimumSizeHint)` — so one item that
        cannot be made narrower is enough to give the area a horizontal range,
        and everything past the right edge becomes unreachable except by
        dragging sideways. Capping the maximum is enough to stop it, because
        the resize Qt performs is itself clamped to the maximum, and the
        result is that the content is always exactly as wide as the window it
        is being read through.

        This is a floor under the whole interface rather than a fix for any
        one page: it holds for every scroll area in the app, at every text
        size, including pages added later. It is only safe because the text
        inside wraps (see TextScale._apply_wrap) — the two go together, and
        without the wrapping this would clip long lines instead of folding
        them.
        """
        w = self.widget()
        if w is None:
            return
        vw = self.viewport().width()
        # Always measured as though the vertical scrollbar were showing, even
        # when it is not.
        #
        # Otherwise the three quantities here chase each other in a circle:
        # the content's width is set from the viewport, the width decides how
        # tall the wrapped text becomes, the height decides whether a vertical
        # scrollbar is needed, and the scrollbar changes the viewport width we
        # started from. Qt resolves that iteratively and does not always land
        # in the same place — it left a scrollbar's width of sideways scroll
        # on a page roughly one run in three. Reserving the width
        # unconditionally costs about six pixels and makes the answer the same
        # every time.
        bar = self.verticalScrollBar()
        if not bar.isVisible():
            vw -= max(0, bar.sizeHint().width())
        vw -= self._extra_reserve
        if vw > 0 and w.maximumWidth() != vw:
            w.setMaximumWidth(vw)
            # The cap changes the content's width after Qt has already laid
            # its children out against the old one, so they have to be told to
            # measure again — otherwise a row keeps a width belonging to a
            # viewport that no longer exists and hangs over the right edge.
            # Guarded on a real change so this costs nothing on the resizes
            # where the width has not actually moved.
            lay = w.layout()
            if lay is not None:
                lay.invalidate()

    # Ceiling on the self-correction below. Anything approaching this would
    # mean something other than scrollbar arithmetic is wrong, and quietly
    # shaving the content narrower and narrower would hide it.
    MAX_EXTRA_RESERVE = 24

    def _deferred_clamp(self):
        self._reclamp_queued = False
        try:
            # Last line of defence. Reserving the scrollbar up front makes the
            # arithmetic predictable, but this widget sits inside layouts that
            # settle over several passes — and on nested scroll areas the two
            # levels settle against each other. If a horizontal range has
            # survived all that, take it off the content directly rather than
            # work out which pass was responsible. Monotonic and capped, so it
            # converges and cannot creep.
            over = self.horizontalScrollBar().maximum()
            if 0 < over and self._extra_reserve < self.MAX_EXTRA_RESERVE:
                self._extra_reserve = min(self.MAX_EXTRA_RESERVE,
                                          self._extra_reserve + over)
            self._clamp_width()
        except RuntimeError:
            pass          # the C++ object went away while the timer was out

    def setWidget(self, w):
        super().setWidget(w)
        self._clamp_width()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._resettle()

    def viewportEvent(self, ev):
        # The viewport can narrow without this widget resizing at all: the
        # moment the content grows tall enough to need a vertical scrollbar,
        # that bar takes its width out of the viewport and nothing else moves.
        # Clamping only on our own resize missed exactly that case, and left
        # the content a scrollbar's width too wide — a few pixels of sideways
        # scrolling that appeared only once a page had enough in it to scroll
        # down.
        handled = super().viewportEvent(ev)
        if ev.type() == QEvent.Type.Resize:
            self._resettle()
        return handled

    def _resettle(self):
        self._clamp_width()
        # And once more after this round of layout has finished.
        #
        # Clamping the content can change how tall it is, which can add or
        # remove the vertical scrollbar, which changes the viewport width we
        # just clamped to — so a single pass can settle on a width that is
        # already out of date by the time it is applied. The re-check is
        # cheap, does nothing when the width has not moved, and cannot loop:
        # _clamp_width only acts on a real change, and the sequence is
        # monotonic — the viewport can lose the scrollbar's width once, not
        # repeatedly.
        if not self._reclamp_queued:
            self._reclamp_queued = True
            QTimer.singleShot(0, self._deferred_clamp)
        # Once the content's width is settled, make sure the wrapped text
        # inside it has the height that width implies. Done here rather than
        # at each of the two dozen places a wrapping label is built, because
        # the answer changes with the width and this is where the width is
        # decided.
        w = self.widget()
        if w is not None and fit_wrapped_labels(w):
            w.updateGeometry()

    # ── Public API ──

    def skip_animation_for_next_add(self):
        """Call before adding a widget programmatically to skip the reveal animation."""
        self._skip_next = True

    def mark_all_revealed(self):
        """Mark all current children as already revealed (e.g. after bulk load)."""
        container = self.widget()
        if not container:
            return
        layout = container.layout()
        if not layout:
            return
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.widget():
                self._revealed.add(id(item.widget()))

    def reset_revealed(self):
        """Clear revealed state — all items will animate on next scroll."""
        self._revealed.clear()

    # ── Scroll handling ──

    def _on_scroll(self, value):
        """Called on every scroll tick — update gradients immediately, throttle viewport check."""
        self._update_gradient_opacities()
        if not self._scroll_throttle.isActive():
            self._scroll_throttle.start()

    def _update_gradient_opacities(self):
        """Compute gradient opacity based on scroll position."""
        sb = self.verticalScrollBar()
        scroll_top = sb.value()
        scroll_max = sb.maximum()

        # Top gradient: fade in as user scrolls down
        self._top_grad_opacity = min(scroll_top / 50.0, 1.0) if scroll_top > 0 else 0.0

        # Bottom gradient: fade in when there's more content below
        if scroll_max <= 0:
            self._bottom_grad_opacity = 0.0
        else:
            bottom_distance = scroll_max - scroll_top
            self._bottom_grad_opacity = min(bottom_distance / 50.0, 1.0)

        # Trigger repaint for gradients
        self.viewport().update()

    def _check_viewport(self):
        """Check which child widgets are in the viewport and animate them in."""
        if Motion.REDUCED_MOTION:
            return

        container = self.widget()
        if not container:
            return
        layout = container.layout()
        if not layout:
            return

        viewport_rect = self.viewport().rect()
        # Map viewport rect to container coordinates
        viewport_top = self.verticalScrollBar().value()
        viewport_bottom = viewport_top + viewport_rect.height()

        anims_started = 0

        for i in range(layout.count()):
            if anims_started >= self.MAX_CONCURRENT_ANIMS:
                break

            item = layout.itemAt(i)
            if not item or not item.widget():
                continue

            widget = item.widget()
            widget_id = id(widget)

            # Skip already-revealed widgets
            if widget_id in self._revealed:
                continue

            # Check if widget is in viewport (at least 50% visible)
            widget_top = widget.y()
            widget_bottom = widget_top + widget.height()
            widget_mid = widget_top + widget.height() / 2

            if widget_mid >= viewport_top and widget_mid <= viewport_bottom:
                # Widget is in view — animate it
                self._revealed.add(widget_id)

                if self._skip_next:
                    self._skip_next = False
                    continue

                self._animate_reveal(widget, delay=anims_started * 50)
                anims_started += 1

    def _animate_reveal(self, widget, delay=0):
        """Animate a widget with scale 0.7→1.0 + opacity 0→1.0."""
        # Set up opacity effect.
        #
        # Only if the widget does not already have one. Qt destroys a widget's
        # existing QGraphicsEffect when a new one is set, so doing this
        # unconditionally silently deleted the drop shadow that AnimatedButton
        # installs on itself — and the next hover then aborted the application.
        # A widget that already has an effect just fades in via height alone.
        opacity_effect = None
        if widget.graphicsEffect() is None:
            opacity_effect = QGraphicsOpacityEffect(widget)
            opacity_effect.setOpacity(0.0)
            widget.setGraphicsEffect(opacity_effect)

        # Initial scale (via transform)
        from PyQt6.QtGui import QTransform
        widget._orig_transform = widget.property("_anim_transform_set")

        dur = Motion.duration(self.REVEAL_DURATION)

        # Create animation group
        ag = QParallelAnimationGroup(self)

        # Opacity animation — only when we installed an effect of our own.
        if opacity_effect is not None:
            op_anim = QPropertyAnimation(opacity_effect, b"opacity")
            op_anim.setDuration(dur)
            op_anim.setStartValue(0.0)
            op_anim.setEndValue(1.0)
            op_anim.setEasingCurve(Motion.MICRO_EASE)
            ag.addAnimation(op_anim)

        # Use a proxy for scale animation via maximumHeight trick
        # We'll animate the widget's maximumHeight from a smaller value to actual
        actual_height = widget.sizeHint().height()
        if actual_height <= 0:
            actual_height = widget.height()
        if actual_height <= 0:
            actual_height = 60  # Fallback

        scaled_height = int(actual_height * 0.7)

        # Remember the floor we are about to borrow. Forcing it back to 0
        # afterwards would undo any setFixedHeight() on the widget and let the
        # layout squash it — which is exactly what happens to the sidebar nav
        # buttons and the chat bubbles once the text is scaled up.
        orig_min_height = widget.minimumHeight()

        # Animate minimum height
        h_anim = QPropertyAnimation(widget, b"minimumHeight")
        h_anim.setDuration(dur)
        h_anim.setStartValue(scaled_height)
        h_anim.setEndValue(actual_height)
        h_anim.setEasingCurve(Motion.MICRO_EASE)
        ag.addAnimation(h_anim)

        # Clean up on finish
        def _cleanup():
            try:
                # Only clear the effect if it is still the one we installed —
                # clearing someone else's would destroy it out from under them.
                if (opacity_effect is not None
                        and widget.graphicsEffect() is opacity_effect):
                    widget.setGraphicsEffect(None)
                widget.setMinimumHeight(orig_min_height)
            except RuntimeError:
                pass          # widget was destroyed mid-animation
            # Remove from active list
            if ag in self._active_anims:
                self._active_anims.remove(ag)

        ag.finished.connect(_cleanup)

        # Store reference to prevent GC
        self._active_anims.append(ag)

        # Start with optional delay
        if delay > 0:
            QTimer.singleShot(delay, ag.start)
        else:
            ag.start()

    # ── Gradient painting ──

    def paintEvent(self, event):
        """Paint gradient overlays on top of the scroll area."""
        super().paintEvent(event)

        if not self._show_gradients:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        r, g, b = self._gradient_color
        vp = self.viewport().rect()

        # Top gradient
        if self._top_grad_opacity > 0.01:
            top_grad = QLinearGradient(0, 0, 0, self._gradient_height)
            top_grad.setColorAt(0, QColor(r, g, b, int(255 * self._top_grad_opacity)))
            top_grad.setColorAt(1, QColor(r, g, b, 0))
            painter.fillRect(0, 0, vp.width(), self._gradient_height, top_grad)

        # Bottom gradient
        if self._bottom_grad_opacity > 0.01:
            bot_y = vp.height() - self._gradient_height
            bot_grad = QLinearGradient(0, bot_y, 0, vp.height())
            bot_grad.setColorAt(0, QColor(r, g, b, 0))
            bot_grad.setColorAt(1, QColor(r, g, b, int(255 * self._bottom_grad_opacity)))
            painter.fillRect(0, bot_y, vp.width(), self._gradient_height, bot_grad)

        painter.end()

    def showEvent(self, event):
        """When the scroll area becomes visible, do an initial viewport check."""
        super().showEvent(event)
        # Delay slightly so layout has settled
        QTimer.singleShot(100, self._check_viewport)
        QTimer.singleShot(100, self._update_gradient_opacities)
