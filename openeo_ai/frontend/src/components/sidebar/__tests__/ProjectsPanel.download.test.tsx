// ABOUTME: A saved result's file download sends the Authorization header the route needs,
// rather than a plain link the server always refuses with 401.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AuthContext, type AuthContextValue } from '@/components/auth/auth-context'
import { JobFileRow } from '../ProjectsPanel'

const auth: AuthContextValue = {
  session: null, user: null, accessToken: 'tok', loading: false,
  signOut: async () => {}, passwordRecovery: false, clearPasswordRecovery: () => {},
}

afterEach(() => vi.restoreAllMocks())

describe('JobFileRow download', () => {
  it('fetches the file with the bearer token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('data'))
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    render(
      <AuthContext.Provider value={auth}>
        <JobFileRow saveId="abcd1234" file={{ name: 'a b.tif', path: 'p', ext: '.tif', size_bytes: 1, modified: '' }} />
      </AuthContext.Provider>,
    )
    fireEvent.click(screen.getByTestId('download-abcd1234-a b.tif'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledWith('/saved-jobs/abcd1234/files/a%20b.tif', {
      headers: { Authorization: 'Bearer tok' },
    })
  })
})
