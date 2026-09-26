# Retrieval ablation

Fixture: `teamapp-ts` · 25 hand-written requests · recall@8 and MRR.
Embeddings: `hash-bow-v2` (offline lexical stand-in; re-run with real embeddings for semantic numbers)

| Retrieval | recall@8 | MRR |
|---|---|---|
| vector only | 0.813 | 0.592 |
| full-text only | 0.860 | 0.702 |
| all retrievers (no graph) | 0.910 | 0.873 |
| all retrievers + graph expansion | 0.913 | 0.948 |

**holdout split** (12 requests; never used for tuning)

| Retrieval | recall@8 | MRR |
|---|---|---|
| vector only | 0.833 | 0.598 |
| full-text only | 0.806 | 0.679 |
| all retrievers (no graph) | 0.833 | 0.792 |
| all retrievers + graph expansion | 0.819 | 0.892 |

**tune split** (13 requests; weights were tuned on these)

| Retrieval | recall@8 | MRR |
|---|---|---|
| vector only | 0.795 | 0.586 |
| full-text only | 0.910 | 0.724 |
| all retrievers (no graph) | 0.981 | 0.949 |
| all retrievers + graph expansion | 1.000 | 1.000 |

Misses with all retrievers + graph expansion:

- Send a welcome email after registration: missing UserService
- Rate limit password reset requests: missing authRoutes
- Return 404 when a team does not exist: missing NotFoundError
- Require authentication on the user profile routes: missing requireAuth
- Verify a user's email address: missing AuthController

## Reuse detection

precision 1.000 · recall 1.000 · accuracy 1.000

| Proposed | Expected | Top candidate | Score | Verdict | OK |
|---|---|---|---|---|---|
| InvitationTokenService | TokenService | TokenService | 0.77 | reuse | ✓ |
| MailerService | EmailService, EmailClient | EmailService | 0.768 | reuse | ✓ |
| TeamsRepo | TeamRepository | TeamRepository | 0.693 | extend | ✓ |
| VerificationCodeService | TokenService, EmailVerificationService | TokenService | 0.689 | extend | ✓ |
| UserAccountService | UserService | UserService | 0.805 | reuse | ✓ |
| SmtpClient | EmailClient, EmailService | EmailClient | 0.677 | extend | ✓ |
| PasswordRecoveryService | PasswordResetService | PasswordResetService | 0.769 | reuse | ✓ |
| MembershipService | TeamService, TeamRepository | TeamService | 0.718 | reuse | ✓ |
| TokenStore | TokenRepository, TokenService | TokenRepository | 0.708 | reuse | ✓ |
| EmailConfirmationService | EmailVerificationService | EmailVerificationService | 0.755 | reuse | ✓ |
| CsvExportService | (new) | TeamService | 0.411 | new_ok | ✓ |
| PaymentService | (new) | TokenService | 0.216 | new_ok | ✓ |
| ImageResizer | (new) | UserService | 0.106 | new_ok | ✓ |
| FeatureFlagService | (new) | TokenService | 0.191 | new_ok | ✓ |
| RateLimiter | (new) | PasswordResetService | 0.238 | new_ok | ✓ |
| SearchIndexer | (new) | TeamService | 0.212 | new_ok | ✓ |
| AnalyticsTracker | (new) | TokenService | 0.105 | new_ok | ✓ |
| PdfRenderer | (new) | EmailService | 0.159 | new_ok | ✓ |
| CacheService | (new) | UserRepository | 0.367 | new_ok | ✓ |
| WebhookDispatcher | (new) | EmailService | 0.35 | new_ok | ✓ |

_Reuse weights and name synonyms were adjusted while looking at all 20 reuse cases, so the reuse numbers are not a holdout result._
