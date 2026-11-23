# Security

## Reporting a problem

Please don't open a public issue for a security problem. Report it privately through GitHub:
**Security → Report a vulnerability** on [onetwothird/vedit](https://github.com/onetwothird/vedit/security/advisories/new).

Include what you found, how to reproduce it, and what someone could do with it. You'll get a reply within 7 days,
and a fix or a plan within 30. Once it's fixed you're credited in the release notes, unless you'd rather not be.

## Supported versions

Only the latest release and the `main` branch get security fixes.

## What Vedit protects

- **Your videos stay on your computer.** Analysis, editing and rendering run locally. Only the optional AI
  highlight step sends text (the transcript) to the AI provider you choose, with your own key.
- **AI keys** are kept in your operating system's credential store (Windows Credential Manager). They are never
  written to project files, logs or browser storage, and only the step that calls the AI sees one.
- **The local engine** listens on `127.0.0.1` only, with a new random token each time the app starts. Every
  request needs that token, and only the app's own origins are accepted. The preview's media routes take the token
  in the query because a video element cannot send headers. They serve only a project's own video and
  thumbnails, and b-roll its clips use, never an arbitrary file.
- **Sign-in** uses Supabase Auth with PKCE. The app holds only the public key; a secret (`sb_secret_`) key never
  ships in the app.

## In scope

The engine (`scripts/`), the desktop app (`app/`), the installer and update channel, and anything that could
expose a key, a token, a file outside a project, or run code from a crafted video, project or `edl.json`.

Out of scope: problems that need someone already in control of your computer or account, and bugs in FFmpeg,
Python or other dependencies themselves (report those upstream; tell us if Vedit makes them reachable).
