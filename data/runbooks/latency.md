# Slow dependency responses

## Symptoms

A connection succeeds, but downstream response time increases and requests miss deadlines.

## Mechanism

Dependency processing latency can consume an upstream deadline. Separate downstream duration from local queue time and connection acquisition. Correlation does not identify the downstream internal cause.

## Checks

Inspect phase-level request timings and downstream latency observations for the same interval. Compare successful and timed-out requests; record missing downstream evidence.
