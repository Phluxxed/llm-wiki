# Spec: Automatic Project-Aware Brain Retrieval

**Date:** 2026-08-27

**Status:** Approved for implementation

**Primary repository:** `/Users/brummerv/llm-wiki`

**Consumer repository:** `/Users/brummerv/phluxxed/anvil_redux`

## Objective

When an agent works inside a repository, Brain recall should automatically use
the matching Brain project without requiring the prompt to contain the project
name and without requiring a second hand-maintained project catalogue in
Anvil.

The capability is complete when a host supplies portable Workspace Identity to
the existing `compile-context` interface, llm-wiki resolves that identity
against canonical wiki frontmatter, and the compiler admits question-relevant
project and Global Pages without allowing unrelated project material to crowd
the bounded result. The interface is exposed through both
`wiki_compile_context` and the `llm-wiki compile-context` CLI; both transports
must preserve one request and response contract.

This is a shared llm-wiki capability. Codex, Claude, or another host may provide
workspace identity through its own adapter, but no host should implement Brain
project matching or project-scoped retrieval policy independently.

## Problem

The current runtime has two disconnected facts:

1. Anvil can inspect the current working directory, find its Git root and
   remotes, and derive a directory alias.
2. llm-wiki can compile question-shaped Brain evidence, but its request contains
   only the question, explicit page seeds, state view, temporal view, and
   budget.

Anvil bridges the gap with `brain-bootstrap.json.projects`. That list duplicates
project identity outside the project pages. A repository missing from the list
becomes `unknown_project`, even when Brain already contains relevant pages. A
prompt that does not repeat the repository name then depends on incidental
lexical overlap.

The live llm-wiki workspace demonstrates the failure:

- Anvil identifies the Git root as `/Users/brummerv/llm-wiki`, the directory
  alias as `llm-wiki`, and the normalized remote as
  `github.com/phluxxed/llm-wiki`.
- The Codex Brain bootstrap manifest has no matching project record.
- Automatic Brain recall therefore renders `current_project: unknown_project`.
- A neutral question does not naturally retrieve llm-wiki orientation, while
  the same question with an explicit llm-wiki seed does.

Adding one manifest record for llm-wiki would repair only this repository. It
would preserve the duplicated catalogue and repeat the failure for the next
project.

### Current implementation evidence

- `src/llm_wiki_core/contracts.py` rejects compiler request fields outside the
  current versioned allowlist; Workspace Identity does not cross the compiler
  interface today.
- `src/llm_wiki_core/documents.py` preserves arbitrary page frontmatter in
  `WikiPage`, so canonical project metadata can remain plain Markdown without a
  new store.
- `src/llm_wiki_core/providers/local.py` matches frontmatter only through title
  and tags. Project identity and membership currently have no retrieval
  meaning.
- `src/llm_wiki_core/providers/loci.py` already lets llm-wiki provide an exact
  loaded-page allowlist before Loci ranking. Project-aware eligibility should
  reuse this domain-owned pattern rather than teach Loci about Brain.
- `/Users/brummerv/phluxxed/anvil_redux/src/brain-context/project.ts` already
  derives and normalizes Git remotes and directory aliases.
- `/Users/brummerv/phluxxed/anvil_redux/src/brain-context/retrieve.ts` currently
  sends question, explicit seeds, and budgets to llm-wiki, but no Workspace
  Identity.
- `/Users/brummerv/phluxxed/anvil_redux/src/brain-context/index.ts` currently
  removes manifest-listed project pages from prompt-recall evidence. That
  manifest-owned suppression must not survive the cutover.

## Ubiquitous Language

**Workspace Identity** — portable facts supplied by a host about the current
working repository: normalized Git remotes and a directory alias. It never
contains an absolute path.

**Project Identity** — canonical identity declared by one `type: project` Brain
page: one `project_id`, zero or more aliases, and zero or more normalized Git
remotes.

