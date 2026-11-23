import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import Screen from "../components/Screen";
import type { StepNumber } from "../components/Steps";
import { engine } from "../engine/client";
import { t } from "../i18n/t";
import { baseName } from "../lib/native";

/** Stands in for screens that later tasks build (Pick clips, Edit, Export, Settings). */
export default function Placeholder({ titleKey, step }: { titleKey: string; step?: StepNumber }) {
  const { id, hid } = useParams();
  const [file, setFile] = useState<string>();
  const [clips, setClips] = useState<number>();

  useEffect(() => {
    if (!id) return;
    let live = true;
    engine.getProject(id).then(
      (p) => {
        if (!live) return;
        setFile(baseName(p.source));
        setClips(p.clips.length);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [id]);

  return (
    <Screen bar={{ fileName: file, saved: !!file, step, editHid: hid }} narrow>
      <h1 className="title">{t(titleKey)}</h1>
      {clips !== undefined && <p className="note-text">{t("placeholder.clips", { n: clips })}</p>}
      <p className="note-text">{t("placeholder.soon")}</p>
    </Screen>
  );
}
