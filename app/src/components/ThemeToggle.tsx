import { useEffect, useState } from "react";
import { t } from "../i18n/t";
import { getTheme, onThemeChange, switchTheme, type Theme } from "../theme/theme";

/** Its own header button, beside the account menu. The sun and the moon share one button: the outgoing icon
    turns away as the incoming one turns in, and the page colours fade across instead of snapping. */
export default function ThemeToggle() {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  const label = t(next === "light" ? "theme.toLight" : "theme.toDark");

  useEffect(() => onThemeChange(setThemeState), []);

  const toggle = () => switchTheme(next);

  return (
    <button type="button" className={`theme-btn ${theme}`} onClick={toggle} aria-label={label} title={label}>
      <svg className="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
      </svg>
      <svg className="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
      </svg>
    </button>
  );
}
