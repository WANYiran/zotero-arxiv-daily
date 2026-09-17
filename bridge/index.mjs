import { WechatyBuilder } from 'wechaty'
import qr from 'qrcode-terminal'
import { Bridge, apiClient } from './bridge.mjs'

const identify = process.argv.includes('--identify')
if (!process.env.WECHATY_PUPPET_SERVICE_TOKEN) {
  throw new Error('Configure a working Wechaty Puppet Service token before starting the bridge')
}
const bot = WechatyBuilder.build({
  name: process.env.WECHATY_NAME || 'paper-assistant',
  puppet: 'wechaty-puppet-service',
  puppetOptions: { token: process.env.WECHATY_PUPPET_SERVICE_TOKEN },
})
const bridge = identify ? null : new Bridge(bot,
  apiClient(process.env.ASSISTANT_URL || 'http://127.0.0.1:8080', process.env.ASSISTANT_TOKEN),
  process.env.WECHAT_OWNER_ID)

let online = false
let pending = 0
let chain = Promise.resolve()
bot.on('scan', qrcode => {
  console.log('Scan in WeChat to log in to the selected provider. Treat this QR code as private.')
  qr.generate(qrcode, { small: true })
})
bot.on('login', () => { online = true; console.log('Bridge logged in') })
bot.on('logout', () => { online = false; console.log('Bridge logged out') })
bot.on('error', () => { console.error('WeChat transport error; check provider connection') })
bot.on('message', message => {
  if (identify) {
    if (!message.self() && !message.room() && message.type() === bot.Message.Type.Text) {
      console.log('Direct-message sender ID:', message.talker().id)
    }
    return
  }
  if (pending >= 20) { console.error('Inbound queue full; send the question again later'); return }
  pending++
  chain = chain.then(() => bridge.receive(message))
    .catch(() => console.error('Question could not be processed; send it again later'))
    .finally(() => { pending-- })
})

const timer = setInterval(() => {
  if (online && bridge) bridge.deliver().catch(() => console.error('Delivery pending; will retry after lease expiry'))
}, 3000)
async function stop() { clearInterval(timer); await bot.stop(); process.exit(0) }
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
await bot.start()
