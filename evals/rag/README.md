# RAG evaluation

IncidentClue evaluates runbook retrieval using 24 synthetic incident cases,
split evenly between development and held-out evaluation. Cases cover
answerable incidents, conflicting observations, insufficient evidence and
situations where no runbook guidance should be returned.

## Measured results

The 12 held-out cases include:

- 6 answerable incidents.
- 4 incidents with ambiguous or insufficient evidence.
- 2 incidents where no runbook guidance should be returned.

| Measure | Result |
| --- | --- |
| Answerable cases receiving relevant guidance | 6 / 6 |
| No-guidance cases receiving unwanted guidance | 1 / 2 |
| Service and document-version filtering | Passed across all 12 held-out cases |
| Mean reciprocal rank across 10 cases with relevance labels | 0.833 |
| Keyword-search baseline on the same 10 cases | 0.850 |

Retrieval found relevant guidance for all six answerable incidents, with no
service-scope or document-version violations across the 12 held-out cases.
The evaluation also identified two areas for improvement: withholding guidance
when none applies and ranking relevant passages ahead of the keyword-search
baseline. That experiment did not meet all retrieval quality targets.

These metrics assess runbook retrieval, not diagnosis correctness.

A separate diagnosis comparison tested six development cases with and without
RAG. Of 12 attempted runs, nine returned accepted outputs and three encountered
execution failures. Unsupported causal claims remained, so improved diagnosis
quality has not been established.

## Scope and limitations

Results use a small, assistant-curated synthetic dataset and an experimental
retrieval policy that differs from the current application. They do not establish
production accuracy or improved diagnosis quality.
