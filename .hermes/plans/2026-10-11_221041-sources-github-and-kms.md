# Knowledge sources: GitHub and knowledge-management systems

> **Status: future work — not implemented, not scheduled.** Recorded so the work is
> unambiguous when it is picked up. For Hermes: implement task-by-task with the
> `subagent-driven-development` skill, never skipping the verification steps.

**Goal:** get knowledge into the base from where it actually lives — GitHub (repos, issues,
PRs, releases, wikis) and knowledge-management systems (Confluence first, then Notion,
SharePoint, Jira, Slack/Teams, MediaWiki, BookStack, Outline, Zendesk, ServiceNow) — with the
same contract the PDFs already have: every hit traceable to its source, page/item and version.

**Architecture:** today a *source* yields **files** that are then extracted like PDFs. GitHub
and Confluence yield **documents** (pages, threads, releases) that change over time, so the
connector contract gains a second shape: an item list with an id, a version and an update
time, a fetch that renders the item to text, and an incremental re-scan that only refetches
what changed. Storage, search, citations and the UI stay as they are.

**Tech stack:** Python 3.12 · `requests` (already a dependency) · `python-frontmatter`-free
markdown handling · the existing SQLite + vector store · Starlette routes and the same
connector registry.

**Depends on:** `.hermes/plans/2026-10-11_205850-more-file-types.md` — HTML/Markdown e-mail
extraction and the **locator** (a label in place of a bare page number) are prerequisites;
without them a Confluence page has nowhere to point.

---

## Current state (verified, `~/pdfcomp`)

| where | what it does today |
|---|---|
| `pdfkb/sources.py:connectors()` | the selectable types, each `{id,label,category,available,install,note,fields[]}` — 8 today: `local`, `http`, `webdav`, `ssh`, `ftp`, `smb`, `s3`, `rclone`. The UI renders the fields from this list, so a new type needs no UI work. |
| `pdfkb/sources.py` | per-type listing (`_list_http`, `_list_webdav`, the ssh `find`, the s3 `list_objects_v2`, …), `fetch(cfg, remote, dest_dir) -> Path` copying one file into the active library's `sources/`, `put()` writing back, `test(cfg)` reporting reachability. |
| `pdfkb/web.py` | `POST /api/sources/scan` (list what a source holds), `POST /api/sources/ingest` (fetch the ticked items and `add_document` them), `sources_list/save/delete/test/browse`. |
| `pdfkb/store.py` | `documents(doc_id, path, filename, sha1, npages, nchunks, title, added_at, extractor)` — `path` is a **local file**, and the citation is `filename + page`. |

Two gaps that define the work: the scan result is a list of **file paths**, and a document's
identity is a **local file** rather than "page 12 of this Confluence page at version 7".

## Constraints

- **Never delete user data.** An item that disappears upstream is marked *stale* (or
  *missing upstream*), never silently purged; a purge only follows an explicit delete.
- **Secrets stay out of git.** Tokens live in `integrations.json` (mode 0600) and are masked
  in API responses; the repo must never grow a credential (`.gitignore` already covers `.env`).
- The **one-writer rule** holds: a sync writes only into the active knowledge base.
- Verification must not need the internet or real credentials — see the stub in Task 5.
- Additive migrations only (`ALTER TABLE … ADD COLUMN`), so existing bases keep working.

---

## Task 1: a connector can return documents, not just files

**Objective:** make the source contract cover remote documents.

**Files:** `pdfkb/sources.py` (the listing functions), `pdfkb/web.py` (the scan/ingest
routes), `pdfkb/store.py` (columns).

**Step 1 — the item shape.** A scan returns a list of items; a file item keeps its current
meaning, a document item carries more:

```python
{"id": "confluence:PROJ:123456", "title": "Release checklist",
 "kind": "docx" | "markdown" | "html" | "pdf", "remote": "123456",
 "version": "7", "updated": "2026-10-11T09:12:00Z", "url": "https://wiki.example/pages/123456",
 "bytes": 4096}
```

**Step 2 — the columns** (additive, all nullable so old rows are unaffected):
`documents.source_id`, `source_kind`, `source_url`, `external_id`, `external_version`,
`external_updated`, `fetched_at`, `stale` (0/1).

**Step 3 — `fetch` writes the rendered text.** A document item is rendered to markdown and
written into `sources/` as `<slug>-<hash8>.md`; `documents.source_url` keeps the page URL so
the citation is a link and the modal's *Open in new tab* leaves the app when the source has a
URL. The local copy stays the preview fallback.

**Step 4 — verify:** with the stub from Task 5, a document item ingests, is searchable, and
its hit carries `source_url` + `external_version`; a PDF item behaves exactly as today.

---

## Task 2: GitHub

**Objective:** index a repository — its documents, its issues and PRs, its releases, its wiki.

**Step 1 — the connector declaration** in the registry (no UI work):

```python
{"id": "github", "label": "GitHub (repo, issues, PRs, wiki)", "category": "Knowledge",
 "available": True, "note": "personal access token, read-only is enough",
 "fields": [{"key": "token", "label": "personal access token", "secret": True},
            {"key": "repo", "label": "owner/repo", "placeholder": "mokmail/pdfcomp"},
            {"key": "include", "label": "what to take",
             "placeholder": "files, issues, prs, releases, wiki"},
            {"key": "paths", "label": "file filter (optional)", "placeholder": "docs/**, **/*.md"},
            {"key": "since", "label": "only newer than (optional)"}]}
```

