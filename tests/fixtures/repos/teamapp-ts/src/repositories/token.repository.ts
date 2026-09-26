import { and, eq, lt } from 'drizzle-orm';
import { db } from '@/db/client';
import { tokens } from '@/db/schema';

export interface TokenRecord {
  value: string;
  userId: string;
  purpose: string;
  expiresAt: Date;
  consumedAt: Date | null;
}

/** Persistence for single-use tokens (ADR-004: Postgres, not Redis). */
export class TokenRepository {
  async insert(token: TokenRecord): Promise<void> {
    await db.insert(tokens).values(token);
  }

  async findByValue(value: string, purpose: string): Promise<TokenRecord | undefined> {
    const rows = await db.select().from(tokens).where(and(eq(tokens.value, value), eq(tokens.purpose, purpose)));
    return rows[0];
  }

  async markConsumed(value: string): Promise<void> {
    await db.update(tokens).set({ consumedAt: new Date() }).where(eq(tokens.value, value));
  }

  async deleteExpired(before: Date): Promise<void> {
    await db.delete(tokens).where(lt(tokens.expiresAt, before));
  }
}
