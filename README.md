# flashcert-share

A small server that publishes FlashCert courses as read-only web pages. The FlashCert app syncs a course
(notes, revision sheet, question bank, flashcards) with a token; the course gets its own link, and anyone with
the link can read it, take the quizzes and study the flashcards. Visitors have no account and can't generate
anything. They can flag an error on any element, and the app collects those reports.

* `/` lists the **public** courses, grouped by certification.
* `/c/<id>` is one course: **Notes**, **Revision**, **Quiz**, **Cards**. Every answer and card opens the passage of
  the notes it rests on, highlighted, without leaving the quiz.
* Visitor progress (quiz scores, known cards) stays in the visitor's browser, and only once they agree.
  Turning it on also asks the browser for *persistent storage*; the page explains why.
* One Docker image (Python, Flask, SQLAlchemy). SQLite by default, PostgreSQL by changing one URL. No CDN.

## Run it

The server ships as a Docker image, `ghcr.io/valkea/flashcert-share`, built by GitHub Actions
([`.github/workflows/share-server-image.yml`](../.github/workflows/share-server-image.yml)) on every push to `main`
that touches `server/` (`:latest`, `:sha-…`) and on tags `share-v1.2.3` (`:1.2.3`, `:1.2`), for amd64 and arm64.

On the VPS, in a folder holding [`compose.yaml`](compose.yaml) and a `.env` made from [`.env.example`](.env.example):

```bash
docker compose up -d
docker compose exec share flashcert-share token create --label phone   # prints the token for the app, once
```

The container listens on `127.0.0.1:8080` of the host (change `FLASHCERT_SHARE_PORT`), keeps its SQLite file in
the `data` volume and restarts by itself. Point a (sub)domain or a path of your reverse proxy at it:
[`deploy/nginx.conf`](deploy/nginx.conf) or [`deploy/Caddyfile`](deploy/Caddyfile). Under a path
(`example.com/courses/`) the proxy sends `X-Forwarded-Prefix` and `FLASHCERT_SHARE_PUBLIC_URL` includes the path.
`GET /healthz` answers `{"ok": true}` and backs the image's health check.

Upgrade: `docker compose pull && docker compose up -d` (pin a version with `FLASHCERT_SHARE_IMAGE=…:0.1.0`).
Tables are created on start. Back up the `data` volume (or the PostgreSQL one).

GHCR packages start private: either make `flashcert-share` public in the GitHub package settings, or
`docker login ghcr.io` on the VPS with a token that can read packages. To use Docker Hub instead, change the
`images:` line and the login step of the workflow (`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` secrets).

Without the registry, `docker compose build` builds the image from this folder.

### Try a course before the app syncs

