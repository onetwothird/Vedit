import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { shouldOfferSignIn, useAuth } from "../auth/auth";
import FilmFrame from "../components/FilmFrame";
import Screen from "../components/Screen";
import { engine, engineMediaUrl, isMock } from "../engine/client";
import { isWeb } from "../engine/flavor";
import type { CloudProject, Project, Story } from "../engine/types";
import { t } from "../i18n/t";
import { baseName } from "../lib/native";
import { isWhole, newImport, projectHome } from "../lib/projectKind";
import { useFilmstrip } from "../lib/useFilmstrip";
import { projectSyncLabel, type SyncLabel, type SyncView } from "../sync/labels";
import { useSync } from "../sync/sync";
import CloudItem from "./home/CloudProject";
import { BatchArt, ClipArt, PhotosArt, ReelArt, VlogArt } from "./home/HomeArt";
import "./home/home.css";
import "./legal.css";

const TILES = [
  { key: "vlog", Art: VlogArt, to: newImport("vlog") },
  { key: "reel", Art: ReelArt, to: newImport("reel") },
  { key: "photos", Art: PhotosArt, to: "/s/new" },
  { key: "batch", Art: BatchArt, to: "/batch" },
] as const;

type Recent = { status: "loading" } | { status: "error" } | { status: "ready"; projects: Project[]; stories: Story[] };

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
/** 12 fills whole rows at 4, 3 and 2 columns */
const PAGE_SIZE = 12;

type Entry = { kind: "story"; at: string; s: Story } | { kind: "project"; at: string; p: Project } | { kind: "cloud"; at: string; c: CloudProject };

/** Stories, projects and the cloud's projects not on this computer in one list, newest first. */
function entries(projects: Project[], stories: Story[], cloud: CloudProject[] = []): Entry[] {
  const all: Entry[] = [
    ...stories.map((s): Entry => ({ kind: "story", at: s.updated, s })),
    ...projects.map((p): Entry => ({ kind: "project", at: p.created, p })),
    ...cloud.map((c): Entry => ({ kind: "cloud", at: c.updated, c })),
  ];
  const time = (iso: string) => {
    const n = new Date(iso).getTime();
    return Number.isNaN(n) ? 0 : n;
  };
  return all.sort((a, b) => time(b.at) - time(a.at));
}

