# flashcert-share

A small server that publishes FlashCert courses as read-only web pages, and keeps a copy of them for the app.
The FlashCert app syncs its courses (notes, revision sheet, question bank, flashcards) with a token, both ways:
the server is a backup of the phone and carries courses between devices. A shared course gets its own link, and
anyone with the link can read it, take the quizzes and study the flashcards. Visitors have no account and can't
generate anything. They can flag an error on any element, and the app collects those reports.

* `/` lists the **public** courses, grouped by certification.
* `/c/<id>` is one course: **Notes**, **Revision**, **Quiz**, **Cards**. Every answer and card opens the passage of
  the notes it rests on, highlighted, without leaving the quiz.
* Visitor progress (quiz scores, known cards) stays in the visitor's browser, and only once they agree.
  Turning it on also asks the browser for *persistent storage*; the page explains why.
* One Docker image (Python, Flask, SQLAlchemy). SQLite by default, PostgreSQL by changing one URL. No CDN.

## Host your own

You need a server reachable from the internet (a small VPS is plenty) with Docker and Docker Compose, a domain or
subdomain pointing at it, and a reverse proxy that handles HTTPS (nginx, Caddy, Traefik…).

**1. Get the compose file and the settings**, in an empty folder on the server:

```bash
mkdir flashcert-share && cd flashcert-share
curl -fsSLO https://raw.githubusercontent.com/Valkea/FlashCert-Server/main/compose.yaml
curl -fsSL https://raw.githubusercontent.com/Valkea/FlashCert-Server/main/.env.example -o .env
```

(Or `git clone https://github.com/Valkea/FlashCert-Server.git` and work in that folder.)

