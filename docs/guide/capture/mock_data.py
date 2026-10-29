"""가이드 스크린샷용 샘플 데이터. 실제 계정·회의 내용은 쓰지 않는다."""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 29, 11, 20, tzinfo=KST)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


START = NOW.replace(hour=10, minute=0, second=0)

# (화자, 원문, 번역, 원문 언어, 시작 후 초)
_LINES = [
    ("speaker1", "Thanks everyone for joining. Today I'd like to finalize the launch timeline for the Korean market.",
     "모두 참석해 주셔서 감사합니다. 오늘은 한국 시장 출시 일정을 확정하고 싶습니다.", "en", 5),
    ("speaker2", "From the engineering side, the localized build is ready, but we still need final QA on the payment flow.",
     "엔지니어링 쪽에서는 현지화 빌드가 준비됐지만, 결제 흐름에 대한 최종 QA가 아직 남아 있습니다.", "en", 32),
    ("me", "결제 QA는 저희 팀이 다음 주 수요일까지 마무리할 수 있습니다.",
     "Our team can finish the payment QA by next Wednesday.", "ko", 58),
    ("speaker1", "That works. Then can we lock in November 18th as the launch date?",
     "좋습니다. 그럼 출시일을 11월 18일로 확정해도 될까요?", "en", 81),
    ("speaker2", "I'd suggest keeping a one-week buffer in case the app store review takes longer than usual.",
     "앱스토어 심사가 평소보다 길어질 경우를 대비해 1주일 정도 여유를 두는 걸 제안합니다.", "en", 104),
    ("speaker1", "Good point. Let's set the 18th as the target and the 25th as the fallback date.",
     "좋은 지적이에요. 18일을 목표일로, 25일을 예비일로 잡읍시다.", "en", 131),
    ("speaker3", "Marketing will need the final copy by November 4th to prepare the launch campaign.",
     "마케팅팀은 출시 캠페인을 준비하려면 11월 4일까지 최종 카피가 필요합니다.", "en", 158),
    ("me", "카피 초안은 이번 주 금요일까지 공유드리겠습니다.",
     "I'll share the draft copy by this Friday.", "ko", 185),
    ("speaker1", "Perfect. Let's also schedule a go/no-go review on November 14th with all leads.",
     "좋아요. 11월 14일에 리드 전원이 참석하는 출시 여부 최종 검토 회의도 잡읍시다.", "en", 207),
    ("speaker2", "Sounds good. I'll send out the calendar invite right after this call.",
     "좋습니다. 이 통화가 끝나면 바로 일정 초대를 보내겠습니다.", "en", 236),
]

MAIN_ID = "mtg-launch"


def main_messages():
    out = []
    for i, (speaker, original, translated, lang, sec) in enumerate(_LINES):
        out.append({
            "id": f"msg-{i + 1}",
            "speaker": speaker,
            "originalText": original,
            "translatedText": translated,
            "detectedLanguage": lang,
            "targetLanguage": "en" if lang == "ko" else "ko",
            "timestamp": iso(START + timedelta(seconds=sec)),
        })
    return out


SUMMARY = """## 핵심 요약
- 한국 시장 출시일을 **11월 18일**로 확정하고, 예비일은 **11월 25일**로 둔다
- 출시 전 **11월 14일** 리드 전원이 참석하는 출시 여부(Go/No-go) 최종 검토를 연다

## 논의 내용
- **현지화 빌드**: 준비 완료. 결제 흐름 최종 QA만 남음
- **앱스토어 심사**: 평소보다 길어질 수 있어 1주일 여유를 두자는 제안 → 예비일로 반영
- **마케팅 캠페인**: 최종 카피가 11월 4일까지 필요

## 결정 사항
- 출시 목표일 11월 18일, 예비일 11월 25일
- 11월 14일 Go/No-go 리뷰 진행

## 후속 업무
| 담당 | 할 일 | 기한 |
|---|---|---|
| 우리 팀 | 결제 흐름 최종 QA | 다음 주 수요일 |
| 우리 팀 | 마케팅 카피 초안 공유 | 이번 주 금요일 |
| 마케팅팀 | 최종 카피 확정 | 11월 4일 |
| 엔지니어링 | Go/No-go 리뷰 일정 초대 발송 | 오늘 |
"""

BRIEF = "11월 18일 한국 출시(예비일 25일)를 확정했고, 결제 QA는 다음 주 수요일·카피 초안은 이번 주 금요일까지 마무리하기로 했습니다."

PAGE = {
    "pageId": "pg-launch",
    "url": "https://pages.guide.test/p/pg-launch",
    "generatedAt": iso(NOW - timedelta(minutes=5)),
    "title": "시애틀 팀과 4분기 한국 출시 일정 점검",
    "shareSummary": BRIEF,
}

TEMPLATES = [
    {
        "templateId": "tpl-weekly",
        "name": "주간 팀 회의",
        "kind": "instruction",
        "body": "섹션은 이슈 / 원인 / 조치 / 담당 순서로 정리해 주세요. 각 이슈는 표로, 조치에는 기한을 붙여 주세요.",
        "createdAt": iso(NOW - timedelta(days=10)),
        "updatedAt": iso(NOW - timedelta(days=2)),
    },
]


def _meeting(mid, title, created, count):
    return {"meetingId": mid, "title": title, "createdAt": iso(created), "messageCount": count, "hasAudio": True}


def meeting_list():
    return [
        {**_meeting(MAIN_ID, "시애틀 팀과 4분기 한국 출시 일정 점검", START, 10), "brief": BRIEF},
        _meeting("mtg-2", "AWS 파트너 아키텍처 리뷰", NOW.replace(hour=9, minute=0), 42),
        _meeting("mtg-3", "Weekly Sync — APAC Sales", NOW - timedelta(days=1), 57),
        _meeting("mtg-4", "고객 인터뷰: 결제 경험 개선", NOW - timedelta(days=2), 33),
        _meeting("mtg-5", "Design critique: onboarding flow", NOW - timedelta(days=8), 28),
        _meeting("mtg-6", "Q3 실적 공유 미팅", NOW - timedelta(days=40), 64),
    ]


def main_meeting(with_summary=True, with_page=True):
    m = {**meeting_list()[0], "messages": main_messages(), "briefUpdatedAt": iso(NOW),
         "briefMessageCount": 10, "briefLastMessageId": "msg-10", "summaryStyle": "detailed"}
    if with_summary:
        m.update(summary=SUMMARY, summaryGeneratedStyle="detailed", summarizedAt=iso(NOW))
    if with_page:
        m["page"] = PAGE
    return m


QUICK_HISTORY = [
    {"id": "q1", "sourceText": "일정을 일주일 정도 미룰 수 있을까요?", "targetText": "Could we push the schedule back by about a week?",
     "sourceLang": "ko", "targetLang": "en", "createdAt": iso(NOW - timedelta(minutes=12))},
    {"id": "q2", "sourceText": "go/no-go review", "targetText": "출시 여부 최종 검토 회의",
     "sourceLang": "en", "targetLang": "ko", "createdAt": iso(NOW - timedelta(minutes=30))},
]

MEMO_KO = "출시 일정은 어떻게 결정됐나요?"
MEMO_EN = "How was the launch schedule decided?"
QA_ANSWER = ("**11월 18일**을 목표일로, **11월 25일**을 예비일로 정했습니다.\n\n"
             "- 앱스토어 심사가 길어질 수 있어 1주일 여유를 두자는 제안을 반영했습니다\n"
             "- 최종 여부는 **11월 14일 Go/No-go 리뷰**에서 확인합니다")
