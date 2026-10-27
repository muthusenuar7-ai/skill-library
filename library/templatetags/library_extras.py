from django import template
from django.utils.safestring import mark_safe

from library.markdown_utils import render_markdown

register = template.Library()


@register.filter
def markdown_safe(value):
    return mark_safe(render_markdown(value))  # noqa: S308 - sanitised by nh3 inside render_markdown


@register.filter
def filesize(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    """Current GET parameters with some replaced (None removes a key)."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


EMOJI = {
    "HR": "👥", "Finance": "💳", "Sales": "📈", "Marketing": "📣", "Data Analysis": "📊",
    "Excel": "🟩", "Power BI": "📉", "Coding": "💻", "AI Automation": "⚡", "Research": "🔬",
    "Project Management": "🗂", "Operations": "⚙️", "Customer Service": "💬", "Management": "🎯",
    "Consulting": "🧭", "Training & Learning": "🎓", "Presentations": "🖥️", "Documents": "📄",
    "Career": "💼", "LinkedIn": "🔗", "Email & Communication": "✉️", "Meetings": "📅",
    "General Productivity": "✨", "AI Everyday Tasks": "🤖", "Everyday Office": "🗃️",
}


@register.filter
def category_emoji(name):
    return EMOJI.get(name, "📘")


ICON_KEYWORDS = [
    (("excel", "sheet", "formula"), "table"), (("power bi", "dax", "dashboard", "report"), "chart"), (("data", "analy", "kpi", "research"), "chart"),
    (("hr", "people", "recruit", "career", "customer", "service", "support"), "users"), (("finance", "budget", "account", "sales"), "coins"),
    (("market", "linkedin", "social", "brand"), "megaphone"), (("code", "coding", "develop", "automation", "ai ", "agent"), "code"),
    (("operation", "project", "process", "manage"), "gear"), (("email", "communication", "meeting", "document", "present", "writing", "office"), "doc"),
    (("train", "learn", "education", "consult"), "compass"),
]


@register.filter
def domain_icon(name):
    """Name of an icon in static/img/icons.svg chosen from the category name (falls back to a neutral folder)."""
    low = f" {str(name).lower()} "
    for words, icon in ICON_KEYWORDS:
        if any(w in low for w in words):
            return icon
    return "folder"
