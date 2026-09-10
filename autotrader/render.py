"""Rendering listings into the shapes each notification channel wants."""

from __future__ import annotations

import html as htmllib
from typing import Any, Iterable

from .state import Change

MAX_SMS = 1500


def _esc(text: Any) -> str:
    return htmllib.escape(str(text or ""), quote=True)


def _facts(listing) -> list[str]:
    """The short chips shown under a car's name."""
    out = [listing.price_text]
    if listing.mileage_km is not None:
        out.append(listing.mileage_text)
    title = listing.display_title.lower()
    for value in (listing.trim, listing.drivetrain, listing.transmission,
                  listing.color, listing.location or listing.province):
        # display_title already carries the trim; do not say it twice.
        if value and str(value).lower() not in title:
            out.append(str(value))
    return out


def headline(changes: list[Change]) -> str:
    """One line summarising a run, used as the subject / message title."""
    new = sum(1 for c in changes if c.kind == Change.NEW)
    drops = sum(1 for c in changes if c.kind == Change.PRICE_DROP)
    rises = sum(1 for c in changes if c.kind == Change.PRICE_RISE)
    gone = sum(1 for c in changes if c.kind == Change.REMOVED)
    priced = sum(1 for c in changes if c.kind == Change.PRICED)
    bits = []
    if new:
        bits.append(f"{new} new listing{'s' if new != 1 else ''}")
    if drops:
        bits.append(f"{drops} price drop{'s' if drops != 1 else ''}")
    if rises:
        bits.append(f"{rises} price increase{'s' if rises != 1 else ''}")
    if priced:
        bits.append(f"{priced} price published")
    if gone:
        bits.append(f"{gone} removed")
    return "AutoTrader: " + (", ".join(bits) if bits else "no changes")


def _change_prefix(change: Change) -> str:
    if change.kind == Change.PRICE_DROP:
        return f"PRICE DROP ${abs(change.delta or 0):,} off - "
    if change.kind == Change.PRICE_RISE:
        return f"Price up ${abs(change.delta or 0):,} - "
    if change.kind == Change.PRICED:
        return "Price now shown - "
    if change.kind == Change.REMOVED:
        return "Removed - "
    return ""


def as_text(changes: list[Change], *, limit: int = 12, footer: str = "") -> str:
    """Plain text, for SMS and any channel without formatting."""
    lines = [headline(changes), ""]
    for change in changes[:limit]:
        listing = change.listing
        lines.append(f"{_change_prefix(change)}{listing.display_title}")
        lines.append("  " + " | ".join(_facts(listing)))
        if listing.url:
            lines.append("  " + listing.url)
        lines.append("")
    if len(changes) > limit:
        lines.append(f"...and {len(changes) - limit} more.")
    if footer:
        lines.append(footer)
    return "\n".join(lines).strip()


def as_sms(changes: list[Change]) -> str:
    """Deliberately terse - SMS is billed per segment."""
    lines = [headline(changes)]
    for change in changes[:5]:
        listing = change.listing
        lines.append(f"{_change_prefix(change)}{listing.display_title} "
                     f"{listing.price_text} {listing.mileage_text}")
        if listing.url:
            lines.append(listing.url)
    if len(changes) > 5:
        lines.append(f"+{len(changes) - 5} more")
    return "\n".join(lines)[:MAX_SMS]


def as_markdown(changes: list[Change], *, limit: int = 12) -> str:
    """Slack/Discord-flavoured markdown."""
    lines = [f"*{headline(changes)}*", ""]
    for change in changes[:limit]:
        listing = change.listing
        title = listing.display_title
        link = f"<{listing.url}|{title}>" if listing.url else title
        lines.append(f"{_change_prefix(change)}{link}")
        lines.append("_" + " | ".join(_facts(listing)) + "_")
    if len(changes) > limit:
        lines.append(f"_...and {len(changes) - limit} more._")
    return "\n".join(lines)


def as_telegram_html(changes: list[Change], *, limit: int = 12) -> str:
    """Telegram accepts a small HTML subset: b, i, a, code, pre, u, s."""
    lines = [f"<b>{_esc(headline(changes))}</b>"]
    for change in changes[:limit]:
        listing = change.listing
        title = _esc(listing.display_title)
        link = f'<a href="{_esc(listing.url)}">{title}</a>' if listing.url else f"<b>{title}</b>"
        prefix = ""
        if change.kind == Change.PRICE_DROP:
            prefix = f"↓ <b>${abs(change.delta or 0):,} off</b> "
        elif change.kind == Change.PRICE_RISE:
            prefix = f"↑ ${abs(change.delta or 0):,} more "
        elif change.kind == Change.PRICED:
            prefix = "💲 price published – "
        elif change.kind == Change.REMOVED:
            prefix = "✖ gone – "
        lines.append("")
        lines.append(f"{prefix}{link}")
        lines.append(f"<i>{_esc(' | '.join(_facts(listing)))}</i>")
        if change.kind in (Change.PRICE_DROP, Change.PRICE_RISE):
            lines.append(f"<s>${change.old_price:,}</s> → <b>${change.new_price:,}</b>")
    if len(changes) > limit:
        lines.append(f"\n<i>...and {len(changes) - limit} more.</i>")
    return "\n".join(lines)


