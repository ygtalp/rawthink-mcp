"""Controlled-vocabulary regression locks.

A real vault reached 46 entity types and 66 relation types. Two causes, both
locked down here: entityType carried both role and subject area, and unknown
relation types were accepted with a warning nothing acted on.
"""
import pytest

from rawthink_mcp import config
from rawthink_mcp.graph import (SchemaError, normalize_relation_type,
                                validate_domain, validate_entity_type,
                                validate_epistemic, validate_visibility)


class TestEntityType:
    def test_accepts_canonical(self):
        for t in config.ENTITY_TYPES:
            assert validate_entity_type(t) == t

    def test_is_case_insensitive(self):
        assert validate_entity_type("DECISION") == "decision"

    def test_rejects_the_types_that_caused_the_drift(self):
        for legacy in ("karar", "kavram", "icgoru", "bug-fix", "saglik-bulgusu"):
            with pytest.raises(SchemaError):
                validate_entity_type(legacy)

    def test_is_required(self):
        """It used to default to 'concept', which is a guess written as a fact."""
        with pytest.raises(SchemaError):
            validate_entity_type(None)

    def test_error_points_at_domain(self):
        """A subject-area value should be redirected, not just refused."""
        with pytest.raises(SchemaError, match="domain"):
            validate_entity_type("health-finding")


class TestDomain:
    def test_accepts_known(self):
        for d in ("software", "music", "galaxy", "philosophy"):
            assert validate_domain(d) == d

    def test_rejects_unknown(self):
        with pytest.raises(SchemaError):
            validate_domain("astrology")

    def test_is_required_for_new_entities(self):
        with pytest.raises(SchemaError):
            validate_domain(None)


class TestEpistemic:
    def test_absent_means_unknown_not_assertion(self):
        """Recording an unstated claim as an assertion invents a claim."""
        assert validate_epistemic(None) == "unknown"
        assert validate_epistemic("") == "unknown"

    def test_accepts_the_four_values(self):
        for v in config.EPISTEMIC_VALUES:
            assert validate_epistemic(v) == v

    def test_rejects_invented_values(self):
        with pytest.raises(SchemaError):
            validate_epistemic("maybe")


class TestVisibility:
    def test_defaults_to_private(self):
        assert validate_visibility(None) == "private"

    def test_rejects_unknown(self):
        with pytest.raises(SchemaError):
            validate_visibility("public")


class TestRelationType:
    def test_accepts_canonical(self):
        for r in config.RELATION_TYPES:
            assert normalize_relation_type(r) == r

    @pytest.mark.parametrize("alias,canonical", [
        ("connected_to", "related_to"),
        ("aspect_of", "part_of"),
        ("demonstrates", "exemplifies"),
        ("extends", "evolved_into"),
        ("icerir", "part_of"),
        ("arastirir", "investigates"),
    ])
    def test_folds_known_synonyms(self, alias, canonical):
        assert normalize_relation_type(alias) == canonical

    def test_normalizes_separators_and_case(self):
        assert normalize_relation_type("Connected-To") == "related_to"

    def test_rejects_rather_than_warns(self):
        """This is the whole point: a warning that lets the write through is a
        decision to allow it, written in the voice of disapproval."""
        with pytest.raises(SchemaError):
            normalize_relation_type("beyin-entropisini-artirir")

    def test_is_required(self):
        with pytest.raises(SchemaError):
            normalize_relation_type(None)
