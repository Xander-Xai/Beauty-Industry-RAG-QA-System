#!/usr/bin/env python3
"""Extract the real-corpus V1 sources from two official NMPA/State Council files.

This is an extraction tool, not a data generator: it copies text out of two
already-published official documents and records where every fragment came from.

Inputs (all official, none redistributed by this repository in raw form):

* ``--xlsx``  NMPA announcement 2021 No. 62, attachment
  《已使用化妆品原料目录（2021年版）》. The workbook is the authoritative copy.
* ``--regulation-html``  State Council Order No. 727
  《化妆品监督管理条例》 from www.gov.cn.

Outputs are two JSONL files under ``--out-dir``. Every record carries its
``logical_id``, source URL, document version and locator, so the corpus builder
never has to guess provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

REGULATION_LOGICAL_ID = "nmpa/regulation-727"
INGREDIENTS_LOGICAL_ID = "nmpa/used-ingredients-2021"

REGULATION_URL = "https://www.gov.cn/zhengce/zhengceku/2020-06/29/content_5522593.htm"
REGULATION_VERSION = "国务院令第727号（2020-06-16公布，2021-01-01施行）"
INGREDIENTS_URL = "https://www.nmpa.gov.cn/directory/web/nmpa/images/1619771157857007835.xlsx"
INGREDIENTS_ANNOUNCEMENT_URL = "https://www.nmpa.gov.cn/xxgk/ggtg/qtggtg/20210430171722172.html"
INGREDIENTS_VERSION = "国家药监局公告2021年第62号《已使用化妆品原料目录（2021年版）》"

LICENSE_BASIS = (
    "《中华人民共和国著作权法》第五条：法律、法规，国家机关的决议、决定、命令和其他具有立法、"
    "行政、司法性质的文件，及其官方正式译文不适用本法。本项目仅收录官方文档的派生摘录并标注来源。"
)

ARTICLE_RE = re.compile(r"^第([一二三四五六七八九十百零]+)条")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十]+章")

#: Exact standard Chinese names selected from the 2021 catalogue. The selection
#: is deliberately small and fact-shaped: common humectants, emollients,
#: surfactants, preservatives, UV filters, actives, colorants and hair dyes.
#: Keeping exact names (not substrings) is what prevents "积雪草" from pulling in
#: 50 unrelated PEG esters.
SELECTED_INGREDIENTS = (
    "甘油",
    "丙二醇",
    "1,2-丁二醇",
    "1,2-戊二醇",
    "1,2-己二醇",
    "透明质酸钠",
    "水解透明质酸钠",
    "乙酰化透明质酸钠",
    "透明质酸钠交联聚合物",
    "泛醇",
    "烟酰胺",
    "尿囊素",
    "甜菜碱",
    "海藻糖",
    "双丙甘醇",
    "乙基己基甘油",
    "角鲨烷",
    "辛酸/癸酸甘油三酯",
    "异壬酸异壬酯",
    "碳酸二辛酯",
    "聚二甲基硅氧烷",
    "环五聚二甲基硅氧烷",
    "鲸蜡硬脂醇",
    "硬脂酸",
    "甘油硬脂酸酯",
    "辛基十二醇",
    "白池花（LIMNANTHES ALBA）籽油",
    "霍霍巴（SIMMONDSIA CHINENSIS）籽油",
    "牛油果树（BUTYROSPERMUM PARKII）果脂",
    "矿油",
    "矿脂",
    "月桂醇聚醚硫酸酯钠",
    "椰油酰胺丙基甜菜碱",
    "月桂酰肌氨酸钠",
    "椰油酰甘氨酸钠",
    "鲸蜡硬脂基葡糖苷",
    "山梨坦橄榄油酸酯",
    "聚山梨醇酯-20",
    "聚山梨醇酯-60",
    "月桂醇硫酸酯钠",
    "苯氧乙醇",
    "苯甲醇",
    "苯甲酸钠",
    "山梨酸钾",
    "羟苯甲酯",
    "羟苯丙酯",
    "氯苯甘醚",
    "辛甘醇",
    "脱氢乙酸钠",
    "水杨酸",
    "苯扎氯铵",
    "甲基异噻唑啉酮",
    "甲基氯异噻唑啉酮",
    "二氧化钛",
    "氧化锌",
    "二苯酮-3",
    "甲氧基肉桂酸乙基己酯",
    "水杨酸乙基己酯",
    "奥克立林",
    "胡莫柳酯",
    "丁基甲氧基二苯甲酰基甲烷",
    "双-乙基己氧苯酚甲氧苯基三嗪",
    "亚甲基双-苯并三唑基四甲基丁基酚",
    "视黄醇",
    "视黄醇棕榈酸酯",
    "抗坏血酸（维生素C）",
    "抗坏血酸葡糖苷",
    "抗坏血酸磷酸酯钠",
    "熊果苷",
    "α-熊果苷",
    "曲酸",
    "氢醌",
    "苯酚",
    "羟基乙酸",
    "乳酸",
    "苹果酸",
    "积雪草（CENTELLA ASIATICA）提取物",
    "积雪草苷",
    "神经酰胺 NP",
    "神经酰胺 AP",
    "神经酰胺 EOP",
    "神经酰胺 NS/神经酰胺 NG",
    "红没药醇",
    "甘草酸二钾",
    "凝血酸",
    "咖啡因",
    "生育酚（维生素E）",
    "生育酚乙酸酯",
    "CI 77891",
    "CI 77491",
    "CI 77492",
    "CI 77499",
    "CI 19140",
    "CI 15985",
    "CI 42090",
    "CI 16035",
    "CI 17200",
    "CI 45410",
    "对苯二胺",
    "对氨基苯酚",
    "间苯二酚",
    "2-甲基间苯二酚",
    "4-氨基-2-羟基甲苯",
    "苯基甲基吡唑啉酮",
    "氯化钠",
    "柠檬酸",
    "柠檬酸钠",
    "氢氧化钠",
    "三乙醇胺",
    "卡波姆",
    "黄原胶",
    "羟乙基纤维素",
    "膨润土",
    "滑石粉",
    "云母",
    "一氮化硼",
    "乙醇",
    "变性乙醇",
    "香精",
)


class _TextExtractor(HTMLParser):
    """Collect visible text from an HTML document without a parser dependency."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self._parts)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean(value: object) -> str:
    if value is None:
        return "未提供"
    text = str(value).strip()
    return text if text else "未提供"