/** The width a thumbnail box is drawn at, so its frame can fill it. */
function useBoxWidth<T extends HTMLElement>(initial: number): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(1, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function Chevron({ back }: { back?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={back ? "m14.5 6-6 6 6 6" : "m9.5 6 6 6-6 6"} />
    </svg>
  );
}

/** Page buttons for Recent projects: previous, each page, next. */
function Pager({ page, pages, onPage }: { page: number; pages: number; onPage(n: number): void }) {
  return (
    <nav className="pager" aria-label={t("home.recent.pages")}>
      <button type="button" className="pager-btn" aria-label={t("home.recent.prev")} disabled={page === 0} onClick={() => onPage(page - 1)}>
        <Chevron back />
      </button>
      {Array.from({ length: pages }, (_, i) => (
        <button key={i} type="button" className={`pager-btn pager-num${i === page ? " on" : ""}`} aria-current={i === page ? "page" : undefined}
          aria-label={t("home.recent.page", { n: i + 1, total: pages })} onClick={() => onPage(i)}>
          {i + 1}
        </button>
      ))}
      <button type="button" className="pager-btn" aria-label={t("home.recent.next")} disabled={page === pages - 1} onClick={() => onPage(page + 1)}>
        <Chevron />
      </button>
    </nav>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateFormat.format(d);
}

export default function Home() {
  const account = useAuth((a) => a.account);
  const sync = useSync();
  const syncView: SyncView = { available: sync.available, active: sync.running && !!account, states: sync.states, cloudIds: sync.cloudIds, busy: sync.busy };
  const [recent, setRecent] = useState<Recent>({ status: "loading" });
  const [page, setPage] = useState(0);
  const head = useRef<HTMLElement>(null);
  // the website's front page is open to everyone, but there is nothing to list until someone signs in
  const web = isWeb();
  const visitor = web && !account;

  useEffect(() => {
    if (visitor) return;
    let live = true;
    Promise.all([engine.recent(), engine.stories().catch(() => [] as Story[])]).then(
      ([projects, stories]) => live && setRecent({ status: "ready", projects, stories }),
      () => live && setRecent({ status: "error" }),
    );
    return () => {
      live = false;
    };
  }, [visitor]);

  const all = recent.status === "ready" ? entries(recent.projects, recent.stories, syncView.active ? sync.cloudOnly : []) : [];
  const pages = Math.max(1, Math.ceil(all.length / PAGE_SIZE));
  const at = Math.min(page, pages - 1);
  function turn(n: number) {
    setPage(n);
    // keep the list's heading in view so the new page starts where the eye is
    const el = head.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start" });
  }

  if (!web && shouldOfferSignIn(account)) return <Navigate to="/signin" replace />;
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

      {visitor ? (
        <section className="recent" aria-labelledby="visitor-h">
          <div className="recent-head"><h2 id="visitor-h">{t("home.signedout.title")}</h2></div>
          <p className="note-text">{t("home.signedout.note")}</p>
          <Link className="primary inline" to="/signin">{t("home.signedout.signin")}</Link>
        </section>
      ) : (
      <section className="recent" aria-labelledby="recent-h" ref={head}>
        <div className="recent-head">
          <h2 id="recent-h">
            {t("home.recent.title")}
            {all.length > 0 && <span className="recent-count">{all.length}</span>}
          </h2>
          {pages > 1 && <Pager page={at} pages={pages} onPage={turn} />}
        </div>
        {recent.status === "loading" && <p className="note-text">{t("home.recent.loading")}</p>}
        {recent.status === "error" && <p className="note-text">{t("home.recent.error")}</p>}
        {recent.status === "ready" && all.length === 0 && <p className="note-text">{t("home.recent.empty")}</p>}
        {all.length > 0 && (
          <ul className="recent-list">
            {all.slice(at * PAGE_SIZE, (at + 1) * PAGE_SIZE).map((e) =>
              e.kind === "story" ? <StoryItem key={e.s.id} s={e.s} />
                : e.kind === "cloud" ? <CloudItem key={`cloud-${e.c.id}`} c={e.c} date={formatDate(e.c.updated)} />
                  : <RecentItem key={e.p.id} p={e.p} sync={projectSyncLabel(e.p.id, e.p.settings, syncView)} />)}
          </ul>
        )}
      </section>
      )}
      {web && (
        <p className="legal-links">
          <Link to="/terms">{t("legal.terms.link")}</Link>
          <Link to="/privacy">{t("legal.privacy.link")}</Link>
        </p>
      )}
    </Screen>
  );
}

/** A story with no photo yet: stacked frames, in the app's stroke style. */
function StoryGlyph() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="10" y="14" width="28" height="22" rx="3" />
      <path d="M14 10h20M10 36l8-8 6 6 5-5 9 9" />
      <circle cx="19" cy="21" r="2.5" />
    </svg>
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
        <span className="r-thumb r-story">{url ? <img src={url} alt="" /> : <StoryGlyph />}</span>
        <span className="r-name" title={name}>{name}</span>
        <span className="r-meta">
          <span>{formatDate(s.updated)}</span>
          <span>{t(n === 1 ? "home.recent.story.one" : "home.recent.story.other", { n })}</span>
        </span>
      </Link>
    </li>
  );
}

/** Where a project lives and whether it is synced (desktop with sync only). */
function SyncBadges({ label }: { label: SyncLabel }) {
  return (
    <span className="r-sync" role="group" aria-label={t("sync.badges")}>
      <span className={`sync-badge ${label.where}`}>{t(`sync.where.${label.where}`)}</span>
      {label.status && <span className={`sync-status ${label.status}`}>{t(`sync.status.${label.status}`)}</span>}
    </span>
  );
}

/** One recent project: its first frame, file name, then date and clip count underneath. */
function RecentItem({ p, sync }: { p: Project; sync: SyncLabel | null }) {
  const film = useFilmstrip(p.id);
  const [box, w] = useBoxWidth<HTMLSpanElement>(232);
  const n = p.clips.length;
  const name = p.settings.title || baseName(p.source);
  const web = isWeb(); // a project synced from the desktop without its video: the website can only list it
  return (
    <li>
      <Link className="recent-item" to={n > 0 ? projectHome(p) : `/p/${p.id}/import`}>
        <span className="r-thumb" ref={box}>
          <FilmFrame film={film} t={p.clips[0]?.highlight.start ?? 0} fit={{ width: w, height: Math.round((w * 9) / 16) }} />
        </span>
        <span className="r-name" title={name}>{name}</span>
        <span className="r-meta">
          <span>{formatDate(p.created)}</span>
          <span>{isWhole(p.type) ? t(`home.recent.type.${p.type}`) : n > 0 ? t(n === 1 ? "home.recent.clips.one" : "home.recent.clips.other", { n }) : t("home.recent.noclips")}</span>
        </span>
        {sync && <SyncBadges label={sync} />}
        {web && p.has_video_in_cloud === false && <span className="r-novideo">{t("sync.web.novideo")}</span>}
      </Link>
    </li>
  );
}
