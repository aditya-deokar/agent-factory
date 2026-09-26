import type { Request, Response, NextFunction } from 'express';
import type { ZodSchema } from 'zod';
import { ValidationError } from '../errors/app-error';

/** Validates req.body against a Zod schema before the handler runs. */
export function validate(schema: ZodSchema) {
  return (req: Request, res: Response, next: NextFunction) => {
    const result = schema.safeParse(req.body);
    if (!result.success) {
      next(new ValidationError(result.error.message));
      return;
    }
    req.body = result.data;
    next();
  };
}
