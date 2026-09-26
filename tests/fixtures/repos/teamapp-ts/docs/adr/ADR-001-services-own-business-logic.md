# ADR-001: Services own business logic

## Status

Accepted

## Context

Early handlers mixed HTTP parsing, validation and business rules, which made the rules hard to reuse
from background jobs and hard to test.

## Decision

Business logic belongs in services. Controllers only translate HTTP requests into service calls and
service results into responses.

## Consequences

Controllers stay thin. Services can be reused and unit-tested without HTTP.