**Project Membership** — the `projects` frontmatter field on a maintained page.
It names the canonical project IDs to which the page directly applies.

**Project Resolution** — llm-wiki's exact match from Workspace Identity to one
Project Identity. Its result is `matched`, `unknown`, or `ambiguous`.

**Project Scope** — the matched project ID and project page used as a retrieval
signal for one compiler request.

**Project Anchor** — the canonical project page resolved for Project Scope. It
provides current orientation and graph entry points.

**Global Page** — a page with no `projects` field. It remains eligible when its
content answers the question.

## Design Decisions

### 1. Project pages are the identity source of truth

Project identity belongs on the project page:

```yaml
type: project
identity:
  project_id: llm_wiki
  aliases:
    - llm-wiki
  remotes:
    - github.com/phluxxed/llm-wiki
```

Rules:

- `project_id` is a stable lowercase snake-case identifier matching
  `[a-z0-9]+(?:_[a-z0-9]+)*` with at most 64 characters.
- `aliases` are portable human or directory names. Matching is exact after
  trimming and case folding; it is not fuzzy. A page may declare at most 32
  aliases, each at most 255 characters.
- `remotes` use Anvil's existing credential-free normalized remote form.
  A page may declare at most 16 remotes, each at most 1,024 characters.
- Identity requires `project_id` plus at least one alias or remote. Duplicate
  aliases or remotes on one page are invalid after normalization.
- Project IDs, normalized aliases, and normalized remotes are unique across
  loaded project pages.
- The project page implicitly belongs to its own project ID and does not repeat
  a `projects` field.
- A project page without valid identity remains readable but cannot be resolved
  from Workspace Identity.

`brain-bootstrap.json` continues to own bounded bootstrap pages and budgets. It
stops being a project catalogue after migration.

### 2. Maintained pages declare direct project membership

Project-specific pages use canonical project IDs:

```yaml
projects:
  - llm_wiki
```

Rules:

- A page may belong to more than one project.
- Omitting `projects` means global or not yet classified; it never means the
  current project.
- A present `projects` field must be a non-empty list of at most 16 unique valid
  project IDs. `null`, a scalar, an empty list, duplicates, and dangling IDs are
  invalid; they do not make the page global.
- Every listed ID must resolve to one loaded project page.
- Shared patterns and entities list multiple IDs only when each association is
  substantive.
- Immutable source files do not require project membership. Their derived wiki
  pages own scope, and existing source/evidence links preserve provenance.
- Existing pages without `projects` remain valid during migration.

Links, tags, and Loci graph evidence remain useful discovery signals. They do
not become authoritative Project Membership because their meaning is broader
than direct applicability.

### 3. Hosts supply identity; llm-wiki resolves projects

The compiler accepts one optional additive object. MCP uses the object directly:

```json
{
  "workspace_identity": {
    "directory_alias": "llm-wiki",
    "remotes": ["github.com/phluxxed/llm-wiki"]
  }
}
```

The interface deliberately excludes:

- cwd or another absolute path;
- host name;
- agent identity;
- a caller-selected Brain page; and
- a caller-selected project ID.

This keeps the host adapter shallow. It reports observed workspace facts and
does not decide Brain meaning.

The CLI exposes the same object without an absolute path:

```text
llm-wiki compile-context \
  --workspace-directory-alias llm-wiki \
  --workspace-remote github.com/phluxxed/llm-wiki
```

Repeated `--workspace-remote` flags preserve all observed normalized remotes.
The CLI constructs the same internal request object as MCP; it does not own a
second resolver.

Anvil's existing internal workspace inspection type may continue to contain a
Git root for local command execution. Before transport, Anvil must construct a
separate `WorkspaceIdentityInput` wire value containing only
`directory_alias` and `remotes`. A test must prove that `gitRoot`, cwd, and any
other absolute path are absent from CLI arguments, compiler requests, compiler
response metadata, and the new project-resolution diagnostic payload. Anvil's
existing trusted Brain-usage journal may retain its standard workspace
provenance for partitioning and audit; this feature must not copy `gitRoot`,
cwd, or raw remotes into recall observations or other new usage fields.

