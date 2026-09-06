import { vi } from 'vitest'

// A WebSocket stand-in for chatClient.js's own createChatSocket: the test
// decides when it opens, what it receives, and when it drops, and can read
// back every frame the client sent. Returns the list every connection
// attempt appends to, newest last.
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

export function turnIdOf(socket, index = 0) {
  return socket.sent.filter((f) => f.type === 'turn')[index].turn_id
}

export const noopApi = () => ({ createChatSocket: vi.fn() })
