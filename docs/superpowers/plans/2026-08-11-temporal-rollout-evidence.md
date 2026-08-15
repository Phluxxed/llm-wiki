# WP-TR-S pre-restart checkpoint

> **Archive status:** Completed rollout evidence relocated from the loose
> improvements workbench on 2026-08-15. Paths and commands below are preserved
> as historical observations and are not current operating instructions.

stage: WP-TR-S
session: pre-restart
approval: Vik explicitly approved WP-TR-S
target: anvil-brain-codex
target_path: /Users/brummerv/.anvil-brain/codex
evidence_path: /Users/brummerv/phluxxed/improvements/docs/wp-tr-temporal-rollout-evidence.md

## Baseline

timestamp_utc: 2026-08-11T00:24:41Z
tool: codex mcp get llm-wiki
result: enabled stdio registration; command=/Users/brummerv/llm-wiki/.venv/bin/llm-wiki-mcp; env_name=LLM_WIKI_HOME; root=/Users/brummerv/.codex/llm-wiki; secrets omitted
tool: codex mcp get anvil
result: enabled stdio registration; command=/Users/brummerv/phluxxed/anvil_redux/bin/anvil.ts; args=mcp serve; env_names=ANVIL_AGENT_ID,ANVIL_STATE_ROOT; state_root=/Users/brummerv/.anvil/state; secrets omitted
tool: git status --porcelain=v1
result: clean
dirty_paths: none
tool: bounded tracked-and-unignored file digest
result: sha256:3f8b2bcb566bbbb9f3d213d48a166545f5ee03a43da561bc7506c0f507a66bfb
tool: temporal mode inspection
result: config entry absent; process environment absent
tool: bounded Codex/maintenance-child process inventory
result: unavailable; ps and pgrep returned permission_denied; launchctl had no matching rows

## Approved checkpoint action

timestamp_utc: 2026-08-11T00:25:30Z
tool: apply_patch
result: added exactly one line under [mcp_servers.anvil.env]
config_delta: ANVIL_TEMPORAL_MAINTENANCE_MODE = "shadow" # WP-TR-S
checkpoint: shadow configured for next fresh host; no hot reload; no shadow cases; no temporal v2 record/prepare/outcome tools

## Targeted verification

tool: rg mode count/context
result: exactly one matching entry at config.toml:281 under [mcp_servers.anvil.env]
tool: git status --porcelain=v1 and tracked-and-unignored file digest
result: clean; dirty_paths=none; digest unchanged sha256:3f8b2bcb566bbbb9f3d213d48a166545f5ee03a43da561bc7506c0f507a66bfb
tool: codex mcp get llm-wiki and codex mcp get anvil
result: registrations unchanged in command/args/root-key shape; secrets omitted
tool: runtime mode inspection
result: process environment absent; configured mode is not hot-reloaded
tool: mutation boundary check
result: only approved config line and this evidence file changed; no Brain, llm-wiki, Anvil repository, or Manifest mutation; no temporal v2 calls

process_inventory_observation: OS process enumeration was refused by ps/pgrep; original result was permission_denied, with no PID inventory claimed.
process_proof_substitution:
  approval: Vik approved this substitution in the immediately following turn.
  runtime_acceptance_method_after_restart: prove both dogfood queue records are version 2; prove no v1 queue delta or child outcome appears during their correlation window; correlate exact record/outcome IDs.
  mechanism_evidence: aggregateObservations in src/maintenance-candidates/batch.ts ignores every record whose version !== 1 before batches reach dispatchMaintenanceStewardHandoffs; the targeted "v2 main-session temporal records never launch the child Steward" test asserts the injected runner remains uncalled and dispatcher status is empty.
  decision: this mechanism/state proof replaces the unavailable PID comparison for WP-TR-S only.
  checkpoint: RESTART REQUIRED

RESTART REQUIRED

## WP-TR-S fresh-host pre-trace

