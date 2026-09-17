"""Board HTML to the plain text a person reads: breaks kept, noise dropped."""

from presence.connectors.html import to_text


def test_entities_and_once_escaped_html():
    assert to_text("Tom &amp; Jerry &lt;3 &nbsp;ok") == "Tom & Jerry <3 ok"
    greenhouse = ("&lt;div&gt;&lt;p&gt;We build &amp;amp; ship.&lt;/p&gt;&lt;ul&gt;"
                  "&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;&lt;/div&gt;")
    assert to_text(greenhouse) == "We build & ship.\n\n- Python\n- SQL"


def test_paragraphs_lists_and_headings_keep_their_breaks():
    html = ("<h2>About</h2><p>First  para.</p>\n\n<p>Second<br>line</p>"
            "<ul><li>a</li><li>b</li></ul><ol><li>c</li></ol>")
    assert to_text(html) == "About\n\nFirst para.\n\nSecond\nline\n\n- a\n- b\n\n- c"
    assert to_text("<li>x</li><li>y</li>") == "- x\n- y"  # Lever's lists come without a <ul>


def test_scripts_styles_dropped_and_whitespace_collapsed():
    html = ("<style>p{color:red}</style><script>alert(1)</script><p>  Hello \t world </p>"
            "<div>\n\n\n</div><p>End</p>")
    assert to_text(html) == "Hello world\n\nEnd"
    assert to_text("") == "" and to_text(None) == ""
    assert to_text("plain text, no tags") == "plain text, no tags"