def as_email_html(changes: list[Change], *, limit: int = 25,
                  dashboard_url: str = "") -> str:
    """A self-contained HTML email.

    Inline styles only, table-free layout where possible, and every colour
    stated explicitly - email clients strip <style> blocks and some force a
    dark background, so nothing may depend on a stylesheet or a default.
    """
    cards: list[str] = []
    for change in changes[:limit]:
        listing = change.listing
        badge = ""
        if change.kind == Change.PRICE_DROP:
            badge = (f'<span style="display:inline-block;background:#0f7b3f;color:#ffffff;'
                     f'font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;">'
                     f'&#8595; ${abs(change.delta or 0):,} off</span>')
        elif change.kind == Change.PRICE_RISE:
            badge = (f'<span style="display:inline-block;background:#9a3412;color:#ffffff;'
                     f'font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;">'
                     f'&#8593; ${abs(change.delta or 0):,}</span>')
        elif change.kind == Change.NEW:
            badge = ('<span style="display:inline-block;background:#1d4ed8;color:#ffffff;'
                     'font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;">NEW</span>')
        elif change.kind == Change.PRICED:
            badge = ('<span style="display:inline-block;background:#0f7b3f;color:#ffffff;'
                     'font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;">'
                     'PRICE SHOWN</span>')
        elif change.kind == Change.REMOVED:
            badge = ('<span style="display:inline-block;background:#6b7280;color:#ffffff;'
                     'font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;">REMOVED</span>')

        photo = ""
        if listing.thumbnail:
            photo = (f'<a href="{_esc(listing.url)}"><img src="{_esc(listing.thumbnail)}" '
                     f'width="200" alt="" style="width:200px;max-width:38%;height:auto;'
                     f'border-radius:8px;display:block;border:0;"></a>')

        was = ""
        if change.kind in (Change.PRICE_DROP, Change.PRICE_RISE) and change.old_price:
            was = (f'<span style="color:#6b7280;text-decoration:line-through;'
                   f'font-size:14px;">${change.old_price:,}</span> ')

        cards.append(f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="margin:0 0 14px 0;background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;">
 <tr>
  <td style="padding:14px;" valign="top">{photo}</td>
  <td style="padding:14px 14px 14px 0;" valign="top">
   {badge}
   <div style="margin:8px 0 4px 0;font:600 17px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
    <a href="{_esc(listing.url)}" style="color:#111827;text-decoration:none;">{_esc(listing.display_title)}</a>
   </div>
   <div style="font:700 20px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#0f7b3f;margin:0 0 6px 0;">
    {was}{_esc(listing.price_text)}
   </div>
   <div style="font:400 14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#4b5563;">
    {_esc(' &#183; '.join(_facts(listing)[1:]) or 'No further details')}
   </div>
   <div style="margin-top:10px;">
    <a href="{_esc(listing.url)}" style="display:inline-block;background:#111827;color:#ffffff;
       font:600 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
       padding:9px 14px;border-radius:6px;text-decoration:none;">View on AutoTrader</a>
   </div>
  </td>
 </tr>
</table>""".replace("&#183;", "&middot;"))

    more = (f'<p style="font:400 14px/1.5 -apple-system,Arial,sans-serif;color:#6b7280;">'
            f'&hellip;and {len(changes) - limit} more.</p>') if len(changes) > limit else ""
    dash = (f'<p style="margin-top:18px;"><a href="{_esc(dashboard_url)}" '
            f'style="color:#1d4ed8;font:400 14px/1.5 -apple-system,Arial,sans-serif;">'
            f'Open your dashboard</a></p>') if dashboard_url else ""

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:20px;background:#f3f4f6;">
<div style="max-width:640px;margin:0 auto;">
 <h1 style="font:700 20px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#111827;margin:0 0 16px 0;">
  {_esc(headline(changes))}
 </h1>
 {''.join(cards)}
 {more}
 {dash}
 <p style="font:400 12px/1.5 -apple-system,Arial,sans-serif;color:#9ca3af;margin-top:22px;">
  Sent by your AutoTrader watcher. Change what you get in config.json or the dashboard settings.
 </p>
</div></body></html>"""


def as_discord_embeds(changes: list[Change], *, limit: int = 10) -> list[dict[str, Any]]:
    """Discord caps a message at 10 embeds."""
    colours = {Change.NEW: 0x1D4ED8, Change.PRICE_DROP: 0x0F7B3F,
               Change.PRICE_RISE: 0x9A3412, Change.PRICED: 0x0F7B3F,
               Change.REMOVED: 0x6B7280}
    embeds: list[dict[str, Any]] = []
    for change in changes[:min(limit, 10)]:
        listing = change.listing
        fields = [{"name": "Price", "value": listing.price_text, "inline": True}]
        if listing.mileage_km is not None:
            fields.append({"name": "Odometer", "value": listing.mileage_text, "inline": True})
        if listing.location or listing.province:
            fields.append({"name": "Where", "value": listing.location or listing.province,
                           "inline": True})
        if change.kind in (Change.PRICE_DROP, Change.PRICE_RISE) and change.old_price:
            fields.append({"name": "Was", "value": f"${change.old_price:,}", "inline": True})
        embed: dict[str, Any] = {
            "title": listing.display_title[:250] or f"Listing {listing.id}",
            "url": listing.url or None,
            "color": colours.get(change.kind, 0x111827),
            "description": change.describe(),
            "fields": fields,
        }
        if listing.thumbnail:
            embed["thumbnail"] = {"url": listing.thumbnail}
        if listing.search_name:
            embed["footer"] = {"text": listing.search_name[:2000]}
        embeds.append(embed)
    return embeds


def as_json_payload(changes: list[Change], run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Machine-readable body for the custom-webhook channel."""
    return {
        "headline": headline(changes),
        "count": len(changes),
        "run": run or {},
        "changes": [{
            "kind": c.kind,
            "old_price": c.old_price,
            "new_price": c.new_price,
            "delta": c.delta,
            "listing": c.listing.to_dict(),
        } for c in changes],
    }
