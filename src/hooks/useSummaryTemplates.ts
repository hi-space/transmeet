import { useCallback, useEffect, useState } from 'react'
import { api, type SummaryTemplateInput } from '@/lib/api'
import type { SummaryTemplate } from '@/lib/summaryStyles'

// 내 양식 목록. 로그인 뒤 한 번 불러오고, 저장·삭제하면 서버 응답으로 갱신한다.
export function useSummaryTemplates(enabled: boolean) {
  const [templates, setTemplates] = useState<SummaryTemplate[]>([])
  const [error, setError] = useState<string>()
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setTemplates(await api.summaryTemplates.list())
      setLoaded(true)
      setError(undefined)
    } catch {
      setError('내 양식을 불러오지 못했습니다.')
    }
  }, [])

  useEffect(() => {
    if (enabled) void refresh()
    else setTemplates([])
  }, [enabled, refresh])

  const save = useCallback(async (input: SummaryTemplateInput, templateId?: string) => {
    const saved = templateId
      ? await api.summaryTemplates.update(templateId, input)
      : await api.summaryTemplates.create(input)
    setTemplates((prev) => [saved, ...prev.filter((t) => t.templateId !== saved.templateId)])
    return saved
  }, [])

  const remove = useCallback(async (templateId: string) => {
    await api.summaryTemplates.remove(templateId)
    setTemplates((prev) => prev.filter((t) => t.templateId !== templateId))
  }, [])

  return { templates, loaded, refresh, save, remove, error }
}
