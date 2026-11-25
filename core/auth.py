"""
core/auth.py — Deep-learning facial recognition + password authentication gate for SIVA.

Uses OpenCV's built-in DNN face recognition pipeline:
  • YuNet   — deep-learning face detector  (~430 KB ONNX model)
  • SFace   — deep-learning face recogniser (~37 MB ONNX model)
             Creates a 128-dimensional face embedding unique to each person.
             Cosine similarity is used for matching — different people score
             well below 0.3, the same person consistently scores above 0.5.

These are production-grade models from the OpenCV Model Zoo, originally
trained on large-scale datasets on NVIDIA GPUs.  They run on CPU via ONNX.

Security features:
  • Deep-learning 128D face embeddings (not texture patterns like LBPH)
  • Only ONE person can enroll — others are rejected with high confidence
  • Blink-based liveness detection (blocks photo spoofing)
  • Multi-frame enrollment with consistency check
  • PBKDF2-HMAC password hashing with random salt (600 000 iterations)
  • Brute-force lockout (5 attempts → 5 min cooldown)
  • Face embedding file integrity check (SHA-256)
  • Dual password input: keyboard + voice (🎤 mic icon)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from PyQt6.QtCore import (
    QRect, QRectF, QSize, Qt, QTimer, pyqtSignal, QPoint, QPointF
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from core import prefs
from core.motion import Motion, AnimatedButton
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget, QFrame,
)


# ── Colours (match SIVA UI) ─────────────────────────────────────────────────

class C:
    BG        = "#EBE7DF"
    PANEL     = "#F2F0EB"
    PANEL2    = "#F2F0EB"
    BORDER    = "#E6D5B8"
    BORDER_B  = "#8B5E3C"
    PRI       = "#376757"
    PRI_DIM   = "#7DAF9C"
    PRI_GHO   = "rgba(55,103,87,0.1)"
    ACC       = "#8B5E3C"
    ACC2      = "#376757"
    GREEN     = "#376757"
    RED       = "#D32F2F"
    TEXT      = "#2D2926"
    TEXT_DIM  = "#5D4037"
    WHITE     = "#FFFFFF"


def _qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


# ── Paths & constants ────────────────────────────────────────────────────────

# Lockout state lives beside the config, not in %TEMP%.
#
# It used to sit in the temp directory, where emptying temp — or Windows'
# own disk cleanup, on its own schedule — wiped it. That made the lockout both
# trivially bypassable by anyone guessing, and liable to forget itself for a
# legitimate user midway through a cooldown.
_LOCKOUT_FILE   = Path(__file__).resolve().parent.parent / "config" / "auth_lockout.json"
# One lockout, one short second chance, then the door changes.
#
# Five wrong passwords costs 50 seconds. After the wait there are only two
# more tries — someone who knows the password does not need five again, and
# someone who does not know it is only guessing. Those two gone, guessing
# stops being an option at all: the only way in is a code emailed to the
# address on file, which every user is required to have, so this can never be
# a dead end.
_MAX_ATTEMPTS   = 5            # first round
_SECOND_ROUND_ATTEMPTS = 2     # after the 50-second wait
_LOCKOUT_FIRST  = 50
_ROUNDS_BEFORE_RECOVERY = 2
_FACE_TIMEOUT   = 15           # seconds before password fallback
_WARMUP_FRAMES  = 30           # skip for camera auto-exposure
_ENROLL_FRAMES  = 40           # total frames captured during enrollment
_MIN_BLINKS     = 2            # required blinks for liveness
_PBKDF2_ITERS   = 600_000

# Cosine similarity threshold for SFace (OpenCV default is 0.363)
# We use 0.40 — strict enough to block different faces, lenient enough for
# lighting / angle variation of the enrolled person.
_COSINE_THRESHOLD = 0.65

# ── Model URLs (OpenCV Model Zoo — hosted on GitHub) ─────────────────────────
_MODELS_DIR_NAME = "models"
_YUNET_FILENAME  = "face_detection_yunet_2023mar.onnx"
_SFACE_FILENAME  = "face_recognition_sface_2021dec.onnx"
_YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/"
    "models/face_detection_yunet/" + _YUNET_FILENAME
)
_SFACE_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/"
    "models/face_recognition_sface/" + _SFACE_FILENAME
)

# Haar cascade for blink detection (ships with OpenCV)
_CASCADE_DIR = Path(cv2.data.haarcascades)  # type: ignore[attr-defined]
_EYE_CASCADE_PATH = str(_CASCADE_DIR / "haarcascade_eye_tree_eyeglasses.xml")


# ═════════════════════════════════════════════════════════════════════════════
#  FaceAuth — deep-learning face detection + recognition + liveness
# ═════════════════════════════════════════════════════════════════════════════

class FaceAuth:
    """Deep-learning face auth engine (OpenCV YuNet + SFace)."""

    def __init__(self, config_dir: Path, status_callback=None):
        self._config_dir     = config_dir
        self._models_dir     = config_dir / _MODELS_DIR_NAME
        self._embedding_path = config_dir / "face_embedding.npy"
        self._config_path    = config_dir / "api_keys.json"
        self._status_cb      = status_callback  # for UI progress updates

        self._eye_cascade = cv2.CascadeClassifier(_EYE_CASCADE_PATH)
        self._clahe       = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        self._detector:   cv2.FaceDetectorYN   | None = None
        self._recognizer: cv2.FaceRecognizerSF | None = None
        self._stored_embedding: np.ndarray | None = None
        self._models_ready = False

        self._init_models()

    # ── model download & init ────────────────────────────────────────────────

    def _report(self, msg: str):
        try:
            print(f"[Auth] {msg}")
        except UnicodeEncodeError:
            print(f"[Auth] {msg.encode('ascii', errors='replace').decode('ascii')}")
        if self._status_cb:
            self._status_cb(msg)

    def _download_file(self, url: str, dest: Path, label: str) -> bool:
        if dest.exists() and dest.stat().st_size > 1000:
            return True  # already downloaded
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._report(f"Downloading {label}…")
        try:
            urllib.request.urlretrieve(url, str(dest))
            self._report(f"✓ {label} downloaded ({dest.stat().st_size / 1e6:.1f} MB)")
            return True
        except Exception as e:
            self._report(f"✗ Failed to download {label}: {e}")
            return False

    def _init_models(self):
        """Download ONNX models if needed and initialise detectors."""
        yunet_path = self._models_dir / _YUNET_FILENAME
        sface_path = self._models_dir / _SFACE_FILENAME

        ok1 = self._download_file(_YUNET_URL, yunet_path, "YuNet face detector")
        ok2 = self._download_file(_SFACE_URL, sface_path, "SFace face recogniser")

        if not (ok1 and ok2):
            self._report("⚠ Face models unavailable — password-only mode")
            self._models_ready = False
            return

        try:
            self._detector = cv2.FaceDetectorYN.create(
                str(yunet_path), "", (320, 320),
                score_threshold=0.7,
                nms_threshold=0.3,
                top_k=5,
            )
            self._recognizer = cv2.FaceRecognizerSF.create(
                str(sface_path), ""
            )
            self._models_ready = True
            self._report("✓ Face recognition engine ready (YuNet + SFace)")
        except Exception as e:
            self._report(f"✗ Failed to initialise models: {e}")
            self._models_ready = False
            return

        # Load stored face embedding if exists
        if self._embedding_path.exists():
            try:
                cfg = self._read_config()
                stored_hash = cfg.get("face_model_hash", "")
                if stored_hash:
                    actual_hash = self._embedding_hash()
                    if actual_hash != stored_hash:
                        self._report("⚠ Face data tampered! Forcing password auth.")
                        self._stored_embedding = None
                        return
                self._stored_embedding = np.load(str(self._embedding_path))
                self._report(f"✓ Face embedding loaded ({self._stored_embedding.shape})")
            except Exception as e:
                self._report(f"✗ Failed to load face embedding: {e}")
                self._stored_embedding = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _read_config(self) -> dict:
        with open(self._config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_config(self, cfg: dict):
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)

    def _embedding_hash(self) -> str:
        h = hashlib.sha256()
        with open(self._embedding_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @property
    def has_model(self) -> bool:
        return self._models_ready and self._stored_embedding is not None

    @property
    def models_ready(self) -> bool:
        return self._models_ready

    # ── face detection ───────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray):
        """Detect faces using YuNet DNN. Returns (faces_array, largest_idx)."""
        if not self._detector:
            return None, -1

        h, w = frame.shape[:2]
        self._detector.setInputSize((w, h))
        _, faces = self._detector.detect(frame)

        if faces is None or len(faces) == 0:
            return None, -1

        # Return the largest face
        areas = [f[2] * f[3] for f in faces]
        idx = int(np.argmax(areas))
        return faces, idx

    def get_face_rect(self, faces, idx) -> tuple[int, int, int, int]:
        """Extract (x, y, w, h) from the detected face array."""
        f = faces[idx]
        return int(f[0]), int(f[1]), int(f[2]), int(f[3])

    # ── face embedding ───────────────────────────────────────────────────────

    def _extract_embedding(self, frame: np.ndarray, face) -> np.ndarray:
        """Align face and extract 128D embedding."""
        aligned = self._recognizer.alignCrop(frame, face)
        return self._recognizer.feature(aligned)

    # ── enrollment ───────────────────────────────────────────────────────────

    def enroll(self, frames: list[np.ndarray]) -> bool:
        """Enroll a face from captured frames.

        Takes multiple frames, extracts embeddings, checks consistency
        (all embeddings must belong to the same person), then stores the
        average embedding.
        """
        if not self._models_ready:
            return False

        embeddings: list[np.ndarray] = []

        for frame in frames:
            faces, idx = self.detect(frame)
            if faces is None:
                continue
            emb = self._extract_embedding(frame, faces[idx])
            embeddings.append(emb)

        if len(embeddings) < 10:
            self._report(f"Enrollment failed: only {len(embeddings)} usable frames")
            return False

        # ── Consistency check: all frames must be the SAME person ────────
        # Compare each pair. If any pair has cosine similarity < 0.5, the
        # frames contain different people → reject.
        ref = embeddings[0]
        for i, emb in enumerate(embeddings[1:], 1):
            score = self._recognizer.match(
                ref, emb, cv2.FaceRecognizerSF_FR_COSINE
            )
            if score < 0.5:
                self._report(
                    f"Enrollment failed: inconsistent faces "
                    f"(frame {i} scored {score:.3f} vs reference)"
                )
                return False

        # Average all embeddings for a robust representation
        avg_embedding = np.mean(embeddings, axis=0).astype(np.float32)
        # Normalise
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm

        # Save embedding
        np.save(str(self._embedding_path), avg_embedding)
        self._stored_embedding = avg_embedding

        # Save hash
        cfg = self._read_config()
        cfg["face_model_hash"] = self._embedding_hash()
        self._write_config(cfg)

        self._report(f"✓ Enrolled with {len(embeddings)} frames (averaged)")
        return True

    # ── verification ─────────────────────────────────────────────────────────

    def verify(self, frame: np.ndarray) -> tuple[bool, float, Optional[tuple]]:
        """Verify a face against the stored embedding.

        Returns (matched, cosine_score, face_rect_or_None).
        """
        if not self._models_ready or self._stored_embedding is None:
            return False, 0.0, None

        faces, idx = self.detect(frame)
        if faces is None:
            return False, 0.0, None

        face_rect = self.get_face_rect(faces, idx)
        emb = self._extract_embedding(frame, faces[idx])

        score = self._recognizer.match(
            self._stored_embedding, emb, cv2.FaceRecognizerSF_FR_COSINE
        )

        matched = score >= _COSINE_THRESHOLD
        return matched, float(score), face_rect

    # ── blink detection (still uses Haar eye cascade) ────────────────────────

    def check_eyes(self, frame: np.ndarray, face_rect: tuple) -> int:
        """Returns number of eyes detected in the face region (0, 1, or 2)."""
        x, y, w, h = face_rect
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        grey = self._clahe.apply(grey)
        roi = grey[y:y + h, x:x + w]
        # Only search upper 60% of face (eye region)
        eye_roi = roi[0:int(h * 0.6), :]
        eyes = self._eye_cascade.detectMultiScale(
            eye_roi, scaleFactor=1.1, minNeighbors=4,
            minSize=(int(w * 0.08), int(h * 0.04)),
        )
        return len(eyes)


# ═════════════════════════════════════════════════════════════════════════════
#  Password utilities
# ═════════════════════════════════════════════════════════════════════════════

def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """PBKDF2-HMAC-SHA256.  Returns (hex_hash, hex_salt)."""
    if salt is None:
        salt = os.urandom(32)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return h.hex(), salt.hex()


def _verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    if not stored_hash or not stored_salt:
        return False
    salt = bytes.fromhex(stored_salt)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return h.hex() == stored_hash


# ── lockout persistence ──────────────────────────────────────────────────────

def _read_lockout() -> tuple[int, float, int]:
    """(failed attempts this round, locked-until timestamp, rounds served)."""
    try:
        d = json.loads(_LOCKOUT_FILE.read_text(encoding="utf-8"))
        return (int(d.get("fails", 0)),
                float(d.get("until", 0.0)),
                int(d.get("rounds", 0)))
    except Exception:
        return 0, 0.0, 0


def _write_lockout(fails: int, until: float, rounds: int = 0):
    # Through safe_store: a kill mid-write used to be able to leave a truncated
    # file, which reads back as "no lockout at all".
    from core import safe_store
    safe_store.write_json(_LOCKOUT_FILE,
                          {"fails": fails, "until": until, "rounds": rounds},
                          indent=2)


def _clear_lockout():
    try:
        _LOCKOUT_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _attempts_allowed(rounds: int) -> int:
    """Five tries to begin with; only two after the wait."""
    return _MAX_ATTEMPTS if rounds < 1 else _SECOND_ROUND_ATTEMPTS


def _lockout_duration(round_number: int) -> int:
    """
    How long a lockout lasts. There is only ever one — 50 seconds, after the
    first five wrong. The round after it goes straight to recovery instead.
    """
    return _LOCKOUT_FIRST


def recovery_required(rounds: int = None) -> bool:
    """
    Has guessing been exhausted?

    True once both lockouts have been served. From here the password box is
    dead and a mailed code is the only way in.
    """
    if rounds is None:
        rounds = _read_lockout()[2]
    return rounds >= _ROUNDS_BEFORE_RECOVERY


# ═════════════════════════════════════════════════════════════════════════════
#  Word-to-number conversion for spoken passwords
# ═════════════════════════════════════════════════════════════════════════════

_WORD_NUMS = {
    "zero": 0, "oh": 0, "o": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_MULTIPLIERS = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}


def _words_to_number(text: str) -> str:
    """Convert spoken number words to digit string.

    Examples:
        'two thousand two'           → '2002'
        'one two three four'         → '1234'
        'nineteen ninety nine'       → '1999'
        'four five six seven'        → '4567'
        'twenty twenty'              → '2020'
        'two thousand and twenty five' → '2025'

    If the text contains no recognisable number words it is returned as-is
    (lowercased, spaces removed) so typed-out passwords still work.
    """
    import re as _re

    # Normalise
    raw = text.lower().strip()
    # Strip filler words
    tokens = [t for t in _re.split(r"[\s,]+", raw) if t not in ("and", "a", "")]

    if not tokens:
        return raw.replace(" ", "")

    # ── Try compound number interpretation first ────────────────────────
    # e.g. "two thousand two" → 2002, "nineteen hundred" → 1900
    has_multiplier = any(t in _MULTIPLIERS for t in tokens)
    has_number_word = any(t in _WORD_NUMS for t in tokens)

    if has_number_word and has_multiplier:
        # Standard compound number: accumulate with multipliers
        result = 0
        current = 0
        for tok in tokens:
            if tok in _WORD_NUMS:
                current += _WORD_NUMS[tok]
            elif tok in _MULTIPLIERS:
                mult = _MULTIPLIERS[tok]
                if current == 0:
                    current = 1
                current *= mult
                if mult >= 1000:
                    result += current
                    current = 0
            # skip unrecognised tokens
        result += current
        return str(result)

    # ── Sequence-of-digits interpretation ────────────────────────────────
    # e.g. "four five six seven" → "4567", "twenty twenty" → "2020"
    if has_number_word:
        digits: list[str] = []
        for tok in tokens:
            if tok in _WORD_NUMS:
                val = _WORD_NUMS[tok]
                digits.append(str(val))
            elif tok.isdigit():
                digits.append(tok)
            # skip unrecognised
        if digits:
            return "".join(digits)

    # ── Fallback: strip spaces and return raw (handles "2002" spoken as-is)
    cleaned = _re.sub(r"\s+", "", raw)
    return cleaned


# ═════════════════════════════════════════════════════════════════════════════
#  VoicePassword — record mic → transcribe via Whisper (offline)
# ═════════════════════════════════════════════════════════════════════════════

class VoicePassword:
    """Records from the microphone and transcribes using the project's own
    WhisperSTT engine (faster-whisper, fully offline after first download).
    Spoken number words are converted to digits for password matching.
    """

    _engine = None  # class-level cache so the model loads only once

    @classmethod
    def _get_engine(cls):
        if cls._engine is None:
            from core.stt import WhisperSTT
            cls._engine = WhisperSTT(model_name="base", language="en")
        return cls._engine

    @staticmethod
    def listen(duration: float = 5.0, sample_rate: int = 16000) -> str:
        try:
            import sounddevice as sd
        except ImportError as e:
            print(f"[Auth] Voice password unavailable (sounddevice): {e}")
            return ""
        try:
            # Record audio as int16 mono
            print("[Auth] 🎤 Recording voice password…")
            audio_i16 = sd.rec(
                int(duration * sample_rate),
                samplerate=sample_rate, channels=1, dtype="int16",
            )
            sd.wait()
            print(f"[Auth] 🎤 Recorded {len(audio_i16)} samples")

            # Convert int16 → float32 normalised (Whisper expects this)
            audio_f32 = audio_i16.flatten().astype(np.float32) / 32768.0

            # Transcribe with Whisper
            engine = VoicePassword._get_engine()
            transcript = engine.transcribe(audio_f32)
            print(f"[Auth] 🎤 Raw transcript: '{transcript}'")

            if not transcript:
                return ""

            # Convert spoken numbers to digits
            converted = _words_to_number(transcript)
            print(f"[Auth] 🎤 Converted: '{transcript}' → '{converted}'")
            return converted

        except Exception as e:
            print(f"[Auth] Voice recognition error: {e}")
            import traceback; traceback.print_exc()
            return ""


# ═════════════════════════════════════════════════════════════════════════════
#  CameraPreview — live camera feed widget with scanning overlay
# ═════════════════════════════════════════════════════════════════════════════

class _CameraPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pixmap: QPixmap | None = None
        self._face_rect: tuple | None = None
        self._status = "INITIALISING"
        self._status_color = _qcol(C.PRI)
        self._blinks = 0
        self._scan_angle = 0.0
        self._tick = 0
        self._score = 0.0
        self._cached_scanlines: QPixmap | None = None

    def set_frame(self, pixmap: QPixmap, face_rect=None, score=0.0):
        self._pixmap = pixmap
        self._face_rect = face_rect
        self._score = score
        self._tick += 1
        self._scan_angle = (self._scan_angle + 3.0) % 360
        self.update()

    def set_status(self, text: str, color: QColor):
        self._status = text
        self._status_color = color
        self.update()

    def set_blinks(self, count: int):
        self._blinks = count
        self.update()



    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.fillRect(self.rect(), QColor("#FAFAFA"))

        if self._pixmap:
            pad_x = 0
            pad_y = 0
            pw = W - pad_x * 2
            ph = H - pad_y * 2
            
            scaled = self._pixmap.scaled(
                pw, ph, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            
            p.save()
            path = QPainterPath()
            path.addRoundedRect(pad_x, pad_y, pw, ph, 8, 8)
            p.setClipPath(path)
            
            ox = pad_x + (pw - scaled.width()) // 2
            oy = pad_y + (ph - scaled.height()) // 2
            p.drawPixmap(ox, oy, scaled)
            
            if self._face_rect is not None:
                fx, fy, fw, fh = self._face_rect
                sx = scaled.width() / self._pixmap.width()
                sy = scaled.height() / self._pixmap.height()
                rx = int(ox + fx * sx)
                ry = int(oy + fy * sy)
                rw = int(fw * sx)
                rh = int(fh * sy)
                
                # Dynamic Status Colors
                is_match = self._status.startswith("RECOGNISED") or self._status.startswith("ENROLLMENT COMPLETE")
                
                if is_match:
                    box_color = QColor(0, 255, 0) # Green (Authenticated)
                    glow_color = QColor(0, 255, 0, 70)
                elif self._score >= 0.3:
                    box_color = QColor(186, 142, 94) # Brown/Gold (OK)
                    glow_color = QColor(186, 142, 94, 70)
                elif self._score > 0.0:
                    box_color = QColor(255, 200, 0) # Yellow (Unsure)
                    glow_color = QColor(255, 200, 0, 70)
                else:
                    box_color = QColor(255, 50, 50) # Red (Not detected/unrecognized)
                    glow_color = QColor(255, 50, 50, 70)

                bl = min(rw, rh) // 3
                corner_radius = 8
                
                def draw_corners(pen):
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    # TL
                    tl = QPainterPath()
                    tl.moveTo(rx + bl, ry)
                    tl.lineTo(rx + corner_radius, ry)
                    tl.arcTo(rx, ry, corner_radius*2, corner_radius*2, 90, 90)
                    tl.lineTo(rx, ry + bl)
                    p.drawPath(tl)
                    # TR
                    tr = QPainterPath()
                    tr.moveTo(rx + rw - bl, ry)
                    tr.lineTo(rx + rw - corner_radius, ry)
                    tr.arcTo(rx + rw - corner_radius*2, ry, corner_radius*2, corner_radius*2, 90, -90)
                    tr.lineTo(rx + rw, ry + bl)
                    p.drawPath(tr)
                    # BL
                    bl_path = QPainterPath()
                    bl_path.moveTo(rx + bl, ry + rh)
                    bl_path.lineTo(rx + corner_radius, ry + rh)
                    bl_path.arcTo(rx, ry + rh - corner_radius*2, corner_radius*2, corner_radius*2, 270, -90)
                    bl_path.lineTo(rx, ry + rh - bl)
                    p.drawPath(bl_path)
                    # BR
                    br = QPainterPath()
                    br.moveTo(rx + rw - bl, ry + rh)
                    br.lineTo(rx + rw - corner_radius, ry + rh)
                    br.arcTo(rx + rw - corner_radius*2, ry + rh - corner_radius*2, corner_radius*2, corner_radius*2, 270, 90)
                    br.lineTo(rx + rw, ry + rh - bl)
                    p.drawPath(br)
                    
                draw_corners(QPen(glow_color, 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                draw_corners(QPen(box_color, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

            p.restore() # Remove clipping

        # Apple-style white frosted status panel
        parts = self._status.split("—")
        line1 = parts[0].strip()
        line2 = parts[1].strip() if len(parts) > 1 else ""

        p.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        fm1 = p.fontMetrics()
        tw1 = fm1.horizontalAdvance(line1)
        
        p.setFont(QFont("Segoe UI", 15))
        fm2 = p.fontMetrics()
        tw2 = fm2.horizontalAdvance(line2) if line2 else 0
        
        max_w = max(tw1, tw2)
        panel_w = max(260, max_w + 80)
        panel_h = 80 if line2 else 60
        
        # Draw panel centered at the bottom, overlapping the camera view
        px = W / 2 - panel_w / 2
        py = H - panel_h - 20 # Overlap
        
        panel_rect = QRectF(px, py, panel_w, panel_h)
        
        # Soft shadow
        for i in range(1, 6):
            shadow = QRectF(px - i, py - i + 4, panel_w + i*2, panel_h + i*2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 8 - i))
            p.drawRoundedRect(shadow, 20, 20)

        # Panel body
        p.setBrush(QColor(255, 255, 255, 240))
        p.setPen(QPen(QColor(255, 255, 255, 100), 1))
        p.drawRoundedRect(panel_rect, 20, 20)
        
        # Text
        is_match = self._status.startswith("RECOGNISED") or self._status.startswith("ENROLLMENT COMPLETE")
        
        # If RECOGNISED, make it say "Face matched" like the image requested if possible, or just keep status
        if line1 == "RECOGNISED":
            line1 = "Face matched"
            tw1 = fm1.horizontalAdvance(line1)
            
        col1 = QColor(60, 180, 100) if is_match else QColor(40, 40, 40)
        
        p.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        p.setPen(col1)
        if line2:
            y1 = int(py + 18 + fm1.ascent())
            p.drawText(int(W/2 - tw1/2), y1, line1)
            p.setFont(QFont("Segoe UI", 15))
            p.setPen(QColor(40, 40, 40))
            y2 = int(py + 46 + fm2.ascent())
            p.drawText(int(W/2 - tw2/2), y2, line2)
        else:
            y1 = int(py + panel_h/2 - fm1.height()/2 + fm1.ascent())
            p.drawText(int(W/2 - tw1/2), y1, line1)

        p.end()


# ═════════════════════════════════════════════════════════════════════════════
#  AuthGate — authentication widget (embedded inside AuthOverlay)
# ═════════════════════════════════════════════════════════════════════════════

class AuthGate(QWidget):
    """Authentication gate widget.  Emits auth_completed / auth_failed."""

    _frame_sig   = pyqtSignal(object, object, float)  # (QPixmap, face_rect, score)
    _status_sig  = pyqtSignal(str, object)
    _blink_sig   = pyqtSignal(int)
    _done_sig    = pyqtSignal(bool)
    _enroll_prompt_sig = pyqtSignal(str)
    _voice_pw_sig = pyqtSignal(str)

    # Public signals consumed by the overlay
    auth_completed = pyqtSignal()     # face or password verified
    auth_failed    = pyqtSignal()     # user closed / cancelled
    recovery_needed = pyqtSignal()    # guessing exhausted — email is the way in

    # Password panel headings — calm while the scan runs, warning after it fails.
    _PW_TITLE_IDLE = "◈  ENTER PASSWORD  ·  OR RETRY THE FACE SCAN"
    _PW_TITLE_FAIL = "⚠  FACE NOT RECOGNISED — ENTER PASSWORD"
    _PW_TITLE_ONLY = "◈  ENTER YOUR PASSWORD"

    def __init__(self, config_dir: Path, parent=None, mode: str = None):
        super().__init__(parent)
        # "face_password" runs the camera with the password behind it;
        # "password" skips the camera entirely. There is no face-only mode —
        # see the note in core/prefs.py.
        self._mode = mode or prefs.AUTH_FACE_PASSWORD
        self._config_dir  = config_dir
        self._config_path = config_dir / "api_keys.json"
        self._authenticated = False
        self._camera_active = False
        self._voice_recording = False
        (self._failed_attempts, self._lockout_until,
         self._lockout_rounds) = _read_lockout()
        # Counts wrong passwords in *this* sitting, which is what decides
        # whether a successful face scan should push the user to reset it.
        self._password_failures = 0
        # Read by main.py once the dashboard is up — see _on_done.
        self.needs_password_reset = False

        self._face_auth: FaceAuth | None = None  # init in camera thread

        # Success chime. It used to belong to the face persona widget; it is
        # kept here so removing that widget did not also remove the sound.
        # Guarded: a machine with no working audio backend must not be able to
        # stop someone signing in.
        self._chime = None
        try:
            from PyQt6.QtMultimedia import QSoundEffect
            from PyQt6.QtCore import QUrl
            wav = Path(__file__).resolve().parent.parent / "assets" / "sounds" / "confirm_chime.wav"
            if wav.exists():
                self._chime = QSoundEffect(self)
                self._chime.setSource(QUrl.fromLocalFile(str(wav)))
                self._chime.setVolume(0.6)
        except Exception:
            self._chime = None

        self._setup_ui()
        self._connect_signals()

    # ── UI construction ──────────────────────────────────────────────────────

    @staticmethod
    def _make_mic_icon(color: QColor, size: int = 64) -> QIcon:
        """Draw a classic studio microphone icon matching the reference silhouette."""
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(color, size * 0.06, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx = size / 2
        # Mic capsule (rounded rectangle / ellipse at top)
        cap_w = size * 0.38
        cap_h = size * 0.42
        cap_y = size * 0.06
        capsule = QRectF(cx - cap_w / 2, cap_y, cap_w, cap_h)
        p.setBrush(QBrush(color))
        p.drawRoundedRect(capsule, cap_w / 2, cap_w / 2)

        # U-shaped cradle around the capsule
        p.setBrush(Qt.BrushStyle.NoBrush)
        cradle_w = size * 0.56
        cradle_h = size * 0.38
        cradle_y = cap_y + cap_h * 0.25
        cradle_rect = QRectF(cx - cradle_w / 2, cradle_y, cradle_w, cradle_h)
        p.drawArc(cradle_rect, 0, -180 * 16)  # bottom half of ellipse

        # Vertical stand line
        stand_top = cradle_y + cradle_h / 2
        stand_bot = size * 0.82
        p.drawLine(int(cx), int(stand_top), int(cx), int(stand_bot))

        # Horizontal base
        base_w = size * 0.36
        p.drawLine(int(cx - base_w / 2), int(stand_bot), int(cx + base_w / 2), int(stand_bot))

        p.end()
        return QIcon(pix)

    def _setup_ui(self):
        from core.verification_card import VerificationCard
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(16)

        title = QLabel("[ ◡ ]  S.I.V.A - Identity Verification")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 16))
        title.setStyleSheet(f"color: #1F1F1F; background: transparent;")
        layout.addWidget(title)

        # Camera preview.
        #
        # Flexible, not setFixedSize(800, 600). Fixed at 600 tall it could not
        # shrink, and the auth window is only 740 tall — so once the title and
        # everything below had taken their share there was not enough room
        # left, and Qt simply laid the next widget on top of the preview
        # instead of shrinking it. That is what used to cover the camera.
        #
        # Given the remaining space instead, it always fits, at any window
        # size, and it absorbs the height of the controls below it; the card
        # tracks it via _recentre_card().
        self._camera_placeholder = QWidget()
        # The preview is the one compressible thing in the column, so it has to
        # be able to give — at the largest text setting the controls below need
        # every pixel. A floor of 240 was too tall to honour there, and the
        # layout answered by stacking the scan status back on top of the
        # preview: the exact bug that moving the status out of the frame was
        # meant to end.
        #
        # _ts_ignore_geom keeps the text-scale engine off that floor. It scales
        # minimum heights so grown text does not clip its box, but there is no
        # text in here — only a camera picture — and scaling it turned the
        # 240 floor into 326 at the largest step, which no 740px window could
        # give it.
        self._camera_placeholder.setProperty("_ts_ignore_geom", True)
        self._camera_placeholder.setMinimumSize(320, 140)
        self._camera_placeholder.setSizePolicy(QSizePolicy.Policy.Expanding,
                                               QSizePolicy.Policy.Expanding)
        layout.addWidget(self._camera_placeholder, stretch=1)

        self._camera = VerificationCard(self)
        self._camera.raise_()
        self._recentre_card()

        # ── Scan status — under the preview, never across the face ───────────
        #
        # This used to be painted inside the camera card, as a panel floating
        # over the bottom of the frame. That is exactly where a face sits, so
        # "Face matched — blink 2 more" landed across the mouth and chin of
        # the person it was talking about, and hid the very thing the user was
        # trying to line up.
        #
        # It is a real widget in the column now. Nothing overlaps, and the
        # text is free to be as large as it needs to be.
        self._scan_status = QFrame()
        self._scan_status.setObjectName("scanStatus")
        self._scan_status.setFixedHeight(68)
        self._scan_status.setMinimumWidth(320)
        scan_layout = QVBoxLayout(self._scan_status)
        scan_layout.setContentsMargins(28, 6, 28, 6)
        scan_layout.setSpacing(2)

        self._scan_line1 = QLabel("")
        self._scan_line1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scan_line1.setFont(QFont("Segoe UI", 15, QFont.Weight.DemiBold))
        scan_layout.addWidget(self._scan_line1)

        self._scan_line2 = QLabel("")
        self._scan_line2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scan_line2.setFont(QFont("Segoe UI", 12))
        self._scan_line2.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;")
        scan_layout.addWidget(self._scan_line2)

        # Centred pill rather than a bar across the full width — the shape the
        # message had when it was painted on the frame, minus the covering-up.
        scan_row = QHBoxLayout()
        scan_row.addStretch(1)
        scan_row.addWidget(self._scan_status)
        scan_row.addStretch(1)
        layout.addLayout(scan_row)
        self._set_scan_status("", _qcol(C.PRI))

        # ── Password panel — sits directly under the preview ─────────────────
        #
        # Password, voice and both buttons are on screen from the start, right
        # below the camera, so the user never has to wait for the face scan to
        # fail before they can reach for another way in.
        self._pw_panel = QWidget()
        self._pw_panel.setStyleSheet("background: transparent;")
        pw_layout = QVBoxLayout(self._pw_panel)
        pw_layout.setContentsMargins(8, 2, 8, 2)
        # Tight, because the text inside it is no longer tiny. The gaps are
        # what pay for the larger type at the biggest text-scale steps, where
        # the whole column has to fit in the same 740px window.
        pw_layout.setSpacing(4)

        # Calm heading while the scan is still running; _show_password_panel()
        # swaps in the warning if the face check actually fails.
        # Segoe UI, not Courier New. Everything on this panel used to be set
        # in a narrow monospace at 7–10pt — the smallest, hardest-to-read text
        # on a screen whose whole job is telling someone how to get in.
        self._pw_title = QLabel(self._PW_TITLE_IDLE)
        self._pw_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pw_title.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self._pw_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        pw_layout.addWidget(self._pw_title)

        pw_row = QHBoxLayout()
        pw_row.setSpacing(4)

        self._pw_input = QLineEdit()
        self._pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw_input.setPlaceholderText("Type your password, or press the mic")
        self._pw_input.setFont(QFont("Segoe UI", 12))
        self._pw_input.setFixedHeight(44)
        self._pw_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.6); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._pw_input.returnPressed.connect(self._on_pw_submit)
        pw_row.addWidget(self._pw_input)

        self._mic_btn = AnimatedButton()
        self._mic_btn.setFixedSize(44, 44)
        self._mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_btn.setToolTip("Press to say your password")
        self._mic_btn.setIcon(self._make_mic_icon(_qcol(C.PRI)))
        self._mic_btn.setIconSize(QSize(26, 26))
        self._mic_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.6);
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        self._mic_btn.clicked.connect(self._on_mic_click)
        pw_row.addWidget(self._mic_btn)

        pw_layout.addLayout(pw_row)

        # Says what to do, not how it works. It used to lead with "Voice uses
        # offline Whisper" — the name of the speech engine, which tells the
        # person signing in nothing they can act on.
        mic_hint = QLabel("Press the microphone icon to say your password")
        mic_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mic_hint.setFont(QFont("Segoe UI", 11))
        mic_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        pw_layout.addWidget(mic_hint)

        self._pw_submit = AnimatedButton("▸  AUTHENTICATE")
        self._pw_submit.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self._pw_submit.setFixedHeight(44)
        self._pw_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pw_submit.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        
        self._pw_submit.clicked.connect(self._on_pw_submit)
        btn_row.addWidget(self._pw_submit)
        
        self._pw_retry = AnimatedButton("↻  RETRY FACE SCAN")
        self._pw_retry.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self._pw_retry.setFixedHeight(44)
        self._pw_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pw_retry.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ACC2};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.ACC2};
            }}
        """)
        self._pw_retry.clicked.connect(self._on_retry_face_scan)
        btn_row.addWidget(self._pw_retry)
        
        pw_layout.addLayout(btn_row)

        self._pw_status = QLabel("")
        self._pw_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pw_status.setFont(QFont("Segoe UI", 11))
        self._pw_status.setStyleSheet(f"color: {C.RED}; background: transparent;")
        pw_layout.addWidget(self._pw_status)

        # Always available, not only once the lockouts are exhausted — someone
        # who knows they have forgotten it should not have to fail ten times to
        # be offered the way out.
        self._forgot_btn = AnimatedButton("Forgot your password?")
        self._forgot_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self._forgot_btn.setFixedHeight(32)
        self._forgot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._forgot_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ACC2};
                border: none; text-decoration: underline;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        self._forgot_btn.clicked.connect(self.recovery_needed.emit)
        pw_layout.addWidget(self._forgot_btn)

        cfg = {}
        if self._config_path.exists():
            with open(self._config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        
        # On screen from the start, but only once a password exists to type —
        # on first run there is nothing to authenticate against yet, and the
        # setup panel below takes over after enrollment.
        #
        # The heading is what used to make this unusable up front: it read
        # "FACE NOT RECOGNISED — ENTER PASSWORD" before the camera had looked
        # at anyone. It now starts calm and only turns into that warning when
        # the scan really fails.
        self._has_password = "app_password_hash" in cfg

        # Directly under the preview, ahead of the separator and the
        # enrollment prompt, so the controls are the first thing below the
        # camera rather than the last thing on the screen.
        layout.addWidget(self._pw_panel)
        # Visibility set AFTER the layout takes it: addWidget reparents the
        # widget, and Qt drops the explicit-hide flag on a reparent, so
        # anything hidden beforehand comes back the moment the screen shows.
        self._pw_panel.setVisible(self._has_password)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep2)

        # Enrollment prompt. Empty on every ordinary sign-in, so it is hidden
        # rather than held open — the strip it was reserving belongs to the
        # preview, which is the one thing on this screen that can use it.
        self._enroll_label = QLabel("")
        self._enroll_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._enroll_label.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        self._enroll_label.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self._enroll_label.setFixedHeight(24)
        layout.addWidget(self._enroll_label)
        self._enroll_label.setVisible(False)

        # ── First-run password setup panel ───────────────────────────────────
        self._setup_panel = QWidget()
        self._setup_panel.setStyleSheet("background: transparent;")
        setup_layout = QVBoxLayout(self._setup_panel)
        setup_layout.setContentsMargins(8, 4, 8, 4)
        setup_layout.setSpacing(6)

        setup_title = QLabel("◈  SET FALLBACK PASSWORD")
        setup_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        setup_title.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        setup_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        setup_layout.addWidget(setup_title)

        self._setup_pw = QLineEdit()
        self._setup_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._setup_pw.setPlaceholderText("Choose a password")
        self._setup_pw.setFont(QFont("Courier New", 10))
        self._setup_pw.setFixedHeight(34)
        self._setup_pw.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.6); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        setup_layout.addWidget(self._setup_pw)

        self._setup_pw2 = QLineEdit()
        self._setup_pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self._setup_pw2.setPlaceholderText("Confirm password")
        self._setup_pw2.setFont(QFont("Courier New", 10))
        self._setup_pw2.setFixedHeight(34)
        self._setup_pw2.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,0.6); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        setup_layout.addWidget(self._setup_pw2)

        self._setup_status = QLabel("")
        self._setup_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._setup_status.setFont(QFont("Courier New", 8))
        self._setup_status.setStyleSheet(f"color: {C.RED}; background: transparent;")
        setup_layout.addWidget(self._setup_status)

        setup_btn = AnimatedButton("▸  SAVE & COMPLETE SETUP")
        setup_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        setup_btn.setFixedHeight(34)
        setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        setup_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.GREEN};
                border: 1px solid {C.GREEN}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: rgba(0,255,136,0.08); border: 1px solid {C.GREEN};
            }}
        """)
        setup_btn.clicked.connect(self._on_setup_submit)
        setup_layout.addWidget(setup_btn)

        layout.addWidget(self._setup_panel)
        # After the addWidget, for the same reason as the password panel:
        # hidden first, this first-run "SET FALLBACK PASSWORD" form reappeared
        # on every sign-in.
        self._setup_panel.setVisible(False)

        # Stretch factor 0, so spare height goes to the preview instead of
        # being split evenly with it. At the default factor of 1 this spacer
        # competed with the placeholder as an equal, and half of every spare
        # pixel went to empty background below the sign-in controls while the
        # camera view stayed small.
        layout.addStretch(0)

    # ── signal wiring ────────────────────────────────────────────────────────

    def _connect_signals(self):
        self._frame_sig.connect(self._on_frame)
        self._status_sig.connect(self._on_status)
        self._blink_sig.connect(self._on_blink)
        self._done_sig.connect(self._on_done)
        self._enroll_prompt_sig.connect(self._on_enroll_prompt)
        self._voice_pw_sig.connect(self._on_voice_pw_result)

    def _on_frame(self, pixmap, face_rect, score):
        # Runs on the GUI thread once per captured frame — keep it light. A
        # print() here (flushed, to a Windows console) cost more than painting
        # the frame did.
        self._camera.set_frame(pixmap, face_rect, score)
        # Arithmetic and a rect comparison; it only touches geometry if the
        # camera turns out to send a shape other than the 4:3 assumed before
        # the first frame arrived.
        self._recentre_card()

    def _on_status(self, text, color):
        self._set_scan_status(text, color)

    def _set_scan_status(self, text: str, color: QColor):
        """
        Show a scan message in the panel below the preview.

        The panel holds its height whether or not it has anything to say. The
        scan writes to it several times a second, and a widget that came and
        went at that rate would reflow the column — shoving the preview, and
        the hand-placed card with it, on every frame.

        The stylesheet is only rewritten when something about it actually
        changes: setStyleSheet() is not cheap, and the text-scale engine
        re-scales on every StyleChange it sees.
        """
        parts = text.split("—")
        line1 = parts[0].strip()
        line2 = parts[1].strip() if len(parts) > 1 else ""

        self._scan_line1.setText(line1)
        self._scan_line2.setText(line2)
        self._scan_line2.setVisible(bool(line2))

        col = color.name() if isinstance(color, QColor) else str(color)
        if col != getattr(self, "_scan_colour", None):
            self._scan_colour = col
            self._scan_line1.setStyleSheet(
                f"color: {col}; background: transparent;")

        filled = bool(line1)
        if filled != getattr(self, "_scan_filled", None):
            self._scan_filled = filled
            self._scan_status.setStyleSheet(f"""
                QFrame#scanStatus {{
                    background: {C.WHITE if filled else 'transparent'};
                    border: 1px solid {C.BORDER if filled else 'transparent'};
                    border-radius: 14px;
                }}
                QLabel {{ background: transparent; }}
            """)

    def _on_blink(self, count):
        self._camera.set_blinks(count)

    def _on_enroll_prompt(self, text):
        self._enroll_label.setText(text)
        if self._enroll_label.isVisible() != bool(text):
            self._enroll_label.setVisible(bool(text))
            # Showing or hiding it reflows the column under the preview, and
            # the card is placed by hand — re-sync it after the reflow, not
            # before.
            QTimer.singleShot(0, self._recentre_card)

    def _on_voice_pw_result(self, text):
        self._voice_recording = False
        self._reset_mic_style()
        if text:
            self._pw_input.setText(text)
            self._pw_status.setText(f"🎤 Heard: '{text}' — authenticating…")
            self._pw_status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
            # Wait briefly so the user sees what was typed, then submit
            QTimer.singleShot(600, lambda: self._try_password(text))
        else:
            self._pw_status.setText("Could not understand. Try again.")
            self._pw_input.setEnabled(True)
            self._pw_submit.setEnabled(True)
    def _on_retry_face_scan(self):
        self._set_scan_status("", _qcol(C.PRI))
        # The panel stays put — only the warning heading goes back to calm.
        self._pw_title.setText(self._PW_TITLE_IDLE)
        self._pw_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._start_camera()

    # Wrong passwords before a face scan counts as a rescue rather than a
    # normal sign-in. One mistyped character is a slip; three means they have
    # genuinely lost the password and need to set one they will remember.
    _RESCUE_THRESHOLD = 3

    def _on_done(self, success):
        self._authenticated = success
        if success:
            # The face got them in, but the password is still forgotten — and
            # a face alone is not something they can type in next time.
            if self._password_failures >= self._RESCUE_THRESHOLD:
                self.needs_password_reset = True
            _clear_lockout()
            self._confirm_success()
        else:
            self._show_password_panel()

    def _confirm_success(self):
        """The one hand-off out of this screen: chime, green tick, then go."""
        if self._chime is not None:
            try:
                self._chime.play()
            except Exception:
                pass
        self._camera.confirm(self._emit_auth_success)

    # ── password panel ───────────────────────────────────────────────────────

    def _show_password_panel(self):
        self._pw_title.setText(self._PW_TITLE_FAIL)
        self._pw_title.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        self._pw_panel.setVisible(True)
        self._pw_input.setFocus()
        self._update_lockout_ui()
        # If it was hidden, revealing it reflows the column and moves the
        # preview placeholder; singleShot(0) so the card is re-synced after
        # that reflow, not before.
        QTimer.singleShot(0, self._recentre_card)

    def _update_lockout_ui(self):
        now = time.time()
        if recovery_required(self._lockout_rounds):
            # Both cooldowns have been served. Nothing here re-opens the
            # password box — only a valid emailed code does.
            self._enter_recovery_required()
            return
        if self._lockout_until > now:
            remaining = int(self._lockout_until - now)
            mins, secs = divmod(remaining, 60)
            self._pw_status.setText(f"🔒 LOCKED OUT — {mins}:{secs:02d} remaining")
            self._pw_status.setStyleSheet(f"color: {C.RED}; background: transparent;")
            self._pw_submit.setEnabled(False)
            self._mic_btn.setEnabled(False)
            self._pw_input.setEnabled(False)
            QTimer.singleShot(1000, self._update_lockout_ui)
        else:
            self._pw_submit.setEnabled(True)
            self._mic_btn.setEnabled(True)
            self._pw_input.setEnabled(True)
            if self._failed_attempts > 0:
                remain = (_attempts_allowed(self._lockout_rounds)
                          - self._failed_attempts)
                self._pw_status.setText(f"⚠ {remain} attempt(s) remaining")
                self._pw_status.setStyleSheet(f"color: {C.ACC}; background: transparent;")
            else:
                self._pw_status.setText("")

    def _on_pw_submit(self):
        password = self._pw_input.text().strip()
        if not password:
            return
        self._try_password(password)

    def _on_mic_click(self):
        if self._voice_recording:
            return
        self._voice_recording = True
        self._mic_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.RED}; color: white;
                border: 1px solid {C.RED}; border-radius: 3px;
            }}
        """)
        self._pw_status.setText("🎤 Listening… speak your password (5 sec)")
        self._pw_status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self._pw_input.setEnabled(False)
        self._pw_submit.setEnabled(False)

        def _record():
            text = VoicePassword.listen(duration=5.0)
            self._voice_pw_sig.emit(text)

        threading.Thread(target=_record, daemon=True).start()

    def _reset_mic_style(self):
        self._mic_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.6); color: {C.PRI};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        self._pw_input.setEnabled(True)
        self._pw_submit.setEnabled(True)

    def _try_password(self, password: str):
        now = time.time()
        if self._lockout_until > now:
            self._update_lockout_ui()
            return

        cfg = self._read_config()
        stored_hash = cfg.get("app_password_hash", "")
        stored_salt = cfg.get("app_password_salt", "")

        if _verify_password(password, stored_hash, stored_salt):
            self._authenticated = True
            _clear_lockout()
            self._pw_status.setText("✓ ACCESS GRANTED")
            self._pw_status.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
            self._confirm_success()
        else:
            self._failed_attempts += 1
            self._password_failures += 1
            if self._failed_attempts >= _attempts_allowed(self._lockout_rounds):
                # A round is over. The first costs 50 seconds; the second is
                # only two tries long and ends the guessing outright — from
                # there a mailed code is the only way in.
                self._lockout_rounds += 1
                self._failed_attempts = 0
                if recovery_required(self._lockout_rounds):
                    self._lockout_until = 0.0
                    _write_lockout(0, 0.0, self._lockout_rounds)
                    self._enter_recovery_required()
                    return
                self._lockout_until = now + _lockout_duration(self._lockout_rounds)
                _write_lockout(0, self._lockout_until, self._lockout_rounds)
            else:
                _write_lockout(self._failed_attempts, 0.0, self._lockout_rounds)
            self._pw_input.clear()
            self._update_lockout_ui()
            if self._lockout_until <= now:
                self._pw_status.setText("✗ ACCESS DENIED — wrong password")
                self._pw_status.setStyleSheet(f"color: {C.RED}; background: transparent;")

    def _enter_recovery_required(self):
        """
        Guessing is over — the only way in now is a code sent by email.

        The password box is disabled rather than hidden, so it is obvious that
        this is the same screen and the same account, not an error.
        """
        self._pw_input.clear()
        self._pw_input.setEnabled(False)
        self._pw_submit.setEnabled(False)
        self._mic_btn.setEnabled(False)
        self._pw_title.setText("✉  TOO MANY TRIES — USE EMAIL TO GET BACK IN")
        self._pw_title.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        self._pw_status.setText(
            "Press “Email me a code” and we will send one to your email.")
        self._pw_status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        self.recovery_needed.emit()

    # ── first-run setup ──────────────────────────────────────────────────────

    def _show_setup_panel(self):
        self._setup_panel.setVisible(True)
        self._setup_pw.setFocus()
        # Same reflow concern as _show_password_panel.
        QTimer.singleShot(0, self._recentre_card)

    def _on_setup_submit(self):
        pw1 = self._setup_pw.text().strip()
        pw2 = self._setup_pw2.text().strip()
        if not pw1:
            self._setup_status.setText("Password cannot be empty")
            return
        if len(pw1) < 4:
            self._setup_status.setText("Password must be at least 4 characters")
            return
        if pw1 != pw2:
            self._setup_status.setText("Passwords do not match")
            return

        h, s = _hash_password(pw1)
        cfg = self._read_config()
        cfg["app_password_hash"] = h
        cfg["app_password_salt"] = s
        self._write_config(cfg)

        self._authenticated = True
        _clear_lockout()
        self._confirm_success()

    # ── config I/O ───────────────────────────────────────────────────────────

    def _read_config(self) -> dict:
        with open(self._config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_config(self, cfg: dict):
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)

    # ── camera thread ────────────────────────────────────────────────────────

    def _start_camera(self):
        self._camera_active = True
        # The expand is deferred by one event-loop turn on purpose.
        #
        # run() calls this BEFORE exec(), so at this instant the dialog has
        # never been laid out and the placeholder still reports y=0 instead of
        # its real y=60. Expanding into that rect put the camera preview 60px
        # too high, straight over the title — which is what made the screen
        # look like things were stacked on top of each other. One turn later
        # exec() has shown the dialog and the geometry is real.
        QTimer.singleShot(0, self._expand_camera_card)
        threading.Thread(target=self._camera_loop, daemon=True).start()

    def _expand_camera_card(self):
        if not self._camera_active:
            return
        try:
            self._camera.expand(self._preview_rect())
        except RuntimeError:
            pass

    def _stop_camera(self):
        self._camera_active = False

    def _frame_to_pixmap(self, frame: np.ndarray) -> QPixmap:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)

    def _camera_loop(self):
        """Background thread: instant capture → background model init → verify."""
        # Open camera IMMEDIATELY using CAP_ANY to skip DirectShow 2-3s delay on Windows
        cap = cv2.VideoCapture(0, cv2.CAP_ANY)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        
        if not cap.isOpened():
            self._status_sig.emit("CAMERA UNAVAILABLE", _qcol(C.RED))
            self._done_sig.emit(False)
            return

        self._status_sig.emit("Warming up security engines...", _qcol(C.ACC2))
        
        # Start loading deep-learning models in a sub-thread
        models_loaded = threading.Event()
        
        def _load_models():
            def _model_status(msg):
                if "download" in msg.lower() or "error" in msg.lower():
                    self._status_sig.emit(msg, _qcol(C.ACC2))
            self._face_auth = FaceAuth(self._config_dir, status_callback=_model_status)
            models_loaded.set()
            
        threading.Thread(target=_load_models, daemon=True).start()

        # Stream instantly to UI while models load into RAM and auto-exposure warms up
        while not models_loaded.is_set():
            ret, frame = cap.read()
            if not ret or not self._camera_active:
                models_loaded.wait(1.0) # wait briefly before breaking
                break
            frame = cv2.flip(frame, 1)
            pixmap = self._frame_to_pixmap(frame)
            self._frame_sig.emit(pixmap, None, 0.0)
            time.sleep(0.01)
            
        if not self._camera_active:
            cap.release()
            return
            
        if self._face_auth is None or not self._face_auth.models_ready:
            self._status_sig.emit("Models unavailable — use password", _qcol(C.ACC))
            self._done_sig.emit(False)
            cap.release()
            return

        # Proceed directly to verification/enrollment (warmup is already done implicitly)
        try:
            if not self._face_auth.has_model:
                self._run_enrollment(cap)
            else:
                self._run_verification(cap)
        finally:
            cap.release()

    # ── enrollment flow ──────────────────────────────────────────────────────

    def _run_enrollment(self, cap):
        self._status_sig.emit("ENROLLMENT — Look at camera", _qcol(C.ACC2))
        self._enroll_prompt_sig.emit("▸ Look STRAIGHT at the camera & move slightly")
        all_frames: list[np.ndarray] = []
        captured = 0
        attempts = 0
        max_attempts = _ENROLL_FRAMES * 10

        while captured < _ENROLL_FRAMES and attempts < max_attempts and self._camera_active:
            ret, frame = cap.read()
            if not ret:
                attempts += 1
                continue
            attempts += 1

            frame = cv2.flip(frame, 1)
            pixmap = self._frame_to_pixmap(frame)

            faces, idx = self._face_auth.detect(frame)
            if faces is not None:
                face_rect = self._face_auth.get_face_rect(faces, idx)
                all_frames.append(frame.copy())
                captured += 1
                self._frame_sig.emit(pixmap, face_rect, 0.0)
                self._status_sig.emit(
                    f"CAPTURING… {captured}/{_ENROLL_FRAMES}", _qcol(C.ACC2))
            else:
                self._frame_sig.emit(pixmap, None, 0.0)
                self._status_sig.emit("Position face in frame", _qcol(C.ACC))

            time.sleep(0.08)

        self._enroll_prompt_sig.emit("")

        if len(all_frames) < 10:
            self._status_sig.emit("ENROLLMENT FAILED — not enough frames", _qcol(C.RED))
            self._done_sig.emit(False)
            return

        self._status_sig.emit("TRAINING MODEL…", _qcol(C.ACC2))
        success = self._face_auth.enroll(all_frames)

        if success:
            self._status_sig.emit("ENROLLMENT COMPLETE ✓", _qcol(C.GREEN))
            time.sleep(0.8)
            # Clean up old LBPH model if it exists
            old_model = self._config_dir / "face_model.yml"
            if old_model.exists():
                try:
                    old_model.unlink()
                except Exception:
                    pass
            # A returning user re-recording their face from Settings already
            # has a password; asking them to invent another one would be
            # nonsense. They go straight into the welcome cinematic and the
            # reveal, the same way an ordinary sign-in does.
            if self._has_password:
                QTimer.singleShot(0, self._confirm_success)
            else:
                QTimer.singleShot(0, self._show_setup_panel)
        else:
            self._status_sig.emit("ENROLLMENT FAILED", _qcol(C.RED))
            self._done_sig.emit(False)

    # ── verification flow ────────────────────────────────────────────────────

    def _run_verification(self, cap):
        self._status_sig.emit("SCANNING…", _qcol(C.PRI))

        start_time    = time.time()
        blink_count   = 0
        eyes_open     = True
        match_frames  = 0
        total_needed  = 3   # consecutive matching frames required
        prev_center   = None

        while self._camera_active:
            ret, frame = cap.read()
            if not ret:
                continue

            frame = cv2.flip(frame, 1)
            pixmap = self._frame_to_pixmap(frame)

            matched, score, face_rect = self._face_auth.verify(frame)

            if face_rect is not None:
                self._frame_sig.emit(pixmap, face_rect, score)

                # Anti-sway stability check
                cx = face_rect[0] + face_rect[2] // 2
                cy = face_rect[1] + face_rect[3] // 2
                
                is_stable = False
                if prev_center:
                    dist = abs(cx - prev_center[0]) + abs(cy - prev_center[1])
                    if dist < 15:  # Face hasn't moved significantly
                        is_stable = True
                else:
                    is_stable = True
                    
                prev_center = (cx, cy)

                # Blink detection (only when face is completely stable)
                if is_stable:
                    n_eyes = self._face_auth.check_eyes(frame, face_rect)
                    if n_eyes >= 2:
                        if not eyes_open:
                            blink_count += 1
                            self._blink_sig.emit(blink_count)
                        eyes_open = True
                    elif n_eyes == 0:
                        eyes_open = False
                else:
                    # Face is moving — ignore eye state to prevent motion blur false-positives
                    eyes_open = True

                if matched:
                    match_frames += 1
                    if match_frames >= total_needed and blink_count >= _MIN_BLINKS:
                        self._status_sig.emit("RECOGNISED ✓", _qcol(C.GREEN))
                        time.sleep(1.0)
                        self._done_sig.emit(True)
                        return
                    elif match_frames >= total_needed:
                        self._status_sig.emit(
                            f"Face matched — blink {_MIN_BLINKS - blink_count} more",
                            _qcol(C.ACC2))
                    else:
                        self._status_sig.emit(
                            f"VERIFYING… ({score:.2f})", _qcol(C.PRI))
                else:
                    match_frames = 0
                    if score > 0.2:
                        self._status_sig.emit(
                            f"LOW MATCH ({score:.2f}) — not you?", _qcol(C.ACC))
                    else:
                        self._status_sig.emit(
                            f"UNKNOWN FACE ({score:.2f})", _qcol(C.RED))
            else:
                self._frame_sig.emit(pixmap, None, 0.0)
                match_frames = 0
                self._status_sig.emit("NO FACE DETECTED", _qcol(C.ACC))

            # Timeout
            elapsed = time.time() - start_time
            if elapsed > _FACE_TIMEOUT:
                self._status_sig.emit("TIMEOUT — use password", _qcol(C.ACC))
                self._done_sig.emit(False)
                return

            time.sleep(0.05)

    # ── public API ───────────────────────────────────────────────────────────

    def start(self):
        """Begin authentication (non-blocking)."""
        if self._mode == prefs.AUTH_PASSWORD:
            self._begin_password_only()
        else:
            self._start_camera()

    def _begin_password_only(self):
        """
        Password-only sign-in: no camera is opened at all.

        The preview and its card are taken out of the column rather than left
        empty, so the password panel is what the screen is actually about. If
        there is no password on file yet there is nothing to check against, so
        the first-run panel that creates one is shown instead.
        """
        self._camera_placeholder.hide()
        self._camera.hide()
        self._scan_status.hide()   # nothing is scanning — do not hold its space

        if not self._has_password:
            self._pw_panel.setVisible(False)
            self._show_setup_panel()
            return

        self._pw_title.setText(self._PW_TITLE_ONLY)
        self._pw_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._pw_retry.hide()          # nothing to retry — there is no scan
        self._pw_panel.setVisible(True)
        self._pw_input.setFocus()
        self._update_lockout_ui()

    def stop_animations(self):
        """Stop all child animation timers to avoid QPainter conflicts
        when a parent QGraphicsOpacityEffect is installed."""
        try:
            self._face_persona._tmr.stop()
        except Exception:
            pass
        try:
            self._camera._tmr.stop()
        except Exception:
            pass

    def _emit_auth_success(self):
        """Called after the confirm animation finishes."""
        self._stop_camera()
        self.stop_animations()
        self.auth_completed.emit()

    # The camera is opened at 640×480, so 4:3 until a frame proves otherwise.
    _PREVIEW_ASPECT = 4 / 3

    def _preview_rect(self) -> QRect:
        """
        The largest rect with the camera's own shape that fits the preview
        area, centred in it.

        The card used to take the whole preview area and scale the frame with
        KeepAspectRatioByExpanding — so a 4:3 picture in an 800×300 letterbox
        was blown up until it covered the box and everything past the edges was
        thrown away. Roughly half the picture, top and bottom, never made it to
        the screen: the top of the head and the chin were simply outside the
        card.

        Shaping the card to the camera instead means the whole frame is on
        screen, and the white card ends exactly where the picture does rather
        than being padded out with letterbox bars.
        """
        area = self._camera_placeholder.geometry()
        aspect = self._PREVIEW_ASPECT
        pm = getattr(self._camera, "_pixmap", None)
        if pm is not None and not pm.isNull() and pm.height() > 0:
            aspect = pm.width() / pm.height()

        w = area.width()
        h = int(round(w / aspect))
        if h > area.height():
            h = area.height()
            w = int(round(h * aspect))
        return QRect(area.x() + (area.width() - w) // 2,
                     area.y() + (area.height() - h) // 2,
                     w, h)

    def _recentre_card(self):
        """
        The verification card is placed by hand, not by the layout, so it has
        to be kept in step with the layout by hand too.

        Collapsed, it sits as a small pill centred over the top of the preview
        area — previously it was placed at a fixed y=20, which put it on top of
        the title.

        Expanded, it is locked to the preview area, at the camera's own aspect
        ratio. That tracking is the part that was missing: the card was
        positioned once, at expand time, and then never again. Showing or
        hiding a panel below it reflows the layout and moves the placeholder,
        but the card stayed where it was — stranded on top of its neighbours.
        This is what made the screen look like things were sitting on each
        other.

        Skipped entirely while an animation owns the geometry, so it never
        fights the expand or the hand-off.
        """
        card = getattr(self, "_camera", None)
        ph = getattr(self, "_camera_placeholder", None)
        if card is None or ph is None:
            return
        try:
            state = getattr(card, "_state", "IDLE")
            if state in ("EXPANDING", "CONFIRM"):
                return                       # an animation owns it right now

            if state == "SCAN":
                target = self._preview_rect()
                if card.geometry() != target:
                    card.setGeometry(target)
                return

            # Collapsed: a small pill at the top of the preview area, which is
            # the whole placeholder — the camera's shape only matters once
            # there is a picture in it.
            area = ph.geometry()
            from core import text_scale
            f = text_scale.factor()
            w, h = int(120 * f), int(36 * f)
            card.setGeometry(area.x() + (area.width() - w) // 2,
                             area.y() + int(12 * f), w, h)
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recentre_card()

    def cleanup(self):
        """Stop camera and release resources."""
        self._camera_active = False
        self._stop_camera()
        # The card's 16 ms repaint timer is started by expand() and otherwise
        # never stops — it would still be ticking on the GUI thread through the
        # welcome cinematic, the reveal, and the rest of the session.
        try:
            self._camera._tmr.stop()
        except (AttributeError, RuntimeError):
            pass