**Step 2 — take the cheap, high-signal things first**
`GET /repos/{owner}/{repo}/contents/{path}` for files (markdown, text, and any PDFs),
`GET /repos/{owner}/{repo}/issues?state=all&since=…` (issues **and** PRs share this endpoint —
filter on `pull_request`), `GET /repos/{owner}/{repo}/pulls` + `/comments` for review threads,
`GET /repos/{owner}/{repo}/releases`, and the wiki through `/repos/{owner}/{repo}/contents`
of the `<repo>.wiki.git` repository (or the wiki pages API where available).

**Step 3 — one thread is one document.** An issue with its comments becomes one markdown
document whose blocks are the body and each comment; the locator is `issue #123 · comment 4`,
so a hit points at the exact comment, not the whole thread.

**Step 4 — be a good API citizen.** Store the `ETag` (or `updated_at`) per item and send
`If-None-Match` on re-scan: a second scan of an unchanged repo then returns `304`s and costs
no rate limit. Back off on `429`/`403`-with-`Retry-After`; one request in flight per connector.

**Step 5 — verify:** the stub serves two issues and one file, changes one of them, and the
second scan must fetch **only the changed item** (the stub records every request path).

Optional later: `POST /api/hooks/github` with HMAC (`X-Hub-Signature-256`, secret in the
connector config) so a push re-indexes that item within seconds.

---

## Task 3: knowledge-management systems — Confluence first

**Objective:** the same shape for KMS APIs, starting with the one this user actually has.

**Step 1 — Confluence** (`wiki`, `/rest/api/content`):
* list with CQL — `cql=space=PROJ and type=page and lastmodified > "2026-10-01"`,
  `expand=version,body.storage,_links`, paginated with `limit`+`start`;
* Cloud auth = email + API token; Data Center = personal access token (`Bearer`);
* the item's `version.number` is the `external_version`, `_links.webui` is the page URL;
* the page body is storage-format XHTML → text through the HTML extractor from the non-PDF
  plan, with the heading path as the locator (`§ Release checklist / Rollback`).

**Step 2 — then, in this order** (each is a variation of the same code):
Notion (`POST /v1/search`, `last_edited_time`), SharePoint/OneDrive via Microsoft Graph
(`/sites/{site}/drive/root/children` + `/sites/{site}/pages`), Google Drive/Docs
(`files.list` + `files.export` to text), Jira (`/rest/api/2/search?jql=…`, issues as
documents), Slack/Teams (export or API: channels as documents, messages as blocks),
MediaWiki (`api.php?action=query&list=allpages`), BookStack / Outline (`/api/pages`),
Zendesk (`/api/v2/help_center/articles`) and ServiceNow (Knowledge API).
For anything with a web UI and no API: an **HTTP crawl** connector (sitemap.xml → pages) —
the existing `http` connector already walks links, so this is a depth + type filter.

**Step 3 — incremental sync.** Per source, keep `last_sync` and a per-item `external_version`
in `settings.json`; a re-scan asks the API only for what changed (`lastmodified >`, `since=`,
`last_edited_time`, Graph `$filter`). A sync is **resumable**: a cursor is stored so a 10 000
page space can be taken in chunks of ~500 with a visible counter and a *Continue* button —
never a silent truncation.

**Step 4 — verify:** the stub answers the CQL listing with three pages at two versions; after
editing one page the second scan must fetch one page, and the previously indexed page keeps
its old chunks until the new version is stored (no gap where the page is unsearchable).

---

## Task 4: provenance and the UI

**Objective:** a hit on a Confluence page or a GitHub issue cites the *page*, not a local file.

- The result row and the modal footer show `title · locator · version · source` and link
  `source_url`; the local copy is what the preview iframe shows.
- The Sources page lists per source: how many items, when it last synced, how many are stale,
  and a *Sync now* button.
- A stale item is flagged in the document list (`missing upstream`) with the option to delete
  it — the app never removes it on its own.

## Task 5: verification without the internet

**Objective:** prove all of it offline.

Build a **stub server** in the probe (the same trick used for `docling-serve`) that answers
the GitHub and Confluence REST shapes, records every request, and can be told to change one
item. Then assert: scan lists the items, ingest stores them with the right `external_version`
and `source_url`, search finds a phrase inside a comment (locator `comment 4`), a re-scan hits
only the changed item, an upstream deletion marks stale rather than purging, and the same run
still passes every existing check.

---

## Risks, tradeoffs, open questions

1. **Volume.** A Confluence space or a big repo can be tens of thousands of items. Cap per
   sync, make it resumable, and keep the cap visible.
2. **Rate limits and ToS.** GitHub 5 000 requests/hour authenticated, Confluence much tighter;
   conditional requests are not optional, they are the design. Wikis and issue trackers are
   also *user content* — index what the token can already read, nothing more.
3. **Content fidelity.** Confluence storage XHTML and Notion blocks are not markdown; expect
   to lose tables and macros. Keep the raw payload only if it is cheap — otherwise re-fetch.
4. **Identity.** A page's identity is `(connector, external_id)`, not its title: titles get
   renamed, ids do not. Version changes update the document rather than creating a second one.
5. **Which to build first.** GitHub is the cheapest and the most useful here (the tool's own
   repo); Confluence is the one the user's workplace actually has. Both are worth more than
   the long tail — do not start with ServiceNow.
6. **Auth surface.** Tokens in `integrations.json` (0600), masked in the API, never in git;
   a token that can read a whole tenant is a big blast radius, so default to a single space
   or repo per connector and say so in the field's note.
