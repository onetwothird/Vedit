import type { ReactNode } from "react";
import TopBar, { type TopBarProps } from "./TopBar";

/** The window layout every screen shares: the top bar over one scrolling panel.
    `wide` fills the window (workspaces: Pick, Edit); `narrow` is a centred 720px column (forms: Import, Export, Settings). */
export default function Screen({ bar, wide, narrow, children }: { bar?: TopBarProps; wide?: boolean; narrow?: boolean; children: ReactNode }) {
  return (
    <div className="app">
      <TopBar {...bar} />
      <main className="pane screen">
        <div className={wide ? "content wide" : narrow ? "content narrow" : "content"}>{children}</div>
      </main>
    </div>
  );
}
