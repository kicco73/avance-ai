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

        self.state: str = "NORMAL"

        # Used to detect partial tags before committing them.
        self.pending: str = ""

        self.tag_content: str = ""

    def filter(self, chunk: str) -> str:
        """Returns only the visible text — characters inside recognized
        tags are stripped from output."""
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
        self.pending += char

        if self.open_tag.startswith(self.pending):

            if self.pending == self.open_tag:
                self.pending = ""
                self.tag_content = ""
                self.state = "IN_TAG"

            return ""

        result = self.pending
        self.pending = ""

        return result

    def _process_tag_content(self, char: str) -> None:
        self.pending += char

        if self.close_tag.startswith(self.pending):

            if self.pending == self.close_tag:
                self.pending = ""
                if not self.on_tag:
                    self._default_on_tag(self.tag_content)
                else:
                    self.on_tag(self.tag_content)

                self.state = "NORMAL"

            return

        self.tag_content += self.pending
        self.pending = ""

    def flush(self) -> str:
        """Flush remaining buffered characters, called once input truly
        ends. If still mid-tag (the model opened a tag but never closed
        it), the accumulated content is recovered as visible text rather
        than discarded — `on_tag` never fires in this case."""
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
        """Recovers content stuck behind any tag the model opened but
        never closed, for every tag tracked. Chained rather than run
        independently per tag, since an earlier tag's recovered content
        can itself contain a later tag (e.g. unclosed [audio] swallowing [signals]/[env])."""
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