import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserAttribute,
  type ISignUpResult,
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

export function signUp(email: string, password: string): Promise<ISignUpResult> {
  return new Promise((resolve, reject) => {
    const attrs = [new CognitoUserAttribute({ Name: 'email', Value: email })]
    getPool().signUp(email, password, attrs, [], (err, result) => {
      if (err) return reject(err)
      resolve(result!)
    })
  })
}

export function confirmSignUp(email: string, code: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: getPool() })
    user.confirmRegistration(code, true, (err) => {
      if (err) return reject(err)
      resolve()
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

export function signOut(user: CognitoUser): void {
  user.signOut()
}
