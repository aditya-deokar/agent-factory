import { z } from 'zod';

export const createTeamSchema = z.object({ name: z.string().min(2) });
export const addMemberSchema = z.object({ userId: z.string(), role: z.enum(['owner', 'member']) });
