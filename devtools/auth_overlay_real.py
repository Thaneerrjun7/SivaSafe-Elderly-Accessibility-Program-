"""
The REAL auth screen: MainWindow + AuthOverlay + begin(), matching main.py.
Camera capture loop stubbed so no webcam is used; the expand path still runs.
"""
import os
import sys
import time

ROOT = r"C:\Users\Arjun Shanker\Downloads\Mark-XLVI-main\Mark-XLVI-main"
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QObject, QEvent

from core import crash_guard
crash_guard.install()

import core.auth as auth_mod
auth_mod.AuthGate._camera_loop = lambda self: None      # no webcam

import ui as ui_mod
from core.auth_overlay import AuthOverlay, AUTH_WIDTH, AUTH_HEIGHT

siva = ui_mod.SivaUI("face.png")
win = siva._win
screen = QApplication.primaryScreen().availableGeometry()
win.setMinimumSize(AUTH_WIDTH, 400)
win.resize(AUTH_WIDTH, AUTH_HEIGHT)
win.setWindowTitle("S.I.V.A — AUTHENTICATION")

overlay = AuthOverlay(parent=win.centralWidget(), config_dir=Path(ROOT) / "config")
overlay.setGeometry(0, 0, win.centralWidget().width(), win.centralWidget().height())
overlay.raise_()


class _W(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and overlay.isVisible():
            overlay.setGeometry(0, 0, obj.width(), obj.height())
        return False


w = _W(win)
win.centralWidget().installEventFilter(w)

overlay.begin()

gate = overlay._auth


def rect(x):
    g = x.geometry()
    return f"x={g.x():4d} y={g.y():4d} w={g.width():4d} h={g.height():4d}"


def check():
    print(f"AUTH_WIDTH x AUTH_HEIGHT = {AUTH_WIDTH}x{AUTH_HEIGHT}")
    print(f"window          {win.width()}x{win.height()}")
    print(f"overlay         {rect(overlay)}")
    print(f"AuthGate        {rect(gate)}")
    print(f"placeholder     {rect(gate._camera_placeholder)}")
    print(f"camera card     {rect(gate._camera)}")
    print(f"pw panel        {rect(gate._pw_panel)} visible={gate._pw_panel.isVisible()}")
    ph = gate._camera_placeholder.geometry()
    cg = gate._camera.geometry()
    # The card takes the camera's aspect ratio inside the preview area, not the
    # whole area — comparing it against the placeholder would always say False.
    print(f"\ncard fitted to preview area: {gate._preview_rect() == cg}")
    print(f"preview area {ph.width()}x{ph.height()}  card {cg.width()}x{cg.height()}")
    win.grab().save(os.path.join(OUT, "AUTH_OVERLAY_real.png"))
    app = QApplication.instance()
    app.quit()


QTimer.singleShot(2000, check)
QApplication.instance().exec()
