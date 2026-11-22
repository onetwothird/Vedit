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
