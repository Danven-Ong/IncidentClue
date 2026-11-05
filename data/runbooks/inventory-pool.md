# Inventory pool measurement exception

## Symptoms

Inventory reports many idle database connections while request latency is elevated.

## Mechanism

Inventory exposes total open connections separately from checked-out connections. Idle connections alone do not establish pool exhaustion; acquisition wait and checked-out count are necessary discriminators.

## Checks

Read checked-out count, configured limit and acquisition wait. Compare these with query durations before attributing latency to the pool.
