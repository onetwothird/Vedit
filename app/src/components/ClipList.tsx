import { useRef, type KeyboardEvent } from "react";
import type { FilmstripInfo, ProjectClip } from "../engine/types";
import { t } from "../i18n/t";
import { sortClips, type ClipSort } from "../lib/clips";
import { formatLength } from "../lib/time";
import FilmFrame from "./FilmFrame";

export interface ClipListProps {
  clips: ProjectClip[];
  currentId?: string;
  sort: ClipSort;
  film: FilmstripInfo | null;
  onSort(sort: ClipSort): void;
  onSelect(hid: string): void;
  onToggleKept(hid: string): void;
}

const SORTS: ClipSort[] = ["best", "order"];

/** The ranked clip list shared by Pick clips and the Edit screen's left pane. */
export default function ClipList({ clips, currentId, sort, film, onSort, onSelect, onToggleKept }: ClipListProps) {
  const sorted = sortClips(clips, sort);
  const mains = useRef(new Map<string, HTMLButtonElement>());
  const tabbable = sorted.some((c) => c.highlight.id === currentId) ? currentId : sorted[0]?.highlight.id;

  function onKeyDown(e: KeyboardEvent<HTMLUListElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (!(e.target as HTMLElement).classList.contains("clip-main")) return;
    e.preventDefault();
    const at = sorted.findIndex((c) => c.highlight.id === currentId);
    const next = at < 0 ? 0 : at + (e.key === "ArrowDown" ? 1 : -1);
    const target = sorted[next];
    if (!target || next === at) return;
    onSelect(target.highlight.id);
    mains.current.get(target.highlight.id)?.focus();
  }

  return (
    <div className="clip-list">
      <fieldset className="seg clip-sort">
        <legend className="sr-only">{t("clips.sort")}</legend>
        {SORTS.map((s) => (
          <label key={s} className="seg-opt">
            <input type="radio" name="clip-sort" value={s} checked={sort === s} onChange={() => onSort(s)} />
            <span>{t(`clips.sort.${s}`)}</span>
          </label>
        ))}
      </fieldset>
      <ul className="clips" onKeyDown={onKeyDown}>
        {sorted.map((c) => {
          const h = c.highlight;
          const on = h.id === currentId;
          return (
            <li key={h.id} className={`clip${on ? " on" : ""}${c.kept ? "" : " off"}`}>
              <button
                type="button"
                className="clip-main"
                ref={(el) => {
                  if (el) mains.current.set(h.id, el);
                  else mains.current.delete(h.id);
                }}
                aria-current={on ? "true" : undefined}
                tabIndex={h.id === tabbable ? 0 : -1}
                onClick={() => onSelect(h.id)}
              >
                <FilmFrame film={film} t={h.start} width={64} className="thumb" />
                <span className="clip-text">
                  <span className="clip-title">{h.title}</span>
                  <span className="clip-meta mono">
                    <span>{formatLength(h.end - h.start)}</span>
                    <span className="score" title={t("clips.score", { n: Math.round(h.score) })}>
                      <span className="bar" aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, h.score))}%` }} /></span>
                      <span>{Math.round(h.score)}</span>
                    </span>
                  </span>
                </span>
              </button>
              <label className="keep">
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={c.kept}
                  aria-label={t("clips.keep", { title: h.title })}
                  onChange={() => onToggleKept(h.id)}
                />
                <span className="box" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
