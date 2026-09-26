import { describe, expect, it, vi } from 'vitest';
import { TokenService } from '../src/services/token.service';
import { TokenRepository } from '../src/repositories/token.repository';

describe('TokenService', () => {
  it('creates a token and validates it for the same purpose', async () => {
    const repo = new TokenRepository();
    const stored: Record<string, any> = {};
    vi.spyOn(repo, 'insert').mockImplementation(async (t) => { stored[t.value] = t; });
    vi.spyOn(repo, 'findByValue').mockImplementation(async (v) => stored[v]);
    const service = new TokenService(repo);
    const value = await service.create('u1', 'password_reset', 60);
    await expect(service.validate(value, 'password_reset')).resolves.toMatchObject({ userId: 'u1' });
  });
});
