# Copyright (c) 2026, Titansoft Limited and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from titan_digitax.titan_digitax.utils.company_config import (
    get_enabled_digitax_companies,
    is_digitax_enabled_for_company,
)


def _mock_conf():
    """A frappe.conf stand-in that answers .get("sync_with_digitax") truthily
    without raising on the many other attributes framework internals read.

    frappe.utils.get_url() — called by get_digitax_callback_url_for_sales_with_items
    — reads frappe.conf.http_port, .developer_mode, .restart_supervisor_on_update
    etc. directly (not via .get()). A bare object exposing only .get() raises
    AttributeError the moment any of those are touched; a MagicMock answers all of
    them as a (harmless, falsy-safe) mock instead.
    """
    conf = MagicMock()
    conf.get.side_effect = lambda k, d=None: True if k == "sync_with_digitax" else d
    return conf


def _ensure_company(company):
    """Create a throwaway Company for a fictitious test name, if it doesn't exist.

    `Digitax Company Settings.company` is a Link field, so a row cannot be inserted
    against a company that isn't real. FrappeTestCase rolls back the whole
    transaction afterwards, so this never lingers.

    The abbreviation is randomised rather than derived from the name: Company
    abbreviations are unique site-wide, and a name-derived abbreviation can collide
    with one left behind by an earlier test run against the same site.
    """
    if frappe.db.exists("Company", company):
        return
    frappe.get_doc(
        {
            "doctype": "Company",
            "company_name": company,
            "abbr": frappe.generate_hash(length=6).upper(),
            "default_currency": "KES",
            "country": "Kenya",
        }
    ).insert(ignore_permissions=True)