def parse_regulation(html_path: Path) -> list[dict]:
    """Return one record per article (plus front matter) from the regulation HTML."""
    parser = _TextExtractor()
    parser.feed(html_path.read_text(encoding="utf-8", errors="ignore"))
    text = parser.text

    start = text.find("化妆品监督管理条例\n第一章")
    if start < 0:
        raise ValueError("could not locate the regulation body in the fetched HTML")
    body = text[start:]
    tail = body.find("\n解读\n")
    if tail >= 0:
        body = body[:tail]

    lines = [line.strip() for line in body.split("\n")]
    lines = [line for line in lines if line and not CHAPTER_RE.match(line)]

    records: list[dict] = []
    front_matter = [
        "《化妆品监督管理条例》",
        "中华人民共和国国务院令 第727号",
        "《化妆品监督管理条例》已经2020年1月3日国务院第77次常务会议通过，现予公布，自2021年1月1日起施行。",
        "总　理　　李克强",
        "2020年6月16日",
    ]
    records.append(
        {
            "logical_id": f"{REGULATION_LOGICAL_ID}/front-matter",
            "record_type": "front_matter",
            "source_url": REGULATION_URL,
            "source_document": REGULATION_VERSION,
            "locator": "标题与公布信息",
            "license_basis": LICENSE_BASIS,
            "text": "\n".join(front_matter),
        }
    )

    article_number: str | None = None
    article_lines: list[str] = []
    counter = 0

    def flush() -> None:
        nonlocal counter
        if article_number is None:
            return
        counter += 1
        records.append(
            {
                "logical_id": f"{REGULATION_LOGICAL_ID}/article-{counter:03d}",
                "record_type": "article",
                "source_url": REGULATION_URL,
                "source_document": REGULATION_VERSION,
                "locator": f"第{article_number}条",
                "license_basis": LICENSE_BASIS,
                "text": "\n".join(article_lines),
            }
        )

    for line in lines:
        match = ARTICLE_RE.match(line)
        if match:
            flush()
            article_number = match.group(1)
            article_lines = [line]
        elif article_number is not None:
            article_lines.append(line)
    flush()

    if counter != 80:
        raise ValueError(f"expected 80 articles in the regulation, parsed {counter}")
    return records


