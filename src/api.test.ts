import { describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('streamMessage', () => {
  it('parses server-sent events into typed events', async () => {
    const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('data: {"type":"token","content":"Hi"}\n\ndata: {"type":"done"}\n\n')); controller.close() } })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body }))
    const events = []
    for await (const event of api.streamMessage(1, 'Hello')) events.push(event)
    expect(events).toEqual([{type: 'token', content: 'Hi'}, {type: 'done'}])
  })
})
