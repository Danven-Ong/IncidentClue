# Catalog warm-up readiness

## Symptoms

Catalog remains unready during its documented cache warm-up interval.

## Mechanism

Catalog version 3 keeps readiness false until cache warm-up completes. Within the documented 90-second window this can be expected; beyond it, further evidence is needed. This exception does not apply to other services.

## Checks

Inspect elapsed warm-up time, progress events and the running catalog version. A healthy process alone does not prove that warm-up has completed.