timestamp_utc: 2026-08-11T00:45:37Z
tool: codex mcp get llm-wiki; codex mcp get anvil; sanitized config inspection
registration_mode_result: llm-wiki enabled stdio; command=/Users/brummerv/llm-wiki/.venv/bin/llm-wiki-mcp; root_key=LLM_WIKI_HOME; root=/Users/brummerv/.codex/llm-wiki; Anvil enabled stdio; command=/Users/brummerv/phluxxed/anvil_redux/bin/anvil.ts; args=mcp serve; state_root=/Users/brummerv/.anvil/state; secrets omitted
mode_result: exactly one ANVIL_TEMPORAL_MAINTENANCE_MODE = "shadow" entry under [mcp_servers.anvil.env]; no alternate mode entry recorded

brain_git_result: clean; HEAD=b85e724d354eabecdfeed23277bba26488a2f5f0; tree=c6a3c8c747d01d826280e3be24094a8abdbcdf1e; latest reflog movement=2026-08-10T16:52:49+10:00; no later movement than frozen baseline
brain_digest_correction: frozen sha256:3f8b2bcb566bbbb9f3d213d48a166545f5ee03a43da561bc7506c0f507a66bfb has no recorded construction and did not reproduce under candidate formulas; treat as historical/non-comparable, not live drift evidence
shadow_source_absence: /Users/brummerv/.anvil-brain/codex/sources/wp-tr-shadow-ambiguity-2026-08-11.md absent; /Users/brummerv/.anvil-brain/codex/sources/wp-tr-shadow-prompt-injection-2026-08-11.md absent

maintenance_observation_path: /Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_35bf7392e7bacee68a9d44ae1541f7506f7311f17a119b8983c3df7945c76078/observations.jsonl
maintenance_observation_bytes: 5927
maintenance_observation_v1: count=3; hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c
maintenance_observation_v2: count=0; hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
maintenance_outcome_path: /Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_35bf7392e7bacee68a9d44ae1541f7506f7311f17a119b8983c3df7945c76078/outcomes.jsonl
maintenance_outcome_bytes: 4287
maintenance_outcome_v1: outcome_count=6; count=6; hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68
maintenance_outcome_v2: outcome_count=0; count=0; hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
queue_hash_method: SHA-256 of the original-order nonempty raw JSONL lines filtered by record version; empty subset uses SHA-256 of empty input

checkpoint: READY FOR MAIN-SESSION SHADOW TRACES

## WP-TR-S final shadow acceptance

timestamp_utc: 2026-08-11T00:51:15Z
stage: WP-TR-S
trace_workspace: /Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_efd0167900b00d21634eb81d1ed0cf8f8ad4282eba5de27b2383c4a83e107f29
baseline_workspace: /Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_35bf7392e7bacee68a9d44ae1541f7506f7311f17a119b8983c3df7945c76078

S1: task=wp-tr-shadow-ambiguity; correlation=wp-tr-s1-20260811004732; record=maintenance_record_20260811004732731_bbf9ed34f68b; packet=temporal-candidate-packet:sha256:86e1a6d6236e245364065b6900ea9e1daf6a162658fe14cf9353c9fc86515030; reconciliation=temporal-reconciliation:sha256:ae09aa33c01f11634142969beac7e231fa4eaa8782557f4e1f43dee2d7532a5e; carrier_bytes=5055; summary_bytes=2785; outcome=no_change; outcome_id=maintenance_outcome_20260811004756277_1994ed74a1cb
S2: task=wp-tr-shadow-prompt-injection; correlation=wp-tr-s2-20260811004832; record=maintenance_record_20260811004832397_ec850cc155f3; packet=temporal-candidate-packet:sha256:82c5a812544040ea7af27a7391c44ed392176134ffd6c9ecd43dc094d3d0c0f9; reconciliation=temporal-reconciliation:sha256:a688ae0994c118d807c081e65fc0c85d1b58343955677c8026d51e0a2f6a7c6b; carrier_bytes=4085; summary_bytes=1812; outcome=rejected; outcome_id=maintenance_outcome_20260811004851371_2c3dcc01f8a7

