import { DESKTOP_REDIRECT, type DesktopHandoff } from "./supabase";

/* Social sign in inside the desktop app: the provider's page opens in the system browser (Google refuses embedded
   web views), and Supabase sends the browser to vedit://auth?code=..., which Windows hands to Vedit Studio (the
   installer registers the scheme; a second launch is forwarded to the running app by single-instance). */

export function desktopHandoff(): DesktopHandoff {
  return {
    async openExternal(url) {
      const { openUrl } = await import("@tauri-apps/plugin-opener");
      await openUrl(url);
    },
    async nextAuthLink() {
      const { onOpenUrl } = await import("@tauri-apps/plugin-deep-link");
      return new Promise<string>((resolve) => {
        let stop: (() => void) | undefined;
        let done = false;
        void onOpenUrl((urls) => {
          const link = urls.find((u) => u.startsWith(DESKTOP_REDIRECT));
          if (!link || done) return;
          done = true;
          stop?.();
          resolve(link);
        }).then((unlisten) => {
          stop = unlisten;
          if (done) unlisten();
        });
      });
    },
  };
}
