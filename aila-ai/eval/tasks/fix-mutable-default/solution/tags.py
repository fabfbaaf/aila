"""给文章打标签。"""


def add_tag(tag, tags=None):
    if tags is None:
        tags = []
    if tag not in tags:
        tags.append(tag)
    return tags
