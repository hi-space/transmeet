import type { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda'
import {
  SESv2Client,
  GetEmailIdentityCommand,
  CreateEmailIdentityCommand,
  DeleteEmailIdentityCommand,
  NotFoundException,
} from '@aws-sdk/client-sesv2'

// 미팅 종료 메일의 수신 주소 인증. SES 가 샌드박스라 수신자도 SES identity 로 인증돼 있어야 한다.
// 주소는 Cognito Authorizer 가 검증한 ID 토큰의 email 클레임만 쓴다 — 남의 주소로 인증 메일을 보내지 못하게.
//   GET  /me/email         → { email, status: 'verified' | 'pending' | 'none' }
//   POST /me/email/verify  → 인증 메일 (재)발송 후 같은 모양

const ses = new SESv2Client({ region: process.env.REGION })

type Status = 'verified' | 'pending' | 'none'

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
}

function respond(statusCode: number, body: unknown): APIGatewayProxyResult {
  return { statusCode, headers, body: JSON.stringify(body) }
}

function callerEmail(event: APIGatewayProxyEvent): string | undefined {
  const claims = event.requestContext.authorizer?.claims ?? {}
  const verified = claims.email_verified === true || claims.email_verified === 'true'
  return verified && typeof claims.email === 'string' && claims.email ? claims.email : undefined
}

async function identityStatus(email: string): Promise<Status> {
  try {
    const r = await ses.send(new GetEmailIdentityCommand({ EmailIdentity: email }))
    return r.VerifiedForSendingStatus ? 'verified' : 'pending'
  } catch (err) {
    if (err instanceof NotFoundException) return 'none'
    throw err
  }
}

export async function handler(event: APIGatewayProxyEvent): Promise<APIGatewayProxyResult> {
  try {
    const email = callerEmail(event)
    if (!email) return respond(400, { error: '로그인 계정에 확인된 이메일이 없습니다' })

    let status = await identityStatus(email)
    if (event.httpMethod === 'POST' && status !== 'verified') {
      // SES 는 인증 메일 재발송 API 가 없다 — pending 이면 지우고 다시 만들어 새 메일을 받게 한다
      if (status === 'pending') await ses.send(new DeleteEmailIdentityCommand({ EmailIdentity: email }))
      await ses.send(new CreateEmailIdentityCommand({ EmailIdentity: email }))
      status = 'pending'
    }
    return respond(200, { email, status })
  } catch (err) {
    console.error('Email handler error:', err)
    return respond(500, { error: 'Internal server error' })
  }
}
