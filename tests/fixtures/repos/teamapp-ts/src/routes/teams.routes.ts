import { Router } from 'express';
import { TeamController } from '../controllers/team.controller';
import { validate } from '../middleware/validate.middleware';
import { requireAuth } from '../middleware/auth.middleware';
import { addMemberSchema, createTeamSchema } from '../validators/team.schema';

export function teamRoutes(controller: TeamController): Router {
  const router = Router();
  router.post('/teams', requireAuth, validate(createTeamSchema), (req, res) => controller.create(req, res));
  router.get('/teams/:id', requireAuth, (req, res) => controller.get(req, res));
  router.post('/teams/:id/members', requireAuth, validate(addMemberSchema), (req, res) => controller.addMember(req, res));
  router.get('/teams/:id/members', requireAuth, (req, res) => controller.listMembers(req, res));
  return router;
}
