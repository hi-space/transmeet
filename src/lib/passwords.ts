type PasswordAction = 'change' | 'request' | 'reset' | 'setup' | 'login' | 'verify'

export function validateNewPassword(password: string, confirmation: string): string | null {
  if (password.length < 8) return '비밀번호는 8자 이상 입력해 주세요.'
  if (password.length > 256) return '비밀번호는 256자 이하로 입력해 주세요.'
  if (password !== confirmation) return '비밀번호가 일치하지 않습니다.'
  return null
}

export function passwordErrorMessage(error: unknown, action: PasswordAction): string {
  const details = error as { code?: string; name?: string } | null
  const code = details?.code ?? details?.name
  switch (code) {
    case 'CodeMismatchException':
      return '인증 코드가 일치하지 않습니다. 메일의 코드를 다시 확인해 주세요.'
    case 'ExpiredCodeException':
      return '인증 코드가 만료되었습니다. 코드를 다시 받아 주세요.'
    case 'LimitExceededException':
    case 'TooManyRequestsException':
    case 'TooManyFailedAttemptsException':
      return '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.'
    case 'InvalidPasswordException':
      return '사용할 수 없는 비밀번호입니다. 8자 이상의 다른 비밀번호를 입력해 주세요.'
    case 'PasswordHistoryPolicyViolationException':
      return '최근 사용한 비밀번호입니다. 다른 비밀번호를 입력해 주세요.'
    case 'CodeDeliveryFailureException':
      return '인증 메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.'
    case 'NotAuthorizedException':
      if (action === 'change') {
        return '현재 비밀번호를 확인해 주세요. 로그인이 만료되었다면 다시 로그인해 주세요.'
      }
      if (action === 'login') return '이메일 또는 비밀번호를 확인해 주세요.'
      if (action === 'reset') return '인증 정보를 확인하거나 코드를 다시 받아 주세요.'
      return '로그인이 만료되었습니다. 다시 로그인해 주세요.'
    case 'UserNotFoundException':
      return '이메일 또는 인증 정보를 확인해 주세요.'
    case 'UserNotConfirmedException':
      return '계정 확인이 필요합니다. 관리자에게 문의해 주세요.'
    case 'PasswordResetRequiredException':
      return '비밀번호 재설정이 필요합니다. 비밀번호 찾기를 이용해 주세요.'
    case 'NetworkError':
      return '연결을 확인한 뒤 다시 시도해 주세요.'
  }
  const fallbacks: Record<PasswordAction, string> = {
    change: '비밀번호를 변경하지 못했습니다. 다시 로그인한 뒤 시도해 주세요.',
    request: '재설정을 요청하지 못했습니다. 이메일을 확인하거나 관리자에게 문의해 주세요.',
    reset: '비밀번호를 재설정하지 못했습니다. 인증 코드와 비밀번호를 확인해 주세요.',
    setup: '비밀번호를 설정하지 못했습니다. 다시 로그인한 뒤 시도해 주세요.',
    login: '로그인하지 못했습니다. 잠시 후 다시 시도해 주세요.',
    verify: '이메일 인증을 진행하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  }
  return fallbacks[action]
}
