import { render } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import TaskLog from './TaskLog'

describe('TaskLog', () => {
  test('follows a single appended row when the viewer was already at the bottom', () => {
    let height = 100
    let scrollTop = 0
    const { container, rerender } = render(
      <TaskLog items={[{ sequence: 1, message: 'one', level: 'info', timestamp: 1 }]} />,
    )
    const node = container.querySelector('[role="list"]')
    Object.defineProperties(node, {
      scrollHeight: { configurable: true, get: () => height },
      clientHeight: { configurable: true, get: () => 100 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value) => { scrollTop = value },
      },
    })
    height = 140

    rerender(
      <TaskLog items={[
        { sequence: 1, message: 'one', level: 'info', timestamp: 1 },
        { sequence: 2, message: 'two', level: 'info', timestamp: 2 },
      ]} />,
    )

    expect(node.scrollTop).toBe(140)
  })
})
