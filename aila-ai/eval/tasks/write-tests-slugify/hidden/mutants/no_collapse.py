"""把标题变成网址里用的短名字。"""

import re


def slugify(text):
    text = text.strip().lower()
    text = re.sub(r"[\W_]", "-", text)
    return text.strip("-")
