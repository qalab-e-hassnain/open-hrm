"""
Either signal places you at the office.

A desk PC has no GPS: navigator.geolocation answers POSITION_UNAVAILABLE and
the portal sends the check-in with no coordinates at all. Demanding a fix told
people sitting at their own desks that they were not in the office.
"""
import pytest

from app.core.geofencing import is_office_network, office_networks
from app.schemas.attendance import AttendanceCheckIn, AttendanceCheckOut


class TestCoordinatesAreOptional:
    """Required coordinates produced a 422 naming the missing field."""

    def test_a_checkin_with_no_fix_is_accepted(self):
        payload = AttendanceCheckIn(address="Office desk")
        assert payload.latitude is None and payload.longitude is None

    def test_a_checkout_with_no_fix_is_accepted(self):
        assert AttendanceCheckOut().latitude is None

    def test_a_fix_still_validates_its_range(self):
        from pydantic import ValidationError
        AttendanceCheckIn(latitude=33.6, longitude=72.9)       # fine
        with pytest.raises(ValidationError):
            AttendanceCheckIn(latitude=91.0, longitude=72.9)
        with pytest.raises(ValidationError):
            AttendanceCheckIn(latitude=33.6, longitude=181.0)


class TestOfficeNetwork:
    """
    The office is dual-stacked, so a request arrives on whichever family the
    client picks — both have to match. The IPv6 host part rotates under
    privacy extensions, so the /64 prefix is what is matched, not the address.
    """

    @pytest.fixture(autouse=True)
    def _ranges(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(
            settings, "OFFICE_IP_RANGES",
            "154.192.71.73/32, 2407:d000:2b:4319::/64", raising=False)

    def test_both_families_parse(self):
        assert len(office_networks()) == 2

    def test_the_office_ipv4_matches(self):
        assert is_office_network("154.192.71.73") is True

    def test_another_ipv4_does_not(self):
        assert is_office_network("154.192.71.74") is False

    def test_any_host_in_the_ipv6_prefix_matches(self):
        # Privacy extensions rotate the host part; the prefix is the office.
        assert is_office_network("2407:d000:2b:4319:ec4a:2777:f9a9:324e") is True
        assert is_office_network("2407:d000:2b:4319:0:0:0:1") is True

    def test_a_different_ipv6_prefix_does_not(self):
        assert is_office_network("2407:d000:2b:431a::1") is False

    def test_a_private_address_never_matches(self):
        # A LAN address is never on the wire by the time it reaches the server;
        # every office request arrives NAT-translated.
        assert is_office_network("192.168.18.73") is False

    def test_rubbish_and_empty_are_safe(self):
        assert is_office_network("") is False
        assert is_office_network("not-an-ip") is False
        assert is_office_network(None) is False


class TestFailsSafe:
    def test_an_unset_range_refuses_rather_than_admits(self, monkeypatch):
        # The whole design rests on this: a stale or missing value means
        # GPS-less PCs need a fix again — where they already stood — rather
        # than letting the world in.
        from app.config import settings
        monkeypatch.setattr(settings, "OFFICE_IP_RANGES", "", raising=False)
        assert is_office_network("154.192.71.73") is False

    def test_an_unreadable_entry_is_skipped_not_fatal(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "OFFICE_IP_RANGES",
                            "nonsense, 154.192.71.73/32", raising=False)
        nets = office_networks()
        assert len(nets) == 1
        assert is_office_network("154.192.71.73") is True
