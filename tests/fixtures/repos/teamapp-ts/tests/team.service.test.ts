import { describe, expect, it, vi } from 'vitest';
import { TeamService } from '../src/services/team.service';
import { TeamRepository } from '../src/repositories/team.repository';
import { UserRepository } from '../src/repositories/user.repository';

describe('TeamService', () => {
  it('adds the owner as the first member', async () => {
    const teams = { create: vi.fn(), addMember: vi.fn() } as unknown as TeamRepository;
    const users = {} as UserRepository;
    const id = await new TeamService(teams, users).createTeam('owner-1', 'Core');
    expect(teams.addMember).toHaveBeenCalledWith(id, 'owner-1', 'owner');
  });
});
