"""The native chat: turning what a browser says into a turn, and the
turn's own stream back into frames on that browser's socket.

Not the socket itself. /api/core/bus is one connection per identity
that the whole SPA reads, so it stays in system/ — this package is what
happens to be listening on it. The socket publishes an inbound frame as
a bus.INPUT_TEXT and never learns whether anyone answered; a build
without src/webchat/ still has the channel, still gets its boot state
and its benchmark progress, and has nothing that can turn a message into
a turn.
"""
