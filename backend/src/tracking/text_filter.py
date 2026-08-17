from typing import Callable, Optional
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class Filter(ABC):
    @abstractmethod
    def filter(self, text: str) -> str:
        return text

class StreamingTagFilter(Filter):
    def __init__(
        self,
        open_tag: str,
        close_tag: str,
        on_tag: Optional[Callable[[str], None]] = None
    ) -> None:
        self.open_tag = open_tag
        self.close_tag = close_tag
        
        self.on_tag = on_tag
        
        # Current parser state
        self.state: str = "NORMAL"

        # Temporary buffer used to detect partial tags
        self.pending: str = ""

        # Buffer containing the hidden tag content
        self.tag_content: str = ""

    def filter(self, chunk: str) -> str:
        """
        Process an incoming text chunk and return only visible text.
        Characters inside recognized tags are removed from output.
        """
        output: list[str] = []

        for char in chunk:
            if self.state == "NORMAL":
                visible = self._process_normal(char)
                if visible:
                    output.append(visible)

            elif self.state == "IN_TAG":
                self._process_tag_content(char)

        return "".join(output)

    def _process_normal(self, char: str) -> str:
        """
        Process characters outside tags.

        Detects the beginning of an opening tag.
        """
        self.pending += char

        # The current buffer can still be the beginning of the tag
        if self.open_tag.startswith(self.pending):

            # Full opening tag detected
            if self.pending == self.open_tag:
                self.pending = ""
                self.tag_content = ""
                self.state = "IN_TAG"

            return ""

        # The buffered characters are not a tag prefix
        result = self.pending
        self.pending = ""

        return result

    def _process_tag_content(self, char: str) -> None:
        """
        Process characters inside a tag.

        Detects the closing tag incrementally.
        """
        self.pending += char

        # The current buffer can still be the beginning of the closing tag
        if self.close_tag.startswith(self.pending):

            # Full closing tag detected
            if self.pending == self.close_tag:
                self.pending = ""
                if not self.on_tag:
                    self._default_on_tag(self.tag_content)
                else:
                    self.on_tag(self.tag_content)
                
                self.state = "NORMAL"

            return

        # The buffer is not part of a closing tag
        self.tag_content += self.pending
        self.pending = ""

    def flush(self) -> str:
        """
        Flush remaining buffered characters — called once the input
        truly ends (see ConcatTagFilter.flush/_receive_ai_stream_and_
        sendreply). If still mid-tag at that point, the model opened
        this tag but never closed it (a real, observed failure mode,
        e.g. a truncated/cut-off reply) — recovers whatever was
        accumulated (both the confirmed tag_content and any trailing
        false-start toward the close tag still sitting in `pending`) as
        visible text instead of silently discarding it, so a malformed
        reply degrades to leaking that text rather than losing the rest
        of the message along with it. `on_tag`'s own callback never
        fires in this case (the close tag was never actually seen), so
        callers reading e.g. an audio-tag's `tag_content` directly after
        this must expect it to be reset to "" here too.
        """
        if self.state == "NORMAL":
            remaining = self.pending
            self.pending = ""
            return remaining

        recovered = self.tag_content + self.pending
        self.tag_content = ""
        self.pending = ""
        self.state = "NORMAL"
        return recovered

    def _default_on_tag(self, content: str) -> None:
        """
        Default callback executed when a tag is found.
        """
        pass

class TagFilter(StreamingTagFilter):

    def filter(self, text: str) -> str:
        return super().filter(text) + self.flush()

class ConcatTagFilter(StreamingTagFilter):
    def __init__(self, *tag_names, **on_tag):
        self.tags = {
            tag_name: StreamingTagFilter(f'[{tag_name}]', f'[/{tag_name}]', on_tag=on_tag.get(tag_name))
            for tag_name in tag_names
        }

    def filter(self, text: str) -> str:
        for filter in self.tags.values():
            text = filter.filter(text)
        return text

    def flush(self) -> str:
        """Call once the true end of the input has been reached (see
        StreamingTagFilter.flush's own docstring) — recovers any content
        stuck behind a tag the model opened but never closed, for every
        tag this filter tracks. filter() alone (the streaming path, one
        call per chunk) never calls this on its own: a filter still
        mid-tag after one chunk is normal — more of the tag's own
        content, or its close, may simply be in the next chunk.

        Chained the same way filter_and_flush already is, not one
        independent flush() per tag: an earlier tag's own recovered
        content can itself contain a later tag (e.g. an unclosed
        [audio] swallows the real [signals]/[env] tags right along with
        the visible reply — see its own flush()) — piping each filter's
        output into the next one's filter()+flush() lets a later filter
        still recognize and strip its own tag out of what an earlier one
        just recovered, exactly like a fresh, complete text would."""
        text = ""
        for filter in self.tags.values():
            text = filter.filter(text) + filter.flush()
        return text

    def filter_and_flush(self, text: str) -> str:
        for filter in self.tags.values():
            text = filter.filter(text) + filter.flush()
        return text

def main() -> None:
    """
    Interactive test program.
    """

    tag_filter = StreamingTagFilter(open_tag="A", close_tag="B")

    print("Streaming tag filter test")
    print("Type text chunks. Use '[AUDIO]...[/AUDIO]' for hidden content.")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            chunk = input("> ")

        except EOFError:
            break

        if chunk.lower() == "exit":
            break

        visible = tag_filter.filter(chunk)

        print(f"OUTPUT: {visible!r}")


if __name__ == "__main__":
    main()