export type WorkspaceTab = 'voice' | 'notes' | 'summary' | 'page'
export type UtilityTab = Exclude<WorkspaceTab, 'voice'>

/**
 * Decide which utility content is visible when desktop and mobile share panels.
 * Background summary requests may change desktopTab while a reader uses another tab.
 */
export function getVisibleUtilityTab(
  isDesktop: boolean,
  activeTab: WorkspaceTab,
  desktopTab: UtilityTab
): UtilityTab {
  // The desktop utility can update beside the conversation. On mobile, honor
  // the selected tab so a background summary never replaces notes or a page.
  if (isDesktop || activeTab === 'voice') return desktopTab
  return activeTab
}
