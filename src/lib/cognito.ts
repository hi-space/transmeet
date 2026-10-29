import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserAttribute,
  type CognitoUserSession,
} from 'amazon-cognito-identity-js'

export type SignInResult =
  | { type: 'success'; user: CognitoUser }
  | { type: 'newPasswordRequired'; user: CognitoUser }

let _pool: CognitoUserPool | null = null

function getPool(): CognitoUserPool {
  if (!_pool) {
    const poolId = process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID ?? ''
    const clientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID ?? ''
    _pool = new CognitoUserPool({ UserPoolId: poolId, ClientId: clientId })
  }
  return _pool
}

export function signIn(email: string, password: string): Promise<SignInResult> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: getPool() })
    const authDetails = new AuthenticationDetails({ Username: email, Password: password })
    user.authenticateUser(authDetails, {
      onSuccess: () => resolve({ type: 'success', user }),
      onFailure: reject,
      newPasswordRequired: () => resolve({ type: 'newPasswordRequired', user }),
    })
  })
}

export function completeNewPassword(user: CognitoUser, newPassword: string): Promise<CognitoUser> {
  return new Promise((resolve, reject) => {
    user.completeNewPasswordChallenge(
      newPassword,
      {},
      {
        onSuccess: () => resolve(user),
        onFailure: reject,
      }
    )
  })
}

export async function changeAccountPassword(
  currentPassword: string,
  newPassword: string
): Promise<void> {
  const user = await authenticatedUser()
  return new Promise((resolve, reject) => {
    user.changePassword(currentPassword, newPassword, (err) => {
      if (err) return reject(err)
      resolve()
    })
  })
}

export function requestPasswordReset(email: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email.trim(), Pool: getPool() })
    user.forgotPassword({
      onSuccess: () => resolve(),
      onFailure: (err) => {
        // Older pool configurations can expose this error despite the client setting.
        const code = (err as Error & { code?: string }).code ?? err.name
        if (code === 'UserNotFoundException') return resolve()
        reject(err)
      },
    })
  })
}

export function confirmPasswordReset(
  email: string,
  code: string,
  newPassword: string
): Promise<void> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email.trim(), Pool: getPool() })
    user.confirmPassword(code.trim(), newPassword, {
      onSuccess: () => resolve(),
      onFailure: reject,
    })
  })
}

export function refreshSession(): Promise<CognitoUser | null> {
  return new Promise((resolve) => {
    const user = getPool().getCurrentUser()
    if (!user) return resolve(null)
    user.getSession((err: Error | null) => {
      if (err) return resolve(null)
      resolve(user)
    })
  })
}

export const AUTH_SESSION_REFRESHED_EVENT = 'transmeet:auth-session-refreshed'

async function authenticatedUser(): Promise<CognitoUser> {
  const user = await refreshSession()
  if (!user) throw new Error('로그인이 만료되었습니다. 다시 로그인해 주세요.')
  return user
}

function publishSession(session: CognitoUserSession): void {
  // 이미 열린 회의에도 현재 클레임을 전달한다. 소켓을 닫으면 진행 중인 녹음이 끊긴다.
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent(AUTH_SESSION_REFRESHED_EVENT, {
        detail: session.getIdToken().getJwtToken(),
      })
    )
  }
}

async function refreshVerifiedSession(user: CognitoUser): Promise<void> {
  const session = user.getSignInUserSession()
  if (!session) throw new Error('로그인이 만료되었습니다. 다시 로그인해 주세요.')
  const refreshed = await new Promise<CognitoUserSession>((resolve, reject) => {
    user.refreshSession(
      session.getRefreshToken(),
      (err: Error | null, next: CognitoUserSession | null) => {
        if (err) return reject(err)
        if (!next)
          return reject(new Error('로그인 정보를 갱신하지 못했습니다. 다시 시도해 주세요.'))
        resolve(next)
      }
    )
  })
  publishSession(refreshed)
}

export async function getAccountEmail(): Promise<{ email: string; verified: boolean }> {
  const user = await authenticatedUser()
  const attributes = await new Promise<CognitoUserAttribute[]>((resolve, reject) => {
    user.getUserAttributes((err, values) => {
      if (err) return reject(err)
      resolve(values ?? [])
    })
  })
  const values = Object.fromEntries(attributes.map((attr) => [attr.getName(), attr.getValue()]))
  if (!values.email) throw new Error('로그인 계정에 이메일 주소가 없습니다.')
  const verified = values.email_verified === 'true'
  const session = user.getSignInUserSession()
  const claims = session?.getIdToken().decodePayload()
  if (
    verified &&
    (claims?.email !== values.email ||
      (claims?.email_verified !== true && claims?.email_verified !== 'true'))
  ) {
    // 다른 탭에서 인증했어도 REST와 WebSocket이 오래된 토큰을 계속 쓰지 않게 한다.
    await refreshVerifiedSession(user)
  } else if (verified && session) {
    // 다른 탭이 저장한 새 토큰을 읽었어도 이 탭의 기존 소켓에는 별도로 알려야 한다.
    publishSession(session)
  }
  return { email: values.email, verified }
}

export async function sendAccountEmailVerificationCode(): Promise<void> {
  const user = await authenticatedUser()
  return new Promise((resolve, reject) => {
    user.getAttributeVerificationCode('email', {
      onSuccess: () => resolve(),
      onFailure: reject,
    })
  })
}

export async function verifyAccountEmailCode(code: string): Promise<void> {
  const user = await authenticatedUser()
  await new Promise<void>((resolve, reject) => {
    user.verifyAttribute('email', code.trim(), {
      onSuccess: () => resolve(),
      onFailure: reject,
    })
  })
  await refreshVerifiedSession(user)
}

// REST Authorization 헤더와 WebSocket 인증 메시지에 싣는 ID 토큰. 서버는 sub 로 미팅 소유자를 가린다.
// getSession 이 만료된 토큰을 refresh 토큰으로 갱신하므로 매 요청마다 불러도 된다. Cognito 미설정·미로그인이면 null.
export function getIdToken(): Promise<string | null> {
  return new Promise((resolve) => {
    if (!process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID) return resolve(null)
    const user = getPool().getCurrentUser()
    if (!user) return resolve(null)
    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session?.isValid()) return resolve(null)
      resolve(session.getIdToken().getJwtToken())
    })
  })
}

export function signOut(user: CognitoUser): void {
  user.signOut()
}
