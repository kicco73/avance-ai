import { readonly, ref } from 'vue'

const channel = ref(null)

export const liveChatChannel = readonly(channel)

export function installChatChannel(contributed) {
  channel.value = contributed ?? null
}
