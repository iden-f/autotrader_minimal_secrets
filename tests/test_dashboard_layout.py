"""The published page, opened in a real browser and measured.

Everything here is a property that has actually been broken by a change to
this page during its build, and that no unit test would have caught:

* a selector list with an @media block inside it silently killed the rule
  above it, and the unread badge went dark-on-blue;
* the phone card rules were written before the card itself, so the layout
  they described never applied;
* the tertiary grey used for every timestamp, count and unit sat at 3.2:1;
* the bottom tab bar was drawn after the fetch, so it grew from nothing on
  a phone and shoved the page.

These are checked by rendering, not by reading the CSS, because in every one
of those cases the CSS said the right thing and the browser did something
else. Skipped when playwright or the bundled browser is missing, so the suite
still runs anywhere.
"""

from __future__ import annotations

import json
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
CHROME = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
WIDTHS = (390, 834, 1440)
THEMES = ("light", "dark")

playwright = pytest.importorskip("playwright.sync_api",
                                 reason="playwright is not installed")


def _browser_path() -> str | None:
    if CHROME.exists():
        return str(CHROME)
    found = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    return str(found[0]) if found else None


@pytest.fixture(scope="module")
def site():
    """Serve docs/ on a loopback port for the length of the module."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(DOCS))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def browser():
    path = _browser_path()
    if not path:
        pytest.skip("no bundled chromium to render with")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=path)
        try:
            yield b
        finally:
            b.close()


CONTRAST_JS = r"""
() => {
  const lum = c => { const f = v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); };
    return 0.2126*f(c[0])+0.7152*f(c[1])+0.0722*f(c[2]); };
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if(!m) return null;
    return m[1].split(',').map(Number).slice(0,3); };
  const bgOf = el => { let n = el; while (n) { const c = getComputedStyle(n).backgroundColor;
      if (parse(c) && !/rgba\(0, 0, 0, 0\)/.test(c)) return parse(c); n = n.parentElement; }
    return [255,255,255]; };
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!el.firstChild || el.firstChild.nodeType !== 3) continue;
    const text = el.textContent.trim();
    if (!text || text.length > 80) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || !el.offsetParent) continue;
    const fg = parse(cs.color); if (!fg) continue;
    const L1 = lum(fg), L2 = lum(bgOf(el));
    const ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight)||400;
    const need = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3 : 4.5;
    if (ratio < need - 0.01) bad.push({text: text.slice(0,40), ratio: Math.round(ratio*100)/100,
                                       need, cls: String(el.className).slice(0,40)});
  }
  return bad;
}
"""


def _page(browser, site, width, theme, view=None):
    ctx = browser.new_context(viewport={"width": width, "height": 900},
                              color_scheme=theme)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" and "ERR_TUNNEL" not in m.text
            and "Failed to load resource" not in m.text else None)
    page.goto(site, wait_until="networkidle")
    page.wait_for_timeout(250)
    if view:
        page.click(f'[data-view-link="{view}"]')
        page.wait_for_timeout(250)
    return ctx, page, errors


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_nothing_scrolls_sideways(browser, site, width, theme):
    """A page that scrolls horizontally on a phone is a broken page."""
    ctx, page, _ = _page(browser, site, width, theme)
    try:
        for view in ("feed", "listings", "searches", "status"):
            page.click(f'[data-view-link="{view}"]')
            page.wait_for_timeout(200)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 0, f"{view} at {width}px/{theme} overflows by {overflow}px"
    finally:
        ctx.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_every_piece_of_text_meets_aa(browser, site, width, theme):
    ctx, page, _ = _page(browser, site, width, theme)
    try:
        for view in ("feed", "listings", "searches", "status"):
            page.click(f'[data-view-link="{view}"]')
            page.wait_for_timeout(200)
            bad = page.evaluate(CONTRAST_JS)
            assert not bad, f"{view} at {width}px/{theme}: {json.dumps(bad[:4])}"
    finally:
        ctx.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_tab_labels_sit_on_one_line(browser, site, width):
    """A count badge must not make its tab taller than the one without."""
    ctx, page, _ = _page(browser, site, width, "light")
    try:
        tops = page.evaluate(
            "() => [...document.querySelectorAll('.tab > span:first-child')]"
            ".map(s => Math.round(s.getBoundingClientRect().top))")
        assert tops, "no tabs rendered"
        assert max(tops) - min(tops) <= 2, f"tab labels vary by {max(tops)-min(tops)}px"
    finally:
        ctx.close()


def test_nothing_is_too_small_to_tap(browser, site):
    ctx, page, _ = _page(browser, site, 390, "light", view="listings")
    try:
        small = page.evaluate("""() => [...document.querySelectorAll('button,a,select,input')]
          .filter(e => { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.height < 30 || r.width < 30); })
          .map(e => (e.textContent || e.tagName).trim().slice(0, 30))""")
        assert not small, f"tap targets under 30px: {small}"
    finally:
        ctx.close()


def test_the_page_does_not_jump_while_it_loads(browser, site):
    """Cumulative layout shift, measured the way a browser measures it."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    try:
        page.add_init_script("""window.__cls = 0;
          new PerformanceObserver(l => { for (const e of l.getEntries())
            if (!e.hadRecentInput) window.__cls += e.value;
          }).observe({type: 'layout-shift', buffered: true});""")
        page.goto(site, wait_until="networkidle")
        page.wait_for_timeout(900)
        cls = page.evaluate("() => window.__cls")
        assert cls < 0.02, f"layout shifted by {cls:.4f}"
    finally:
        ctx.close()


