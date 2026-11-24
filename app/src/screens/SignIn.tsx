import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { MIN_PASSWORD, skipSignIn, useAuth, type SocialProvider } from "../auth/auth";
import Logo from "../components/Logo";
import { t } from "../i18n/t";
import { ClipArt, bars } from "./home/HomeArt";
import "./home/home.css";
import "./signin.css";

type Mode = "signin" | "signup";

const WAVE = bars(64, 7);

/** Phones: a short waveform strip with the playhead sweeping across it once, in place of the product panel. */
function WaveStrip() {
  return (
    <div className="signin-wave" aria-hidden="true">
      <svg viewBox={`0 0 ${WAVE.length * 6} 56`} preserveAspectRatio="none">
        {WAVE.map((v, i) => {
          const h = 6 + v * 44;
          return <rect key={i} x={i * 6 + 1} y={(56 - h) / 2} width={3.6} height={h} rx={1.2} />;
        })}
      </svg>
      <span className="signin-sweep"><i /></span>
    </div>
  );
}

/** Sign in or create an account (CapCut-style): the product on one side, the ways in on the other. */
export default function SignIn() {
  const navigate = useNavigate();
  const { account, busy, error, run, clearError } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (account) navigate("/", { replace: true });
  }, [account, navigate]);

  function switchMode(m: Mode) {
    setMode(m);
    clearError();
  }

  // The mode switch is a tablist: arrow keys, Home and End move between the two tabs.
  const tabs = useRef<HTMLDivElement>(null);
  function onTabKey(e: KeyboardEvent<HTMLButtonElement>) {
    const next: Mode | null =
      e.key === "ArrowRight" || e.key === "ArrowLeft" ? (mode === "signin" ? "signup" : "signin")
        : e.key === "Home" ? "signin" : e.key === "End" ? "signup" : null;
    if (!next) return;
    e.preventDefault();
    switchMode(next);
    tabs.current?.querySelector<HTMLElement>(`#tab-${next}`)?.focus();
  }

  async function social(p: SocialProvider) {
    await run((auth) => auth.social(p));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    await run((auth) => (mode === "signin" ? auth.signIn(email, password) : auth.signUp(name, email, password)));
  }

  function skip() {
    skipSignIn();
    navigate("/", { replace: true });
  }

  return (
    <div className="signin">
      <aside className="signin-show" aria-hidden="true">
        <span className="signin-marks"><i /><i /><i /><i /></span>
        <div className="signin-brand">
          <Logo size={26} />
          <span className="signin-word">{t("app.name")}</span>
        </div>
        <div className="signin-art">
          <ClipArt />
        </div>
        <p className="signin-line">{t("signin.show")}</p>
      </aside>

      <main className="signin-main">
        <div className="signin-strip">
          <div className="signin-brand">
            <Logo size={26} />
            <span className="signin-word">{t("app.name")}</span>
          </div>
          <WaveStrip />
        </div>
        <div className="signin-card">
          <h1 className="signin-title">{t(mode === "signin" ? "signin.title" : "signup.title")}</h1>

          <div ref={tabs} className="seg signin-modes" role="tablist" aria-label={t("signin.modes")}>
            {(["signin", "signup"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                id={`tab-${m}`}
                className="seg-tab"
                aria-selected={mode === m}
                aria-controls="signin-panel"
                tabIndex={mode === m ? 0 : -1}
                onClick={() => switchMode(m)}
                onKeyDown={onTabKey}
              >
                {t(`signin.mode.${m}`)}
              </button>
            ))}
          </div>

          <div className="signin-panel" id="signin-panel" role="tabpanel" aria-labelledby={`tab-${mode}`}>
            <div className="signin-social">
              <button type="button" className="social google" onClick={() => social("google")} disabled={busy}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z" />
                  <path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5H1.3v3.1A12 12 0 0 0 12 24z" />
                  <path fill="#FBBC05" d="M5.3 14.3a7.2 7.2 0 0 1 0-4.6V6.6H1.3a12 12 0 0 0 0 10.8l4-3.1z" />
                  <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.3 6.6l4 3.1c.9-2.9 3.6-4.9 6.7-4.9z" />
                </svg>
                {t("signin.google")}
              </button>
              <button type="button" className="social facebook" onClick={() => social("facebook")} disabled={busy}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#1877F2" d="M24 12a12 12 0 1 0-13.9 11.9v-8.4H7.1V12h3V9.4c0-3 1.8-4.7 4.5-4.7 1.3 0 2.7.2 2.7.2v3h-1.5c-1.5 0-2 .9-2 1.9V12h3.4l-.5 3.5h-2.9v8.4A12 12 0 0 0 24 12z" />
                  <path fill="#fff" d="m16.7 15.5.5-3.5h-3.4V9.8c0-1 .5-1.9 2-1.9h1.5v-3s-1.4-.2-2.7-.2c-2.7 0-4.5 1.7-4.5 4.7V12h-3v3.5h3v8.4a12 12 0 0 0 3.7 0v-8.4z" />
                </svg>
                {t("signin.facebook")}
              </button>
            </div>

            <div className="signin-or"><span>{t("signin.or")}</span></div>

            <form className="signin-form" onSubmit={submit} noValidate>
              {/* The name field opens when creating an account; closed, it is inert so it can't be reached. */}
              <div className={mode === "signup" ? "name-slot open" : "name-slot"} inert={mode !== "signup"}>
                <div>
                  <label className="field-text">
                    <span>{t("signup.name")}</span>
                    <input type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
                  </label>
                </div>
              </div>
              <label className="field-text">
                <span>{t("signin.email")}</span>
                <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <label className="field-text">
                <span>{t("signin.password")}</span>
                <input
                  type="password"
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                  required
                  minLength={MIN_PASSWORD}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                {mode === "signup" && <small>{t("signup.password.hint", { n: MIN_PASSWORD })}</small>}
              </label>
              {error === "check_email" ? (
                <p className="notice" role="status">{t("auth.error.check_email")}</p>
              ) : error ? (
                <p className="error" role="alert">{t(`auth.error.${error}`, { n: MIN_PASSWORD })}</p>
              ) : null}
              <button type="submit" className="primary signin-submit" disabled={busy}>
                {t(mode === "signin" ? "signin.submit" : "signup.submit")}
              </button>
            </form>
          </div>
        </div>
        <button type="button" className="signin-skip" onClick={skip}>{t("signin.skip")}</button>
      </main>
    </div>
  );
}
