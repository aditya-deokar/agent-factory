"""Build the `teamapp-ts` fixture repository with a deterministic git history.

The sources live in `repos/teamapp-ts/` without a .git directory. This script copies
them into `dest`, renames `env.fixture` -> `.env` (left untracked but NOT ignored, so
the auditor's secret guard is exercised) and `gitignore.fixture` -> `.gitignore`, then
replays 25 commits with a fixed author and fixed dates. "Touch" steps append a
revision comment so some files change together (co-change mining has signal).

    uv run python tests/fixtures/build_teamapp.py <dest>
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).parent / "repos" / "teamapp-ts"

# (message, files added, files touched)
HISTORY: list[tuple[str, list[str], list[str]]] = [
    ("chore: scaffold project", ["package.json", "tsconfig.json", ".gitignore", "README.md"], []),
    ("feat(db): schema and client", ["src/db/schema.ts", "src/db/client.ts"], []),
    ("feat(errors): AppError hierarchy", ["src/errors/app-error.ts", "src/middleware/error.middleware.ts"], []),
    ("feat(users): user repository", ["src/repositories/user.repository.ts"], []),
    ("feat(users): user service", ["src/services/user.service.ts"], []),
    (
        "feat(auth): register endpoint",
        [
            "src/controllers/auth.controller.ts",
            "src/routes/auth.routes.ts",
            "src/validators/auth.schema.ts",
            "src/middleware/validate.middleware.ts",
        ],
        [],
    ),
    (
        "feat(tokens): token repository and service",
        [
            "src/repositories/token.repository.ts",
            "src/services/token.service.ts",
            "docs/adr/ADR-003-redis-for-tokens.md",
        ],
        [],
    ),
    (
        "feat(email): email client and service",
        ["src/integrations/email.integration.ts", "src/services/email.service.ts"],
        [],
    ),
    (
        "feat(auth): password reset",
        ["src/services/password-reset.service.ts", "tests/password-reset.service.test.ts"],
        [],
    ),
    ("test: token service tests", ["tests/token.service.test.ts"], []),
    ("feat(auth): email verification", ["src/services/email-verification.service.ts"], []),
    (
        "docs: ADR-001 services own business logic",
        ["docs/adr/ADR-001-services-own-business-logic.md", "docs/architecture.md"],
        [],
    ),
    (
        "feat(teams): team repository and service",
        ["src/repositories/team.repository.ts", "src/services/team.service.ts"],
        [],
    ),
    (
        "feat(teams): team routes",
        [
            "src/controllers/team.controller.ts",
            "src/routes/teams.routes.ts",
            "src/middleware/auth.middleware.ts",
            "src/validators/team.schema.ts",
        ],
        [],
    ),
    ("feat(users): profile endpoints", ["src/controllers/user.controller.ts", "src/routes/users.routes.ts"], []),
    (
        "docs: ADR-002 no db access in controllers",
        ["docs/adr/ADR-002-no-db-access-in-controllers.md", "CONTRIBUTING.md"],
        [],
    ),
    (
        "refactor(tokens): move tokens to Postgres",
        ["docs/adr/ADR-004-postgres-for-tokens.md"],
        ["src/repositories/token.repository.ts", "src/services/token.service.ts"],
    ),
    (
        "fix(teams): owner becomes first member",
        ["tests/team.service.test.ts"],
        ["src/services/team.service.ts", "src/repositories/team.repository.ts"],
    ),
    (
        "fix(teams): check member exists",
        [],
        ["src/services/team.service.ts", "src/repositories/team.repository.ts", "src/controllers/team.controller.ts"],
    ),
    (
        "feat(teams): list members",
        [],
        [
            "src/services/team.service.ts",
            "src/repositories/team.repository.ts",
            "src/controllers/team.controller.ts",
            "src/routes/teams.routes.ts",
        ],
    ),
    ("feat: composition root", ["src/index.ts"], []),
    (
        "fix(auth): verification link",
        [],
        [
            "src/services/email-verification.service.ts",
            "src/services/password-reset.service.ts",
            "src/services/token.service.ts",
        ],
    ),
    ("chore(tokens): tune token ttl", [], ["src/services/token.service.ts", "src/services/password-reset.service.ts"]),
    (
        "fix(auth): reset flow consumes the token",
        [],
        ["src/services/password-reset.service.ts", "src/services/token.service.ts"],
    ),
    ("docs: readme architecture section", [], ["README.md"]),
]

AUTHOR = {
    "GIT_AUTHOR_NAME": "Dana Dev",
    "GIT_AUTHOR_EMAIL": "dana@teamapp.dev",
    "GIT_COMMITTER_NAME": "Dana Dev",
    "GIT_COMMITTER_EMAIL": "dana@teamapp.dev",
}
_RENAMES = {"env.fixture": ".env", "gitignore.fixture": ".gitignore"}


def _git(dest: Path, *args: str, date: str | None = None) -> None:
    env = {**os.environ, **AUTHOR}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", *args], cwd=dest, env=env, check=True, capture_output=True)


def _copy_sources(dest: Path) -> None:
    for src in sorted(SOURCE.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(SOURCE).as_posix()
        target = dest / _RENAMES.get(rel, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)


def build_teamapp(dest: Path, commits: int | None = None) -> Path:
    """Create the fixture repo at dest (must not exist or be empty). Returns dest."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{dest.name}-sources"
    if staging.exists():
        shutil.rmtree(staging)
    _copy_sources(staging)
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "config", "core.autocrlf", "false")
    _git(dest, "config", "commit.gpgsign", "false")
    for i, (message, added, touched) in enumerate(HISTORY[: commits or len(HISTORY)]):
        for rel in added:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staging / rel, target)
        for rel in touched:
            comment = "<!-- rev -->" if rel.endswith(".md") else "// rev"
            with (dest / rel).open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(f"{comment} {i + 1}\n")
        _git(dest, "add", "-A", "--", *added, *touched)
        _git(dest, "commit", "-q", "-m", message, date=f"2026-01-{i + 1:02d}T10:00:00+00:00")
    shutil.copyfile(staging / ".env", dest / ".env")  # untracked, not ignored
    shutil.rmtree(staging)
    return dest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_teamapp.py <dest>")
    print(build_teamapp(Path(sys.argv[1])))
