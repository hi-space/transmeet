export interface ClipboardContent {
  text: string
  html: string
}

export type ClipboardFormat = 'rich' | 'text'

// Keep a synchronous copy path for browsers without the async Clipboard API.
// A copy event carries both formats without inserting copied HTML into the page.
function legacyCopy(text: string, html?: string): ClipboardFormat {
  const activeElement = document.activeElement
  const selection = document.getSelection()
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) =>
        selection.getRangeAt(index).cloneRange()
      )
    : []
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  let format: ClipboardFormat = 'text'
  const onCopy = (event: ClipboardEvent) => {
    if (!html || !event.clipboardData) return
    event.clipboardData.setData('text/plain', text)
    event.clipboardData.setData('text/html', html)
    event.preventDefault()
    format = 'rich'
  }

  document.body.appendChild(textarea)
  document.addEventListener('copy', onCopy)
  textarea.select()
  try {
    if (!document.execCommand('copy')) throw new Error('클립보드 복사가 거부되었습니다')
    return format
  } finally {
    document.removeEventListener('copy', onCopy)
    textarea.remove()
    if (activeElement instanceof HTMLElement) activeElement.focus({ preventScroll: true })
    if (selection) {
      selection.removeAllRanges()
      ranges.forEach((range) => selection.addRange(range))
    }
  }
}

export async function copyText(text: string): Promise<void> {
  try {
    if (typeof navigator.clipboard?.writeText !== 'function') throw new Error('No Clipboard API')
    await navigator.clipboard.writeText(text)
  } catch {
    legacyCopy(text)
  }
}

/** Call from the click handler, before any fetch or other asynchronous work. */
export async function copyRichText(content: ClipboardContent): Promise<ClipboardFormat> {
  if (typeof navigator.clipboard?.write === 'function' && typeof ClipboardItem !== 'undefined') {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([content.html], { type: 'text/html' }),
          'text/plain': new Blob([content.text], { type: 'text/plain' }),
        }),
      ])
      return 'rich'
    } catch {
      // Some browsers allow the synchronous copy event even when write() is denied.
    }
  }
  try {
    return legacyCopy(content.text, content.html)
  } catch {
    await copyText(content.text)
    return 'text'
  }
}
