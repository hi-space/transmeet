# Invite-only accounts and password management

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Only administrators create accounts; users set, change, and recover their own passwords.

**Architecture:** Keep the existing Cognito user pool and browser SDK. Disable public registration in Cognito, retain the first-login password challenge, add authenticated account settings, and add an email-code recovery flow to the login screen. Reuse existing email verification without coupling account recovery to SES meeting notifications.

**Tech Stack:** Next.js 14, React 18, TypeScript, amazon-cognito-identity-js, AWS CDK, Node test runner, Python Playwright.

**Spec:** User-approved conversation on 2026-10-29: administrator invitations through the AWS console; first-login password setup; settings for changing a known password; email-code recovery for forgotten passwords; implement, commit, push, and deploy.

## Global Constraints

- Preserve existing accounts, meetings, in-progress sessions, and the user's unrelated working tree changes.
- Do not add public registration, an administrator dashboard, password storage, or new AWS services.
- Use the existing visual components and Korean interface copy.
- Preserve the current minimum password length of 8; validate confirmation locally and let Cognito enforce its policy.
- Keep passwords in component memory only. Do not log them, persist them, or trim them.
- Set account recovery to verified email only; expose account email verification independently of meeting-email delivery.
- Use neutral recovery-request messaging to avoid disclosing whether an account exists.
- Preserve the active session after an authenticated password change so a live meeting is not interrupted.
- The user has authorized implementation, commit, push to the existing repository, and production deployment.
- Follow-up: Cognito must automatically generate temporary passwords. After deployment, invite the one email address explicitly supplied by the user; check for an existing account first and never reset it implicitly.

## Review Focus

- An uninvited visitor must be blocked by Cognito itself, even when directly calling SignUp.
- Forgotten-password recovery must never mark the visitor authenticated or create a user.
- Wrong or expired codes, incorrect current passwords, rate limits, and delivery failures must have actionable Korean feedback.
- Double submissions and stale asynchronous responses must not corrupt screens or show old errors.
- Unverified invited users must have a way to verify their email before they need recovery.

## Task 1: Cognito password operations and validation

**Files:** `src/lib/cognito.ts`, new `src/lib/passwords.ts`, new `tests/passwords.test.mjs`.

**Interfaces:**
- `changeAccountPassword(currentPassword: string, newPassword: string): Promise<void>`
- `requestPasswordReset(email: string): Promise<void>`
- `confirmPasswordReset(email: string, code: string, newPassword: string): Promise<void>`
- `validateNewPassword(password: string, confirmation: string): string | null`
- `passwordErrorMessage(error: unknown, action: 'change' | 'request' | 'reset' | 'setup' | 'login' | 'verify'): string`

- [x] Add failing behavior tests for authenticated change, recovery without a session, whitespace preservation, error propagation, local validation, and neutral unknown-account recovery.
- [x] Run `node --test tests/passwords.test.mjs` and verify the missing operations fail.
- [x] Wrap the existing Cognito SDK methods, remove unused public sign-up helpers, and implement shared validation/error messages.
- [x] Run the password tests and existing email-verification tests; expect no failures.

## Task 2: Login recovery and account settings

**Files:** `src/components/AuthScreen.tsx`, new `src/components/PasswordResetForm.tsx`, new `src/components/AccountSettings.tsx`, `src/components/SettingsPanel.tsx`, new `tests/auth.fixture.tsx`, new `tests/auth_browser.py`.

**Interfaces:** `AccountSettings` has no props and obtains the current account through existing Cognito helpers. `SettingsTab` gains `account`, visible only with Cognito configured. `AuthScreen` retains its existing `onAuth` callback, invoked only after real authentication.

- [x] Write browser scenarios around real components and real Cognito SDK with intercepted network calls.
- [x] Verify the missing recovery entry and account tab make those scenarios fail.
- [x] Add recovery request, code entry, resend/back, and success-return-to-login screens.
- [x] Add the account tab with email/verification status, explicit verification-code controls, and current/new/confirm password fields.
- [x] Reuse password validation and Korean errors for first-login setup.
- [x] Run browser scenarios for change success/failure, recovery success/expired code/resend, unverified accounts, first-login setup, duplicate submission, and mobile/dark layouts.

## Task 3: Enforce administrator-created accounts

**Files:** `infra/lib/transmeet-stack.ts`, `README.md`.

- [x] Set `selfSignUpEnabled: false` and `accountRecovery: cognito.AccountRecovery.EMAIL_ONLY`.
- [x] Keep the current 7-day temporary-password validity explicit and include the login URL and first-login instructions in the invitation email.
- [x] Document invitation through the Cognito console, email verification, password change, and recovery.
- [x] Compile/synthesize CDK using existing deployed image/context settings.
- [x] Assert the synthesized user pool has `AllowAdminCreateUserOnly: true` and a single `verified_email` recovery mechanism.
- [x] Inspect the deployment change set; preserve unrelated deployed resources.

## Task 4: Review and release

- [x] Run all Node tests, auth browser scenarios, production build, and relevant CDK checks.
- [x] Request one independent code review of the complete change; address material findings.
- [ ] Commit only the feature files with the repository's conventional Korean commit style.
- [ ] Fast-forward the original checkout and push `main`, preserving unrelated working tree changes.
- [ ] Deploy the Cognito configuration and frontend; wait for CloudFormation and CloudFront completion.
- [ ] Verify production recovery UI, deployed asset versions, and administrator-only user creation from read-only AWS configuration checks.
- [ ] Create the user-approved account without supplying a temporary password, request EMAIL delivery, and verify its initial status.
- [ ] Report the commit, deployed site, verification, and any material limitation.

## Verified implementation notes

- 146 Node tests and 5 real-browser scenarios passed. Browser scenarios include duplicate submissions, mismatch validation, wrong current password, expired recovery code, email verification, first-login challenge, and mobile dark mode.
- Next.js production build, CDK TypeScript build/synth, Cognito CloudFormation schema validation, and independent code review passed.
- The live user pool already blocked self-registration through an out-of-band setting; its CloudFormation template did not. This change makes that restriction durable.
- Release uses the synthesized Cognito properties merged into the current deployed template because unrelated backend deployment settings differ from this checkout. The reviewed change set modifies Cognito without replacement and re-evaluates only its API Gateway authorizer ARN reference.
- New password forms preserve the current session and clear password fields after success. Recovery remains signed out.
