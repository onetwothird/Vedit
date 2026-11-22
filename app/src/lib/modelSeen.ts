// Remembers which analyze jobs had a model-download step. The Import screen can unmount mid-run and come back after
// that step is over, so the answer must live outside the component (and outside a reload, via sessionStorage).

import { jobStore } from "../engine/events";

const seen = new Set<string>();
const key = (id: string): string => `vedit.job.${id}.model`;

function record(id: string): void {
  seen.add(id);
  try {
    sessionStorage.setItem(key(id), "1");
  } catch {
    // storage unavailable: the in-memory set still covers this session
  }
}

// Watching the store for the whole app lifetime also keeps the job feed open, so no stage event is missed.
jobStore.subscribe(() => {
  for (const job of jobStore.getJobs().values()) if (job.stage === "model" && !seen.has(job.id)) record(job.id);
});

export function hasModelStep(jobId: string | undefined): boolean {
  if (!jobId) return false;
  if (seen.has(jobId)) return true;
  try {
    return sessionStorage.getItem(key(jobId)) === "1";
  } catch {
    return false;
  }
}
