# Catalog database readiness checks

## Symptoms

Catalog readiness reports database unavailable after warm-up completes.

## Mechanism

Cache warm-up and database connectivity are independent readiness conditions. Completion of warm-up does not prove database access. The normal warm-up exception cannot explain an explicit database error.

## Checks

Read the readiness reason and matching database connection error. Separate name resolution, handshake failures and schema rejection using the recorded error categories.