Project-aware retrieval is compiler contract version 3. Version 3 is a superset
of version 2, including its temporal query support, and adds optional
`workspace_identity` plus required `query.project_resolution` output. Versions
1 and 2 retain their exact accepted fields and serialization; passing
Workspace Identity with either version is `INVALID_INPUT`.

In version 3, an absent `workspace_identity` preserves current retrieval
behaviour and returns `project_resolution: {"status": "not_requested"}`.
Invalid values fail request validation before provider execution.
Anvil validates and normalizes raw Git remote observations locally, discarding
unusable values with a bounded diagnostic before it constructs the wire value.
llm-wiki accepts only credential-free normalized `host/path` remotes and rejects
wire values containing schemes, user-info, query/fragment data, traversal
syntax, control characters, or unbounded input. Raw remote strings never cross
the compiler seam.

The wire object allows at most eight unique remotes of at most 1,024 characters
each and one directory alias of at most 255 characters. The alias is one
non-empty basename with no slash, backslash, or control character. Duplicate
normalized remotes collapse in first-observed order. A normalized remote is
lowercase ASCII, contains one hostname and one or more path segments, and uses
only letters, digits, `.`, `_`, `-`, and `/`; it has no leading/trailing slash
or empty, `.`/`..` path segment.

### 4. Resolution is exact, deterministic, and observable

llm-wiki resolves Workspace Identity in this order:

1. exact normalized remote;
2. exact normalized directory alias; and
3. `unknown` when neither matches.

All supplied remotes are evaluated together. Remotes that match more than one
project produce `ambiguous`, even when each individual remote is unique. One or
more remote matches take precedence over an alias that names a different
project; the compiler returns the remote match and diagnostic
`PROJECT_ALIAS_SHADOWED`. With no remote match, an alias matching more than one
project is `ambiguous`. The compiler must not select one by file order,
retrieval rank, title similarity, or recency.

Contract version 3 adds bounded resolution metadata to the response's existing
`query` object:

```json
{
  "query": {
    "project_resolution": {
      "status": "matched",
      "project_id": "llm_wiki",
      "page": "projects/llm-wiki.md",
      "matched_by": "remote"
    }
  }
}
```

The discriminated result shapes also include:

```json
{"status": "not_requested"}
```

and:

```json
{"status": "unknown"}
```

```json
{
  "status": "ambiguous",
  "matched_by": "remote",
  "candidates": [
    {"project_id": "alpha", "page": "projects/alpha.md"},
    {"project_id": "beta", "page": "projects/beta.md"}
  ],
  "candidate_count": 2
}
```

Ambiguous candidates sort by `(project_id, page)`, return at most eight rows,
and report the complete `candidate_count`. The compiler emits stable diagnostic
code `PROJECT_IDENTITY_AMBIGUOUS`. Invalid canonical identity emits
`PROJECT_IDENTITY_INVALID`. Neither state may inherit an unrelated project.

Anvil renders a matched page as the existing Recall Route value
`current_project: projects/<page>.md`. It renders both `unknown` and `ambiguous`
as `current_project: unknown_project`; the ambiguous diagnostic remains visible
in bounded diagnostics rather than being converted into a guessed project.

Resolution metadata, diagnostics, and any selected project evidence count
against the existing complete response budget.

### 5. Project Scope constrains project material; it does not replace the question

Workspace identity must not be concatenated to the natural-language question.
That would hide structured routing inside lexical search, alter query shape,
and make provenance unclear.

For a matched project, llm-wiki constructs an active project set. It initially
contains the workspace-matched project. Exact Project Identities named in the
question and Project Membership reached by explicit caller seeds may widen the
set. Expansion is exact and observable; fuzzy similarity cannot widen it.

