'use client'

import { usePanelResize } from '@/hooks/usePanelResize'

export function useSummaryResize(defaultWidth = 360) {
  return usePanelResize({
    storageKey: 'transmeet-summary-width',
    defaultWidth,
    minWidth: 280,
    maxWidth: 960,
    edge: 'left',
  })
}
