import { Router } from 'express';
import { AuthController } from '../controllers/auth.controller';
import { validate } from '../middleware/validate.middleware';
import { registerSchema, requestResetSchema, resetPasswordSchema, verifyEmailSchema } from '../validators/auth.schema';

export function authRoutes(controller: AuthController): Router {
  const router = Router();
  router.post('/auth/register', validate(registerSchema), (req, res) => controller.register(req, res));
  router.post('/auth/password-reset', validate(requestResetSchema), (req, res) => controller.requestPasswordReset(req, res));
  router.post('/auth/password-reset/confirm', validate(resetPasswordSchema), (req, res) => controller.resetPassword(req, res));
  router.post('/auth/verify-email', validate(verifyEmailSchema), (req, res) => controller.verifyEmail(req, res));
  return router;
}
