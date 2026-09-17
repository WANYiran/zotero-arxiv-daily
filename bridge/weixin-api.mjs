// Text-only integration based on Tencent/openclaw-weixin docs/protocol.md (2.4.9).
// This is our adapter, not the official OpenClaw plugin or an OpenClaw agent.
import { randomBytes, createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const DEFAULT_BASE = 'https://ilinkai.weixin.qq.com'
const VERSION = '2.4.9'

export function trustedBase(value) {
  const url = new URL(value)
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.weixin.qq.com') ||
      url.port || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Untrusted Weixin endpoint')
  }
  return url.origin
}

export function writePrivate(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 })
  const temp = file + '.' + randomBytes(8).toString('hex')
  fs.writeFileSync(temp, JSON.stringify(value), { mode: 0o600, flag: 'wx' })
  fs.renameSync(temp, file)
}

export function readPrivate(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')) }
  catch (error) { if (error.code === 'ENOENT' && fallback !== undefined) return fallback; throw error }
}

export class WeixinApi {
  constructor(base = DEFAULT_BASE, token = '', fetchImpl = fetch) {
    this.base = trustedBase(base)
    this.token = token
    this.fetch = fetchImpl
  }

  async request(endpoint, body, timeout = 40000) {
    const headers = { 'iLink-App-Id': 'bot', 'iLink-App-ClientVersion': String(0x020409) }
    if (body !== undefined) {
      Object.assign(headers, {
        'Content-Type': 'application/json', AuthorizationType: 'ilink_bot_token',
        'X-WECHAT-UIN': Buffer.from(String(randomBytes(4).readUInt32BE())).toString('base64'),
      })
      if (this.token) {
        headers.Authorization = 'Bearer ' + this.token
        body = { ...body, base_info: { channel_version: VERSION, bot_agent: 'PaperAssistant/1.0' } }
      }
    }
    const response = await this.fetch(this.base + '/ilink/bot/' + endpoint, {
      method: body === undefined ? 'GET' : 'POST', headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'error', signal: AbortSignal.timeout(timeout),
    })
    if (!response.ok) throw new Error('Weixin HTTP ' + response.status)
    const data = await response.json()
    if ((data.ret !== undefined && data.ret !== 0) || (data.errcode !== undefined && data.errcode !== 0)) {
      const error = new Error('Weixin request rejected')
      error.expired = data.ret === -14 || data.errcode === -14
      throw error
    }
    return data
  }
}

export class WeixinBridge {
  constructor(weixin, assistant, account, state, save) {
    if (!account.owner || !account.bot) throw new Error('A QR-bound owner and bot are required')
    Object.assign(this, { weixin, assistant, account, state, save })
    this.delivering = false
  }

  async receiveBatch(batch) {
    for (const message of batch.msgs ?? []) {
      if (message.group_id || message.message_type !== 1 || message.from_user_id !== this.account.owner) continue
      const text = (message.item_list ?? []).filter(item => item.type === 1)
        .map(item => item.text_item?.text ?? '').join('\n').trim()
      if (!text || text.length > 3000 || message.message_id === undefined) continue
      if (message.context_token) {
        this.state.context = message.context_token
        this.save(this.state)
      }
      const id = createHash('sha256').update(this.account.bot + ':' + String(message.message_id)).digest('hex')
      await this.assistant('/chat', { message_id: 'weixin-' + id, text, deliver: true })
    }
    // Advance only after the entire batch has been durably handled by the assistant.
    // A retry after a crash uses the same message IDs and therefore does not requeue replies.
    if (batch.get_updates_buf) this.state.cursor = batch.get_updates_buf
    this.save(this.state)
  }

  async deliver() {
    if (this.delivering || !this.state.context) return
    this.delivering = true
    try {
      const { item } = await this.assistant('/outbox/claim')
      if (!item) return
      const result = await this.weixin.request('sendmessage', { msg: {
        from_user_id: '', to_user_id: this.account.owner,
        client_id: 'paper-' + createHash('sha256').update(this.account.bot + ':' + item.id).digest('hex'),
        message_type: 2, message_state: 2, context_token: this.state.context,
        item_list: [{ type: 1, text_item: { text: item.text } }],
      } }, 30000)
      if (result.ret !== 0) throw new Error('Weixin did not confirm delivery')
      await this.assistant('/outbox/ack', { id: item.id, lease_token: item.lease_token })
    } finally { this.delivering = false }
  }
}
