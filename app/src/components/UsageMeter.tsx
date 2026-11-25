import { useEffect, useState } from "react";
import { engine } from "../engine/client";
import type { Usage } from "../engine/types";
import { t } from "../i18n/t";

const GB = 1024 ** 3;

/** 1.2, 10, 0: one decimal, none when it would be ".0". */
function gigabytes(bytes: number): string {
  return String(Math.round((Math.max(0, bytes) / GB) * 10) / 10);
}

/** The month's minutes and the stored gigabytes as the sentences the meter shows. */
export function usageText(u: Usage): { minutes: string; storage: string } {
  const used = Math.floor(Math.max(0, u.seconds_used) / 60);
  const limit = Math.floor(u.seconds_limit / 60);
  return {
    minutes: t("usage.minutes", { used, limit }),
    storage: t("usage.storage", { used: gigabytes(u.storage_bytes), limit: gigabytes(u.storage_limit) }),
  };
}

function Bar({ label, text, now, max }: { label: string; text: string; now: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((now / max) * 100)) : 0;
  return (
    <div className="usage-row">
      <p className="usage-text">{text}</p>
      <div className="meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(now, max)} aria-valuetext={text}>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** What this month's analysis time and the stored files add up to, against the account's limits (the website only). */
export default function UsageMeter() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    engine.usage().then(
      (u) => live && setUsage(u),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, []);

  if (failed) return <p className="set-note usage-meter">{t("usage.error")}</p>;
  if (!usage) return <div className="usage-meter" aria-busy="true" aria-label={t("usage.loading")} />;
  const text = usageText(usage);
  return (
    <div className="usage-meter">
      <Bar label={t("usage.minutes.label")} text={text.minutes} now={usage.seconds_used} max={usage.seconds_limit} />
      <Bar label={t("usage.storage.label")} text={text.storage} now={usage.storage_bytes} max={usage.storage_limit} />
    </div>
  );
}
