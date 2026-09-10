import json

from autotrader import archive
from autotrader.archive import archive_listing, prune, size_report
from autotrader.listing import Listing


def car():
    return Listing(id="123456", url="https://www.autotrader.ca/a/x/19_123456_/",
                   title="2021 BMW M5", price=99999, mileage_km=52000,
                   images=["https://cdn.example/a.jpg"])


def test_archiving_off_writes_nothing(tmp_path):
    assert archive_listing(car(), {"mode": "off"}, root=tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_metadata_mode_writes_facts_but_not_the_page(tmp_path):
    folder = archive_listing(car(), {"mode": "metadata"}, root=tmp_path)
    data = json.loads((folder / "metadata.json").read_text())
    assert data["price"] == 99999 and data["archived_at"]
    assert not (folder / "page.html").exists()


def test_full_mode_stores_the_page(tmp_path):
    folder = archive_listing(car(), {"mode": "full"}, html="<html>hi</html>", root=tmp_path)
    assert (folder / "page.html").read_text() == "<html>hi</html>"


def test_images_are_only_downloaded_when_asked(tmp_path):
    class Fetcher:
        def __init__(self): self.calls = 0
        def get_bytes(self, url, referer=None): self.calls += 1; return b"\xff\xd8jpeg"

    f = Fetcher()
    archive_listing(car(), {"mode": "metadata", "images": 0}, fetcher=f, root=tmp_path)
    assert f.calls == 0
    folder = archive_listing(car(), {"mode": "metadata", "images": 1}, fetcher=f, root=tmp_path)
    assert f.calls == 1 and (folder / "photo_1.jpg").exists()


def test_archiving_never_raises(tmp_path):
    class Broken:
        def get_bytes(self, *a, **k): raise RuntimeError("nope")
    assert archive_listing(car(), {"mode": "metadata", "images": 3},
                           fetcher=Broken(), root=tmp_path) is not None


def test_retention_keeps_only_the_newest(tmp_path):
    for i, stamp in enumerate(["2020-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00",
                               "2026-01-01T00:00:00+00:00"]):
        folder = tmp_path / f"listing{i}"
        folder.mkdir()
        (folder / "metadata.json").write_text(json.dumps({"archived_at": stamp}))
    removed = prune({"keep_last": 2, "keep_days": 0}, root=tmp_path)
    assert len(removed) == 1 and removed == ["listing0"]


def test_dry_run_deletes_nothing(tmp_path):
    folder = tmp_path / "old"; folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"archived_at": "2000-01-01T00:00:00+00:00"}))
    assert prune({"keep_days": 30, "keep_last": 0}, root=tmp_path, dry_run=True) == ["old"]
    assert folder.exists()


def test_v1_timestamps_are_understood(tmp_path):
    """v1 wrote 'saved': 'YYYY-MM-DD HH:MM:SS' with no timezone."""
    folder = tmp_path / "old"; folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"saved": "2020-06-24 23:25:18"}))
    assert prune({"keep_days": 30, "keep_last": 0}, root=tmp_path) == ["old"]


def test_size_report_separates_pages_from_photos(tmp_path):
    folder = tmp_path / "x"; folder.mkdir()
    (folder / "page.html").write_text("x" * 1000)
    (folder / "photo_1.jpg").write_bytes(b"y" * 500)
    report = size_report(tmp_path)
    assert report["folders"] == 1
    assert report["html_bytes"] == 1000 and report["image_bytes"] == 500


# --------------------------------------------------------------- compacting

V1_PAGE = """<!doctype html><html><head>
<script type="application/ld+json">{"@context":"https://schema.org",
"@type":"Car","name":"2021 BMW M5 Competition","modelYear":2021,
"mileageFromOdometer":{"@type":"QuantitativeValue","value":52000,"unitCode":"KMT"},
"offers":{"@type":"Offer","price":109999,"priceCurrency":"CAD"}}</script>
</head><body><h1>2021 BMW M5 Competition</h1></body></html>"""


def _v1_folder(root, listing_id="13166607", images=8):
    folder = root / listing_id
    folder.mkdir(parents=True)
    (folder / "page.html").write_text(V1_PAGE, encoding="utf-8")
    (folder / "metadata.json").write_text(json.dumps({
        "id": listing_id,
        "url": f"https://www.autotrader.ca/a/bmw/m5/winnipeg/manitoba/19_{listing_id}_/",
        "title": "4 Winnipeg 1,866 km 52,000 km 2021 BMW M5 Competition Sedan ...",
        "saved": "2025-08-12 02:41:06",
    }), encoding="utf-8")
    for n in range(1, images + 1):
        (folder / f"image_{n}.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 900)
    return folder


def test_compact_extracts_before_it_deletes(tmp_path):
    """The HTML is only thrown away once its facts are in metadata.json."""
    folder = _v1_folder(tmp_path)
    result = archive.compact(root=tmp_path)

    assert result["folders"] == 1 and result["upgraded"] == 1
    assert result["failed"] == []
    assert not (folder / "page.html").exists()
    assert not list(folder.glob("image_*"))

    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    # v1 stored a scraped blob of card text; the real facts were in the page.
    assert meta["year"] == 2021
    assert meta["price"] == 109999
    assert meta["mileage_km"] == 52000
    assert meta["compacted_from"] == "v1-archive"
    # The original save time survives, so history does not jump to today.
    assert meta["archived_at"].startswith("2025-08-12")


def test_compact_dry_run_changes_nothing(tmp_path):
    folder = _v1_folder(tmp_path)
    before = (folder / "metadata.json").read_text(encoding="utf-8")

    result = archive.compact(root=tmp_path, dry_run=True)

    assert result["bytes_freed"] > 0
    assert (folder / "page.html").exists()
    assert len(list(folder.glob("image_*"))) == 8
    assert (folder / "metadata.json").read_text(encoding="utf-8") == before


def test_compact_keeps_a_folder_it_cannot_read(tmp_path):
    """Nothing is deleted from an archive we failed to extract anything from."""
    folder = tmp_path / "99999999"
    folder.mkdir()
    (folder / "page.html").write_text("<html><body>nothing useful</body></html>",
                                      encoding="utf-8")
    # No metadata.json at all, so there is no url to rebuild a listing from.

    result = archive.compact(root=tmp_path)

    assert result["failed"] == ["99999999"]
    assert (folder / "page.html").exists()


def test_compact_is_idempotent(tmp_path):
    _v1_folder(tmp_path)
    archive.compact(root=tmp_path)
    again = archive.compact(root=tmp_path)
    assert again["folders"] == 0 and again["bytes_freed"] == 0


def test_compact_can_keep_some_photos(tmp_path):
    folder = _v1_folder(tmp_path)
    archive.compact(root=tmp_path, keep_images=2)
    assert sorted(p.name for p in folder.glob("image_*")) == ["image_1.jpg", "image_2.jpg"]


def test_a_compacted_archive_still_migrates_with_full_detail(tmp_path):
    """Compacting must not cost a re-migration the facts it used to recover."""
    from autotrader.migrate import read_archive

    folder = _v1_folder(tmp_path)
    rich_before, saved_before = read_archive(folder)
    archive.compact(root=tmp_path)
    rich_after, saved_after = read_archive(folder)

    assert (rich_after.year, rich_after.price, rich_after.mileage_km) == \
           (rich_before.year, rich_before.price, rich_before.mileage_km)
    assert saved_after == saved_before
