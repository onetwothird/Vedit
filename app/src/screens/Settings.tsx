import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/auth";
import Avatar from "../components/Avatar";
import Screen from "../components/Screen";
import { engine, isTauri } from "../engine/client";
import { useJob } from "../engine/events";
import type { Brand, Language, ModelStatus, Platform } from "../engine/types";
import { t } from "../i18n/t";
import { baseName, loadAiKey, pickFileNative, pickFolderNative, removeAiKey, saveAiKey } from "../lib/native";
import {
  getAiProvider, getLanguage, getPlatform, getSettings, saveSettings, setAiProvider, setLanguage, setPlatform,
  type AiProvider, type Settings as SettingsState,
} from "../lib/prefs";
import { appVersion, checkForUpdate, restartApp, type FoundUpdate } from "../lib/updates";
import { getTheme, onThemeChange, switchTheme, type Theme } from "../theme/theme";
import poster from "./home/podcast-poster.jpg";

const LANGUAGES: Language[] = ["en", "tl", "taglish"];
const PLATFORMS: Platform[] = ["reels", "shorts", "tiktok"];
const PROVIDERS: AiProvider[] = ["anthropic", "openai"];
const THEMES: Theme[] = ["dark", "light"];

function Seg<T extends string>(props: {
  legend: string;
  name: string;
  options: { value: T; label: string }[];
  value: T;
  onChange(v: T): void;
}) {
  return (
    <fieldset className="field">
      <legend>{props.legend}</legend>
      <div className="seg">
        {props.options.map((o) => (
          <label key={o.value} className="seg-opt">
            <input type="radio" name={props.name} value={o.value} checked={props.value === o.value} onChange={() => props.onChange(o.value)} />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

const SECTIONS = ["account", "general", "ai", "models", "brand", "updates"] as const;
type SectionId = (typeof SECTIONS)[number];

/* Section icons, in the app's stroke style (1.7, round caps). */
const ICONS: Record<SectionId, ReactNode> = {
  account: <><circle cx="12" cy="8" r="3.6" /><path d="M5 20c.6-3.6 3.4-5.6 7-5.6s6.4 2 7 5.6" /></>,
  general: <><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></>,
  ai: <><path d="M10 4.5 11.6 9 16 10.5 11.6 12 10 16.5 8.4 12 4 10.5 8.4 9z" /><path d="M17.5 14.5v5M15 17h5" /></>,
  models: <path d="M4 10.5v3M8 7v10M12 4v16M16 8v8M20 10.5v3" />,
  brand: <><path d="M12 3.5a8.5 8.5 0 1 0 0 17c1 0 1.6-.7 1.6-1.5 0-.9-.6-1.3-.6-2.1 0-.9.7-1.5 1.6-1.5h2.1a3.8 3.8 0 0 0 3.8-3.8c0-4.4-3.8-8.1-8.5-8.1z" /><circle cx="7.8" cy="11" r=".9" /><circle cx="10.5" cy="7.4" r=".9" /><circle cx="15" cy="7.8" r=".9" /></>,
  updates: <><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" /><path d="M19.5 4.5v4h-4" /></>,
};

function Section({ id, title, note, children }: { id: SectionId; title: string; note?: string; children: ReactNode }) {
  const head = useId();
  return (
    <section className="set-sec" id={id} aria-labelledby={head} tabIndex={-1}>
      <header className="set-head">
        <span className="set-icon" aria-hidden="true">
          <SectionIcon id={id} />
        </span>
        <h2 id={head}>{title}</h2>
        {note && <p className="set-note">{note}</p>}
      </header>
      {children}
    </section>
  );
}

// ---- account ------------------------------------------------------------------------------------------------------

function AccountSection() {
  const { account, signOut } = useAuth();
  const navigate = useNavigate();
  return (
    <Section id="account" title={t("settings.account")}>
      {account ? (
        <div className="set-account">
          <Avatar account={account} size="lg" />
          <span className="who">
            <span className="who-name">{account.name}</span>
            {account.email && <span className="who-email" title={account.email}>{account.email}</span>}
            <span className={`plan ${account.plan}`}>{t(`menu.plan.${account.plan}`)}</span>
          </span>
          <button
            type="button"
            className="secondary"
            onClick={async () => {
              await signOut();
              navigate("/signin");
            }}
          >
            {t("menu.account.signout")}
          </button>
        </div>
      ) : (
        <div className="set-account out">
          <span className="set-note">{t("settings.account.signedout")}</span>
          <Link className="primary" to="/signin">{t("settings.account.signin")}</Link>
        </div>
      )}
    </Section>
  );
}

// ---- general ------------------------------------------------------------------------------------------------------

function GeneralSection() {
  const [language, setLang] = useState<Language>(getLanguage);
  const [platform, setPlat] = useState<Platform>(getPlatform);
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const [settings, setSettings] = useState<SettingsState>(getSettings);
  const desktop = isTauri();

  useEffect(() => onThemeChange(setThemeState), []);

  function update(next: SettingsState) {
    setSettings(next);
    saveSettings(next);
  }

  async function chooseFolder() {
    try {
      const path = await pickFolderNative();
      if (path) update({ outputMode: "folder", outputFolder: path });
    } catch {
      // the dialog failed to open: nothing changes
    }
  }

  return (
    <Section id="general" title={t("settings.general")}>
      <div className="set-grid">
        <Seg<Language>
          legend={t("settings.language")}
          name="language"
          options={LANGUAGES.map((l) => ({ value: l, label: t(`import.language.${l}`) }))}
          value={language}
          onChange={(v) => {
            setLang(v);
            setLanguage(v);
          }}
        />
        <Seg<Platform>
          legend={t("settings.platform")}
          name="platform"
          options={PLATFORMS.map((p) => ({ value: p, label: t(`import.platform.${p}`) }))}
          value={platform}
          onChange={(v) => {
            setPlat(v);
            setPlatform(v);
          }}
        />
        <Seg<Theme>
          legend={t("settings.theme")}
          name="theme"
          options={THEMES.map((th) => ({ value: th, label: t(`settings.theme.${th}`) }))}
          value={theme}
          onChange={switchTheme}
        />
        <fieldset className="field">
          <legend>{t("settings.output")}</legend>
          <div className="seg">
            <label className="seg-opt">
              <input type="radio" name="output" checked={settings.outputMode === "next"} onChange={() => update({ ...settings, outputMode: "next" })} />
              <span>{t("settings.output.next")}</span>
            </label>
            <label className="seg-opt">
              <input
                type="radio"
                name="output"
                checked={settings.outputMode === "folder"}
                disabled={!desktop}
                onChange={() => (settings.outputFolder ? update({ ...settings, outputMode: "folder" }) : void chooseFolder())}
                onClick={() => settings.outputMode === "folder" && desktop && void chooseFolder()}
              />
              <span>{t("settings.output.folder")}</span>
            </label>
          </div>
          {settings.outputMode === "folder" && settings.outputFolder && (
            <p className="set-note mono-line" title={settings.outputFolder}>{t("settings.output.chosen", { path: settings.outputFolder })}</p>
          )}
          {!desktop && <p className="set-note">{t("settings.output.browser")}</p>}
        </fieldset>
      </div>
    </Section>
  );
}

// ---- AI key -------------------------------------------------------------------------------------------------------

function AiSection() {
  const [provider, setProvider] = useState<AiProvider>(() => getAiProvider() ?? "anthropic");
  const [key, setKey] = useState(""); // what is being typed; a saved key is never put back into the field
  const [saved, setSaved] = useState(false);
  const [shown, setShown] = useState(false);
  const [status, setStatus] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const inputId = useId();

  useEffect(() => {
    let live = true;
    setKey("");
    setStatus(null);
    loadAiKey(provider).then(
      (k) => live && setSaved(!!k),
      () => live && setSaved(false),
    );
    return () => {
      live = false;
    };
  }, [provider]);

  const say = (text: string, bad = false) => setStatus({ text, bad });

  async function save() {
    const k = key.trim();
    if (!k) return say(t("settings.ai.empty"), true);
    try {
      await saveAiKey(provider, k);
      setAiProvider(provider);
      setKey("");
      setSaved(true);
      say(t("settings.ai.saved"));
    } catch {
      say(t("settings.ai.err.store"), true);
    }
  }

  async function remove() {
    try {
      await removeAiKey(provider);
      setSaved(false);
      say(t("settings.ai.removed"));
    } catch {
      say(t("settings.ai.err.store"), true);
    }
  }

  async function test() {
    let k = key.trim();
    if (!k) {
      try {
        k = (await loadAiKey(provider)) ?? "";
      } catch {
        return say(t("settings.ai.err.store"), true);
      }
    }
    if (!k) return say(t("settings.ai.empty"), true);
    setBusy(true);
    say(t("settings.ai.testing"));
    try {
      const res = await engine.testAi({ provider, api_key: k });
      if (res.ok) say(t("settings.ai.ok"));
      else if (res.error === "rejected" || res.error === "network") say(t(`settings.ai.err.${res.error}`), true);
      else say(t("settings.ai.err.generic", { code: res.error }), true);
    } catch {
      say(t("settings.ai.err.engine"), true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section id="ai" title={t("settings.ai")} note={t("settings.ai.note")}>
      <Seg<AiProvider>
        legend={t("settings.ai.provider")}
        name="provider"
        options={PROVIDERS.map((p) => ({ value: p, label: t(`settings.ai.${p}`) }))}
        value={provider}
        onChange={(p) => {
          setProvider(p);
          setAiProvider(p);
        }}
      />
      <div className="field">
        <label htmlFor={inputId} className="field-label">{t("settings.ai.key")}</label>
        <div className="set-key">
          <input
            id={inputId}
            className="ed-input"
            type={shown ? "text" : "password"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={saved ? t("settings.ai.placeholder") : ""}
            autoComplete="off"
            spellCheck={false}
          />
          <button type="button" className="secondary" aria-pressed={shown} onClick={() => setShown((s) => !s)}>
            {shown ? t("settings.ai.hide") : t("settings.ai.show")}
          </button>
        </div>
      </div>
      <div className="row wrap">
        <button type="button" className="primary" onClick={save} disabled={!key.trim()}>{t("settings.ai.save")}</button>
        <button type="button" className="secondary" onClick={test} disabled={busy}>{t("settings.ai.test")}</button>
        <button type="button" className="secondary" onClick={remove} disabled={!saved}>{t("settings.ai.remove")}</button>
      </div>
      <p className={status?.bad ? "error" : "set-note"} role="status">
        {status?.text ?? (saved ? t("settings.ai.saved") : t("settings.ai.none"))}
      </p>
    </Section>
  );
}

// ---- language models ----------------------------------------------------------------------------------------------

const sizeText = (mb: number): string => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`);

function ModelRow({ name, info, onChanged, onError }: {
  name: string;
  info: ModelStatus[string];
  onChanged(next: ModelStatus): void;
  onError(msg: string): void;
}) {
  const [jobId, setJobId] = useState<string | undefined>();
  const [working, setWorking] = useState(false);
  const job = useJob(jobId);
  const named = t(`settings.model.${name}`);
  const label = named === `settings.model.${name}` ? name : named;
  const running = job?.state === "queued" || job?.state === "running";
  const pct = Math.round(job?.pct ?? 0);

  useEffect(() => {
    if (!job) return;
    if (job.state === "done") {
      setJobId(undefined);
      engine.models().then(onChanged, () => undefined);
    } else if (job.state === "failed" || job.state === "cancelled") {
      setJobId(undefined);
      onError(t("settings.model.failed"));
    }
    // onChanged and onError only set state in the parent
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.state]);

  async function download() {
    setWorking(true);
    try {
      const j = await engine.downloadModel(name);
      setJobId(j.id);
    } catch {
      onError(t("settings.model.failed"));
    } finally {
      setWorking(false);
    }
  }

  async function remove() {
    setWorking(true);
    try {
      onChanged(await engine.deleteModel(name));
    } catch (e) {
      onError((e as { code?: string }).code === "busy" ? t("settings.model.busy") : t("settings.model.failed"));
    } finally {
      setWorking(false);
    }
  }

  return (
    <li className={`model-row${info.installed ? " installed" : ""}`}>
      <svg className="model-glyph" viewBox="0 0 20 20" aria-hidden="true">
        {[5, 9, 14, 7, 11, 4].map((h, i) => <rect key={i} x={1.5 + i * 3} y={10 - h / 2} width="1.8" height={h} rx=".9" />)}
      </svg>
      <div className="model-info">
        <span className="model-name">{label}</span>
        <span className="model-size mono">{sizeText(info.size_mb)}</span>
      </div>
      {running ? (
        <div className="model-progress">
          <div className="meter" role="progressbar" aria-label={t("settings.model.progress", { name: label })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <i style={{ width: `${pct}%` }} />
          </div>
          <span className="mono">{pct}%</span>
        </div>
      ) : (
        <>
          <span className={`model-state${info.installed ? " ok" : ""}`}>{info.installed ? t("settings.model.installed") : t("settings.model.missing")}</span>
          {info.installed ? (
            <button type="button" className="secondary" onClick={remove} disabled={working}>{t("settings.model.delete")}</button>
          ) : (
            <button type="button" className="secondary" onClick={download} disabled={working}>{t("settings.model.download")}</button>
          )}
        </>
      )}
    </li>
  );
}

function ModelsSection() {
  const [models, setModels] = useState<ModelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    engine.models().then(
      (m) => live && setModels(m),
      () => live && setError(t("settings.models.error")),
    );
    return () => {
      live = false;
    };
  }, []);

  return (
    <Section id="models" title={t("settings.models")} note={t("settings.models.note")}>
      {models ? (
        <ul className="model-list">
          {Object.entries(models).map(([name, info]) => (
            <ModelRow
              key={name}
              name={name}
              info={info}
              onChanged={(m) => {
                setModels(m);
                setError(null);
              }}
              onError={setError}
            />
          ))}
        </ul>
      ) : (
        !error && <p className="set-note">{t("settings.models.loading")}</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </Section>
  );
}

// ---- brand kit ----------------------------------------------------------------------------------------------------

const EMPTY_BRAND: Brand = { font: "", caption_color: "", highlight: "", logo: "", intro: "", outro: "", music: "" };
type FileKey = "logo" | "intro" | "outro" | "music";
const FILE_KINDS: Record<FileKey, { name: string; extensions: string[]; accept: string }> = {
  logo: { name: "settings.dialog.image", extensions: ["png", "jpg", "jpeg", "webp", "svg"], accept: "image/*" },
  intro: { name: "settings.dialog.video", extensions: ["mp4", "mov", "m4v", "mkv", "webm"], accept: "video/*" },
  outro: { name: "settings.dialog.video", extensions: ["mp4", "mov", "m4v", "mkv", "webm"], accept: "video/*" },
  music: { name: "settings.dialog.audio", extensions: ["mp3", "m4a", "aac", "wav", "flac", "ogg"], accept: "audio/*" },
};

function FileField({ id, label, value, kind, onChange }: { id: string; label: string; value: string; kind: FileKey; onChange(v: string): void }) {
  const input = useRef<HTMLInputElement>(null);
  const spec = FILE_KINDS[kind];

  async function choose() {
    if (isTauri()) {
      try {
        const path = await pickFileNative(t(spec.name), spec.extensions);
        if (path) onChange(path);
      } catch {
        // the dialog failed to open: nothing changes
      }
    } else {
      input.current?.click();
    }
  }

  return (
    <div className="field">
      <span className="field-label" id={`${id}-l`}>{label}</span>
      <div className="set-file" role="group" aria-labelledby={`${id}-l`}>
        <span className={`set-file-name${value ? "" : " none"}`} title={value}>{value ? baseName(value) : t("settings.brand.none")}</span>
        <button type="button" className="secondary" onClick={choose}>{t("settings.brand.choose")}</button>
        {value && <button type="button" className="secondary" onClick={() => onChange("")}>{t("settings.brand.clear")}</button>}
        <input
          ref={input}
          id={id}
          type="file"
          accept={spec.accept}
          aria-labelledby={`${id}-l`}
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) onChange(f.name); // a browser has only the name; the desktop app has the full path
          }}
        />
      </div>
    </div>
  );
}

/** Black or white, whichever reads on `hex` (a #rrggbb colour). */
function inkOn(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255];
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#111" : "#fff";
}

const CAPTION_STYLES = ["pop", "box", "clean", "karaoke", "emoji"] as const;
type CaptionStyle = (typeof CAPTION_STYLES)[number];

/**
 * What the brand kit does: a 9:16 frame with a sample caption in the chosen font and colours, drawn in each caption style
 * the editor offers (the style itself is picked per clip, so this choice only changes the preview).
 */
function CaptionPreview({ brand }: { brand: Brand }) {
  const [style, setStyle] = useState<CaptionStyle>("pop");
  const font = brand.font.replace(/["\\;{}]/g, "").trim();
  const color = brand.caption_color || "#ffffff";
  const hl = brand.highlight || "#ffffff";
  const word = t("settings.brand.sample.word");
  let spoken: ReactNode;
  if (style === "clean") spoken = word;
  else if (style === "karaoke") {
    // "Ang" is said, "galing" is half way through filling, the rest waits
    spoken = <span className="cap-fill" style={{ backgroundImage: `linear-gradient(90deg, ${hl} 55%, ${color} 55%)` }}>{word}</span>;
  } else if (style === "box") spoken = <span style={{ color: hl }}>{word}</span>;
  else spoken = <span className="cap-hl" style={{ background: hl, color: inkOn(hl) }}>{word}</span>;
  return (
    <figure className="cap-preview" data-style={style}>
      <div className="cap-frame" aria-hidden="true">
        <img src={poster} alt="" loading="lazy" decoding="async" />
        <p className="cap-line" style={{ color, fontFamily: font ? `"${font}", var(--sans)` : undefined }}>
          <span className="cap-text">
            {style === "karaoke" ? <span style={{ color: hl }}>{t("settings.brand.sample.before")}</span> : t("settings.brand.sample.before")}{" "}
            {spoken}
            {style === "emoji" && <span className="cap-emoji"> 🔥</span>}{" "}
            {t("settings.brand.sample.after")}
          </span>
        </p>
      </div>
      <div className="cap-styles">
        <Seg<CaptionStyle>
          legend={t("settings.brand.style")} name="cap-preview-style" value={style} onChange={setStyle}
          options={CAPTION_STYLES.map((s) => ({ value: s, label: t(`edit.cap.style.${s}`) }))}
        />
      </div>
      <figcaption>{t("settings.brand.preview")}</figcaption>
    </figure>
  );
}

function BrandSection() {
  const [brand, setBrand] = useState<Brand>(EMPTY_BRAND);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const base = useId();

  useEffect(() => {
    let live = true;
    engine.brand().then(
      (b) => {
        if (!live) return;
        setBrand({ ...EMPTY_BRAND, ...b });
        setLoaded(true);
      },
      () => live && setStatus({ text: t("settings.brand.loaderror"), bad: true }),
    );
    return () => {
      live = false;
    };
  }, []);

  const set = (patch: Partial<Brand>) => {
    setBrand((b) => ({ ...b, ...patch }));
    setStatus(null);
  };

  async function save() {
    setBusy(true);
    try {
      setBrand(await engine.saveBrand(brand));
      setStatus({ text: t("settings.brand.saved"), bad: false });
    } catch {
      setStatus({ text: t("settings.brand.error"), bad: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section id="brand" title={t("settings.brand")} note={t("settings.brand.note")}>
      <div className="brand-kit">
      <div className="set-grid">
        <div className="field">
          <label className="field-label" htmlFor={`${base}-font`}>{t("settings.brand.font")}</label>
          <input id={`${base}-font`} className="ed-input" value={brand.font} onChange={(e) => set({ font: e.target.value })} autoComplete="off" />
        </div>
        <FileField id={`${base}-logo`} label={t("settings.brand.logo")} kind="logo" value={brand.logo} onChange={(v) => set({ logo: v })} />
        <div className="field">
          <label className="field-label" htmlFor={`${base}-color`}>{t("settings.brand.color")}</label>
          <div className="set-color">
            <input id={`${base}-color`} className="ed-color" type="color" value={brand.caption_color || "#ffffff"} onChange={(e) => set({ caption_color: e.target.value })} />
            <span className="mono">{brand.caption_color || t("settings.brand.none")}</span>
          </div>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${base}-hl`}>{t("settings.brand.highlight")}</label>
          <div className="set-color">
            <input id={`${base}-hl`} className="ed-color" type="color" value={brand.highlight || "#ffffff"} onChange={(e) => set({ highlight: e.target.value })} />
            <span className="mono">{brand.highlight || t("settings.brand.none")}</span>
          </div>
        </div>
        <FileField id={`${base}-intro`} label={t("settings.brand.intro")} kind="intro" value={brand.intro} onChange={(v) => set({ intro: v })} />
        <FileField id={`${base}-outro`} label={t("settings.brand.outro")} kind="outro" value={brand.outro} onChange={(v) => set({ outro: v })} />
        <FileField id={`${base}-music`} label={t("settings.brand.music")} kind="music" value={brand.music} onChange={(v) => set({ music: v })} />
      </div>
      <CaptionPreview brand={brand} />
      </div>
      <div className="row wrap">
        <button type="button" className="primary" onClick={save} disabled={busy || !loaded}>{t("settings.brand.save")}</button>
      </div>
      {status && <p className={status.bad ? "error" : "set-note"} role="status">{status.text}</p>}
    </Section>
  );
}

// ---- updates ------------------------------------------------------------------------------------------------------

type UpdateState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "latest" }
  | { kind: "found"; update: FoundUpdate }
  | { kind: "installing" }
  | { kind: "ready" }
  | { kind: "error" };

function UpdatesSection() {
  const [version, setVersion] = useState("");
  const [state, setState] = useState<UpdateState>({ kind: "idle" });
  const desktop = isTauri();

  useEffect(() => {
    let live = true;
    void appVersion().then((v) => live && setVersion(v));
    return () => {
      live = false;
    };
  }, []);

  async function check() {
    setState({ kind: "checking" });
    try {
      const update = await checkForUpdate();
      setState(update ? { kind: "found", update } : { kind: "latest" });
    } catch {
      setState({ kind: "error" });
    }
  }

  async function install(update: FoundUpdate) {
    setState({ kind: "installing" });
    try {
      await update.install();
      setState({ kind: "ready" });
    } catch {
      setState({ kind: "error" });
    }
  }

  const message = !desktop
    ? t("settings.updates.browser")
    : state.kind === "latest"
      ? t("settings.updates.latest")
      : state.kind === "found"
        ? t("settings.updates.found", { v: state.update.version })
        : state.kind === "ready"
          ? t("settings.updates.ready")
          : state.kind === "error"
            ? t("settings.updates.error")
            : "";

  return (
    <Section id="updates" title={t("settings.updates")}>
      <p className="set-version">{version && t("settings.updates.version", { v: version })}</p>
      <div className="row wrap">
        {state.kind === "ready" ? (
          <button type="button" className="primary" onClick={() => void restartApp()}>{t("settings.updates.restart")}</button>
        ) : state.kind === "found" ? (
          <button type="button" className="primary" onClick={() => void install(state.update)}>{t("settings.updates.install")}</button>
        ) : (
          <button type="button" className="secondary" onClick={check} disabled={!desktop || state.kind === "checking" || state.kind === "installing"}>
            {state.kind === "checking" ? t("settings.updates.checking") : state.kind === "installing" ? t("settings.updates.installing") : t("settings.updates.check")}
          </button>
        )}
      </div>
      <p className={state.kind === "error" ? "error" : "set-note"} role="status">{message}</p>
    </Section>
  );
}

function SectionIcon({ id }: { id: SectionId }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{ICONS[id]}</svg>
  );
}

/** The section list beside Settings: where you are, and a jump to the others. */
function SectionNav() {
  const [current, setCurrent] = useState<SectionId>("account");
  // after a click the list keeps the chosen section while the page scrolls to it (the last ones can't reach the top)
  const held = useRef(0);
  useEffect(() => {
    // The current section is the last one whose top has passed a line 30% down the window. At the end of the page the
    // last section wins, since the short ones at the bottom can never reach that line.
    let frame = 0;
    const update = () => {
      frame = 0;
      if (Date.now() < held.current) return;
      const first = document.getElementById(SECTIONS[0]);
      let box = first?.parentElement ?? null;
      while (box && !(box.scrollHeight > box.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
      const top = box ? box.getBoundingClientRect().top : 0;
      const line = top + (window.innerHeight - top) * 0.3;
      const tops = SECTIONS.map((id) => document.getElementById(id)?.getBoundingClientRect().top ?? Infinity);
      let i = 0;
      tops.forEach((t, k) => {
        if (t <= line) i = k;
      });
      // a short section whose heading is still in full view at the top stays current, though the next one has passed the line
      if (i > 0 && tops[i - 1] >= top - 4) i -= 1;
      let next: SectionId = SECTIONS[i];
      const last = document.getElementById(SECTIONS[SECTIONS.length - 1]);
      if (last && last.getBoundingClientRect().bottom <= window.innerHeight + 2) next = SECTIONS[SECTIONS.length - 1];
      setCurrent(next);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    // the page scrolls inside the app's content area, so listen to every scroll on the way up
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    update();
    return () => {
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  function go(id: SectionId) {
    const el = document.getElementById(id);
    if (!el) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
    el.focus({ preventScroll: true });
    held.current = Date.now() + 1200;
    setCurrent(id);
  }

  return (
    <nav className="set-nav" aria-label={t("settings.nav")}>
      <ul>
        {SECTIONS.map((id) => (
          <li key={id}>
            <button type="button" className={id === current ? "on" : undefined} aria-current={id === current ? "location" : undefined} onClick={() => go(id)}>
              <SectionIcon id={id} />
              <span>{t(`settings.${id}`)}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export default function Settings() {
  return (
    <Screen>
      <div className="imp set-page">
        <h1 className="title imp-title">{t("settings.title")}</h1>
        <div className="set-layout">
          <SectionNav />
          <div className="settings">
            <AccountSection />
            <GeneralSection />
            <AiSection />
            <ModelsSection />
            <BrandSection />
            <UpdatesSection />
          </div>
        </div>
      </div>
    </Screen>
  );
}
