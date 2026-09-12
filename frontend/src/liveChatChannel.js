// How the app's one live chat reaches its own backend: opening or
// resuming a session, reading the operator's view of it, reading its
// messages. What a person *does* in a conversation does not come through
// here — it goes on the socket (see backend docs/BUS.md). Every one of those declares a channel, and only the skill
// that owns the channel can name it — so the core asks here instead,
// and whoever boots the app hands this module whatever the registry
// collected (see composables/useAppBoot.js, the same shape as
// messageNotifier.js, which is a leaf for the same reason: a skill may
// reach back into the store it is contributing to).
//
// The null object is what a build without a browser channel looks like:
// the live chat has nowhere to open a session, so it opens none. Nothing
// here knows which skill would have answered, or that one exists.
const noLiveChatChannel = {
  getCurrentSession: async () => null,
  createSession: async () => null,
  getOperatorState: async () => null,
  getMessages: async () => [],
}

let channel = noLiveChatChannel

export function installLiveChatChannel(contributions) {
  channel = contributions.length > 0 ? contributions[0] : noLiveChatChannel
}

export function liveChatChannel() {
  return channel
}
