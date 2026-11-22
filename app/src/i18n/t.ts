import en from "./en.json";

const strings: Record<string, string> = en;

export function t(key: string, vars?: Record<string, string | number>): string {
  const raw = strings[key];
  if (raw === undefined) {
    if (import.meta.env.DEV) console.warn(`[i18n] missing key: ${key}`);
    return key;
  }
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (m, name: string) =>
    name in vars ? String(vars[name]) : m,
  );
}
