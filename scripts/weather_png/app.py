"""
Weather visualizer — pulls from open-meteo (free, no API key).
Usage: place settings.json next to this file, then run it.
Output: weather_<location>.png
"""

import json, sys, math
from datetime import datetime, timezone
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

# ── Palette (e-ink: white bg, dark ink) ───────────────────────────────────────
BG        = (255, 255, 255)
FG        = (0,   0,   0  )
GRAY      = (100, 100, 100)
LGRAY     = (200, 200, 200)
DGRAY     = (60,  60,  60 )
ACCENT    = (60,  60,  60 )   # graph line / bars


# ── WMO weather code → short label ────────────────────────────────────────────
WMO_LABELS = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm+hail", 99: "Thunderstorm+hail",
}

def wmo_label(code):
    return WMO_LABELS.get(code, f"Code {code}")

# ASCII art stand-ins for weather icons (drawn as text glyphs)
WMO_SYMBOL = {
    0: "☀", 1: "🌤", 2: "⛅", 3: "☁",
    45: "🌫", 48: "🌫",
    51: "🌦", 53: "🌦", 55: "🌧",
    61: "🌧", 63: "🌧", 65: "🌧",
    71: "🌨", 73: "🌨", 75: "🌨",
    80: "🌦", 81: "🌧", 82: "🌧",
    95: "⛈",  96: "⛈",  99: "⛈",
}

def wmo_symbol(code):
    return WMO_SYMBOL.get(code, "?")


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_settings():
    path = Path("scripts/weather_png/settings.json")
    if not path.exists():
        print("settings.json not found"); sys.exit(1)
    with open(path) as f:
        return json.load(f)

def safe_filename(s):
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in s).strip()


# ── Font loading ──────────────────────────────────────────────────────────────

def load_fonts():
    try:
        B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return {
            "huge":   ImageFont.truetype(B, 42),
            "large":  ImageFont.truetype(B, 20),
            "medium": ImageFont.truetype(R, 15),
            "small":  ImageFont.truetype(R, 11),
            "tiny":   ImageFont.truetype(R,  9),
        }
    except Exception:
        f = ImageFont.load_default()
        return {k: f for k in ("huge", "large", "medium", "small", "tiny")}


# ── Geocoding via open-meteo ──────────────────────────────────────────────────

def geocode(location):
    r = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    results = r.json().get("results")
    if not results:
        print(f"Location not found: {location}"); sys.exit(1)
    hit = results[0]
    return hit["latitude"], hit["longitude"], hit.get("name", location), hit.get("country", "")


# ── Weather fetch ─────────────────────────────────────────────────────────────

def fetch_weather(lat, lon):
    params = {
        "latitude":  lat,
        "longitude": lon,
        "timezone":  "auto",
        "forecast_days": 8,
        "current": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m",
            "precipitation_probability", "wind_speed_10m", "weather_code",
        ]),
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily":  "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
    return r.json()


def extract_today_hourly(data):
    """Return lists of (hour_str, temp, precip_pct) for today only."""
    times  = data["hourly"]["time"]
    temps  = data["hourly"]["temperature_2m"]
    precip = data["hourly"]["precipitation_probability"]
    today  = datetime.now().date().isoformat()
    rows   = [(t[11:16], te, pr)
              for t, te, pr in zip(times, temps, precip)
              if t.startswith(today)]
    return rows


def extract_daily(data):
    """Return list of dicts for days 0..7."""
    d = data["daily"]
    days = []
    for i in range(min(8, len(d["time"]))):
        dt = datetime.fromisoformat(d["time"][i])
        days.append({
            "label":  dt.strftime("%a"),
            "code":   d["weather_code"][i],
            "t_max":  d["temperature_2m_max"][i],
            "t_min":  d["temperature_2m_min"][i],
            "precip": d["precipitation_probability_max"][i],
        })
    return days


# ── Drawing helpers ───────────────────────────────────────────────────────────

