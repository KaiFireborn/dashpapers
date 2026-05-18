import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Layout ────────────────────────────────────────────────────────────────────
BOARD_PADDING   = 18
COLUMN_WIDTH    = 240
COLUMN_GAP      = 12
COLUMN_PADDING  = 8
COLUMN_HEADER_H = 26
CARD_PADDING    = 6
CARD_GAP        = 5
TAG_H           = 14
TAG_PADDING_X   = 4
TAG_GAP         = 3
TASK_LINE_H     = 14
TASK_GAP        = 2
TITLE_H         = 34


# ── Palette (e-ink: white bg, dark ink) ───────────────────────────────────────
BG_BOARD        = (255, 255, 255)
BG_COLUMN       = (238, 238, 238)
BG_CARD         = (255, 255, 255)
BG_TAG          = (210, 210, 210)

TEXT_TITLE      = (0,   0,   0  )
TEXT_COLUMN     = (40,  40,  40 )
TEXT_CARD       = (30,  30,  30 )
TEXT_MUTED      = (110, 110, 110)
TEXT_TAG        = (50,  50,  50 )

BORDER_COLUMN   = (190, 190, 190)
BORDER_CARD     = (210, 210, 210)
BORDER_CHECKBOX = (130, 130, 130)


# ── I/O helpers ───────────────────────────────────────────────────────────────

def load_settings():
    path = Path("settings.json")
    if not path.exists():
        print("settings.json not found"); sys.exit(1)
    with open(path) as f:
        return json.load(f)

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def safe_filename(name):
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()


# ── Font loading ──────────────────────────────────────────────────────────────

def load_fonts():
    try:
        B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return {
            "title":  ImageFont.truetype(B, 17),
            "bold":   ImageFont.truetype(R, 11),   # regular weight for card titles
            "small":  ImageFont.truetype(R,  9),
        }
    except Exception:
        f = ImageFont.load_default()
        return {"title": f, "bold": f, "small": f}


# ── Text helpers ──────────────────────────────────────────────────────────────

def tw(draw, text, font):
    return draw.textlength(text, font=font)

