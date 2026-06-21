"""Tests for FusionPBX REST client."""

import httpx
import pytest
import respx

from app.services.fusionpbx.client import FusionpbxClient
from app.services.exceptions import ServiceUnavailableError, BadRequestError


class TestListDomains:
    """Tests for list_domains method."""

    @respx.mock
    def test_list_domains_returns_parsed(self):
        """Test list_domains returns parsed response."""
        respx.get("http://fpbx/api/domains").mock(
            return_value=httpx.Response(200, json={"domains": [{"name": "a.local"}]})
        )
        c = FusionpbxClient("http://fpbx", "k")
        assert c.list_domains() == [{"name": "a.local"}]

    @respx.mock
    def test_transport_error_raises_service_unavailable(self):
        """Test transport error raises ServiceUnavailableError."""
        respx.get("http://fpbx/api/domains").mock(side_effect=httpx.ConnectError("down"))
        c = FusionpbxClient("http://fpbx", "k")
        with pytest.raises(ServiceUnavailableError):
            c.list_domains()


class TestCreateDomain:
    """Tests for create_domain method."""

    @respx.mock
    def test_create_domain_posts_and_returns_response(self):
        """Test create_domain posts payload and returns response."""
        respx.post("http://fpbx/api/domains").mock(
            return_value=httpx.Response(201, json={"domain_uuid": "123", "name": "test.local"})
        )
        c = FusionpbxClient("http://fpbx", "k")
        result = c.create_domain("test.local")
        assert result == {"domain_uuid": "123", "name": "test.local"}

    @respx.mock
    def test_create_domain_raises_on_400(self):
        """Test create_domain raises BadRequestError on 4xx."""
        respx.post("http://fpbx/api/domains").mock(
            return_value=httpx.Response(400, text="Invalid domain")
        )
        c = FusionpbxClient("http://fpbx", "k")
        with pytest.raises(BadRequestError):
            c.create_domain("test.local")

    @respx.mock
    def test_create_domain_raises_on_transport_error(self):
        """Test create_domain raises ServiceUnavailableError on transport error."""
        respx.post("http://fpbx/api/domains").mock(side_effect=httpx.ConnectError("down"))
        c = FusionpbxClient("http://fpbx", "k")
        with pytest.raises(ServiceUnavailableError):
            c.create_domain("test.local")


class TestListExtensions:
    """Tests for list_extensions method."""

    @respx.mock
    def test_list_extensions_returns_parsed(self):
        """Test list_extensions returns parsed response."""
        respx.get("http://fpbx/api/domains/example.local/extensions").mock(
            return_value=httpx.Response(
                200, json={"extensions": [{"number": "100", "display_name": "Alice"}]}
            )
        )
        c = FusionpbxClient("http://fpbx", "k")
        result = c.list_extensions("example.local")
        assert result == [{"number": "100", "display_name": "Alice"}]

    @respx.mock
    def test_list_extensions_raises_on_transport_error(self):
        """Test list_extensions raises ServiceUnavailableError on transport error."""
        respx.get("http://fpbx/api/domains/example.local/extensions").mock(
            side_effect=httpx.ConnectError("down")
        )
        c = FusionpbxClient("http://fpbx", "k")
        with pytest.raises(ServiceUnavailableError):
            c.list_extensions("example.local")


class TestCreateExtension:
    """Tests for create_extension method."""

    @respx.mock
    def test_create_extension_posts_and_returns_response(self):
        """Test create_extension posts payload and returns response."""
        respx.post("http://fpbx/api/domains/example.local/extensions").mock(
            return_value=httpx.Response(
                201, json={"extension_uuid": "456", "number": "101", "display_name": "Bob"}
            )
        )
        c = FusionpbxClient("http://fpbx", "k")
        result = c.create_extension("example.local", "101", "secret123", "Bob")
        assert result == {"extension_uuid": "456", "number": "101", "display_name": "Bob"}

    @respx.mock
    def test_create_extension_raises_on_400(self):
        """Test create_extension raises BadRequestError on 4xx."""
        respx.post("http://fpbx/api/domains/example.local/extensions").mock(
            return_value=httpx.Response(400, text="Invalid extension")
        )
        c = FusionpbxClient("http://fpbx", "k")
        with pytest.raises(BadRequestError):
            c.create_extension("example.local", "101", "secret123", "Bob")

    @respx.mock
    def test_create_extension_raises_on_transport_error(self):
        """Test create_extension raises ServiceUnavailableError on transport error."""
        respx.post("http://fpbx/api/domains/example.local/extensions").mock(
            side_effect=httpx.ConnectError("down")
        )
        c = FusionpbxClient("http://fpbx", "k")
        with pytest.raises(ServiceUnavailableError):
            c.create_extension("example.local", "101", "secret123", "Bob")
