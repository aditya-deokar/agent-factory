# Architecture

## Layers

| Layer | Folder | Responsibility |
|---|---|---|
| Routes | `src/routes` | HTTP paths, auth and validation middleware |
| Controllers | `src/controllers` | Translate HTTP to service calls |
| Services | `src/services` | Business rules |
| Repositories | `src/repositories` | All database access (Drizzle) |
| Integrations | `src/integrations` | External providers such as SMTP |

## Tokens

`TokenService` owns the token lifecycle: create, validate, consume, expire. `PasswordResetService`
and `EmailVerificationService` both reuse it with different purposes. Tokens are stored in Postgres
by `TokenRepository` (ADR-004).

## Email

`EmailService` renders templates and sends them through `EmailClient`.
