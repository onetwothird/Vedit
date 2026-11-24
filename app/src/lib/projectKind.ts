import type { Aspect, Platform, Project, ProjectType } from "../engine/types";

/* Project types. A clip project finds many highlights in a long video (Import, Pick clips, Edit, Export). A vlog
   or Quick Reel project edits the whole video as one clip (Import, Edit, Export): the engine names that clip h01. */

export const WHOLE_HID = "h01";

export const isWhole = (type: ProjectType | undefined): boolean => type === "vlog" || type === "reel";

export function parseType(v: string | null | undefined): ProjectType {
  return v === "vlog" || v === "reel" ? v : "clip";
}

export const PLATFORMS_FOR: Record<ProjectType, Platform[]> = {
  clip: ["reels", "shorts", "tiktok"],
  reel: ["reels", "shorts", "tiktok"],
  vlog: ["youtube"],
};

/** The platform a new project of this type starts with: the saved choice when the type allows it. */
export function platformFor(type: ProjectType, preferred: Platform): Platform {
  const allowed = PLATFORMS_FOR[type];
  return allowed.includes(preferred) ? preferred : allowed[0];
}

/** Where a project goes once its analysis is done, or when it is opened again. */
export function projectHome(p: Pick<Project, "id" | "type">): string {
  return isWhole(p.type) ? `/p/${p.id}/edit/${WHOLE_HID}` : `/p/${p.id}/pick`;
}

/** The import page for a new project of this type. */
export const newImport = (type: ProjectType): string => (type === "clip" ? "/p/new/import" : `/p/new/import?type=${type}`);

/** The shape Export starts with: a vlog stays wide, everything else is vertical. */
export const defaultFormat = (type: ProjectType | undefined): Aspect => (type === "vlog" ? "16:9" : "9:16");
