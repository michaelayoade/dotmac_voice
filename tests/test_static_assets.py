from app.templates import _static_asset_url

# Use a committed static file (input.css) rather than styles.css, which is a
# gitignored Tailwind build artifact absent outside the Docker build.


def test_static_asset_url_adds_content_version() -> None:
    url = _static_asset_url("/static/css/input.css")
    assert url.startswith("/static/css/input.css?v=")
    assert len(url.rsplit("=", 1)[1]) == 12


def test_static_asset_url_preserves_existing_query_separator() -> None:
    url = _static_asset_url("/static/css/input.css?theme=default")
    assert "&v=" in url
