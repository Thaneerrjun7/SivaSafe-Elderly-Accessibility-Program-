from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, pyqtProperty, QParallelAnimationGroup, QRect, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPainterPath, QColor, QPixmap, QPen, QRadialGradient, QBrush, QFont
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtCore import QUrl
import os

from core.motion import Motion
from core.auth import C

class VerificationCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 36)
        
        self._state = "IDLE"  # IDLE, EXPANDING, SCAN, CONFIRM
        
        # Animatable properties
        self._corner_radius = 18.0
        self._check_scale = 0.0
        self._scan_opacity = 1.0
        self._content_opacity = 0.0
        
        self._pixmap = None
        self._face_rect = None
        self._score = 0.0

        self._scan_angle = 0.0
        self._tick = 0
        
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        
        # Preload chime

        
        self._handoff_cb = None
        
    @pyqtProperty(float)
    def corner_radius(self): return self._corner_radius
    
    @corner_radius.setter
    def corner_radius(self, v):
        self._corner_radius = v
        self.update()
        
    @pyqtProperty(float)
    def check_scale(self): return self._check_scale
    
    @check_scale.setter
    def check_scale(self, v):
        self._check_scale = v
        self.update()
        
    @pyqtProperty(float)
    def content_opacity(self): return self._content_opacity
    
    @content_opacity.setter
    def content_opacity(self, v):
        self._content_opacity = v
        self.update()
        
    @pyqtProperty(float)
    def scan_opacity(self): return self._scan_opacity
    
    @scan_opacity.setter
    def scan_opacity(self, v):
        self._scan_opacity = v
        self.update()

    def _step(self):
        if self._state == "SCAN":
            self._scan_angle = (self._scan_angle + 3.0) % 360
            self.update()
            
    def set_frame(self, pixmap: QPixmap, face_rect=None, score=0.0):
        self._pixmap = pixmap
        self._face_rect = face_rect
        self._score = score
        self.update()
        
    # Scan status is no longer painted here. It used to be a floating panel
    # across the bottom of the frame — which is where a face sits, so the
    # message covered the person it was describing. AuthGate owns it now, as a
    # real widget below the preview: see AuthGate._set_scan_status().

    def set_blinks(self, count: int):
        pass
        
    def expand(self, target_rect: QRect):
        self._state = "EXPANDING"
        self._tmr.start(16)
        
        self._ag = QParallelAnimationGroup(self)
        
        # Geometry
        geom = Motion.anim(self, "geometry", Motion.DUR_EXPAND, Motion.EXPAND_EASE)
        geom.setStartValue(self.geometry())
        geom.setEndValue(target_rect)
        self._ag.addAnimation(geom)
        
        # Corner radius
        rad = Motion.anim(self, "corner_radius", Motion.DUR_EXPAND, Motion.EXPAND_EASE)
        rad.setStartValue(18.0)
        rad.setEndValue(24.0)
        self._ag.addAnimation(rad)
        
        # Content Opacity (fade in during back half)
        # Using a slight delay or just standard ease for now, since QPropertyAnimation doesn't support 
        # startDelay inside QParallelAnimationGroup easily without sequential. We will just use standard fade.
        op = Motion.anim(self, "content_opacity", int(Motion.DUR_EXPAND), Motion.MICRO_EASE)
        op.setStartValue(0.0)
        op.setEndValue(1.0)
        self._ag.addAnimation(op)
        
        self._ag.finished.connect(self._on_expanded)
        self._ag.start()
        
    def _on_expanded(self):
        self._state = "SCAN"
        
    def confirm(self, handoff_callback):
        self._state = "CONFIRM"
        self._handoff_cb = handoff_callback
        
        self._ag = QParallelAnimationGroup(self)
        
        fade = Motion.anim(self, "scan_opacity", Motion.DUR_MICRO, Motion.MICRO_EASE)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        self._ag.addAnimation(fade)
        
        from PyQt6.QtCore import QEasingCurve
        scale = Motion.anim(self, "check_scale", Motion.DUR_MICRO, QEasingCurve.Type.OutBack)
        scale.setStartValue(0.0)
        scale.setEndValue(1.0)
        self._ag.addAnimation(scale)
        
        
        
        self._ag.finished.connect(self._on_confirmed)
        self._ag.start()
        
    def _on_confirmed(self):
        if self._handoff_cb:
            self._handoff_cb()
            
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        W, H = self.width(), self.height()
        
        # Draw background pill/card
        path = QPainterPath()
        path.addRoundedRect(0, 0, W, H, self._corner_radius, self._corner_radius)
        
        if self._state == "IDLE":
            p.fillPath(path, QColor(C.ACC)) # Brown notch
            return
            
        p.fillPath(path, QColor("#FAFAFA"))
        
        # Clip to rounded rect
        p.setClipPath(path)
        
        if self._pixmap and self._content_opacity > 0:
            p.setOpacity(self._content_opacity)
            # KeepAspectRatio, not ...ByExpanding: the whole frame is drawn,
            # never cropped to the card. AuthGate._preview_rect() gives the
            # card the camera's own aspect ratio, so "the whole frame" and
            # "fills the card" are the same thing and no bars appear.
            scaled = self._pixmap.scaled(
                W, H, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            ox = (W - scaled.width()) // 2
            oy = (H - scaled.height()) // 2
            p.drawPixmap(ox, oy, scaled)
            
        # Face Rect
            if self._face_rect is not None:
                fx, fy, fw, fh = self._face_rect
                sx = scaled.width() / self._pixmap.width() if self._pixmap else 1
                sy = scaled.height() / self._pixmap.height() if self._pixmap else 1
                rx = ox + fx * sx
                ry = oy + fy * sy
                rw = fw * sx
                rh = fh * sy
                p.setPen(QPen(QColor(C.PRI), 2))
                p.drawRect(int(rx), int(ry), int(rw), int(rh))