Question identity matching case-folds text, collapses runs of spaces,
underscores, and hyphens to one space, and matches a complete project ID, alias,
or project title bounded by non-alphanumeric characters. Every added project ID
is reported in query metadata with reason `question_identity_match` or
`explicit_seed_membership`.

The compiler derives one internal `ProjectIndex` from all loaded canonical
pages, resolves explicit seeds against that complete mapping, computes the
active project set, and only then constructs the eligible-page mapping supplied
to providers. Normal page eligibility is:

- the Project Anchors for the active project set;
- pages whose `projects` list contains at least one active project ID; and
- Global Pages with no `projects` field.

Other project landing pages and pages with a non-empty `projects` list that
does not intersect the active set are ineligible before provider ranking.
Explicit caller seeds may admit an exact out-of-scope page, but the response
must record that cross-project reason.

Malformed Project Identity or Project Membership never enters the Global Page
set. In contract version 3:

- a `type: project` page with missing or invalid `identity` is ineligible unless
  explicitly seeded;
- `identity` on a non-project page is invalid and does not create an anchor;
- a malformed or dangling `projects` field makes that page ineligible unless
  explicitly seeded; and
- an explicitly seeded invalid page admits only that exact page and does not
  widen the active project set.

Versions 1 and 2 retain their current page-loading and provider behaviour.

Every normal provider receives the same eligible-page mapping:

- Frontmatter and text iterate only eligible pages.
- Graph edges and traversal are constructed only from eligible pages.
- Source evidence is reached only through an eligible page's existing source
  reference.
- Loci receives the exact eligible-page paths before it scores, sorts, logs, or
  applies its result limit.
- SeedProvider receives the already-admitted exact caller seeds.

No provider may recover an inactive or invalid page from the complete loaded
mapping. The complete mapping is used only for ProjectIndex construction and
safe explicit-seed resolution.

Within that eligible set:

- the Project Anchor becomes an internal scope seed for provider discovery;
- candidates whose page has matching Project Membership record a deterministic
  `active_project:<project_id>` provenance signal;
- the Project Anchor receives the same signal;
- question-derived relevance, state, authority, evidence roles, and budget
  remain mandatory selection inputs;
- explicit caller seeds retain their current exact-seed semantics; and
- Global Pages remain eligible when they answer the question.

An internal scope seed is not an explicit caller seed. It must not inherit the
current `SeedProvider` rule that makes every explicit seed unconditional output.
Providers may use it for graph and candidate discovery without forcing the
project page into every answer.

After provider collection, if no question-derived candidate belongs to the
workspace-matched project, the compiler must add the Project Anchor as a
fallback candidate with:

- provider `project`;
- route and selection reason `project_orientation_fallback`;
- the whole project-page body and its normal whole-body locator;
- the roles derived for the current query shape;
- the page's authored state and authority signals; and
- normal truncation and complete-response budget behaviour.

The fallback provider ranks after the current text provider, but its candidate
is exempt from lower-marginal-value omission in the same way an explicit seed
is. It is still subject to item, content-byte, complete-byte, and estimated-token
ceilings. No separate project-membership rank weight is added in version 3;
eligibility plus mandatory fallback provides the project-aware behaviour
without rewriting existing provider precedence. This makes questions such as
“What should I work on next?” useful without allowing unrelated project history
to crowd out a specific answer.

Project Scope is a hard exclusion only for pages explicitly owned by inactive
projects. It is not a project-only filter: Global Pages remain eligible, and an
exact cross-project question or explicit seed widens the active project set.

For `unknown` or `ambiguous` resolution, the compiler runs current
question-shaped retrieval and reports the resolution state. It applies no
workspace-project boost or fallback project page. Exact project identities in
the question and explicit seeds retain their current retrieval routes.

### 6. llm-wiki owns the deep module

The project-aware compiler seam remains `wiki_compile_context`.

llm-wiki owns:

