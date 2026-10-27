"""Safe Markdown rendering: Markdown -> HTML -> allow-list sanitiser (nh3).

Raw HTML in a skill, script tags, event handlers, javascript: links and images are all removed
before anything reaches a page. Skill text is never marked safe without passing through here.
"""
import re

import markdown
import nh3

ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em", "b", "i",
    "code", "pre", "blockquote", "table", "thead", "tbody", "tr", "th", "td", "a", "del", "sup", "sub", "span",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "code": {"class"}, "th": {"align"}, "td": {"align"}}
FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)


def strip_front_matter(text):
    return FRONT_MATTER.sub("", text, count=1)


def render_markdown(text):
    html = markdown.markdown(
        strip_front_matter(text or ""),
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html",
    )
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
