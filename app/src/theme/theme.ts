export type Theme = "dark" | "light";

const KEY = "vedit.theme";

export function getTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle("light", theme === "light");
  let meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "color-scheme";
    document.head.appendChild(meta);
  }
  meta.content = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // storage unavailable: the theme applies for this session only
  }
}

const EVENT = "vedit:theme";

/** Calls `fn` whenever the theme changes anywhere (the header button, Settings). Returns the unsubscribe. */
export function onThemeChange(fn: (theme: Theme) => void): () => void {
  const h = () => fn(getTheme());
  window.addEventListener(EVENT, h);
  return () => window.removeEventListener(EVENT, h);
}

/** setTheme with the colour fade, and every listener told. */
export function switchTheme(theme: Theme): void {
  const html = document.documentElement;
  html.classList.add("theme-fade");
  window.setTimeout(() => html.classList.remove("theme-fade"), 320);
  setTheme(theme);
  window.dispatchEvent(new Event(EVENT));
}
