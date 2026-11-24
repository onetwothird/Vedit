import { t } from "../i18n/t";
import { stageRows } from "../lib/progress";

export interface ProgressStagesProps {
  stage: string;
  pct: number;
  hasModel: boolean;
  finished?: boolean;
  /** A vlog or Quick Reel project: no highlight search or framing stages. */
  whole?: boolean;
}

/** The analyze stages in order: finished ones with a check, the current one with its own %, the rest waiting. */
export default function ProgressStages({ stage, pct, hasModel, finished, whole }: ProgressStagesProps) {
  const rows = stageRows(stage, pct, hasModel, finished, whole);
  return (
    <ol className="stages">
      {rows.map((r) => (
        <li
          key={r.key}
          className={`stage-row ${r.status}`}
          aria-current={r.status === "current" ? "step" : undefined}
        >
          <span className="mark" aria-hidden="true">
            {r.status === "done" ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                <path d="m5 12 5 5 9-10" />
              </svg>
            ) : (
              <i />
            )}
          </span>
          <span className="name">{t(`import.stage.${r.key}`)}</span>
          <span className="val mono">
            {r.status === "done" ? (
              <span className="sr-only">{t("import.stage.done")}</span>
            ) : r.status === "current" ? (
              `${r.pct}%`
            ) : (
              <span className="sr-only">{t("import.stage.waiting")}</span>
            )}
          </span>
          {r.status === "current" && (
            <span className="bar" aria-hidden="true">
              <i style={{ width: `${r.pct}%` }} />
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
