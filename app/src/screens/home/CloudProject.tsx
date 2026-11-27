import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Dialog from "../../components/Dialog";
import { engine } from "../../engine/client";
import type { CloudProject } from "../../engine/types";
import { t } from "../../i18n/t";
import { projectHome } from "../../lib/projectKind";
import { sizeText } from "../../sync/labels";
import { cloudProjectOpened, openCloudProject, waitForSyncJob } from "../../sync/sync";

function CloudGlyph() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 34h19a8 8 0 0 0 .8-15.96A11 11 0 0 0 13.6 21 6.5 6.5 0 0 0 15 34Z" />
    </svg>
  );
}

/** A project that is only in the cloud (made on the website, or on another computer): opening it downloads its video
    once, into Videos/Vedit/Cloud, and then opens it here. */
export default function CloudItem({ c, date }: { c: CloudProject; date: string }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [pct, setPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setError(null);
    setPct(0);
    try {
      const r = await openCloudProject(c.id);
      let pid = r.project?.id;
      if (r.job) {
        const done = await waitForSyncJob(r.job, (p) => setPct(Math.round(p)));
        pid = (done.result as { project?: string } | null)?.project;
        if (pid) await cloudProjectOpened(c.id);
      }
      if (!pid) throw new Error(t("sync.cloud.notopened"));
      navigate(projectHome(await engine.getProject(pid)));
    } catch (e) {
      setPct(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const close = () => {
    if (pct === null) setOpen(false); // a download under way keeps the dialog open until it ends
  };

  return (
    <li>
      <button type="button" className="recent-item cloud-item" onClick={() => setOpen(true)}>
        <span className="r-thumb r-story"><CloudGlyph /></span>
        <span className="r-name" title={c.title}>{c.title}</span>
        <span className="r-meta">
          <span>{date}</span>
        </span>
        <span className="r-sync" role="group" aria-label={t("sync.badges")}>
          <span className="sync-badge cloud">{t("sync.where.cloud")}</span>
        </span>
      </button>
      {open && (
        <Dialog
          title={c.expired ? t("sync.cloud.expired") : !c.has_video_in_cloud ? t("sync.cloud.novideo")
            : c.video_bytes ? t("sync.cloud.download", { size: sizeText(c.video_bytes) }) : t("sync.cloud.nosize")}
          onClose={close}
        >
          <p className="sync-dialog-sub">{c.title}</p>
          {pct !== null && (
            <p className="sync-dialog-progress">
              <progress max={100} value={pct} aria-label={t("sync.cloud.progress")} />
              <span role="status">{t("sync.cloud.downloading", { pct })}</span>
            </p>
          )}
          {error && <p className="error" role="alert">{t("sync.cloud.error", { msg: error })}</p>}
          <div className="sync-dialog-actions">
            {c.expired || !c.has_video_in_cloud ? (
              <button type="button" className="secondary" onClick={close}>{t("sync.cloud.close")}</button>
            ) : (
              <>
                <button type="button" className="secondary" onClick={close} disabled={pct !== null}>{t("sync.cloud.cancel")}</button>
                <button type="button" className="primary" onClick={() => void download()} disabled={pct !== null}>{t("sync.cloud.download.action")}</button>
              </>
            )}
          </div>
        </Dialog>
      )}
    </li>
  );
}
