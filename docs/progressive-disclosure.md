# Progressive-disclosure contract (frozen)

> Status: **Frozen** on 2026-07-22 by maintainer decision in
> [#2](https://github.com/A2C-SMCP/theseus-kit/issues/2). This document is the
> single source of truth for the large-configuration read surface. The read
> tools ([#4]) and the `window://` resources ([#9]) implement this contract
> verbatim; changes require reopening #2.

## Decision summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Disclosure scheme | **A — index + stateless structural selector** | Generic MCP clients must work without A2C-SMCP (#1 release gate); stateless, replayable, no server session. Aligns with the `window://` "no query" rule and the `/config/recent` projection driven by `get_config_detail`. |
| Default read scope | **All three lifecycle states** (draft + template + online) | Matches the three-state summary in #9 and the 0.1.0 surface. Filtering narrows; it never expands a smaller default. |
| Result budget | **8 KiB default / 32 KiB hard cap** (post-redaction, serialized JSON) | Tighter than the 16/64 draft. Trades more disclosure rounds for a smaller per-call context footprint — acceptable because the summary → list → detail loop is the intended interaction. |

Rejected alternatives B (Manifest + JSON Pointer) and C (Snapshot/Query Handle)
are documented in #2. C is incompatible with the stateless, generic-client
release gate; B is folded into A as the `select` pointer inside `get_config_detail`.

## Tools

All four are **standard MCP tools** and must be fully usable in a generic MCP
client with no A2C-SMCP awareness. A2C-only layout metadata travels in `_meta`;
it is ignorable.

Every response carries `_meta = { revision, fetched_at }`, where `revision` is
the upstream configuration revision/etag observed at fetch time and `fetched_at`
is an ISO-8601 timestamp. There is **no server-side snapshot caching**: every
call reads live upstream state.

### `get_config_summary`

```text
get_config_summary(state?: "draft" | "template" | "online") -> {
  robot_identity: { robot_id, display_name?, server_version? },
  states: {
    draft?:    { present: bool, root_locator, status: "clean" | "dirty" | "publishing" | "unknown",
                 revision?, last_modified? },
    template?: { present: bool, count: int },
    online?:   { present: bool, root_locator, revision?, last_modified? }
  },
  _meta: { revision, fetched_at }
}
```

`state` omitted → all three entries present. `state` given → only that entry.
This is the entry point for an LLM that does not yet know what exists.

### `list_config_nodes`

```text
list_config_nodes(
  parent?:    locator,                         # omit => forest roots; give => children of that node
  state?:     "draft" | "template" | "online",
  scene?:     str, factory?: str, name?: str,  # best-effort filters (upstream-dependent)
  cursor?:    str,                             # opaque pagination token
  page_size?: int = 50                         # clamped to [1, 200]
) -> {
  nodes: [ { locator, state, kind, name?, size_bytes, revision? } ],
  next_cursor?: str,                            # absent => end of results
  drift?: bool,                                 # true if upstream revision changed mid-pagination
  _meta: { revision, fetched_at, filters }
}
```

`list_config_nodes` paginates **siblings/children**; `get_config_detail` views a
**bounded subtree**. Use `list_config_nodes` when a node has more children than a
depth-bounded detail read can return.

### `get_config_detail`

```text
get_config_detail(
  locator:   str,            # required, see grammar below
  select?:   str = "",       # RFC 6901 JSON Pointer within the node's value
  depth?:    int = 3,        # 0..N, clamped; 0 => scalar/identity only, no children
  max_bytes?: int = 8192      # clamped to [1024, 32768]
) -> {
  locator, state, revision,
  subtree: <json>,            # bounded, redacted sub-value
  truncated: bool,
  truncated_at?: str,         # JSON Pointer where the cut happened
  bytes_returned: int,
  bytes_estimated_total?: int,
  redacted: [str],            # JSON Pointers of redacted fields (paths only, never values)
  next_actions?: [ { action, ...params } ],
  _meta: { revision, fetched_at }
}
```

On success, updates the in-process **recent-detail projection** consumed by
`window://com.a2c-smcp.theseus-kit/config/recent` (#9) with this `locator` and
`revision`. This side effect is the only state `get_config_detail` mutates, and
it is process-local and non-persistent.

### `get_template`

```text
get_template(
  template_id:    str,
  metadata_only?: bool = false,
  select?, depth?, max_bytes?                 # honored only when metadata_only = false
) ->
  metadata_only = true:  { template_id, name, lifecycle, size_bytes, root_locator, _meta }
  metadata_only = false: same shape as get_config_detail, locator := tcfg:template/<template_id>
```

## Locator grammar

A locator is a **stateless, replayable** identifier for one node in the
three-state configuration forest. The same locator always re-resolves against
live upstream state; it carries no token, session, or TTL.

```text
locator := "tcfg:" state [ pointer ]
state   := "draft" | "online" | "template"
pointer := "" | <RFC 6901 JSON Pointer>      # "" / absent => state root
```

- `tcfg:draft` → draft root; `tcfg:draft/robots/main` → node at `/robots/main`.
- `tcfg:online` → online root; `tcfg:online/robots/main` analogously.
- `tcfg:template` → the template collection root (only listable, not a detail
  target).
- `tcfg:template/<template_id>` → root of that template. The **first pointer
  segment for the `template` state is the template id**.
- `tcfg:template/<template_id>/sensors/0` → subtree within that template.

`template_id` charset is `[A-Za-z0-9_-]+`. Pointer segments use RFC 6901
`~0` / `~1` escaping.

### `locator` vs `select`

- `locator` chooses the **node** in the forest.
- `select` (RFC 6901 pointer) drills **within** the located node's own value,
  returning a bounded sub-subtree. Use it when a single node is itself too large
  for the depth/byte budget. Default `""` returns the whole node (then bounded by
  `depth` + `max_bytes`).

## Cursor semantics

- Cursors appear **only** in `list_config_nodes`.
- The token is opaque base64url, encoding `{ parent, state, filters, offset,
  revision_at_issue }`.
- Pagination is **stateless**: the server re-reads live upstream on each call. If
  the upstream revision changed since the cursor was issued, the response sets
  `drift: true` and the caller may restart the listing.
- No TTL, no server session, nothing to reclaim.

## Budgets

| Surface | Default | Hard cap | Clamp |
|---------|---------|----------|-------|
| `get_config_detail` subtree (`max_bytes`) | **8 KiB** | **32 KiB** | `[1024, 32768]` |
| `list_config_nodes` page (`page_size`) | 50 | 200 | `[1, 200]` |

Budgets are measured on the **redacted, serialized** payload: redaction is
applied first, size second, truncation last. A node full of secrets therefore
returns mostly `"<<redacted>>"` and never overflows the budget.

## Truncation

When the serialized subtree exceeds `max_bytes` at the requested `depth`:

- `truncated: true`.
- The cut happens at a **node boundary** — never mid-object-key or
  mid-array-element.
- `truncated_at` records the JSON Pointer where it stopped; `bytes_returned` and
  `bytes_estimated_total` quantify the partial result.
- `next_actions[]` is non-empty and offers at least one of:
  - narrow `select` to the subtree rooted at `truncated_at`,
  - lower `depth`,
  - request a smaller `max_bytes`,
  - `list_config_nodes` of the children at `truncated_at`.

Markers are structured JSON fields, not free text, so an LLM can parse them.

## Sensitive-field redaction

Mandatory on **all** read surfaces — `get_config_detail`, `get_template`, and the
`window://` resources. Applied server-side, after the upstream fetch and before
serialization, even if the upstream accidentally returns a secret.

- Match keys case-insensitively by substring: `password`, `passwd`, `secret`,
  `token`, `credential`, `api_key`, `apikey`, `access_key`, `private_key`,
  `client_secret`, `refresh_token`, `pat`, `bearer`, `authorization`. Also honor
  any field the upstream schema flags sensitive.
- Replaced value: `"<<redacted>>"`. Applied recursively to nested objects and
  arrays.
- Every redacted path is listed in `redacted[]` — **paths only, never values**.

## Freshness and data expiry

- No snapshot is cached. Every call is live; the contract's "expiry" is simply
  that each response declares the `revision` it was built from.
- Mutation tools (`update_draft`, `save_template`, `publish_config`) change a
  state's revision. A subsequent read returns the new revision; the LLM must
  re-read to act on the latest state.
- The recent-detail projection (for `window://recent`) is **marked stale** when a
  mutation changes the relevant state's revision. `window://recent` then surfaces
  a clear "stale — reopen to refresh" marker rather than silently stale data. The
  projection is process-local and is never persisted.

## Generic-MCP-client compatibility

The four tools form the complete read surface and work with no A2C-SMCP support.
`window://` / `skill://` (#9, #10) consume this same contract; any A2C-specific
layout lives in `_meta` and is safely ignorable by a generic client.
