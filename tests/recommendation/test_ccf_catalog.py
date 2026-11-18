from __future__ import annotations


def test_builtin_ccf_catalog_is_complete_versioned_and_uses_the_rename_revision() -> None:
    from paper_reader.recommendation.ccf import load_builtin_ccf_catalog

    catalog = load_builtin_ccf_catalog()

    assert catalog.catalog_id == "ccf-2022-sixth-2024-rename"
    assert catalog.edition == 6
    assert catalog.rating_year == 2022
    assert catalog.rename_revision_date == "2024-06-28"
    assert catalog.source_pdf_sha256 == (
        "9390786c231888ad441b639bc5ae734b5809a8b86602bd85ef220f2073913bca"
    )
    assert len(catalog.entries) == 649
    assert sum(entry.venue_type == "journal" for entry in catalog.entries) == 284
    assert sum(entry.venue_type == "conference" for entry in catalog.entries) == 365


def test_ccf_lookup_matches_current_names_and_rename_aliases_without_fuzzy_guessing() -> None:
    from paper_reader.recommendation.ccf import load_builtin_ccf_catalog

    catalog = load_builtin_ccf_catalog()

    neurips = catalog.find("NeurIPS", venue_type="conference")
    old_fse = catalog.find("FSE/ESEC", venue_type="conference")
    old_scc = catalog.find("IEEE SCC", venue_type="conference")
    old_its = catalog.find("ITS", venue_type="conference")
    unknown = catalog.find("A venue that merely resembles NeurIPS", venue_type="conference")

    assert neurips is not None
    assert (neurips.rank, neurips.category, neurips.source_page) == (
        "A",
        "artificial_intelligence",
        57,
    )
    assert old_fse is not None
    assert old_fse.name == "ACM International Conference on the Foundations of Software Engineering"
    assert old_fse.rank == "A"
    assert old_scc is not None
    assert (old_scc.abbreviation, old_scc.rank) == ("SSE", "C")
    assert old_its is not None
    assert (old_its.abbreviation, old_its.rank) == ("ISS", "B")
    assert unknown is None


def test_ccf_evidence_is_only_applicable_to_computer_science_venues() -> None:
    from paper_reader.recommendation.ccf import ccf_rating_evidence
    from paper_reader.recommendation.relevance import EvidenceStatus

    observed = ccf_rating_evidence(
        "CVPR",
        venue_type="conference",
        is_computer_science=True,
    )
    outside_scope = ccf_rating_evidence(
        "CVPR",
        venue_type="conference",
        is_computer_science=False,
    )
    not_listed = ccf_rating_evidence(
        "Local Workshop on Cancer Imaging",
        venue_type="conference",
        is_computer_science=True,
    )

    assert observed.status is EvidenceStatus.OBSERVED
    assert observed.value == 100
    assert observed.evidence["rank"] == "A"
    assert outside_scope.status is EvidenceStatus.NOT_APPLICABLE
    assert outside_scope.reason == "ccf_not_applicable_outside_computer_science"
    assert not_listed.status is EvidenceStatus.UNKNOWN
    assert not_listed.reason == "venue_not_listed_in_ccf"
