import { randomUUID } from 'node:crypto';
import { UserRepository } from '../repositories/user.repository';
import { NotFoundError } from '../errors/app-error';

/** Business rules for user accounts. */
export class UserService {
  constructor(private readonly users: UserRepository) {}

  async register(email: string, passwordHash: string): Promise<string> {
    const id = randomUUID();
    await this.users.create({ id, email, passwordHash, emailVerified: false });
    return id;
  }

  async getProfile(id: string) {
    const user = await this.users.findById(id);
    if (!user) throw new NotFoundError('user');
    return { id: user.id, email: user.email, emailVerified: user.emailVerified };
  }
}
