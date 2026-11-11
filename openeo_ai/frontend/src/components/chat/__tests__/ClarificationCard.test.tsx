import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ClarificationCard } from '../ClarificationCard'

const questions = [
  { question: 'Which field?', options: [{ label: 'North' }, { label: 'South' }] },
]

describe('ClarificationCard', () => {
  it('needs an answer before submitting', () => {
    render(<ClarificationCard questions={questions as never} onSubmit={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()
  })

  it('submits a chosen option', () => {
    const onSubmit = vi.fn()
    render(<ClarificationCard questions={questions as never} onSubmit={onSubmit} />)
    fireEvent.click(screen.getByRole('button', { name: /North/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    expect(onSubmit).toHaveBeenCalledWith({ '0': 'North' })
  })

  it('lets the user type an answer the options do not cover', () => {
    const onSubmit = vi.fn()
    render(<ClarificationCard questions={questions as never} onSubmit={onSubmit} />)
    fireEvent.change(screen.getByLabelText(/Your own answer/), { target: { value: ' My east paddock ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    expect(onSubmit).toHaveBeenCalledWith({ '0': 'My east paddock' })
  })
})
