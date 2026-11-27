import { t } from "../i18n/t";
import { syncStore, useSync } from "../sync/sync";

/** What sync has to say (a conflict copy, a project uploaded again), until dismissed. */
export default function SyncToasts() {
  const { toasts } = useSync();
  if (!toasts.length) return null;
  return (
    <div className="sync-toasts" role="status">
      {toasts.map((m) => (
        <p key={m.id} className="sync-toast">
          <span>{m.message}</span>
          <button type="button" className="secondary" onClick={() => syncStore.dismissToast(m.id)}>{t("sync.toast.dismiss")}</button>
        </p>
      ))}
    </div>
  );
}
