from citadel import web


def test_publisher_page_first_then_search():
    assert (
        web.best_url({"doi": "10.1109/23.659030", "title": "CREME96"})
        == "https://doi.org/10.1109/23.659030"
    )
    assert web.best_url({"arxiv": "2103.00613", "title": "x"}) == "https://arxiv.org/abs/2103.00613"
    url = web.best_url(
        {
            "title": "Radiation Effects In Satellite Cables",
            "authors": ["Wilkenfeld, J. M."],
            "year": 1978,
        }
    )
    assert url.startswith("https://www.google.com/search?q=")
    assert "%22Radiation+Effects+In+Satellite+Cables%22+Wilkenfeld+1978+pdf" in url


def test_google_on_request():
    url = web.best_url({"doi": "10.1/x", "title": "A title"}, google=True)
    assert url.startswith("https://www.google.com/search?q=%22A+title%22")
