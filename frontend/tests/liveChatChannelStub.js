// The live chat reaches its backend through whatever channel skill the
// registry contributed (see src/liveChatChannel.js); a core test has no
// registry, so it installs the api mocks it already holds. Without this
// the live store gets the null object — which is a build with no browser
// channel at all, and opens no session.
//
// The import is dynamic on purpose: a test that calls vi.resetModules()
// gets a fresh liveChatChannel.js, and a static import here would keep
// installing into the copy from before the reset.
export async function installApiBackedLiveChannel(api) {
  const { installLiveChatChannel } = await import('../src/liveChatChannel.js')
  installLiveChatChannel([{
    getCurrentSession: (sessionId) => api.getCurrentSession(sessionId),
    createSession: () => api.postCreateSession(),
    postAction: (actionName, sessionId) => api.postAction(actionName, sessionId),
    getOperatorState: (sessionId) => api.getOperatorState(sessionId),
    getMessages: (sessionId) => api.getMessages(sessionId),
  }])
}
