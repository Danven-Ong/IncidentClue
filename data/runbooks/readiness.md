# Readiness and dependency initialization

## Symptoms

The process is alive but readiness remains false while initialization is incomplete.

## Mechanism

Liveness and readiness answer different questions. Failed dependency initialization may prevent readiness even when the process responds. A failed readiness check does not identify the dependency failure.

## Checks

Read readiness reason codes and startup logs. Correlate dependency initialization events with readiness transitions before proposing a cause.
