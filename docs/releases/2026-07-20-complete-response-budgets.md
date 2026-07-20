# Complete-response compiler budget correction

Date: 2026-07-20
Package: `llm-wiki 0.2.0`
Runtime contract: `2`

## Shipped capability

- `max_bytes` and optional `max_estimated_tokens` now bound the complete serialized compiler response;
- impossible budgets fail through the structured `BUDGET_TOO_SMALL` contract;
- detailed omission and diagnostic rows are capped at 16 each and compact further when required, while total and returned counts remain visible;
- atomic evidence is never excerpted during final response fitting; and
- Loci rank orders evidence without forcing redundant top-three results after coverage.

## Regression proof

- complete-response byte and estimated-token ceilings are exercised with hundreds of lower-value candidates;
- exact evidence, envelope, item, and token accounting remains tied to the final serialized response;
- atomic evidence is omitted rather than partially returned when envelope cost prevents it fitting; and
- retrieval-rank tests prove the best relevant Loci result remains while redundant lower-ranked hits are omitted.

The live Codex Brain acceptance query for current Manifest status returned the
single relevant status source without excerpting it. The complete response was
5,309 bytes and 1,328 estimated tokens against ceilings of 16,384 bytes and
4,096 estimated tokens. It reported 207 total omissions through 16 bounded
detail rows, returned no unrelated language-project evidence, and emitted no
diagnostic.

The complete local suite passed 363 tests with 2 skips. Python byte-compilation,
wheel and source-distribution builds, and whitespace validation also passed.
