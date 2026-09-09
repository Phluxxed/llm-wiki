# Unified Maintenance New-Page Ingest Repair Brief

**Date:** 2026-08-18
**Status:** Repair brief
**Observed path:** Brain Steward ingest of a new project page through
`wiki_build_maintenance`

## Outcome

Make the unified maintenance contract usable for a new project page from an
absent target through Steward review and a resolved temporal revision. The
contract must expose the data it requires, report validation failures together,
and provide a documented bootstrap path that does not deadlock before the
proposal can exist. The resulting flow remains candidate-only: proposal
generation and this runtime do not acquire mutation authority.

## Observed defects

### Opaque nested MCP schema

The `wiki_build_maintenance` MCP schema exposes `source`, `claims`, and
`evidence` as opaque mappings. The required nested source and claim contracts
are therefore hidden from callers and from schema-driven validation. A caller
cannot reliably discover the required fields, types, temporal precision, or
hash format from the tool definition.

### Sequential validation failures

Validation stops at the first missing or malformed nested value. Callers see a
series of one-error-at-a-time failures instead of one actionable response that
identifies all invalid paths and their expected contracts.

### New-page bootstrap deadlock

For a new project page, `resolved_page` rejects the nonexistent target while a
literal subject is rejected as well. This occurs even though a proposal is
required before mutation and therefore cannot depend on a page that has not
yet been created.

The observed workaround was:

1. submit a proposal against an existing Brain page;
2. create the project page in Brain;
3. submit a second proposal after the page resolves; and
4. apply the temporal revision.

This workaround is not a valid new-page ingest contract because it introduces
an unrelated existing-page dependency and duplicates proposal work.

## Reproduction record

The failure occurred while adding the absent Brain project page
`projects/tokyo-bench.md` through registered wiki alias
`anvil-brain-codex`. The authoritative source was Codex session
`01a00507-914b-7ae0-872c-3e593f1a1e16`.

The caller had to discover the input contract through successive failed calls:

1. the initial source envelope was rejected for missing required fields;
2. a prefixed hash was rejected because `content_hash` must be exactly 64
   lowercase hexadecimal SHA-256 characters;
3. temporal values were rejected until they used the required object shapes
   and whole-second, timezone-aware instants;
4. `resolved_page` failed with
   `subject references unknown wiki page: projects/tokyo-bench.md`; and
5. substituting a literal subject failed with
   `candidate subject may not be literal`.

That leaves no valid subject representation for a claim about an absent page.
The successful workaround produced this evidence:

- initial gap proposal against an existing Brain page:
  `maintenance-proposal:sha256:7b86b5cd86f70a1130bcee8dd7c7559473ddea162c3e447025ded5660dde41b2`;
- second proposal after the Steward created the page:
  `maintenance-proposal:sha256:3e73967297cc0b480494cdc9b6da74a89cca2caf8a00fec10bc3cb26afca67b8`;
- temporal candidate:
  `temporal-candidate:sha256:59ea6efd2aa0f35ed299f45b4b187e1e21a17817ea35f369f1589c5b6564cd75`;
- temporal observation:
  `temporal-observation:sha256:49e5a6d41aa0a8c57e678dca12ecf1f34cf4e5887beb60b54a9325378abedfd5`;
- accepted temporal revision:
  `temporal-revision:sha256:eaa8df21c85803cda7e0ca5bcc678e28843148d101888805e3c480b80241dd46`;
  and
- temporal claim key:
  `temporal-claim:sha256:90c69dd6ca63e074585204fef0f1ce1285192cb2b3bbb19a95ae0e98300d530b`.

These identifiers are reproduction evidence, not fixtures that a repaired
implementation must preserve.

## Observed input requirements

The public contract must make the following nested requirements explicit.

### Source

The minimal non-temporal source accepts `source_kind`, `source_ref`, and
`content_hash`. A claims-bearing source additionally requires:

- `source_kind`;
- `source_ref`;
- `content_hash`, exactly 64 lowercase hexadecimal characters representing a
  SHA-256 digest;
- an object locator;
- `input_type`;
- `observed_at`, a timezone-aware timestamp with whole-second precision;
- object `source_event_time`;
- `retention`; and
- `payload_text`.

### Claims

Each claim object requires:

- `subject`;
- `predicate`;
- `object`;
- `claim_scope`;
- `proposed_world_validity`;
- `signals`; and
- `unknowns`.

All claim and validity time values must be timezone-aware and expressed at
whole-second precision, consistent with the source timestamps.

## Implementation map

The repair should begin at these live seams:

| File | Current responsibility | Required change |
| --- | --- | --- |
| `src/llm_wiki_mcp/mcp_server.py:220` | Publishes `wiki_build_maintenance` with `Mapping[str, Any]` source/claim/evidence arguments. | Publish discoverable nested input types, including the conditional claims-bearing source shape. |
| `src/llm_wiki_mcp/wiki_runtime.py:260` | Routes the unified request and converts the first validation exception into one `INVALID_INPUT`. | Preserve the public error code while returning all actionable field diagnostics. |
| `src/llm_wiki_mcp/wiki_runtime.py:622` | Selects the minimal or temporal source field set and validates hash/payload consistency. | Expose this conditional contract in the MCP schema instead of hiding it behind runtime failure. |
| `src/llm_wiki_mcp/wiki_runtime.py:687` | Rejects every `resolved_page` entity absent from the current wiki page map. | Add the approved absent-page identity/bootstrap path without weakening validation for existing-page claims. |
| `src/llm_wiki_core/temporal.py:603` | Rejects literal candidate subjects. | Retain the invariant; solve new-page identity explicitly rather than treating literals as page identities. |
| `tests/test_mcp_unified_maintenance.py` | Covers existing-page runtime behavior and one stdio round trip. | Add schema discovery, aggregate invalid-input, and absent-page end-to-end cases. |

The existing stdio test only proves a minimal, claim-free request against an
already-created wiki. It does not exercise the larger claims-bearing source
contract or the absent-page bootstrap boundary.

## Desired repairs

1. Replace opaque `source`, `claims`, and `evidence` mappings in the MCP
   surface with strongly typed nested schemas. Model minimal and
   claims-bearing source variants explicitly; encode required fields, nested
   shapes, timestamp rules, and the `content_hash` format.
2. Add valid end-to-end examples for both an existing Brain page and an absent
   new project page. Examples should be executable contract fixtures, not only
   prose snippets.
3. Define a first-class new-page identity contract, or document a stable
   two-phase bootstrap that explicitly permits proposal creation before page
   resolution and identifies the page deterministically afterward.
4. Consolidate validation failures into one structured diagnostic containing
   every invalid field path, the violated rule, and the expected shape. Keep
   `INVALID_INPUT` as the public error code and attach the diagnostics as
   structured details.
5. Add a contract/stdio acceptance test that starts with absent
   `projects/new.md`, runs proposal generation, has the Steward create the
   project page, produces the resolved temporal revision, and finishes with
   lint and render checks.
6. Preserve candidate-only semantics and `mutation.allowed = false` for the
   maintenance packet and proposal-generation path. Page creation and any
   accepted revision remain governed by the Steward's existing mutation path.

## Acceptance contract

The repair is complete when a real stdio client can inspect
`wiki_build_maintenance.inputSchema` and discover both source variants plus
every nested claim field; execute the absent-page fixture without undocumented
fields or workaround proposals; receive all nested validation diagnostics from
one deliberately malformed request; have the Steward create the page; resolve
the temporal claim to the same predeclared identity; produce the temporal
revision; pass lint and render; and confirm the maintenance result still grants
no mutation authority to the producer.
