# Cross-wiki acceptance

These cases freeze authored evidence spans before grading compiler output. They are deterministic and judge-free.

Run the live checks explicitly:

```bash
LLM_WIKI_ACCEPTANCE_AI_GRAPH=/path/to/ai_graph_ideas \
LLM_WIKI_ACCEPTANCE_BRAIN=/path/to/brain \
.venv/bin/python3 -m unittest tests.cross_wiki.test_acceptance -v
```

Absent roots are skipped so package contributors do not need either private wiki. The production release record must include a run with both roots set. Do not replace these span checks with an LLM judge score.
