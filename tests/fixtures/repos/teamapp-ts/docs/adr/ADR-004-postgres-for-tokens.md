# ADR-004: Store tokens in Postgres

## Status

Accepted

Supersedes ADR-003.

## Context

Redis was only used for tokens, and running it cost more than it saved. Token volume is low.

## Decision

Store single-use tokens in the Postgres `tokens` table through `TokenRepository`, and reuse
`TokenService` for every expiring-token flow instead of adding new token stores.

## Consequences

One less service to operate. Expired tokens are deleted by `TokenService.expire`.