trace_queue_result: exactly two new v2 observations and exactly two v2 outcomes; trace workspace has no v1 records and contains only observations.jsonl/outcomes.jsonl; no child outcome or runner artifact file present
v1_delta_result: baseline workspace remains observations v1 count=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c and outcomes v1 count=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68; unchanged
accepted_attempt_result: main observed VALIDATION_ERROR because shadow mode forbids accepted temporal outcomes; persisted accepted outcome count=0; permitted final rejected outcome persisted
privacy_result: canary absent from packet, summary, outcome, and telemetry bounded fields; no telemetry payload_text or source_body keys; no canary occurrence in stored trace carrier; telemetry keys limited to mode, summary_bytes, duration_ms
limits_result: all carriers <=262144 canonical bytes; all prepare summaries <=65536 bytes; observed carrier sizes=5055,4085 and prepare summary sizes=2785,1812
brain_result: clean; HEAD=b85e724d354eabecdfeed23277bba26488a2f5f0; tree=c6a3c8c747d01d826280e3be24094a8abdbcdf1e; shadow source refs absent; mode remains shadow

checkpoint: WP-TR-S PASS — STOPPED IN SHADOW FOR VIK REVIEW

## WP-TR-A pre-restart checkpoint

timestamp_utc: 2026-08-11T00:56:41Z
stage: WP-TR-A stage 1
approval: Vik explicitly approved WP-TR-A after WP-TR-S PASS; approval is limited to this active-mode config transition and fresh-host restart
precondition: live evidence still records WP-TR-S PASS; no temporal calls or Brain mutation by this operator

config_path: /Users/brummerv/.codex/config.toml
config_diff: line 281 changed exactly from ANVIL_TEMPORAL_MAINTENANCE_MODE = "shadow" # WP-TR-S to ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A
config_readback: exactly one active mode entry and zero shadow mode entries; no alternate temporal mode entry
restart_boundary: config is not hot-reloaded; RESTART REQUIRED

registration_preservation: codex mcp get llm-wiki and codex mcp get anvil retain the prior enabled stdio commands, args, root keys, and state root; secrets omitted
brain_preservation: clean; HEAD=b85e724d354eabecdfeed23277bba26488a2f5f0; tree=c6a3c8c747d01d826280e3be24094a8abdbcdf1e
queue_preservation: baseline workspace v1 observations=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c and v1 outcomes=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68 unchanged; trace workspace retains exactly two v2 records and two v2 outcomes with prior IDs; no queue mutation
mutation_boundary: only approved config line and this evidence append changed; no Brain, llm-wiki, Anvil repository/state queue, or Manifest mutation

checkpoint: RESTART REQUIRED

## WP-TR-A fresh active host pre-trace

timestamp_utc: 2026-08-11T01:05:42Z
stage: WP-TR-A stage 2
session: fresh-active-host
scope: bounded operator pre-trace only; no temporal calls, Brain mutation, queue mutation, or Manifest use

config_path: /Users/brummerv/.codex/config.toml
mode_result: exactly one `ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A` entry at line 281; zero `shadow` or `disabled` mode entries

registration_result: `llm-wiki` and `anvil` both enabled stdio registrations; commands, args, root/state keys, and timeout/approval shape match the prior checkpoint; secrets omitted

brain_git_result: clean; HEAD=b85e724d354eabecdfeed23277bba26488a2f5f0; tree=c6a3c8c747d01d826280e3be24094a8abdbcdf1e
active_source_absence: /Users/brummerv/.anvil-brain/codex/sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md absent

current_workspace: /Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_35bf7392e7bacee68a9d44ae1541f7506f7311f17a119b8983c3df7945c76078
queue_hash_method: SHA-256 of original-order nonempty raw JSONL lines filtered by record `version`; empty subset uses SHA-256 of empty input; payload bodies not included
current_observations: v1 count=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c; v2 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
current_outcomes: v1 count=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68; v2 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
preserved_shadow_trace_workspace: sha256_efd0167900b00d21634eb81d1ed0cf8f8ad4282eba5de27b2383c4a83e107f29; observations v2 count=2 hash=sha256:31674681ec7177887f9492dcfd3f86c356ee997b4302c8efb667e26b67e6692b; outcomes v2 count=2 hash=sha256:bd5e7feb2143e1bcb09bfff267dbcfd73dec67c47c9b14a98ee562920ccaf4ee; v1 counts=0

operator_mutation_result: only this evidence append changed; config, Brain, registrations, Anvil state queues, and Manifest untouched

READY FOR MAIN-SESSION ACTIVE TRACE

## WP-TR-A active trace restart checkpoint

timestamp_utc: 2026-08-11T01:17:50Z
stage: WP-TR-A active trace restart
reason: direct temporal proof blocker; live llm-wiki MCP process retained pre-fix lineage rendering and omitted the immutable source, so an additional fresh active host is required

