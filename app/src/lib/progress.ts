// The client's copy of the server's analyze share table, so overall pct can be turned into a per-stage %.
// Server: a model download step takes 0.3 and every other share is scaled by 0.7.

export type StageKey = "proxy" | "model" | "analyze" | "highlights" | "framing";

const BASE_SHARES: [StageKey, number][] = [
  ["proxy", 0.1], ["analyze", 0.6], ["highlights", 0.15], ["framing", 0.15],
];

export interface StageShare {
  key: StageKey;
  share: number;
}

/** The stages of one analyze job, in order, with the share of overall progress each one covers. */
export function stageShares(hasModel: boolean): StageShare[] {
  const list = BASE_SHARES.map(([key, share]) => ({ key, share: hasModel ? share * 0.7 : share }));
  return hasModel ? [{ key: "model" as StageKey, share: 0.3 }, ...list] : list;
}

export type StageStatus = "done" | "current" | "pending";

export interface StageRow {
  key: StageKey;
  status: StageStatus;
  /** 0-100 for the current stage, 100 for done, 0 for pending. */
  pct: number;
}

/** Where each stage stands, from the job's current stage name and overall pct. */
export function stageRows(stage: string, overallPct: number, hasModel: boolean, finished = false): StageRow[] {
  const shares = stageShares(hasModel);
  const at = shares.findIndex((s) => s.key === stage);
  let start = 0;
  return shares.map(({ key, share }, i) => {
    const from = start;
    start += share;
    if (finished || (at >= 0 && i < at)) return { key, status: "done" as const, pct: 100 };
    if (i !== at) return { key, status: "pending" as const, pct: 0 };
    const within = ((overallPct / 100 - from) / share) * 100;
    return { key, status: "current" as const, pct: Math.round(Math.min(100, Math.max(0, within))) };
  });
}

/** Seconds left, or null while the estimate is too early to trust (pct 3 or lower). */
export function secondsLeft(elapsedMs: number, pct: number): number | null {
  if (!(pct > 3)) return null;
  return Math.max(0, Math.round(((elapsedMs / 1000) / pct) * (100 - pct)));
}
