import type { Request, Response } from 'express';
import { UserService } from '../services/user.service';
import { UserRepository } from '../repositories/user.repository';

/** HTTP adapter for the signed-in user's profile. */
export class UserController {
  constructor(
    private readonly users: UserService,
    private readonly userRepository: UserRepository,
  ) {}

  async me(req: Request, res: Response) {
    res.json(await this.users.getProfile(req.headers['x-user-id'] as string));
  }

  // Legacy shortcut: writes straight to the repository instead of going through UserService.
  async updateProfile(req: Request, res: Response) {
    await this.userRepository.update(req.headers['x-user-id'] as string, { email: req.body.email });
    res.status(204).end();
  }
}
