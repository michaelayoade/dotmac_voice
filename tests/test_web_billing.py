"""Tests for billing admin web pages."""


class TestWebBillingProducts:
    def test_list_requires_auth(self, client):
        response = client.get("/admin/billing/products", follow_redirects=False)
        assert response.status_code == 302

    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/products",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Products" in response.content

    def test_create_page(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/products/create",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200


class TestWebBillingPrices:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/prices",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Prices" in response.content

    def test_create_page(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/prices/create",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200


class TestWebBillingCustomers:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/customers",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Customers" in response.content

    def test_create_page(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/customers/create",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200


class TestWebBillingSubscriptions:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/subscriptions",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Subscriptions" in response.content


class TestWebBillingInvoices:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/invoices",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Invoices" in response.content


class TestWebBillingPaymentMethods:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/payment-methods",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Payment Methods" in response.content


class TestWebBillingCoupons:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/coupons",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Coupons" in response.content

    def test_create_page(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/coupons/create",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200


class TestWebBillingEntitlements:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/entitlements",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Entitlements" in response.content

    def test_create_page(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/entitlements/create",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200


class TestWebBillingWebhookEvents:
    def test_list(self, client, person, auth_session, auth_token):
        response = client.get(
            "/admin/billing/webhook-events",
            cookies={"access_token": auth_token},
        )
        assert response.status_code == 200
        assert b"Webhook Events" in response.content
