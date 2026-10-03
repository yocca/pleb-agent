from pleb_agent.extract import content
from tests.fakes import image_pdf, text_pdf

DINNER = """<html><body><main><h1>Dinner</h1><p>Roast chicken with potatoes, $24.</p>
<p>Steak frites, $32. Burrata with tomatoes, $16.</p></main></body></html>"""
HAPPY = """<html><body><main><h1>Specials</h1>
<p>Happy Hour Mon–Fri 4–7pm, $6 drafts</p><p>Kitchen open until 11.</p></main></body></html>"""


def test_no_signal_means_no_model_input() -> None:
    assert content.gate(content.html_to_text(DINNER)) is None


def test_signal_passes_the_gate() -> None:
    gated = content.gate(content.html_to_text(HAPPY))
    assert gated is not None
    assert "Happy Hour Mon–Fri 4–7pm, $6 drafts" in gated


def test_other_signals() -> None:
    assert content.has_signal("HALF PRICE oysters every Tuesday")
    assert content.has_signal("Drink specials nightly")
    assert content.has_signal("hh daily")
    assert content.has_signal("Weekdays 5-7: $5 beers")
    assert not content.has_signal("Open 5-11 daily")  # time range, but no price nearby
    assert not content.has_signal("Thursday through Sunday")


def test_gate_keeps_text_near_signal_and_bounds_size() -> None:
    text = "filler " * 2000 + "Happy hour 4-7" + " filler" * 2000
    gated = content.gate(text)
    assert gated is not None
    assert "Happy hour 4-7" in gated
    assert len(gated) <= content.GATE_MAX


def test_pdf_text() -> None:
    assert "Happy Hour 4-7pm" in content.pdf_to_text(text_pdf("Happy Hour 4-7pm $5 beers"))


def test_image_only_pdf_has_no_text_and_renders() -> None:
    pdf = image_pdf()
    assert content.pdf_to_text(pdf) == ""
    images = content.pdf_to_images(pdf)
    assert len(images) == 1
    assert images[0].media_type == "image/png"
    assert images[0].data.startswith(b"\x89PNG")


def test_menu_images_detected() -> None:
    html = """<img src="/logo.png" alt="Logo"><img src="/img/happy-hour.jpg" alt="">
    <img src="data:image/png;base64,AAA" alt="happy hour">"""
    assert content.menu_image_urls(html, "https://tavern.example/", page_is_candidate=False) == [
        "https://tavern.example/img/happy-hour.jpg"
    ]
    # On a candidate page (e.g. /happy-hour) every image may be the menu.
    assert len(content.menu_image_urls(html, "https://tavern.example/hh", page_is_candidate=True)) == 2
