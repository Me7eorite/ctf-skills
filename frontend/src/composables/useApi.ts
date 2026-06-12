import { useMutation, useQuery, type UseQueryReturnType } from '@tanstack/vue-query'
import type { ComputedRef, Ref } from 'vue'

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const contentType = response.headers.get('content-type') ?? ''
    if (contentType.includes('application/json')) {
      const payload = (await response.json()) as { message?: string; detail?: string }
      throw new Error(payload.message ?? payload.detail ?? `${response.status} ${response.statusText}`)
    }
    const text = await response.text()
    throw new Error(text || `${response.status} ${response.statusText}`)
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as unknown as T
}

export function useApiQuery<T>(
  key: string | Array<string | Ref<unknown> | ComputedRef<unknown>>,
  path: string,
): UseQueryReturnType<T, Error> {
  return useQuery<T, Error>({
    queryKey: Array.isArray(key) ? key : [key],
    queryFn: () => http<T>(path),
  })
}

export function useApiMutation<TResponse, TInput>(
  path: string,
  method: 'POST' | 'PUT' | 'DELETE' = 'POST',
) {
  return useMutation<TResponse, Error, TInput | undefined>({
    mutationFn: (input) =>
      http<TResponse>(path, {
        method,
        body: input ? JSON.stringify(input) : undefined,
      }),
  })
}

export const apiClient = { http }
