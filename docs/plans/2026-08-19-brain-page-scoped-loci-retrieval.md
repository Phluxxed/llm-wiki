# Brain Page-Scoped Loci Retrieval Implementation Plan

**Date:** 2026-08-19  
**Status:** Ready for implementation  
**Primary repository:** `/Users/brummerv/llm-wiki`  
**Primary baseline:** `dba66e0458c69867aad09ba2695400e206e73080`  
**Dependency repository:** `/Users/brummerv/loci`  
**Dependency baseline:** `c786a54f793a7597224797ed1e5adea47f4a60fd`  
**Implementation boundary:** Loci provides generic pre-ranking file eligibility;
llm-wiki supplies the canonical wiki-page set for normal recall.

## Outcome

Keep the complete wiki repository indexed while ensuring that normal
llm-wiki/Brain recall ranks and hydrates only loaded canonical pages. Dense
documents under `sources/`, plus `_templates/`, scripts, conventions, agent
manuals, and other repository support files, must not consume Loci's top-N
search slots or enter the Loci provider's recall evidence.

The change is complete when one real stdio producer-consumer path proves all of
the following:

1. Loci indexes a temporary wiki containing canonical pages, dense sources,
   templates, scripts, and repository documentation;
2. llm-wiki passes the exact loaded-page paths to `loci_search` as an eligibility
   allowlist;
3. Loci filters by that allowlist before scoring, sorting, and applying `limit`;
4. a weaker canonical-page match survives even when excluded source documents
   would otherwise dominate the query;
5. llm-wiki hydrates and selects only canonical page sections from the Loci
   provider; and
6. explicit source, grounding, and maintenance facilities remain available
   through their existing routes.

This is a retrieval-boundary repair. It does not redefine what Loci indexes,
turn Loci into a wiki-aware service, or remove source evidence from llm-wiki.

> **TL;DR:** Index the whole repository, but let llm-wiki provide an exact page allowlist that Loci applies before ranking, so normal Brain recall cannot be crowded out by sources or operational files.

## Problem And Current Evidence

### Loci currently ranks the complete indexed corpus

`IndexStore.search` in
`/Users/brummerv/loci/src/loci/storage/index_store.py` iterates every indexed
symbol, applies only `kind` and `lang` eligibility, calculates scores, sorts the
whole result set, and then slices `scored[:limit]`. Markdown templates receive
a score penalty, but sources and other dense prose documents remain eligible.

The `loci_search` MCP tool currently exposes:

- `repo`;
- `query`;
- optional `kind`;
- optional `lang`; and
- `limit`.

It has no caller-supplied file scope. Filtering after this tool returns cannot
recover a canonical page that was already displaced beyond the top-N cutoff.

### llm-wiki currently accepts non-page Loci results

`LociProvider.collect` in
`src/llm_wiki_core/providers/loci.py` calls Loci with `MAX_RESULTS = 40`, then
looks up each returned path with `context.pages.get(file_path)`. A missing page
does not reject the result. Instead, the provider still creates a
`CandidateEvidence`; source-directory results are labelled as sources and can
receive `answer`, `support`, and `authority` roles.

This makes the later compiler or hook responsible for discarding evidence that
should never have entered normal Loci-backed recall. It also means discarded
results have already consumed the bounded search and hydration budget.

### llm-wiki already has the canonical eligibility set

`ProviderContext.pages` is the mapping of loaded wiki pages. The loader excludes
source and support directories/files and requires page frontmatter. It is the
existing domain-owned answer to “which files are Brain pages?” No new page
registry, path convention, or duplicated index is needed.

## Producer-Consumer Contract

Add one optional Loci search argument:

```text
file_paths: list[str] | None
```

Its contract is:

- `None` or omission preserves today's repository-wide search behavior;
- a non-empty list is an exact allowlist of normalized, repository-relative,
  POSIX file paths;
- an empty list means that no files are eligible and returns no symbols;
- absolute paths, parent traversal, empty path entries, NUL bytes, non-string
  values, and an excessive number of entries fail with `INVALID_INPUT`;
- duplicates are normalized away without changing result order;
- eligibility is applied before scoring, sorting, search logging, and `limit`;
- the full repository remains indexed and available to unscoped callers; and
- returned symbol and coverage payload shapes remain backward compatible.

llm-wiki's normal Loci provider must call that contract with:

