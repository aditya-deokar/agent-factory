import { eq } from 'drizzle-orm';
import { db } from '../db/client';
import { teams, teamMembers } from '../db/schema';

/** Persistence for teams and their members. */
export class TeamRepository {
  async create(team: { id: string; name: string; ownerId: string }): Promise<void> {
    await db.insert(teams).values(team);
  }

  async findById(id: string) {
    const rows = await db.select().from(teams).where(eq(teams.id, id));
    return rows[0];
  }

  async addMember(teamId: string, userId: string, role: string): Promise<void> {
    await db.insert(teamMembers).values({ teamId, userId, role });
  }

  async listMembers(teamId: string) {
    return db.select().from(teamMembers).where(eq(teamMembers.teamId, teamId));
  }
}
