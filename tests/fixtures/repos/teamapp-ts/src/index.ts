import express from 'express';
import { UserRepository } from './repositories/user.repository';
import { TeamRepository } from './repositories/team.repository';
import { TokenRepository } from './repositories/token.repository';
import { TokenService } from './services/token.service';
import { EmailService } from './services/email.service';
import { EmailClient } from './integrations/email.integration';
import { UserService } from './services/user.service';
import { PasswordResetService } from './services/password-reset.service';
import { EmailVerificationService } from './services/email-verification.service';
import { TeamService } from './services/team.service';
import { AuthController } from './controllers/auth.controller';
import { TeamController } from './controllers/team.controller';
import { UserController } from './controllers/user.controller';
import { authRoutes } from './routes/auth.routes';
import { teamRoutes } from './routes/teams.routes';
import { userRoutes } from './routes/users.routes';
import { errorMiddleware } from './middleware/error.middleware';

// Composition root: the only place that wires concrete classes together.
const userRepository = new UserRepository();
const tokens = new TokenService(new TokenRepository());
const email = new EmailService(new EmailClient());
const users = new UserService(userRepository);
const passwordReset = new PasswordResetService(tokens, email, userRepository);
const verification = new EmailVerificationService(tokens, email, userRepository);
const teams = new TeamService(new TeamRepository(), userRepository);

const app = express();
app.use(express.json());
app.use(authRoutes(new AuthController(users, passwordReset, verification)));
app.use(teamRoutes(new TeamController(teams)));
app.use(userRoutes(new UserController(users, userRepository)));
app.use(errorMiddleware);

app.listen(3000);
