"""Makes audio/score.wav: an original cinematic score for the Mary Grace Baloyo story (made from scratch, royalty-free).

    python audio/make_score.py

80 BPM, one bar = 3 s. Sections follow the narration (times from the .srt):
  0-15   hook        tense B minor pad, heartbeat pulse, sparse high piano
  15-51  her life    hopeful D major piano arpeggios; crisp hats + soft kick from ~30 s ("At alam mo rin ba?")
  51-66  the flight  tension: tremolo strings, pulse on every beat, riser into the eject
  66-81  eject/crash almost silent: soft pad, single sad piano notes
  81-108 legacy      emotional swell; peak at 96 s ("At heto pa!" medal reveal) with melody + fuller drums
  108-   statue/outro warm resolve to D, fade out
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
BAR = 3.0
BEAT = BAR / 4
END = 122.5
N = int(SR * END)
L = np.zeros(N)
R = np.zeros(N)
rng = np.random.default_rng(1978)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def add(sig, t0, gain=1.0, pan=0.0):
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i]
    L[i:i + len(sig)] += sig * gain * (1 - pan) ** 0.5
    R[i:i + len(sig)] += sig * gain * (1 + pan) ** 0.5


def piano(m, dur=2.5, vel=1.0):
    t = np.arange(int(SR * dur)) / SR
    f = hz(m)
    s = np.zeros_like(t)
    for n in range(1, 8):
        fn = f * n * (1 + 0.0004 * n * n)  # slight stretch, like real strings
        if fn > 16000:
            break
        s += np.sin(2 * np.pi * fn * t) / n ** 1.6 * np.exp(-t * (1.2 + 0.9 * n))
    s *= np.minimum(1, t / 0.004)
    click = rng.standard_normal(len(t)) * np.exp(-t * 300) * 0.04  # hammer: the "crisp" bit
    s = s + np.diff(click, prepend=0)
    s *= np.minimum(1, (dur - t) / 0.05)
    return s * vel


def pad(notes, dur, attack=0.8, release=1.2, bright=6, trem=0.0):
    t = np.arange(int(SR * (dur + release))) / SR
    s = np.zeros_like(t)
    for m in notes:
        for det in (-0.0018, 0.0, 0.0021):
            f = hz(m) * (1 + det)
            ph = rng.uniform(0, 2 * np.pi)
            for n in range(1, bright + 1):
                s += np.sin(2 * np.pi * f * n * t + ph * n) / n ** 1.3
    env = np.minimum(1, t / attack) * np.where(t < dur, 1.0, np.exp(-(t - dur) / (release / 3)))
    if trem:
        env *= 0.75 + 0.25 * np.sin(2 * np.pi * trem * t)
    return s * env / (len(notes) * 3 * 2.5)


def kick(vel=1.0):
    t = np.arange(int(SR * 0.5)) / SR
    f = 45 + 75 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7) * vel


def hat(vel=1.0, length=0.05):
    t = np.arange(int(SR * length)) / SR
    n = rng.standard_normal(len(t))
    n = np.diff(np.diff(n, prepend=0), prepend=0)  # high-pass twice: bright, crisp tick
    return n / 4 * np.exp(-t * 90) * vel


def snare(vel=1.0):
    t = np.arange(int(SR * 0.25)) / SR
    n = np.diff(rng.standard_normal(len(t)), prepend=0) / 2
    return (n * np.exp(-t * 22) + 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)) * vel


def riser(dur):
    t = np.arange(int(SR * dur)) / SR
    n = rng.standard_normal(len(t))
    lp = np.empty_like(n)
    acc = 0.0
    cut = 300 + 5000 * (t / dur) ** 2
    a = np.exp(-2 * np.pi * cut / SR)
    for i in range(len(n)):
        acc = (1 - a[i]) * n[i] + a[i] * acc
        lp[i] = acc
    return lp * (t / dur) ** 2.5 * 1.5


CH = {  # bass + voicing (MIDI)
    "D": [38, 50, 57, 62, 66], "A": [33, 45, 52, 57, 61, 64], "Bm": [35, 47, 54, 59, 62, 66],
    "G": [31, 43, 50, 55, 59, 62], "Em": [28, 40, 47, 52, 55, 59], "F#": [30, 42, 49, 54, 58, 61],
}
ARP = {  # arpeggio tones for the piano (mid register)
    "D": [62, 66, 69, 74], "A": [61, 64, 69, 73], "Bm": [59, 62, 66, 71],
    "G": [59, 62, 67, 71], "Em": [59, 64, 67, 71], "F#": [58, 61, 66, 70],
}
bars = (
    [("hook", c) for c in ["Bm", "G", "Bm", "G", "A"]]                       # 0-15
    + [("life", c) for c in ["D", "A", "Bm", "G"] * 3]                         # 15-51
    + [("flight", c) for c in ["Bm", "G", "Em", "F#", "F#"]]                   # 51-66
    + [("crash", c) for c in ["Bm", "G", "Bm", "G", "Em"]]                     # 66-81
    + [("legacy", c) for c in ["G", "D", "A", "Bm", "A"]]                      # 81-96
    + [("peak", c) for c in ["D", "A", "Bm", "G"]]                             # 96-108
    + [("outro", c) for c in ["G", "A", "D"]]                                  # 108-117 (+ D held)
)

melody = [  # (beat offset from 96 s, midi, beats) : the "medal" theme
    (0, 78, 1.5), (1.5, 76, 0.5), (2, 74, 2), (4, 76, 1.5), (5.5, 73, 0.5), (6, 69, 2),
    (8, 71, 1), (9, 73, 1), (10, 74, 2), (12, 76, 1.5), (13.5, 74, 0.5), (14, 74, 2),
]

for bi, (sec, ch) in enumerate(bars):
    t0 = bi * BAR
    last = bi == len(bars) - 1
    dur = BAR + (END - t0 - BAR - 1.5 if last else 0)
    notes = CH[ch]
    arp = ARP[ch]
    if sec == "hook":
        add(pad(notes[1:5], dur, attack=1.2, bright=4), t0, 0.55)
        add(pad([notes[0]], dur, attack=0.6, bright=3), t0, 0.5)
        for b in (0, 0.5):  # heartbeat: thump-thump
            add(kick(0.55 if b == 0 else 0.35), t0 + b)
        add(piano(arp[-1] + 12, 2.8, 0.35), t0 + 1.5, pan=0.3)
    elif sec == "life":
        groove = t0 >= 30
        add(pad(notes[1:], dur, attack=0.9, bright=5), t0, 0.5)
        add(pad([notes[0]], dur, bright=3), t0, 0.45)
        seq = [arp[0], arp[1], arp[2], arp[3], arp[2] + 12 if arp[2] + 12 < 84 else arp[2], arp[3], arp[2], arp[1]]
        for k, m in enumerate(seq):
            add(piano(m, 1.6, 0.55 if k % 2 == 0 else 0.42), t0 + k * BEAT / 2, pan=-0.25 + 0.5 * (k / 7))
        if groove:
            for k in range(8):
                add(hat(0.5 if k % 2 else 0.3), t0 + k * BEAT / 2, pan=0.35)
            add(kick(0.6), t0)
            add(kick(0.45), t0 + 2 * BEAT)
    elif sec == "flight":
        add(pad(notes[1:], dur, attack=0.4, bright=7, trem=7.5), t0, 0.55)
        add(pad([notes[0]], dur, attack=0.2, bright=4), t0, 0.5)
        for k in range(4):
            add(kick(0.65), t0 + k * BEAT)
            add(piano(notes[1], 0.7, 0.45), t0 + k * BEAT)
        for k in range(16):
            add(hat(0.35 + 0.25 * (k % 4 == 2)), t0 + k * BEAT / 4, pan=0.3)
        if bars[bi + 1][0] == "crash":  # last flight bar -> riser into the eject
            add(riser(BAR), t0, 0.35)
    elif sec == "crash":
        add(pad(notes[1:4], dur, attack=2.0, bright=3), t0, 0.35)
        add(piano(arp[0] - 12, 3.0, 0.4), t0 + 0.75, pan=-0.2)
        add(piano(arp[2], 3.0, 0.25), t0 + 2.25, pan=0.2)
    elif sec in ("legacy", "peak"):
        peak = sec == "peak"
        add(pad(notes[1:], dur, attack=0.9 if not peak else 0.4, bright=6), t0, 0.55 if not peak else 0.62)
        add(pad([n + 12 for n in notes[2:]], dur, attack=1.2, bright=4), t0, 0.22 if not peak else 0.32)  # strings up high
        add(pad([notes[0]], dur, bright=3), t0, 0.5)
        seq = [arp[0], arp[1], arp[2], arp[3], arp[2], arp[1], arp[2], arp[3]]
        for k, m in enumerate(seq):
            add(piano(m, 1.6, 0.45 if k % 2 == 0 else 0.34), t0 + k * BEAT / 2, pan=0.25 - 0.5 * (k / 7))
        add(kick(0.55 if not peak else 0.7), t0)
        add(kick(0.4 if not peak else 0.55), t0 + 2 * BEAT)
        for k in range(8):
            add(hat(0.25 if not peak else 0.4), t0 + k * BEAT / 2, pan=0.35)
        if peak:
            add(snare(0.5), t0 + BEAT)
            add(snare(0.5), t0 + 3 * BEAT)
    elif sec == "outro":
        add(pad(notes[1:], dur, attack=0.8, bright=5), t0, 0.5)
        add(pad([notes[0]], dur, bright=3), t0, 0.45)
        for k, m in enumerate(arp):
            add(piano(m, 2.4, 0.42), t0 + k * BEAT, pan=-0.2 + 0.4 * k / 3)
        if last:
            add(piano(74, 5.0, 0.4), t0 + 4 * BEAT)
            add(piano(78, 5.0, 0.3), t0 + 4 * BEAT + 0.02)

for off, m, beats in melody:  # medal theme on top
    add(piano(m, beats * BEAT + 1.2, 0.62), 96 + off * BEAT, pan=0.05)
    add(piano(m - 12, beats * BEAT + 1.2, 0.25), 96 + off * BEAT)

# ---- reverb (FFT convolution with a decaying-noise impulse response), then master
ir_t = np.arange(int(SR * 2.4)) / SR
irL = rng.standard_normal(len(ir_t)) * np.exp(-ir_t * 2.6)
irR = rng.standard_normal(len(ir_t)) * np.exp(-ir_t * 2.6)
irL[: int(0.012 * SR)] = 0
irR[: int(0.017 * SR)] = 0


def conv(x, ir):
    n = 1 << int(np.ceil(np.log2(len(x) + len(ir))))
    return np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(ir, n), n)[: len(x)]


wetL, wetR = conv(L, irL), conv(R, irR)
wetL *= np.abs(L).max() / (np.abs(wetL).max() + 1e-9)
wetR *= np.abs(R).max() / (np.abs(wetR).max() + 1e-9)
outL, outR = L + 0.35 * wetL, R + 0.35 * wetR
st = np.stack([outL, outR], 1)
st = np.tanh(st / np.abs(st).max() * 1.4) / np.tanh(1.4)  # gentle glue
t = np.arange(N) / SR
st *= np.minimum(1, t / 0.5)[:, None] * np.clip((END - t) / 3.0, 0, 1)[:, None]  # fade in / out
st = st / np.abs(st).max() * 10 ** (-1 / 20)

out = Path(__file__).resolve().parent / "score.wav"
with wave.open(str(out), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((st * 32767).astype("<i2").tobytes())
print("wrote", out, f"{END:.1f}s")
