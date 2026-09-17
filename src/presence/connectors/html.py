"""HTML from a board's API, turned into plain text a person can read.

Boards publish a job's text as HTML (Greenhouse escapes that HTML once more).
This keeps paragraph and list breaks, drops scripts and styles, and collapses
the rest of the whitespace. Standard library only; nothing is fetched here.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "template", "head", "svg"}
_BREAK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl", "dt", "dd",
          "table", "tr", "section", "article", "header", "footer", "blockquote", "pre", "hr",
          "figure", "figcaption", "address", "main", "nav", "aside", "form", "fieldset"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" ")
        elif tag in _BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BREAK and tag != "br":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def to_text(html: str) -> str:
    """Plain text from HTML (or from text that is already plain): paragraphs
    separated by a blank line, list items as '- ' lines, entities decoded."""
    raw = str(html or "")
    if "<" not in raw and "&lt;" in raw:
        raw = unescape(raw)  # Greenhouse ships the job's HTML escaped once more
    parser = _Text()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line != "-")).strip()


__all__ = ["to_text"]
