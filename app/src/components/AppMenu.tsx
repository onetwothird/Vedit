import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { initials, useAuth } from "../auth/auth";
import { t } from "../i18n/t";
import { getTheme, setTheme, type Theme } from "../theme/theme";

/** One header control for app-wide things (like the account button in CapCut and Descript): the account
    (or Sign in), the Dark / Light appearance switch, Settings and Sign out. */
export default function AppMenu() {
  const [open, setOpen] = useState(false);
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const navigate = useNavigate();
  const { account, signOut } = useAuth();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function choose(next: Theme) {
    if (next === theme) return;
    // fade colours across the switch instead of snapping (skipped under reduced motion by the CSS)
    const html = document.documentElement;
    html.classList.add("theme-fade");
    window.setTimeout(() => html.classList.remove("theme-fade"), 320);
    setTheme(next);
    setThemeState(next);
  }

  return (
    <div className="app-menu" ref={root}>
      <button
        ref={button}
        type="button"
        className="menu-btn"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={t("menu.label")}
        title={t("menu.label")}
        onClick={() => setOpen((o) => !o)}
      >
        {account ? (
          <span className="avatar" aria-hidden="true">{initials(account.name)}</span>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="8.5" r="3.5" />
            <path d="M5 19.5c1.2-3.3 3.8-5 7-5s5.8 1.7 7 5" />
          </svg>
        )}
        <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m7 10 5 5 5-5" />
        </svg>
      </button>
      {open && (
        <div className="menu-panel" id={panelId} role="group" aria-label={t("menu.label")}>
          {account ? (
            <div className="menu-account">
              <span className="avatar lg" aria-hidden="true">{initials(account.name)}</span>
              <span className="who">
                <span className="who-name">{account.name}</span>
                <span className="who-email" title={account.email}>{account.email}</span>
                <span className={`plan ${account.plan}`}>{t(`menu.plan.${account.plan}`)}</span>
              </span>
            </div>
          ) : (
            <Link className="primary menu-signin" to="/signin" onClick={() => setOpen(false)}>{t("menu.account.signin")}</Link>
          )}
          <fieldset className="menu-section">
            <legend>{t("menu.appearance")}</legend>
            <div className="seg seg-full">
              {(["dark", "light"] as const).map((m) => (
                <label key={m} className="seg-opt">
                  <input type="radio" name={`${panelId}-theme`} checked={theme === m} onChange={() => choose(m)} />
                  <span>{t(`menu.theme.${m}`)}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <Link className="menu-item" to="/settings" onClick={() => setOpen(false)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M10.3 3.6a1.7 1.7 0 0 1 3.4 0l.2.9a1.7 1.7 0 0 0 2.5 1l.8-.4a1.7 1.7 0 0 1 2.4 2.4l-.4.8a1.7 1.7 0 0 0 1 2.5l.9.2a1.7 1.7 0 0 1 0 3.4l-.9.2a1.7 1.7 0 0 0-1 2.5l.4.8a1.7 1.7 0 0 1-2.4 2.4l-.8-.4a1.7 1.7 0 0 0-2.5 1l-.2.9a1.7 1.7 0 0 1-3.4 0l-.2-.9a1.7 1.7 0 0 0-2.5-1l-.8.4a1.7 1.7 0 0 1-2.4-2.4l.4-.8a1.7 1.7 0 0 0-1-2.5l-.9-.2a1.7 1.7 0 0 1 0-3.4l.9-.2a1.7 1.7 0 0 0 1-2.5l-.4-.8a1.7 1.7 0 0 1 2.4-2.4l.8.4a1.7 1.7 0 0 0 2.5-1z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            {t("topbar.settings")}
          </Link>
          {account && (
            <button
              type="button"
              className="menu-item"
              onClick={async () => {
                setOpen(false);
                await signOut();
                navigate("/signin");
              }}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10" />
              </svg>
              {t("menu.account.signout")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
