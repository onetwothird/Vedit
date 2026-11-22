import type { ReactNode } from "react";
import TopBar, { type TopBarProps } from "./TopBar";

/** The window layout every screen shares: the top bar over one scrolling panel. `wide` drops the reading-width limit. */
export default function Screen({ bar, wide, children }: { bar?: TopBarProps; wide?: boolean; children: ReactNode }) {
  return (
    <div className="app">
      <TopBar {...bar} />
      <main className="pane screen">
        <div className={wide ? "content wide" : "content"}>{children}</div>
      </main>
    </div>
  );
}
