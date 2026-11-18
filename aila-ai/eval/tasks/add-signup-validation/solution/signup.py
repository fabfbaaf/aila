"""注册表单。"""

import re


def validate_signup(form):
    get = lambda k: str(form.get(k, "") or "")  # noqa: E731
    errors = {}
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", get("username")):
        errors["username"] = "用户名要 3 到 20 个字母、数字或下划线"
    email = get("email")
    local, _, domain = email.partition("@")
    if email.count("@") != 1 or not local or "." not in domain:
        errors["email"] = "邮箱格式不对"
    password = get("password")
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        errors["password"] = "密码至少 8 位，要同时有字母和数字"
    if get("confirm") != password:
        errors["confirm"] = "两次输入的密码不一样"
    return errors
