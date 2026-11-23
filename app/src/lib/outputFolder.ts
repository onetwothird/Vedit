// Where exported clips go. The engine uses the same rule when no folder is sent: "<name> - vedit" beside the video.

const sepOf = (path: string): string => (path.includes("\\") ? "\\" : "/");

export const joinPath = (dir: string, name: string): string => (/[\/]$/.test(dir) ? dir : dir + sepOf(dir)) + name;

/** `<folder of the video>/<video name without extension> - vedit`, written with the video path's own separators. */
export function defaultOutputFolder(source: string): string {
  const cut = Math.max(source.lastIndexOf("/"), source.lastIndexOf("\\"));
  const dir = cut >= 0 ? source.slice(0, cut) : "";
  const file = source.slice(cut + 1);
  const dot = file.lastIndexOf(".");
  const stem = dot > 0 ? file.slice(0, dot) : file;
  const folder = `${stem} - vedit`;
  return dir ? joinPath(dir, folder) : folder;
}
