import { pgTable, text, timestamp, boolean } from 'drizzle-orm/pg-core';

/** Registered users. */
export const users = pgTable('users', {
  id: text('id').primaryKey(),
  email: text('email').notNull().unique(),
  passwordHash: text('password_hash').notNull(),
  emailVerified: boolean('email_verified').default(false),
});

/** Teams own projects; users join through team_members. */
export const teams = pgTable('teams', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  ownerId: text('owner_id').notNull(),
});

export const teamMembers = pgTable('team_members', {
  teamId: text('team_id').notNull(),
  userId: text('user_id').notNull(),
  role: text('role').notNull(),
});

/** Single-use expiring tokens (password reset, email verification). See ADR-004. */
export const tokens = pgTable('tokens', {
  value: text('value').primaryKey(),
  userId: text('user_id').notNull(),
  purpose: text('purpose').notNull(),
  expiresAt: timestamp('expires_at').notNull(),
  consumedAt: timestamp('consumed_at'),
});