- Project Identity and Project Membership parsing;
- uniqueness and reference validation;
- Project Resolution;
- scope-seed construction;
- project-aware page eligibility before Loci or another provider ranks;
- project-aware provider signals and selection;
- response metadata and diagnostics;
- complete response budgeting; and
- cross-wiki compatibility behaviour.

Host adapters own only Workspace Identity observation. For Anvil, this reuses
the existing Git-root, remote-normalization, and directory-alias implementation.
Claude or another host supplies the same portable object through its own
lifecycle adapter.

Anvil must also stop using the legacy manifest project list as a blanket
evidence-suppression list. After cutover it may de-duplicate only the exact
Project Anchor when that body was already rendered elsewhere in the same hook
response. It must preserve other evidence selected by the compiler and must not
reconstruct project membership outside llm-wiki.

Loci remains repository navigation infrastructure. It does not learn Brain
project semantics. llm-wiki may pass exact page paths or scope signals into
existing generic Loci interfaces, but Project Identity and Project Membership
do not move into Loci.

### 7. Recall remains read-only

Unknown repositories do not create Brain pages during SessionStart or prompt
recall. Automatic mutation would turn temporary checkouts, dependencies, and
experiments into durable cognition without Steward judgment.

Unknown Workspace Identity may become bounded maintenance evidence through the
existing Brain Steward workflow. Creating a project page and classifying
related pages remain explicit wiki changes with normal lint, render, and review.

## Validation Contract

llm-wiki lint reports:

- malformed `identity` or `projects` values;
- duplicate project IDs, aliases, or remotes;
- a `projects` entry with no matching project page;
- a remote that is not normalized or contains credentials; and
- a project page that has no resolvable identity.

During migration, a project page without identity is a lint warning. Invalid
present identity/membership, duplicates, and dangling membership are lint
errors. Once all supported strict Brain wikis have migrated, their profile may
promote missing project identity to an error. Permissive wiki reading remains
compatible with existing pages.

Compiler-time invalid or conflicting project metadata emits bounded diagnostics
and excludes the invalid pages under version 3. It must not make otherwise
valid Global Page retrieval unavailable. Doctor retains its current runtime,
configuration, and provider-health ownership; it does not duplicate page lint.

## Migration and Compatibility

Rollout order matters because Anvil and llm-wiki are separate repositories.

1. Add frontmatter parsing, validation, request/response schema and CLI support, and
   project-aware compiler tests to llm-wiki under contract version 3. Existing
   version 1 and 2 calls remain unchanged.
2. Move each existing Brain bootstrap project record into its canonical project
   page. Preserve the existing `identity.project_id` and aliases; add remotes.
3. Add reviewed `projects` membership to the highest-value project-specific
   pages. Unclassified pages remain valid and receive no project boost.
4. Add Anvil support for Brain bootstrap manifest version 2. Version 2 preserves
   core pages, Collaboration Kernel declarations, and budgets, but removes the
   `projects` field. A version 2 manifest containing `projects` is invalid.
5. Keep version 1 support for one deprecation window. A version 1 manifest uses
   only the current manifest resolver and the legacy compiler call. A version 2
   manifest skips `selectBrainProject`, always calls the project-aware compiler,
   and consumes `query.project_resolution`. The two identity sources are never
   merged or compared during one request.
6. Upgrade the Brain-local llm-wiki runtime before changing that Brain to
   manifest version 2. The manifest version is the capability switch; Anvil
   does not probe an unsupported CLI with new flags and then guess from a
   generic process failure.
7. Change each Brain manifest to version 2 only after its project-page identity
   lint passes. A version 2 compiler failure degrades recall visibly and must
   not fall back to removed manifest identity.
8. Prove the real host-to-CLI-to-render path, then delete Anvil's version 1
   manifest project resolver after the deprecation window.

## Acceptance Contract

The smallest complete acceptance check uses a temporary strict Brain plus the
shipped Anvil-to-llm-wiki producer-consumer path.

The fixture contains:

