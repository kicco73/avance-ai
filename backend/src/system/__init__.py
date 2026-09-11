"""The platform's own plumbing: the parts every other package stands on
and no build can leave out.

The Bus is here because everything reaches everything else through it.
The broadcaster and the shared websocket are here because they are the
two ways the running system talks outward, and neither belongs to
whatever happens to be listening — the broadcaster publishes
ui.progress and never learns who reads it, and /ws/notifications is
one connection per identity that the whole SPA uses, not the chat's.

The three loose modules that came with them (logging_factory, session,
service_error) are leaves: no imports of their own, imported by nearly
everything. They sat at the top of src/ for no reason other than having
been written first.

NOTE — the frontend itself is a skill, and this package still contains
part of it. `/ws/notifications` is a browser connection; ws_turn and
ws_human_relay are a turn and an operator handover carried over that
connection. They are here rather than in a package of their own because
the whole SPA depends on the channel today (useAppBoot.js,
useTestExecutionTree.js, api/chat.js), not just the chat window. When
the native interface is dropped, these three go with it: the inbound
frame becomes a bus.INPUT_TEXT publication like any other, and nothing
in this package knows what a turn is.
"""
