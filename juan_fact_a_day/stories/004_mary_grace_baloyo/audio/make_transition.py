"""Makes audio/transition.wav: a soft, warm scene transition (replaces the cartoony pop for this story).

    python audio/make_transition.py

Airy swish (filtered noise that swells and fades) + low warm tone + faint bell shimmer. Made from scratch, royalty-free.
"""
import wave
from pathlib import Path

import numpy as np

SR = 48000
DUR = 1.1
t = np.arange(int(SR * DUR)) / SR
rng = np.random.default_rng(7)


def lowpass(x, cutoff):
    """One-pole low-pass, cutoff may change over time (array)."""
    a = np.exp(-2 * np.pi * np.broadcast_to(cutoff, x.shape) / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a[i]) * v + a[i] * acc
        y[i] = acc
    return y


# 1) airy swish: noise, brightening then darkening, swelling to a peak at ~0.28 s
swell = np.clip(t / 0.28, 0, 1) ** 2 * np.exp(-np.clip(t - 0.28, 0, None) * 7)
cut = 600 + 3200 * np.exp(-((t - 0.28) / 0.18) ** 2)
noise = rng.standard_normal(t.size)
swish = lowpass(lowpass(noise, cut), cut)
swish -= lowpass(swish, 180)  # take out the rumble
swish = swish / np.abs(swish).max() * swell

# 2) warm low tone (soft "body", no hard attack)
tone_env = np.clip((t - 0.2) / 0.08, 0, 1) * np.exp(-np.clip(t - 0.28, 0, None) * 4.5)
tone = (np.sin(2 * np.pi * 196 * t) + 0.35 * np.sin(2 * np.pi * 392 * t)) * tone_env

# 3) faint bell shimmer (G major-ish: 784 / 1175 / 1568 Hz), gentle attack
bell_env = np.clip((t - 0.24) / 0.03, 0, 1) * np.exp(-np.clip(t - 0.27, 0, None) * 5)
bell = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((784, 1.0), (1175, 0.55), (1568, 0.3))) * bell_env

mono = 0.55 * swish + 0.45 * tone + 0.16 * bell
mono *= np.minimum(1, np.minimum(t / 0.01, (DUR - t) / 0.08))  # no clicks at either end

# slight stereo width: the swish moves left -> right
pan = np.clip(t / 0.5, 0, 1)
left = mono * (1 - 0.3 * pan)
right = mono * (0.7 + 0.3 * pan)
st = np.stack([left, right], 1)
st = st / np.abs(st).max() * 10 ** (-3 / 20)  # peak -3 dBFS; scene volume sets the real loudness

out = Path(__file__).resolve().parent / "transition.wav"
with wave.open(str(out), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((st * 32767).astype("<i2").tobytes())
print("wrote", out)
