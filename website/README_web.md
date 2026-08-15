# tinyagentos.dev

Marketing/docs site for TinyAgentOS. Static HTML/CSS/JS, no framework,
no server-side rendering, no database. Kept in its own repo, deployed
independently from the TinyAgentOS API itself.

## Local development

```
npm install
npm run dev
```

Opens a local dev server via Vite with hot reload.

## Build

```
npm run build
```

Outputs a static `dist/` folder ready to deploy anywhere that serves
static files.

## Lint & format

```
npm run lint
npm run format
```

## Deployment

Recommended: **Vercel** or **Cloudflare Pages**.

1. Connect this repo to your Vercel/Cloudflare Pages project.
2. Set the build command to `npm run build` and the output directory
   to `dist`.
3. Every push to `main` auto-deploys to production; every PR gets its
   own preview URL. No custom CI config needed — both platforms
   handle this natively once connected.
4. Point your domain's DNS at the provider once you're ready to go
   live, and confirm the auto-issued HTTPS certificate before
   announcing the launch.

## What's in here

- `index.html` — the page itself, semantic markup
- `styles.css` — all styling, includes `prefers-reduced-motion` handling
- `main.js` — oscilloscope wave generation, scroll-triggered reveals,
  copy-to-clipboard, all gated behind reduced-motion / IntersectionObserver
  feature checks
- `404.html` — styled error page
- `robots.txt`, `sitemap.xml` — update the domain in both before launch

## Before launch — checklist

- [ ] Replace placeholder benchmark numbers in the "Measured, not
      marketed" section with real output from `scripts/run_benchmarks.py`
      in the main TinyAgentOS repo
- [ ] Replace `your-org/tinyagentos` GitHub links with the real repo URL
- [ ] Replace `tinyagentos.dev` in `robots.txt` / `sitemap.xml` /
      `index.html` meta tags with your actual domain
- [ ] Run Lighthouse — target 90+ on Performance, Accessibility, Best
      Practices, SEO
- [ ] Cross-browser check: Safari, Firefox, Chrome, mobile Safari
- [ ] Confirm HTTPS is enforced, no mixed content
- [ ] Set up analytics (Plausible / Cloudflare Web Analytics — avoid
      Google Analytics given the "no cloud dependency" positioning)
- [ ] Set up uptime monitoring (UptimeRobot free tier or Cloudflare
      health checks)

## Optional: live benchmark readout (plan Day 6)

The hero's "INFERENCE.TRACE" panel currently shows illustrative,
static values (marked `DEMO`, not `LIVE`, in the markup — intentional,
don't relabel it `LIVE` until it's wired to something real). To wire
it to actual numbers from a running TinyAgentOS instance:

1. Add a read-only, rate-limited `GET /api/v1/public-stats` endpoint
   to the TinyAgentOS API (not this repo) sourced from
   `infrastructure/metrics.py`, returning only
   `{p50_ms, tasks_per_sec, memory_mb}`.
2. Lock CORS on that endpoint to this site's domain only.
3. In `main.js`, replace the hardcoded `.stat .val` values with a
   `fetch()` on page load, falling back to the current static values
   if the request fails or times out.