def text_w(draw, text, font):
    return int(draw.textlength(text, font=font))

def draw_centered(draw, cx, y, text, font, color=FG):
    w = text_w(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=color)

def draw_right(draw, rx, y, text, font, color=FG):
    w = text_w(draw, text, font)
    draw.text((rx - w, y), text, font=font, fill=color)

def draw_hline(draw, x0, x1, y, color=LGRAY):
    draw.line([(x0, y), (x1, y)], fill=color)


# ── Graph renderers ───────────────────────────────────────────────────────────

def draw_line_graph(draw, x, y, w, h, values, label_fn, fonts):
    """Draw a polyline graph. label_fn(v) -> str for y-axis hints."""
    if not values:
        return
    lo, hi = min(values), max(values)
    span   = (hi - lo) or 1

    def px(v):  # value → pixel y (inverted)
        return y + h - int((v - lo) / span * h)

    points = [(x + int(i / (len(values) - 1) * w), px(v))
              for i, v in enumerate(values)]

    draw.line(points, fill=ACCENT, width=2)

    # Min/max labels
    draw.text((x + 2, y + 1),     label_fn(hi), font=fonts["tiny"], fill=GRAY)
    draw.text((x + 2, y + h - 12), label_fn(lo), font=fonts["tiny"], fill=GRAY)


