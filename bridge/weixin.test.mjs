import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { WeixinApi, WeixinBridge, trustedBase, writePrivate, parsePayload } from './weixin-api.mjs'

const message = { message_type: 1, message_id: 9, from_user_id: 'owner', context_token: 'context',
  item_list: [{ type: 1, text_item: { text: '帮助' } }] }

test('only the QR-bound owner can submit direct text messages', async () => {
  const calls = [], state = { cursor: '' }
  const bridge = new WeixinBridge({}, async (...args) => calls.push(args), { owner: 'owner', bot: 'bot' }, state, () => {})
  await bridge.receiveBatch({ msgs: [ { ...message, from_user_id: 'stranger' }, { ...message, group_id: 'group' },
    { ...message, message_type: 2 }, message ], get_updates_buf: 'next' })
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], '/chat')
  assert.equal(calls[0][1].deliver, true)
  assert.equal(state.context, 'context')
  assert.equal(state.cursor, 'next')
  const firstId = calls[0][1].message_id
  await bridge.receiveBatch({ msgs: [message] })
  assert.equal(calls[1][1].message_id, firstId)
})

test('failed durable chat submission does not advance cursor', async () => {
  const state = { cursor: 'old' }
  const bridge = new WeixinBridge({}, async () => { throw new Error('offline') }, { owner: 'owner', bot: 'bot' }, state, () => {})
  await assert.rejects(bridge.receiveBatch({ msgs: [message], get_updates_buf: 'new' }))
  assert.equal(state.cursor, 'old')
})

test('ack follows confirmed delivery and stable client ID survives retries', async () => {
  const calls = [], sent = []
  const bridge = new WeixinBridge({ request: async (endpoint, body) => { sent.push(body); return { ret: 0 } } },
    async (endpoint, body) => { calls.push(endpoint); return { item: { id: 1, text: '日报', lease_token: 'lease' } } },
    { owner: 'owner', bot: 'bot' }, { context: 'context' }, () => {})
  await bridge.deliver(); await bridge.deliver()
  assert.deepEqual(calls, ['/outbox/claim', '/outbox/ack', '/outbox/claim', '/outbox/ack'])
  assert.equal(sent[0].msg.to_user_id, 'owner')
  assert.equal(sent[0].msg.context_token, 'context')
  assert.equal(sent[0].msg.client_id, sent[1].msg.client_id)
})

test('unconfirmed send is never acknowledged', async () => {
  const calls = []
  const bridge = new WeixinBridge({ request: async () => ({}) }, async endpoint => {
    calls.push(endpoint); return { item: { id: 1, text: '日报', lease_token: 'lease' } }
  }, { owner: 'owner', bot: 'bot' }, { context: 'context' }, () => {})
  await assert.rejects(bridge.deliver())
  assert.deepEqual(calls, ['/outbox/claim'])
  assert.equal(bridge.delivering, false)
})

test('server message ID confirms a send even when ret is omitted', async () => {
  const calls = []
  const bridge = new WeixinBridge({ request: async () => ({ message_id: '18446744073709551615' }) }, async endpoint => {
    calls.push(endpoint); return { item: { id: 1, text: '日报', lease_token: 'lease' } }
  }, { owner: 'owner', bot: 'bot' }, { context: 'context' }, () => {})
  await bridge.deliver()
  assert.deepEqual(calls, ['/outbox/claim', '/outbox/ack'])
})

test('uint64 IDs remain distinct and quoted message text is untouched', () => {
  const first = parsePayload('{"message_id":18446744073709551614,"text":"message_id:18446744073709551614"}')
  const second = parsePayload('{"message_id":18446744073709551615}')
  assert.equal(first.message_id, '18446744073709551614')
  assert.equal(second.message_id, '18446744073709551615')
  assert.notEqual(first.message_id, second.message_id)
  assert.equal(first.text, 'message_id:18446744073709551614')
})

