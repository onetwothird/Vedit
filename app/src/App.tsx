import { useState } from "react";
import { getTheme, setTheme, type Theme } from "./theme/theme";
import { t } from "./i18n/t";

export default function App() {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";

  function toggle() {
    setTheme(next);
    setThemeState(next);
  }

  return (
    <main style={{ padding: 24, display: "flex", alignItems: "baseline", gap: 16 }}>
      <h1 className="pixel" style={{ fontSize: 22, fontWeight: 700, letterSpacing: ".01em" }}>
        {t("app.name")}
      </h1>
      <button
        onClick={toggle}
        style={{ padding: "6px 12px", borderRadius: 8, background: "var(--chip)", color: "var(--body)" }}
      >
        {t("theme.toggle", { mode: t(`theme.${next}`) })}
      </button>
    </main>
  );
}
