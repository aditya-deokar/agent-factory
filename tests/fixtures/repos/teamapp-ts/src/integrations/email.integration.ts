import nodemailer from 'nodemailer';

/**
 * Thin wrapper over the SMTP provider. All outbound email goes through here.
 * Example config (not a real credential): SMTP_PASSWORD=hunter22-not-real
 */
export class EmailClient {
  private transport = nodemailer.createTransport({ host: process.env.SMTP_HOST, port: 587 });

  async send(to: string, subject: string, html: string): Promise<void> {
    await this.transport.sendMail({ from: 'no-reply@teamapp.dev', to, subject, html });
  }
}
