# Required configuration

## Symptoms

Startup validation reports an absent required setting before serving requests.

## Mechanism

A required setting missing from the effective process environment can stop initialization. A deployment timestamp alone does not establish this condition.

## Checks

Inspect the named validation error and effective configuration metadata. Compare required setting names with the deployed specification; do not print secret values.