```python
file_paths=sorted(context.pages)
```

The consumer invariant is:

> Every candidate emitted by `LociProvider.collect` corresponds to a key in
> `ProviderContext.pages`. A result outside that set is rejected explicitly and
> cannot be assigned answer, support, authority, endpoint, state, or lineage
> roles by this provider.

The producer-consumer boundary is runnable only after the actual `loci-mcp`
stdio server accepts the allowlist, returns scoped results, llm-wiki hydrates
them through `loci_get`, and `wiki_compile_context` serializes a page-only Loci
candidate set from a fully indexed temporary wiki.

## Architecture Decisions

### Keep one complete Loci index

Do not add `.gitignore` entries, Loci index exclusions, a second page-only
index, a virtual repository, or a copied page tree. Sources and support files
remain navigable for explicit editing, maintenance, provenance, and debugging.

### Put domain meaning in llm-wiki

Loci owns only a generic exact-file eligibility primitive. It must not learn
about `sources/`, Brain, frontmatter validity, wiki page types, or llm-wiki's
directory conventions. llm-wiki derives eligibility from its already-loaded
`ProviderContext.pages` mapping.

### Filter before ranking, not after retrieval

Over-fetching and post-filtering are not acceptable steady-state mechanisms.
They cannot guarantee that an eligible page survives an arbitrary number of
strong excluded matches, and they waste hydration and response budget.

### Use exact paths rather than globs or prefix rules

The consumer already has the exact page set. Exact repository-relative paths
avoid a new pattern language, platform-dependent glob semantics, and accidental
admission of future support directories. Convert the list to a set at the
store boundary so membership remains cheap.

### Preserve explicit source workflows

Do not remove or weaken `SourceProvider`, page source references, grounding
checks, source excerpts, maintenance candidates, or direct unscoped Loci
navigation. This plan changes only the normal Loci provider's recall
eligibility. Source evidence may still be consulted by an explicit source,
grounding, audit, or maintenance route; it is not durable page guidance.

### Defend at both sides of the boundary

Pre-ranking filtering in Loci is the functional fix. llm-wiki must also reject
and diagnose any out-of-scope result returned by a stale, incompatible, or
incorrect Loci server. The defensive check must not become a post-filtering
fallback or trigger unbounded retries.

## Implementation Plan

### 1. Record the two-repository starting point

Repositories:

- `/Users/brummerv/loci` at
  `c786a54f793a7597224797ed1e5adea47f4a60fd`; and
- `/Users/brummerv/llm-wiki` at
  `dba66e0458c69867aad09ba2695400e206e73080`.

Before editing:

- re-check both HEADs and worktrees;
- preserve the pre-existing untracked llm-wiki files
  `docs/plans/2026-08-18-unified-maintenance-new-page-ingest-repair.md` and
  `uv.lock`;
- do not stage, rewrite, delete, or claim those files as part of this work;
- record the installed `loci-mcp` command resolved by llm-wiki; and
- confirm that the current llm-wiki test environment can start that command.

If either baseline has moved, inspect the changed seams and update this plan's
assumptions before implementation. Do not reset either worktree.

Completion criterion: both starting states and the actual Loci executable used
by llm-wiki are known without mutating user work.

### 2. Add pre-ranking exact-file eligibility to Loci

Modify:

- `/Users/brummerv/loci/src/loci/storage/index_store.py`;
- `/Users/brummerv/loci/src/loci/service.py`;
- `/Users/brummerv/loci/src/loci/mcp_server.py`;
- `/Users/brummerv/loci/tests/storage/test_index_store.py`;
- `/Users/brummerv/loci/tests/test_service.py`; and
- `/Users/brummerv/loci/tests/test_mcp_server.py`.

Implement the contract vertically:

1. Add optional `file_paths` parameters through `loci_search`,
   `search_symbols_result`, `search_symbols`, and `IndexStore.search`.
2. Validate and normalize the public input once in the service layer. Keep the
   store parameter typed as an already-normalized collection or set.
3. Choose and document a finite maximum entry count comfortably above current
   Brain page counts. Report the count and configured maximum in
   `INVALID_INPUT` details when exceeded; do not echo the whole list.
4. Compare exact `symbol["file_path"]` values against the allowlist before
   `_score_symbol_detail`, inherited Markdown scoring, template adjustment,
   sorting, or slicing.
