"""Audit the report corpus with three cheap algorithms instead of a memory.

The docs directory grows by one file per experiment, and nothing ever removes
one. After a while the corpus answers "what did we try?" but stops answering
"what is true now?" -- two reports disagree and both look authoritative.

The first measurement changed what the other two could mean. 27 of 61 reports
are written by scripts, and `real_model_lab` alone produced nine of them. Those
nine score 0.9 against each other because they share a template, not because
they repeat each other's argument. Comparing them is measuring the generator.
So the corpus is split first, and each half gets the question it can answer:

- Scripted reports: an index question. How many instances of one experiment
  exist, and which one is the current answer?
- Authored reports: a redundancy question, via TF-IDF cosine over character
  bigrams (Chinese has no spaces; bigrams are the dependency-free tokeniser).
  A pair above the threshold is a merge-or-archive decision.

Every file, scripted or not, also gets: outgoing reference check (a backticked
path must still exist) and incoming check (a report nobody links to is a report
nobody reads).

Deterministic by construction -- same corpus, same report -- so the `--check`
gate can hold this page to the files it describes.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DEFAULT_REPORT = DOCS / "report-audit.md"

# The audit describes the corpus; it is not part of it, or every run would
# report itself as a 100%-similar duplicate of the previous run.
SKIP = {"report-audit.md"}

SIM_THRESHOLD = 0.55
MIN_COMPARABLE_TOKENS = 120

# Files that link into docs/ but do not live there: excluding them made every
# report they mention look like an orphan.
EXTERNAL_LINKERS = ("README.md", "CHANGELOG.md")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "out"}

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
FENCE = re.compile(r"```.*?```", re.DOTALL)
BACKTICK_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|md|json|jsonl|html))`")
MD_LINK = re.compile(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)")

# Two ways a report announces the script that produced it.
GEN_PATTERNS = (
    re.compile(r"(?:python\s+-m\s+|python\s+)([\w.]+)\s+--write"),
    re.compile(r"由\s*`([\w./]+)`\s*生成"),
)


def tokens(text: str) -> list[str]:
    """Character bigrams for Chinese, whole words for the Latin alphabet."""
    text = FENCE.sub(" ", text)
    out = [w.lower() for w in WORD.findall(text)]
    for run in CJK_RUN.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


def vectors(corpus: dict[str, list[str]]) -> dict[str, dict[str, float]]:
    """TF-IDF, smoothed. Sparse dicts because most bigrams appear once."""
    n = len(corpus)
    df: Counter[str] = Counter()
    for toks in corpus.values():
        df.update(set(toks))
    result: dict[str, dict[str, float]] = {}
    for name, toks in corpus.items():
        counts = Counter(toks)
        total = sum(counts.values()) or 1
        result[name] = {
            term: (count / total) * (math.log((n + 1) / (df[term] + 1)) + 1.0)
            for term, count in counts.items()
        }
    return result


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(weight * b.get(term, 0.0) for term, weight in a.items())
    norm_a = math.sqrt(sum(w * w for w in a.values()))
    norm_b = math.sqrt(sum(w * w for w in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def pairs_of(vecs: dict[str, dict[str, float]]) -> list[tuple[float, str, str]]:
    names = sorted(vecs)
    pairs = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            pairs.append((cosine(vecs[left], vecs[right]), left, right))
    pairs.sort(reverse=True)
    return pairs


def generator_of(body: str) -> str:
    """Which script wrote this file, if any.

    Scripted reports are instances of one experiment, not independent
    documents: `real_model_lab` has produced nine. Comparing them for textual
    redundancy measures the template they share, which is the kind of false
    finding that makes an audit untrustworthy.
    """
    head = "\n".join(body.splitlines()[:8])
    for pattern in GEN_PATTERNS:
        match = pattern.search(head)
        if match:
            return match.group(1)
    return ""


def path_index() -> set[str]:
    """Every tracked-ish file path, so `pipeline.py` can resolve to its real home.

    Prose writes `validator.py`, not `src/shotplan/agents/validator.py`. Requiring
    the full path would flag 100+ correct references as rot and teach the reader
    to ignore this section.
    """
    index: set[str] = set()
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        index.add(path.relative_to(ROOT).as_posix())
    return index


def worth_checking(ref: str) -> bool:
    """Only explicit repo paths, and only ones that must still be reachable.

    Three classes of backticked path look broken but are not:

    - bare filenames (`rooms.json`): prose uses them for hypothetical or planned
      files, and for narrative like "ADR 0013 pointed at a file that did not
      exist". Requiring these to resolve flags correct sentences.
    - generated output (`evals/out/...`): cleaned between runs by design.
    - abbreviations and globs: handled by `resolves`.

    Narrowing here is what separates a usable section from one the reader learns
    to skip. `tests/test_docs_links.py` already covers clickable links; this
    covers the paths prose *mentions*.
    """
    if ".." in ref or "*" in ref:
        return False
    if not ref.startswith(("docs/", "src/", "scripts/", "tests/", "evals/")):
        return False
    return "/out/" not in ref


def resolves(ref: str, index: set[str]) -> bool:
    """A path is valid if it resolves from the repo root or from docs/."""
    for base in (ROOT, DOCS):
        if (base / ref).exists():
            return True
    return ref in index


def families(names: list[str], min_size: int = 3) -> list[tuple[str, list[str]]]:
    """Group by the leading token of the filename: the `real-model-*` pile."""
    buckets: dict[str, list[str]] = {}
    for name in names:
        stem = name[:-3] if name.endswith(".md") else name
        lead = stem.split("-")[0]
        buckets.setdefault(lead, []).append(name)
    return sorted(
        ((lead, sorted(group)) for lead, group in buckets.items() if len(group) >= min_size),
        key=lambda item: (-len(item[1]), item[0]),
    )


def advice(sim: float) -> str:
    if sim >= 0.90:
        return "几乎重复：留一份，另一份首行标注「已被取代」"
    if sim >= 0.75:
        return "高度重叠：核对结论是否一致，旧的标注日期与状态"
    return "部分重叠：只核对数字是否互相矛盾"


def render(
    files: dict[str, list[str]],
    text_by_name: dict[str, str],
    external: dict[str, str],
) -> str:
    generators = {name: generator_of(body) for name, body in text_by_name.items()}
    auto = {name: gen for name, gen in generators.items() if gen}
    authored = [name for name in files if not generators[name]]

    # Similarity is only asked of documents a person wrote. Two instances of one
    # generator differ by their numbers, not by their argument.
    comparable = {
        name: files[name]
        for name in authored
        if len(files[name]) >= MIN_COMPARABLE_TOKENS
    }
    skipped_small = sorted(set(authored) - set(comparable))

    vecs = vectors(files)
    pairs = pairs_of({name: vecs[name] for name in comparable})

    nearest: dict[str, tuple[float, str]] = {name: (0.0, "") for name in comparable}
    for sim, left, right in pairs:
        for a, b in ((left, right), (right, left)):
            if sim > nearest[a][0]:
                nearest[a] = (sim, b)

    redundant = [(sim, a, b) for sim, a, b in pairs if sim >= SIM_THRESHOLD]
    # Explicit tiebreak on the name: two documents can carry exactly the same
    # marginal information (a symmetric pair shares one similarity value), and
    # without it the order would depend on how the corpus was built.
    unique = sorted(comparable, key=lambda n: (-(1.0 - nearest[n][0]), n))
    groups = families(sorted(files))

    index = path_index()
    broken: list[tuple[str, str]] = []
    inbound: dict[str, int] = {name: 0 for name in files}

    def count_links(body: str, self_name: str | None) -> None:
        for target in MD_LINK.findall(body):
            clean = target.rsplit("/", 1)[-1]
            if clean in inbound and clean != self_name:
                inbound[clean] += 1

    for name in files:
        body = text_by_name[name]
        for ref in sorted(set(BACKTICK_PATH.findall(body))):
            if worth_checking(ref) and not resolves(ref, index):
                broken.append((name, ref))
        count_links(body, self_name=name)
    for body in external.values():
        count_links(body, self_name=None)

    orphans = sorted(
        n
        for n, count in inbound.items()
        if count == 0 and n not in {"ROADMAP.md", "HANDOFF.md"}
    )

    total_bytes = sum(len(body.encode("utf-8")) for body in text_by_name.values())
    mean_marginal = (
        sum(1.0 - nearest[n][0] for n in comparable) / len(comparable) if comparable else 0.0
    )

    by_gen: dict[str, list[str]] = {}
    for name, gen in auto.items():
        by_gen.setdefault(gen, []).append(name)

    lines = [
        "# 报告治理审计",
        "",
        "由 `scripts/audit_reports.py` 生成，勿手改。",
        "",
        "先分两类再问问题：脚本生成的报告问「同一实验有几个实例」，人写的报告问"
        "「是否互相重复」。混在一起比较会把模板相同误判成内容重复。",
        "",
        "## 摘要",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 扫描报告 | {len(files)} 份（{total_bytes / 1024:.0f} KB） |",
        f"| 脚本生成 | {len(auto)} 份，来自 {len(by_gen)} 个生成器 |",
        f"| 人写报告 | {len(authored)} 份 |",
        f"| 人写报告冗余对（≥ {SIM_THRESHOLD:.2f}） | {len(redundant)} 对 |",
        f"| 同名前缀家族（≥ 3 份） | {len(groups)} 个 |",
        f"| 失效引用 | {len(broken)} 处 |",
        f"| 孤儿报告（无入链） | {len(orphans)} 份 |",
        f"| 人写报告平均边际信息量 | {mean_marginal:.3f} |",
        "",
    ]

    lines += ["## 1. 同源实例：报告为什么会堆积", ""]
    multi = sorted(
        ((gen, names) for gen, names in by_gen.items() if len(names) >= 2),
        key=lambda item: -len(item[1]),
    )
    if not multi:
        lines += ["没有生成器产出 2 份以上。", ""]
    else:
        for gen, names in multi:
            lines.append(
                f"`{gen}` 一个脚本产出 **{len(names)} 份**，"
                f"占全部脚本生成报告的 {len(names) / max(len(auto), 1) * 100:.0f}%。"
            )
            lines.append("")
            for name in sorted(names):
                lines.append(f"- `{name}`")
            lines.append("")
        lines.append(
            "这些文件的高相似度是模板的必然结果，**不是冗余**——它们记录了不同的数字。"
            "真正的问题是读者无法从文件名判断哪份是当前结论。"
        )
        lines.append("")
        lines.append("**命名即契约**：`X.md` 是默认输出路径，每次覆盖，代表当前结论；"
                     "`X-<日期>.md` 是一次快照，代表当时的状态。同源实例超过 2 份时，"
                     "新增的一份要能回答「这次与上次的假设有什么不同」，"
                     "否则它只是又一个需要归档的文件。")
        lines.append("")
    singles = len(by_gen) - len(multi)
    if singles:
        lines.append(f"其余 {singles} 个生成器各产出 1 份，属正常。")
        lines.append("")

    lines += ["## 2. 人写报告的冗余对：每一行是一次决定", ""]
    lines.append(
        "不聚类。单链接聚类会把 A~B、B~C 连成 A~C，制造出根本不相似的「一簇」；"
        "每一对独立呈现，读者可以逐行否决。"
    )
    lines.append("")
    if not redundant:
        lines += ["没有超过阈值的对。", ""]
    else:
        lines += ["| 报告 A | 报告 B | 相似度 | 建议 |", "| --- | --- | ---: | --- |"]
        for sim, left, right in redundant[:20]:
            lines.append(f"| `{left}` | `{right}` | {sim:.3f} | {advice(sim)} |")
        lines.append("")
        if len(redundant) > 20:
            lines.append(f"（另有 {len(redundant) - 20} 对未列出，低于表中最后一对。）")
            lines.append("")
        lines.append(
            "**不要直接删**：这些文件记录的是「当时为什么那么判断」。"
            "保留边际信息量最高的一份作为当前结论，其余在首行加一行"
            "`> 已被 <新文件> 取代（<日期>）；本文保留为历史记录`。"
        )
        lines.append("")

    lines += ["## 3. 同主题家族：命名层面的堆积", ""]
    if not groups:
        lines += ["没有 3 份以上的同名家族。", ""]
    else:
        lines += ["| 前缀 | 份数 | 文件 |", "| --- | ---: | --- |"]
        for lead, group in groups:
            joined = "、".join(f"`{name}`" for name in group)
            lines.append(f"| {lead} | {len(group)} | {joined} |")
        lines.append("")
        lines.append(
            "家族大不一定是坏事（脚本生成的就该成组），但人写的部分若也在膨胀，"
            "通常说明同一问题被反复回答。"
        )
        lines.append("")

    lines += ["## 4. 边际信息量排序：人写报告里谁值得先读", ""]
    lines.append(
        f"`1 - 最近邻相似度`，只在 {len(comparable)} 份人写报告内比较"
        f"（< {MIN_COMPARABLE_TOKENS} token 的 {len(skipped_small)} 份只查引用）。"
        "接近 1 表示这份讲的东西别处没有；接近 0 表示它是别人的复述。"
    )
    lines.append("")
    if not comparable:
        lines += ["没有可比的人写报告。", ""]
    else:
        lines.append("| 报告 | 边际信息量 | 最近邻 | 相似度 |")
        lines.append("| --- | ---: | --- | ---: |")
        for name in unique[:10]:
            sim, other = nearest[name]
            lines.append(f"| `{name}` | {1 - sim:.3f} | `{other}` | {sim:.3f} |")
        lines.append("")
        lines.append("末位（信息量最低）：")
        lines.append("")
        for name in unique[-5:]:
            sim, other = nearest[name]
            lines.append(f"- `{name}` — {1 - sim:.3f}，最近邻 `{other}`（{sim:.3f}）")
        lines.append("")

    lines += ["## 5. 失效引用：读者点开就是 404", ""]
    if not broken:
        lines += ["无。", ""]
    else:
        for name, ref in broken:
            lines.append(f"- `{name}` → `{ref}`")
        lines.append("")
        lines.append(
            "门禁 `check_doc_claims` 只校验数字声称，不校验路径；"
            "这一节是唯一会指出「文档指向不存在的文件」的地方。"
        )
        lines.append("")

    lines += ["## 6. 孤儿报告：没人链接，等于没人读", ""]
    if not orphans:
        lines += ["无。", ""]
    else:
        scripted_orphans = [n for n in orphans if n in auto]
        authored_orphans = [n for n in orphans if n not in auto]
        if authored_orphans:
            lines.append(f"**人写但没有入链（{len(authored_orphans)} 份，需要处理）**")
            lines.append("")
            for name in authored_orphans:
                lines.append(f"- `{name}`")
            lines.append("")
            lines.append(
                "处理方式二选一：在 `ROADMAP` 或相关报告里加入**可点击链接**"
                "（反引号里的路径不算，读者点不到）；或承认它不是给读者看的，移出 `docs/`。"
            )
            lines.append("")
        if scripted_orphans:
            lines.append(f"**脚本生成且无入链（{len(scripted_orphans)} 份，属正常）**")
            lines.append("")
            for name in scripted_orphans:
                lines.append(f"- `{name}`")
            lines.append("")
            lines.append(
                "同一实验的历史实例不需要人工索引，它们的问题在第 1 节的实例数量，不在链接。"
            )
            lines.append("")

    lines += [
        "## 口径与已知偏差",
        "",
        "- **token 化**：中文按字符 bigram，英文按 3 字母以上的整词。不用分词器是为了零依赖，"
        "代价是「拍摄/摄影」这类近义词不会被归并，相似度整体偏低（保守，不会误报冗余）。",
        "- **剥离代码块**：比较的是论述而非示例代码。两份文档共享一段代码不算重复。",
        "- **生成器识别**：读前 8 行找 `python -m X --write` 或「由 `X` 生成」。"
        "识别失败会把脚本报告误当人写报告，因此识别到的实例一律不参与相似度比较。",
        "- **路径解析**：只检查 `docs/`、`src/`、`scripts/`、`tests/`、`evals/` 开头的显式路径。"
        "裸文件名（`rooms.json`）多为假设或叙述，`evals/out/` 是被清理的产物，"
        "含 `..` 或 `*` 的写法是简写——这三类报出来只会训练读者忽略本节。"
        "可点击的 Markdown 链接由 `tests/test_docs_links.py` 负责，本节查的是正文提到的路径。",
        "- **入链统计**：`README.md` 与 `CHANGELOG.md` 也算链接来源，否则它们提到的报告"
        "会全部被误判为孤儿。",
        "- **不纳入 ADR**（`docs/adr/`）：决策记录刻意共用模板，相似度不构成冗余信号。",
        "- **交叉引用会推高相似度**：为了让读者不困惑而补的导航句（「同日还有两份更小的采样」）"
        "本身增加了共同词汇——实测中一份报告补完导航后，与另外两份的相似度反而从 0.60 升到 0.64。"
        "所以 0.55–0.70 更像「同一批实验的不同切面」，而不是「两篇在讲同一件事」："
        "先核对数字，再决定是否合并。",
        "- **本文件自身不参与**审计，否则每次运行都会把自己报成 100% 重复。",
        "- **提交动作会改变语料**：语料取的是 git 索引，`git add` 一份新文档就会让统计数 +1。"
        "新增文档后要**先 add、再 `--write`**，否则本地算的是旧份数，CI 算的是新份数——"
        "这个陷阱已经踩过两次（一次是未跟踪的草稿被算进来，一次是刚 add 的新文档没重新生成）。",
        "",
        f"_语料指纹：`{corpus_fingerprint(text_by_name)}`（内容变化时此值必变）_",
        "",
    ]
    return "\n".join(lines)


def corpus_fingerprint(text_by_name: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(text_by_name):
        digest.update(name.encode("utf-8"))
        digest.update(text_by_name[name].encode("utf-8"))
    return digest.hexdigest()[:16]


def tracked_only(corpus: dict[str, str]) -> dict[str, str]:
    """Drop files git does not know about.

    An untracked draft sitting in docs/ made this report claim 62 documents
    locally and 61 on CI, so `--check` passed here and failed there. The corpus
    being audited is the repository, not the working directory.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "docs"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return corpus  # no git available: audit everything and say so
    known = {
        Path(line).name for line in result.stdout.splitlines() if line.endswith(".md")
    }
    return {name: body for name, body in corpus.items() if name in known}