def wrap_text(draw, text, font, max_w):
    """Word-wrap text to fit max_w pixels; returns list of lines."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        cand = (cur + " " + word).strip()
        if tw(draw, cand, font) <= max_w:
            cur = cand
        else:
            if cur: lines.append(cur)
            cur = word
    if cur: lines.append(cur)
    return lines or [""]

def strip_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()

def clamp_desc_rows(draw, text, font, max_w, max_rows):
    """Wrap text and return at most max_rows lines (last line gets '...' if truncated).
    max_rows=0 means hide the description entirely."""
    if max_rows == 0 or not text:
        return []
    lines = wrap_text(draw, text, font, max_w)
    if len(lines) <= max_rows:
        return lines
    truncated = lines[:max_rows]
    truncated[-1] = truncated[-1].rstrip() + "..."
    return truncated


# ── Due-date formatting ───────────────────────────────────────────────────────

def format_due_date(due_str):
    """Return (label, is_late) from an ISO date string."""
    if not due_str:
        return None, False
    try:
        due   = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        hours = (due - datetime.now(timezone.utc)).total_seconds() / 3600
        late  = hours < 0
        h     = abs(hours)
        if h < 1:    label = "<1h ago"         if late else "in <1h"
        elif h < 24: label = f"{int(h)}h ago"  if late else f"in {int(h)}h"
        else:        label = f"{int(h/24)}d ago" if late else f"in {int(h/24)}d"
        return label, late
    except Exception:
        return due_str[:10], False


# ── Measurement pass (dry-run to size the canvas) ────────────────────────────

def measure_card_height(draw, card, fonts, cfg):
    inner_w = COLUMN_WIDTH - 2 * COLUMN_PADDING - 2 * CARD_PADDING
    h = CARD_PADDING

    # Card title (may wrap)
    h += len(wrap_text(draw, card.get("name", "Untitled"), fonts["bold"], inner_w)) * 13

    # Optional description (limited to desc_rows lines)
    desc_lines = clamp_desc_rows(draw, strip_html(card.get("description", "")),
                                 fonts["small"], inner_w, cfg["desc_rows"])
    if desc_lines:
        h += 2 + len(desc_lines) * 11

    # Tags row
    if card.get("tags"):
        h += CARD_PADDING + TAG_H

    # Tasks: either expanded list or compact "done/total" count
    tasks = card.get("tasks", [])
    if tasks:
        h += CARD_PADDING
        if cfg["show_tasks"]:
            h += len(tasks) * (TASK_LINE_H + TASK_GAP) - TASK_GAP
        else:
            h += TASK_LINE_H

    # Due date
    if format_due_date(card.get("dueDate"))[0]:
        h += CARD_PADDING + 13

    h += CARD_PADDING
    return h

def measure_column_height(draw, col, fonts, cfg):
    h = COLUMN_HEADER_H + COLUMN_PADDING
    for card in col.get("cards", []):
        h += measure_card_height(draw, card, fonts, cfg) + CARD_GAP
    return h + COLUMN_PADDING


# ── Drawing primitives ────────────────────────────────────────────────────────

def draw_rect(draw, x0, y0, x1, y1, radius, fill, outline=None):
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=outline)

def draw_checkbox(draw, x, y, checked):
    draw.rectangle([x, y, x + 9, y + 9], fill=BG_CARD, outline=BORDER_CHECKBOX)
    if checked:
        draw.line([(x+1, y+4), (x+3, y+7), (x+7, y+1)], fill=(30, 30, 30), width=1)

def draw_tag(draw, x, y, text, fonts):
    """Draw a pill tag; returns the width consumed."""
    w = int(tw(draw, text, fonts["small"])) + TAG_PADDING_X * 2
    draw_rect(draw, x, y, x + w, y + TAG_H, 2, BG_TAG)
    draw.text((x + TAG_PADDING_X, y + 2), text, font=fonts["small"], fill=TEXT_TAG)
    return w


# ── Card renderer ─────────────────────────────────────────────────────────────

def draw_card(draw, x, y, card, fonts, cfg):
    """Draw a card; returns height used."""
    h  = measure_card_height(draw, card, fonts, cfg)
    x1 = x + COLUMN_WIDTH - 2 * COLUMN_PADDING
    draw_rect(draw, x, y, x1, y + h, 2, BG_CARD, BORDER_CARD)

    cx, cy  = x + CARD_PADDING, y + CARD_PADDING
    inner_w = x1 - x - 2 * CARD_PADDING

    # Title
    for line in wrap_text(draw, card.get("name", "Untitled"), fonts["bold"], inner_w):
        draw.text((cx, cy), line, font=fonts["bold"], fill=TEXT_CARD)
        cy += 13

    # Description
    desc_lines = clamp_desc_rows(draw, strip_html(card.get("description", "")),
                                 fonts["small"], inner_w, cfg["desc_rows"])
    if desc_lines:
        cy += 2
        for line in desc_lines:
            draw.text((cx, cy), line, font=fonts["small"], fill=TEXT_MUTED)
            cy += 11

    # Tags
    if card.get("tags"):
        cy += CARD_PADDING
        tx = cx
        for tag in card["tags"]:
            tx += draw_tag(draw, tx, cy, tag.get("text", ""), fonts) + TAG_GAP
        cy += TAG_H

    # Tasks: expanded list or compact count
    tasks = card.get("tasks", [])
    if tasks:
        cy += CARD_PADDING
        if cfg["show_tasks"]:
            for task in tasks:
                done = task.get("finished", False)
                draw_checkbox(draw, cx, cy + 1, done)
                draw.text((cx + 13, cy), task.get("name", ""), font=fonts["small"],
                          fill=TEXT_MUTED if done else TEXT_CARD)
                cy += TASK_LINE_H + TASK_GAP
        else:
            done_count = sum(1 for t in tasks if t.get("finished"))
            draw.text((cx, cy), f"{done_count}/{len(tasks)} tasks",
                      font=fonts["small"], fill=TEXT_MUTED)
            cy += TASK_LINE_H

    # Due date
    due_label, is_late = format_due_date(card.get("dueDate"))
    if due_label:
        cy += CARD_PADDING
        draw.text((cx, cy), ("LATE: " if is_late else "Due: ") + due_label,
                  font=fonts["small"], fill=TEXT_MUTED)

    return h


# ── Column renderer ───────────────────────────────────────────────────────────

def draw_column(draw, x, y, col, fonts, cfg):
    """Draw a column with header and all its cards; returns height used."""
    col_h = measure_column_height(draw, col, fonts, cfg)
    draw_rect(draw, x, y, x + COLUMN_WIDTH, y + col_h, 3, BG_COLUMN, BORDER_COLUMN)
    draw.text((x + COLUMN_PADDING, y + 7), col.get("title", ""), font=fonts["bold"], fill=TEXT_COLUMN)

    cy = y + COLUMN_HEADER_H + COLUMN_PADDING
    for card in col.get("cards", []):
        cy += draw_card(draw, x + COLUMN_PADDING, cy, card, fonts, cfg) + CARD_GAP

    return col_h


# ── Board filtering ───────────────────────────────────────────────────────────

def filter_columns(columns, cfg):
    result = []
    for col in columns:
        if cfg["skip_done"]  and col.get("title", "").strip().lower() == "done": continue
        if cfg["skip_empty"] and not col.get("cards"):                            continue
        result.append(col)
    return result or columns   # never return empty


# ── Board renderer ────────────────────────────────────────────────────────────

def render_board(board, fonts, cfg):
    """Render one board to a PIL Image, then crop to output_size if set."""
    columns = filter_columns(board.get("columns", []), cfg)

    probe       = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    col_heights = [measure_column_height(probe, c, fonts, cfg) for c in columns]
    max_col_h   = max(col_heights, default=100)

    n      = len(columns)
    width  = BOARD_PADDING * 2 + n * COLUMN_WIDTH + (n - 1) * COLUMN_GAP
    height = BOARD_PADDING * 2 + TITLE_H + max_col_h

    img  = Image.new("RGB", (width, height), BG_BOARD)
    draw = ImageDraw.Draw(img)

    draw.text((BOARD_PADDING, BOARD_PADDING), board.get("title", "Board"),
              font=fonts["title"], fill=TEXT_TITLE)

    cx = BOARD_PADDING
    for col in columns:
        draw_column(draw, cx, BOARD_PADDING + TITLE_H, col, fonts, cfg)
        cx += COLUMN_WIDTH + COLUMN_GAP

    # Fit to fixed output size if specified; [0, 0] means natural size
    if cfg["output_size"] and any(cfg["output_size"]):
        ow, oh = cfg["output_size"]
        canvas = Image.new("RGB", (ow, oh), BG_BOARD)
        canvas.paste(img, (0, 0))
        return canvas

    return img


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    s       = load_settings()
    data    = load_json(s["source_json"])
    fonts   = load_fonts()
    out_dir = Path(s.get("output_dir", "."))
    out_dir.mkdir(parents=True, exist_ok=True)

    size_val = s.get("output_size")   # e.g. [800, 480] or null
    cfg = {
        "skip_empty":  s.get("skip_empty_columns", True),
        "skip_done":   s.get("skip_done_column",   True),
        "show_tasks":  s.get("show_tasks",         True),
        "desc_rows":   s.get("description_rows",   0),
        "output_size": tuple(size_val) if size_val else None,
    }

    boards = data.get("boards", [])
    if not boards:
        print("No boards found."); sys.exit(1)

    for i, board in enumerate(boards):
        img  = render_board(board, fonts, cfg)
        path = out_dir / (safe_filename(board.get("title", f"board_{i}")) + ".png")
        img.save(path)
        print(f"Saved: {path}  ({img.size[0]}x{img.size[1]})")

if __name__ == "__main__":
    main()