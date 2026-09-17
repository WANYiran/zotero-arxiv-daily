import path from 'node:path'
import fs from 'node:fs'
import { setTimeout as sleep } from 'node:timers/promises'
import { apiClient } from './bridge.mjs'
import { DEFAULT_BASE, trustedBase, WeixinApi, WeixinBridge, readPrivate, writePrivate } from './weixin-api.mjs'

const directory = process.env.WEIXIN_DATA || '/data'
const accountFile = path.join(directory, 'account.json')
const pendingFile = path.join(directory, 'pending-login.json')
const command = process.argv[2] || 'run'

async function main() {
  if (command === 'login-start') {
    const api = new WeixinApi()
    const qr = await api.request('get_bot_qrcode?bot_type=3', { local_token_list: [] })
    if (!qr.qrcode || !qr.qrcode_img_content) throw new Error('Missing QR code')
    writePrivate(pendingFile, { qrcode: qr.qrcode, base: DEFAULT_BASE, started: Date.now() })
    // Display only the short-lived login QR payload; credentials are never printed.
    console.log(JSON.stringify({ qr_url: qr.qrcode_img_content }))
    return
  }
  if (command === 'login-poll') {
    const pending = readPrivate(pendingFile)
    const codeFile = path.join(directory, 'verify-code')
    while (Date.now() - pending.started < 300000) {
      let endpoint = 'get_qrcode_status?qrcode=' + encodeURIComponent(pending.qrcode)
      if (fs.existsSync(codeFile)) endpoint += '&verify_code=' + encodeURIComponent(fs.readFileSync(codeFile, 'utf8').trim())
      const result = await new WeixinApi(pending.base).request(endpoint)
      if (result.status === 'confirmed') {
        if (!result.bot_token || !result.ilink_bot_id || !result.ilink_user_id) throw new Error('Incomplete login response')
        const existing = readPrivate(accountFile, null)
        if (existing && existing.owner !== result.ilink_user_id) throw new Error('Owner change requires a separate assistant deployment')
        writePrivate(accountFile, { token: result.bot_token, bot: result.ilink_bot_id,
          owner: result.ilink_user_id, base: trustedBase(result.baseurl || DEFAULT_BASE) })
        fs.rmSync(pendingFile, { force: true }); fs.rmSync(codeFile, { force: true })
        console.log('LOGIN_CONFIRMED')
        return
      }
      if (result.status === 'scaned_but_redirect' && result.redirect_host) {
        pending.base = trustedBase('https://' + result.redirect_host)
        writePrivate(pendingFile, pending)
      } else if (['need_verifycode', 'verify_code_blocked', 'expired', 'binded_redirect'].includes(result.status)) {
        console.log('LOGIN_STATUS ' + result.status)
        return
      }
      await sleep(1500)
    }
    console.log('LOGIN_STATUS expired')
    return
  }
  if (command !== 'run') throw new Error('Unknown command')
  const account = readPrivate(accountFile)
  const stateFile = path.join(directory, 'state-' + Buffer.from(account.bot).toString('base64url') + '.json')
  const state = readPrivate(stateFile, { cursor: '', context: '' })
  const weixin = new WeixinApi(account.base, account.token)
  const bridge = new WeixinBridge(weixin, apiClient(process.env.ASSISTANT_URL, process.env.ASSISTANT_TOKEN),
    account, state, value => writePrivate(stateFile, value))
  let pausedUntil = 0
  const failure = error => {
    if (error.expired) pausedUntil = Date.now() + 3600000
    console.error(error.expired ? 'SESSION_EXPIRED: login again; paused for one hour'
      : error.code === -2 ? 'SEND_REJECTED: waiting for a new owner message; digest remains queued'
      : 'REQUEST_FAILED: queued messages remain pending')
  }
  setInterval(() => {
    if (Date.now() >= pausedUntil) bridge.deliver().catch(failure)
  }, 2000)
  console.log('WEIXIN_BRIDGE_STARTED: private text messages from the QR-bound owner only')
  while (true) {
    if (Date.now() < pausedUntil) { await sleep(5000); continue }
    try {
      const batch = await weixin.request('getupdates', { get_updates_buf: state.cursor }, 45000)
      await bridge.receiveBatch(batch)
      await sleep(1000)
    } catch (error) { failure(error); await sleep(5000) }
  }
}

main().catch(() => { console.error('WEIXIN_SETUP_FAILED: inspect configuration or retry login; credentials were not logged'); process.exitCode = 1 })
