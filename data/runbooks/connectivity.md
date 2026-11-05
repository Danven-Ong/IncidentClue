# Dependency connection establishment

## Symptoms

Connection attempts are refused or time out before a successful handshake.

## Mechanism

A refusal indicates a connection was rejected; a connect timeout has several possible network or endpoint causes. Neither proves application processing is slow.

## Checks

Compare connection-phase errors, endpoint/port metadata and listener health observations. Distinguish connect time from request processing time.
