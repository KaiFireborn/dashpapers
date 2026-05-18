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
            "title": ImageFont.truetype(B, 17),
            "bold":  ImageFont.truetype(R, 11),
            "small": ImageFont.truetype(R,  9),
        }
    except Exception:
        f = ImageFont.load_default()
        return {"title": f, "bold": f, "small": f}


# ── Text helpers ──────────────────────────────────────────────────────────────

def tw(draw, text, font):
    return draw.textlength(text, font=font)

def wrap_text(draw, text, font, max_w):
    """Word-wrap text; returns list of lines."""
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
    """Wrap and cap to max_rows lines, appending '...' if truncated. 0 = hide."""
    if max_rows == 0 or not text:
        return []
    lines = wrap_text(draw, text, font, max_w)
    if len(lines) <= max_rows:
        return lines
    clipped = lines[:max_rows]
    clipped[-1] = clipped[-1].rstrip() + "..."
    return clipped


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
        if h < 1:    label = "<1h ago"           if late else "in <1h"
        elif h < 24: label = f"{int(h)}h ago"    if late else f"in {int(h)}h"
        else:        label = f"{int(h/24)}d ago"  if late else f"in {int(h/24)}d"
        return label, late
    except Exception:
        return due_str[:10], False


# ── Measurement ───────────────────────────────────────────────────────────────

def measure_card_height(draw, card, fonts, cfg):
    inner_w = COLUMN_WIDTH - 2 * COLUMN_PADDING - 2 * CARD_PADDING
    h = CARD_PADDING
    h += len(wrap_text(draw, card.get("name", "Untitled"), fonts["bold"], inner_w)) * 13
    desc_lines = clamp_desc_rows(draw, strip_html(card.get("description", "")),
                                 fonts["small"], inner_w, cfg["desc_rows"])
    if desc_lines:
        h += 2 + len(desc_lines) * 11
    if card.get("tags"):
        h += CARD_PADDING + TAG_H
    tasks     = card.get("tasks", [])
    due_label = format_due_date(card.get("dueDate"))[0]
    # Expanded task list sits above the footer row
    if tasks and cfg["show_tasks"]:
        h += CARD_PADDING + len(tasks) * (TASK_LINE_H + TASK_GAP) - TASK_GAP
    # Footer row: task count (left) and/or due date (right) — always one line
    if (tasks and not cfg["show_tasks"]) or due_label:
        h += CARD_PADDING + TASK_LINE_H
    return h + CARD_PADDING

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
    """Draw a pill tag; returns width consumed."""
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

    for line in wrap_text(draw, card.get("name", "Untitled"), fonts["bold"], inner_w):
        draw.text((cx, cy), line, font=fonts["bold"], fill=TEXT_CARD); cy += 13

    desc_lines = clamp_desc_rows(draw, strip_html(card.get("description", "")),
                                 fonts["small"], inner_w, cfg["desc_rows"])
    if desc_lines:
        cy += 2
        for line in desc_lines:
            draw.text((cx, cy), line, font=fonts["small"], fill=TEXT_MUTED); cy += 11

    if card.get("tags"):
        cy += CARD_PADDING
        tx = cx
        for tag in card["tags"]:
            tx += draw_tag(draw, tx, cy, tag.get("text", ""), fonts) + TAG_GAP
        cy += TAG_H

    tasks = card.get("tasks", [])
    due_label, is_late = format_due_date(card.get("dueDate"))

    # Expanded task list (only when show_tasks is on)
    if tasks and cfg["show_tasks"]:
        cy += CARD_PADDING
        for task in tasks:
            done = task.get("finished", False)
            draw_checkbox(draw, cx, cy + 1, done)
            draw.text((cx + 13, cy), task.get("name", ""), font=fonts["small"],
                      fill=TEXT_MUTED if done else TEXT_CARD)
            cy += TASK_LINE_H + TASK_GAP

    # Footer row: compact task count on the left, due date on the right
    show_count = tasks and not cfg["show_tasks"]
    if show_count or due_label:
        cy += CARD_PADDING
        if show_count:
            done_count = sum(1 for t in tasks if t.get("finished"))
            draw.text((cx, cy), f"{done_count}/{len(tasks)} tasks",
                      font=fonts["small"], fill=TEXT_MUTED)
        if due_label:
            label = ("LATE: " if is_late else "Due: ") + due_label
            lw    = int(tw(draw, label, fonts["small"]))
            draw.text((x1 - CARD_PADDING - lw, cy), label,
                      font=fonts["small"], fill=TEXT_MUTED)

    return h


