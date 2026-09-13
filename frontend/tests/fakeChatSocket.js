import { vi } from 'vitest'

export function installFakeChatSocket(api) {
  const sockets = []
  api.createChatSocket.mockImplementation(() => {
    const ws = {
      readyState: 0,
      sent: [],
      closeCalls: 0,
      onopen: null,
      onmessage: null,
      onerror: null,
      onclose: null,
      send(data) {
        ws.sent.push(JSON.parse(data))
      },
      close() {
        ws.closeCalls++
        if (ws.readyState === 3) return
        ws.readyState = 3
        ws.onclose?.()
      },
      closeWithCode(code) {
        if (ws.readyState === 3) return
        ws.readyState = 3
        ws.onclose?.({ code })
      },
      open() {
        ws.readyState = 1
        ws.onopen?.()
      },
      failToOpen() {
        ws.readyState = 3
        ws.onclose?.()
      },
      emit(frame) {
        ws.onmessage?.({ data: JSON.stringify(frame) })
      },
    }
    sockets.push(ws)
    return ws
  })
  return sockets
}

export const noopApi = () => ({ createChatSocket: vi.fn() })
