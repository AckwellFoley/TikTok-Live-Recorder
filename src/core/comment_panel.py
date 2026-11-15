"""Replay actual chat messages in a separate, bounded comments panel."""

import math
import unicodedata
from collections import deque
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, features


WAITING = "Waiting for comments"
MAX_VISIBLE = 30
MAX_TEXT = 2000
BG = "#10131c"
_RAQM = features.check_feature("raqm")


def _clean_text(value, limit=MAX_TEXT):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "  ")
    return "".join(char for char in text if char == "\n" or unicodedata.category(char) not in ("Cc", "Cs"))[:limit]


def _at(value):
    try:
        at = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, at) if math.isfinite(at) else None


def comment_frames(timeline, duration, max_visible=MAX_VISIBLE, *, updates_per_second=5):
    """Return sparse states on a25fps grid, never ahead of message arrival.

    Each state owns a small list of references to normalized message dictionaries.
    A bounded deque avoids recopying the growing recording history per message.
    """
    duration = _at(duration)
    if duration is None or duration <= 0:
        return []
    max_visible = max(1, min(MAX_VISIBLE, int(max_visible)))
    try:
        updates_per_second = float(updates_per_second)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Comment updates per second must be positive.") from exc
    if not math.isfinite(updates_per_second) or updates_per_second <= 0:
        raise ValueError("Comment updates per second must be positive.")
    frames_per_update = max(1, math.ceil(25 / updates_per_second))
    events = []
    for index, item in enumerate(timeline.get("comments") or []):
        if not isinstance(item, dict):
            continue
        at = _at(item.get("time"))
        text = _clean_text(item.get("text"))
        if at is None or at >= duration or not text.strip():
            continue
        comment = {key: _clean_text(item.get(key), 160)
                   for key in ("id", "user_id", "username", "name")}
        comment.update(time=at, text=text)
        events.append((at, 1, index, comment))
    for index, item in enumerate(timeline.get("comment_statuses") or []):
        if not isinstance(item, dict):
            continue
        at = _at(item.get("time"))
        if at is not None and at < duration:
            events.append((at, 0, index, _clean_text(item.get("status"), 240).replace("\n", " ")))

    visible, seen_ids = deque(maxlen=max_visible), set()
    status = WAITING
    result = [{"time": 0.0, "comments": [], "status": status}]
    frame, last_signature = None, ((), status)

    def publish(at):
        nonlocal last_signature
        comments = list(visible)
        signature = (tuple((c["time"], c["id"], c["user_id"], c["username"], c["name"], c["text"])
                           for c in comments), status)
        if signature == last_signature:
            return
        snapshot = {"time": at, "comments": comments, "status": status}
        if result[-1]["time"] == at:
            result[-1] = snapshot
        else:
            result.append(snapshot)
        last_signature = signature

    for at, kind, _index, item in sorted(events, key=lambda event: event[:3]):
        quantized = math.ceil(at * 25 / frames_per_update - 1e-9) * frames_per_update / 25
        if quantized >= duration:
            continue
        if frame is not None and quantized != frame:
            publish(frame)
        frame = quantized
        if kind == 0:
            status = item
        else:
            if item["id"] and item["id"] in seen_ids:
                continue
            if item["id"]:
                seen_ids.add(item["id"])
            visible.append(item)
            if status == WAITING:
                status = ""
    if frame is not None:
        publish(frame)
    return result


@lru_cache(maxsize=64)
def _faces(size, bold=False):
    names = ["C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
             "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
             "C:/Windows/Fonts/seguiemj.ttf", "C:/Windows/Fonts/seguisym.ttf",
             "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msgothic.ttc",
             "C:/Windows/Fonts/malgun.ttf"]
    result = []
    for name in names:
        try:
            result.append(ImageFont.truetype(name, size))
        except OSError:
            continue
    if not result:
        result.append(ImageFont.load_default(size=size))
    return tuple(result)


@lru_cache(maxsize=64)
def _missing(face):
    mask = face.getmask("\U0010ffff")
    return mask.size, bytes(mask)


@lru_cache(maxsize=8192)
def _has_glyph(face, char):
    if unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return True
    try:
        mask = face.getmask(char)
        return (mask.size, bytes(mask)) != _missing(face)
    except (UnicodeError, ValueError):
        return False


def _clusters(text):
    clusters = []
    for char in text:
        joining = (unicodedata.combining(char) or char in ("\ufe0e", "\ufe0f", "\u200d")
                   or 0x1F3FB <= ord(char) <= 0x1F3FF)
        if clusters and (joining or clusters[-1].endswith("\u200d")):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def _runs(text, size, bold=False):
    faces = _faces(size, bold)
    result = []
    for cluster in _clusters(text):
        candidates = faces
        if any(0x1F000 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF for char in cluster):
            candidates = sorted(faces, key=lambda f: not str(getattr(f, "path", "")).lower().endswith("seguiemj.ttf"))
        face = next((f for f in candidates if all(_has_glyph(f, char) for char in cluster)), faces[0])
        if result and result[-1][0] is face:
            result[-1] = face, result[-1][1] + cluster
        else:
            result.append((face, cluster))
    return result


