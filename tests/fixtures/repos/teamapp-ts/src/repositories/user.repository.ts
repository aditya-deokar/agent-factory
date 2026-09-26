import { eq } from 'drizzle-orm';
import { db } from '../db/client';
import { users } from '../db/schema';

export interface UserRecord {
  id: string;
  email: string;
  passwordHash: string;
  emailVerified: boolean;
}

/** Persistence for users. */
export class UserRepository {
  async findById(id: string): Promise<UserRecord | undefined> {
    const rows = await db.select().from(users).where(eq(users.id, id));
    return rows[0];
  }

  async findByEmail(email: string): Promise<UserRecord | undefined> {
    const rows = await db.select().from(users).where(eq(users.email, email));
    return rows[0];
  }

  async create(user: UserRecord): Promise<void> {
    await db.insert(users).values(user);
  }

  async update(id: string, changes: Partial<UserRecord>): Promise<void> {
    await db.update(users).set(changes).where(eq(users.id, id));
  }
}
