from __future__ import annotations

NOT_LINKED = (
    "This number isn't linked to any account. "
    "Open your profile on the web and add this WhatsApp number."
)
NOT_REGISTERED = "Your account isn't registered yet: sign in on the web and accept the terms."
REGISTERED = "You're all set! Registration complete — you can start chatting now."
TERMS_ACCEPTED = "Thanks — terms accepted. You can continue chatting."
ACCEPT_TERMS_LABEL = "Accept"
PAUSED = "This project is currently paused. Please try again later."
UNSUPPORTED = "For now I can only read text messages."
NO_CHAT_STATE = "The conversation doesn't accept messages at this point. Continue from the web."
INVALID_ACTION = "That option is no longer available. Please choose one of these instead."
BUSY = "Please wait a moment and try again."
SESSION_TAKEN_OVER = "This conversation continued somewhere else. Send another message to keep chatting here."
TECHNICAL_PROBLEM = (
    "We apologize for the inconvenience — a technical problem occurred. "
    "Please try again in a moment."
)
TURN_PROBLEM = "There was a problem processing your message. Please try again."
NO_CHAT_HERE = "This number can't hold a conversation right now. Please try again later."
UNSUPPORTED_AUDIO = "I can't listen to voice notes yet — please type your message."
AUDIO_NOT_UNDERSTOOD = "I couldn't make out that voice note. Could you repeat it, or type it?"
OPTIONS_PROMPT = "What would you like to do?"


class Notice(object):

    def __init__(self, text: str) -> None:
        self.text = text

    async def delivered(self, conversation) -> None:
        await conversation.notify(self.text)


class NoticeKeepingChoices(Notice):

    async def delivered(self, conversation) -> None:
        await conversation.notify_keeping_choices(self.text)


class TermsNotice(object):

    async def delivered(self, conversation) -> None:
        await conversation.ask_to_accept_terms()


_UNEXPECTED = Notice(TURN_PROBLEM)

_FOR_CODE = {
    "state_not_chat": NoticeKeepingChoices(NO_CHAT_STATE),
    "action_unavailable": NoticeKeepingChoices(INVALID_ACTION),
    "session_channel_mismatch": Notice(SESSION_TAKEN_OVER),
    "session_superseded": Notice(SESSION_TAKEN_OVER),
    "turn_in_progress": Notice(BUSY),
    "project_unavailable": Notice(PAUSED),
    "session_closed": Notice(TECHNICAL_PROBLEM),
    "session_not_found": Notice(TECHNICAL_PROBLEM),
    "empty_message": Notice(UNSUPPORTED),
}

_FOR_REASON = {
    "terms": TermsNotice(),
    "paused": Notice(PAUSED),
    "no_project": Notice(TECHNICAL_PROBLEM),
    "no_channel": Notice(NO_CHAT_HERE),
}


def for_code(code: str | None) -> Notice:
    return _FOR_CODE.get(code or "", _UNEXPECTED)


def for_reason(reason: str | None):
    return _FOR_REASON.get(reason or "", _UNEXPECTED)
