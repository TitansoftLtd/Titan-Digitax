# Copyright (c) 2025, Titansoft Limited and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from titan_digitax.titan_digitax.utils.company_config import (
    get_enabled_digitax_companies,
    is_digitax_enabled_for_company,
)


def _make_row(company, enabled=1, description=""):
    row = MagicMock()
    row.company = company
    row.enabled = enabled
    row.description = description
    return row


def _make_settings(rows=None, enable=1, target_country="Kenya"):
    settings = MagicMock()
    settings.enable = enable
    settings.target_country = target_country
    settings.get.side_effect = lambda key, *args: rows if key == "company_configurations" else None
    settings.company_configurations = rows or []
    return settings


class TestDigitaxCompanyConfig(FrappeTestCase):
    """Tests for the per-company eligibility helpers."""

    def test_no_row_for_company_returns_false(self):
        settings = _make_settings(rows=[])
        self.assertFalse(is_digitax_enabled_for_company("Acme Kenya", settings))

    def test_row_exists_but_disabled_returns_false(self):
        settings = _make_settings(rows=[_make_row("Acme Kenya", enabled=0)])
        self.assertFalse(is_digitax_enabled_for_company("Acme Kenya", settings))

    def test_row_exists_and_enabled_returns_true(self):
        settings = _make_settings(rows=[_make_row("Acme Kenya", enabled=1)])
        self.assertTrue(is_digitax_enabled_for_company("Acme Kenya", settings))

    def test_other_company_enabled_but_not_queried(self):
        settings = _make_settings(rows=[_make_row("Acme Kenya", enabled=1)])
        self.assertFalse(is_digitax_enabled_for_company("Braeburn Nairobi", settings))

    def test_empty_company_arg_returns_false(self):
        settings = _make_settings(rows=[_make_row("Acme Kenya", enabled=1)])
        self.assertFalse(is_digitax_enabled_for_company("", settings))
        self.assertFalse(is_digitax_enabled_for_company(None, settings))

    def test_get_enabled_companies_returns_only_enabled(self):
        settings = _make_settings(rows=[
            _make_row("Enabled Co", enabled=1),
            _make_row("Disabled Co", enabled=0),
            _make_row("Also Enabled", enabled=1),
        ])
        result = set(get_enabled_digitax_companies(settings))
        self.assertIn("Enabled Co", result)
        self.assertIn("Also Enabled", result)
        self.assertNotIn("Disabled Co", result)


class TestDigitaxSettingsValidation(FrappeTestCase):
    """Tests for DigitaxSettings.validate()."""

    def _get_settings_doc(self):
        return frappe.get_single("Digitax Settings")

    def _append_row(self, doc, company, enabled=1):
        doc.append("company_configurations", {
            "company": company,
            "enabled": enabled,
        })

    @patch("frappe.db.get_value")
    def test_duplicate_company_row_raises(self, mock_get_value):
        mock_get_value.return_value = None  # not a group, country check done after
        doc = self._get_settings_doc()
        existing = list(doc.company_configurations or [])

        # Add a duplicate pair for a fake company
        doc.company_configurations = []
        self._append_row(doc, "__test_dup__", enabled=1)
        self._append_row(doc, "__test_dup__", enabled=1)

        with self.assertRaises(frappe.ValidationError):
            doc.validate()

        # Restore
        doc.company_configurations = existing

    @patch("frappe.db.get_value")
    def test_enabled_group_company_raises(self, mock_get_value):
        def side_effect(doctype, name, field):
            if field == "is_group":
                return 1
            return "Kenya"
        mock_get_value.side_effect = side_effect

        doc = self._get_settings_doc()
        existing = list(doc.company_configurations or [])
        doc.company_configurations = []
        self._append_row(doc, "__test_group__", enabled=1)

        with self.assertRaises(frappe.ValidationError):
            doc.validate()

        doc.company_configurations = existing

    @patch("frappe.db.get_value")
    def test_enabled_non_target_country_raises(self, mock_get_value):
        def side_effect(doctype, name, field):
            if field == "is_group":
                return 0
            if field == "country":
                return "Tanzania"
            return None
        mock_get_value.side_effect = side_effect

        doc = self._get_settings_doc()
        original_country = doc.target_country
        doc.target_country = "Kenya"
        existing = list(doc.company_configurations or [])
        doc.company_configurations = []
        self._append_row(doc, "__test_tz__", enabled=1)

        with self.assertRaises(frappe.ValidationError):
            doc.validate()

        doc.target_country = original_country
        doc.company_configurations = existing

    @patch("frappe.db.get_value")
    def test_disabled_non_target_country_row_is_allowed(self, mock_get_value):
        def side_effect(doctype, name, field):
            if field == "is_group":
                return 0
            if field == "country":
                return "Tanzania"
            return None
        mock_get_value.side_effect = side_effect

        doc = self._get_settings_doc()
        original_country = doc.target_country
        doc.target_country = "Kenya"
        existing = list(doc.company_configurations or [])
        doc.company_configurations = []
        self._append_row(doc, "__test_tz_disabled__", enabled=0)

        # Should not raise
        doc.validate()

        doc.target_country = original_country
        doc.company_configurations = existing


