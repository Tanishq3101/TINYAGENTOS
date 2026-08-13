# TinyAgentOS Website — Build & Deployment Plan

Goal: ship the landing page in the mockup as a real, live, fast website —
not a generic template. Split into two tracks that can run in parallel:
**Frontend** (the site itself) and **Backend/Infra** (hosting, domain,
CI/CD, and — if you want the live benchmark numbers to be real instead
of static — a small API to serve them).

Scope decision up front: this is a **marketing/docs site**, separate
from the TinyAgentOS API itself. It does not need to talk to your
Phi-3 inference backend at all unless you specifically want the
"live" scope readout to reflect real numbers from a running instance.
Default plan below treats the scope animation as illustrative (fake
data, real design) — Day 6 has the optional path if you want it wired
to real metrics instead.

---

## Day 1 — Design lock + project setup

**Frontend**
- Finalize the mockup direction (palette, type, signature element) —
  treat today as the last day changes to the core look are cheap
- Set up the project: Vite + vanilla HTML/CSS/JS (no framework needed
  for a single-page marketing site — React/Next adds build complexity
  you don't need yet)
- Convert the mockup HTML into a proper project structure:
  `index.html`, `styles.css`, `main.js`, `/assets`
- Set up ESLint + Prettier for consistency

**Backend/Infra**
- Register domain (if not already owned)
- Create hosting account: **Vercel or Cloudflare Pages** recommended
  for a static site — free tier, automatic HTTPS, global CDN, git-push
  deploys. (Skip a traditional VPS/Nginx setup entirely for this —
  that's infrastructure for the API, not the marketing site.)
- Create the GitHub repo for the website (separate repo from
  TinyAgentOS itself — keep the product and its marketing site
  independently deployable)

---

## Day 2 — Core layout build

**Frontend**
- Build the nav + hero section from the mockup as real, semantic HTML
  (proper `<nav>`, `<h1>`, `<section>` — not div soup; matters for SEO
  and accessibility)
- Implement the oscilloscope SVG trace as a reusable component/function
  rather than hardcoded path data — makes it easy to regenerate with
  different "waveforms" later
- Mobile-first CSS: build the single-column mobile layout first, then
  add the `@media` split into the two-column desktop hero
- Lighthouse check early — catch layout shift / contrast issues before
  they compound

**Backend/Infra**
- Set up the CI pipeline for the site repo: on push to `main`, run
  lint + build + deploy automatically (Vercel/Cloudflare Pages do this
  natively once connected to the repo — no custom GitHub Actions
  needed for a static site)
- Set up a preview-deployment flow: every PR gets its own preview URL
  before merging to `main`

---

## Day 3 — Motion + interaction pass

**Frontend**
- Implement the animated pulse-dot pipeline diagram with
  `IntersectionObserver` so the animation only starts once the section
  scrolls into view (don't run animations off-screen — wastes battery,
  looks off if the user scrolls past fast)
- Add `prefers-reduced-motion` handling — anyone with that OS setting
  gets the static version, no waveform/pulse animation. This is not
  optional polish; it's an accessibility requirement.
- Hover/focus states on every interactive element (buttons, nav links)
  — keyboard focus rings, not just `:hover`
- Scroll-triggered fade-ins for the spec cards and benchmark table
  (subtle — the skill guidance is right that over-animating reads as
  AI-generated; one orchestrated hero moment + light scroll reveals is
  enough)

**Backend/Infra**
- Add a basic `robots.txt` and `sitemap.xml`
- Set up analytics (Plausible or Cloudflare Web Analytics — avoid
  Google Analytics if you care about the "no cloud dependency" ethos
  the product itself is selling)

---

## Day 4 — Content + copy pass

**Frontend**
- Replace all placeholder copy with real copy that matches what
  TinyAgentOS actually does (pull directly from `README.md`,
  `docs/ARCHITECTURE.md`, and the real benchmark numbers once you have
  them from Day 28's hardening work — don't invent metrics)
- Write real spec-card content: Quantized runtime, Agent
  orchestration, Non-root by default, Structured logging — tie each
  one to an actual file/module in the repo, the way the mockup does
- Add a real code snippet block (the `docker compose up` command)
  with copy-to-clipboard — small detail, developers expect it

**Backend/Infra**
- Set up environment-specific config: a staging URL
  (`staging.tinyagentos.dev` or similar) separate from production, so
  you can review before every deploy goes live
- SSL/HTTPS — automatic on Vercel/Cloudflare Pages, just confirm it's
  actually enforced (no mixed content, no HTTP fallback)

---

## Day 5 — Performance + accessibility hardening

**Frontend**
- Audit and trim: inline critical CSS, defer non-critical JS, lazy-load
  anything below the fold
- Font loading: use `font-display: swap` on the IBM Plex fonts so text
  isn't invisible while fonts load
- Run axe DevTools or similar — check color contrast (the amber accent
  on dark background needs a contrast check, not just an aesthetic one),
  alt text on any real images, proper heading hierarchy
- Target: Lighthouse 90+ on Performance, Accessibility, Best Practices,
  and SEO — this is a reasonable bar for a single static page, not an
  aspirational one

**Backend/Infra**
- Set caching headers / CDN cache rules for static assets (fonts, CSS,
  JS) — long cache with cache-busting filenames, so repeat visitors
  don't re-download unchanged assets
- Set up uptime monitoring (UptimeRobot free tier, or Cloudflare's
  built-in health checks) so you know if the site goes down

---

## Day 6 — (Optional) Wire the scope readout to real data

Only do this if you actually want the "LIVE" oscilloscope numbers on
the hero to reflect a real running TinyAgentOS instance, rather than
being illustrative design. If you skip this, the site stays purely
static — simpler, cheaper, nothing to keep running.

**Backend**
- Add a lightweight, read-only endpoint on the *actual* TinyAgentOS API
  (not the website) that exposes just the metrics you want to show
  publicly — e.g. `GET /api/v1/public-stats` returning
  `{p50_ms, tasks_per_sec, memory_mb}` sourced from
  `infrastructure/metrics.py`
- **Do not expose this without rate limiting** — this is a new public,
  unauthenticated endpoint; think about what it should and shouldn't
  reveal (no internal error rates, no request content, nothing that
  gives away infrastructure details beyond the three numbers you chose)
- CORS: allow only your website's domain to call this endpoint, not `*`

**Frontend**
- Replace the hardcoded scope-stat values with a `fetch()` call on
  page load, with a static fallback value shown if the fetch fails or
  times out (never show a blank/broken UI if your own inference
  instance happens to be down — the marketing site should stay up
  independently)

---

## Day 7 — Launch

**Frontend**
- Final cross-browser check (Safari, Firefox, Chrome, mobile Safari —
  Safari especially, since it handles some CSS/animation timing
  differently)
- Final copy proofread
- 404 page — style it consistently with the rest of the site, don't
  leave the host's default error page

**Backend/Infra**
- Point the real domain's DNS at the hosting provider
- Confirm HTTPS certificate is issued and auto-renewing
- Final Lighthouse + broken-link check
- Deploy to production
- Post-launch: watch uptime monitor + analytics for the first 24 hours

---

## What this plan deliberately leaves out

- **No CMS** — the site is small enough that editing HTML/CSS directly
  is faster than standing up and maintaining a headless CMS for it
- **No server-rendered framework (Next.js/Nuxt)** — nothing here needs
  server-side rendering; it's a static page, and static hosting is
  simpler, cheaper, and has fewer moving parts to secure
- **No database** — even the optional Day 6 metrics endpoint is
  stateless; it reads live process metrics, it doesn't need to persist
  anything
- **No user accounts / auth on the website itself** — that's a
  TinyAgentOS API concern, not a marketing site concern; keep the two
  systems separate

## Best-practices checklist (carried through every day above, not a
## separate task)

- [ ] Semantic HTML, not div soup
- [ ] Mobile-first responsive design
- [ ] `prefers-reduced-motion` respected
- [ ] Keyboard navigable, visible focus states
- [ ] Real copy, real numbers — no invented benchmarks or fake
      testimonials
- [ ] HTTPS enforced, no mixed content
- [ ] Lighthouse 90+ across all four categories before launch
- [ ] Separate staging/production environments
- [ ] Marketing site and product API stay independently deployable
