import { describe, expect, it, vi } from 'vitest'
import { api, isSuccessfulTerminal, toActivity } from './api'

describe('streamMessage', () => {
  it('parses server-sent events into typed events', async () => {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('data: {"type":"token","content":"Hi"}\n\ndata: {"type":"done"}\n\n'),
        )
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body }))
    const events = []
    for await (const event of api.streamMessage(1, 'Hello')) events.push(event)
    expect(events).toEqual([
      { type: 'token', content: 'Hi' },
      { type: 'done' },
    ])
  })
})

describe('execution event helpers', () => {
  it('maps execution and approval_required events for the activity feed', () => {
    expect(
      toActivity({
        type: 'execution',
        id: 'e1',
        run_id: 'r1',
        event_type: 'tool_call',
        status: 'validated',
        tool_name: 'calculate',
        payload: { arguments: { left: 1 } },
      }),
    ).toMatchObject({ id: 'e1', status: 'validated', tool_name: 'calculate' })

    expect(
      toActivity({
        type: 'approval_required',
        approval_id: 'a1',
        run_id: 'r1',
        tool_name: 'demo_request_approval',
        arguments: { message: 'demo' },
        reason: 'Explicit user approval is required',
      }),
    ).toEqual({
      id: 'a1',
      run_id: 'r1',
      tool_call_id: null,
      event_type: 'approval',
      status: 'waiting_approval',
      tool_name: 'demo_request_approval',
      payload: {
        arguments: { message: 'demo' },
        reason: 'Explicit user approval is required',
      },
    })
  })

  it('treats done and approval_required as successful terminals, not bare stream end', () => {
    expect(isSuccessfulTerminal({ type: 'done' })).toBe(true)
    expect(isSuccessfulTerminal({ type: 'approval_required', approval_id: 'a1' })).toBe(true)
    expect(isSuccessfulTerminal({ type: 'token', content: 'hi' })).toBe(false)
    expect(isSuccessfulTerminal({ type: 'execution', id: 'e1' })).toBe(false)
  })
})