def draw_bar_graph(draw, x, y, w, h, values, label_fn, fonts):
    """Draw vertical bars (for precipitation %)."""
    if not values:
        return
    n      = len(values)
    bar_w  = max(2, w // n - 2)
    hi     = max(values) or 1

    for i, v in enumerate(values):
        bh    = int(v / hi * h) if hi else 0
        bx    = x + int(i / n * w)
        top   = y + h - bh
        draw.rectangle([bx, top, bx + bar_w, y + h], fill=DGRAY)

    # Max label
    draw.text((x + 2, y + 1), label_fn(hi), font=fonts["tiny"], fill=GRAY)


# ── Section renderers ─────────────────────────────────────────────────────────

def draw_current(draw, img_w, cur, location_name, fonts):
    """Top section: icon glyph, temperature, stats, location/condition."""
    pad = 16

    # Icon glyph (large Unicode symbol)
    symbol = wmo_symbol(cur["weather_code"])
    draw.text((pad, pad), symbol, font=fonts["huge"], fill=FG)

    # Temperature
    temp_str = f"{round(cur['temperature_2m'])}°"
    draw.text((pad + 70, pad), temp_str, font=fonts["huge"], fill=FG)

    # Stats block
    sx = pad + 200
    sy = pad + 6
    lh = 18
    draw.text((sx, sy),        f"Feels like: {round(cur['apparent_temperature'])}°", font=fonts["small"], fill=GRAY)
    draw.text((sx, sy + lh),   f"Humidity: {cur['relative_humidity_2m']}%",           font=fonts["small"], fill=GRAY)
    draw.text((sx, sy + lh*2), f"Wind: {round(cur['wind_speed_10m'])} km/h",          font=fonts["small"], fill=GRAY)
    draw.text((sx, sy + lh*3), f"Precip: {cur['precipitation_probability']}%",        font=fonts["small"], fill=GRAY)

    # Location + condition (right-aligned)
    draw_right(draw, img_w - pad, pad,      location_name,              fonts["large"])
    draw_right(draw, img_w - pad, pad + 26, wmo_label(cur["weather_code"]), fonts["medium"], GRAY)
    draw_right(draw, img_w - pad, pad + 48, datetime.now().strftime("%A %d %b"),       fonts["small"], GRAY)

    return pad * 2 + 52   # height consumed


def draw_graphs(draw, x, y, w, h, hourly_rows, fonts):
    """Two stacked graphs: temperature (line) and precipitation (bars)."""
    n       = len(hourly_rows)
    gh      = (h - 24) // 2   # height per graph, with gap
    temps   = [r[1] for r in hourly_rows]
    precips = [r[2] for r in hourly_rows]

    # Temperature line graph
    draw.text((x, y), "Temperature", font=fonts["tiny"], fill=GRAY)
    draw_line_graph(draw, x, y + 12, w, gh - 12, temps, lambda v: f"{v:.0f}°", fonts)

    # Precipitation bar graph
    gy2 = y + gh + 12
    draw.text((x, gy2), "Precipitation", font=fonts["tiny"], fill=GRAY)
    draw_bar_graph(draw, x, gy2 + 12, w, gh - 12, precips, lambda v: f"{v:.0f}%", fonts)

    # Hour labels along the bottom
    label_y = y + h - 10
    for i, (hour, _, _) in enumerate(hourly_rows):
        if i % 3 == 0:   # every 3 hours to avoid crowding
            lx = x + int(i / (n - 1) * w) if n > 1 else x
            draw_centered(draw, lx, label_y, hour, fonts["tiny"], GRAY)

    return h


def draw_forecast(draw, x, y, w, h, daily, fonts):
    """Row of 7-day forecast tiles."""
    n      = len(daily)
    tile_w = w // n

    for i, day in enumerate(daily):
        tx  = x + i * tile_w
        tcx = tx + tile_w // 2

        draw_centered(draw, tcx, y,      day["label"],              fonts["small"])
        draw_centered(draw, tcx, y + 18, wmo_symbol(day["code"]),   fonts["medium"])
        draw_centered(draw, tcx, y + 38, f"{round(day['t_max'])}°", fonts["small"])
        draw_centered(draw, tcx, y + 54, f"{round(day['t_min'])}°", fonts["small"], GRAY)

        # Thin separator
        if i > 0:
            draw.line([(tx, y), (tx, y + h)], fill=LGRAY)


# ── Main compose ──────────────────────────────────────────────────────────────

def render(data, location_name, out_w, out_h, fonts):
    cur         = data["current"]
    hourly_rows = extract_today_hourly(data)
    daily       = extract_daily(data)[1:8]   # skip today, show next 7

    PAD       = 14
    GRAPH_H   = int(out_h * 0.38)
    FORECAST_H = 75
    HEADER_H  = out_h - GRAPH_H - FORECAST_H - PAD * 4

    img  = Image.new("RGB", (out_w, out_h), BG)
    draw = ImageDraw.Draw(img)

    # Header
    hy = PAD
    draw_current(draw, out_w, cur, location_name, fonts)

    # Divider
    div_y = PAD + max(HEADER_H, 72)
    draw_hline(draw, PAD, out_w - PAD, div_y)

    # Graphs
    gy = div_y + PAD
    draw_graphs(draw, PAD, gy, out_w - PAD * 2, GRAPH_H, hourly_rows, fonts)

    # Divider
    div2_y = gy + GRAPH_H + PAD
    draw_hline(draw, PAD, out_w - PAD, div2_y)

    # Forecast
    fy = div2_y + PAD
    draw_forecast(draw, PAD, fy, out_w - PAD * 2, FORECAST_H, daily, fonts)

    return img


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    s     = load_settings()
    fonts = load_fonts()

    location = s.get("location", "London")
    size_val = s.get("output_size", [800, 480])
    out_w, out_h = size_val if (size_val and any(size_val)) else (800, 480)
    out_dir = Path(s.get("output_dir", "."))
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Geocoding: {location}")
    lat, lon, name, country = geocode(location)
    display_name = f"{name}, {country}" if country else name
    print(f"  → {display_name} ({lat:.3f}, {lon:.3f})")

    print("Fetching weather...")
    data = fetch_weather(lat, lon)

    img  = render(data, display_name, out_w, out_h, fonts)
    path = out_dir / f"weather_{safe_filename(location)}.png"
    img.save(path)
    print(f"Saved: {path}")

if __name__ == "__main__":
    main()