"""本地文字识别：RapidOCR（PP-OCR 模型，随安装包自带，CPU 上跑），把识别出的文本框按行拼好。

参数是对着「终端里的长报错行」调的：默认参数会把一整行很长的 TypeError 漏掉——
检测时把图缩得太小、文本框扩得不够。屏幕上的字不会倒着，所以关掉方向分类，还快一点。
"""

from typing import Any

import numpy as np

OCR_OPTIONS = {
    "det_limit_side_len": 1600,
    "det_limit_type": "max",
    "det_unclip_ratio": 2.0,
    "use_cls": False,
}


class Ocr:
    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # 开始看屏幕时才加载（约 1 秒）

        self._engine = RapidOCR(**OCR_OPTIONS)

    def lines(self, image: np.ndarray) -> list[str]:
        result, _ = self._engine(image)
        return group_lines(result or [])


def group_lines(result: list[Any]) -> list[str]:
    """文本框 → 一行行文字：按竖直位置分行，同一行里按从左到右拼起来"""
    boxes = []
    for points, text, _score in result:
        ys = [p[1] for p in points]
        top, bottom = min(ys), max(ys)
        boxes.append(((top + bottom) / 2, bottom - top, min(p[0] for p in points), str(text).strip()))
    boxes.sort()

    rows: list[list[tuple[float, float, float, str]]] = []
    for box in boxes:
        if rows:
            row = rows[-1]
            center = sum(b[0] for b in row) / len(row)
            height = max(b[1] for b in row)
            if abs(box[0] - center) < 0.5 * max(height, box[1]):
                row.append(box)
                continue
        rows.append([box])
    return [" ".join(b[3] for b in sorted(row, key=lambda b: b[2]) if b[3]) for row in rows]
