import { createHash } from 'node:crypto'

export function apiClient(baseUrl, token, fetchImpl = fetch) {
  const url = new URL(baseUrl)
  if (url.username || url.password || url.search || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['assistant', 'localhost', '127.0.0.1'].includes(url.hostname)))) {
    throw new Error('Use HTTPS, or the local Docker assistant service')
  }
  if (!token || token.length < 32) throw new Error('ASSISTANT_TOKEN is missing or too short')
  return async (path, body = {}) => {
    const response = await fetchImpl(baseUrl.replace(/\/$/, '') + path, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(60000),
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new Error(`Assistant HTTP ${response.status}`)
    return response.json()
  }
}

export class Bridge {
  constructor(bot, api, ownerId) {
    if (!ownerId) throw new Error('WECHAT_OWNER_ID is required; use --identify to find it')
    this.bot = bot
    this.api = api
    this.ownerId = ownerId
    this.delivering = false
  }

  async receive(message) {
    // The personal bridge intentionally supports one owner's direct messages only.
    if (message.self() || message.room() || message.type() !== this.bot.Message.Type.Text) return false
    if (message.talker().id !== this.ownerId) return false
    const text = message.text().trim()
    if (!text || text.length > 3000) return false
    const messageId = 'wechat-' + createHash('sha256').update(String(message.id)).digest('hex')
    await this.api('/chat', { message_id: messageId, text, deliver: true })
    return true
  }

  async deliver() {
    if (this.delivering) return
    this.delivering = true
    try {
      const contact = await this.bot.Contact.find({ id: this.ownerId })
      if (!contact || contact.id !== this.ownerId) return
      const { item } = await this.api('/outbox/claim')
      if (!item) return
      await contact.say(item.text)
      // Ack only after transport reports success. A crash in between can duplicate a part.
      await this.api('/outbox/ack', { id: item.id, lease_token: item.lease_token })
    } finally {
      this.delivering = false
    }
  }
}
