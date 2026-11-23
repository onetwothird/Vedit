import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { initials, useAuth } from "../auth/auth";
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

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section className="set-sec" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {note && <p className="set-note">{note}</p>}
      {children}
    </section>
  );
}

// ---- account ------------------------------------------------------------------------------------------------------

function AccountSection() {
  const { account, signOut } = useAuth();
  const navigate = useNavigate();
  return (
    <Section title={t("settings.account")}>
      {account ? (
        <div className="set-account">
          <span className="avatar lg" aria-hidden="true">{initials(account.name)}</span>
          <span className="who">
            <span className="who-name">{account.name}</span>
            <span className="who-email" title={account.email}>{account.email}</span>
          </span>
          <span className={`plan ${account.plan}`}>{t(`menu.plan.${account.plan}`)}</span>
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
        <div className="set-account">
          <span className="set-note">{t("settings.account.signedout")}</span>
          <Link className="secondary" to="/signin">{t("settings.account.signin")}</Link>
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
    <Section title={t("settings.general")}>
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
    <Section title={t("settings.ai")} note={t("settings.ai.note")}>
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
            placeholder={saved ? "••••••••••••" : ""}
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
    <li className="model-row">
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
    <Section title={t("settings.models")} note={t("settings.models.note")}>
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
  logo: { name: "Image", extensions: ["png", "jpg", "jpeg", "webp", "svg"], accept: "image/*" },
  intro: { name: "Video", extensions: ["mp4", "mov", "m4v", "mkv", "webm"], accept: "video/*" },
  outro: { name: "Video", extensions: ["mp4", "mov", "m4v", "mkv", "webm"], accept: "video/*" },
  music: { name: "Audio", extensions: ["mp3", "m4a", "aac", "wav", "flac", "ogg"], accept: "audio/*" },
};

function FileField({ id, label, value, kind, onChange }: { id: string; label: string; value: string; kind: FileKey; onChange(v: string): void }) {
  const input = useRef<HTMLInputElement>(null);
  const spec = FILE_KINDS[kind];

  async function choose() {
    if (isTauri()) {
      try {
        const path = await pickFileNative(spec.name, spec.extensions);
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
    <Section title={t("settings.brand")} note={t("settings.brand.note")}>
      <div className="set-grid">
        <div className="field">
          <label className="field-label" htmlFor={`${base}-font`}>{t("settings.brand.font")}</label>
          <input id={`${base}-font`} className="ed-input" value={brand.font} onChange={(e) => set({ font: e.target.value })} autoComplete="off" />
        </div>
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
            <input id={`${base}-hl`} className="ed-color" type="color" value={brand.highlight || "#ffc72c"} onChange={(e) => set({ highlight: e.target.value })} />
            <span className="mono">{brand.highlight || t("settings.brand.none")}</span>
          </div>
        </div>
        <FileField id={`${base}-logo`} label={t("settings.brand.logo")} kind="logo" value={brand.logo} onChange={(v) => set({ logo: v })} />
        <FileField id={`${base}-intro`} label={t("settings.brand.intro")} kind="intro" value={brand.intro} onChange={(v) => set({ intro: v })} />
        <FileField id={`${base}-outro`} label={t("settings.brand.outro")} kind="outro" value={brand.outro} onChange={(v) => set({ outro: v })} />
        <FileField id={`${base}-music`} label={t("settings.brand.music")} kind="music" value={brand.music} onChange={(v) => set({ music: v })} />
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
    <Section title={t("settings.updates")}>
      <p className="set-note">{version && t("settings.updates.version", { v: version })}</p>
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

export default function Settings() {
  return (
    <Screen narrow>
      <h1 className="title">{t("settings.title")}</h1>
      <div className="settings">
        <AccountSection />
        <GeneralSection />
        <AiSection />
        <ModelsSection />
        <BrandSection />
        <UpdatesSection />
      </div>
    </Screen>
  );
}
