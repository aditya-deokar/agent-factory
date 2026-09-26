import { drizzle } from 'drizzle-orm/node-postgres';
import pg from 'pg';

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

/** The one database handle. Only repositories may import it (ADR-002). */
export const db = drizzle(pool);