Share a course file directly (the harness' `bundle.json` works):

```bash
docker compose exec -T share flashcert-share import /dev/stdin --certification "Claude Agent SDK" < bundle.json
```

## Settings

In `.env` (see [`.env.example`](.env.example)):

| Variable | Default | |
|---|---|---|
| `FLASHCERT_SHARE_PUBLIC_URL` | from the request | Base of the links handed to the app, e.g. `https://courses.example.com`. Set it. |
| `FLASHCERT_SHARE_DATABASE_URL` | SQLite file in the `data` volume | Any SQLAlchemy URL, e.g. `postgresql+psycopg://flashcert:pass@db/flashcert` |
| `FLASHCERT_SHARE_PROXIES` | `1` | Reverse proxies in front whose `X-Forwarded-*` headers are trusted. `0` when the port faces the internet directly. |
| `FLASHCERT_SHARE_SITE_NAME` | `FlashCert` | Shown in the header and page titles |
| `FLASHCERT_SHARE_MAX_UPLOAD_MB` | `20` | Largest course the app may upload (keep the proxy's body limit in line) |
| `FLASHCERT_SHARE_REPORTS_PER_HOUR` | `20` | Error reports allowed per visitor IP per hour |
| `FLASHCERT_SHARE_PORT` | `8080` | Host port (compose only) |
| `FLASHCERT_SHARE_IMAGE` | `ghcr.io/valkea/flashcert-share:latest` | Image (compose only) |

## Command line

Inside the container: `docker compose exec share flashcert-share …`

```
flashcert-share token create [--label NAME] | token list | token revoke ID
flashcert-share courses                    shared courses with their links
flashcert-share import FILE [--uid X] [--certification NAME] [--unlisted]
flashcert-share reports [--all] [--resolve ID ...]
flashcert-share copy-db TARGET_URL         copy everything to another (empty) database
flashcert-share serve [--host] [--port] [--threads] [--proxies]   (what the container runs)
```

### PostgreSQL

`compose.yaml` has an optional `db` service. SQLite is plenty for a handful of friends; switch when you want
the data in a database server:

```bash
# .env: POSTGRES_PASSWORD=…   (leave DATABASE_URL unset for now)
docker compose --profile postgres up -d
docker compose exec share flashcert-share copy-db postgresql+psycopg://flashcert:<password>@db/flashcert
# .env: FLASHCERT_SHARE_DATABASE_URL=postgresql+psycopg://flashcert:<password>@db/flashcert
docker compose --profile postgres up -d
```

Links don't change: share ids are copied with the courses. An external PostgreSQL works the same way.

## What visitors get

| | |
|---|---|
| Notes | The course notes, with a table of contents, formulas (KaTeX, loaded only when a course has some) and a flag on each section |
| Revision | Essentials, definitions, structure blocks (tables, steps, timelines, formulas, diagrams, rules, concepts) and "don't mix these up" |
| Quiz | 5 / 10 / 20 / all questions from the bank: single choice, multiple choice, true/false, fill the blank, ordering. Missed and never-asked questions come first, "Retry my mistakes" replays the missed ones. Each answer shows the explanation and **Read the passage**. |
| Cards | Flip, then "I knew it" / "Still learning"; restudy the ones still being learned |
| Report an error | On the course, a notes section, a sheet item, a question or a card: a message and an optional name. A hidden honeypot field and a per-IP limit keep bots out. |

Progress lives under `localStorage` keys starting with `fcs:`, written only after the visitor clicks
**Save my progress**. Before that, answers are kept in memory for the visit and a small banner offers to save
them (the answers given so far are saved too). **Your progress** in the header shows what was granted and how
much space is used, and erases everything. `navigator.storage.persist()` is what protects the data from
eviction: Firefox asks the visitor, Chrome decides from site engagement or a bookmark, and the page says
which way it went.

Reports keep the message, the optional name, the element and a copy of its text at the time. No IP address
or cookie is stored (the rate limit lives in memory).

## Sync API (for the app)

Every `/api/v1` call takes `Authorization: Bearer <token>`. JSON in, JSON out; errors are
`{"error": "…"}` with 400, 401, 404, 413 (too large), 422 (course rejected) or 429.

| | |
|---|---|
| `GET /api/v1/status` | `{ok, version, courses, open_reports, base_url}`; use it for "Test connection" |
| `GET /api/v1/courses` | every synced course: `{uid, share_id, url, title, certification, lang, position, visibility, hash, questions, cards, created_at, updated_at, open_reports}` |
| `PUT /api/v1/courses/{uid}` | create or replace a course (body below). 201 when created, 200 otherwise. |
| `GET /api/v1/courses/{uid}` | the summary plus `content` as stored |
| `PATCH /api/v1/courses/{uid}` | `{"visibility": "public" \| "unlisted"}` without resending the course |
| `DELETE /api/v1/courses/{uid}` | stop sharing: the course and its reports are deleted, the link stops working. Sharing again gives a new link. |
| `GET /api/v1/reports?status=open\|resolved\|dismissed\|all&course={uid}&since_id={n}` | reports, oldest first: `{id, course_uid, element: {kind, id}, excerpt, message, author, status, created_at, updated_at}` |
| `PATCH /api/v1/reports/{id}` | `{"status": "resolved" \| "dismissed" \| "open"}` |
| `DELETE /api/v1/reports/{id}` | |

`uid` is the app's own stable id for the course (1–100 characters among `A-Z a-z 0-9 . _ : -`), e.g. a UUID
kept on the course row. The server answers with the public `url`, which never changes while the course is shared.

### Course document

```json
{
  "format": "flashcert-share-1",
  "title": "Hooks and guardrails",
  "certification": "Claude Agent SDK",
  "lang": "en",
  "visibility": "public",
  "position": 3,
  "notes": "## Section\n\n### Subsection\n...",
  "sheet": { "essentiel": [], "definitions": [], "structure": [], "pieges": [] },
  "questions": [
    { "id": "q12", "type": "mcq", "q": "…", "answers": ["…", "…"], "correct": 1,
      "explanation": "…", "notion": "…", "difficulty": 2,
      "sources": [{ "section_id": "2.1", "quote": "…", "verified": true }] }
  ],
  "cards": [
    { "id": "c7", "front": "…", "back": "…", "notion": "…", "sources": [{ "section_id": "1.1", "quote": "…" }] }
  ]
}
```

* Questions and cards use the app's own JSON (`mcq`, `multi`, `true_false`, `cloze`, `order`; the older single
  `source` object is accepted). Give each a stable `id`, such as the database row id, so reports point at the
  right item across syncs. Items without one get an id from their text.
* `certification`, `lang`, `position` (order inside the certification), `sheet`, `questions` and `cards` are optional.
* Unplayable questions and cards are dropped and listed in `warnings` in the response. Transcript, analysis and
  costs are never stored, even when sent (a harness `flashcert-course-1` bundle is accepted as is).

The response is the course summary plus `created`, `changed` and `warnings`. `hash` changes only when what
visitors see changes, so the app can compare it with a hash of its own (or simply re-send: identical content
leaves `updated_at` alone).

Suggested app flow: a **Sharing** section in Settings holds the server URL and token (with "Test connection"
via `/status`). **Share** on a course sends `PUT` and opens the system share sheet with the returned `url`;
later edits re-send it. Reports come from `GET /api/v1/reports?since_id=<last seen>`.

## Public endpoints

`GET /api/public/courses`, `GET /api/public/courses/{share_id}` (what the pages use) and
`POST /api/public/courses/{share_id}/reports` with
`{"element": {"kind": "question|card|section|sheet|course", "id": "q12"}, "message": "…", "name": "…"}`.
Section ids are the notes numbering (`"2"`, `"2.1"`); sheet ids are `essentiel.0`, `definitions.3`,
`structure.1`, `pieges.2`.

## Development

Plain Python (3.10+): Flask, SQLAlchemy, waitress.

```bash
uv venv && uv pip install -e '.[dev,postgres]'
.venv/bin/flashcert-share serve --proxies 0          # http://127.0.0.1:8080, SQLite in ~/.local/share/flashcert-share
.venv/bin/pytest
FLASHCERT_SHARE_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:5432/fcs .venv/bin/pytest   # empties that database
```

KaTeX 0.16.11 is vendored under `static/vendor/katex` (MIT).
