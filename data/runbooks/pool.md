# Database connection-pool exhaustion

## Symptoms

Requests wait for a database connection while the pool remains at its configured limit.

## Mechanism

Occupied pool connections can force new requests to queue. Long transactions or unreleased connections are possible mechanisms, not established causes from a full pool alone.

## Checks

Compare active connections, pool limit and acquisition wait time. Inspect transaction ages separately from query duration; timeouts alone do not establish pool exhaustion.
