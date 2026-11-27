import { Link } from "react-router-dom";
import { isWeb } from "../engine/flavor";
import type { Project } from "../engine/types";
import { t } from "../i18n/t";
import Screen from "./Screen";
import type { TopBarProps } from "./TopBar";

/** The website, for a project synced from the desktop without its video: nothing here can play or change it. */
export const needsDesktop = (p: Project | null): boolean => isWeb() && p?.has_video_in_cloud === false;

/** Stands in for a project screen (player, editing, export) whose video is only on the desktop. */
export default function NoCloudVideo({ bar, title }: { bar: TopBarProps; title: string }) {
  return (
    <Screen bar={bar}>
      <h1 className="title">{title}</h1>
      <p className="note-text" role="status">{t("sync.web.novideo")}</p>
      <Link className="secondary inline" to="/">{t("sync.web.novideo.home")}</Link>
    </Screen>
  );
}