def test_the_detail_sheet_opens_closes_and_hands_focus_back(browser, site):
    ctx, page, errors = _page(browser, site, 390, "light", view="listings")
    try:
        page.click(".card")
        page.wait_for_timeout(250)
        assert page.evaluate("() => document.getElementById('sheet').dataset.open === '1'")
        assert page.evaluate(
            "() => document.activeElement.id === 'sheet-close'"), "focus did not enter the sheet"
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        assert page.evaluate("() => document.getElementById('sheet').dataset.open !== '1'")
        assert page.evaluate(
            "() => document.activeElement.classList.contains('card')"), "focus did not come back"
        assert not errors, errors
    finally:
        ctx.close()


def test_an_alert_link_opens_that_car(browser, site):
    """ntfy sends you to #/listing/<id>; that has to land on the car."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    try:
        page.goto(site, wait_until="networkidle")
        page.wait_for_timeout(250)
        data = json.loads((DOCS / "data.json").read_text())
        target = next(l for l in data["listings"] if not l.get("filtered"))
        page.goto(f"{site}#/listing/{target['id']}", wait_until="networkidle")
        page.wait_for_timeout(400)
        assert page.evaluate("() => document.getElementById('sheet').dataset.open === '1'")
        title = page.inner_text("#sheet-title")
        assert str(target.get("year") or "") in title or target["id"][:6] in title, title
    finally:
        ctx.close()


def test_the_service_worker_and_manifest_are_publishable():
    """The parts that make it installable, checked without a browser."""
    manifest = json.loads((DOCS / "manifest.webmanifest").read_text())
    assert manifest["start_url"].endswith("#/feed")
    for icon in manifest["icons"]:
        assert (DOCS / icon["src"]).exists(), f"{icon['src']} is missing"
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
    sw = (DOCS / "sw.js").read_text()
    assert "data.json" in sw, "the data file needs its own caching rule"


def test_the_app_script_parses():
    """A syntax error here is a blank page, and no other test would see it."""
    node = subprocess.run(["node", "--version"], capture_output=True)
    if node.returncode != 0:
        pytest.skip("no node to parse with")
    out = subprocess.run(["node", "--check", str(DOCS / "app.js")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


class TestVoiceControlCanSayWhatItSees:
    """WCAG 2.5.3, Label in Name.

    The feed cards carried an aria-label reading "Price drop: 2018 BMW M5.
    $66,888 $65,888 alerted" while visibly reading "28h ago … 2018 BMW M5 …
    -$1,000 … $66,888 $65,888 alerted". Someone driving the page by voice
    reads what is on screen and says it, and no phrase they could see matched
    the name the button answered to. Lighthouse caught it; the axe ruleset
    this project runs does not include that check.

    The fix is structural rather than a longer label: the name is built from
    the button's own content, so the two cannot disagree again.
    """

    @staticmethod
    def _code(block: str) -> str:
        """The block with // comments stripped.

        The first version of this test matched the word "aria-label" inside
        the comment explaining why there is no aria-label, and failed.
        """
        import re
        return re.sub(r"^\s*//.*$", "", block, flags=re.M)

    def test_a_feed_card_has_no_aria_label_to_disagree_with(self):
        from pathlib import Path
        js = Path("docs/app.js").read_text()
        block = js.split("const b = el('button', 'ev'")[1].split("li.appendChild(b)")[0]
        assert "aria-label" not in self._code(block), (
            "an aria-label here has to repeat every visible word or it fails "
            "Label in Name - name it from the content instead")

    def test_the_kind_still_reaches_a_screen_reader(self):
        from pathlib import Path
        js = Path("docs/app.js").read_text()
        block = js.split("const b = el('button', 'ev'")[1].split("li.appendChild(b)")[0]
        assert 'class="sr"' in block and "KIND[e.kind].label" in block