def collect() -> tuple[dict[str, str], dict[str, str]]:
    """The corpus under audit, plus the root-level files that link into it."""
    corpus = {}
    # Sort by name, not by Path: Windows compares paths case-insensitively while
    # Linux does not, so `ROADMAP.md` lands before `evaluation.md` on one machine
    # and after it on the other. Two documents with equal marginal information
    # then swap places, and the generated report stops being reproducible.
    for path in sorted(DOCS.glob("*.md"), key=lambda p: p.name):
        if path.name in SKIP:
            continue
        corpus[path.name] = path.read_text(encoding="utf-8")
    external = {}
    for name in EXTERNAL_LINKERS:
        path = ROOT / name
        if path.exists():
            external[name] = path.read_text(encoding="utf-8")
    return tracked_only(corpus), external


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用相似度与引用完整性审计报告语料")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    text_by_name, external = collect()
    if not text_by_name:
        print("docs/ 下没有 md 文件", file=sys.stderr)
        return 2

    tokens_by_name = {name: tokens(body) for name, body in text_by_name.items()}
    body = render(tokens_by_name, text_by_name, external)

    if args.check:
        if not args.report_path.exists():
            print(f"缺少 {args.report_path}，先运行 --write", file=sys.stderr)
            return 2
        if args.report_path.read_text(encoding="utf-8").strip() != body.strip():
            print(
                "报告治理页已过期：语料变了。运行 "
                "py -3.12 scripts/audit_reports.py --write",
                file=sys.stderr,
            )
            return 2
        print("报告治理页与语料一致", file=sys.stderr)
        return 0

    if args.write:
        args.report_path.write_text(body, encoding="utf-8")
        print(f"wrote {args.report_path} ({len(body)} chars)", file=sys.stderr)
        return 0

    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
