import { TokenService } from './token.service';
import { EmailService } from './email.service';
import { UserRepository } from '../repositories/user.repository';

const PURPOSE = 'email_verification';

/** Email verification: same token lifecycle as password reset, different purpose. */
export class EmailVerificationService {
  constructor(
    private readonly tokens: TokenService,
    private readonly email: EmailService,
    private readonly users: UserRepository,
  ) {}

  async sendVerification(userId: string, emailAddress: string): Promise<void> {
    const token = await this.tokens.create(userId, PURPOSE, 24 * 60 * 60);
    await this.email.sendTemplate(emailAddress, 'verify-email', { link: `/verify?token=${token}` });
  }

  async verify(token: string): Promise<void> {
    const record = await this.tokens.consume(token, PURPOSE);
    await this.users.update(record.userId, { emailVerified: true });
  }
}
