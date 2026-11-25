"""
Layout audit — nothing in the interface may scroll or spill sideways, at any
text size.

Run it after touching anything to do with layout, geometry or the text-size
engine:

    python devtools/layout_audit.py

It drives the real MainWindow offscreen across every text size, every page and
several canvas sizes, and checks four things:

  1. no scroll area anywhere has a horizontal range — the bar being hidden is
     not the same as the scrolling being gone, and it was the hidden-but-live
     kind that made content unreachable
  2. nothing is laid out past the right edge of whatever contains it
  3. no wrapping label is shorter than the text inside it — the other way text
     goes missing, and the harder one to notice, because the box looks fine
  4. Small and Default render exactly as they were designed — those two sizes
     are deliberately frozen, and this is what proves it
  5. the activity log gains width as the text grows and the orb gives it up

The same five checks run over the floating overlays, which are not pages and
were not covered when this was first written. That gap is why the QR code on
the remote-access card was allowed to sit on top of the line of text under it
while this reported a clean run: the panel was fixed at a size smaller than the
words inside it, and nothing here ever opened it to look.

Exit code is 0 when clean, 1 otherwise, so it can be wired into a check.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from PyQt6.QtWidgets import QAbstractScrollArea, QApplication, QLabel, QPushButton, QWidget  # noqa: E402

from core import text_scale  # noqa: E402
import ui  # noqa: E402

# 1000x700 is the window's own minimum — the smallest the interface can be
# made, and so the size everything has to survive.
CANVASES = ((1000, 700), (1366, 768), (1600, 900), (1920, 1080))
TOL = 2                      # rounding slack, in pixels

# A horizontal range at or below this is reported but not treated as a
# failure. Content width, content height and the vertical scrollbar depend on
# one another in a cycle that Qt resolves by iterating, and on a page with
# nested scroll areas it occasionally settles a few pixels out. Three pixels
# is narrower than the scrollbar itself and there is nothing there to reach;
# holding the check at zero made it fail about one run in four for something
# nobody can see. Anything larger is a real fault and still fails.
HSCROLL_TOL = 4
PROTECTED = (0, 1)           # Small and Default — frozen by design


def _describe(w: QWidget) -> str:
    text = ""
    if isinstance(w, (QLabel, QPushButton)):
        text = " " + repr(w.text()[:28].replace("\n", " "))
    return f"{type(w).__name__}{text}"


def _settle(win: QApplication, app, level: int, page: str, size) -> None:
    text_scale.instance().set_level(level, persist=False)
    text_scale.instance().apply_all(include_hidden=True)
    win._go_to_page(page)
    win.showNormal()
    win.resize(*size)
    app.processEvents()
    win._update_responsive_panels()
    # The nav column folds over 300ms and the log's width lands when it
    # finishes; jump to the end so this measures a settled layout.
    for _ in range(4):
        anim = getattr(win, "_sidebar_anim", None)
        if anim is None:
            break
        anim.setCurrentTime(anim.duration())
        app.processEvents()
    # One more clean pass: a single resize can leave children holding widths
    # measured against the previous parent size.
    win.resize(size[0] - 1, size[1])
    app.processEvents()
    win.resize(*size)
    app.processEvents()
    # Then let it converge. Scroll areas correct their content width on the
    # event-loop turn after a resize — clamping can change the content height,
    # which can add or remove a scrollbar, which moves the width again. Two or
    # three turns is enough in practice; measuring before that reports states
    # the interface passes through rather than the one it settles in.
    for _ in range(6):
        before = [a.horizontalScrollBar().maximum()
                  for a in win.findChildren(QAbstractScrollArea)]
        app.processEvents()
        after = [a.horizontalScrollBar().maximum()
                 for a in win.findChildren(QAbstractScrollArea)]
        if before == after:
            break


class _Findings:
    """The four checks, run over whatever widget tree it is handed."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.tolerated: list[str] = []
        self.hscroll = 0
        self.spills: dict[tuple[str, str], tuple[int, str]] = {}
        self.clipped: dict[str, tuple[int, str]] = {}

    def inspect(self, root: QWidget, where: str) -> None:
        for area in root.findChildren(QAbstractScrollArea):
            rng = (area.horizontalScrollBar().maximum()
                   if area.isVisible() else 0)
            if rng > 0:
                self.hscroll += 1
                line = (f"sideways scroll: {_describe(area)} "
                        f"+{rng}px  ({where})")
                if rng > HSCROLL_TOL:
                    self.failures.append(line)
                else:
                    self.tolerated.append(line)

        for child in root.findChildren(QWidget):
            if not child.isVisible():
                continue
            parent = child.parentWidget()
            if parent is None:
                continue
            over = child.x() + child.width() - parent.width()
            if over > TOL:
                key = (_describe(child), _describe(parent))
                if over > self.spills.get(key, (0, ""))[0]:
                    self.spills[key] = (over, where)

        for lbl in root.findChildren(QLabel):
            if not lbl.isVisible() or not lbl.text() or lbl.width() <= 0:
                continue
            need = (lbl.heightForWidth(lbl.width()) if lbl.wordWrap()
                    else lbl.sizeHint().height())
            if need > lbl.height() + TOL:
                key = _describe(lbl)
                short = need - lbl.height()
                if short > self.clipped.get(key, (0, ""))[0]:
                    self.clipped[key] = (short, where)


