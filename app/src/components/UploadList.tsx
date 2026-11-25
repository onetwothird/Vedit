import { t } from "../i18n/t";

/* Files on their way to the library (the website): one row each, with its own progress bar and, when the screen
   allows it, a button that stops just that file. */

export interface UploadRow {
  id: string;
  name: string;
  /** 0 to 100; null while the file waits for a free slot. */
  pct: number | null;
}

export default function UploadList({ rows, onCancel, label }: { rows: UploadRow[]; onCancel?(id: string): void; label?: string }) {
  if (!rows.length) return null;
  return (
    <ul className="up-list" aria-label={label}>
      {rows.map((r) => {
        const pct = r.pct ?? 0;
        return (
          <li key={r.id} className={`up-row${r.pct === null ? " waiting" : ""}`}>
            <span className="up-name mono" title={r.name}>{r.name}</span>
            <div className="meter" role="progressbar" aria-label={t("upload.row.label", { name: r.name })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
              <i style={{ width: `${pct}%` }} />
            </div>
            <span className="up-pct mono" aria-hidden="true">{t("upload.row.pct", { pct })}</span>
            {onCancel && (
              <button type="button" className="icon-btn" aria-label={t("upload.row.cancel", { name: r.name })} onClick={() => onCancel(r.id)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17" /></svg>
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
