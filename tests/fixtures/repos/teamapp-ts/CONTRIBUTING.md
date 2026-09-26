# Contributing

- Never call repositories from controllers. Controllers talk to services only.
- All route input must be validated with a Zod schema via the `validate` middleware.
- Do not introduce another validation library; we use Zod everywhere.
- New tests go in `tests/` and use Vitest.
- Keep pull requests focused on one change.
