import { describe, it, expect } from 'vitest'
import { authUrl } from '../utils'

describe('authUrl', () => {
  it('adds the token to this site\'s URLs', () => {
    expect(authUrl('/render-raster?path=a', 'tok')).toBe('/render-raster?path=a&token=tok')
    expect(authUrl(`${window.location.origin}/render-raster`, 'tok')).toContain('token=tok')
  })

  it('never sends the token to another site', () => {
    expect(authUrl('https://evil.example/x.png', 'tok')).toBe('https://evil.example/x.png')
    expect(authUrl('//evil.example/x.png', 'tok')).toBe('//evil.example/x.png')
  })
})
