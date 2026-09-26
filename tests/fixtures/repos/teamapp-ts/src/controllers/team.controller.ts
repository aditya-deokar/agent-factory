import type { Request, Response } from 'express';
import { TeamService } from '../services/team.service';

/** HTTP adapter for teams. */
export class TeamController {
  constructor(private readonly teams: TeamService) {}

  async create(req: Request, res: Response) {
    const id = await this.teams.createTeam(req.headers['x-user-id'] as string, req.body.name);
    res.status(201).json({ id });
  }

  async get(req: Request, res: Response) {
    res.json(await this.teams.getTeam(req.params.id));
  }

  async addMember(req: Request, res: Response) {
    await this.teams.addMember(req.params.id, req.body.userId, req.body.role);
    res.status(204).end();
  }

  async listMembers(req: Request, res: Response) {
    res.json(await this.teams.listMembers(req.params.id));
  }
}
