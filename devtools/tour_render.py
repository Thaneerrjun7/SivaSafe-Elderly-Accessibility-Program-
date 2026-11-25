"""
Every stop on the first-run walkthrough, drawn and measured.

    python devtools/tour_render.py

Walks all twelve steps through the real MainWindow at two text sizes on the
smallest canvas the window can be made, and writes `TOUR_01..12.png` so the
result can be looked at as well as asserted.

Four checks per step:

  1. no scroll area anywhere gained a horizontal range — the hard requirement
     this interface holds at zero, and the reason the card is measured before
     it is placed rather than after
  2. the caption card is entirely inside the window. It is positioned by hand
     against the lit control, so nothing but the clamp in `_place_card` keeps
     it on screen, and a card half off the right edge is exactly the failure
     that clamp exists to prevent
  3. the lit area is inside the window too
  4. the words fit their boxes

Plus one check the others cannot make: **at a comfortable size, every step that
names a control must actually find it.** The tour resolves anchors by attribute
name and degrades to a centred card when one is missing — which is right at
1000x700, where the sidebar folds and the activity log is dropped, but at
1600x900 a missing anchor means somebody renamed a widget and the tour quietly
stopped pointing at anything. Degrading gracefully and being broken look
identical from the outside; this is what tells them apart.

Exit code is 0 when clean, 1 otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Deliberately NOT offscreen, unlike layout_audit. The offscreen platform ships
# no font database, so every glyph comes out as an empty box — fine for
# measuring, useless for the half of this tool that exists to be looked at.
# safety_page_render.py renders the same way and for the same reason.

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from PyQt6.QtWidgets import QAbstractScrollArea, QApplication, QLabel, QWidget  # noqa: E402

from core import permissions, prefs, text_scale  # noqa: E402
import ui  # noqa: E402

_OUT = Path(__file__).resolve().parent
TOL = 2
HSCROLL_TOL = 4          # same slack layout_audit allows, for the same reason

# The smallest the window can be made, and a comfortable one. Anchors are only
# required to resolve on the second: on the first the interface is entitled to
# have folded the thing being pointed at out of the way.
TIGHT = (1000, 700)
ROOMY = (1600, 900)

# Read rather than written down: the number of text steps has changed once
# already, and a hard-coded 4 is an IndexError the next time it does.
_LARGEST = len(text_scale.STEPS) - 1

problems: list[str] = []


def _describe(w: QWidget) -> str:
    text = f" {w.text()[:24]!r}" if isinstance(w, QLabel) else ""
    return f"{type(w).__name__}{text}"


def _settle(app, win, level: int, size) -> None:
    text_scale.instance().set_level(level, persist=False)
    text_scale.instance().apply_all(include_hidden=True)
    win.showNormal()
    win.resize(*size)
    app.processEvents()
    win._update_responsive_panels()
    for _ in range(4):
        anim = getattr(win, "_sidebar_anim", None)
        if anim is None:
            break
        anim.setCurrentTime(anim.duration())
        app.processEvents()
    for _ in range(6):
        app.processEvents()


def _run(app, win, level: int, size, save: bool, require_anchors: bool) -> None:
    label = f"{size[0]}x{size[1]} / {text_scale.LABELS[level]}"
    print(f"\n{label}")
    _settle(app, win, level, size)
    cw = win.centralWidget()

    for i, step in enumerate(ui.TOUR_STEPS):
        # Built fresh per step and never finished: TourOverlay._end() writes
        # "walkthrough_done" to the real preferences file, and a dev tool must
        # not be able to switch off somebody's first run.
        tour = ui.TourOverlay(win, cw)
        tour.setGeometry(cw.rect())
        tour.show()
        tour._goto(i)
        for _ in range(8):
            app.processEvents()
        tour._resync()
        for _ in range(4):
            app.processEvents()

        card = tour._card
        hole = tour._hole
        where = f"step {i + 1:>2} ({step.page or '-'}/{step.anchor or '-'})"

        # 1. sideways scrolling
        for area in tour.findChildren(QAbstractScrollArea):
            rng = area.horizontalScrollBar().maximum() if area.isVisible() else 0
            if rng > HSCROLL_TOL:
                problems.append(f"{label} {where}: sideways scroll "
                                f"+{rng}px in {_describe(area)}")

        # 2. the card is on screen
        r = card.geometry()
        if (r.left() < -TOL or r.top() < -TOL
                or r.right() > tour.width() + TOL
                or r.bottom() > tour.height() + TOL):
            problems.append(
                f"{label} {where}: card at {r.x()},{r.y()} {r.width()}x{r.height()} "
                f"is outside the {tour.width()}x{tour.height()} window")

        # 3. so is the lit area
        if hole is not None and not tour.rect().contains(hole):
            problems.append(f"{label} {where}: lit area {hole} escapes the window")

        # 4. the words fit
        for lbl in tour.findChildren(QLabel):
            if not lbl.isVisible() or not lbl.text() or lbl.width() <= 0:
                continue
            need = (lbl.heightForWidth(lbl.width()) if lbl.wordWrap()
                    else lbl.sizeHint().height())
            if need > lbl.height() + TOL:
                problems.append(f"{label} {where}: {_describe(lbl)} needs "
                                f"{need - lbl.height()}px more height")

        # 5. anchors resolve, where the interface has room to show them
        if step.anchor:
            if hole is None:
                msg = f"{label} {where}: anchor {step.anchor!r} did not resolve"
                if require_anchors:
                    problems.append(msg)
                else:
                    print(f"  {where}  (anchor folded away — allowed here)")

        # 6. a step that asks for something really does raise the card. This is
        #    the wiring that turns the tour from a slideshow into the thing the
        #    permission system is introduced by, and it is invisible from the
        #    outside if it silently stops working.
        asked = win._perm_overlay
        if step.ask and not permissions.is_granted(step.ask):
            if asked is None:
                problems.append(f"{label} {where}: asks for {step.ask!r} "
                                "but no permission card appeared")
            elif asked._perm.key != step.ask:
                problems.append(f"{label} {where}: raised the {asked._perm.key!r} "
                                f"card instead of {step.ask!r}")

        spot = f"lit {hole.width()}x{hole.height()}" if hole else "centred"
        ask = f"  asks {step.ask}" if step.ask else ""
        print(f"  {where}  card {r.width()}x{r.height()} @{r.x()},{r.y()}  {spot}{ask}")

        if save:
            path = _OUT / f"TOUR_{i + 1:02d}.png"
            win.grab().save(str(path))

        # Answered with Cancel, never Allow: Cancel is the one that writes
        # nothing, so this harness cannot grant somebody a permission by
        # running.
        if win._perm_overlay is not None:
            win._perm_overlay._cancel.click()
            app.processEvents()
        win._perm_queue.clear()

        tour.hide()
        tour.deleteLater()
        app.processEvents()

    win._go_to_page("home")
    app.processEvents()


def main() -> int:
    prev = prefs.get("text_scale_level", 1)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(ui.SIVASAFE_STYLESHEET)
    ui._init_fonts()
    text_scale.install(app, ui.SIVASAFE_STYLESHEET)
    win = ui.MainWindow(face_path="")
    win.show()

    try:
        # Roomy first, so the PNGs saved are the legible ones and the anchor
        # check runs where every control is actually on screen.
        _run(app, win, 1, ROOMY, save=True, require_anchors=True)
        _run(app, win, _LARGEST, ROOMY, save=False, require_anchors=True)
        _run(app, win, 1, TIGHT, save=False, require_anchors=False)
        _run(app, win, _LARGEST, TIGHT, save=False, require_anchors=False)
    finally:
        text_scale.instance().set_level(prev, persist=False)
        prefs.set("text_scale_level", prev)
        win.hide()

    print("\n" + "=" * 62)
    print(f"steps {len(ui.TOUR_STEPS)}  ·  canvases {TIGHT[0]}x{TIGHT[1]}, "
          f"{ROOMY[0]}x{ROOMY[1]}  ·  text sizes Default, Largest")
    print(f"PNGs written to {_OUT}\\TOUR_01..{len(ui.TOUR_STEPS):02d}.png")
    if problems:
        print(f"\nFAILED — {len(problems)} problems")
        for p in problems[:25]:
            print(f"  · {p}")
        return 1
    print("\nPASS — every step fits, points at something, and never scrolls sideways")
    return 0


if __name__ == "__main__":
    sys.exit(main())
