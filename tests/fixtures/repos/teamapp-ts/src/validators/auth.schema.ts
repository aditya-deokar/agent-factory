import { z } from 'zod';

export const registerSchema = z.object({ email: z.string().email(), password: z.string().min(12) });
export const requestResetSchema = z.object({ email: z.string().email() });
export const resetPasswordSchema = z.object({ token: z.string(), password: z.string().min(12) });
export const verifyEmailSchema = z.object({ token: z.string() });
