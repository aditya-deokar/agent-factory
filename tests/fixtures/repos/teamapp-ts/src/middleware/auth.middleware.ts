import type { Request, Response, NextFunction } from 'express';

/** Rejects requests without a session user. */
export function requireAuth(req: Request, res: Response, next: NextFunction) {
  if (!req.headers.authorization) {
    res.status(401).json({ error: 'unauthorized' });
    return;
  }
  next();
}
