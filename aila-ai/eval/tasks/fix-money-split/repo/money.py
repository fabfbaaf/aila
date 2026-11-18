"""账单平分。"""


def split_bill(total, people):
    share = round(float(total) / people, 2)
    return [share] * people
