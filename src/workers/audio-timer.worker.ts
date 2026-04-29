let intervalId: ReturnType<typeof setInterval> | null = null

self.onmessage = (e: MessageEvent<{ type: 'start' | 'stop'; interval?: number }>) => {
  if (e.data.type === 'start') {
    if (intervalId) clearInterval(intervalId)
    intervalId = setInterval(() => {
      self.postMessage({ type: 'tick' })
    }, e.data.interval ?? 700)
  } else if (e.data.type === 'stop') {
    if (intervalId) {
      clearInterval(intervalId)
      intervalId = null
    }
  }
}
