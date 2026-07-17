# Deploying the Admin app — FREE (Render + Turso)

The admin panel is served **by the backend** at `/admin`, so you just deploy the
backend. This is a **$0** setup: the app runs on Render's free plan, and all
tracking data (runs, users, teams, cost) lives in a **free Turso cloud DB** so it
survives redeploys even though the free instance has an ephemeral disk.

## 1. Create a free Turso database
1. Sign up at https://turso.tech (free tier).
2. Create a database (any name).
3. Copy its **URL** (looks like `libsql://your-db.turso.io`) and create an
   **auth token**. Keep both handy.

## 2. Push the repo to GitHub
Commit today's files (incl. `render.yaml`, `src/db.py`, `admin-frontend/index.html`).
Never commit `.env`.

## 3. Create the service on Render (free)
1. https://dashboard.render.com → **New → Blueprint** → connect the repo.
   Render reads `render.yaml` and proposes a **free** web service.
2. Click **Apply**.

## 4. Set the secret env vars
Service → **Environment** → add:
- `OPENROUTER_API_KEY` = your OpenRouter key
- `GOOGLE_CLIENT_ID` = your OAuth Web client id (for `@nxtwave.co.in`)
- `TURSO_DATABASE_URL` = `libsql://your-db.turso.io`
- `TURSO_AUTH_TOKEN` = your Turso token

Do **not** set `AUTH_DISABLED` in production.

## 5. Point Google OAuth at the deployed URL
Render gives a URL like `https://tr-doc-generator.onrender.com`. In Google Cloud
Console → your OAuth client → **Authorized JavaScript origins**, add that exact
origin. Save.

## 6. Use it
- Admin panel: `https://<your-app>.onrender.com/admin`
- Sign in with an admin `@nxtwave.co.in` account (must be in `harness.yaml →
  auth.admin_emails`, and an OAuth **test user** while the consent screen is in Testing).

## Good to know
- **Free-tier cold starts:** the free instance sleeps after ~15 min idle and takes
  ~30–60s to wake on the next request. Fine for internal use.
- **Generated .docx** files sit on the ephemeral instance and regenerate on demand;
  all *tracking* data persists in Turso. Want persistent files too? Add object
  storage, or use a paid disk (set `TR_DATA_DIR=/var/data` instead of Turso).
- **Local dev is unchanged:** with no Turso env vars set, the app uses the local
  `knowledge_base/tr_app.db` file automatically.
- **Separate-domain admin host** (optional): serve `admin-frontend/index.html`
  anywhere, then run `localStorage.setItem('tr_admin_api','https://<your-app>.onrender.com')`
  in that page and add the domain to the OAuth origins + backend CORS.
