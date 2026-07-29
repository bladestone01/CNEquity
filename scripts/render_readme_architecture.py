#!/usr/bin/env python3
"""Render a layered architecture diagram for README (docs/assets/*.png).

Requires Pillow. Regenerates:

  docs/assets/architecture-overview.zh.png
  docs/assets/architecture-overview.en.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"

# Canvas (logical); rendered at 2× for retina.
W, H = 980, 640
SCALE = 2

BG = (248, 250, 252)
MUTED = (100, 116, 139)
LINE = (203, 213, 225)

SRC_FILL = (239, 246, 255)
SRC_BORDER = (147, 197, 253)
SRC_INK = (30, 64, 175)

SRC2_FILL = (236, 254, 255)
SRC2_BORDER = (103, 232, 249)
SRC2_INK = (14, 116, 144)

ENG_FILL = (238, 242, 255)
ENG_BORDER = (165, 180, 252)
ENG_INK = (67, 56, 202)

LAKE_FILL = (240, 253, 244)
LAKE_BORDER = (134, 239, 172)
LAKE_INK = (21, 128, 61)

Q_FILL = (255, 247, 237)
Q_BORDER = (253, 186, 116)
Q_INK = (194, 65, 12)

PRIMARY = ["tdx_protocol", "eastmoney", "sina", "cninfo"]
SUPPLEMENT = ["baostock", "akshare", "ths", "sw", "cni", "macro"]

COPY = {
    "zh": {
        "out": "architecture-overview.zh.png",
        "primary_label": "主源 / 日更",
        "supplement_label": "回填 / 结构 / 宏观",
        "engine_title": "asl run daily",
        "engine_sub": "编排  ·  水位  ·  失败重试  ·  质量审计",
        "lake_stages": ["staging", "curated", "derived"],
        "lake_note": "Parquet  ·  行级 source / data_version / fetched_at  ·  + calendar seeds",
        "consumers": [
            ("Python load() API", "复权 / universe / PIT"),
            ("DuckDB 视图 / SQL", "本地查询，无需服务端"),
            ("Polars 直读 Parquet", "研究流水线友好"),
        ],
        "layer_labels": ("数据源", "编排引擎", "数据湖", "消费侧"),
    },
    "en": {
        "out": "architecture-overview.en.png",
        "primary_label": "primary / daily",
        "supplement_label": "backfill / structure / macro",
        "engine_title": "asl run daily",
        "engine_sub": "orchestrate  ·  watermarks  ·  retry  ·  quality audit",
        "lake_stages": ["staging", "curated", "derived"],
        "lake_note": "Parquet  ·  row-level source / data_version / fetched_at  ·  + calendar seeds",
        "consumers": [
            ("Python load() API", "adjust / universe / PIT"),
            ("DuckDB views / SQL", "local query, no server"),
            ("Polars on Parquet", "research-pipeline friendly"),
        ],
        "layer_labels": ("Sources", "Orchestration", "Lake", "Consumers"),
    },
}


def _font(
    size: int,
    *,
    bold: bool = False,
    cjk: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[tuple[str, int]] = []
    if cjk:
        candidates += [
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
            ("/System/Library/Fonts/STHeiti Light.ttc", 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        ]
    elif bold:
        candidates += [
            ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
            ("/Library/Fonts/Arial Bold.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ]
    candidates += [
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/Library/Fonts/Arial.ttf", 0),
        ("/System/Library/Fonts/Helvetica.ttc", 0),
        ("/System/Library/Fonts/PingFang.ttc", 0),
    ]
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _round_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int],
    radius: int = 14,
    width: int = 2,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _center_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    x0, y0, x1, y1 = xy
    tw, th = _text_size(draw, text, font)
    draw.text(((x0 + x1 - tw) / 2, (y0 + y1 - th) / 2), text, font=font, fill=fill)


def _arrow_down(
    draw: ImageDraw.ImageDraw,
    x: int,
    y0: int,
    y1: int,
    *,
    color: tuple[int, int, int] = LINE,
) -> None:
    draw.line((x, y0, x, y1 - 8), fill=color, width=3)
    draw.polygon([(x, y1), (x - 6, y1 - 10), (x + 6, y1 - 10)], fill=color)


def _arrow_right(
    draw: ImageDraw.ImageDraw,
    x0: int,
    x1: int,
    y: int,
    *,
    color: tuple[int, int, int] = (34, 197, 94),
) -> None:
    draw.line((x0, y, x1 - 8, y), fill=color, width=3)
    draw.polygon([(x1, y), (x1 - 10, y - 6), (x1 - 10, y + 6)], fill=color)


def _draw_source_row(
    draw: ImageDraw.ImageDraw,
    *,
    names: list[str],
    y0: int,
    h: int,
    pad_x: int,
    content_w: int,
    gap: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    border: tuple[int, int, int],
    ink: tuple[int, int, int],
    s: int,
) -> list[tuple[int, int, int, int]]:
    n = len(names)
    src_w = (content_w - gap * (n - 1)) // n
    boxes: list[tuple[int, int, int, int]] = []
    for i, name in enumerate(names):
        x0 = pad_x + i * (src_w + gap)
        box = (x0, y0, x0 + src_w, y0 + h)
        boxes.append(box)
        _round_rect(draw, box, fill=fill, outline=border, radius=12 * s // 2, width=2)
        _center_text(draw, box, name, font, ink)
    return boxes


def render(locale: str) -> Path:
    cfg = COPY[locale]
    img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    draw = ImageDraw.Draw(img)
    s = SCALE

    def S(v: int | float) -> int:
        return int(v * s)

    cjk = locale == "zh"
    font_label = _font(S(12), bold=True, cjk=cjk)
    font_src = _font(S(14), bold=True, cjk=False)
    font_title = _font(S(22), bold=True, cjk=False)
    font_sub = _font(S(15), cjk=cjk)
    font_stage = _font(S(18), bold=True, cjk=False)
    font_note = _font(S(12), cjk=cjk)
    font_card = _font(S(16), bold=True, cjk=cjk)
    font_card_sub = _font(S(12), cjk=cjk)
    font_row = _font(S(11), bold=True, cjk=cjk)

    pad_x = S(28)
    content_w = S(W) - 2 * pad_x
    gap = S(10)

    def _layer_chip(
        text: str,
        *,
        x: int,
        y: int,
        fill: tuple[int, int, int],
        border: tuple[int, int, int],
        ink: tuple[int, int, int],
    ) -> None:
        tw, th = _text_size(draw, text, font_label)
        box = (x, y, x + tw + S(16), y + th + S(10))
        _round_rect(draw, box, fill=fill, outline=border, radius=S(8), width=S(1))
        draw.text((x + S(8), y + S(5)), text, font=font_label, fill=ink)

    # Primary sources
    src1_y0, src_h = S(52), S(44)
    _layer_chip(
        cfg["layer_labels"][0],
        x=pad_x,
        y=src1_y0 - S(28),
        fill=SRC_FILL,
        border=SRC_BORDER,
        ink=SRC_INK,
    )
    draw.text(
        (pad_x + S(90), src1_y0 - S(22)),
        cfg["primary_label"],
        font=font_row,
        fill=MUTED,
    )
    primary_boxes = _draw_source_row(
        draw,
        names=PRIMARY,
        y0=src1_y0,
        h=src_h,
        pad_x=pad_x,
        content_w=content_w,
        gap=gap,
        font=font_src,
        fill=SRC_FILL,
        border=SRC_BORDER,
        ink=SRC_INK,
        s=s,
    )

    # Supplement sources
    src2_y0 = src1_y0 + src_h + S(28)
    draw.text(
        (pad_x, src2_y0 - S(18)),
        cfg["supplement_label"],
        font=font_row,
        fill=MUTED,
    )
    supplement_boxes = _draw_source_row(
        draw,
        names=SUPPLEMENT,
        y0=src2_y0,
        h=src_h,
        pad_x=pad_x,
        content_w=content_w,
        gap=gap,
        font=font_src,
        fill=SRC2_FILL,
        border=SRC2_BORDER,
        ink=SRC2_INK,
        s=s,
    )

    eng_y0, eng_h = src2_y0 + src_h + S(36), S(72)
    eng_box = (pad_x, eng_y0, pad_x + content_w, eng_y0 + eng_h)
    # Arrows from both rows into engine (subset to avoid clutter)
    for box in primary_boxes:
        cx = (box[0] + box[2]) // 2
        _arrow_down(draw, cx, box[3] + S(2), eng_y0 - S(2), color=SRC_BORDER)
    # One arrow from center of supplement row
    mid_sup = (supplement_boxes[0][0] + supplement_boxes[-1][2]) // 2
    _arrow_down(draw, mid_sup, src2_y0 + src_h + S(2), eng_y0 - S(2), color=SRC2_BORDER)

    _round_rect(draw, eng_box, fill=ENG_FILL, outline=ENG_BORDER, radius=S(16), width=S(2))
    tw, th = _text_size(draw, cfg["engine_title"], font_title)
    sw, sh = _text_size(draw, cfg["engine_sub"], font_sub)
    stack_h = th + S(8) + sh
    ty = eng_y0 + (eng_h - stack_h) // 2
    draw.text(
        (pad_x + (content_w - tw) // 2, ty),
        cfg["engine_title"],
        font=font_title,
        fill=ENG_INK,
    )
    draw.text(
        (pad_x + (content_w - sw) // 2, ty + th + S(8)),
        cfg["engine_sub"],
        font=font_sub,
        fill=MUTED,
    )
    _layer_chip(
        cfg["layer_labels"][1],
        x=pad_x + S(12),
        y=eng_y0 + S(8),
        fill=(255, 255, 255),
        border=ENG_BORDER,
        ink=ENG_INK,
    )

    mid_x = pad_x + content_w // 2
    lake_y0, lake_h = eng_y0 + eng_h + S(32), S(88)
    _arrow_down(draw, mid_x, eng_y0 + eng_h + S(4), lake_y0 - S(2), color=ENG_BORDER)

    lake_box = (pad_x, lake_y0, pad_x + content_w, lake_y0 + lake_h)
    _round_rect(draw, lake_box, fill=LAKE_FILL, outline=LAKE_BORDER, radius=S(16), width=S(2))

    stages = cfg["lake_stages"]
    stage_gap = S(28)
    stage_w = S(150)
    stages_total = stage_w * len(stages) + stage_gap * (len(stages) - 1)
    sx = pad_x + (content_w - stages_total) // 2
    stage_cy = lake_y0 + S(34)
    for i, stage in enumerate(stages):
        x0 = sx + i * (stage_w + stage_gap)
        box = (x0, lake_y0 + S(14), x0 + stage_w, lake_y0 + S(54))
        _round_rect(draw, box, fill=(255, 255, 255), outline=LAKE_BORDER, radius=S(10), width=S(2))
        _center_text(draw, box, stage, font_stage, LAKE_INK)
        if i < len(stages) - 1:
            _arrow_right(
                draw,
                x0 + stage_w + S(4),
                x0 + stage_w + stage_gap - S(4),
                stage_cy,
                color=LAKE_BORDER,
            )

    nw, nh = _text_size(draw, cfg["lake_note"], font_note)
    draw.text(
        (pad_x + (content_w - nw) // 2, lake_y0 + lake_h - nh - S(12)),
        cfg["lake_note"],
        font=font_note,
        fill=MUTED,
    )
    _layer_chip(
        cfg["layer_labels"][2],
        x=pad_x + S(12),
        y=lake_y0 + S(8),
        fill=(255, 255, 255),
        border=LAKE_BORDER,
        ink=LAKE_INK,
    )

    cons_y0, cons_h = lake_y0 + lake_h + S(36), S(100)
    _arrow_down(draw, mid_x, lake_y0 + lake_h + S(4), cons_y0 - S(2), color=LAKE_BORDER)

    consumers = cfg["consumers"]
    cg = S(16)
    cw = (content_w - cg * (len(consumers) - 1)) // len(consumers)
    for i, (title, sub) in enumerate(consumers):
        x0 = pad_x + i * (cw + cg)
        box = (x0, cons_y0, x0 + cw, cons_y0 + cons_h)
        _round_rect(draw, box, fill=Q_FILL, outline=Q_BORDER, radius=S(14), width=S(2))
        tw, th = _text_size(draw, title, font_card)
        sw, sh = _text_size(draw, sub, font_card_sub)
        stack = th + S(10) + sh
        ty = cons_y0 + (cons_h - stack) // 2
        draw.text((x0 + (cw - tw) // 2, ty), title, font=font_card, fill=Q_INK)
        draw.text((x0 + (cw - sw) // 2, ty + th + S(10)), sub, font=font_card_sub, fill=MUTED)

    _layer_chip(
        cfg["layer_labels"][3],
        x=pad_x,
        y=cons_y0 - S(26),
        fill=Q_FILL,
        border=Q_BORDER,
        ink=Q_INK,
    )

    draw.rounded_rectangle(
        (S(8), S(8), S(W) - S(8), S(H) - S(8)),
        radius=S(20),
        outline=(226, 232, 240),
        width=S(2),
    )

    out = ASSETS / cfg["out"]
    ASSETS.mkdir(parents=True, exist_ok=True)
    final = img.resize((W, H), Image.Resampling.LANCZOS)
    final.save(out, "PNG", optimize=True)
    return out


def main() -> None:
    for locale in ("zh", "en"):
        path = render(locale)
        print(f"wrote {path.relative_to(ROOT)} ({W}x{H})")


if __name__ == "__main__":
    main()
