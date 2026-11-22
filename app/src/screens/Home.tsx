import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Screen from "../components/Screen";
import { engine } from "../engine/client";
import type { Project } from "../engine/types";
import { t } from "../i18n/t";
import { baseName } from "../lib/native";
import { BatchArt, ClipArt, PhotosArt, ReelArt, VlogArt } from "./home/HomeArt";
import "./home/home.css";

const SOON = [
  { key: "vlog", Art: VlogArt },
  { key: "reel", Art: ReelArt },
  { key: "photos", Art: PhotosArt },
  { key: "batch", Art: BatchArt },
] as const;

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
      <ul className="home-modes" aria-label={t("home.tiles.label")}>
        <li>
          <Link className="mode-hero" to="/p/new/import" data-tile>
            <span>
              <span className="m-title">{t("home.tile.clip.title")}</span>
              <span className="m-desc">{t("home.tile.clip.desc")}</span>
              <span className="m-cta">{t("home.tile.clip.cta")}</span>
            </span>
            <ClipArt />
          </Link>
        </li>
        <li>
          <ul className="mode-soon-row">
            {SOON.map(({ key, Art }) => {
              const title = t(`home.tile.${key}.title`);
              return (
                <li key={key}>
                  <div className="mode-soon" role="group" aria-disabled="true" aria-label={title} data-tile>
                    <Art />
                    <span className="m-title">{title}</span>
                    <span className="m-desc">{t(`home.tile.${key}.desc`)}</span>
                    <span className="m-soon">{t("home.soon")}</span>
                  </div>
                </li>
              );
            })}
          </ul>
        </li>
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
