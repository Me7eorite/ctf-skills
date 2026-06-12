import { computed, ref, watch, type Ref } from 'vue'

/**
 * Track whether a reactive form value diverges from a captured snapshot.
 *
 * ``isDirty`` is ``true`` whenever any tracked field differs from the value
 * at the most recent ``snapshot()`` call. Used by Save buttons to disable
 * themselves when no changes are pending.
 */
export function useDirty<T extends Record<string, unknown>>(form: Ref<T>) {
  const baseline = ref<T>({ ...form.value }) as Ref<T>

  const isDirty = computed(() => {
    const current = form.value
    const original = baseline.value
    for (const key of Object.keys(current)) {
      if ((current as Record<string, unknown>)[key] !== (original as Record<string, unknown>)[key]) {
        return true
      }
    }
    return false
  })

  function snapshot() {
    baseline.value = { ...form.value }
  }

  watch(form, () => undefined, { deep: true })

  return { isDirty, snapshot }
}
