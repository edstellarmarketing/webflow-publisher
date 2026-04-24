# Plan: Support Any Webflow Collection ID

## Goal
Replace the hardcoded `COLLECTION_ID = "64ac3a242208dda62b6e6a90"` (blog collection) with a
dynamic, user-supplied collection ID. The app should work with any Webflow CMS collection — not
just the single hardcoded blog collection.

---

## Current State

- **`app.py` line 10**: `COLLECTION_ID = "64ac3a242208dda62b6e6a90"` — single global constant
- Every API call that touches the collection reads from this constant:
  - `GET /collections/{COLLECTION_ID}` — fetch schema / test connection (line 759)
  - `GET /collections/{COLLECTION_ID}/items` — list + search items (lines 777, 812–824)
  - `PATCH /collections/{COLLECTION_ID}/items` — update item (lines 1318–1328)
  - `POST /collections/{COLLECTION_ID}/items` — create item (line 851–872)
- The collection ID is displayed as read-only info in the sidebar (line ~895)

---

## Proposed Changes

### 1. Remove the hardcoded constant
Delete (or comment out) line 10:
```python
# COLLECTION_ID = "64ac3a242208dda62b6e6a90"   ← remove
```

### 2. Add a Collection ID input in the sidebar
In the sidebar section (around line 885–938), add a `st.text_input` for the collection ID,
stored in `st.session_state["collection_id"]`.

Place it directly below the API token input so users fill in both before doing anything.

```python
collection_id = st.sidebar.text_input(
    "Collection ID",
    value=st.session_state.get("collection_id", ""),
    placeholder="e.g. 64ac3a242208dda62b6e6a90",
    help="Webflow CMS Collection ID to publish to",
)
st.session_state["collection_id"] = collection_id
```

### 3. Gate all actions behind a valid collection ID
Add a guard so that "Test Connection", slug search, push, and create are all blocked
(with a clear `st.warning`) when `collection_id` is empty.

### 4. Replace every reference to `COLLECTION_ID` with the session-state value
Every function / code block that currently uses `COLLECTION_ID` should instead read:
```python
collection_id = st.session_state.get("collection_id", "")
```
Affected call sites (all in `app.py`):
| Lines | Action |
|-------|--------|
| 759   | Fetch collection metadata (test connection) |
| 777   | List items (test connection) |
| 812–824 | Paginate items to find slug |
| 829–846 | Update item (PATCH) |
| 851–872 | Create item (POST) |
| 1318–1328 | Push draft/live |

### 5. Update the sidebar info display
The line that currently shows the hardcoded ID as static text should instead show the
user-entered value (or "not set" if blank).

### 6. (Optional / nice-to-have) Persist recent collection IDs
A `st.selectbox` or simple dropdown that stores the last 3–5 used collection IDs in
`st.session_state["recent_collections"]` so users can switch quickly between collections
without retyping. **Out of scope for this change — do only if user requests.**

---

## Files Changed
| File | Change |
|------|--------|
| `app.py` | Remove constant; add sidebar input; replace ~6 `COLLECTION_ID` references |

No new files needed.

---

## Testing Checklist
- [ ] Sidebar shows Collection ID input below API token field
- [ ] "Test Connection" button fails gracefully when collection ID is blank
- [ ] "Test Connection" succeeds with a valid collection ID (e.g. `64ac3a242208dda62b6e6a90`)
- [ ] "Find Blog Post" (slug search) uses the entered collection ID
- [ ] Update flow PATCHes the correct collection
- [ ] Create flow POSTs to the correct collection
- [ ] Switching to a different collection ID mid-session works without stale state

---

## Risks / Notes
- Users must know their collection ID upfront. The sidebar `help` tooltip should point them
  to Webflow Dashboard → CMS → [Collection] → Settings → Collection ID.
- No backward-compatibility issue: the app has no persistent config, so removing the constant
  does not break anything stored externally.
- The field is free-text; no format validation needed beyond "non-empty" (Webflow will return a
  4xx if the ID is wrong, and the existing error-display code handles that).
