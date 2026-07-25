/**
 * 플랫폼/브라우저 기능 감지 유틸
 *
 * output: 'export' (정적 빌드)이므로 SSR 시점에는 navigator가 없다.
 * 반드시 클라이언트(useEffect / 이벤트 핸들러)에서만 호출할 것.
 */

/** iOS(iPhone/iPad/iPod) 및 iPadOS 13+ (Macintosh로 위장) 판별 */
export function isIOS(): boolean {
  if (typeof navigator === 'undefined') return false
  const ua = navigator.userAgent
  if (/iPad|iPhone|iPod/.test(ua)) return true
  // iPadOS 13+ 는 UA를 Macintosh로 보고하므로 터치 지원 여부로 구분
  return ua.includes('Macintosh') && navigator.maxTouchPoints > 1
}

/**
 * 시스템 오디오 캡처(getDisplayMedia) 가능 여부.
 * iOS Safari는 getDisplayMedia 자체를 구현하지 않으며,
 * 구현된 브라우저라도 오디오 트랙을 못 주는 경우가 있어 시작 시 한 번 더 검증한다.
 */
export function supportsSystemAudio(): boolean {
  if (typeof navigator === 'undefined') return false
  if (isIOS()) return false
  return typeof navigator.mediaDevices?.getDisplayMedia === 'function'
}