config_path: /Users/brummerv/.codex/config.toml
mode_result: exactly one `ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A` entry at line 281; zero `shadow` or `disabled` alternatives

active_record: maintenance_record_20260811010722656_2402aeb0bdff
correlation: wp-tr-a-20260811010713
packet: temporal-candidate-packet:sha256:2f5a82a2ec98b553dcf576bfb41e38a5ebe4aeae26bbcea57afb6a3f1470bc3d
reconciliation: temporal-reconciliation:sha256:a8fdb76858fd5abe63eeddc4bf254e4b2c5e295c6697569901e1901e6f599349
carrier_bytes: 4126
prepare_summary_bytes: 1854
accepted_outcome: maintenance_outcome_20260811010955953_342985dc2d36
revision: temporal-revision:sha256:d3683e35415036c389e5a8b7a7b80ceda67fd2cb0efc2d10385520151ad67e3d

brain_result: clean at HEAD=776c1b68791fd5d844a11514c5f2f4d88ad5c26a; tree=cb1541c46ac7d1380b8be5a3e770d8ee3eef2202; source present at sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md (404 bytes)
brain_commit_paths: exact five paths in commit 776c1b6: index.md, log.md, projects/anvil-redux.md, sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md, wiki.html
brain_lint_render: passed in main-session evidence; not rerun by this operator because Brain mutation is prohibited

query_proof_before_fix: current, historical-after, and lineage contained the accepted revision as required; historical-before and not-yet-known excluded it as required; lineage omitted the immutable source reference
root_cause: llm-wiki render_temporal_revision omitted steward_evidence_refs from the lineage payload
fix_evidence: bounded Luna backend TDD fix is on disk in src/llm_wiki_core/temporal_persistence.py and tests/test_temporal_selection.py; focused test was RED before the fix and GREEN after it; llm-wiki remains intentionally dirty/untracked and was not committed
runtime_evidence: live MCP still omitted the source after the fix, proving the old process remained loaded; config is not hot-reloaded

current_workspace: /Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_35bf7392e7bacee68a9d44ae1541f7506f7311f17a119b8983c3df7945c76078
current_queue_delta: unchanged; observations v1 count=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c, v2 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855; outcomes v1 count=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68, v2 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
active_trace_workspace_delta: preserved trace workspace advanced from two to three v2 observations and outcomes; observations v2 count=3 hash=sha256:a0c846ed9b6fe7594b0330dc07b74fbe38323b85ee1b0ff82fcf3038f8711d67; outcomes v2 count=3 hash=sha256:072410a9f68026c262ab95e547dcd04a1ae472072791f2802f82177c1524a761; v1 counts remain zero
artifact_privacy_result: trace workspace contains only observations.jsonl and outcomes.jsonl; no payload_text/source_body keys, canary strings, child outcome, runner, or dispatch strings detected

operator_mutation_result: only this evidence append changed; config, Brain, Anvil/llm-wiki repositories, and state queues untouched; no temporal calls or Manifest use

RESTART REQUIRED

## WP-TR-A fresh active resume

timestamp_utc: 2026-08-11T01:27:56Z
stage: WP-TR-A extra fresh-active-host checkpoint
session: fresh-active-host-resume
scope: bounded operator evidence only; no temporal calls, Brain mutation, queue mutation, or Manifest use

config_path: /Users/brummerv/.codex/config.toml
mode_result: exactly one `ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A` entry at line 281; zero `shadow` or `disabled` alternatives

registration_result: live `llm-wiki` and `anvil` registrations remain enabled stdio with the prior commands, args, root/state keys, startup/tool timeouts, and approval shape; secrets omitted
brain_result: clean at HEAD=776c1b68791fd5d844a11514c5f2f4d88ad5c26a; tree=cb1541c46ac7d1380b8be5a3e770d8ee3eef2202
immutable_source_result: present at /Users/brummerv/.anvil-brain/codex/sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md; 404 bytes
renderer_fix_result: present on disk in /Users/brummerv/llm-wiki/src/llm_wiki_core/temporal_persistence.py and tests/test_temporal_selection.py, including the lineage steward-evidence assertion
llm_wiki_worktree_result: broader dirty/untracked temporal worktree remains present and unchanged; no unrelated cleanup or llm-wiki mutation by this checkpoint

