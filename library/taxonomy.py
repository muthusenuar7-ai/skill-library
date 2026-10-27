"""Domain / subcategory tree with real published-skill counts (two aggregate queries, no per-row queries)."""
from collections import Counter

from django.db.models import Count

from .models import Category, Skill

LEVEL_WORDS = {"quick", "structured", "advanced", "workflow", "beginner", "intermediate", "expert"}  # complexity labels, not topics


def public_counts():
    pub = Skill.objects.public().order_by()
    by_domain = dict(pub.values_list("category_id").annotate(n=Count("id")))
    by_sub = dict(pub.exclude(subcategory=None).values_list("subcategory_id").annotate(n=Count("id")))
    return by_domain, by_sub


def taxonomy_tree(include_empty=False):
    """Active major domains, each with `.published_total` and `.subs` (active subcategories with published skills)."""
    by_domain, by_sub = public_counts()
    cats = list(Category.objects.filter(is_active=True))
    subs_by_parent = {}
    for c in cats:
        if c.parent_id:
            c.published_total = by_sub.get(c.pk, 0)
            if c.published_total or include_empty:
                subs_by_parent.setdefault(c.parent_id, []).append(c)
    tree = []
    for c in cats:
        if c.parent_id:
            continue
        c.published_total = by_domain.get(c.pk, 0)
        c.subs = subs_by_parent.get(c.pk, [])
        if c.published_total or include_empty:
            tree.append(c)
    return tree


def popular_topics(limit=6):
    """Most common tags across published skills (complexity labels excluded)."""
    counter, display = Counter(), {}
    for tags in Skill.objects.public().order_by().values_list("tags", flat=True):
        for t in tags.split(","):
            t = t.strip()
            key = t.lower()
            if t and key not in LEVEL_WORDS:
                counter[key] += 1
                display.setdefault(key, t)
    return [display[k] for k, _ in counter.most_common(limit)]