5. Preserve omitted-argument behavior byte-for-byte where practical.
6. Keep search/miss logging bounded to the eligible result set. Do not log
   excluded symbol IDs or copy the allowlist into session logs.
7. Preserve the existing repository index coverage object. The caller already
   knows the requested eligibility scope; do not claim that an empty scoped
   result proves absence from the complete repository.

Required focused cases:

- omission searches the complete indexed corpus as before;
- an exact allowlist excludes a higher-scoring symbol and returns a lower-scored
  eligible symbol within `limit=1`;
- multiple sections from one allowed Markdown page remain eligible;
- an empty allowlist returns zero symbols without widening to the repository;
- duplicates normalize safely;
- invalid, absolute, traversal, NUL-containing, and oversized inputs fail with
  structured `INVALID_INPUT` errors; and
- the real MCP tool schema exposes `file_paths`, applies it, and retains honest
  coverage plus structured error behavior.

Run the smallest Loci acceptance set covering the changed layers:

```bash
cd /Users/brummerv/loci
uv run pytest \
  tests/storage/test_index_store.py \
  tests/test_service.py \
  tests/test_mcp_server.py -q
```

If those files are too broad for the local loop, run the newly added tests by
node first, then run the three-file command once after they pass.

Completion criterion: a real `loci_search` MCP call filters exact files before
ranking and limit while unscoped search remains unchanged.

### 3. Scope llm-wiki's Loci provider to loaded pages

Modify:

- `src/llm_wiki_core/providers/loci.py`; and
- `tests/test_loci_provider.py`.

Change the consumer boundary once:

1. Extend `LociGateway.retrieve`, `LociMcpGateway.retrieve`,
   `LociMcpGateway._retrieve_session`, and `_FunctionLociGateway.retrieve` to
   accept an exact eligible-file collection.
2. In `LociProvider.collect`, derive a stable tuple from
   `sorted(context.pages)` and pass it to the gateway.
3. Send it as `file_paths` in the actual `loci_search` MCP call. Do not attach
   it to `loci_get`; hydration remains by exact symbol ID.
4. Update the injected test callback type and fake search functions to accept
   and assert the new argument.
5. Before constructing a candidate, require
   `file_path in context.pages`. If a result violates the requested scope,
   emit one bounded `LOCI_RESULT_OUT_OF_SCOPE` diagnostic containing the path
   and skip it. Do not retry with a larger limit.
6. Remove source-specific candidate construction and role assignment from
   `LociProvider`. This provider now emits page evidence only; `SourceProvider`
   remains the owner of source evidence.
7. Preserve current-page knowledge-state normalization, exact-section
   hydration, meaningful-query matching, retrieval ranks, truncation flags,
   timeouts, and structured Loci failure handling.

Required focused cases:

- the gateway receives exactly the sorted keys of `ProviderContext.pages`;
- a canonical page section remains a valid exact-section candidate;
- a source, template, script, or unknown path returned by a deliberately broken
  fake gateway produces a bounded diagnostic and no candidate;
- source paths cannot receive `answer`, `support`, or `authority` roles through
  `LociProvider`;
- an empty loaded-page mapping passes an empty allowlist and yields no Loci
  candidates; and
- the stdio gateway sends `file_paths` to a real/fake MCP server and still
  hydrates accepted symbols through one `loci_get` call.

Run:

```bash
cd /Users/brummerv/llm-wiki
.venv/bin/python3 -m unittest tests.test_loci_provider -v
```

Completion criterion: llm-wiki requests only loaded pages and independently
refuses any out-of-scope Loci response.

### 4. Prove the installed producer-consumer boundary

After the focused Loci and llm-wiki tests pass:

1. install or expose the changed Loci package so the `loci-mcp` command resolved
   by llm-wiki runs the changed code;
2. restart any long-lived llm-wiki MCP process so tool discovery sees the new
   additive `file_paths` schema;
3. create a temporary llm-wiki fixture outside both repositories containing:
   - one valid, loaded canonical page with a modest query match;
   - several dense `sources/*.md` documents with much stronger repeated query
     terms;
   - representative `_templates/`, script, conventions, and agent-manual files;
   - valid llm-wiki configuration; and
   - no private or production Brain content;
4. index the complete temporary root with the actual Loci service;
5. call the actual llm-wiki compiler/stdio boundary for the chosen query;
6. capture the Loci request arguments and compiled response;
7. prove that the canonical page is selected, every Loci-backed candidate has a
   loaded page path, and excluded files consume no returned rank;