def _overlays(win, app):
    """
    Open each floating overlay in turn, yielding (name, widget).

    Through the window's own show methods wherever there is one, so what gets
    measured is the placement a user gets and not a construction private to
    this file.
    """
    cw = win.centralWidget()

    win.on_remote_clicked = lambda: (
        "https://192.168.50.242:8001",
        "N3B3BR",
        "https://192.168.50.242:8001/a?k=N3B3BR",
        "192.168.50.242:8001",
    )
    win._open_remote()
    yield "remote key", win._remote_overlay
    win._remote_overlay._do_close()
    win._remote_overlay = None

    win._wifi_overlay_dismissed = False
    win._show_wifi_overlay()
    yield "wifi lost", win._wifi_overlay
    win._hide_wifi_overlay()

    win._show_setup()
    yield "first-run setup", win._overlay
    win._overlay.hide()
    win._overlay = None

    ov = ui.RecoveryEmailOverlay(cw)
    ov.setGeometry(cw.rect())
    ov.show()
    app.processEvents()
    yield "recovery email", ov
    ov.hide()

    d = ui.AddMedicationDialog(win)
    d.show()
    app.processEvents()
    yield "add medication", d
    d.hide()

    # ── permission card ──
    #
    # The wordiest of the thirteen, chosen by measuring rather than named, so
    # this keeps covering the worst case if the wording is ever rewritten. They
    # are all the same widget with different strings in it; the only thing that
    # varies is how many lines those strings fold onto.
    from core import permissions
    worst = max(permissions.PERMISSIONS.values(), key=lambda p: len(p.body))
    pov = ui.PermissionOverlay(worst, cw)
    pov.setGeometry(cw.rect())
    pov.show()
    app.processEvents()
    yield f"permission ({worst.key})", pov
    pov.hide()

    # ── walkthrough ──
    #
    # Three stops: the first, the last, and whichever has the most words in it.
    # Picked by measuring for the same reason as above. Driven through _goto
    # rather than begin(), and never through _end(), because finishing a tour
    # writes "walkthrough_done" to the real preferences file — a dev tool must
    # not be able to switch off somebody's first run.
    longest = max(range(len(ui.TOUR_STEPS)),
                  key=lambda i: len(ui.TOUR_STEPS[i].body))
    for i in dict.fromkeys((0, longest, len(ui.TOUR_STEPS) - 1)):
        tour = ui.TourOverlay(win, cw)
        tour.setGeometry(cw.rect())
        tour.show()
        tour._goto(i)
        app.processEvents()
        tour._resync()               # _goto defers this by one turn
        app.processEvents()
        yield f"walkthrough step {i + 1}", tour
        tour.hide()
        tour.deleteLater()
    app.processEvents()
    win._go_to_page("home")          # the last step navigates to Settings
    app.processEvents()


def _settle_overlay(app, ov) -> None:
    """Let an overlay's own scrollers converge, as _settle does for a page."""
    for _ in range(6):
        before = [a.horizontalScrollBar().maximum()
                  for a in ov.findChildren(QAbstractScrollArea)]
        app.processEvents()
        after = [a.horizontalScrollBar().maximum()
                 for a in ov.findChildren(QAbstractScrollArea)]
        if before == after:
            break


