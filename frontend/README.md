# SILENCE ENGINE dashboard

React 18 + TypeScript + Vite + Recharts. It is a static single-page app: the Flask backend serves the built files from `frontend/dist`, so no Node.js is needed on the server.

```bash
npm ci
```

```bash
npm run dev
```

Opens on <http://127.0.0.1:5173> and proxies `/api` to the backend on port 8000 (start it with `./scripts/dev_backend.sh`). `npm run build` type-checks and writes `dist/`.

Design notes:

- All data comes from the REST API (documented in [../docs/API.md](../docs/API.md)); there are no hard-coded numbers, and a missing count is shown as *unknown*, never as zero.
- API and asset URLs are relative, so the same build works at `/`, behind a reverse-proxy prefix, or behind Jupyter's `/proxy/8000/`.
- Write requests send an `X-Requested-With` header (the backend's anti-CSRF check) and a bearer token when one is configured.
- The backend's Content-Security-Policy forbids inline scripts and external origins, so fonts and libraries are bundled.
- Navigation uses hash routes (`#/cases/ID`), and the interface follows the dark "spatial" theme in `src/styles/app.css`.