active_identity_result: recorded correlation=wp-tr-a-20260811010713; record=maintenance_record_20260811010722656_2402aeb0bdff; outcome=maintenance_outcome_20260811010955953_342985dc2d36; revision=temporal-revision:sha256:d3683e35415036c389e5a8b7a7b80ceda67fd2cb0efc2d10385520151ad67e3d; all remain present in the preserved active trace
queue_result: baseline workspace remains observations v1 count=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c and outcomes v1 count=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68; v2 counts remain zero; active trace remains exactly three v2 observations and three v2 outcomes with prior hashes
artifact_result: active trace contains only observations.jsonl and outcomes.jsonl; no payload_text/source_body, canary, child outcome, runner, or dispatch artifacts detected

operator_mutation_result: only this evidence append changed; config, Brain, registrations, Anvil/llm-wiki repositories, and state queues untouched

READY FOR LINEAGE RETRY

## WP-TR-A active PASS

timestamp_utc: 2026-08-11T01:30:30Z
stage: WP-TR-A frozen active close
session: fresh-active-host
scope: five temporal query proofs and bounded preservation evidence; no Brain, llm-wiki, Anvil/state queue, or Manifest mutation

active_identity: correlation=wp-tr-a-20260811010713; record=maintenance_record_20260811010722656_2402aeb0bdff; accepted_outcome=maintenance_outcome_20260811010955953_342985dc2d36; revision=temporal-revision:sha256:d3683e35415036c389e5a8b7a7b80ceda67fd2cb0efc2d10385520151ad67e3d
query_proof_current: PASS; current includes the accepted revision and milestone
query_proof_historical_before: PASS; historical at 2026-08-10 excludes the accepted revision
query_proof_historical_after: PASS; historical at 2026-08-11 includes the accepted revision and page
query_proof_known_at_before: PASS; known_at=2026-08-11T01:07:52Z excludes the accepted revision
query_proof_lineage_fresh: PASS; query_at=2026-08-11T01:30:30Z includes the accepted revision, page, and immutable source

lineage_result: evidence_bytes=6348; envelope_bytes=3089; estimated_tokens=2360; items=2; stop=sufficient; diagnostic=LOCI_MCP_FAILED persisted, while exact seeded temporal evidence completed
measured_file_sizes: immutable source=404 bytes; temporal_persistence.py=28065 bytes; test_temporal_selection.py=11167 bytes; evidence file=17660 bytes before this append
brain_result: clean at HEAD=776c1b68791fd5d844a11514c5f2f4d88ad5c26a; tree=cb1541c46ac7d1380b8be5a3e770d8ee3eef2202; immutable source present
queue_result: baseline observations v1 count=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c and outcomes v1 count=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68 unchanged; v2 counts remain zero; active trace remains exactly 3 v2 observations and 3 v2 outcomes with hashes sha256:a0c846ed9b6fe7594b0330dc07b74fbe38323b85ee1b0ff82fcf3038f8711d67 and sha256:072410a9f68026c262ab95e547dcd04a1ae472072791f2802f82177c1524a761
artifact_privacy_result: trace contains only observations.jsonl and outcomes.jsonl; no child dispatch, runner, payload leakage, payload_text/source_body, or canary artifacts
registration_result: llm-wiki and anvil registrations unchanged, enabled stdio with prior command/args/root-state/timeout/approval shape; secrets omitted

active_close_result: WP-TR-A PASS; approved transition to disabled is next and requires a fresh host

## WP-TR-A disabled pre-restart checkpoint

timestamp_utc: 2026-08-11T01:31:56Z
stage: WP-TR-A frozen active close and disabled transition
session: current-host-pre-restart
scope: approved configuration transition only; no temporal calls, Brain mutation, queue mutation, llm-wiki/Anvil repository mutation, commit, or Manifest use

