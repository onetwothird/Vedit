"""Generate the channel's sound effects (royalty-free, made from scratch) into assets/sfx/.

    python tools/make_sfx.py
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sfx"


def save(name, x):
    x = np.clip(x / (np.abs(x).max() + 1e-9) * 0.9, -1, 1)
    st = np.repeat((x * 32767).astype(np.int16)[:, None], 2, 1)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR), w.writeframes(st.tobytes())
    print("wrote", OUT / name)


def whoosh(d=0.42):
    n = int(SR * d)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n)
    # band-pass sweep: one-pole low-pass with a cutoff that rises then falls
    fc = 300 + 4200 * np.sin(np.pi * t / d) ** 2
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.zeros(n)
    lp = 0.0
    for i in range(n):
        lp = (1 - a[i]) * noise[i] + a[i] * lp
        y[i] = lp
    y -= np.convolve(y, np.ones(40) / 40, "same")  # remove rumble
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 1.6
    return y * env


def ding(d=0.9):
    t = np.arange(int(SR * d)) / SR
    f = 1318.5  # E6
    y = sum(a * np.sin(2 * np.pi * f * m * t) * np.exp(-t * k) for a, m, k in [(1, 1, 5), (.45, 2.01, 8), (.2, 3.0, 12)])
    return y * np.minimum(1, t / 0.004)


def pop(d=0.12):
    t = np.arange(int(SR * d)) / SR
    f = 900 * np.exp(-t * 25) + 250
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 35) * np.minimum(1, t / 0.002)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    save("whoosh.wav", whoosh())
    save("ding.wav", ding())
    save("pop.wav", pop())
