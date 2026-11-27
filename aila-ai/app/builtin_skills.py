"""随艾拉一起发布的任务本事。直接从程序目录读取，独立于用户装的扩展。"""

from pathlib import Path

from .extensions import ExtensionStore, SkillInfo, scan_skills

BUILTIN_SKILLS = Path(__file__).with_name("builtin_skills")


def task_skills(store: ExtensionStore) -> list[SkillInfo]:
    builtin = scan_skills(BUILTIN_SKILLS, "", "")
    names = {skill.name.casefold() for skill in builtin}
    return builtin + [skill for skill in store.skills() if skill.name.casefold() not in names]