8. run one separate explicit unscoped Loci search against the same index and
   prove that a dense source remains retrievable; and
9. remove only the temporary fixture/store created by this acceptance check.

Do not use the live Brain as a destructive fixture, edit its index policy, or
change its source files. A final read-only Brain smoke is optional only if the
temporary end-to-end path exposes a host-specific discrepancy.

Completion criterion: one representative shipped result crosses Loci search
serialization, pre-ranking eligibility, llm-wiki hydration and validation, and
compiled-context selection with page-only Loci evidence while the same complete
index still serves explicit unscoped source navigation.

### 5. Update only the changed public contract documentation

After the installed acceptance path passes, update the smallest authoritative
Loci documentation surface that describes `loci_search`, expected to include:

- `/Users/brummerv/loci/skills/loci/references/tool-contracts.md`; and
- a README/tool table only if it enumerates `loci_search` arguments.

Document:

- omitted versus empty allowlist behavior;
- exact normalized repository-relative path semantics;
- filtering before rank and limit;
- structured invalid-input behavior; and
- the fact that this is generic caller-supplied eligibility, not wiki logic.

Update llm-wiki documentation only if an existing compiler/provider contract
claims that the Loci provider may return arbitrary indexed repository files.
Do not add a new user-facing configuration knob: canonical eligibility comes
from the existing page loader.

Completion criterion: the additive tool contract is documented once at its
authoritative Loci boundary without duplicating implementation prose.

### 6. Record the proven retrieval boundary in Brain

Only after the installed producer-consumer acceptance path passes, update the
Brain wiki so its durable current-state guidance describes the shipped behavior
rather than this plan.

Use the existing Brain wiki workflow and make the smallest source-backed update:

1. update the canonical `entities/llm-wiki.md` page to state that normal
   Loci-backed recall supplies the exact loaded canonical-page paths as a
   pre-ranking eligibility allowlist;
2. state explicitly that the complete repository remains indexed and that
   source, grounding, maintenance, and direct unscoped Loci routes still expose
   non-page evidence where intended;
3. add or update one source/work-history record with the implementation commits,
   focused test results, and installed temporary-wiki acceptance evidence;
4. keep planned behavior out of durable current-state claims if implementation
   or acceptance has not completed; and
5. run the Brain wiki's smallest directly relevant lint/render or page-validation
   check for the changed pages.

Do not use this step to migrate Brain content, change page/source conventions,
or broaden retrieval doctrine beyond the proven boundary.

Completion criterion: Brain's canonical llm-wiki description and its supporting
evidence accurately record the accepted page-scoped recall boundary while
preserving the explicit non-page workflows.

## Acceptance Criteria

Implementation is complete only when all of the following are true:

- Loci continues to index all supported repository files under its existing
  index policy.
- `loci_search` accepts an optional bounded exact `file_paths` allowlist.
- Omitted `file_paths` preserves repository-wide behavior.
- An empty allowlist yields no eligible symbols and never widens silently.
- File eligibility is applied before scoring, sorting, logging, and `limit`.
- Invalid allowlist inputs fail with bounded structured `INVALID_INPUT` data.
- llm-wiki derives the allowlist from the exact loaded `ProviderContext.pages`
  keys rather than directory guesses or duplicated configuration.
- `LociProvider` emits only candidates whose paths are loaded pages.
- A stale or broken Loci response outside the requested scope is diagnosed and
  rejected without retry or widening.
- Source-specific roles and candidate ownership are removed from
  `LociProvider`; the existing `SourceProvider` and maintenance routes remain
  available.
- A dense source cannot crowd a weaker canonical page out of `limit=1` in the
  focused Loci test.
- The focused Loci and llm-wiki test commands pass.
- One real temporary-wiki stdio path proves complete indexing, pre-ranking page
  eligibility, exact hydration, and page-only compiled Loci evidence.
- One explicit unscoped search proves the indexed source remains retrievable.
- Brain's canonical llm-wiki page records the proven page-scoped normal-recall
  boundary and links to source-backed implementation evidence.
- Brain's focused validation for the changed pages passes.
- Both repositories pass `git diff --check` for the implementation-owned files.
- The two pre-existing untracked llm-wiki files remain unchanged and unstaged.

