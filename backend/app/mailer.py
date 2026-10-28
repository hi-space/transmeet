"""미팅 종료 메일 (SES v2).

SES 가 샌드박스라 수신 주소도 SES 에 인증돼 있어야 한다. 인증 요청은 REST(infra/lambda/email)가 하고,
여기서는 보내기 전에 인증 여부만 확인한다.
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from botocore.exceptions import ClientError

from .aws_clients import sesv2_client
from .config import config

logger = logging.getLogger(__name__)

# 메일 속 서명 URL 의 유효 시간. 태스크 역할 임시 자격 증명으로 서명하므로 이보다 길게 줘도 먼저 만료된다.
EMAIL_AUDIO_URL_EXPIRES = 6 * 3600
SUMMARY_FAILED_TEXT = "요약을 만들지 못했습니다. 미팅 화면에서 다시 시도해 주세요."

# python:3.13-slim 에는 tzdata 가 없어 zoneinfo 대신 고정 오프셋을 쓴다 (한국은 서머타임이 없다)
KST = timezone(timedelta(hours=9), "KST")


def _format_started(started_at: str) -> str:
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def _inline(text: str) -> str:
    """이스케이프한 한 줄에 **굵게** 만 살린다."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))


def summary_to_html(summary: str) -> str:
    """요약 패널 마크다운(제목·불릿·굵게)만 다루는 작은 변환기. 의존성을 늘리지 않으려고 직접 쓴다."""
    out: List[str] = []
    in_list = False
    for raw in summary.splitlines():
        line = raw.strip()
        bullet = re.match(r"^[-*]\s+(.*)", line)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            out.append(f"<h3 style=\"margin:16px 0 6px\">{_inline(heading.group(2))}</h3>")
        elif line:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _button(url: str, label: str, primary: bool) -> str:
    bg, fg = ("#0891b2", "#ffffff") if primary else ("#e2e8f0", "#0f172a")
    return (
        f'<a href="{html.escape(url)}" style="display:inline-block;padding:10px 16px;margin:4px 8px 4px 0;'
        f'background:{bg};color:{fg};border-radius:8px;text-decoration:none;font-weight:600">{label}</a>'
    )


def build_meeting_email(
    title: str,
    started_at: str,
    summary: Optional[str],
    audio_url: Optional[str],
    meeting_url: str,
) -> dict:
    title = title or "제목 없는 미팅"
    when = _format_started(started_at)
    hours = EMAIL_AUDIO_URL_EXPIRES // 3600
    audio_label = "녹음 파일 다운로드"

    buttons = []
    text_links = []
    if audio_url:
        buttons.append(_button(audio_url, audio_label, primary=True))
        text_links.append(f"{audio_label} ({hours}시간 동안 유효): {audio_url}")
    buttons.append(_button(meeting_url, "미팅 화면에서 보기", primary=not audio_url))
    text_links.append(f"미팅 화면에서 보기: {meeting_url}")

    summary_html = summary_to_html(summary) if summary else f"<p>{SUMMARY_FAILED_TEXT}</p>"
    note = (
        f'<p style="color:#64748b;font-size:13px">다운로드 링크는 {hours}시간 동안 유효합니다. '
        "이후에는 미팅 화면에서 받으세요.</p>"
        if audio_url else ""
    )
    body_html = f"""<!doctype html>
<html><body style="font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#0f172a;line-height:1.6;max-width:640px;margin:0 auto;padding:24px">
<h2 style="margin:0 0 4px">{html.escape(title)}</h2>
<p style="color:#64748b;margin:0 0 16px">{html.escape(when)}</p>
<div>{''.join(buttons)}</div>
{note}
<hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0">
{summary_html}
</body></html>"""

    body_text = "\n".join([
        title,
        when,
        "",
        *text_links,
        "",
        summary or SUMMARY_FAILED_TEXT,
    ])
    return {"subject": f"[TransMeet] {title} 요약", "html": body_html, "text": body_text}


async def email_verified(email: str) -> bool:
    async with sesv2_client() as ses:
        try:
            identity = await ses.get_email_identity(EmailIdentity=email)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NotFoundException":
                return False
            raise
    return bool(identity.get("VerifiedForSendingStatus"))


async def send_meeting_email(to: str, mail: dict) -> None:
    async with sesv2_client() as ses:
        await ses.send_email(
            FromEmailAddress=config.MAIL_FROM,
            Destination={"ToAddresses": [to]},
            Content={"Simple": {
                "Subject": {"Data": mail["subject"], "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": mail["html"], "Charset": "UTF-8"},
                    "Text": {"Data": mail["text"], "Charset": "UTF-8"},
                },
            }},
        )
