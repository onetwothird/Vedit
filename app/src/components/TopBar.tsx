import { useState } from "react";
import { Link } from "react-router-dom";
import { t } from "../i18n/t";
import { getTheme, setTheme, type Theme } from "../theme/theme";
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
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";

  function toggleTheme() {
    setTheme(next);
    setThemeState(next);
  }

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
      {step !== undefined ? <Steps current={step} editHid={editHid} /> : <span aria-hidden="true" />}
      <div className="actions">
        <Link className="secondary" to="/settings">{t("topbar.settings")}</Link>
        <button
          type="button"
          className="icon"
          aria-label={t("theme.toggle", { mode: t(`theme.${next}`) })}
          title={t("theme.toggle", { mode: t(`theme.${next}`) })}
          onClick={toggleTheme}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <circle cx="12" cy="12" r="8" />
            <path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" />
          </svg>
        </button>
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