def _make_company_settings_doc(company, enabled=1, **extra):
    """Create (or update) a Digitax Company Settings row for a test company.

    Every DigiTax setting is per company now, with no shared/global tier, so
    base_url and api_key are mandatory on every row — these tests supply
    throwaway values since no test here actually calls the real DigiTax API.
    Idempotent by company name: successive test methods within the same run share
    a connection, and records created by an earlier method remain visible to a
    later one, so a blind insert() would hit a duplicate primary key.
    """
    _ensure_company(company)
    if frappe.db.exists("Digitax Company Settings", company):
        doc = frappe.get_doc("Digitax Company Settings", company)
        doc.enabled = enabled
        for field, value in extra.items():
            doc.set(field, value)
        doc.save(ignore_permissions=True)
        return doc

    doc = frappe.get_doc(
        {
            "doctype": "Digitax Company Settings",
            "company": company,
            "enabled": enabled,
            "base_url": "https://sandbox.digitax.test/api/v1",
            "api_key": "test-api-key",
            **extra,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


class TestDigitaxCompanyConfig(FrappeTestCase):
    """Tests for the per-company eligibility helpers, backed by Digitax Company Settings."""

    def test_no_row_for_company_returns_false(self):
        self.assertFalse(is_digitax_enabled_for_company("__no_such_company__"))

    def test_row_exists_but_disabled_returns_false(self):
        _make_company_settings_doc("Acme Kenya", enabled=0)
        self.assertFalse(is_digitax_enabled_for_company("Acme Kenya"))

    def test_row_exists_and_enabled_returns_true(self):
        _make_company_settings_doc("Acme Kenya", enabled=1)
        self.assertTrue(is_digitax_enabled_for_company("Acme Kenya"))

    def test_other_company_enabled_but_not_queried(self):
        _make_company_settings_doc("Acme Kenya", enabled=1)
        self.assertFalse(is_digitax_enabled_for_company("Braeburn Nairobi"))

    def test_empty_company_arg_returns_false(self):
        _make_company_settings_doc("Acme Kenya", enabled=1)
        self.assertFalse(is_digitax_enabled_for_company(""))
        self.assertFalse(is_digitax_enabled_for_company(None))

    def test_get_enabled_companies_returns_only_enabled(self):
        _make_company_settings_doc("Enabled Co", enabled=1)
        _make_company_settings_doc("Disabled Co", enabled=0)
        _make_company_settings_doc("Also Enabled", enabled=1)

        result = set(get_enabled_digitax_companies())
        self.assertIn("Enabled Co", result)
        self.assertIn("Also Enabled", result)
        self.assertNotIn("Disabled Co", result)


class TestDigitaxCompanySettingsValidation(FrappeTestCase):
    """Tests for DigitaxCompanySettings.validate() — group/country eligibility (D15).

    This validation used to live on the Digitax Settings Single's
    company_configurations child table (_validate_company_configurations); it moved
    to DigitaxCompanySettings._validate_company_country when that table was retired
    in favour of a standalone per-company doctype (D15/D18, mirroring Engage Settings),
    and target_country itself later moved from the (now deleted) Digitax Settings
    Single onto this doctype so every row is fully self-contained.

    Real Company records are used rather than mocking frappe.db.get_value: that call
    is not module-scoped, so a global mock also intercepts the framework's own
    internal lookups (Meta loading, defaults, ...) during Company creation and
    produces confusing unrelated failures.
    """

    def _make_group_company(self, name):
        _ensure_company(name)
        frappe.db.set_value("Company", name, "is_group", 1)
        return name

    def _make_leaf_company(self, name, country):
        _ensure_company(name)
        frappe.db.set_value("Company", name, "country", country)
        return name

    def test_enabled_group_company_raises(self):
        company = self._make_group_company("__test_group__")
        doc = frappe.get_doc(
            {
                "doctype": "Digitax Company Settings",
                "company": company,
                "enabled": 1,
                "base_url": "https://sandbox.digitax.test/api/v1",
                "api_key": "test-api-key",
            }
        )
        with self.assertRaises(frappe.ValidationError):
            doc.validate()

    def test_enabled_non_target_country_raises(self):
        company = self._make_leaf_company("__test_other_country__", "Uganda")

        doc = frappe.get_doc(
            {
                "doctype": "Digitax Company Settings",
                "company": company,
                "enabled": 1,
                "base_url": "https://sandbox.digitax.test/api/v1",
                "api_key": "test-api-key",
                "target_country": "Kenya",
            }
        )
        with self.assertRaises(frappe.ValidationError):
            doc.validate()

    def test_disabled_row_skips_group_and_country_checks(self):
        # A group company would raise if checked; disabled rows are exempt.
        company = self._make_group_company("__test_disabled_group__")
        doc = frappe.get_doc(
            {
                "doctype": "Digitax Company Settings",
                "company": company,
                "enabled": 0,
                "base_url": "https://sandbox.digitax.test/api/v1",
                "api_key": "test-api-key",
            }
        )
        doc.validate()  # should not raise


class TestSendInvoiceEligibility(FrappeTestCase):
    """Integration-style tests for send_sales_invoice_to_digitax eligibility gating."""

    @patch("frappe.conf", new_callable=lambda: _mock_conf())
    @patch("titan_digitax.titan_digitax.utils.sales._post_to_digitax")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_doc")
    def test_no_row_skips_send(self, mock_get_doc, mock_post, _conf):
        """Invoice for a company with no Digitax Company Settings row is skipped."""
        mock_doc = MagicMock()
        mock_doc.company = "__no_digitax_settings_company__"
        mock_doc.is_return = 0
        mock_doc.get.return_value = None  # no custom_sale_id
        mock_doc.custom_sale_id = None
        mock_doc.custom_error_message = None
        mock_get_doc.return_value = mock_doc

        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax
        result = send_sales_invoice_to_digitax("SI-TEST-001")

        self.assertTrue(result.get("skipped"))
        mock_post.assert_not_called()

    @patch("frappe.conf", new_callable=lambda: _mock_conf())
    @patch("titan_digitax.titan_digitax.utils.sales._post_to_digitax")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_doc")
    def test_disabled_row_skips_send(self, mock_get_doc, mock_post, _conf):
        """Invoice for a company with a disabled Digitax Company Settings row is skipped."""
        _make_company_settings_doc("Acme Kenya", enabled=0)

        mock_doc = MagicMock()
        mock_doc.company = "Acme Kenya"
        mock_doc.is_return = 0
        mock_doc.get.return_value = None
        mock_doc.custom_sale_id = None
        mock_doc.custom_error_message = None
        mock_get_doc.return_value = mock_doc

        with patch("titan_digitax.titan_digitax.utils.sales.frappe.db.get_value", return_value="Kenya"):
            from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax
            result = send_sales_invoice_to_digitax("SI-TEST-002")

        self.assertTrue(result.get("skipped"))
        mock_post.assert_not_called()

    @patch("frappe.conf", new_callable=lambda: _mock_conf())
    @patch("titan_digitax.titan_digitax.utils.sales.build_digitax_items_payload")
    @patch("titan_digitax.titan_digitax.utils.sales._post_to_digitax")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_doc")
    def test_credit_note_allowed_even_when_row_disabled(
        self, mock_get_doc, mock_post, mock_build_items, _conf
    ):
        """Credit note against an already-sent invoice bypasses the company row check.

        Item aggregation (build_digitax_items_payload) is mocked out: this test's
        subject is the eligibility gate, not D9/D14 line aggregation, which has its
        own dedicated coverage.
        """
        mock_build_items.return_value = {"ok": True, "items": [{"item_name": "Test Item"}]}
        _make_company_settings_doc("Acme Kenya", enabled=0)

        mock_doc = MagicMock()
        mock_doc.name = "SI-RETURN-001"
        mock_doc.company = "Acme Kenya"
        mock_doc.customer = "__test_customer__"
        mock_doc.customer_name = "Test Customer"
        mock_doc.is_return = 1
        mock_doc.return_against = "SI-ORIG-001"
        mock_doc.grand_total = -5000
        mock_doc.custom_sale_id = "digitax-sale-001"
        mock_doc.custom_trader_invoice_number = None
        mock_doc.get.side_effect = lambda k, d=None: "digitax-sale-001" if k == "custom_sale_id" else d
        mock_doc.custom_error_message = None
        mock_doc.posting_date = "2026-05-27"
        mock_get_doc.return_value = mock_doc

        mock_post.return_value = ({"id": "abc", "status": "Applied"}, 201)

        # frappe.db.get_value is not module-scoped, so this patch also intercepts the
        # framework's own internal lookups (Meta loading, etc.), which call it with a
        # different signature (all-keyword: doctype=, filters=, fieldname=...). Only
        # override the specific (doctype, name, field) calls this test cares about and
        # delegate everything else to the real implementation.
        real_get_value = frappe.db.get_value

        def selective_get_value(*args, **kwargs):
            if len(args) >= 3 and args[0] == "Company" and args[2] == "country":
                return "Kenya"
            if len(args) >= 3 and args[0] == "Sales Invoice" and args[2] == "custom_sale_id":
                return "digitax-sale-001"
            return real_get_value(*args, **kwargs)

        with patch(
            "titan_digitax.titan_digitax.utils.sales.frappe.db.get_value",
            side_effect=selective_get_value,
        ):
            from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax
            send_sales_invoice_to_digitax("SI-RETURN-001")

        mock_post.assert_called_once()
