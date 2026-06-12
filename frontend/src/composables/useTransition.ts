/**
 * Canonical transition names used across the app.
 *
 * - ``page``: 100 ms opacity fade-in (route changes).
 * - ``sheet``: 200 ms slide-in from the right (Sheet open, side panels).
 *
 * Defining them centrally keeps a single source of truth so the durations
 * stay aligned with the spec and design tokens.
 */
export interface AppTransition {
  name: 'page' | 'sheet'
  durationMs: number
}

export const PAGE_TRANSITION: AppTransition = { name: 'page', durationMs: 100 }
export const SHEET_TRANSITION: AppTransition = { name: 'sheet', durationMs: 200 }

export function useTransition(name: AppTransition['name'] = 'page'): AppTransition {
  return name === 'sheet' ? SHEET_TRANSITION : PAGE_TRANSITION
}
