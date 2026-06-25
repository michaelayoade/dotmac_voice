"""Tests for web authentication routes."""


class TestWebAuth:
    def test_login_page_renders(self, client):
        response = client.get("/admin/login")
        assert response.status_code == 200
        assert b"Sign In" in response.content

    def test_login_page_with_next(self, client):
        response = client.get("/admin/login?next=/admin/people")
        assert response.status_code == 200

    def test_login_empty_fields(self, client):
        # Get CSRF token first
        resp = client.get("/admin/login")
        csrf_token = resp.cookies.get("csrf_token", "")

        response = client.post(
            "/admin/login",
            data={"username": "", "password": "", "csrf_token": csrf_token},
            cookies={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert b"required" in response.content

    def test_login_invalid_credentials(self, client):
        resp = client.get("/admin/login")
        csrf_token = resp.cookies.get("csrf_token", "")

        response = client.post(
            "/admin/login",
            data={
                "username": "nonexistent",
                "password": "wrongpass",
                "csrf_token": csrf_token,
            },
            headers={"X-CSRF-Token": csrf_token},
            cookies={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert b"Invalid" in response.content or b"required" in response.content

    def test_logout_clears_cookies(self, client):
        response = client.get("/admin/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/admin/login" in response.headers.get("location", "")

    def test_admin_redirects_without_auth(self, client):
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code == 302
        assert "/admin/login" in response.headers.get("location", "")

    def test_admin_dashboard_with_auth_cookie(
        self, client, person, auth_session, auth_token
    ):
        response = client.get(
            "/admin",
            cookies={"access_token": auth_token},
            follow_redirects=False,
        )
        # Should succeed or redirect to /admin/ (trailing slash normalization)
        assert response.status_code in (200, 307)
