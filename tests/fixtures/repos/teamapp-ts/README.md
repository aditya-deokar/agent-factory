# TeamApp

A small team-management API: accounts, password reset, email verification, and teams.

## Architecture

Requests flow `Route → Controller → Service → Repository → Postgres`. Controllers stay thin;
business rules live in services (see [ADR-001](docs/adr/ADR-001-services-own-business-logic.md)).
Single-use tokens for password reset and email verification are issued by `TokenService`, and
all outbound email goes through `EmailService`.

## Development

```bash
npm install
npm test
```