def _width(text, size, bold=False):
    return sum(face.getlength(run) for face, run in _runs(text, size, bold))


def _ellipsis(text, limit, size, bold=False):
    if _width(text, size, bold) <= limit:
        return text
    clusters = _clusters(text)
    while clusters and _width("".join(clusters) + "…", size, bold) > limit:
        clusters.pop()
    return "".join(clusters) + "…"


def _wrap_text(text, limit, size, max_lines=6):
    """Wrap whitespace and long tokens, retaining explicit paragraph breaks."""
    lines, line, truncated = [], "", False
    # Incremental widths avoid repeated layout of ever-growing unbroken URLs.
    width = 0.0
    for cluster in _clusters(text):
        if cluster == "\n":
            lines.append(line.rstrip())
            line, width = "", 0.0
        else:
            cluster_width = _width(cluster, size)
            if line and width + cluster_width > limit:
                split = line.rfind(" ")
                if split > 0:
                    lines.append(line[:split].rstrip())
                    line = line[split + 1:] + cluster
                else:
                    lines.append(line)
                    line = cluster.lstrip()
                width = _width(line, size)
            else:
                line += cluster
                width += cluster_width
        if len(lines) >= max_lines:
            truncated = True
            break
    if len(lines) < max_lines:
        lines.append(line.rstrip())
    if truncated:
        lines[-1] = _ellipsis(lines[-1].rstrip() + "…", limit, size)
    return lines or [""]


def _rtl(text):
    for char in text:
        direction = unicodedata.bidirectional(char)
        if direction in ("R", "AL"):
            return True
        if direction == "L":
            return False
    return False


def _draw_line(draw, x, y, text, size, fill, width, bold=False):
    runs = _runs(text, size, bold)
    rtl = _rtl(text)
    if rtl:
        x += width
    for face, run in runs:
        advance = face.getlength(run)
        if rtl:
            x -= advance
        options = {"direction": "rtl" if rtl else "ltr"} if _RAQM else {}
        draw.text((round(x), round(y)), run, font=face, fill=fill, anchor="lt", embedded_color=True, **options)
        if not rtl:
            x += advance


def draw_comments(timeline, snapshot, size=(720, 1280)):
    """Draw newest messages at the bottom; discard whole old cards to fit."""
    width, height = size
    if width < 100 or height < 160:
        raise ValueError("The comments panel must be at least100×160 pixels.")
    scale = min(width / 720, height / 1280)
    px = lambda n: max(1, round(n * scale))
    canvas = Image.new("RGBA", size, BG)
    draw = ImageDraw.Draw(canvas)
    margin, text_pad = px(24), px(17)
    text_x, text_width = margin + text_pad, width - 2 * (margin + text_pad)
    comments = [c for c in (snapshot.get("comments") or [])[-MAX_VISIBLE:] if isinstance(c, dict)]
    status = _clean_text(snapshot.get("status"), 240).replace("\n", " ") or ("" if comments else WAITING)
    draw.text((px(28), px(25)), "LIVE COMMENTS", font=_faces(px(31), True)[0], fill="white", anchor="lt")
    subtitle = status or "Latest messages · oldest first"
    _draw_line(draw, px(28), px(77), _ellipsis(subtitle, width - px(56), px(18)),
               px(18), "#a7b2c7", width - px(56))
    draw.line((margin, px(111), width - margin, px(111)), fill="#30394b")

    top, bottom, gap = px(128), height - px(28), px(10)
    name_size, body_size, line_h = px(21), px(25), px(33)
    cards, available = [], bottom - top
    for comment in reversed(comments):
        text = _clean_text(comment.get("text"))
        if not text.strip():
            continue
        name = _clean_text(comment.get("name"), 160).replace("\n", " ")
        username = _clean_text(comment.get("username"), 160).replace("\n", " ")
        author = name or ("@" + username if username else "Viewer")
        if name and username:
            author += "  @" + username
        author = _ellipsis(author, text_width, name_size, True)
        lines = _wrap_text(text, text_width, body_size)
        card_h = text_pad * 2 + px(25) + px(9) + len(lines) * line_h
        if card_h > available:
            break
        cards.append((author, lines, card_h))
        available -= card_h + gap
    y = bottom - sum(card[2] for card in cards) - gap * max(0, len(cards) - 1)
    for author, lines, card_h in reversed(cards):
        draw.rounded_rectangle((margin, y, width - margin, y + card_h),
                               radius=px(14), fill="#1a2130", outline="#2a3345", width=px(1))
        _draw_line(draw, text_x, y + text_pad, author, name_size, "#39d7cf", text_width, True)
        line_y = y + text_pad + px(34)
        for line in lines:
            _draw_line(draw, text_x, line_y, line, body_size, "#f0f3fa", text_width)
            line_y += line_h
        y += card_h + gap
    return canvas
