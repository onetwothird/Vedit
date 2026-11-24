import { Fragment } from "react";
import { Link, useParams } from "react-router-dom";
import { t } from "../i18n/t";
import { WHOLE_HID } from "../lib/projectKind";

export type StepNumber = 1 | 2 | 3 | 4;

export interface StepsProps {
  current?: StepNumber;
  /** Where a completed Edit step leads. Without it, the step goes back to Pick clips. */
  editHid?: string;
  /** A vlog or Quick Reel project: no Pick clips step, and Edit is its one clip. */
  whole?: boolean;
}

const LABELS: [StepNumber, string][] = [
  [1, "step.import"], [2, "step.pick"], [3, "step.edit"], [4, "step.export"],
];

export default function Steps({ current, editHid, whole }: StepsProps) {
  const { id } = useParams();
  const hasProject = !!id && id !== "new";
  const target = (n: StepNumber): string => {
    const base = `/p/${id}`;
    if (n === 1) return `${base}/import`;
    if (n === 2) return `${base}/pick`;
    const hid = editHid ?? (whole ? WHOLE_HID : undefined);
    return hid ? `${base}/edit/${hid}` : `${base}/pick`;
  };
  const shown = whole ? LABELS.filter(([n]) => n !== 2) : LABELS;

  return (
    <nav className="steps" aria-label={t("steps.label")}>
      {shown.map(([n, key], i) => {
        const done = current !== undefined && n < current;
        const on = n === current;
        const label = t(key);
        return (
          <Fragment key={n}>
            {i > 0 && <span className={`sep${current !== undefined && n < current ? " done" : n === current ? " into" : ""}`} aria-hidden="true" />}
            {done && hasProject ? (
              <Link className="step done" to={target(n)}>
                <span className="dot" aria-hidden="true">✓</span>
                <span className="step-label">{label}</span>
              </Link>
            ) : (
              <span className={`step${on ? " on" : done ? " done" : ""}`} aria-current={on ? "step" : undefined}>
                <span className="dot" aria-hidden="true">{done ? "✓" : i + 1}</span>
                <span className="step-label">{label}</span>
              </span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}