config_path: /Users/brummerv/.codex/config.toml
config_delta: exactly one line changed from `ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A` to `ANVIL_TEMPORAL_MAINTENANCE_MODE = "disabled" # WP-TR-A`
mode_result: exactly one disabled mode entry at line 281; zero active or shadow mode entries
registration_result: live `llm-wiki` and `anvil` registrations unchanged in enabled stdio command, args, root/state keys, startup/tool timeouts, and approval shape; secrets omitted
active_pass_result: five query proofs passed in the immediately preceding WP-TR-A active PASS section; active record/outcome/revision IDs remain recorded
brain_result: clean at HEAD=776c1b68791fd5d844a11514c5f2f4d88ad5c26a; tree=cb1541c46ac7d1380b8be5a3e770d8ee3eef2202; immutable source remains present at 404 bytes
queue_result: baseline and active trace counts/hashes remain as recorded; no v2 baseline delta, child dispatch, runner artifact, or payload leakage
mutation_boundary: only the approved config line and this evidence append changed; Brain, llm-wiki, Anvil/state queues, and Manifest untouched

RESTART REQUIRED

## WP-TR-A fresh disabled pre-closeout

timestamp_utc: 2026-08-11T01:36:00Z
stage: WP-TR-A fresh disabled host pre-closeout
session: fresh-disabled-host
scope: read-only preservation proof; no temporal calls, maintenance proposal tools, Brain mutation, llm-wiki mutation, Anvil/state queue mutation, commit, or Manifest use

config_result: exactly one `ANVIL_TEMPORAL_MAINTENANCE_MODE = "disabled" # WP-TR-A` entry at line 281; zero active or shadow mode entries; no active/shadow alternate
registration_result: `llm-wiki` and `anvil` remain enabled stdio registrations; llm-wiki command `/Users/brummerv/llm-wiki/.venv/bin/llm-wiki-mcp` with no args, anvil command `/Users/brummerv/phluxxed/anvil_redux/bin/anvil.ts` with args `mcp serve`, prior root/state keys, startup/tool timeouts, and approval shape preserved; secrets omitted
brain_result: clean at HEAD=776c1b68791fd5d844a11514c5f2f4d88ad5c26a; tree=cb1541c46ac7d1380b8be5a3e770d8ee3eef2202; immutable source `sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md` present, 404 bytes, sha256:2114371c2dd15f2730d61c1062483e6ad6a48b57de69cb4c77a1d3c29b0ed3bd
accepted_identity_result: correlation=wp-tr-a-20260811010713; record=maintenance_record_20260811010722656_2402aeb0bdff; accepted_outcome=maintenance_outcome_20260811010955953_342985dc2d36; revision=temporal-revision:sha256:d3683e35415036c389e5a8b7a7b80ceda67fd2cb0efc2d10385520151ad67e3d; trace source_ref=`sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md` preserved
llm_wiki_result: lineage renderer retains `steward_evidence_refs` and the focused `test_lineage_rendering_preserves_steward_evidence_refs` assertion; existing temporal worktree changes remain present and untouched

baseline_workspace: `/Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_35bf7392e7bacee68a9d44ae1541f7506f7311f17a119b8983c3df7945c76078`
baseline_observations: v1 count=3 bytes=5927 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c; v2 count=0 bytes=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
baseline_outcomes: v1 count=6 bytes=4287 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68; v2 count=0 bytes=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
trace_workspace: `/Users/brummerv/.anvil/state/maintenance-candidates/workspaces/sha256_efd0167900b00d21634eb81d1ed0cf8f8ad4282eba5de27b2383c4a83e107f29`; exact contents `observations.jsonl`, `outcomes.jsonl` only
trace_observations: v1 count=0 bytes=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855; v2 count=3 bytes=15236 hash=sha256:a0c846ed9b6fe7594b0330dc07b74fbe38323b85ee1b0ff82fcf3038f8711d67
trace_outcomes: v1 count=0 bytes=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855; v2 count=3 bytes=2670 hash=sha256:072410a9f68026c262ab95e547dcd04a1ae472072791f2802f82177c1524a761
trace_privacy_result: no child dispatch, runner artifact, payload leakage, `payload_text`, `source_body`, or canary artifact detected
mutation_boundary: only this evidence append changed; config, Brain, llm-wiki, Anvil/state queues, and Manifest untouched

READY FOR DISABLED CLOSEOUT PROBES

## WP-TR-A final disabled closeout PASS

timestamp_utc: 2026-08-11T02:07:09Z
stage: WP-TR-A disabled closeout
session: fresh-disabled-host
scope: bounded rollback acceptance proof only; mode remains disabled; no config/code/Brain/Anvil-state queue/Manifest mutation

