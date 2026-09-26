# ADR-003: Store tokens in Redis

## Status

Superseded by ADR-004

## Context

Password reset tokens are short-lived and looked up by value.

## Decision

Store tokens in Redis with a TTL.

## Consequences

An extra piece of infrastructure to run.