# ── Column renderer ───────────────────────────────────────────────────────────

def draw_column(draw, x, y, col, fonts, cfg):
    """Draw a full column; returns height used."""
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
    return result or columns


# ── Full-board render ─────────────────────────────────────────────────────────

def render_full(board, fonts, cfg):
    """Render the entire board into one image.

    Also returns the pixel boundaries of every column and card so the tiler
    can find clean cut points that avoid splitting elements.

    Returns:
        img           -- PIL Image of the complete board
        col_x_bounds  -- list of (x_left, x_right) per column
        card_y_bounds -- list of (y_top, y_bottom) for every card in the board
    """
    columns = filter_columns(board.get("columns", []), cfg)
    probe   = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    col_heights = [measure_column_height(probe, c, fonts, cfg) for c in columns]
    max_col_h   = max(col_heights, default=100)
    n           = len(columns)

    width  = BOARD_PADDING * 2 + n * COLUMN_WIDTH + (n - 1) * COLUMN_GAP
    height = BOARD_PADDING * 2 + TITLE_H + max_col_h

    img  = Image.new("RGB", (width, height), BG_BOARD)
    draw = ImageDraw.Draw(img)
    draw.text((BOARD_PADDING, BOARD_PADDING), board.get("title", "Board"),
              font=fonts["title"], fill=TEXT_TITLE)

    col_x_bounds  = []
    card_y_bounds = []

    cx = BOARD_PADDING
    for col in columns:
        col_x_bounds.append((cx, cx + COLUMN_WIDTH))
        cy = BOARD_PADDING + TITLE_H + COLUMN_HEADER_H + COLUMN_PADDING
        for card in col.get("cards", []):
            h = measure_card_height(draw, card, fonts, cfg)
            card_y_bounds.append((cy, cy + h))
            cy += h + CARD_GAP
        draw_column(draw, cx, BOARD_PADDING + TITLE_H, col, fonts, cfg)
        cx += COLUMN_WIDTH + COLUMN_GAP

    return img, col_x_bounds, card_y_bounds


# ── Tiling helpers ────────────────────────────────────────────────────────────

def find_page_starts(boundaries, page_size, total):
    """Return pixel offsets at which each page begins along one axis.

    We walk forward page_size pixels at a time and snap each cut backwards to
    the nearest gap between elements so no element is split.  An element that
    straddles the cut will be repeated on the next page (handled at crop time).
    """
    starts = [0]
    pos    = 0
    while pos + page_size < total:
        ideal = pos + page_size
        # Walk backwards from the ideal cut to find a point not inside any element
        cut = ideal
        for candidate in range(ideal, pos, -1):
            if not any(s < candidate < e for s, e in boundaries):
                cut = candidate
                break
        starts.append(cut)
        pos = cut
    return starts


def make_tile(full_img, x0, y0, tile_w, tile_h):
    """Crop a region from full_img and paste onto a fresh white canvas."""
    x1 = min(x0 + tile_w, full_img.width)
    y1 = min(y0 + tile_h, full_img.height)
    canvas = Image.new("RGB", (tile_w, tile_h), BG_BOARD)
    canvas.paste(full_img.crop((x0, y0, x1, y1)), (0, 0))
    return canvas


