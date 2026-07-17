import html
import re
from datetime import datetime, timezone


def to_telegram_html(text: str) -> str:
    """Convert Claude markdown to Telegram-safe HTML."""
    blocks: list[str] = []
    inlines: list[str] = []

    def pull_block(m):
        blocks.append(html.escape(m.group(1).strip()))
        return f"§B{len(blocks)-1}§"

    def pull_inline(m):
        inlines.append(html.escape(m.group(1)))
        return f"§I{len(inlines)-1}§"

    text = re.sub(r'```\w*\n?(.*?)```', pull_block, text, flags=re.DOTALL)
    text = re.sub(r'`([^`\n]+)`', pull_inline, text)
    text = html.escape(text)
    text = re.sub(r'^#{1,3} (.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    for i, content in enumerate(blocks):
        text = text.replace(f"§B{i}§", f"<pre>{content}</pre>")
    for i, content in enumerate(inlines):
        text = text.replace(f"§I{i}§", f"<code>{content}</code>")

    return text


def split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split long messages on newlines to stay under Telegram's 4096 char limit."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if sum(len(l) for l in current) + len(line) > max_len:
            chunks.append("".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("".join(current))
    return chunks


def parse_reset_wait(err_str: str) -> int:
    """Parse 'Limit resets at: YYYY-MM-DD HH:MM:SS UTC' and return seconds to wait."""
    m = re.search(r"Limit resets at:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)", err_str)
    if m:
        reset_at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        secs = max(0, int((reset_at - datetime.now(timezone.utc)).total_seconds())) + 2
        return min(secs, 120)
    return 60
