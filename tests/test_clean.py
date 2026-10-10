from citadel.clean import clean, text_layer_is_garbled


def test_page_anchors_and_internal_links_go():
    md = (
        '<span id="page-1-0"></span>Turbulence [\\[1](#page-4-0)–4] and [\\[5,6\\].](#page-4-1)'
        " see Eq. [\\(2c\\)](#page-1-0)."
    )
    assert clean(md) == "Turbulence [1–4] and [5,6]. see Eq. (2c).\n"


def test_brackets_inside_maths_are_left_alone():
    assert clean("text $\\[x\\]$ and \\[3\\]") == "text $\\[x\\]$ and [3]\n"


def test_equation_numbers_become_tags():
    md = "$$a = b, \\quad (1)$$\n\n$$p_t = \\bar{p} + p_t$$\n, (2b)\n\n$$c = d \\tag{3}$$"
    out = clean(md)
    assert "$$a = b, \\tag{1}$$" in out
    assert "$$p_t = \\bar{p} + p_t \\tag{2b}$$" in out
    assert out.count("\\tag{3}") == 1


def test_short_display_maths_in_a_sentence_becomes_inline():
    out = clean("with \n$$\\rho_0$$\nthe initial density")
    assert out == "with $\\rho_0$ the initial density\n"


def test_real_display_equations_stay():
    md = "as follows:\n\n$$E = mc^2$$\n\nwhere c is"
    assert "\n\n$$E = mc^2$$\n\n" in clean(md)


def test_images_point_into_figures():
    out = clean("![](_page_2_Figure_12.jpeg)\n![x](https://example.org/a.png)")
    assert "![](figures/_page_2_Figure_12.jpeg)" in out
    assert "![x](https://example.org/a.png)" in out


def test_title_heading_is_dropped_and_levels_follow_numbers():
    md = (
        "## Sudden Viscous Dissipation of Compressing Turbulence\n\nAuthors\n\n"
        "#### Abstract\n\nText\n\n## 1 Introduction\n\n#### 2.2 Structure\n\n"
        "## 3.1.2 Deep\n"
    )
    out = clean(md, "Sudden viscous dissipation of compressing turbulence")
    assert out.startswith("Authors")
    assert "\n## Abstract\n" in out and "\n## 1 Introduction\n" in out
    assert "\n### 2.2 Structure\n" in out and "\n#### 3.1.2 Deep" in out


def test_whitespace_is_tidied():
    assert clean("a  \n\n\n\nb") == "a\n\nb\n"


def test_garbled_text_layers():
    aps = "time scale τ ¼ k=ϵ, μðTÞ ∼ T5=2, ½xŠ, ¼ ¼ ðaÞ ðbÞ"
    assert text_layer_is_garbled(aps)
    assert text_layer_is_garbled("(cid:12) " * 10)
    assert not text_layer_is_garbled("A perfectly normal text layer with one ¼ cup.")
