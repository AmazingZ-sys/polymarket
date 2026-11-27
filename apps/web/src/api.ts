export type Row = Record<string, unknown>
export interface Envelope<T> {
  data: T
  meta?: { limit: number; offset: number; total: number }
}
const apiBase = '/api/v1'
export const eventsUrl = apiBase + '/events'
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message)
  }
}

export async function api<T>(
  path: string,
  options?: { body?: unknown; key?: string; signal?: AbortSignal },
): Promise<Envelope<T>> {
  let response: Response
  const write = options?.body !== undefined
  try {
    response = await fetch(apiBase + '/' + path, {
      method: write ? 'POST' : 'GET',
      credentials: 'include',
      cache: 'no-store',
      signal: options?.signal,
      headers: write
        ? {
            'Content-Type': 'application/json',
            'Idempotency-Key': options?.key ?? crypto.randomUUID(),
          }
        : {},
      body: write ? JSON.stringify(options?.body) : undefined,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      throw error
    throw new ApiError(0, '无法连接管理 API，请检查网络和服务状态。')
  }
  const value = await response.json().catch(() => null)
  if (response.status === 401 && !path.startsWith('auth/'))
    window.dispatchEvent(new Event('session-ended'))
  if (!response.ok || !value || !('data' in value)) {
    throw new ApiError(
      response.status,
      value?.error?.message ||
        '管理服务未返回有效响应（' + response.status + '）。',
      value?.error?.code,
    )
  }
  return value as Envelope<T>
}

export function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value))
  })
  return search.size ? '?' + search : ''
}
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成。'
export function asRows(value: unknown): Row[] {
  return Array.isArray(value)
    ? value.filter((v): v is Row => v !== null && typeof v === 'object')
    : []
}
export function rowsToFile(rows: Row[], format: string, name: string) {
  const keys = Array.from(new Set(rows.flatMap((row) => Object.keys(row))))
  const display = (v: unknown): string =>
    v === null || v === undefined
      ? ''
      : typeof v === 'object'
        ? JSON.stringify(v)
        : String(v)
  const csv = (v: unknown) => {
    const field = display(v)
    const safe =
      /^[\t\r\n]|^\s*[=+@-]/.test(field) && !/^-?\d+(\.\d+)?$/.test(field)
        ? "'" + field
        : field
    return '"' + safe.replaceAll('"', '""') + '"'
  }
  const md = (v: unknown) =>
    display(v)
      .replaceAll('|', '\\|')
      .replace(/[\r\n]/g, ' ')
  const content =
    format === 'csv'
      ? '\uFEFF' +
        [
          keys.map(csv).join(','),
          ...rows.map((r) => keys.map((k) => csv(r[k])).join(',')),
        ].join('\r\n')
      : format === 'md'
        ? [
            '# ' + name,
            '',
            '| ' + keys.map(md).join(' | ') + ' |',
            '|' + keys.map(() => '---').join('|') + '|',
            ...rows.map(
              (r) => '| ' + keys.map((k) => md(r[k])).join(' | ') + ' |',
            ),
          ].join('\n')
        : JSON.stringify(rows, null, 2)
  const mime =
    format === 'csv'
      ? 'text/csv'
      : format === 'md'
        ? 'text/markdown'
        : 'application/json'
  const url = URL.createObjectURL(
    new Blob([content], { type: mime + ';charset=utf-8' }),
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name + '.' + format
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
