# Temporal knowledge persistence

This document defines the WP-T4 boundary for Steward-authored temporal claim
history. The core package is a pure reader: it validates frontmatter and folds
known revisions, but never edits a wiki page, source, index, log, database, or
model/API boundary.

## What a Steward may append

A page may contain a `temporal_claim_revisions` frontmatter list. Each entry is
exactly one `temporal-claim-revision/1` record:

```yaml
temporal_claim_revisions:
  - contract_version: temporal-claim-revision/1
    revision_id: temporal-revision:sha256:<64 lowercase hex>
    claim_key: temporal-claim:sha256:<64 lowercase hex>
    claim_scope: default
    decision: accept
    subject: {kind: resolved_page, page: services/api.md}
    predicate: status:has_state
    object: {kind: literal, datatype: type:text, value: ready}
    world_validity:
      from: {kind: known, value: 2026-08-01}
      to: {kind: open}
    recorded_at: 2026-08-10T00:00:00Z
    candidate_ids: [temporal-candidate:sha256:<64 lowercase hex>]
    observation_ids: [temporal-observation:sha256:<64 lowercase hex>]
    retires_revision_ids: []
    supersedes_revision_ids: []
    contradicts_revision_ids: []
    qualification_of_revision_ids: []
    steward_evidence_refs: [sources/api-status.md]
    authority: target_wiki_steward
```

`claim_key` is recomputed from the frozen WP-T1 subject, predicate, and scope
rule. It excludes the object so a corrected value can retain the same claim
key. `revision_id` hashes every other field, including normalized
`recorded_at`, with canonical array ordering. Candidate and observation IDs
are required, valid, sorted, deduplicated, and limited to 64 each. Each
relation array is limited to 64 IDs and all relation targets together to 128;
an entry is at most 65,536 canonical bytes. A page history is at most 512
entries and 1,000,000 canonical bytes.

Subjects cannot be literal entities. Evidence references are normalized,
non-empty relative wiki-page or `sources/` paths; raw payloads and URLs are
not evidence references. Authority is always `target_wiki_steward`.

## Decisions and append order

The five decisions have deliberately narrow meanings:

| Decision | Effect | Allowed targets |
| --- | --- | --- |
| `accept` | Establishes one revision | none |
| `retire` | Closes an established revision | `retires_revision_ids` |
| `supersede` | Establishes a replacement and closes its target | `supersedes_revision_ids` |
| `contradict` | Establishes a contested revision; both sides remain visible | `contradicts_revision_ids` |
| `qualify` | Establishes a qualification without closing its target | `qualification_of_revision_ids` |

The frontmatter list is append-only and its `recorded_at` values are
non-decreasing. A relation target must be earlier in that same page history.
Retirement, supersession, and contradiction targets must share the source
claim key. Qualification is allowed to use a different claim key.

Reject, defer, merge-without-acceptance, no-change, and malformed output are
operational outcomes only. They never become canonical revisions.

## Known-time folding

`parse_temporal_claim_revision` validates one entry,
`parse_temporal_claim_revisions` validates a page's append history, and
`fold_temporal_claim_revisions(revisions, known_at=...)` returns the state
visible at a normalized known-time. A revision recorded after `known_at` is
not visible, even if its world interval is backdated.

The fold reports active, retired, superseded, contested, qualified, and
complete-lineage revision IDs. Retirement and supersession close their target
at the decision's `recorded_at`; contradiction and qualification do not close
their targets. A contested group is never reduced to a winner. The fold does
not evaluate `world_validity`; world-time eligibility belongs to the later
temporal query/provider contract.

The public constructors and parsers are:

```text
build_temporal_claim_revision(...) -> TemporalClaimRevision
parse_temporal_claim_revision(raw) -> TemporalClaimRevision
parse_temporal_claim_revisions(frontmatter) -> tuple[TemporalClaimRevision, ...]
fold_temporal_claim_revisions(revisions, *, known_at) -> TemporalRevisionFold
```

## Steward workflow and failure handling

On a durable change, the Steward:

1. Reads the relevant page and existing revision history.
2. Snapshots mutable evidence under `sources/` when required.
3. Appends the validated revision; it never edits an earlier entry.
4. Keeps page prose consistent with the folded current state.
5. Updates `index.md` and appends the required `log.md` row.
6. Runs the target wiki lint and render commands.

Validation failure is a local authoring error, not a partial write. Preserve
the existing page and history, correct the proposed entry, and retry through
the Steward workflow. The parser does not repair or reorder authored history.
Unknown or late records remain excluded from a known-time fold until their
recorded time is known; no data is silently truncated.

The package has no writer, storage activation, provider, MCP, Brain, Anvil,
or compiler integration in WP-T4. Those boundaries remain read-only until a
later approved task.
