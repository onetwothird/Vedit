import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
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
    // fade colours across the switch instead of snapping (skipped under reduced motion by the CSS)
    const root = document.documentElement;
    root.classList.add("theme-fade");
    window.setTimeout(() => root.classList.remove("theme-fade"), 320);
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
      <Steps current={step} editHid={editHid} />
      <div className="actions">
        <NavLink className="icon" to="/settings" aria-label={t("topbar.settings")} title={t("topbar.settings")}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="gear">
            <path d="M10.3 3.6a1.7 1.7 0 0 1 3.4 0l.2.9a1.7 1.7 0 0 0 2.5 1l.8-.4a1.7 1.7 0 0 1 2.4 2.4l-.4.8a1.7 1.7 0 0 0 1 2.5l.9.2a1.7 1.7 0 0 1 0 3.4l-.9.2a1.7 1.7 0 0 0-1 2.5l.4.8a1.7 1.7 0 0 1-2.4 2.4l-.8-.4a1.7 1.7 0 0 0-2.5 1l-.2.9a1.7 1.7 0 0 1-3.4 0l-.2-.9a1.7 1.7 0 0 0-2.5-1l-.8.4a1.7 1.7 0 0 1-2.4-2.4l.4-.8a1.7 1.7 0 0 0-1-2.5l-.9-.2a1.7 1.7 0 0 1 0-3.4l.9-.2a1.7 1.7 0 0 0 1-2.5l-.4-.8a1.7 1.7 0 0 1 2.4-2.4l.8.4a1.7 1.7 0 0 0 2.5-1z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </NavLink>
        <button
          type="button"
          className="icon theme-toggle"
          aria-label={t("theme.toggle", { mode: t(`theme.${next}`) })}
          title={t("theme.toggle", { mode: t(`theme.${next}`) })}
          onClick={toggleTheme}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true" className="sun">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
          </svg>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true" className="moon">
            <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
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
