import Database from "better-sqlite3";
import { betterAuth } from "better-auth";
import nodemailer from "nodemailer";

const baseURL = process.env.BETTER_AUTH_URL || "https://aihelper.in";
const databaseFile = process.env.BETTER_AUTH_DATABASE || "./better-auth.sqlite";

if (!process.env.BETTER_AUTH_SECRET || process.env.BETTER_AUTH_SECRET.length < 32) {
  throw new Error("BETTER_AUTH_SECRET must be a unique random value of at least 32 characters");
}

const smtpUser = process.env.SMTP_USER || "verify.aihelper@gmail.com";
const smtpPass = process.env.SMTP_PASS;
const smtpHost = process.env.SMTP_HOST || "smtp.gmail.com";
const smtpPort = Number(process.env.SMTP_PORT || 465);
const smtpSecure = process.env.SMTP_SECURE !== "false";
const smtpFrom = process.env.SMTP_FROM || `AI Helper <${smtpUser}>`;

if (!smtpPass) {
  throw new Error("SMTP_PASS is required. For Gmail, create a Google App Password for verify.aihelper@gmail.com.");
}

const mailer = nodemailer.createTransport({
  host: smtpHost,
  port: smtpPort,
  secure: smtpSecure,
  auth: { user: smtpUser, pass: smtpPass },
});

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function transactionalEmail({ title, intro, action, url, expires }) {
  const safeUrl = escapeHtml(url);
  return `<!doctype html><html><body style="margin:0;background:#f4f5f7;color:#17181c;font-family:Arial,sans-serif"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td style="padding:32px 16px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:auto;background:#fff;border-radius:16px;overflow:hidden"><tr><td style="padding:28px 32px;background:#111216;color:#fff"><div style="font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:#8ce9e1">AI Helper</div><h1 style="margin:10px 0 0;font-size:27px;line-height:1.15">${escapeHtml(title)}</h1></td></tr><tr><td style="padding:32px"><p style="margin:0 0 22px;font-size:16px;line-height:1.55">${escapeHtml(intro)}</p><p style="margin:0 0 24px"><a href="${safeUrl}" style="display:inline-block;padding:13px 20px;border-radius:9px;background:#287dff;color:#fff;font-weight:700;text-decoration:none">${escapeHtml(action)}</a></p><p style="margin:0;color:#676b76;font-size:13px;line-height:1.5">${escapeHtml(expires)} If you did not request this, you can safely ignore this email.</p><p style="margin:24px 0 0;padding-top:20px;border-top:1px solid #e5e7eb;color:#676b76;font-size:12px;line-height:1.5">Button not working? Copy and paste this link into your browser:<br><a href="${safeUrl}" style="color:#1d5dd8;word-break:break-all">${safeUrl}</a></p></td></tr></table><p style="max-width:560px;margin:16px auto 0;color:#777b85;font-size:12px;text-align:center">AI Helper · Secure account email</p></td></tr></table></body></html>`;
}

async function sendEmail({ to, subject, text, html }) {
  await mailer.sendMail({
    from: smtpFrom,
    replyTo: smtpUser,
    to,
    subject,
    text,
    html,
    disableFileAccess: true,
    disableUrlAccess: true,
  });
}

export const auth = betterAuth({
  database: new Database(databaseFile),
  baseURL,
  basePath: "/auth",
  secret: process.env.BETTER_AUTH_SECRET,
  trustedOrigins: [baseURL],
  rateLimit: {
    enabled: true,
    window: 60,
    max: 100,
    customRules: {
      "/request-password-reset": { window: 3600, max: 3 },
      "/send-verification-email": { window: 3600, max: 3 },
    },
  },
  advanced: { ipAddress: { ipAddressHeaders: ["x-forwarded-for"] }, useSecureCookies: true },
  emailAndPassword: {
    enabled: true,
    // The HTTP sign-up endpoint is blocked in server.mjs. The owner dashboard
    // provisions accounts through a loopback-only bridge instead.
    disableSignUp: false,
    minPasswordLength: 12,
    maxPasswordLength: 128,
    requireEmailVerification: true,
    revokeSessionsOnPasswordReset: true,
    resetPasswordTokenExpiresIn: 3600,
    sendResetPassword: ({ user, url }) => sendEmail({
      to: user.email,
      subject: "Reset your AI Helper password",
      text: `Reset your AI Helper password within one hour: ${url}\n\nIf you do not see this email, please check your Spam or Junk folder.`,
      html: transactionalEmail({
        title: "Reset your password",
        intro: "We received a request to reset your AI Helper password.",
        action: "Reset password",
        url,
        expires: "This secure link expires in one hour. If you do not see this message, check your Spam or Junk folder.",
      }),
    }),
  },
  emailVerification: {
    sendOnSignUp: true,
    sendOnSignIn: true,
    autoSignInAfterVerification: true,
    sendVerificationEmail: ({ user, url }) => sendEmail({
      to: user.email,
      subject: "Verify your AI Helper email address",
      text: `Verify your email address: ${url}\n\nIf you do not see this email, please check your Spam or Junk folder.`,
      html: transactionalEmail({
        title: "Verify your email",
        intro: "Welcome to AI Helper. Confirm this email address to finish securing your account.",
        action: "Verify email address",
        url,
        expires: "This secure link expires automatically. If you do not see this message, check your Spam or Junk folder.",
      }),
    }),
  },
});
