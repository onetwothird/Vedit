import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { shouldOfferSignIn, useAuth } from "../auth/auth";
import FilmFrame from "../components/FilmFrame";
import Screen from "../components/Screen";
import { engine, engineMediaUrl, isMock } from "../engine/client";
import type { Project, Story } from "../engine/types";
import { t } from "../i18n/t";
import { baseName } from "../lib/native";
import { isWhole, newImport, projectHome } from "../lib/projectKind";
import { useFilmstrip } from "../lib/useFilmstrip";
import { BatchArt, ClipArt, PhotosArt, ReelArt, VlogArt } from "./home/HomeArt";
import "./home/home.css";

const TILES = [
  { key: "vlog", Art: VlogArt, to: newImport("vlog") },
  { key: "reel", Art: ReelArt, to: newImport("reel") },
  { key: "photos", Art: PhotosArt, to: "/s/new" },
  { key: "batch", Art: BatchArt, to: "/batch" },
] as const;

type Recent = { status: "loading" } | { status: "error" } | { status: "ready"; projects: Project[]; stories: Story[] };

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateFormat.format(d);
}

export default function Home() {
  const account = useAuth((a) => a.account);
  const [recent, setRecent] = useState<Recent>({ status: "loading" });

  useEffect(() => {
    let live = true;
    Promise.all([engine.recent(), engine.stories().catch(() => [] as Story[])]).then(
      ([projects, stories]) => live && setRecent({ status: "ready", projects, stories }),
      () => live && setRecent({ status: "error" }),
    );
    return () => {
      live = false;
    };
  }, []);

  if (shouldOfferSignIn(account)) return <Navigate to="/signin" replace />;
  return (
    <Screen>
      <h1 className="title">{t("home.title")}</h1>
      <ul className="home-modes" aria-label={t("home.tiles.label")}>
        <li>
          {/* the whole card opens Import (the link stretches over it); the sound button sits on top of it */}
          <div className="mode-hero">
            <Link className="m-link" to="/p/new/import" data-tile>
              <span className="m-title">{t("home.tile.clip.title")}</span>
              <span className="m-desc">{t("home.tile.clip.desc")}</span>
              <span className="m-cta">{t("home.tile.clip.cta")}</span>
            </Link>
            <ClipArt />
          </div>
        </li>
        <li>
          <ul className="mode-soon-row">
            {TILES.map(({ key, Art, to }) => (
              <li key={key}>
                <Link className="mode-soon mode-live" to={to} data-tile>
                  <Art />
                  <span className="m-title">{t(`home.tile.${key}.title`)}</span>
                  <span className="m-desc">{t(`home.tile.${key}.desc`)}</span>
                  <span className="m-go">{t(`home.tile.${key}.cta`)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </li>
      </ul>

      <section className="recent" aria-labelledby="recent-h">
        <h2 id="recent-h">{t("home.recent.title")}</h2>
        {recent.status === "loading" && <p className="note-text">{t("home.recent.loading")}</p>}
        {recent.status === "error" && <p className="note-text">{t("home.recent.error")}</p>}
        {recent.status === "ready" && recent.projects.length + recent.stories.length === 0 && (
          <p className="note-text">{t("home.recent.empty")}</p>
        )}
        {recent.status === "ready" && recent.projects.length + recent.stories.length > 0 && (
          <ul className="recent-list">
            {recent.stories.map((s) => <StoryItem key={s.id} s={s} />)}
            {recent.projects.map((p) => <RecentItem key={p.id} p={p} />)}
          </ul>
        )}
      </section>
    </Screen>
  );
}

/** One recent story: its first photo, its name, then date and scene count. */
function StoryItem({ s }: { s: Story }) {
  const first = s.scenes.find((sc) => sc.kind === "image");
  const url = first && !isMock() ? engineMediaUrl(`/stories/${s.id}/file`, { path: first.media }) : null;
  const name = s.title || t("home.recent.story.untitled");
  const n = s.scenes.length;
  return (
    <li>
      <Link className="recent-item" to={`/s/${s.id}`}>
        <span className="r-thumb r-story">{url ? <img src={url} alt="" /> : <PhotosArt />}</span>
        <span className="r-name" title={name}>{name}</span>
        <span className="r-meta">
          <span>{formatDate(s.updated)}</span>
          <span>{t(n === 1 ? "home.recent.story.one" : "home.recent.story.other", { n })}</span>
        </span>
      </Link>
    </li>
  );
}

/** One recent project: its first frame, file name, then date and clip count underneath. */
function RecentItem({ p }: { p: Project }) {
  const film = useFilmstrip(p.id);
  const n = p.clips.length;
  return (
    <li>
      <Link className="recent-item" to={n > 0 ? projectHome(p) : `/p/${p.id}/import`}>
        <span className="r-thumb">
          <FilmFrame film={film} t={p.clips[0]?.highlight.start ?? 0} fit={{ width: 232, height: 130 }} />
        </span>
        <span className="r-name" title={baseName(p.source)}>{baseName(p.source)}</span>
        <span className="r-meta">
          <span>{formatDate(p.created)}</span>
          <span>{isWhole(p.type) ? t(`home.recent.type.${p.type}`) : n > 0 ? t(n === 1 ? "home.recent.clips.one" : "home.recent.clips.other", { n }) : t("home.recent.noclips")}</span>
        </span>
      </Link>
    </li>
  );
}
