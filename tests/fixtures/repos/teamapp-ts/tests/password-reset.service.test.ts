import { describe, expect, it, vi } from 'vitest';
import { PasswordResetService } from '../src/services/password-reset.service';
import { TokenService } from '../src/services/token.service';
import { EmailService } from '../src/services/email.service';
import { UserRepository } from '../src/repositories/user.repository';

describe('PasswordResetService', () => {
  it('emails a reset link for a known user', async () => {
    const tokens = { create: vi.fn().mockResolvedValue('tok') } as unknown as TokenService;
    const email = { sendTemplate: vi.fn() } as unknown as EmailService;
    const users = { findByEmail: vi.fn().mockResolvedValue({ id: 'u1', email: 'a@b.c' }) } as unknown as UserRepository;
    await new PasswordResetService(tokens, email, users).requestReset('a@b.c');
    expect(email.sendTemplate).toHaveBeenCalledWith('a@b.c', 'password-reset', { link: '/reset?token=tok' });
  });
});