def parse_ingredients(xlsx_path: Path) -> list[dict]:
    """Return the notice plus one record per selected catalogue row."""
    from openpyxl import load_workbook

    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    notice_rows = list(sheet.iter_rows(min_row=3, max_row=3, values_only=True))
    notice = str(notice_rows[0][0] or "").strip() if notice_rows else ""
    if not notice:
        raise ValueError("catalogue notice row is empty; the workbook shape changed")
    rows = list(sheet.iter_rows(min_row=5, values_only=True))

    records: list[dict] = [
        {
            "logical_id": f"{INGREDIENTS_LOGICAL_ID}/notice",
            "record_type": "notice",
            "source_url": INGREDIENTS_URL,
            "source_document": INGREDIENTS_VERSION,
            "locator": "说明",
            "license_basis": LICENSE_BASIS,
            "text": "\n".join([INGREDIENTS_VERSION, notice]),
        }
    ]

    wanted = set(SELECTED_INGREDIENTS)
    for row in rows:
        serial, zh_name, inci, rinse_off, leave_on, note = row[:6]
        if zh_name is None or str(zh_name).strip() not in wanted:
            continue
        serial_number = int(serial)
        records.append(
            {
                "logical_id": f"{INGREDIENTS_LOGICAL_ID}/row-{serial_number:06d}",
                "record_type": "ingredient_row",
                "source_url": INGREDIENTS_URL,
                "source_document": INGREDIENTS_VERSION,
                "locator": f"序号{serial_number:05d}",
                "license_basis": LICENSE_BASIS,
                "text": "\n".join(
                    [
                        f"序号：{serial_number:05d}",
                        f"中文名称：{str(zh_name).strip()}",
                        f"INCI名称/英文名称：{_clean(inci)}",
                        f"淋洗类产品最高历史使用量（%）：{_clean(rinse_off)}",
                        f"驻留类产品最高历史使用量（%）：{_clean(leave_on)}",
                        f"备注：{_clean(note)}",
                    ]
                ),
            }
        )
    workbook.close()
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            record.setdefault("_data_class", "PUBLIC_OFFICIAL_DOCUMENT")
            record.setdefault("_carries_real_regulatory_claims", True)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", required=True, help="path to the downloaded 2021 catalogue workbook")
    parser.add_argument("--regulation-html", required=True, help="path to the downloaded regulation HTML")
    parser.add_argument("--out-dir", default="corpus/real_v1/sources")
    args = parser.parse_args(argv)

    xlsx_path = Path(args.xlsx)
    html_path = Path(args.regulation_html)
    out_dir = Path(args.out_dir)

    regulation = parse_regulation(html_path)
    ingredients = parse_ingredients(xlsx_path)

    write_jsonl(out_dir / "regulation_727.jsonl", regulation)
    write_jsonl(out_dir / "used_ingredients_2021.jsonl", ingredients)

    print(f"regulation records: {len(regulation)}")
    print(f"ingredient records: {len(ingredients)}")
    print(f"source xlsx sha256:  {sha256_file(xlsx_path)}")
    print(f"source html sha256:  {sha256_file(html_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
