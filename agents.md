# Agent Notes — Webflow CMS Publisher

## Project overview
Single-file Streamlit app that pushes editorial content into any Webflow CMS
collection. Four input modes (Raw HTML, Webflow-ready HTML, CSV, CMS-fields
Markdown). See `README.md` for the user-facing description.

## Stack
- Python 3.9+, Streamlit, requests, beautifulsoup4 (see `requirements.txt`)
- Webflow REST API v2
- No backend, no database — credentials persisted optionally to `~/.webflow_publisher.json`

## Layout
- `app.py` — everything. Top-to-bottom: config → embed rules → HTML preprocessing
  → block classifier → conversion functions → `classify_and_wrap` → Webflow API
  helpers → CMS-fields .md helpers → block parsing helpers → credential persistence
  → Streamlit UI.
- `requirements.txt`
- `README.md`
- `Burnout Prevention and Recovery CMS Fields.md` — example CMS-fields input
- `Courses Collection ID.txt` — example collection ID
- `anycollection.md` — design doc for the dynamic-collection-ID feature

## Run / build / test
```
pip install -r requirements.txt
streamlit run app.py
```
Opens at <http://localhost:8501>.

No test suite yet. Pure helpers worth covering when one gets added:
`split_into_blocks`, `convert_company_profile`, `convert_eval_grid`,
`convert_faq_details`, `convert_quotes_to_single`, `parse_cms_fields_md`,
`wrap_faq_section`, `plain_text_only`, `is_faq_field`,
`is_trainer_paragraph_field`.

## Env / credentials
- **Webflow API token** + **collection ID** entered in the sidebar.
- Optional persistence at `~/.webflow_publisher.json` (chmod 600 on POSIX),
  opt-in via the "Remember on this machine" checkbox. Untick to delete.

## Agent-specific notes

### Don't
- Don't refactor `app.py` into multiple files without a plan — UI state flows
  between sections and isn't trivially splittable.
- Don't change the `data-rt-embed-type` attribute or its single/double quote
  treatment without testing in the Webflow Editor. Webflow strips bare
  `<section>` / unknown elements from Rich Text unless inside that wrapper.
- Don't add `--no-verify` to commits or push to a shared remote without asking.
- Don't broaden `is_embed_block` further — it already treats any classed `<div>`
  as an embed; widening it more will start wrapping decorative wrappers.

### Watch out for
- **Field-type-aware transforms run AFTER schema resolution** — adding new
  rules belongs in the UI's resolve loop, not in `parse_cms_fields_md`.
  Reason: `parse_cms_fields_md` doesn't yet know what Webflow says the field
  type is.
- **FAQ wrap is idempotent** — re-pushing must not double-wrap. The check looks
  at the first ~120 chars for `data-rt-embed-type` and the whole string for
  `schema.org/faqpage`.
- **`convert_quotes_to_single` encodes apostrophes** in values (`&#39;`)
  to keep attributes well-formed after the swap.
- **All `requests` calls carry a 30 s timeout** (`REQUEST_TIMEOUT`). If you add
  a new call site, include it — Streamlit workers hang otherwise.
- **`search_item_by_slug` tries the `?slug=` filter first** then falls back to
  pagination. Don't remove the fallback — some Webflow API revisions ignore
  the filter param.

### Known fragile spots
- The "any classed `<div>` is an embed" rule (`is_embed_block`) — by design,
  but it can wrap purely cosmetic wrappers. Edit blocks in the per-block UI
  before push, or restructure source HTML.
- `convert_eval_grid` keeps only the first 6 cards and drops the rest.
  Intentional — region-specific extras get pruned.
- `parse_cms_fields_md` expects the table to have `| Section | Field Name |
  Input Type | … |` shape. Other column layouts break the split-by-`|` logic.

### When making schema-related changes
- Test the connection helper (`test_api_connection`) first — it surfaces
  field count and item count without making writes.
- The **Schema Inspector** expander in the Push CMS Fields mode dumps every
  field's type and referenced collection ID. Use it before adding rules.

### When updating the README
- Update the "Field-type rules" table whenever a new schema-type rule is added
  in the UI resolve loop.
- Update the API surface table when a new Webflow endpoint call is added.