class TestSendInvoiceEligibility(FrappeTestCase):
    """Integration-style tests for send_sales_invoice_to_digitax eligibility gating."""

    @patch("frappe.conf", new_callable=lambda: type("cfg", (), {"get": staticmethod(lambda k, d=None: True if k == "sync_with_digitax" else d)}))
    @patch("titan_digitax.titan_digitax.utils.sales._post_to_digitax")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_single")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_doc")
    def test_no_row_skips_send(self, mock_get_doc, mock_get_single, mock_post, _conf):
        """Invoice for a company with no Digitax row is skipped; _post_to_digitax not called."""
        mock_settings = _make_settings(rows=[], enable=1, target_country="Kenya")
        mock_get_single.return_value = mock_settings

        mock_doc = MagicMock()
        mock_doc.company = "Acme Kenya"
        mock_doc.is_return = 0
        mock_doc.get.return_value = None  # no custom_sale_id
        mock_doc.custom_sale_id = None
        mock_doc.custom_error_message = None
        mock_get_doc.return_value = mock_doc

        # Patch company country check
        with patch("titan_digitax.titan_digitax.utils.sales.frappe.db.get_value", return_value="Kenya"):
            from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax
            result = send_sales_invoice_to_digitax("SI-TEST-001")

        self.assertTrue(result.get("skipped"))
        mock_post.assert_not_called()

    @patch("frappe.conf", new_callable=lambda: type("cfg", (), {"get": staticmethod(lambda k, d=None: True if k == "sync_with_digitax" else d)}))
    @patch("titan_digitax.titan_digitax.utils.sales._post_to_digitax")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_single")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_doc")
    def test_disabled_row_skips_send(self, mock_get_doc, mock_get_single, mock_post, _conf):
        """Invoice for a company with a disabled row is skipped."""
        mock_settings = _make_settings(rows=[_make_row("Acme Kenya", enabled=0)], enable=1, target_country="Kenya")
        mock_get_single.return_value = mock_settings

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

    @patch("frappe.conf", new_callable=lambda: type("cfg", (), {"get": staticmethod(lambda k, d=None: True if k == "sync_with_digitax" else d)}))
    @patch("titan_digitax.titan_digitax.utils.sales._post_to_digitax")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_single")
    @patch("titan_digitax.titan_digitax.utils.sales.frappe.get_doc")
    def test_credit_note_allowed_even_when_row_disabled(self, mock_get_doc, mock_get_single, mock_post, _conf):
        """Credit note against an already-sent invoice bypasses the company row check."""
        mock_settings = _make_settings(rows=[_make_row("Acme Kenya", enabled=0)], enable=1, target_country="Kenya")
        mock_get_single.return_value = mock_settings

        mock_doc = MagicMock()
        mock_doc.company = "Acme Kenya"
        mock_doc.is_return = 1
        mock_doc.return_against = "SI-ORIG-001"
        mock_doc.grand_total = -5000
        mock_doc.custom_sale_id = "digitax-sale-001"
        mock_doc.get.side_effect = lambda k, d=None: "digitax-sale-001" if k == "custom_sale_id" else d
        mock_doc.custom_error_message = None
        mock_doc.posting_date = "2026-05-27"
        mock_get_doc.return_value = mock_doc

        mock_post.return_value = ({"id": "abc", "status": "Applied"}, 201)

        with patch("titan_digitax.titan_digitax.utils.sales.frappe.db.get_value") as mock_dbval:
            mock_dbval.side_effect = lambda dt, name, field, **kw: (
                "Kenya" if field == "country" else
                "digitax-sale-001" if field == "custom_sale_id" else
                None
            )
            from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax
            send_sales_invoice_to_digitax("SI-RETURN-001")

        mock_post.assert_called_once()
