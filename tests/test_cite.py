from citadel import cite
from conftest import make_ref


def test_article_bibtex():
    ref = make_ref(
        volume="401",
        issue="5",
        pages="229-380",
        authors=["Verma, Mahendra K.", "O'Neil, K."],
        title="MHD & 50% $\\beta$_x",
    )
    entry = cite.bibtex(ref)
    assert entry.startswith("@article{verma2004statistical,\n")
    assert "  author = {Verma, Mahendra K. and O'Neil, K.}" in entry
    assert "  title = {{MHD \\& 50\\% $\\beta$\\_x}}" in entry
    assert "  journal = {Physics Reports}" in entry
    assert "  pages = {229--380}" in entry and "  number = {5}" in entry
    assert "  doi = {10.1016/j.physrep.2004.07.007}" in entry
    assert entry.endswith("}\n")


def test_other_types():
    book = cite.bibtex(make_ref(type="book", journal="", publisher="IOP", isbn="9780750333078"))
    assert book.startswith("@book{") and "publisher = {IOP}" in book and "isbn" in book
    chapter = cite.bibtex(make_ref(type="chapter", journal="High-Energy-Density Physics"))
    assert chapter.startswith("@incollection{") and "booktitle = {High-Energy-Density" in chapter
    preprint = cite.bibtex(make_ref(type="preprint", doi="", arxiv="2405.16626", journal=""))
    assert preprint.startswith("@misc{") and "eprint = {2405.16626}" in preprint
    assert "archiveprefix = {arXiv}" in preprint


def test_reference_article():
    ref = make_ref(volume="401", issue="5", pages="229-380")
    assert cite.reference(ref) == (
        "Verma, M. K. (2004). Statistical theory of magnetohydrodynamic turbulence: recent "
        "results. Physics Reports, 401(5), 229–380. "
        "https://doi.org/10.1016/j.physrep.2004.07.007"
    )


def test_reference_authors_and_books():
    many = make_ref(authors=[f"Author{i}, A." for i in range(7)])
    assert cite.reference(many).startswith("Author0, A., Author1, A., Author2, A., et al. (2004)")
    two = make_ref(authors=["Davidovits, Seth", "Fisch, Nathaniel J."])
    assert cite.reference(two).startswith("Davidovits, S., & Fisch, N. J. (2004).")
    book = make_ref(
        type="book",
        journal="",
        publisher="IOP Publishing",
        doi="",
        authors=["Dunlap, Mark"],
        year=2021,
        title="Energy from Nuclear Fusion",
    )
    assert cite.reference(book) == "Dunlap, M. (2021). Energy from Nuclear Fusion. IOP Publishing."
    assert cite.reference(make_ref(authors=["NRL"], year=None)).startswith("NRL (n.d.).")
    hyphen = make_ref(authors=["Picard, Jean-Luc"])
    assert cite.reference(hyphen).startswith("Picard, J.-L. (2004)")


def test_maths_in_titles_becomes_plain_text():
    ref = make_ref(title="Spectral index $-3/2$ in MHD")
    assert "Spectral index -3/2 in MHD." in cite.reference(ref)


def test_format_ref():
    ref = make_ref()
    assert cite.format_ref(ref, "key") == "verma2004statistical"
    assert cite.format_ref(ref, "bibtex").startswith("@article")


def test_export_bib_rewrites_only_on_change(tmp_path):
    from citadel.index import Index
    from conftest import file_ref

    root = tmp_path / "lib"
    file_ref(root, make_ref())
    file_ref(
        root,
        make_ref(citekey="abel2001x", title="Another paper here", topic="optics", doi="10.1/a"),
    )
    index = Index(tmp_path / "index.db", root)
    index.reindex()
    path, changed, count = cite.export_bib(index)
    text = path.read_text()
    assert changed and count == 2 and path == root / "library.bib"
    assert text.index("@article{abel2001x") < text.index("@article{verma2004statistical")
    mtime = path.stat().st_mtime_ns
    _, changed, _ = cite.export_bib(index)
    assert not changed and path.stat().st_mtime_ns == mtime
    index.close()
