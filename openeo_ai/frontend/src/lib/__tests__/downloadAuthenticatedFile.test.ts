import { describe, it, expect, vi, afterEach } from 'vitest'
import { downloadAuthenticatedFile } from '../utils'

afterEach(() => vi.restoreAllMocks())

describe('downloadAuthenticatedFile', () => {
  it('refuses to save an error response as the file', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('no', { status: 401 }))
    await expect(downloadAuthenticatedFile('/x', 'x', 'tok')).rejects.toThrow('401')
  })
})
