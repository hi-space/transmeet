import type { SpeakerRole } from '@/types/meeting'

export function isVoiceSpeaker(speaker: unknown): speaker is `speaker${number}` {
  return (
    typeof speaker === 'string' &&
    /^speaker[1-9][0-9]*$/.test(speaker) &&
    Number.isSafeInteger(Number(speaker.slice(7)))
  )
}

export function normalizeSpeaker(speaker: unknown): SpeakerRole {
  return speaker === 'me' || isVoiceSpeaker(speaker) ? speaker : 'speaker1'
}

export function getSpeakerLabel(speaker: SpeakerRole, language: 'ko' | 'en' = 'ko'): string {
  if (speaker === 'me') return 'Me'
  return `${language === 'ko' ? '화자' : 'Speaker'} ${speaker.slice(7)}`
}
