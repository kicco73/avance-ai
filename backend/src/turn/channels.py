"""Which channels a session may have been opened on.

A channel's name is the name of the skill that is that channel — the key
in its own skill.py, and the segment in its own routes. It used to have a
second name of its own ('native-chat' for webchat, 'whatsapp-chat' for
whatsapp), which meant every sentence about a channel was ambiguous: the
package, or the string in ChatSession.channel? They are the same thing,
so they have the same name.

The list is still here, and still wrong to be here: core cannot say which
channels exist in a build it is not told about. It becomes a contribution
point, each channel naming itself — a build without webchat has no
webchat channel, the same way a build without talk has no audio. Until
then this is the set db/sessions.py validates against, and nothing reads
it to find out what a channel is called: whoever needs that is a channel
and already knows.
"""
from __future__ import annotations

CHANNELS = ("webchat", "whatsapp")
