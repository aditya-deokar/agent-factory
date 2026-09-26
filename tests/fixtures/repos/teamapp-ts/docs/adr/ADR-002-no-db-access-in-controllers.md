# ADR-002: No database access from controllers

## Status

Accepted

## Context

A controller that queried the database directly skipped a business rule enforced in the service.

## Decision

Controllers must not use repositories or the database client directly. All data access goes
through a service, and only repositories access the database.

## Consequences

Every data path passes through the service layer, so rules are enforced in one place.
