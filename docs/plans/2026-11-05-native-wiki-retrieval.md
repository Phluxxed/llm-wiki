# Native wiki retrieval rollout

## Accepted outcome

Remove Loci retrieval, transport, cache mirroring, graph profiles, runtime
diagnostics, and configuration from llm-wiki. Keep the existing seed,
frontmatter, text, Markdown-link graph, and source providers. Preserve temporal
reasoning, project scoping, OKF metadata, budgets, and explicit coverage limits.
Runtime contract becomes 3; configuration schema stays 1 and compiler request
contracts remain unchanged. Runtime-2 wikis require an explicit, receipt-backed
migration that preserves customization and comments and supports rollback.

Migrate Brain, install the runtime in both environments, remove only llm-wiki's
Loci registration settings, and verify fresh MCP lookup, relationships, source
grounding, and maintenance with Loci unavailable. Preserve dirty work, immutable
sources, temporal history, historical reports, and external caches. Commit and
push scoped changes in llm-wiki and Brain. Codex code-navigation Loci is separate.

## Delegated work

The primary session owns integration, Brain stewardship and rollout, host
configuration, complete acceptance, commits, and pushes. Workers share this
checkout and have exclusive scopes. Initial capacity: primary only, three slots
available. Each worker must finish directly without spawning agents.

| Task | Terminal contribution and owned scope | Acceptance | Model / effort / fork |
| --- | --- | --- | --- |
| native_runtime | Remove runtime Loci implementation in compiler, selection, providers, graph adapter; own provider/compiler/selection/project/temporal regression tests and affected fixtures, plus graph baseline script/tests | Five native providers, no Loci execution, genuine link evidence, missing bridges uncovered, existing reasoning/budget checks preserved | configured default / high / none |
| runtime_migration | Config, doctor, migration, script drift and their dedicated tests | Runtime 3 rejects retired settings; runtime 2 inspection, dry-run, customized comment-preserving apply, verify and exact rollback work | configured default / high / none |
| mcp_docs | MCP output models and dedicated MCP tests; scaffold and active docs/skill contracts | Public schemas and scaffolds contain only native providers/runtime 3, historical evidence preserved, docs accurate | configured default / high / none |
| bridge_semantics | Read-only bounded consultation: atomic native paths across graph, selection and temporal filtering | Recommend the smallest way to prevent incomplete paths or ineligible intermediate pages covering a bridge; preserve required behavior without a replacement graph service | explicit gpt-6-astra / high / none |

## Results

- `mcp_docs`: adopted schema/scaffold/docs changes. Its 38 focused tests passed;
  existing unsupported skill `version` frontmatter remains outside this change.
- `runtime_migration`: adopted native config/doctor and receipt migration. Its
  39 focused tests passed, including customized comment-preserving rollback.
  Unsupported or ambiguous rewrites block instead of guessing.
- `bridge_semantics` launches after those workers completed (capacity changed
  from full to two running). The existing native graph independently marks
  every hop as a bridge; a budget can therefore drop part of a chain while
  reporting coverage. Atomic paths also require all intermediate pages to pass
  temporal eligibility. This is a concrete gap in the accepted preservation
  requirements, not a request for broader graph research.
- `bridge_semantics`: adopted atomic paths and per-span metadata, with fixes
  for draft/unknown lifecycle status and mixed historical/contradicted state.
  The consultation confirmed project filtering occurs before graph creation.
  Alternative-path search and broader retrieval evaluations are not required
  for this outcome. Its response is read-only; implementation remains owned by
  `native_runtime` and acceptance by the primary.
- `native_runtime`: adopted five-provider implementation, removal of external
  modules, native baseline tooling, and atomic native paths with per-page state
  and provenance. Its 152 focused tests passed, followed by a 13-test baseline
  suite including the final native-span reconstruction regression.
- Primary integration updated three remaining active fixture configurations
  (question relevance, snapshots, and OKF export) to runtime 3. Historical
  compiler request/response fixtures remain unchanged where intentional.

## Completed acceptance

- Full repository command: `.venv/bin/python -m unittest discover -s tests -t . -q`.
  Result: 544 tests run successfully, with two optional live cross-wiki cases
  skipped because their external fixture roots were not configured.
- Runtime-2 inspect and dry-run agreed on the three Brain targets. Applied
  receipt `20261105T060401Z-3bb609caa189`; verification reported compatible,
  compiler smoke passed, no pending operations, and no blockers. Customized
  multiline/comment/CRLF configurations and exact-byte rollback passed tests.
- Built one wheel and installed it in both the repository and Brain virtual
  environments. Readback reported runtime contract 3 and the five providers in
  both installed packages. Schema version remains 1.
- Removed `LOCI_BASE_DIR` and `LOCI_STORE_NAMESPACE` only from Codex's llm-wiki
  registration. Its only remaining setting is `LLM_WIKI_HOME`; the independent
  Codex Loci registration was compared and preserved exactly.
- A fresh MCP server launched from the installed configured executable with
  an empty executable search path and no Loci environment settings. Real Brain
  page/question lookup, lexical section retrieval, source excerpt, authored
  relationship, missing relationship, and read-only maintenance proposal checks
  passed. Process auditing permitted only the query adapter `--help` health
  probe; any other child launch would fail. No Loci calls or diagnostics occurred.
- Native relationship proof was the authored link from the identity page to
  the maintained-cognition page. The invented QuasarCrane relationship left
  bridge uncovered. The real relationship wording also triggered maintenance
  roles, leaving finding/ownership uncovered; budget omissions remained explicit.
  These checks establish bounded behavior, not general answer completeness.
- Brain Steward reviewed fresh candidate-only proposals, updated the existing
  project/entity pages and operational documentation, saved one immutable
  source, and appended two validated temporal claims. Existing 424 source files
  and 410 temporal records were compared and preserved. Primitive tags/counts
  are unchanged. Unrelated dirty files were byte-compared with the baseline.
- Brain lint reports no structural issues; render completed. The pre-existing
  rendered artifact changes were metadata evaluation timestamps only; the new
  render includes the accepted pages/source/log update. Existing historical
  evidence and external cache directories were not changed.
- The resident MCP connection still holds runtime 2 and requires reconnecting;
  this was observed explicitly. Fresh installed runtime 3 is verified. No claim
  of refreshing an already loaded host catalog is made.

Local detailed receipts, process audit, baseline hashes, raw MCP packets, and
test logs are retained at `/tmp/llm-wiki-native-20261105/`. Brain's migration
backups and receipt also remain under its ignored `.llm-wiki/migrations/`.
