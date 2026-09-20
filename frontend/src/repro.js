import { createApp, defineComponent, h, ref, onMounted, nextTick } from 'vue'
import './styles/base.css'
import MessageBubble from './components/chat/MessageBubble.vue'
import ActionButtons from './components/chat/ActionButtons.vue'
import { scopeSkinToChat } from './chatSkinScope.js'
import { BottomAnchor } from './components/chat/bottomAnchor.js'

const q = new URLSearchParams(location.search)
const skin = q.get('skin') !== '0'
if (skin) {
  const style = document.createElement('style')
  style.textContent = scopeSkinToChat('.chat-header, .chat-body, .chat-footer {\nbackground: linear-gradient(135deg, #3d1f00, #b84d00, #0055ff);\n}')
  document.head.appendChild(style)
}
const css = document.createElement('style')
css.textContent = `
.live-chat-window{position:fixed;inset:0;display:flex;background:white;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.chat-window-outer{flex:1;display:flex;min-height:0;min-width:0}
.chat-window-shell{flex:1;display:flex;min-height:0;min-width:0}
.chat-window{position:relative;display:flex;flex-direction:column;flex:1;min-height:0;min-width:0}
.chat-header{flex-shrink:0;height:70px;position:relative;color:#fff;padding:1rem;box-sizing:border-box}
.messages{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;overscroll-behavior-y:contain}
.messages-content{flex:none;display:flex;flex-direction:column;gap:.5rem}
.chat-footer{display:flex;flex-direction:column;flex-shrink:0}
`
document.head.appendChild(css)

const words = 'Questa è la risposta in streaming che arriva pezzo per pezzo dal bus e viene renderizzata come markdown dentro la bolla, e continua ancora un poco per andare a capo.'.split(' ')
let nextId = 0
const App = defineComponent({
  setup() {
    const messages = ref([])
    const actions = ref([{ name: 'a', ui_button: 'Uno' }, { name: 'b', ui_button: 'Due' }])
    const disabled = ref(false)
    const scrollEl = ref(null), contentEl = ref(null)
    const anchor = new BottomAnchor()
    for (let i = 0; i < 12; i++) messages.value.push({ id: ++nextId, role: i % 2 ? 'assistant' : 'user', content: `Messaggio numero ${i} abbastanza lungo da riempire lo schermo del telefono e forzare lo scroll.`, timestamp: new Date().toISOString() })
    function patch(id, p) { const i = messages.value.findIndex((m) => m.id === id); messages.value[i] = { ...messages.value[i], ...p } }
    async function turn() {
      disabled.value = true
      const id = ++nextId
      messages.value.push({ id, role: 'assistant', content: '', pending: true, awaitingReply: false, statusText: '', progressTitle: '', progressPercentage: null, timestamp: new Date().toISOString() })
      await wait(300); patch(id, { pending: false, awaitingReply: true })
      await wait(1500)
      for (let i = 1; i <= words.length; i++) { patch(id, { content: words.slice(0, i).join(' '), pending: false, awaitingReply: false }); await wait(90) }
      await wait(300)
      patch(id, { content: words.join(' '), messageId: 'm' + id, pending: false, awaitingReply: false, progressPercentage: null })
      actions.value = [{ name: 'c', ui_button: 'Tre' }, { name: 'd', ui_button: 'Quattro' }, { name: 'e', ui_button: 'Cinque' }]
      disabled.value = false
      document.title = 'done'
    }
    onMounted(() => { anchor.attach(scrollEl.value, contentEl.value); setTimeout(turn, 1000) })
    return () => h('div', { class: 'live-chat-window' }, [h('div', { class: 'chat-window-outer' }, [h('div', { class: 'chat-window-shell' }, [h('div', { class: 'chat-window' }, [
      h('div', { class: 'chat-header' }, 'header'),
      h('div', { class: 'messages chat-body', ref: scrollEl, onScroll: () => anchor.onScroll() }, [h('div', { class: 'messages-content', ref: contentEl },
        messages.value.map((msg) => h(MessageBubble, { key: msg.id, message: msg, reactions: [{ key: 'up', ui_label: '👍' }], showTimestamp: true })))]),
      h('div', { class: 'chat-footer' }, [h(ActionButtons, { actions: actions.value, disabled: disabled.value, onAction: turn })])
    ])])])])
  }
})
function wait(ms) { return new Promise((r) => setTimeout(r, ms)) }
createApp(App).mount('#app')
