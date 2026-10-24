# flashcert-share

A small server that publishes FlashCert courses as read-only web pages, and keeps a copy of them for the app.
The FlashCert app syncs its courses (notes, revision sheet, question bank, flashcards) with a token, both ways:
the server is a backup of the phone and carries courses between devices. A shared course gets its own link, and
anyone with the link can read it, take the quizzes and study the flashcards. Visitors have no account and can't
generate anything. They can flag an error on any element, and the app collects those reports.

* `/` lists the **public** courses, grouped by certification.
* `/c/<id>` is one course: **Notes**, **Revision**, **Quiz**, **Cards**. Every answer and card opens the passage of
  the notes it rests on, highlighted, without leaving the quiz.
* **Add to FlashCert** on a shared course or certification hands it to the app, which adds this server by itself:
  a copy, a copy that follows the author's changes, or (when the owner allows it) the course itself, to edit it with
  them (see [Sharing with other people](#sharing-with-other-people)).
* Visitor progress stays in the visitor's browser, and only once they agree. It counts successes in a row as the app
  does, so visitors without the app or an account master questions, cards, courses and certifications too, with the
  app's celebrations. Turning it on also asks the browser for *persistent storage*; the page explains why.
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
docker compose exec share flashcert-share token create --label alice   # prints the token, once
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
the home page. The label is the name the people they share with see: the app replaces it with the name set in its
settings, and `token rename ID --label NAME` changes it here.

**Each token owns its courses.** A course belongs to the token that first sent it. Only that token sees it in the
sync API, changes it, downloads it, deletes it and reads its error reports; for another token it doesn't exist.
Use the same token on all of one person's devices so they sync together. `token list` shows each token's label,
last use and number of courses. `token revoke ID` stops one token from syncing without affecting the others: its
courses stay online, and `owner NEW_ID --from ID` hands them to another token (a replacement phone, for instance).

Courses sent before owners existed (version 0.1) belong to the server's only token if there is just one. Otherwise
an identical resend can claim one. Assign courses explicitly before changing their content: `courses` shows each
course's owner, and `owner TOKEN_ID UID…` sets it by hand.

## Sharing with other people

A link to a course (`/c/<id>`) or to a whole certification (`/cert/<id>`) has an **Add to FlashCert** button. It
opens the app (`flashcert://add?url=<the page>`), which adds this server to its list by itself: nobody types an
address or a token. What the button offers is the owner's choice for that course or certification, within what the
server allows (`FLASHCERT_SHARE_LINK_ACTIONS`, `copy,follow,edit` by default):

* **copy**: the app makes a course of its own from the page. Edits to it never touch the original; it backs up to
  the person's own server, under a new id, if they have one.
* **follow**: a copy that also takes the author's later changes (the app asks with `If-None-Match`). Questions and
  cards keep their ids, so progress on them survives an update; a copy edited on the phone asks before taking one.
* **edit**: the person joins the course as an **editor**: their app syncs the course itself, and their changes reach
  everyone who has it. Someone who already syncs with this server gets the access on their token; anyone else gets
  a new **guest** token. Only when the owner chose it (a course that never chose keeps the server's other defaults),
  and never on a public page: a listed course can be found by anyone. Editors show among the owner's members and
  can be removed there.

The link has no expiry and no count: it works for whoever has it while the course or certification is shared and
offers it. Stopping the share (private) ends it, and the access of those who joined from it.

* There is **no sign-up**. A guest token only exists because someone joined from a link, and it only reaches what
  it was given: it can't send courses of its own. `token promote ID` turns a guest into a user.
* **Editors** send changes: each one is a new revision recorded with its author (`history`), recoverable like the
  owner's, and editors read the course's error reports. An owner can make one a **viewer**
  (`PATCH /api/v1/memberships/{id}`), who only syncs the course down. Only the owner decides who sees the course,
  where it is listed, who is a member, and deletes it.
* A **certification** is shared as a whole: every course of its owner in that certification, now and later,
  except private ones, and only while the certification itself is shared. Renamed in the app, it keeps its link
  and the people it is shared with.
* When access ends (the member is removed or leaves, the course is deleted, made private while joined from its
  link, or moved out of the certification), the member's devices are told the course is `revoked` and keep their
  copy, detached.
* Study progress never leaves anyone's phone: each person keeps theirs on a shared course.
* People appear under their token's label: the owner on shared courses, members in the owner's list,
  editors in the history. The app sends the name set in its settings (`PATCH /api/v1/me`); a guest token starts
  with the name given when joining.

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
| `FLASHCERT_SHARE_LINK_ACTIONS` | `copy,follow,edit` | The most **Add to FlashCert** may offer: any of `copy`, `follow`, `edit`, or `none`. Owners can offer less; `edit` only when they choose it, never on public pages. |
| `FLASHCERT_SHARE_JOINS_PER_HOUR` | `10` | Joins from a link allowed per visitor IP per hour |
| `FLASHCERT_SHARE_PORT` | `8080` | Host port (compose only) |
| `FLASHCERT_SHARE_IMAGE` | `ghcr.io/valkea/flashcert-share:latest` | Image (compose only) |

## Command line

Inside the container: `docker compose exec share flashcert-share …`

```
flashcert-share token create [--label NAME] | token list | token revoke ID | token promote ID
flashcert-share token rename ID --label NAME   the name the people sharing with this token see
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

The web UI shares the Android app's spiral logo, Space Grotesk headings, rounded cards and pill controls.
The header offers the same nine palettes (Flashy by default) and a System / Light / Dark appearance selector.
System follows the browser's theme preference; explicit Light or Dark overrides it. Both choices are remembered.
Fonts and branding assets are served locally. To refresh them from the Android checkout:

```bash
python3 tools/sync_branding.py ../FlashCert-android
```

| | |
|---|---|
| Notes | The course notes, with a table of contents, formulas (KaTeX, loaded only when a course has some) and a flag on each section |
| Revision | Essentials, definitions, structure blocks (tables, steps, timelines, formulas, diagrams, rules, concepts) and "don't mix these up" |
| Quiz | 5 / 10 / 20 / all questions from the bank: single choice, multiple choice, true/false, fill the blank, ordering. Questions recovering from a mistake come first, then never-asked ones, then due ones (the app's intervals), and "Retry my mistakes" replays the missed ones. Each answer shows the explanation and **Read the passage**. |
| Cards | Flip, then "I knew it" / "Still learning"; restudy the ones still being learned. Cards come in the same order as questions, in random order within each group. |
| Successes in a row | As in the app: each question and card counts its correct answers in a row, at most one per study day (which starts at 4 a.m.), and a mistake starts the count again. At its target (the author's certification's, else 3) an item is **Mastered**, and a course is mastered once all its items are (cards count only with a target). The course page shows its bar; the course lists show what is mastered. |
| Celebrations | A mastered question or card gets its tag with a pop and sparks (a card, a stamp); a mastered course fills its bar and throws confetti, with a link to its certification's page; there, once every course is mastered, a recap (courses and items mastered, days studied, day streak) shows once per course count. "Reduce motion" shows each end state without animation; **Show celebrations** in **Your progress** turns them off. |
| Importance | Once the app has a checklist for the course: **All**, **Essential**, **Important**, **Details** and **Other** chips on Notes, Quiz and Cards, as in the app. From All a tap shows that level alone, further taps add or remove levels; one choice applies to the three tabs. Quiz and Cards keep the items of the chosen levels (with their counts); Notes dims the lines and key points of other levels (headings and chapters without a checklist never are). |
| Progress | As in the app's progress screen, for this course. **Now**: share right and answers over 30 days with their change from the 30 days before, items mastered, day streak, and today's questions and cards to review (one tap opens them). **Past**: answers per day, by question type (the weakest marked), weakest notions (each with **Practise**, mistakes first), flashcards mastered / learning / new, quizzes. **Future**: item encounters over the next 14 days, assuming right answers. Every item counts, whatever importance is shown. |
| Report an error | On the course, a notes section, a sheet item, a question or a card: a message and an optional name. A hidden honeypot field and a per-IP limit keep bots out. |

`/progress` (**See my progress** in **Your progress**) gathers the same figures across courses, the courses grouped
by certification with their bars and what to review today, and the reviews to come. It reads only this browser:
the server knows nothing of it, and a course shows up once it has been answered on its page.

Progress lives under `localStorage` keys starting with `fcs:` (`fcs:course:{share id}` per course, with its items, a
daily log of answers and the title it is listed by, `fcs:days` for
the study days, `fcs:cert:{share id}` for a recap already shown), written only after the visitor clicks
**Save my progress**. Mastering takes several study days, so in memory alone it rarely happens. Before that, answers are kept in memory for the visit and a small banner offers to save
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

Defaults: the last 20 revisions, trash retained 30 days, review at 30% rewritten notes or sheets, or 20% removed/edited
questions or cards. Clearing an existing sheet also needs review. Settings are `FLASHCERT_SHARE_HISTORY_LIMIT`,
`FLASHCERT_SHARE_TRASH_DAYS`, `FLASHCERT_SHARE_NOTES_CHANGE_PERCENT` and `FLASHCERT_SHARE_ITEMS_CHANGE_PERCENT`.
Expired trash and excess live history are pruned on startup and writes; deletion tombstones remain to prevent
silent resurrection by an old device. `/status` advertises `safe_sync: 1` and the configured thresholds.
Clients negotiate `X-FlashCert-Sync-Version: 1`. Older clients can read summaries and create new courses, but
cannot silently change existing content, download replacements or receive deletion instructions that would
hard-delete local recordings. They must upgrade before participating in two-way sync.

Every `/api/v1` call takes `Authorization: Bearer <token>` and only sees that token's courses and their reports:
other people's courses answer 404 (403 when sending one), unless they were shared with it (below). JSON in, JSON out; errors are `{"error": "…"}` with
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

### Shared courses (sync version 2)

With `X-FlashCert-Sync-Version: 2`, `GET /api/v1/courses` also lists the courses shared with the token. Every
summary has a `role` (`owner`, `editor`, `viewer`); shared ones add the `owner`'s label, and the owner's own carry
`link_actions` (their choice, `null` for the server's) and `actions` (what the link offers). `gone` adds
`{uid, reason: "revoked", revision}` when a shared course is no longer reachable: keep the local copy, detached.
`GET`, `history` and revisions work for members; `PUT`, `preview` and `restore` for editors, whose `PUT` keeps the
owner's `visibility`, `certification` and `position`. A guest token's `PUT` of a new course answers 403
`code: guest`; a viewer's 403 `code: read_only`. `/status` adds `sync_version: 2`, `kind` (`user`/`guest`),
`site_name`, `link_actions` and `certification_uids: 1`.

Certifications have the app's own stable id, `certification_uid` in the course document (same characters as a
course `uid`): a course belongs to its owner's certification with that id, and one sent without it (older apps) to
the certification of that name. The server keeps one certification per owner and name. When two devices sent the same
name with different ids, the first one stays, and course summaries say which: `certification_uid`, with
`certification_name`, the certification's current name (`certification` is the name the course was last sent
with, until it is sent again after a rename). A rename goes through `PUT /api/v1/certifications/{uid}` before the
courses are sent again with the new name.

| | |
|---|---|
| `PATCH /api/v1/me` | `{name}` (1–100 characters): this token's label, the name other people see |
| `PATCH /api/v1/courses/{uid}` | also `{link_actions: [..] \| null}` (the owner) |
| `GET /api/v1/members?course=uid` or `?certification=name&certification_uid=id` | `{members: [{id, token_id, label, guest, role, …}]}` |
| `PATCH /api/v1/memberships/{id}` | `{role}` (the owner) |
| `DELETE /api/v1/memberships/{id}` | the owner removes a member, or the member leaves |
| `GET /api/v1/memberships` | what was shared with this token: `{id, role, owner, course: {uid, title} \| certification: {uid, name, share_id}}` |
| `PUT /api/v1/certifications` | `{name, uid, visibility, link_actions}` → `{uid, name, share_id, url, visibility, courses, link_actions, actions}`; `private`: not shared |
| `PUT /api/v1/certifications/{uid}` | `{name, previous}`: renamed in the app from `previous` (`null`: only tells its id). Answers the certification as it is now: another device's name when it renamed it since, the server's `uid` when another device sent it first, its `previous` name finding one the server has no id for yet. 409 `code: name_taken` when another certification in use (courses, members or a page) has the name; one not in use gives it up |
| `GET /api/v1/certifications` | the owner's certifications shared, or with members |

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
  "certification_uid": "5f0c2a1e-…",
  "lang": "en",
  "visibility": "public",
  "position": 3,
  "modified_at": 1760000000000,
  "expected_revision": 0,
  "notes": "## Section\n\n### Subsection\n...",
  "sheet": { "essentiel": [], "definitions": [], "structure": [], "pieges": [] },
  "questions": [
    { "id": "q12", "type": "mcq", "q": "…", "answers": ["…", "…"], "correct": 1,
      "explanation": "…", "notion": "…", "difficulty": 2, "objective_ids": ["…"], "importance": 3,
      "sources": [{ "section_id": "2.1", "quote": "…", "verified": true }] }
  ],
  "cards": [
    { "id": "c7", "front": "…", "back": "…", "notion": "…", "sources": [{ "section_id": "1.1", "quote": "…" }] }
  ],
  "notions": [{ "chapter": "Section", "quote": "…", "importance": 3 }],
  "review_targets": { "questions": 3, "cards": 3 }
}
```

* Questions and cards use the app's own JSON (`mcq`, `multi`, `true_false`, `cloze`, `order`; the older single
  `source` object is accepted). Give each a stable `id`, such as the database row id, so reports point at the
  right item across syncs. Items without one get an id from their text.
* `objective_ids` (optional, on questions and cards) are the app's coverage checklist notions an item tests. The
  server keeps them for the app and leaves them out of the pages; a change to them alone doesn't count as an
  edit when deciding whether a replacement needs review.
* `importance` (optional, on questions and cards) is the highest level of the checklist notions an item tests:
  `3` essential, `2` important, `1` detail, `0` none. `notions` (optional) lists the checklist's notions with the
  `##` chapter heading they belong to, a `quote` of the notes and their `importance` (`1`–`3`). The app sends
  both once a course has a checklist; the pages then filter by importance. Like `objective_ids`, a change to
  them alone isn't an edit for review.
* `review_targets` (optional) are the successes in a row the course's certification asks of each question (`1`–`100`)
  and card (`0`–`100`, `0` leaving cards out of mastery). The pages use them to count mastery as the app does, and
  the app's defaults (3 and 3) without them. Out-of-range values are dropped with a warning.
* A valid item comes back from `GET /api/v1/courses/{uid}` exactly as it was sent, so the app can compare its copy
  with the server's. Older servers (0.3) filled in empty `sources`, `accept` and `bank` lists and sorted `multi`
  answers; change reviews treat those differences as no edit.
* `visibility` is `public`, `unlisted` or `private`. `certification`, `certification_uid`, `lang`, `position` (order inside the
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

`GET /api/public/courses/{share_id}` also gives `url`, `revision`, `hash`, `modified_at`, `position`, `actions` and
`server: {name, url}`, with an `ETag` (`If-None-Match` answers 304). `GET /api/public/certifications/{share_id}`
lists a shared certification's courses the same way. `POST /api/public/join` takes `{course: share_id}` or
`{certification: share_id}`, when its link offers `edit`, and an optional `name`; with a bearer token the access goes to that token (and `name` renames it), otherwise the answer
carries a new guest `token` labelled with `name`, once (201).

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
