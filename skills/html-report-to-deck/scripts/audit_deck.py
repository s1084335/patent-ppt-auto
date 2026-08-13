"""輸出前品質閘門：檢查字級、圖表重複與頁數。

⚠ 圖片去重必須用**內容雜湊**：python-pptx 讀到的 `image.filename` 在 pptx 內會被統一
   改名成 image.png，用檔名判斷會得到「14 張全部重複」的假結果。

用法：python audit_deck.py <pptx> [允許的字級,預設 24,16]
回傳碼：0 = 全部通過；1 = 有違規（字級不符或圖表重複）
"""
from __future__ import annotations

import sys
from collections import Counter

from pptx import Presentation


def main() -> int:
    prs = Presentation(sys.argv[1])
    allowed = {float(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "24,16").split(",")}
    sizes, imgs, bad = Counter(), Counter(), []

    for i, sl in enumerate(prs.slides, 1):
        for sh in sl.shapes:
            if sh.__class__.__name__ == "Picture":
                imgs[sh.image.sha1] += 1
            if not sh.has_text_frame:
                continue
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if not r.text.strip():
                        continue
                    pt = r.font.size.pt if r.font.size else None
                    sizes[pt] += 1
                    if pt not in allowed:
                        bad.append((i, pt, r.text[:20]))

    dups = {k: v for k, v in imgs.items() if v > 1}
    print("字級分布：", dict(sizes))
    print(f"不符字級的文字 run：{len(bad)}")
    for b in bad[:10]:
        print("   P%d  %s  %s" % b)
    print(f"圖片總數 {sum(imgs.values())}／不重複 {len(imgs)}")
    print("重複使用的圖表：", f"{len(dups)} 組" if dups else "無")
    print("頁數：", len(prs.slides._sldIdLst))
    return 1 if (bad or dups) else 0


if __name__ == "__main__":
    raise SystemExit(main())
