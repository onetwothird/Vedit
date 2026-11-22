import { Fragment } from "react";
import { Link, useParams } from "react-router-dom";
import { t } from "../i18n/t";

export type StepNumber = 1 | 2 | 3 | 4;

export interface StepsProps {
  current?: StepNumber;
  /** Where a completed Edit step leads. Without it, the step goes back to Pick clips. */
  editHid?: string;
}

const LABELS: [StepNumber, string][] = [
  [1, "step.import"], [2, "step.pick"], [3, "step.edit"], [4, "step.export"],
];

export default function Steps({ current, editHid }: StepsProps) {
  const { id } = useParams();
  const hasProject = !!id && id !== "new";
  const target = (n: StepNumber): string => {
    const base = `/p/${id}`;
    if (n === 1) return `${base}/import`;
    if (n === 2) return `${base}/pick`;
    return editHid ? `${base}/edit/${editHid}` : `${base}/pick`;
  };

  return (
    <nav className="steps" aria-label={t("steps.label")}>
      {LABELS.map(([n, key], i) => {
        const done = current !== undefined && n < current;
        const on = n === current;
        const label = t(key);
        return (
          <Fragment key={n}>
            {i > 0 && <span className="sep" aria-hidden="true" />}
            {done && hasProject ? (
              <Link className="step done" to={target(n)}>
                <span className="dot" aria-hidden="true">✓</span>
                <span className="step-label">{label}</span>
              </Link>
            ) : (
              <span className={`step${on ? " on" : done ? " done" : ""}`} aria-current={on ? "step" : undefined}>
                <span className="dot" aria-hidden="true">{done ? "✓" : n}</span>
                <span className="step-label">{label}</span>
              </span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}