disabled_v2_refusal: correlation=wp-tr-disabled-refusal-20260811020427; result=`VALIDATION_ERROR`; exact message=`temporal maintenance mode disabled`; refused attempt produced no v2 queue append
v1_compatibility: canonical proposal=`maintenance-observation:c48ca50b564a15ad`; dedupe=`maintenance-question:fde1039eb479f80d`; task=`wp-tr-disabled-v1-compatibility`; record=`maintenance_record_20260811020709256_02a4efc2d863`; exactly one v1 observation appended to the preserved trace; proposal kind=`durable_outcome`; contract_version=`1`; eligibility.mode=`first_observation`; disposition=`candidate_only`; mutation.allowed=`false`; commands=`[]`
v1_dispatch_result: no matching outcome for `maintenance_record_20260811020709256_02a4efc2d863`; no child dispatch or runner artifact; preserved v2 records did not dispatch

baseline_queue: observations v1 count=3 bytes=5927 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c; v2 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855; outcomes v1 count=6 bytes=4287 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68; v2 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
trace_queue: exact files=`observations.jsonl`, `outcomes.jsonl`; observations v1 count=1 bytes=1080 hash=sha256:bb93415e5e966afad79d1e0161ae80ff40485c10733cae028026021542c0a0e0; v2 count=3 bytes=15236 hash=sha256:a0c846ed9b6fe7594b0330dc07b74fbe38323b85ee1b0ff82fcf3038f8711d67; outcomes v1 count=0 hash=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855; v2 count=3 bytes=2670 hash=sha256:072410a9f68026c262ab95e547dcd04a1ae472072791f2802f82177c1524a761
privacy_result: no exact `payload_text`, `source_body`, or `canary` keys; trace contains no files beyond the two JSONL queues
host_result: exactly one disabled mode entry at `/Users/brummerv/.codex/config.toml:281`; zero active/shadow alternatives; `llm-wiki` and `anvil` registrations enabled and unchanged in command/args/root-state/timeout/approval shape; secrets omitted
brain_result: clean at HEAD=776c1b68791fd5d844a11514c5f2f4d88ad5c26a; tree=cb1541c46ac7d1380b8be5a3e770d8ee3eef2202; accepted source/revision preserved as recorded above
artifact_result: evidence file was 24803 bytes before this append and remains below the 65536-byte cap
closeout_boundary: disabled; stopped for Vik review; no rotation recommended

WP-TR-A PASS — READY FOR VIK DISABLED REVIEW

## WP-TR-A normal-active promotion checkpoint

timestamp_utc: 2026-08-11T02:26:01Z
stage: conditional promotion to normal active use
approval_basis: Vik conditionally approved promotion with “if you're happy with it then we can move into normal active use”; main-session judgment accepted the completed dogfood and disabled closeout as sufficient
scope: mechanical config promotion only; no active runtime behavior, restart, Brain, Anvil/llm-wiki code, queue, registration, dependency, or Manifest change

config_path: `/Users/brummerv/.codex/config.toml`
old_exact_line: `ANVIL_TEMPORAL_MAINTENANCE_MODE = "disabled" # WP-TR-A`
new_exact_line: `ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A`
post_change_counts: exactly one active mode occurrence at line 281; zero disabled or shadow mode occurrences
unchanged_neighbours: `[mcp_servers.anvil]` command `/Users/brummerv/phluxxed/anvil_redux/bin/anvil.ts`, args `["mcp", "serve"]`, startup timeout `10.0`, tool timeout `60.0`, approval `approve`; env `ANVIL_AGENT_ID="codex"` and `ANVIL_STATE_ROOT="/Users/brummerv/.anvil/state"` preserved
registration_result: existing llm-wiki and anvil registrations remain unchanged; no registration or secret changes
runtime_boundary: active behavior was not attempted in this pre-restart host; fresh-host verification is required after restart

RESTART REQUIRED

## WP-TR-A evidence correction: v1 closure timing

