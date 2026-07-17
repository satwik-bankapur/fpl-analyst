# FPL Analyst — web

Next.js 14 (App Router) frontend for the [FPL Analyst](../README.md) delivery
surface. A thin client over the FastAPI backend: a landing page with a draft
picker, brief pages, and an email capture. No state library, no data client —
plain `fetch` (`lib/api.ts`).

## Develop

```bash
npm install
npm run dev     # http://localhost:3000
```

Point it at a running backend (see the root README for starting the API):

```bash
# web/.env.local (gitignored)
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

Defaults to `http://localhost:8000` if unset. This is the **only** env var the
frontend needs, and it's public (`NEXT_PUBLIC_`) — put no secrets here.

## Layout

```
app/
  page.tsx                     landing (hero + draft picker modal + preview)
  brief/draft/[draftId]/       brief page for a submitted draft
  brief/[teamId]/              brief page for an in-season team (Stage B)
  story/                       freemium teaser page
components/                    DraftPicker, BriefView, EmailCapture, Md, ...
lib/api.ts                    typed fetch layer + shared section ordering
```

`components/Md.tsx` is a deliberately tiny markdown renderer for the stub
Writer's output (bullets, quotes, paragraphs) — it renders as React text nodes,
no `dangerouslySetInnerHTML`, no markdown dependency.

## Build & deploy

```bash
npm run build
npm run start
```

Deployed on Vercel with root directory `web/`. Set `NEXT_PUBLIC_API_URL` to the
production API origin, and make sure that origin matches the backend's
`FRONTEND_ORIGIN` (CORS is an exact-match allowlist). Full steps in
[`docs/deploy-stage-a.md`](../docs/deploy-stage-a.md).