**2. Edit `.env`**: set `FLASHCERT_SHARE_PUBLIC_URL` to the address visitors will open, e.g.
`https://courses.example.com`. The other settings can stay as they are (see [Settings](#settings)).

**3. Start it and create a token** for the app:

```bash
docker compose up -d
docker compose exec share flashcert-share token create --label phone   # prints the token, once
```

**4. Put it behind HTTPS.** The container listens on `127.0.0.1:8080` of the host only. Point your domain at it
with [`deploy/nginx.conf`](deploy/nginx.conf) or [`deploy/Caddyfile`](deploy/Caddyfile) (Caddy gets the
certificate by itself; with nginx, run `certbot --nginx`). Then `https://courses.example.com/healthz` should answer
`{"ok": true}`.

**5. Connect the app**: in FlashCert, **Settings → Share and sync**, enter the address and the token, then
**Save and test**. The app backs up its courses there, and **Share** at the top of a course creates its link.

### Good to know

* The image is `ghcr.io/valkea/flashcert-share` (amd64 and arm64). The container restarts by itself.
* Upgrade: `docker compose pull && docker compose up -d`. Tables are created on start and the data stays (see
  [Your data](#your-data)). To stay on one version, set `FLASHCERT_SHARE_IMAGE=ghcr.io/valkea/flashcert-share:1.2.3`
  in `.env`.
* Another port: `FLASHCERT_SHARE_PORT`. Under a path of an existing site (`example.com/courses/`) rather than its own
  subdomain: the proxy sends `X-Forwarded-Prefix` (both deploy files show how) and `FLASHCERT_SHARE_PUBLIC_URL`
  includes the path.
* Building the image yourself: `docker compose build` in a clone of this repository.

Images are built by GitHub Actions ([`.github/workflows/image.yml`](.github/workflows/image.yml)) after the tests
pass: `:latest` and `:sha-…` from `main`, `:1.2.3` and `:1.2` from tags `v1.2.3`.

### Try a course before the app syncs

Share a course file directly (a [course document](#course-document) or a `flashcert-course-1` bundle):

```bash
docker compose exec -T share flashcert-share import /dev/stdin --certification "Claude Agent SDK" < bundle.json
```

An imported course has no owner, so no app can change it. Add `--owner TOKEN_ID` (see `token list`) to hand it to
that token's app, which then downloads it at its next sync.

## How sharing works

**Both ways, with recoverable changes.** The app sends its courses and gets back their link, their visibility and the
errors visitors report. It also downloads them: a course changed on another device comes back, and a course the
phone doesn't have yet (a new phone, a reinstall, a tablet with the same token) is added to it. When both sides
changed a course since they last synced, the app asks which copy to keep. Safe automatic sync also queues deletions
and large replacements for review; every accepted content change has a recoverable revision. The server keeps only
the course content (notes, revision sheet, questions, cards): recordings, transcript, analysis, costs and study
progress (scores, cards known, review dates) never leave the phone. A course restored from the server starts its
review cycle again.

**Which courses.** In **Settings → Share and sync**, *Back up every course* (on by default) sends every finished course,
*private* unless you share it. Turned off, only the courses you share go to the server.

**Private, link-only or public.** A *private* course is a backup: it has no page, and its link answers "not found".
*Unlisted* courses are reachable only through their link; *public* ones are also listed on the server's home page,
grouped by certification. The app can change it at any time and the link stays the same. Unlisted and public
courses aren't secret: anyone who has the link can open them. Links are long random ids, so nobody finds an
unlisted course by guessing.

**Stop sharing** makes a backed-up course private again. When the app doesn't back up every course, it removes the
course from the server instead, and its link stops working. **Remove from this device** moves a course to local
trash without deleting its server copy. **Delete everywhere** queues a server deletion for review; other devices
also review it before moving their copies into local trash. Server deletions keep content and reports for recovery.

**Several people on one server.** Give each person their own token, labelled with their name:

```bash
docker compose exec share flashcert-share token create --label alice
```

Each person enters the server address and their token in their app. Everyone's public courses appear together on
the home page.

**Each token owns its courses.** A course belongs to the token that first sent it. Only that token sees it in the
sync API, changes it, downloads it, deletes it and reads its error reports; for another token it doesn't exist.
Use the same token on all of one person's devices so they sync together. `token list` shows each token's label,
last use and number of courses. `token revoke ID` stops one token from syncing without affecting the others: its
courses stay online, and `owner NEW_ID --from ID` hands them to another token (a replacement phone, for instance).

Courses sent before owners existed (version 0.1) belong to the server's only token if there is just one. Otherwise
an identical resend can claim one. Assign courses explicitly before changing their content: `courses` shows each
course's owner, and `owner TOKEN_ID UID…` sets it by hand.

## Your data

The database is not inside the container. With the default SQLite, it is a file in a Docker **volume**: a separate
storage area that Compose mounts at `/data` and keeps when the container goes away. So the data survives
`docker compose restart`, `docker compose down` then `up`, upgrades (`pull`) and reboots of the server.

It is lost only when the volume itself is deleted:

* `docker compose down -v` (the `-v` deletes the volumes) or `docker volume rm`.
* Compose names the volume after the folder: `flashcert-share_data` for a folder called `flashcert-share`. If you
  start the server from a folder with another name, it gets a new, empty volume. The old volume and its data are
  still there (`docker volume ls`). Go back to the original folder name, or set `COMPOSE_PROJECT_NAME` to the old
  folder name in `.env`.
* Without Compose, `docker run` needs `-v flashcert-share-data:/data`. Without it, every new container starts with an
  empty volume of its own.

**Backup** (the server keeps running):

```bash
docker compose exec share python -c "import sqlite3; b = sqlite3.connect('/data/backup.db'); sqlite3.connect('/data/flashcert-share.db').backup(b); b.close()"
docker compose cp share:/data/backup.db ./flashcert-share-$(date +%F).db
docker compose exec share sh -c 'rm -f /data/backup.db*'
```

Copy the `.db` file off the server. Don't copy `flashcert-share.db` directly from the volume while the server runs:
recent changes may still be in the `-wal` file next to it.

**Restore** (replaces everything on the server with the backup):

```bash
docker compose cp ./flashcert-share-2026-10-10.db share:/data/restore.db
docker compose exec share python -c "import sqlite3; sqlite3.connect('file:/data/restore.db?mode=rw', uri=True).backup(sqlite3.connect('/data/flashcert-share.db'))"
docker compose exec share sh -c 'rm -f /data/restore.db*'
docker compose restart share
```

The second command fails without touching anything if `restore.db` isn't there. Courses keep the links they had in
the backup. At their next sync, apps review missing courses or a server revision older than their accepted baseline.
A newer phone copy is retained until the person chooses how to reconcile it; device clocks don't pick a winner.

With PostgreSQL ([below](#postgresql)) the data is in the `pgdata` volume instead:
`docker compose exec db pg_dump -U flashcert flashcert > flashcert-share.sql`.

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
flashcert-share courses                    courses with their links and owners
flashcert-share owner TOKEN_ID UID… | --from TOKEN_ID | --unowned   give courses to a token
flashcert-share import FILE [--uid X] [--certification NAME] [--unlisted] [--owner TOKEN_ID]
flashcert-share reports [--all] [--resolve ID ...]
flashcert-share copy-db TARGET_URL         copy everything to another (empty) database
flashcert-share serve [--host] [--port] [--threads] [--proxies]   (what the container runs)
```

### PostgreSQL

`compose.yaml` has an optional `db` service. SQLite is plenty for a few dozen courses and their visitors; switch when
you want the data in a database server:

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

### Recoverable sync (0.3+)

Each course carries a monotonic `revision`. Existing-course writes, visibility changes, deletions and restores
must send `expected_revision`; missing revisions answer 428 and stale revisions answer 409. A new course starts
at revision 1 (`expected_revision: 0` when creating). Identical re-sends don't make another revision.

Large replacements answer 409 with `code: confirmation_required`, a `changes` summary and a `confirmation`
value bound to the proposed normalized content and visibility. `POST /api/v1/courses/{uid}/preview` takes the proposed document
and its expected revision and returns that summary before writing. After review, re-send the exact document
with its `confirmation`. Editing the document or advancing the server revision invalidates the approval.

Deletion is recoverable: `DELETE ...?reason=deleted&expected_revision=R&confirmation=delete-R-deleted` hides the
course and keeps its content and reports. Use `removed` instead of `deleted` to remove only the server copy.
`GET /api/v1/trash` lists retained deleted courses. `GET /api/v1/courses/{uid}/history` lists revisions and
their originating `X-FlashCert-Device`; `GET .../history/{revision}` previews a full snapshot.
`POST .../restore` takes `{revision: V, expected_revision: R, confirmation: "restore-R-V"}` and makes a new
current revision with `event: restored`, preserving the uid and share link. Devices must clear superseded
deletion markers when processing that restore; they must not delete a restored course again automatically.
To restore privately before re-sharing, include `visibility: "private"` and use confirmation `restore-R-V-private`;
then upload the separately reviewed current document. This avoids briefly republishing an older public snapshot.

Defaults: the last 20 revisions, trash retained 30 days, review at 30% rewritten notes or 20% removed/edited
questions or cards. Clearing an existing sheet also needs review. Settings are `FLASHCERT_SHARE_HISTORY_LIMIT`,
`FLASHCERT_SHARE_TRASH_DAYS`, `FLASHCERT_SHARE_NOTES_CHANGE_PERCENT` and `FLASHCERT_SHARE_ITEMS_CHANGE_PERCENT`.
Expired trash and excess live history are pruned on startup and writes; deletion tombstones remain to prevent
silent resurrection by an old device. `/status` advertises `safe_sync: 1` and the configured thresholds.
Clients negotiate `X-FlashCert-Sync-Version: 1`. Older clients can read summaries and create new courses, but
cannot silently change existing content, download replacements or receive deletion instructions that would
hard-delete local recordings. They must upgrade before participating in two-way sync.

Every `/api/v1` call takes `Authorization: Bearer <token>` and only sees that token's courses and their reports:
other people's courses answer 404 (403 when sending one). JSON in, JSON out; errors are `{"error": "…"}` with
400, 401, 403, 404, 409 (review/conflict), 413 (too large), 422 (course rejected), 428 (revision required) or 429.

| | |
|---|---|
| `GET /api/v1/status` | this token's connection status and counts, plus `safe_sync`, `token_id`, retention and review thresholds |
| `GET /api/v1/courses` | `{courses, gone}` for safe clients. Each course includes content metadata, `hash`, `revision`, `event` and report counts. `gone`: `{uid, reason, deleted_at, revision}`, with reason `deleted` or `removed`. Legacy clients receive summaries without `gone`. |
| `PUT /api/v1/courses/{uid}` | create or replace a course (body below). 201 when created, 200 otherwise. Existing changes require `expected_revision`; deleted courses require explicit restore. |
| `GET /api/v1/courses/{uid}` | the summary plus `content` as stored: what the app downloads |
| `PATCH /api/v1/courses/{uid}` | `{visibility, expected_revision}` without resending the course |
| `DELETE /api/v1/courses/{uid}?reason=removed\|deleted&expected_revision=R&confirmation=delete-R-REASON` | hide the course and retain its content/reports in trash; `gone` includes the deletion revision. `removed` leaves other devices' copies local; `deleted` queues a reviewed local deletion. |
| `GET /api/v1/reports?status=open\|resolved\|dismissed\|all&course={uid}&since_id={n}` | reports, oldest first: `{id, course_uid, element: {kind, id}, excerpt, message, author, status, created_at, updated_at}` |
| `PATCH /api/v1/reports/{id}` | `{"status": "resolved" \| "dismissed" \| "open"}` |
| `DELETE /api/v1/reports/{id}` | |

`uid` is the app's own stable id for the course (1–100 characters among `A-Z a-z 0-9 . _ : -`), e.g. a UUID
kept on the course row. The server answers with the public `url`, which never changes while the course is on the
server, including retained trash (a private course has one too; it works once the course is shared).

`updated_at` is when the server last stored new content; `modified_at` is when that content was edited on the
device that sent it (taken from the document, never later than the server's clock). It is informational;
revision numbers and the last accepted baseline drive conflict detection.

### Course document

```json
{
  "format": "flashcert-share-1",
  "title": "Hooks and guardrails",
  "certification": "Claude Agent SDK",
  "lang": "en",
  "visibility": "public",
  "position": 3,
  "modified_at": 1760000000000,
  "expected_revision": 0,
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
* `visibility` is `public`, `unlisted` or `private`. `certification`, `lang`, `position` (order inside the
  certification), `modified_at` (last edit on the device, milliseconds since the epoch), `sheet`, `questions` and
  `cards` are optional.
* Unplayable questions and cards are dropped and listed in `warnings` in the response. Transcript, analysis and
  costs are never stored, even when sent (a `flashcert-course-1` bundle is accepted as is).

The response is the course summary plus `created`, `changed` and `warnings`. `hash` changes only when what
visitors see changes, so the app can compare it with a hash of its own (or simply re-send: identical content
leaves `updated_at` alone).

App flow: a **Sharing** section in Settings holds the server URL and token (with "Test connection" via `/status`).
Each sync first plans changes using `GET /api/v1/courses`, the last accepted `hash`/`revision` and its local
fingerprint. Safe automatic mode applies small unambiguous updates with guarded writes and local snapshots;
conflicts, large changes and deletion events wait for explicit review. Review-everything stages all changes;
paused mode applies none. A stale preview must be refreshed, including when a server backup rewinds revisions.
**Share** on a course sends `PUT` and opens the system share sheet with the returned `url`. Reports come from
`GET /api/v1/reports?since_id=<last seen>`.

## Public endpoints

`GET /api/public/courses`, `GET /api/public/courses/{share_id}` (what the pages use; private courses answer 404) and
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
