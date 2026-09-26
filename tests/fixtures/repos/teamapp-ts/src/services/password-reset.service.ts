import { TokenService } from './token.service';
import { EmailService } from './email.service';
import { UserRepository } from '../repositories/user.repository';

const PURPOSE = 'password_reset';

/** Password reset: a TokenService token emailed to the user, consumed on reset. */
export class PasswordResetService {
  constructor(
    private readonly tokens: TokenService,
    private readonly email: EmailService,
    private readonly users: UserRepository,
  ) {}

  async requestReset(emailAddress: string): Promise<void> {
    const user = await this.users.findByEmail(emailAddress);
    if (!user) return;
    const token = await this.tokens.create(user.id, PURPOSE, 60 * 60);
    await this.email.sendTemplate(user.email, 'password-reset', { link: `/reset?token=${token}` });
  }

  async resetPassword(token: string, passwordHash: string): Promise<void> {
    const record = await this.tokens.consume(token, PURPOSE);
    await this.users.update(record.userId, { passwordHash });
  }
}
