# Dependency name resolution

## Symptoms

Dependency requests fail with name-resolution errors before a connection is established.

## Mechanism

A resolver failure prevents mapping a hostname to an address. It differs from connection refusal and from a response timeout after connection. A generic timeout does not prove DNS failure.

## Checks

Inspect the exact error category, configured hostname and resolver observations from the affected environment. Check for a hostname mismatch without changing configuration.
