# Brain Steward integration

The llm-wiki runtime never mutates Brain from retrieval or maintenance discovery. `wiki_compile_context`, doctor, `wiki_maintenance_candidates`, and `wiki_build_maintenance_candidate` are read-only MCP surfaces.

A maintenance packet is evidence for review, not an edit instruction or truth claim. The steward must read Brain's `wiki-agent.md`, inspect cited evidence and explicit unknowns, decide whether anything belongs in durable memory, then use Brain's normal page/index/log/source rules and lint/render checks. Empty deterministic candidates never waive semantic review, and incubator material is never promoted automatically.

A separate orchestrator may queue and automatically present eligible proposals to Brain Steward. That does not give the orchestrator or this runtime mutation authority: only the steward may accept a proposal and apply a change under Brain's current manual.

This preserves one governed mutation path while letting shared retrieval improvements reach Brain immediately when its canonical package is upgraded.
