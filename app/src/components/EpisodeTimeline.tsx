import { useMemo } from "react";
import { speechEnergy } from "../edit/lanes";
import type { ProjectClip, TranscriptWord } from "../engine/types";
import { t } from "../i18n/t";
import { formatClock } from "../lib/time";

const BARS = 180;

export interface EpisodeTimelineProps {
  clips: ProjectClip[];
  words: readonly TranscriptWord[];
  duration: number;
  currentId?: string;
  onSelect(hid: string): void;
}

/** The whole video as a waveform with every clip marked on it where it happens: kept clips filled, skipped ones
    outlined. The green playhead stands at the selected clip. Each mark is a button that selects its clip. */
export default function EpisodeTimeline({ clips, words, duration, currentId, onSelect }: EpisodeTimelineProps) {
  const levels = useMemo(() => speechEnergy(words, 0, duration, BARS), [words, duration]);
  const inOrder = useMemo(() => [...clips].sort((a, b) => a.highlight.start - b.highlight.start), [clips]);
  if (!(duration > 0)) return null;
  const pct = (s: number) => `${Math.max(0, Math.min(100, (s / duration) * 100))}%`;
  const current = clips.find((c) => c.highlight.id === currentId);
  return (
    <section className="ep" aria-label={t("pick.timeline")}>
      <div className="ep-scroll">
        <div className="ep-track">
          <svg className="ep-wave" viewBox={`0 0 ${BARS * 4} 40`} preserveAspectRatio="none" aria-hidden="true">
            {levels.map((v, i) => {
              const h = 3 + v * 34;
              return <rect key={i} x={i * 4 + 0.6} y={(40 - h) / 2} width={2.8} height={h} rx={1} />;
            })}
          </svg>
          {inOrder.map((c) => {
            const h = c.highlight;
            return (
              <button
                key={h.id}
                type="button"
                className={`ep-clip${c.kept ? " kept" : ""}${h.id === currentId ? " on" : ""}`}
                style={{ left: pct(h.start), width: `max(6px, ${pct(h.end - h.start)})` }}
                aria-label={t("pick.timeline.clip", { title: h.title, at: formatClock(h.start) })}
                aria-current={h.id === currentId ? "true" : undefined}
                onClick={() => onSelect(h.id)}
              />
            );
          })}
          {current && <span className="ep-head" style={{ left: pct(current.highlight.start) }} aria-hidden="true"><i /></span>}
        </div>
        <div className="ep-times mono" aria-hidden="true">
          <span>0:00</span>
          <span>{formatClock(duration)}</span>
        </div>
      </div>
    </section>
  );
}
