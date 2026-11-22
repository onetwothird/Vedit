import type { ReactNode } from "react";
import TopBar, { type TopBarProps } from "./TopBar";

/** The window layout every screen shares: the top bar over one scrolling panel. */
export default function Screen({ bar, children }: { bar?: TopBarProps; children: ReactNode }) {
  return (
    <div className="app">
      <TopBar {...bar} />
      <main className="pane screen">
        <div className="content">{children}</div>
      </main>
    </div>
  );
}