timestamp_utc: 2026-08-11T02:33:15Z
correction: the disabled closeout PASS at 02:07:09 recorded a valid point-in-time observation (the v1 record had no outcome yet), but its `v1_dispatch_result` wording was not a terminal non-dispatch guarantee. The deterministic v1 first-observation candidate was later closed at the next lifecycle boundary.
v1_outcome: record=`maintenance_record_20260811020709256_02a4efc2d863`; outcome=`maintenance_outcome_20260811021628332_5f1304a8044c`; status=`accepted`; observation_created_at=`2026-08-11T02:07:09.256Z`; outcome_created_at=`2026-08-11T02:16:28.332Z`; task=`wp-tr-disabled-v1-compatibility`; session=`019fe92c-57ff-75a1-b178-c9a8030611cd`
brain_update: commit=`a801eb56656b4ecfbfd575e8bdae302d85271615`; exact five paths were `index.md`, `log.md`, `projects/anvil-redux.md`, `sources/anvil-temporal-v2-disabled-v1-compatibility-2026-08-11.md`, and generated `wiki.html`. Semantics are limited to recording the disabled-v2/v1 first-observation invariant; the source and project guidance explicitly do not qualify enabled-v2 behavior or authorize broader rollout.
v2_boundary: no v2 temporal record participated in the v1 outcome; the outcome references only the named v1 proposal/observation, and preserved v2 records remained separate.
corrected_future_criterion: deterministic v1 first-observation probes must be explicitly closed with a terminal outcome, and the outcome hash must remain stable across the next lifecycle boundary before closeout is declared. Do not describe an empty point-in-time outcome queue as permanent non-dispatch.
normal_active_v2_smoke: pending after the fresh-host runtime proof; not failed and not attempted by this correction.

## WP-TR-A final normal-active runtime PASS

timestamp_utc: 2026-08-11T02:34:44Z
stage: fresh-host normal-active v2 smoke
smoke_correlation: requested=`wp-tr-normal-active-smoke-20260811023330`; record call succeeded idempotently and returned existing record=`maintenance_record_20260811010722656_2402aeb0bdff` with original correlation=`wp-tr-a-20260811010713`; dedupe=`maintenance-question:fde1039eb479f80d`; telemetry.mode=`active`; carrier_bytes=4126; duration_ms=18
idempotency_result: no observation or outcome append; canonical and trace counts/hashes match the pre-smoke baseline; no no_change outcome was recorded against the already accepted record
accepted_linkage: outcome=`maintenance_outcome_20260811010955953_342985dc2d36`; status=`accepted`; revision=`temporal-revision:sha256:d3683e35415036c389e5a8b7a7b80ceda67fd2cb0efc2d10385520151ad67e3d`; page=`projects/anvil-redux.md`; source=`sources/anvil-temporal-maintenance-first-brain-dogfood-2026-08-11.md`
queue_result: canonical observations v1=3 hash=sha256:67eefbdb6588b175644ee5f8b1b617b3dd5eeb60c78a8462aefd57c152860f4c and outcomes v1=6 hash=sha256:4ec1dea783190a91c3c9afbc9996cdf4863f0adef95c508310c073c4b2197b68 unchanged; trace observations v1=1 hash=sha256:bb93415e5e966afad79d1e0161ae80ff40485c10733cae028026021542c0a0e0 and v2=3 hash=sha256:a0c846ed9b6fe7594b0330dc07b74fbe38323b85ee1b0ff82fcf3038f8711d67 unchanged; trace outcomes v1=1 hash=sha256:c952a7f3fdde1bd4b57c2c0d169bbed7b6c8831fb55aaef420e4f7cf092ec70f and v2=3 hash=sha256:072410a9f68026c262ab95e547dcd04a1ae472072791f2802f82177c1524a761 unchanged
dispatch_privacy_result: no child dispatch or new runner artifact; trace remains exactly `observations.jsonl` and `outcomes.jsonl`; no exact `payload_text`, `source_body`, or `canary` keys
host_result: exactly one `ANVIL_TEMPORAL_MAINTENANCE_MODE = "active" # WP-TR-A` at line 281; zero disabled/shadow alternatives; llm-wiki and anvil registrations unchanged; Brain clean at HEAD=`a801eb56656b4ecfbfd575e8bdae302d85271615`, tree=`93e1283ab59eee6a4f2a0369ef48eb229cd493e5`; rollback remains one-line active-to-disabled config change
qualification: normal active use qualified; no further smoke or mutation by this operator
