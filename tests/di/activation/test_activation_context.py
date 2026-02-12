from dishka import Has, Marker
from dishka.entities.marker import AndMarker, NotMarker, OrMarker


class TestHasMarker:
    @staticmethod
    def test_has_creates_marker_for_type() -> None:
        marker = Has(str)
        assert marker.value is str

    @staticmethod
    def test_has_supports_negation() -> None:
        marker = ~Has(str)
        assert isinstance(marker, NotMarker)

    @staticmethod
    def test_has_supports_or_composition() -> None:
        marker = Has(str) | Has(int)
        assert isinstance(marker, OrMarker)

    @staticmethod
    def test_has_supports_and_composition() -> None:
        marker = Has(str) & Has(int)
        assert isinstance(marker, AndMarker)


class TestMarkerComposition:
    @staticmethod
    def test_marker_negation() -> None:
        marker = Marker('test')
        negated = ~marker
        assert isinstance(negated, NotMarker)

    @staticmethod
    def test_marker_or() -> None:
        m1 = Marker('a')
        m2 = Marker('b')
        combined = m1 | m2
        assert isinstance(combined, OrMarker)

    @staticmethod
    def test_marker_and() -> None:
        m1 = Marker('a')
        m2 = Marker('b')
        combined = m1 & m2
        assert isinstance(combined, AndMarker)
