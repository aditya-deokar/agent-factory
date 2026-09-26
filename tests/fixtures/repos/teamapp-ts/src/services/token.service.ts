import { randomBytes } from 'node:crypto';
import { TokenRepository, type TokenRecord } from '../repositories/token.repository';
import { ExpiredTokenError } from '../errors/app-error';

/**
 * Issues and validates single-use expiring tokens.
 * Lifecycle: create -> validate -> consume -> expire. Reused by password reset and email verification.
 */
export class TokenService {
  constructor(private readonly tokens: TokenRepository) {}

  /** Create a token for a user and purpose that expires after ttlSeconds. */
  async create(userId: string, purpose: string, ttlSeconds: number): Promise<string> {
    const value = randomBytes(24).toString('hex');
    await this.tokens.insert({ value, userId, purpose, expiresAt: new Date(Date.now() + ttlSeconds * 1000), consumedAt: null });
    return value;
  }

  /** Return the token if it exists, matches the purpose, is unexpired and unused. */
  async validate(value: string, purpose: string): Promise<TokenRecord> {
    const token = await this.tokens.findByValue(value, purpose);
    if (!token || token.consumedAt || token.expiresAt < new Date()) {
      throw new ExpiredTokenError();
    }
    return token;
  }

  /** Validate and mark the token used, in one step. */
  async consume(value: string, purpose: string): Promise<TokenRecord> {
    const token = await this.validate(value, purpose);
    await this.tokens.markConsumed(value);
    return token;
  }

  /** Delete tokens that expired before the given date. */
  async expire(before: Date = new Date()): Promise<void> {
    await this.tokens.deleteExpired(before);
  }
}
