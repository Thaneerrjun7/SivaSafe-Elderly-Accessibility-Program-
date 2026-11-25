"""
Build the looping alarm tone.

The alarm has to carry across a room and keep going until it is answered, but
it is going to be heard by someone who may be startled by it, so it is a pair
of soft-edged sine notes rather than a buzzer. The clip is exactly two seconds
and starts and ends in silence, so winsound's SND_LOOP repeats it seamlessly.

Run from the project root:  python scripts/generate_alarm_sound.py
"""

import math
import os
import struct
import wave

SAMPLE_RATE = 44100
LOOP_SEC = 2.0

# Two notes, a rising fourth — attention-getting without being harsh.
NOTE_HZ = (784.0, 1046.5)       # G5, C6
NOTE_SEC = 0.20
GAP_SEC = 0.12                  # between the two notes of a pair
PAIRS = 2                       # two double-beeps, then a rest
PAIR_GAP_SEC = 0.34             # between one double-beep and the next
VOLUME = 0.55
EDGE_SEC = 0.012                # fade in/out on every note — kills the click


def _note(freq: float, dur_sec: float) -> list[float]:
    n = int(SAMPLE_RATE * dur_sec)
    edge = max(1, int(SAMPLE_RATE * EDGE_SEC))
    out = []
    for i in range(n):
        # Fade both ends so no note begins or ends on a discontinuity.
        if i < edge:
            env = i / edge
        elif i > n - edge:
            env = (n - i) / edge
        else:
            env = 1.0
        # A touch of the octave above gives it some bite at low volume.
        val = (math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
               + 0.18 * math.sin(4 * math.pi * freq * i / SAMPLE_RATE)) / 1.18
        out.append(val * env * VOLUME)
    return out


def _silence(dur_sec: float) -> list[float]:
    return [0.0] * int(SAMPLE_RATE * dur_sec)


def generate_alarm(output_path: str):
    samples: list[float] = []
    for pair in range(PAIRS):
        samples += _note(NOTE_HZ[0], NOTE_SEC)
        samples += _silence(GAP_SEC)
        samples += _note(NOTE_HZ[1], NOTE_SEC)
        if pair < PAIRS - 1:
            samples += _silence(PAIR_GAP_SEC)

    # Pad out to exactly LOOP_SEC so the rest before the next repeat is even.
    total = int(SAMPLE_RATE * LOOP_SEC)
    if len(samples) < total:
        samples += _silence((total - len(samples)) / SAMPLE_RATE)
    samples = samples[:total]

    frames = bytearray()
    for v in samples:
        frames.extend(struct.pack("<h", max(-32768, min(32767, int(v * 32767.0)))))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with wave.open(output_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames)


if __name__ == "__main__":
    generate_alarm("assets/sounds/alarm.wav")
    print("Generated assets/sounds/alarm.wav")
