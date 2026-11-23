# Accounts with Supabase

Vedit Studio signs people in through Supabase Auth. The app only needs your project's address and its
**publishable key**. Both are meant to ship inside the app. Never put a secret (`sb_secret_…`) key in the app.

## 1. Create the project

1. Go to supabase.com, create a project (the free tier is enough to start), and wait for it to finish setting up.
2. Copy the **Project URL** from the project home page, and the **publishable** key (`sb_publishable_…`) from **Project Settings > API Keys**.
3. In `app/`, copy `.env.example` to `.env.local` and paste both values in. `.env.local` is git-ignored.

```
VITE_SUPABASE_URL=https://your-project-ref.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_your-key
```

Restart `npm run dev`. The sign-in screen now talks to your project. (`npm run dev:mock` keeps using the
demo accounts, whatever `.env.local` says.)

## 2. Allow the app's addresses

**Authentication > URL Configuration**

- **Site URL:** `http://localhost:5173`
- **Redirect URLs:** add `http://localhost:5173/`

Google and Facebook send people back to these addresses after they sign in. Anything not listed is refused.

## 3. Email sign in

**Authentication > Sign In / Providers > Email** is on by default.

- Keep **Confirm email** on. New accounts then get a confirmation link, and vedit shows "Check your inbox".
- Supabase's built-in mail sender is rate-limited and meant for testing. Before launch, set up your own SMTP
  under **Authentication > Emails > SMTP Settings**.
- Set the minimum password length to 8 to match the app.

## 4. Google

1. In Google Cloud Console, create an **OAuth client ID** of type **Web application**.
2. Under **Authorized redirect URIs**, add `https://your-project-ref.supabase.co/auth/v1/callback`.
3. In Supabase, open **Authentication > Sign In / Providers > Google**, turn it on, and paste the client ID and secret.

## 5. Facebook

1. In Meta for Developers, create an app and add **Facebook Login**.
2. Under **Valid OAuth Redirect URIs**, add the same callback, `https://your-project-ref.supabase.co/auth/v1/callback`.
3. In Supabase, open **Authentication > Sign In / Providers > Facebook**, turn it on, and paste the App ID and App Secret.
4. In the Facebook Login use case, add the **email** permission. Then, in Supabase's Facebook panel, turn on
   **Allow users without an email**. Facebook accounts made with a phone number don't share an email, and Supabase
   refuses them otherwise ("Error getting user email from external provider"). Such an account stays separate from
   the same person's Google account, because there is no email to link the two.
5. Meta needs the app switched to Live (with a privacy policy URL) before people outside your developer team can sign in.

## 6. Lifetime plan

The header menu shows **Lifetime** when the account's `app_metadata.plan` is `"lifetime"`. Only the server can
set `app_metadata`, so people can't give it to themselves. Until a payment hook does this, you can set it by
hand in the **SQL Editor**:

```sql
update auth.users
set raw_app_meta_data = raw_app_meta_data || '{"plan": "lifetime"}'
where email = 'buyer@example.com';
```

The person sees the new plan the next time their session refreshes (within an hour), or right away after
signing out and back in.

## Desktop app

- The installed app is allowed to reach `https://*.supabase.co` (see `connect-src` in `src-tauri/tauri.conf.json`).
- Email sign in works in the desktop window.
- Google refuses sign in from embedded windows, so the desktop app answers "Google and Facebook sign in come
  with the installer" for now. The installer task adds the system-browser hand-off: open the provider in the
  default browser and return through a `vedit://` link. That `vedit://` address then goes in the Redirect URLs list.
