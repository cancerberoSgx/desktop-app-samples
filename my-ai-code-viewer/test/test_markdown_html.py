from app.markdown_html import markdown_to_wxhtml


def md(text):
    return markdown_to_wxhtml(text, "#eee")


def test_basic_inline_and_headings():
    out = md("# Title\nSome **bold** and `code`.")
    assert "<h3>Title</h3>" in out  # h1 shifted down for wx.html
    assert "<strong>bold</strong>" in out and "<code>code</code>" in out


def test_raw_html_is_escaped():
    out = md("Use a <div> here.\n\n<script>alert(1)</script>")
    assert "<script" not in out and "<div>" not in out
    assert "&lt;div&gt;" in out


def test_only_web_links_are_clickable():
    out = md("[a](https://example.com) [b](toggle:2) [c](./file.py)")
    assert 'href="https://example.com"' in out
    assert "toggle:" not in out and "file.py" not in out


def test_fenced_code_becomes_bgcolor_table():
    out = md('```py\nx = "<b>"\n```')
    assert '<td bgcolor="#eee"><pre>x = &quot;&lt;b&gt;&quot;</pre></td>' in out


def test_list_after_paragraph_and_two_space_nesting():
    out = md("Steps:\n- a\n  - b\n- c")
    assert "<p>Steps:</p>" in out
    assert "<li>a<ul>\n<li>b</li>" in out


def test_fence_inside_ordered_list():
    out = md("1. run:\n\n   ```bash\n   make\n   ```\n2. done")
    assert "<pre>make</pre>" in out and '<ol start="2">' in out


def test_table_after_paragraph_gets_border():
    out = md("See:\n| A | B |\n|:-|-:|\n| 1 | 2 |")
    assert '<table border="1"' in out and '<td align="right">2</td>' in out
