import type { Request, Response } from 'express';
import { UserService } from '../services/user.service';
import { PasswordResetService } from '../services/password-reset.service';
import { EmailVerificationService } from '../services/email-verification.service';

/** HTTP adapter for authentication flows. Thin: all rules live in services (ADR-001). */
export class AuthController {
  constructor(
    private readonly users: UserService,
    private readonly passwordReset: PasswordResetService,
    private readonly verification: EmailVerificationService,
  ) {}

  async register(req: Request, res: Response) {
    const id = await this.users.register(req.body.email, req.body.password);
    await this.verification.sendVerification(id, req.body.email);
    res.status(201).json({ id });
  }

  async requestPasswordReset(req: Request, res: Response) {
    await this.passwordReset.requestReset(req.body.email);
    res.status(202).end();
  }

  async resetPassword(req: Request, res: Response) {
    await this.passwordReset.resetPassword(req.body.token, req.body.password);
    res.status(204).end();
  }

  async verifyEmail(req: Request, res: Response) {
    await this.verification.verify(req.body.token);
    res.status(204).end();
  }
}
