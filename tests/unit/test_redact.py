import pytest

from agent_factory.common.redact import contains_secret, is_secret_file, redact, shannon_entropy

# Fake credentials in the shapes real ones take. Never real values.
SECRETS = [
    ("aws_access_key", "key = AKIAIOSFODNN7EXAMPLE in config"),
    ("openai_key", "OPENAI key sk-proj-abcdefghijklmnopqrstuvwx1234"),
    ("graphacademy_key", "token ga-0123456789abcdefABCDEF"),
    ("github_token", "ghp_0123456789abcdefghijklmnopqrstuvwxyz12"),
    ("slack_token", "xoxb-1234567890-abcdefghij"),
    ("jwt", "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ("url_credentials", "postgres://admin:s3cr3tPass@db.internal:5432/app"),
    ("env_assignment", "NEO4J_PASSWORD=hunter22secret"),
    ("quoted_assignment", 'const config = { password: "correct-horse-battery" }'),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"),
    ("high_entropy", "blob Zx9qL2mN8vB4cX7kP3wR6tY1uI5oA0sDfGhJ"),
]


@pytest.mark.parametrize("kind,text", SECRETS)
def test_redact_detects_each_pattern(kind, text):
    result = redact(text)
    assert not result.clean
    assert kind in {f.kind for f in result.findings}
    assert f"[REDACTED:{kind}]" in result.text or "[REDACTED:" in result.text


def test_redact_keeps_key_names_for_assignments():
    out = redact('password: "correct-horse-battery"').text
    assert out.startswith("password:")
    assert "correct-horse-battery" not in out


def test_url_credentials_keep_host():
    out = redact("mongodb://user:pa55word@cluster0.example.net/db").text
    assert "cluster0.example.net" in out
    assert "pa55word" not in out


@pytest.mark.parametrize(
    "text",
    [
        'NEO4J_PASSWORD="..."',
        "OPENAI_API_KEY=<your key>",
        "API_KEY=${API_KEY}",
        "password: string;",
        "const password = req.body.password;",
        "SECRET_KEY=changeme",
    ],
)
def test_placeholders_and_code_are_not_secrets(text):
    assert redact(text).clean, redact(text).findings


# Ordinary code must survive untouched: a false positive would corrupt symbol cards.
CODE_CORPUS = """\
import { TokenService } from './services/token.service';
export class PasswordResetService extends BaseService implements Resettable {
  constructor(private readonly tokens: TokenService, private readonly emailService: EmailService) {}
  async requestPasswordReset(emailAddress: string): Promise<void> {
    const token = await this.tokens.create(user.id, 'password_reset', 60 * 60);
    const verificationTokenRepositoryImplementation = createRepository();
    return this.emailService.sendTemplate('password-reset', { token: token.value });
  }
}
def create_access_token_for_user(user_id: int, expires_in_seconds: int = 3600) -> str:
    sha = "3f2a91c6d4b8e0f7a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4"
    return jwt_encode({"sub": user_id, "exp": now() + expires_in_seconds})
const router = express.Router(); router.post('/teams/:teamId/invitations', validate(inviteSchema), handler);
export const useTeamInvitations = () => useQuery(['team', teamId, 'invitations'], fetchInvitations);
CREATE VECTOR INDEX af_symbol_embedding IF NOT EXISTS FOR (n:Symbol) ON n.embedding;
https://github.com/aditya-deokar/agent-factory/blob/main/src/agent_factory/common/redact.py
"""


def test_normal_code_has_no_false_positives():
    for line in CODE_CORPUS.splitlines():
        assert redact(line).clean, (line, redact(line).findings)


@pytest.mark.parametrize("kind,text", SECRETS)
def test_redaction_is_idempotent(kind, text):
    once = redact(text).text
    assert redact(once).clean, redact(once).findings


def test_contains_secret():
    assert contains_secret("AKIAIOSFODNN7EXAMPLE")
    assert not contains_secret("")
    assert not contains_secret("hello world")


def test_entropy_bounds():
    assert shannon_entropy("aaaa") == 0
    assert shannon_entropy("0123456789abcdef" * 4) == pytest.approx(4.0)


@pytest.mark.parametrize(
    "path,expected",
    [
        (".env", True),
        (".env.local", True),
        ("config/.env.production", True),
        ("certs/server.pem", True),
        ("keys/id_rsa", True),
        ("id_ed25519.pub", True),
        ("gcp/credentials-prod.json", True),
        ("service-account.json", True),
        ("infra/terraform.tfstate", True),
        (".npmrc", True),
        ("config\\secrets.yaml", True),
        ("src/env.ts", False),
        ("src/services/token.service.ts", False),
        ("docs/keys.md", False),
        ("environment.py", False),
    ],
)
def test_is_secret_file(path, expected):
    assert is_secret_file(path) is expected
