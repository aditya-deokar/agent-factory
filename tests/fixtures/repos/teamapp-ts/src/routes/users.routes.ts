import { Router } from 'express';
import { UserController } from '../controllers/user.controller';
import { requireAuth } from '../middleware/auth.middleware';

export function userRoutes(controller: UserController): Router {
  const router = Router();
  router.get('/me', requireAuth, (req, res) => controller.me(req, res));
  // No schema here yet: the body goes to the repository unvalidated.
  router.patch('/me', requireAuth, (req, res) => controller.updateProfile(req, res));
  return router;
}
