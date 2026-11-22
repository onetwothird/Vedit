import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Screen from "../components/Screen";
import { engine } from "../engine/client";
import type { Project } from "../engine/types";
import { t } from "../i18n/t";
import { baseName } from "../lib/native";

const TILES = ["clip", "vlog", "reel", "photos", "batch"] as const;

type Recent = { status: "loading" } | { status: "error" } | { status: "ready"; projects: Project[] };

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateFormat.format(d);
}

export default function Home() {
  const [recent, setRecent] = useState<Recent>({ status: "loading" });

  useEffect(() => {
    let live = true;
    engine.recent().then(
      (projects) => live && setRecent({ status: "ready", projects }),
      () => live && setRecent({ status: "error" }),
    );
    return () => {
      live = false;
    };
  }, []);

  return (
    <Screen>
      <h1 className="title">{t("home.title")}</h1>
      <ul className="tiles" aria-label={t("home.tiles.label")}>
        {TILES.map((key) => {
          const title = t(`home.tile.${key}.title`);
          const desc = t(`home.tile.${key}.desc`);
          return (
            <li key={key}>
              {key === "clip" ? (
                <Link className="tile active" to="/p/new/import" data-tile>
                  <span className="tile-title">{title}</span>
                  <span className="tile-desc">{desc}</span>
                  <span className="tile-go" aria-hidden="true">→</span>
                </Link>
              ) : (
                <div className="tile soon" role="group" aria-disabled="true" aria-label={title} data-tile>
                  <span className="tile-title">{title}</span>
                  <span className="tile-desc">{desc}</span>
                  <span className="badge">{t("home.soon")}</span>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <section className="recent" aria-labelledby="recent-h">
        <h2 id="recent-h">{t("home.recent.title")}</h2>
        {recent.status === "loading" && <p className="note-text">{t("home.recent.loading")}</p>}
        {recent.status === "error" && <p className="note-text">{t("home.recent.error")}</p>}
        {recent.status === "ready" && recent.projects.length === 0 && (
          <p className="note-text">{t("home.recent.empty")}</p>
        )}
        {recent.status === "ready" && recent.projects.length > 0 && (
          <ul className="recent-list">
            {recent.projects.map((p) => {
              const n = p.clips.length;
              return (
                <li key={p.id}>
                  <Link className="recent-item" to={n > 0 ? `/p/${p.id}/pick` : `/p/${p.id}/import`}>
                    <span className="r-name">{baseName(p.source)}</span>
                    <span className="r-meta mono">{formatDate(p.created)}</span>
                    <span className="r-meta mono">
                      {n > 0 ? t(n === 1 ? "home.recent.clips.one" : "home.recent.clips.other", { n }) : t("home.recent.noclips")}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </Screen>
  );
}