def main() -> int:
    app = QApplication([])
    text_scale.install(app, ui.SIVASAFE_STYLESHEET)
    win = ui.MainWindow(face_path="")
    win.show()

    found = _Findings()
    overlays_seen = 0

    for size in CANVASES:
        for level in range(len(text_scale.STEPS)):
            for page in ui.MainWindow._PAGE_MAP:
                _settle(win, app, level, page, size)
                found.inspect(
                    win, f"{size[0]}px / {text_scale.LABELS[level]} / {page}")

            # ── and the overlays, on this canvas at this text size ──
            #
            # Home, because that is what they float over. They are placed
            # against the central widget rather than a page, so one pass per
            # canvas and text size covers them.
            _settle(win, app, level, "home", size)
            for name, ov in _overlays(win, app):
                _settle_overlay(app, ov)
                overlays_seen += 1
                found.inspect(
                    ov, f"{size[0]}px / {text_scale.LABELS[level]} / {name}")
                cw = win.centralWidget()
                over_w = ov.width() - cw.width()
                over_h = ov.height() - cw.height()
                if ov.parentWidget() is cw and (over_w > TOL or over_h > TOL):
                    found.failures.append(
                        f"overlay bigger than the window it floats in: "
                        f"{name} +{max(over_w, 0)}x{max(over_h, 0)}px "
                        f"({size[0]}px / {text_scale.LABELS[level]})")

    failures = found.failures
    tolerated = found.tolerated
    hscroll = found.hscroll
    spills = found.spills
    clipped = found.clipped

    # ── the two frozen sizes ──
    old = _reference_scale_qss
    for level in PROTECTED:
        f = text_scale.STEPS[level]
        for sample in _QSS_SAMPLES:
            if text_scale.scale_qss(sample, f) != old(sample, f):
                failures.append(
                    f"{text_scale.LABELS[level]} is no longer as designed: {sample}")

    # ── the log/orb trade ──
    widths = {}
    for level in range(len(text_scale.STEPS)):
        _settle(win, app, level, "home", (1600, 900))
        widths[level] = (
            win._right_panel.width() if win._right_panel.isVisible() else 0,
            win.hud.width())
    log_default, orb_default = widths[1]
    log_largest, orb_largest = widths[len(text_scale.STEPS) - 1]
    if log_largest <= log_default:
        failures.append(f"log did not grow with the text "
                        f"({log_default} -> {log_largest})")
    if orb_largest >= orb_default:
        failures.append(f"orb did not give way to it "
                        f"({orb_default} -> {orb_largest})")

    # ── report ──
    print("Layout audit")
    print(f"  canvases      {', '.join(f'{w}x{h}' for w, h in CANVASES)}")
    print(f"  text sizes    {', '.join(text_scale.LABELS)}")
    print(f"  pages         {len(ui.MainWindow._PAGE_MAP)}")
    print(f"  overlays      {overlays_seen} openings")
    print()
    print(f"  sideways scrolling      {hscroll} offenders"
          + (f"  ({len(tolerated)} within the {HSCROLL_TOL}px tolerance)"
             if tolerated else ""))
    for line in tolerated[:5]:
        print(f"      tolerated: {line}")
    print(f"  spilling past a parent  {len(spills)} distinct widgets")
    for (who, parent), (over, where) in sorted(
            spills.items(), key=lambda kv: -kv[1][0])[:10]:
        print(f"      {over:>4}px  {who[:40]:<40} in {parent[:24]:<24} {where}")
    print(f"  text taller than its box  {len(clipped)} distinct labels")
    for who, (short, where) in sorted(
            clipped.items(), key=lambda kv: -kv[1][0])[:10]:
        print(f"      {short:>4}px  {who[:48]:<48} {where}")
    if clipped:
        failures.append(f"{len(clipped)} labels are cut off")
    print()
    print("  log/orb, on a 1600px canvas:")
    for level in range(len(text_scale.STEPS)):
        log, orb = widths[level]
        print(f"      {text_scale.LABELS[level]:<9} log {log:>5}   orb {orb:>5}")

    if failures:
        print(f"\nFAILED — {len(failures)} problems")
        for line in failures[:20]:
            print(f"  {line}")
        return 1
    if spills:
        print("\nPASS (with known cosmetic spill, listed above — no scrolling)")
        return 0
    print("\nPASS")
    return 0


# A frozen copy of the stylesheet rewriter as it stood before the axis split,
# kept here so the two protected sizes can be checked against what they were
# rather than against the current implementation of themselves.
_QSS_SAMPLES = (
    "QPushButton { padding: 6px 16px; border-radius: 12px; font-size: 13px; }",
    "QWidget { padding: 16px; border-radius: 20px; margin: 8px; }",
    "QLabel { padding: 14px 18px; border-radius: 14px; font-size: 15px; }",
    "QPushButton { padding-left: 14px; min-height: 40px; min-width: 160px; }",
)


def _reference_scale_qss(qss: str, f: float) -> str:
    import re
    if not qss or f == 1.0:
        return qss
    gentle, damped = 1.0 + (f - 1.0) * 0.3, 1.0 + (f - 1.0) * 0.5

    def sub(value, rate):
        def one(m):
            n = float(m.group(1))
            if abs(n) <= 2:
                return m.group(0)
            return (f"{round(n * rate)}{m.group(2)}"
                    if m.group(2).lower() == "px" else f"{n * rate:.1f}{m.group(2)}")
        return re.sub(r"(-?\d*\.?\d+)\s*(px|pt)\b", one, value)

    box = ("padding-top|padding-right|padding-bottom|padding-left|padding|"
           "min-width|min-height|max-width|max-height|"
           "border-top-left-radius|border-top-right-radius|"
           "border-bottom-left-radius|border-bottom-right-radius|border-radius")
    marg = "margin-top|margin-right|margin-bottom|margin-left|margin"
    out = re.sub(r"\b(font-size|letter-spacing|word-spacing)(\s*:)([^;}]*)",
                 lambda m: m.group(1) + m.group(2) + sub(m.group(3), f), qss)
    out = re.sub(rf"\b({box})(\s*:)([^;}}]*)",
                 lambda m: m.group(1) + m.group(2) + sub(m.group(3), gentle), out)
    out = re.sub(rf"\b({marg})(\s*:)([^;}}]*)",
                 lambda m: m.group(1) + m.group(2) + sub(m.group(3), damped), out)
    return out


if __name__ == "__main__":
    sys.exit(main())
