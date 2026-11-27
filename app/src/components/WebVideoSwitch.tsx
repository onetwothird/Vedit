import { useState } from "react";
import { useAuth } from "../auth/auth";
import { t } from "../i18n/t";
import { sizeText } from "../sync/labels";
import { setWebVideo, useSync } from "../sync/sync";
import Dialog from "./Dialog";

/** "Available on the web" for one desktop project: on uploads its video to the account (with progress), off asks
    first and deletes the cloud copy. Shown only while sync runs for a signed-in account. */
export default function WebVideoSwitch({ pid }: { pid: string }) {
  const sync = useSync();
  const account = useAuth((a) => a.account);
  const [pending, setPending] = useState<boolean | null>(null); // the state asked for, until the cloud agrees
  const [pct, setPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  if (!sync.available || !sync.running || !account) return null;
  const row = sync.states.find((s) => s.id === pid);
  const other = !!row?.cloud_id && !!row.cloud_owner && row.cloud_owner !== account.id;
  const on = pending ?? !!row?.web_video;
  const busy = pending !== null;

  async function change(next: boolean) {
    setError(null);
    setPending(next);
    setPct(next ? 0 : null);
    try {
      await setWebVideo(pid, next, next ? (p) => setPct(Math.round(p)) : undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
      setPct(null);
    }
  }

  return (
    <div className="web-video">
      <label className="sync-switch" title={other ? t("sync.web.other") : undefined}>
        <input
          type="checkbox"
          role="switch"
          checked={on}
          disabled={!row || other || busy}
          aria-describedby={other ? `wv-other-${pid}` : undefined}
          onChange={(e) => (e.target.checked ? void change(true) : setConfirming(true))}
        />
        <span>{t("sync.web.label")}</span>
      </label>
      {pct !== null && (
        <span className="web-video-progress">
          <progress max={100} value={pct} aria-label={t("sync.web.progress")} />
          <span className="sr-only">{t("sync.web.uploading", { pct })}</span>
        </span>
      )}
      {on && !busy && row && <span className="web-video-note">{t("sync.web.uses", { size: sizeText(row.size) })}</span>}
      {other && <span id={`wv-other-${pid}`} className="web-video-note">{t("sync.web.other")}</span>}
      {error && <span className="error web-video-note" role="alert">{error}</span>}
      {confirming && (
        <Dialog title={t("sync.web.confirm")} onClose={() => setConfirming(false)}>
          <div className="sync-dialog-actions">
            <button type="button" className="secondary" onClick={() => setConfirming(false)}>{t("sync.web.confirm.keep")}</button>
            <button
              type="button"
              className="primary"
              onClick={() => {
                setConfirming(false);
                void change(false);
              }}
            >
              {t("sync.web.confirm.remove")}
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
