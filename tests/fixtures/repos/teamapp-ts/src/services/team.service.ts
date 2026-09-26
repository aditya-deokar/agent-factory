import { randomUUID } from 'node:crypto';
import { TeamRepository } from '../repositories/team.repository';
import { UserRepository } from '../repositories/user.repository';
import { NotFoundError } from '../errors/app-error';

/** Business rules for teams and membership. */
export class TeamService {
  constructor(
    private readonly teams: TeamRepository,
    private readonly users: UserRepository,
  ) {}

  async createTeam(ownerId: string, name: string): Promise<string> {
    const id = randomUUID();
    await this.teams.create({ id, name, ownerId });
    await this.teams.addMember(id, ownerId, 'owner');
    return id;
  }

  async getTeam(id: string) {
    const team = await this.teams.findById(id);
    if (!team) throw new NotFoundError('team');
    return team;
  }

  async addMember(teamId: string, userId: string, role: string): Promise<void> {
    const user = await this.users.findById(userId);
    if (!user) throw new NotFoundError('user');
    await this.teams.addMember(teamId, userId, role);
  }

  async listMembers(teamId: string) {
    return this.teams.listMembers(teamId);
  }
}
