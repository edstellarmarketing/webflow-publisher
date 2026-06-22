# Webflow CMS Publisher

A Streamlit app for pushing content into any Webflow CMS collection — blog HTML
articles, Webflow-ready HTML, pre-formatted CSVs, or a structured CMS-fields
Markdown table. Built for editorial / SEO workflows where one writer drafts in
HTML or a spec doc and another needs to publish it into a specific CMS field
schema without touching the Designer.

![Streamlit](https://img.shields.io/badge/Streamlit-1.30%2B-FF4B4B?logo=streamlit&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![Webflow](https://img.shields.io/badge/Webflow-API_v2-146EF5?logo=webflow&logoColor=white)

---

## What it does

Five input formats, one push pipeline:

| Mode | Source | Behavior |
|------|--------|----------|
| **Raw HTML (auto-converts)** | A blog/article HTML file | Classifies every top-level block as **plain rich text** or **embed**, applies structural conversions (key takeaways → `.takeaway`, eval grid → `.criteria`, company-profile → `.co-card`, etc.), wraps embed blocks with `<div data-rt-embed-type="true">`, injects required CSS once, and converts attribute quoting to Webflow's single-quote style. |
| **Webflow-Ready HTML (direct push)** | HTML already formatted with `data-rt-embed-type` wrappers | Parsed into blocks for inline editing, then pushed as-is. |
| **CSV (pre-formatted)** | CSV with a `content` column | Rows concatenated, parsed into blocks. |
| **Push CMS Fields (.md)** | Markdown table mapping CMS field name → input type → value | Fetches the live collection schema, fuzzy-matches each row to a real field slug, resolves Option/Reference values via the API, applies type-aware transformations, then pushes only the rows you tick. |
| **Bulk Push CMS Fields (.md, up to 5)** | 1–5 .md files at once | Same pipeline as single Push CMS Fields, batched: schema fetched once, reference lookups cached across all files, all updates sent in one `PATCH /items`, all creates in one `POST /items`. Per-file expander with field preview, per-item ✅/❌ result table. |

Built-in support for:
- **H2 section selection** — preview the article's H2s and untick the ones you don't want pushed.
- **Per-block editing** — edit any plain or embed block in a `text_area` before push, with a 10 000-char limit warning per embed.
- **Find by slug** — locate an existing item to update via the Webflow `?slug=` filter (with a paginate fallback for older API versions).
- **Upsert** — create-new automatically updates if the slug already exists.
- **Draft / Live toggle** — sidebar checkbox routes updates through `/items` (staged) or `/items/live`.
- **Schema inspector + reference value diagnostic** — shows every field type in the collection and the available values for every referenced collection.
- **Credential persistence** — opt-in checkbox stores the API token + collection ID at `~/.webflow_publisher.json` so a page reload or next-course push doesn't force a retype. **Untick to delete the file.**
- **"Push another item" reset** — clears per-item state without losing credentials.

---

## Quick start

```bash
git clone https://github.com/edstellarmarketing/webflow-publisher.git
cd webflow-publisher
pip install -r requirements.txt
streamlit run app.py
```

The app opens at <http://localhost:8501>.

### What you need from Webflow

| Thing | Where to find it |
|-------|------------------|
| **Site API Token** | Webflow Dashboard → Site Settings → Apps & Integrations → API Access → Generate Token. Needs **CMS read + write** scope. |
| **Collection ID** | Webflow Designer → CMS → [Your Collection] → Settings (cog) → look at the URL or the Webflow API tab. A 24-char hex string like `64ac3a242208dda62b6e6a90`. |

Both go in the sidebar. Click **🧪 Test API Connection** to verify the token has access and to see how many items the collection contains.

---

## The four input modes in detail

### 1. Raw HTML → auto-converts to Webflow-Ready

Paste in a complete blog article HTML file (full document or fragment). The pipeline:

1. **Normalize** — unescape entities, extract `<body>` or `<article>` content.
2. **Classify each top-level block** — known classes (`takeaway`, `criteria`, `co-card`, `testimonial`, `faq`, `cta-block`, `steps-list`, etc.) or any `<div>` with a class become **embed blocks**; `<h1-h6>`, `<p>`, `<ul>`, `<ol>`, `<blockquote>`, `<figure>` are **plain rich text**.
3. **Convert old-format → Webflow-template format** — e.g. `.key-takeaways` becomes `.takeaway` with a "💡 KEY TAKEAWAYS" header, `.eval-grid` becomes `.criteria` (first 6 cards only — drops region-specific extras), `.company-profile` becomes `.co-card` with the `.co-hdr` / `.co-logo` / `.meta-row` / `.chip` structure, `.expert-quote` becomes `.testimonial`, `<details>` becomes a schema.org-marked `<section class="faq">`.
4. **Inject CSS once** — `co-card` and `criteria` styles emit at the first occurrence and are re-used.
5. **Decorate links** — external links get `rel="nofollow" target="_blank"`; edstellar.com links get `target="_blank"` only.
6. **Quote normalization** — converts double-quoted attributes to single quotes (with apostrophe encoding) so Webflow's API accepts them; the `data-rt-embed-type` attribute is restored to double quotes.

Output is parsed back into a list of editable blocks. You can untick H2 sections to drop them, edit any block inline, and download the final Webflow-ready HTML before pushing.

### 2. Webflow-Ready HTML → direct push

If your HTML is already wrapped (`<div data-rt-embed-type="true">…</div>` around every embed), this mode skips the conversion pipeline and lets you push as-is. Useful when another tool produced the HTML.

### 3. CSV → block list

A simple CSV with a `content` column. Each row's `content` is concatenated into one HTML string and parsed into blocks. Useful for templated batch-content workflows.

### 4. Push CMS Fields (.md)

Probably the most powerful mode. Upload a Markdown file containing a table like:

```md
| Section | Field Name | Input Type | Course Content |
|---------|------------|------------|----------------|
| Hero | Name | Text | Burnout Prevention and Recovery Training |
| Hero | Slug | Text | burnout-prevention-and-recovery-training |
| SEO | Meta Title | Text | Burnout Prevention Training — Edstellar |
| SEO | Meta Description | Text | A 2-day workshop on… |
| Content | Course Description | Text | Helps managers… |
| Content | Courses Card Pointers | Rich Text | Bullet list - Recognize early signs; Build recovery habits; Lead resilient teams |
| Content | Overview | Embedded (Custom Code) | `<div data-rt-embed-type='true'>…</div>` |
| Trainers | Trainers Heading | Text | Meet the Experts Who'll Train Your Team |
| Trainers | Trainers Paragraph | Text | Our team learns from certified trainers… |
| FAQ | FAQ's | Embedded (Custom Code) | `<div class="faq-item">…</div>` |
```

The app:
1. Fetches the live collection schema via `GET /collections/{id}`.
2. Builds a display-name → slug lookup, with three-tier fuzzy matching (exact normalized match → derived slug match → longest-substring match).
3. For each row: parses the value based on `Input Type` (strips backticks for embeds, converts `Bullet list - a; b; c` rich-text rows into `<ul>`), then resolves Option / Reference / MultiReference values against the live API.
4. Applies schema-type-aware post-processing (see [Field-type rules](#field-type-rules) below).
5. Shows a per-field checklist. Identity / structural fields (Name, Slug, Canonical, Delivery Type, Course Level/Type/Category etc.) default to **off** in Update mode and **on** in Create mode. **Select all / Deselect all** buttons available.
6. Push as Update (PATCH against an existing slug) or Create (POST; auto-falls-back to update if the slug already exists).

#### Field-type rules

These run automatically after schema lookup — **no need to configure**:

| Webflow field type | Treatment |
|--------------------|-----------|
| `PlainText`, `Email`, `Link`, `Phone` | **All HTML stripped**, value pushed as bare text. Catches the common bug where a Rich Text source value leaks `<div>`/`<p>` markup into a plain-text field. |
| `Option` | Value matched to one of the field's defined options by name/id (case-insensitive, with kebab-case fallback). |
| `Reference`, `ItemRef`, `ItemReference`, `CollectionItem` | Value (slug or name) looked up in the referenced collection; the item's ID is pushed. |
| `MultiReference`, `ItemRefSet`, `MultiItemRef`, `MultiCollectionItem` | Value is `;`-separated; each is resolved to an item ID. |
| `RichText` + display-name matches FAQ (FAQ, FAQs, FAQ's, FAQs Section, …) | Value wrapped with `<div data-rt-embed-type="true"><section class="faq" itemscope itemtype="https://schema.org/FAQPage">…</section></div>` — required for Webflow Rich Text fields to render the `<section>` instead of stripping it. Idempotent. |
| `RichText` + display-name matches Trainer Paragraph (Trainer/Trainers + Paragraph/Para) | All HTML stripped — keeps prose plain. |
| All other RichText | Pushed as-is. |

Pattern matching is forgiving: plural ("Trainers"), apostrophe ("FAQ's"), short-form ("Trainers Para") all match.

### 5. Bulk Push CMS Fields (.md, up to 5)

Same pipeline as **Push CMS Fields (.md)**, batched. Drag-and-drop 1–5 `.md`
files at once. The 5-file cap is enforced client-side — bypassing it would
risk rate-limit issues and make partial failures harder to audit.

**Action selector (applies to the whole batch):**

| Action | Behavior |
|--------|----------|
| **Upsert** *(default)* | Update if the slug exists, else create as Draft. |
| **Update only** | Skip files whose slug isn't already in the collection. |
| **Create only** | Refuse files whose slug already exists. |

**"Include identity / structural fields" checkbox** — defaults to on for
Create, off for Update (matches single-file behavior). Name + Slug always
flow through regardless, since both Create and Update need them.

**Per-file expander under 🧾 Push plan**

Each file collapses to a one-line header summarizing the key signals:

```
🆕 CREATE  ·  course-1.md  ·  corporate-training-malaysia  ·  18 fields
♻️ UPDATE  ·  course-2.md  ·  burnout-prevention            ·  22 fields  ·  ⚠️ 3 warnings
⏭️ SKIP    ·  course-3.md  ·  dup-slug                      ·  20 fields
```

Open one to inspect: slug, name, decision reason, every field that will be
pushed with a 120-char value preview, unmatched .md columns, and resolution
warnings.

**Performance**

- Schema fetched once per collection — not per file.
- Reference-collection items cached in `st.session_state[f"ref_cache_{id}"]`,
  reused across every file in the batch.
- Maximum **2 write calls** per batch: one `PATCH /items` (or `/items/live`)
  for all updates, one `POST /items` for all creates. Webflow accepts ≤100
  items per call; the chunker handles larger batches if you ever raise the
  5-file cap.
- Slug-existence check still runs once per file (≤5 GETs per batch).

**Failure handling**

Webflow's per-item response is preserved. The result table shows ✅/❌ for
each file independently; opening an ❌ row reveals the raw API error JSON.
Partial success is expected (Webflow's bulk endpoints are **not**
transactional) and clearly surfaced.

---

## File layout

```
.
├── app.py                              # Single-file Streamlit app (~1900 lines)
├── requirements.txt                    # streamlit, requests, beautifulsoup4
├── README.md                           # this file
├── anycollection.md                    # design doc — supporting any collection ID
├── Burnout Prevention and Recovery CMS Fields.md   # example .md push input
├── Courses Collection ID.txt           # example collection ID for the courses collection
└── .devcontainer/                      # devcontainer config (optional)
```

`app.py` is organized top-to-bottom as: config → embed detection rules → HTML
preprocessing → block classifier → conversion functions → `classify_and_wrap`
(the main pipeline) → Webflow API helpers → CMS-fields .md helpers → block
parsing helpers → credential persistence → Streamlit UI.

---

## Configuration

| Constant | Default | Meaning |
|----------|---------|---------|
| `WEBFLOW_API_BASE` | `https://api.webflow.com/v2` | Webflow API endpoint. |
| `EMBED_CHAR_LIMIT` | `10000` | Per-embed character cap. The app warns (doesn't block) when an embed exceeds this. |
| `REQUEST_TIMEOUT` | `30` | Per-call HTTP timeout (seconds). Keeps the Streamlit worker from hanging if Webflow is slow. |
| `CREDS_PATH` | `~/.webflow_publisher.json` | Where opt-in credential persistence stores the token + collection ID. Plaintext, `chmod 600` on POSIX. **Untick the sidebar "Remember on this machine" box to delete.** |

---

## Webflow API surface used

| Endpoint | Used for |
|----------|----------|
| `GET /collections/{id}` | Fetch schema (field list, types, validations). Result cached per session. |
| `GET /collections/{id}/items?slug={slug}` | Find item by slug (fast path). |
| `GET /collections/{id}/items?offset=…` | Paginated fallback for slug search if the filter param is ignored. |
| `GET /collections/{ref_id}/items` | List referenced-collection items (for Reference / MultiReference resolution). Cached per session. |
| `POST /collections/{id}/items` | Create draft item. Bulk mode sends all creates in one call (≤100 items, chunked). |
| `PATCH /collections/{id}/items` | Update staged content. Bulk mode sends all updates in one call. |
| `PATCH /collections/{id}/items/live` | Update + publish live. |
| `GET /token/introspect` | Show token type during the connection test. |

All calls go through one `get_headers()` helper and carry a 30 s timeout.

---

## Security notes

- **The API token is treated as a password input** (`st.text_input(type="password")`).
- **Opt-in persistence only** — the "Remember on this machine" checkbox is off by default. When enabled, the token is written to `~/.webflow_publisher.json` in plaintext (with `chmod 600` on POSIX). Untick to delete.
- **No telemetry, no third-party calls** — only the Webflow API and your local browser.
- **Don't run this on a shared machine** without thinking about who else can read `~/.webflow_publisher.json`.

---

## Known limitations

- The HTML pipeline is opinionated and built around a specific blog template (`.key-takeaways`, `.eval-grid`, `.company-profile`, etc.). Source HTML with different class names passes through as generic embed blocks but won't get the structural conversions.
- Any `<div>` with a class is treated as an embed block. Purely decorative wrappers will be wrapped as embeds, which may inflate the embed count. Edit affected blocks in the per-block UI before push, or restructure the source HTML.
- The reference-collection scan (`list_reference_options`) walks the entire collection. For very large reference collections this is slow on first run; results are cached per session.
- The connection test's "Token" check is best-effort — site-scoped tokens don't expose `/token/introspect`, in which case it shows "Skipped (site token)".

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `❌ Auth failed (401)` on test connection | Token lacks CMS scope, or it's expired. Regenerate in Webflow → Site Settings → Apps & Integrations. |
| `❌ 404` on test connection | Collection ID wrong, or the token is for a different site. |
| FAQ field renders empty in the Editor | Older versions stripped the embed wrapper. Re-run the push — the current version wraps with `<div data-rt-embed-type="true">…</div>`. Note: the **Editor** always shows a placeholder for embed blocks; check the **live** site. |
| Trainer Paragraph shows literal `<div>`/`<p>` | Older versions matched only the singular form. Now catches plural / apostrophe / "Para" variants. Re-push. |
| `Reference value '…' not found in collection` warning | The value in your .md doesn't match any item in the referenced collection. Expand the **🔗 Referenced Collections** diagnostic in the UI to see the available slugs. |
| Push succeeds but page doesn't update | Make sure you ticked **Push to Live** in the sidebar — otherwise the change is staged and you have to publish from the Webflow Designer. |

---

## License

No license file is currently included. Contact the repo owner before redistributing.
