import { useId, useState } from "react";
import { isTauri } from "../engine/client";
import type { BrollSuggestion, BrollSuggestions, Overlay } from "../engine/types";
import { brollAt } from "../edit/overlays";
import { t } from "../i18n/t";
import { pickFolderNative } from "../lib/native";
import { formatClock } from "../lib/time";
import "../broll.css";

/* Suggest b-roll: the engine reads the clip's words, finds where a thing or place is named, and matches photos and
   videos in a folder the user picks (by file name, and by meaning when an AI key is set). Nothing is placed until
   the user adds a suggestion; Skip only hides it. */

const FOLDER_KEY = "vedit.broll.folder";

function lastFolder(): string {
  try {
    return localStorage.getItem(FOLDER_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberFolder(folder: string): void {
  try {
    localStorage.setItem(FOLDER_KEY, folder);
  } catch {
    /* storage blocked: the field just starts empty next time */
  }
}

export interface BrollSuggestProps {
  suggest(folder: string): Promise<BrollSuggestions>;
  /** Adds the layers in one edit (one undo step). */
  onAdd(items: Overlay[]): void;
  t0: number;
  t1: number;
}

type State =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "error"; msg: string }
  | { kind: "done"; files: number; aiFailed: boolean; items: BrollSuggestion[]; total: number };

export function BrollSuggest({ suggest, onAdd, t0, t1 }: BrollSuggestProps) {
  const id = useId();
  const [folder, setFolder] = useState(lastFolder);
  const [state, setState] = useState<State>({ kind: "idle" });

  async function run(from = folder) {
    const f = from.trim();
    if (!f) return;
    rememberFolder(f);
    setState({ kind: "busy" });
    try {
      const r = await suggest(f);
      setState({ kind: "done", files: r.files, aiFailed: r.ai === "ai_failed", items: r.suggestions, total: r.suggestions.length });
    } catch (e) {
      setState({ kind: "error", msg: e instanceof Error ? e.message : String(e) });
    }
  }

  async function choose() {
    const picked = await pickFolderNative().catch(() => null);
    if (!picked) return;
    setFolder(picked);
    void run(picked);
  }

  const drop = (s: BrollSuggestion) =>
    setState((st) => (st.kind === "done" ? { ...st, items: st.items.filter((x) => x !== s) } : st));
  const toLayer = (s: BrollSuggestion) => brollAt(s.path, s.type, s.t, s.duration, t0, t1);

  return (
    <section className="bs" aria-labelledby={`${id}-h`}>
      <h3 className="bs-title" id={`${id}-h`}>{t("edit.bs.title")}</h3>
      <p className="hint">{t("edit.bs.hint")}</p>
      <form
        className="bs-form"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label className="ed-label" htmlFor={`${id}-f`}>{t("edit.bs.folder")}</label>
        <div className="bs-row">
          <input
            id={`${id}-f`}
            className="bs-input mono"
            value={folder}
            placeholder={t("edit.bs.folder.ph")}
            spellCheck={false}
            onChange={(e) => setFolder(e.currentTarget.value)}
          />
          {isTauri() && (
            <button type="button" className="secondary" onClick={choose}>{t("edit.bs.choose")}</button>
          )}
        </div>
        <button type="submit" className="primary bs-go" disabled={!folder.trim() || state.kind === "busy"}>
          {state.kind === "busy" ? t("edit.bs.busy") : t("edit.bs.go")}
        </button>
      </form>

      <div aria-live="polite">
        {state.kind === "error" && <p className="error" role="alert">{t("edit.bs.error", { msg: state.msg })}</p>}
        {state.kind === "done" && (
          <>
            {state.aiFailed && <p className="hint">{t("edit.bs.ai_failed")}</p>}
            {state.files === 0 ? (
              <p className="hint">{t("edit.bs.no_files")}</p>
            ) : state.total === 0 ? (
              <p className="hint">{t("edit.bs.none", { n: state.files })}</p>
            ) : state.items.length === 0 ? (
              <p className="hint">{t("edit.bs.all_done")}</p>
            ) : (
              <>
                <div className="bs-head">
                  <span className="meta">{t("edit.bs.found", { n: state.items.length })}</span>
                  {state.items.length > 1 && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        onAdd(state.items.map(toLayer));
                        setState({ ...state, items: [] });
                      }}
                    >
                      {t("edit.bs.add_all")}
                    </button>
                  )}
                </div>
                <ul className="bs-list" aria-label={t("edit.bs.list")}>
                  {state.items.map((s) => (
                    <li key={`${s.path}@${s.t}`} className={`bs-item ${s.type}`}>
                      <div className="bs-what">
                        <span className="ly-name">{s.name}</span>
                        <span className="bs-when mono">
                          {t("edit.bs.when", { at: formatClock(Math.max(0, s.t - t0)), word: s.word })}
                        </span>
                        {s.why && <span className="bs-why">{s.why}</span>}
                      </div>
                      <span className="row">
                        <button type="button" className="secondary" aria-label={t("edit.bs.add.label", { name: s.name })}
                          onClick={() => { onAdd([toLayer(s)]); drop(s); }}>
                          {t("edit.bs.add")}
                        </button>
                        <button type="button" className="secondary" aria-label={t("edit.bs.skip.label", { name: s.name })} onClick={() => drop(s)}>
                          {t("edit.bs.skip")}
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}