> **TL;DR:** Acceptance requires page eligibility before Loci's top-N cutoff, page-only llm-wiki Loci candidates, preserved full indexing/source navigation, focused tests, and one real temporary-wiki producer-consumer proof.

## Risks, Rollback, And Stop Conditions

### Risks

- **Post-ranking filtering accidentally survives.** A page may still disappear
  behind dense sources. The `limit=1` adversarial test must fail unless
  eligibility occurs inside `IndexStore.search` before scoring and slicing.
- **Empty scope widens accidentally.** Treat `[]` distinctly from `None` at
  every layer.
- **Path normalization disagrees across repositories.** llm-wiki page keys and
  Loci symbol paths must both use normalized repository-relative POSIX paths.
- **A stale installed Loci server hides the new schema.** Verify the executable
  and restart processes before the end-to-end check.
- **The fix suppresses intentional source evidence.** Keep `SourceProvider` and
  explicit unscoped Loci navigation unchanged and prove the latter in the
  acceptance fixture.
- **Large page sets create unbounded tool requests.** Enforce a finite Loci
  entry-count limit with bounded error details; select a ceiling above known
  production wiki sizes rather than tuning to the current 75-file example.

### Rollback

The change is additive across two repositories. Roll back in reverse dependency
order:

1. revert llm-wiki's use of `file_paths` and provider invariant;
2. then revert Loci's additive argument and filtering support; and
3. reinstall/restart the previous local packages if either was installed.

Do not roll back by excluding sources from the index, deleting Loci stores,
rewriting Brain content, or modifying page/source conventions.

### Stop conditions

Stop and report before widening scope if:

- the loaded `context.pages` keys do not match Loci's indexed `file_path`
  representation;
- the changed installed Loci tool schema is not visible after a confirmed
  reinstall/restart;
- normal compiler behavior depends on non-page Loci candidates for a current
  documented requirement;
- the focused acceptance check exposes an unrelated concrete defect; or
- satisfying the contract would require a new index, registry, configuration
  surface, hook, or Brain content migration.

An unexpected acceptance failure authorizes read-only triage, not a broader
repair. Preserve state and return the evidence for owner direction.

## Scope And Review Boundary

Included:

- one additive exact-file filter in Loci search;
- validation, service, MCP, store, and focused Loci tests;
- llm-wiki gateway adoption and defensive page-only enforcement;
- focused provider tests;
- one temporary-wiki installed producer-consumer acceptance path; and
- minimal authoritative contract documentation.
- one post-acceptance, source-backed Brain current-state update and its focused
  validation.

Excluded:

- changing Loci index inclusion/exclusion policy;
- a page-only duplicate index or cache;
- glob/prefix query languages;
- semantic search, embeddings, or ranking retuning;
- redesigning `SourceProvider`, grounding, maintenance, or Brain page rules;
- changing Anvil's hook except for a separately evidenced compatibility defect;
- Brain content migration or any live Brain mutation beyond the narrow
  post-acceptance current-state and evidence update;
- broad regression, security, performance, benchmark, or judge runs; and
- independent review, release, or publication work unless Vik explicitly asks
  for it.

Implementation ends when the requested behavior and the smallest directly
relevant acceptance path pass. Do not add a post-implementation review phase by
default.

## Implementation Handoff

Start in `/Users/brummerv/llm-wiki`, read this plan, then inspect both live
worktrees before creating implementation commits. Preserve the two pre-existing
untracked llm-wiki files named above. Implement the producer first in
`/Users/brummerv/loci`; do not begin llm-wiki integration until the real Loci
MCP tool accepts and applies `file_paths`.

Use the plan as five gates:

1. Loci store/service/MCP filter and focused tests;
2. llm-wiki gateway/provider adoption and focused tests;
3. actual installed temporary-wiki producer-consumer acceptance; and
4. minimal contract documentation and `git diff --check`; and
5. source-backed Brain current-state update and focused wiki validation.

Commit the repositories separately so the dependency order is explicit. The
llm-wiki commit depends on the Loci commit. Do not stage the unrelated untracked
plan or `uv.lock`. Automatic independent review is not part of implementation;
offer it only after the targeted acceptance path passes.

> **TL;DR:** Ship exact pre-ranking file eligibility in Loci first, pass loaded page paths from llm-wiki second, prove both through one real temporary-wiki stdio path, record the accepted boundary in Brain, and stop before optional review or release work.
