# Deploying Race Fuel Co-Pilot to Cloudflare

Race day is a 100% static site served from `frontend/`. FastAPI (`backend/main.py`) is only for local development and automated tests—do not deploy it.

## 1. Pre-deploy checks

Run locally before pushing to `main`:

```powershell
.\.venv\Scripts\python.exe scripts\build_engine_bundle.py
.\.venv\Scripts\python.exe -m pytest
npx.cmd playwright test tests/e2e
```

Confirm:
- `frontend/engine.zip` is up to date (`test_engine_bundle.py` passes).
- `SHELL_CACHE` and `SHELL_FILES_HASH` in `frontend/runtime_manifest.js` match (`test_shell_cache.py` passes).
- `frontend/privacy.html`, `frontend/terms.html`, `frontend/_headers`, and `frontend/_redirects` are committed.

## 2. Deploy via Cloudflare Pages (or Workers Static Assets)

### Option A: Cloudflare Pages
1. Open **Cloudflare Dashboard > Workers & Pages > Create > Pages > Connect to Git**.
2. Select `stefysfv/race-fuel-copilot` and the `main` branch.
3. Build settings:
   - **Framework preset:** `None`
   - **Build command:** *(leave empty)*
   - **Build output directory:** `frontend`
4. Click **Save and Deploy**.

### Option B: Cloudflare Workers Static Assets (`wrangler.jsonc`)
1. Push to `main` (auto-deploys via `npx wrangler deploy`), or run `npx wrangler deploy` locally.
2. Live URL: `https://race-fuel-copilot.stefanaky-yos.workers.dev`.

## 3. Caching headers & WASM MIME type (`frontend/_headers`)

Cloudflare reads `frontend/_headers` for static asset headers:
- `/*.wasm` -> `Content-Type: application/wasm` and `Cache-Control: public, max-age=31536000, immutable`
- `/vendor/pyodide/*` -> `Cache-Control: public, max-age=31536000, immutable`
- `/index.html`, `/sw.js`, `/runtime_manifest.js`, `/manifest.json` -> `Cache-Control: no-cache`

## 4. Redirects (`frontend/_redirects`)

`frontend/_redirects` maps `/privacy` -> `/privacy.html` and `/terms` -> `/terms.html` with `200` rewrites.

## 5. Custom domain (optional)

1. In Cloudflare Dashboard, open **Custom domains > Set up a custom domain**.
2. Add your domain (e.g. `fuel.yourdomain.com`) and confirm the DNS record.
3. Wait for TLS to show **Active** (HTTPS is required for Service Workers, GPS, and Wake Lock).
4. Update `og:url` and `og:image` in `frontend/index.html`, bump `SHELL_CACHE` + `SHELL_FILES_HASH`, and push.

## 6. Post-deploy smoke check

1. Verify WASM MIME type:
   ```powershell
   curl.exe -I https://race-fuel-copilot.stefanaky-yos.workers.dev/vendor/pyodide/pyodide.asm.wasm
   ```
   Confirm `Content-Type: application/wasm`.
2. Open the deployed HTTPS URL on a phone on Wi-Fi and wait for **Ready for offline**.
3. Check **Privacy**, **Terms & Not Medical Advice**, **Changelog**, and **Copy diagnostics** in the footer.
4. Add to Home Screen, turn on Airplane Mode, reload, and confirm a plan builds offline.
