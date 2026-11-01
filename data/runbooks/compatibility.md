# Application and database compatibility

## Symptoms

Startup reports an unsupported database schema or an absent column after a release.

## Mechanism

Application expectations can differ from the installed database schema. Proximity to a release is suggestive; explicit version or missing-column evidence is stronger.

## Checks

Compare the running application version, supported schema range and recorded migration state using read-only metadata. Do not run migrations as a diagnostic action.
