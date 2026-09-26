import type { Request, Response, NextFunction } from 'express';
import { AppError } from '../errors/app-error';

/** Maps AppError subclasses to HTTP responses. Registered last in index.ts. */
export function errorMiddleware(err: Error, req: Request, res: Response, next: NextFunction) {
  if (err instanceof AppError) {
    res.status(err.status).json({ error: err.message });
    return;
  }
  res.status(500).json({ error: 'internal error' });
}
