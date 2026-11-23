/* Why a clip was picked, in plain words, from the scores the engine gave it (scripts/highlights.py WEIGHTS). */

/** Feature keys with a reason worth showing, and how much each counts toward the score. */
const WEIGHTS: Record<string, number> = {
  hook: 1.5,
  energy: 1.0,
  complete: 1.0,
  laughter: 0.8,
  speech_rate: 0.6,
  direct: 0.6,
  length_fit: 0.6,
  energy_var: 0.5,
};

const STRONG = 0.55; // a feature must score at least this (0..1) to count as a reason

/** The clip's top reasons (at most `max`), strongest first, as feature keys; unknown or weak features are dropped. */
export function whyPicked(features: Record<string, number>, max = 3): string[] {
  return Object.entries(features)
    .filter(([k, v]) => k in WEIGHTS && Number.isFinite(v) && v >= STRONG)
    .sort(([a, x], [b, y]) => y * WEIGHTS[b] - x * WEIGHTS[a])
    .slice(0, max)
    .map(([k]) => k);
}
