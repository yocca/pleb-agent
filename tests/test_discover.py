from pleb_agent.crawl import discover

HOME = "https://tavern.example/"
SITE = "tavern.example"


def test_nav_link_to_happy_hour_is_a_candidate() -> None:
    html = '<nav><a href="/about">About</a><a href="/happy-hour">Happy Hour</a></nav>'
    links = discover.parse_links(html, HOME)
    assert "https://tavern.example/happy-hour" in discover.choose_candidates(HOME, SITE, links, [], [])


def test_jsonld_menu_ranks_first() -> None:
    html = """<a href="/drinks">Drinks</a><script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "BarOrPub",
     "hasMenu": "https://tavern.example/menus/drinks.pdf"}</script>"""
    menus = discover.jsonld_menu_urls(html, HOME)
    assert menus == ["https://tavern.example/menus/drinks.pdf"]
    candidates = discover.choose_candidates(HOME, SITE, discover.parse_links(html, HOME), menus, [])
    assert candidates[0] == "https://tavern.example/menus/drinks.pdf"


def test_sitemap_entries_are_candidates() -> None:
    xml = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://tavern.example/specials</loc></url><url><loc>https://tavern.example/careers</loc></url>
    </urlset>"""
    candidates = discover.choose_candidates(HOME, SITE, [], [], discover.sitemap_urls(xml))
    assert candidates == ["https://tavern.example/specials"]


def test_candidates_capped_and_off_site_excluded() -> None:
    html = "".join(f'<a href="/menu-{i}">Menu {i}</a>' for i in range(20))
    html += '<a href="https://resy.com/tavern">Menu on Resy</a><a href="/">Home</a>'
    candidates = discover.choose_candidates(HOME, SITE, discover.parse_links(html, HOME), [], [])
    assert len(candidates) == 5
    assert all(c.startswith("https://tavern.example/menu-") for c in candidates)


def test_terms_prohibiting_robots() -> None:
    text = "Welcome to our site. You may not use robots, spiders or scrapers to access this site. Enjoy!"
    assert discover.prohibits_automation(text) == ("You may not use robots, spiders or scrapers to access this site.")


def test_permissive_terms() -> None:
    text = "These terms govern reservations. Prices may change. Gift cards are not refundable."
    assert discover.prohibits_automation(text) is None


def test_terms_link_found() -> None:
    links = discover.parse_links('<footer><a href="/legal/terms">Terms of Use</a></footer>', HOME)
    assert discover.terms_link(links, SITE) == "https://tavern.example/legal/terms"
