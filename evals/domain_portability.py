"""How much of the rule set survives a different domain? Measure, don't guess.

`docs/generality.md` claims 9 of the 15 rules are portable and 6 are
domain-specific. That claim should be checkable, not asserted -- so this script
classifies every rule from the single rule table (`constraints.RULES`) by
looking for domain vocabulary in its own text.

It is deliberately dumb: a keyword scan, not an LLM judgment. Its only job is to
keep the documentation honest when someone edits a rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shotplan.domain.constraints import RULES  # noqa: E402

# Words that only make sense for a Hanfu photoshoot. If a rule's own text
# contains one of these, the rule has to be re-expressed for another domain.
DOMAIN_WORDS = (
    "妆造", "汉服", "拍摄", "光线", "景点", "客流", "服饰", "风格", "客片",
    "makeup", "shoot", "light", "location", "crowd", "style", "costume",
)


# Wording coupling is measurable; logical coupling is a judgement. Both are
# reported, and the judgement is written down here so it can be argued with
# instead of hiding inside a document.
LOGIC_PORTABLE = {
    "TIME_OVERLAP": "时间块不重叠",
    "TIME_OVERRUN": "总时长不超过预约上限（文案举例提到拍摄，逻辑无关）",
    "MISSING_ACTIVITY": "必须存在主活动；改名即可",
    "MAKEUP_ORDER": "准备活动先于主活动，任何流程都成立",
    "TRANSFER_BUFFER": "两个地点之间要有最小移动时间",
    "BUDGET_EXCEEDED": "总成本不超过预算",
    "UNKNOWN_LOCATION": "地点 id 必须存在；换个词表即可",
    "MISSING_REQUIRED_LOCATION": "用户指定的必选项必须出现",
    "AVOIDED_LOCATION": "用户排除的项不得出现",
    "UNKNOWN_SOURCE_REF": "引用必须能在知识库里解析",
    "NO_SOURCE_REF": "至少要有一条可追溯来源",
    "SHOOT_TOO_SHORT": "主活动最小块长，阈值随预约时长缩放",
    "LIGHT_MISUSE": "资源配置的可用窗口内应有主活动（换域即「可用时段」）",
    "NO_ALTERNATIVE": "需要给出备选方案",
}
DOMAIN_LOGIC = {"STYLE_MISMATCH": "风格与场景的匹配是旅拍特有语义"}


def classify(rule: dict) -> tuple[str, list[str]]:
    text = f"{rule.get('rule', '')} {rule.get('why', '')}"
    hits = sorted({word for word in DOMAIN_WORDS if word in text})
    return ("domain-specific" if hits else "portable"), hits


def main() -> int:
    rows = []
    for rule in RULES:
        wording, hits = classify(rule)
        code = rule["code"]
        logic = "domain-specific" if code in DOMAIN_LOGIC else "portable"
        rows.append({"code": code, "severity": rule["severity"],
                     "wording": wording, "domain_words": hits, "logic": logic})
    total = len(rows)
    wording_ok = sum(1 for r in rows if r["wording"] == "portable")
    logic_ok = sum(1 for r in rows if r["logic"] == "portable")
    print(f"{total} rules\n"
          f"  wording without domain words : {wording_ok}/{total} = {wording_ok / total:.0%}\n"
          f"  logic reusable in another domain: {logic_ok}/{total} = {logic_ok / total:.0%}\n")
    for row in rows:
        mark = "  " if row["logic"] == "portable" else "->"
        words = ",".join(row["domain_words"]) or "-"
        print(f"{mark} {row['code']:22s} {row['severity']:5s} "
              f"logic={row['logic']:16s} wording_words={words}")
    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
