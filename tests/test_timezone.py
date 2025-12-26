"""Unit tests for timezone offset calculation."""
import pytest
from zoneinfo import ZoneInfoNotFoundError

from huckleberry_api import HuckleberryAPI


class TestTimezoneOffset:
    """Unit tests for timezone offset calculation."""

    def test_utc_returns_zero(self):
        """UTC timezone should return 0 offset."""
        api = HuckleberryAPI(email="test", password="test", timezone="UTC")
        assert api._get_timezone_offset_minutes() == 0.0

    def test_fixed_offset_positive_utc(self):
        """Fixed offset timezone east of UTC (e.g., UTC+2 = -120 minutes)."""
        # Etc/GMT-2 is actually UTC+2 (POSIX convention is inverted)
        api = HuckleberryAPI(email="test", password="test", timezone="Etc/GMT-2")
        assert api._get_timezone_offset_minutes() == -120.0

    def test_fixed_offset_negative_utc(self):
        """Fixed offset timezone west of UTC (e.g., UTC-5 = +300 minutes)."""
        # Etc/GMT+5 is actually UTC-5 (POSIX convention is inverted)
        api = HuckleberryAPI(email="test", password="test", timezone="Etc/GMT+5")
        assert api._get_timezone_offset_minutes() == 300.0

    def test_invalid_timezone_raises_error(self):
        """Invalid timezone string should raise ZoneInfoNotFoundError."""
        with pytest.raises(ZoneInfoNotFoundError):
            HuckleberryAPI(email="test", password="test", timezone="Invalid/Timezone")

    def test_real_timezone_reasonable_range(self):
        """Real timezone should return offset in reasonable range."""
        api = HuckleberryAPI(email="test", password="test", timezone="America/New_York")
        offset = api._get_timezone_offset_minutes()
        # New York is UTC-5 (300) or UTC-4 (240) depending on DST
        assert 240 <= offset <= 300

    def test_real_timezone_europe(self):
        """European timezone should return expected offset range."""
        api = HuckleberryAPI(email="test", password="test", timezone="Europe/Berlin")
        offset = api._get_timezone_offset_minutes()
        # Berlin is UTC+1 (-60) or UTC+2 (-120) depending on DST
        assert -120 <= offset <= -60