test('no conversation context means no delivery claim', async () => {
  const bridge = new WeixinBridge({}, () => assert.fail('must not claim'), { owner: 'owner', bot: 'bot' }, {}, () => {})
  await bridge.deliver()
})

test('rejected context releases its lease and pauses until a fresh owner message', async () => {
  const calls = [], state = { context: 'old', lastIncoming: '9' }
  const rejected = new Error('rejected'); rejected.code = -2
  let fail = true
  const bridge = new WeixinBridge({ request: async () => { if (fail) throw rejected; return { message_id: 'receipt' } } },
    async endpoint => { calls.push(endpoint); return { item: { id: 1, text: '日报', lease_token: 'lease' } } },
    { owner: 'owner', bot: 'bot' }, state, () => {})
  await assert.rejects(bridge.deliver())
  assert.deepEqual(calls, ['/outbox/claim', '/outbox/release'])
  await bridge.deliver()
  assert.equal(calls.length, 2)
  await bridge.receiveBatch({ msgs: [message] })
  assert.equal(state.blocked, true, 'replayed incoming ID must not reopen a rejected context')
  await bridge.receiveBatch({ msgs: [{ ...message, message_id: 10, context_token: 'fresh' }] })
  assert.equal(state.blocked, false)
  fail = false
  await bridge.deliver()
  assert.equal(calls.at(-1), '/outbox/ack')
})

test('an old send rejection does not block a newer inbound context', async () => {
  const state = { context: 'old', lastIncoming: '9' }, calls = []
  const rejected = new Error('rejected'); rejected.code = -2
  let bridge
  bridge = new WeixinBridge({ request: async () => {
    await bridge.receiveBatch({ msgs: [{ ...message, message_id: 10, context_token: 'fresh' }] })
    throw rejected
  } }, async endpoint => { calls.push(endpoint); return { item: { id: 1, text: '日报', lease_token: 'lease' } } },
  { owner: 'owner', bot: 'bot' }, state, () => {})
  await assert.rejects(bridge.deliver())
  assert.equal(state.blocked, false)
  assert.equal(state.context, 'fresh')
  assert.equal(calls.at(-1), '/outbox/release')
})

test('reject credential forwarding outside official HTTPS hosts', () => {
  for (const url of ['http://ilinkai.weixin.qq.com', 'https://weixin.qq.com.attacker.com', 'https://ilinkai.weixin.qq.com@attacker.com', 'https://ilinkai.weixin.qq.com:444']) {
    assert.throws(() => trustedBase(url))
  }
  assert.equal(trustedBase('https://ilinkai.weixin.qq.com/'), 'https://ilinkai.weixin.qq.com')
})

test('protocol uses auth only for POST and handles expired sessions without leaking response', async () => {
  const calls = []
  const api = new WeixinApi(undefined, 'secret', async (url, options) => {
    calls.push(options); return { ok: true, text: async () => JSON.stringify({ ret: -14, errmsg: 'private details' }) }
  })
  await assert.rejects(api.request('getupdates', { get_updates_buf: '' }), error => error.expired && !error.message.includes('private'))
  assert.equal(calls[0].headers.Authorization, 'Bearer secret')
  assert.equal(calls[0].redirect, 'error')
  assert.equal(JSON.parse(calls[0].body).base_info.bot_agent, 'PaperAssistant/1.0')
  await assert.rejects(api.request('get_qrcode_status?qrcode=test'))
  assert.equal(calls[1].headers.Authorization, undefined)
})

test('credential files are private after creation and atomic replacement', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'weixin-test-'))
  try {
    const file = path.join(directory, 'account.json')
    writePrivate(file, { token: 'first' }); writePrivate(file, { token: 'second' })
    assert.equal(fs.statSync(file).mode & 0o777, 0o600)
    assert.equal(JSON.parse(fs.readFileSync(file)).token, 'second')
    assert.deepEqual(fs.readdirSync(directory), ['account.json'])
  } finally { fs.rmSync(directory, { recursive: true }) }
})