def stamp_label(img, page_num, total_pages, fonts):
    """Overlay a 'n/total' label in the top-left corner."""
    if total_pages == 1:
        return
    label = f"{page_num}/{total_pages}"
    draw  = ImageDraw.Draw(img)
    lw    = int(draw.textlength(label, font=fonts["small"]))
    pad   = 4
    draw.rectangle([pad, pad, pad + lw + 6, pad + 15], fill=BG_BOARD, outline=(180, 180, 180))
    draw.text((pad + 3, pad + 2), label, font=fonts["small"], fill=TEXT_TITLE)


def tile_has_content(tile):
    """Return True if the tile contains any pixel that isn't the background colour."""
    bg = BG_BOARD
    px = tile.load()
    w, h = tile.size
    for y in range(h):
        for x in range(w):
            if px[x, y] != bg:
                return True
    return False


# ── Save: full + tiles ────────────────────────────────────────────────────────

def save_board(board, fonts, cfg, out_dir, board_index):
    stem = safe_filename(board.get("title", f"board_{board_index}"))
    full_img, col_x_bounds, card_y_bounds = render_full(board, fonts, cfg)

    out_w, out_h = cfg["output_size"] if cfg["output_size"] else (0, 0)
    fw, fh       = full_img.size
    needs_tiles  = (out_w and out_w < fw) or (out_h and out_h < fh)

    # Always write the full image (named _full when tiles will also be written,
    # or plain when size is natural / board fits in one page)
    full_suffix = "_full" if needs_tiles else ""
    full_path   = out_dir / f"{stem}{full_suffix}.png"
    full_img.save(full_path)
    print(f"Saved: {full_path}  ({fw}x{fh})")

    if not needs_tiles:
        return

    # Compute page-start offsets for each axis
    tile_w   = out_w if out_w else fw
    tile_h   = out_h if out_h else fh
    x_starts = find_page_starts(col_x_bounds,  tile_w, fw) if tile_w < fw else [0]
    y_starts = find_page_starts(card_y_bounds, tile_h, fh) if tile_h < fh else [0]

    nx, ny       = len(x_starts), len(y_starts)
    total_tiles  = nx * ny
    tile_num     = 0

    for yi, y0 in enumerate(y_starts):
        for xi, x0 in enumerate(x_starts):
            tile_num += 1
            tile = make_tile(full_img, x0, y0, tile_w, tile_h)
            stamp_label(tile, tile_num, total_tiles, fonts)

            # Suffix: single counter for 1-D split, row_col for 2-D
            if   nx == 1: suffix = f"_{yi + 1}"
            elif ny == 1: suffix = f"_{xi + 1}"
            else:         suffix = f"_{yi + 1}_{xi + 1}"

            if not tile_has_content(tile):
                print(f"Skipped (blank): {stem}{suffix}.png")
                continue

            path = out_dir / f"{stem}{suffix}.png"
            tile.save(path)
            print(f"Saved: {path}  ({tile.width}x{tile.height})  [{tile_num}/{total_tiles}]")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    s       = load_settings()
    data    = load_json(s["source_json"])
    fonts   = load_fonts()
    out_dir = Path(s.get("output_dir", "."))
    out_dir.mkdir(parents=True, exist_ok=True)

    size_val    = s.get("output_size")          # [800, 480], [0, 0], or null/absent
    output_size = tuple(size_val) if (size_val and any(size_val)) else None

    cfg = {
        "skip_empty":  s.get("skip_empty_columns", True),
        "skip_done":   s.get("skip_done_column",   True),
        "show_tasks":  s.get("show_tasks",         True),
        "desc_rows":   s.get("description_rows",   0),
        "output_size": output_size,
    }

    boards = data.get("boards", [])
    if not boards:
        print("No boards found."); sys.exit(1)

    for i, board in enumerate(boards):
        save_board(board, fonts, cfg, out_dir, i)

if __name__ == "__main__":
    main()