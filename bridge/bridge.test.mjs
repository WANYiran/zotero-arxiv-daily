import test from 'node:test'
import assert from 'node:assert/strict'
import { Bridge, apiClient } from './bridge.mjs'

function fixture({ failSend = false } = {}) {
  const calls = []
  const contact = { id: 'owner', async say(text) { if (failSend) throw new Error('offline'); calls.push(['say', text]) } }
  const bot = { Message: { Type: { Text: 7 } }, Contact: { find: async () => contact } }
  const api = async (path, body) => {
    calls.push([path, body])
    return { item: { id: 'digest:one:0000', text: '日报', lease_token: 'lease' } }
  }
  const message = { id: '42', self: () => false, room: () => null, type: () => 7,
    talker: () => ({ id: 'owner' }), text: () => '第3篇' }
  return { bridge: new Bridge(bot, api, 'owner'), calls, message }
}

test('only owner direct text reaches the assistant', async () => {
  const { bridge, calls, message } = fixture()
  for (const override of [{ self: () => true }, { room: () => ({}) }, { type: () => 1 }, { talker: () => ({ id: 'stranger' }) }]) {
    assert.equal(await bridge.receive({ ...message, ...override }), false)
  }
  assert.equal(calls.length, 0)
  assert.equal(await bridge.receive(message), true)
  assert.equal(calls[0][0], '/chat')
  assert.equal(calls[0][1].deliver, true)
})

test('duplicate source messages use the same persistent key', async () => {
  const { bridge, calls, message } = fixture()
  await bridge.receive(message)
  await bridge.receive(message)
  assert.equal(calls[0][1].message_id, calls[1][1].message_id)
})

test('delivery acknowledges only after a successful send', async () => {
  const { bridge, calls } = fixture()
  await bridge.deliver()
  assert.deepEqual(calls.map(x => x[0]), ['/outbox/claim', 'say', '/outbox/ack'])
  const failed = fixture({ failSend: true })
  await assert.rejects(failed.bridge.deliver())
  assert.deepEqual(failed.calls.map(x => x[0]), ['/outbox/claim'])
})

test('HTTP client rejects unsafe endpoints and redirects', async () => {
  assert.throws(() => apiClient('http://public.example', 'x'.repeat(40)))
  assert.throws(() => apiClient('https://user:secret@example.org', 'x'.repeat(40)))
  let request
  const api = apiClient('https://example.org', 'x'.repeat(40), async (url, args) => {
    request = args
    return { ok: true, json: async () => ({ ok: true }) }
  })
  await api('/chat', { text: 'test' })
  assert.equal(request.redirect, 'error')
  assert.equal(request.headers.Authorization, 'Bearer ' + 'x'.repeat(40))
})
