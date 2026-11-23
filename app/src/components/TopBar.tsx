import { Link } from "react-router-dom";
import { t } from "../i18n/t";
import AppMenu from "./AppMenu";
import Logo from "./Logo";
import Steps, { type StepNumber } from "./Steps";

export interface TopBarProps {
  fileName?: string;
  saved?: boolean;
  step?: StepNumber;
  /** Where a completed Edit step leads (a highlight id). */
  editHid?: string;
  primary?: { label: string; count?: number; onClick(): void; disabled?: boolean };
}

export default function TopBar({ fileName, saved, step, editHid, primary }: TopBarProps) {
  return (
    <header className="pane top">
      <div className="brand">
        <Link className="word" to="/" aria-label={t("topbar.home")}>
          <Logo size={26} className="mark" />
          <span>{t("app.name")}</span>
        </Link>
        {fileName && <span className="proj" title={fileName}>{fileName}</span>}
        {saved && <span className="saved">{t("topbar.saved")}</span>}
      </div>
      <Steps current={step} editHid={editHid} />
      <div className="actions">
        <AppMenu />
        {primary && (
          <>
            <span className="divider" aria-hidden="true" />
            <button type="button" className="primary" onClick={primary.onClick} disabled={primary.disabled}>
              {primary.label}
              {primary.count !== undefined && <span className="count">{primary.count}</span>}
            </button>
          </>
        )}
      </div>
    </header>
  );
}