- project Alpha with a project page, alias, and normalized remote;
- one Alpha work-history page with `projects: [alpha]`;
- one relevant Global Page;
- one stronger lexical match belonging to project Beta;
- one unknown repository; and
- an ambiguous identity fixture.

The check must prove:

1. **Remote match survives a renamed checkout.** Anvil observes Alpha's remote
   from a directory whose basename is not an alias. llm-wiki resolves Alpha by
   remote.
2. **Alias fallback works without a remote.** A non-Git or no-remote workspace
   resolves by exact directory alias.
3. **Project relevance is automatic.** A neutral prompt receives Alpha
   orientation or Alpha-scoped evidence without containing “Alpha” and without
   an explicit page seed.
4. **Project scope preserves global knowledge.** A relevant Global Page remains
   selectable even though inactive project-owned pages are excluded.
5. **Other projects do not leak.** Beta's stronger lexical page cannot receive
   Alpha's project-scope signal or enter normal Alpha-scoped recall unless the
   question names Beta exactly or the caller explicitly seeds it.
6. **Unknown stays unknown.** An unmapped repository returns
   `project_resolution.status = unknown` and no project fallback.
7. **Ambiguous stays ambiguous.** Duplicate matching identity returns a stable
   diagnostic and no selected project.
8. **Explicit seeds still work.** Caller seeds preserve their current
   validation, selection, and provenance semantics.
9. **Budgets remain complete.** Resolution metadata, diagnostics, evidence,
   omissions, and framing fit the declared byte and optional estimated-token
   ceilings.
10. **The shipped path is real.** A `UserPromptSubmit` event passes through
    `anvil codex context-hook` and the Context Harness. Anvil supplies the
    path-free `WorkspaceIdentityInput` to the real `llm-wiki compile-context`
    CLI, consumes `query.project_resolution`, renders the project-page marker
    and selected evidence, preserves existing Kernel retention behaviour, and
    does not apply the legacy manifest `projectSet` suppression.

Focused contract checks must additionally prove:

- strict version 1 and 2 requests and responses remain unchanged;
- version 3 rejects malformed, oversized, path-bearing, or non-normalized wire
  identity;
- multiple remote matches and remote/alias conflict follow the declared result
  and diagnostic rules;
- malformed, empty, duplicate, and dangling Project Membership cannot become
  Global Page eligibility;
- inactive project pages cannot re-enter through frontmatter, text, graph,
  source, or Loci providers;
- the fallback Project Anchor is added only when no question-derived candidate
  belongs to the workspace-matched project and is budgeted like other evidence;
  and
- explicit cross-project seeds widen eligibility without changing version 1 or
  2 seed semantics.

Isolated resolver tests or compiler tests do not satisfy item 10.

## Out of Scope

- Automatically creating or editing Brain pages during recall.
- Fuzzy, semantic, or model-selected Project Resolution.
- Sending local absolute paths to Brain or storing them in frontmatter.
- Turning Project Scope into a repository-wide hard filter.
- Requiring every page to belong to a project.
- Teaching Loci about Brain page types or project semantics.
- Implementing another host's lifecycle adapter.
- Bulk-assigning Project Membership without Brain Steward review.

## Implementation Slices After Approval

1. **llm-wiki contract and model:** Workspace Identity, Project Identity,
   Project Membership, resolution result, and schema tests.
2. **llm-wiki retrieval:** internal scope seeds, project signals, fallback
   orientation, diagnostics, selection tests, and full budget enforcement.
3. **Brain migration:** canonical identity on existing project pages plus a
   reviewed high-value membership slice.
4. **Anvil adapter:** pass Workspace Identity, consume Project Resolution, and
   retain one bounded legacy compatibility path.
5. **End-to-end cutover:** real lifecycle-to-MCP acceptance, fresh-session
   dogfood in mapped and unknown repositories, then manifest-project removal.

Implementation work should begin only after the frontmatter contract, retrieval
semantics, and deprecation posture above are accepted.
