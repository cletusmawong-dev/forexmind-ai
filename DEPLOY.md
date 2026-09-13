# Deploying ForexMind AI

Architecture (one URL for users, two pieces behind the scenes):

```
Users / phones (PWA)
        │
        ▼
 Netlify  ── static frontend + /api/* proxy (free)
        │
        ▼
 Render   ── always-on FastAPI backend + demo datasets (free tier)
```

---

## Part 1 — Backend on Render (~10 minutes, free)

1. Push this repo to GitHub (see "GitHub" below if you use the drag & drop route for Netlify).
2. Go to https://dashboard.render.com → **New +** → **Blueprint** → select your repo.
   Render reads `render.yaml` and creates the service `forexmind-ai-api`.
   (Or: **New +** → **Web Service** → pick the repo → runtime **Docker** → it finds `backend/Dockerfile` automatically.)
3. Wait for the first deploy (~3–5 min). Verify: open `https://<your-service>.onrender.com/api/health` → `{"ok": true, ...}`.
4. **Copy your backend URL**, e.g. `forexmind-ai-api.onrender.com`.

> Free-tier note: the service sleeps after ~15 min idle and wakes on the next request
> (~30–50 s). The app shows a graceful "Reconnecting…" state during wake-up.
> Paid plans ($7/mo) keep it always-on.

---

## Part 2 — Frontend on Netlify

### Option A — Drag & drop (fastest, no GitHub needed)

```bash
cd frontend
BACKEND_HOST=your-api.onrender.com ./scripts/prepare-deploy.sh
```

Then drag the generated `frontend/deploy-netlify` folder onto
https://app.netlify.com/drop — done. Netlify hosts it and gives you a URL.

### Option B — Git-backed (auto-deploys on every push)

1. Push the repo to GitHub.
2. Netlify → **Add new site** → **Import an existing project** → pick the repo.
3. Build settings are auto-detected from `frontend/netlify.toml`
   (base `frontend` is not needed if you set **Base directory = `frontend`** in the UI).
4. **Before deploying**: replace `REPLACE-WITH-YOUR-BACKEND-HOST` with your Render host in
   `frontend/netlify.toml` and `frontend/public/_redirects`, commit, push. Netlify redeploys automatically.

### Changing the backend later

Edit the first redirect in `netlify.toml` / `_redirects` (Option B) or re-run the script (Option A).

---

## Verifying the deployment

| Check | Expected |
|---|---|
| `https://<netlify-site>/` | Splash → login screen |
| Sign in with demo account | Home dashboard with balance + market watch |
| Browser devtools → Network → `/api/agent/status` | 200, proxied through Netlify |
| Phone: menu → "Add to Home Screen" | Installs as full-screen PWA with the ForexMind icon |

---

## Optional next steps

- **Custom domain**: Netlify → Domain settings (free HTTPS included).
- **Firebase Firestore**: set `FIREBASE_PROJECT_ID` + `GOOGLE_APPLICATION_CREDENTIALS` on Render.
- **XKiro AI**: set `XKIRO_API_KEY` on Render (key stays server-side).
- **Push notifications (FCM)**: add Firebase server credentials on Render + the VAPID config in the frontend.

## Security notes

- All secrets live in Render's environment variables — never in the frontend bundle.
- Netlify only ever serves static files and proxies `/api/*` — it holds no credentials.
- The demo datasets ship inside the backend image and are always labeled DEMO/HISTORICAL.
