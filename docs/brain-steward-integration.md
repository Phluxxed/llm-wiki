# Brain Steward integration

The runtime reads Brain; it does not maintain Brain autonomously. `wiki_compile_context`, doctor, and `wiki_maintenance_candidates` are read-only MCP surfaces.

A maintenance packet is evidence for review, not an edit instruction or truth claim. The steward must read Brain's `wiki-agent.md`, inspect cited evidence and explicit unknowns, decide whether anything belongs in durable memory, then use Brain's normal page/index/log/source rules and lint/render checks. Empty deterministic candidates never waive semantic review, and incubator material is never promoted automatically.

This preserves one governed mutation path while letting shared retrieval improvements reach Brain immediately when its canonical package is upgraded.
