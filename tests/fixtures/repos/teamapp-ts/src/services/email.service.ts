import { EmailClient } from '../integrations/email.integration';

const TEMPLATES: Record<string, (data: Record<string, string>) => string> = {
  'password-reset': (d) => `<p>Reset your password: ${d.link}</p>`,
  'verify-email': (d) => `<p>Verify your email: ${d.link}</p>`,
};

/** Transactional email: renders a template and sends it through EmailClient. */
export class EmailService {
  constructor(private readonly client: EmailClient) {}

  async sendTemplate(to: string, template: string, data: Record<string, string>): Promise<void> {
    await this.client.send(to, template, TEMPLATES[template](data));
  }
}
