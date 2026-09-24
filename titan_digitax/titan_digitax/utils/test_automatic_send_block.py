# Copyright (c) 2026, Titansoft Limited and Contributors
# See license.txt

"""D21: a company can block every automatic Digitax invoice send, leaving only the
two user-initiated, confirmed entrypoints (the Sales Invoice button and a Digitax
Sync invoice run) able to send.

Plain TestCase, not FrappeTestCase: everything that would touch the database is
mocked, and FrappeTestCase's ERPNext test-record bootstrap fails on this bench
(overlapping Fiscal Year) before any test runs.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

SALES = "titan_digitax.titan_digitax.utils.sales"
SALES_INVOICE = "titan_digitax.titan_digitax.utils.sales_invoice"
COMPANY_CONFIG = "titan_digitax.titan_digitax.utils.company_config"
SYNC_JOB = "titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job"


def _invoice_company(company="Blocked Co"):
    """Patch frappe.db.get_value so ("Sales Invoice", <name>, "company") answers
    `company`, delegating every other lookup to the real implementation."""
    real_get_value = frappe.db.get_value

    def selective(*args, **kwargs):
        if len(args) >= 3 and args[0] == "Sales Invoice" and args[2] == "company":
            return company
        return real_get_value(*args, **kwargs)

    return patch(f"{SALES}.frappe.db.get_value", side_effect=selective)


def _run_directly(company, fn):
    return fn()


@patch(f"{SALES}.run_as_digitax_sync_user", side_effect=_run_directly)
@patch(f"{SALES}._send_sales_invoice_to_digitax_impl", return_value={"id": "sale-1", "status": "ok"})
class TestAutomaticEntrypoint(TestCase):
    def test_blocked_company_is_skipped(self, mock_impl, _run_as):
        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax_automatic

        with _invoice_company(), patch(f"{SALES}.is_automatic_invoice_sending_blocked", return_value=True):
            result = send_sales_invoice_to_digitax_automatic("SI-1")

        self.assertTrue(result["skipped"])
        mock_impl.assert_not_called()

    def test_unblocked_company_sends(self, mock_impl, _run_as):
        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax_automatic

        with _invoice_company(), patch(f"{SALES}.is_automatic_invoice_sending_blocked", return_value=False):
            send_sales_invoice_to_digitax_automatic("SI-1")

        mock_impl.assert_called_once_with("SI-1")

    def test_confirmed_entrypoint_ignores_block(self, mock_impl, _run_as):
        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax_confirmed

        with _invoice_company(), patch(f"{SALES}.is_automatic_invoice_sending_blocked", return_value=True):
            send_sales_invoice_to_digitax_confirmed("SI-1")

        mock_impl.assert_called_once_with("SI-1")


@patch(f"{SALES}.require_digitax_role")
@patch(f"{SALES}.run_as_digitax_sync_user", side_effect=_run_directly)
@patch(f"{SALES}._send_sales_invoice_to_digitax_impl", return_value={"id": "sale-1", "status": "ok"})
class TestManualSingleSend(TestCase):
    def test_blocked_without_confirmation_asks_first(self, mock_impl, _run_as, _role):
        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax

        with _invoice_company(), patch(f"{SALES}.is_automatic_invoice_sending_blocked", return_value=True):
            result = send_sales_invoice_to_digitax("SI-1")

        self.assertTrue(result["requires_confirmation"])
        mock_impl.assert_not_called()

    def test_blocked_with_confirmation_sends_and_leaves_a_comment(self, mock_impl, _run_as, _role):
        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax

        invoice = MagicMock()
        with _invoice_company(), patch(
            f"{SALES}.is_automatic_invoice_sending_blocked", return_value=True
        ), patch(f"{SALES}.frappe.get_doc", return_value=invoice):
            send_sales_invoice_to_digitax("SI-1", send_anyway="1")

        mock_impl.assert_called_once_with("SI-1")
        invoice.add_comment.assert_called_once()

    def test_unblocked_sends_without_asking(self, mock_impl, _run_as, _role):
        from titan_digitax.titan_digitax.utils.sales import send_sales_invoice_to_digitax

        with _invoice_company(), patch(f"{SALES}.is_automatic_invoice_sending_blocked", return_value=False):
            send_sales_invoice_to_digitax("SI-1")

        mock_impl.assert_called_once_with("SI-1")


@patch(f"{SALES_INVOICE}.frappe.enqueue")
@patch(f"{SALES_INVOICE}.frappe.conf")
class TestOnSubmit(TestCase):
    def test_blocked_company_is_not_enqueued(self, conf, enqueue):
        from titan_digitax.titan_digitax.utils.sales_invoice import on_submit

        conf.get.side_effect = lambda k, d=None: True if k == "sync_with_digitax" else d
        with patch(f"{SALES_INVOICE}.is_automatic_invoice_sending_blocked", return_value=True):
            on_submit(frappe._dict(name="SI-1", company="Blocked Co"), "on_submit")

        enqueue.assert_not_called()

    def test_unblocked_company_is_enqueued(self, conf, enqueue):
        from titan_digitax.titan_digitax.utils.sales_invoice import on_submit

        conf.get.side_effect = lambda k, d=None: True if k == "sync_with_digitax" else d
        with patch(f"{SALES_INVOICE}.is_automatic_invoice_sending_blocked", return_value=False):
            on_submit(frappe._dict(name="SI-1", company="Open Co"), "on_submit")

        enqueue.assert_called_once()


class TestRetrySweep(TestCase):
    def test_blocked_company_is_skipped_entirely(self):
        from titan_digitax.titan_digitax.utils.sales import retry_sending_sales_invoice_to_digitax

        with patch(f"{SALES}.is_digitax_enabled_for_company", return_value=True), patch(
            f"{SALES}.is_automatic_invoice_sending_blocked", return_value=True
        ), patch(f"{SALES}.get_digitax_settings") as settings, patch(
            f"{SALES}.send_sales_invoice_to_digitax_automatic"
        ) as send:
            result = retry_sending_sales_invoice_to_digitax(company="Blocked Co")

        self.assertEqual(result["attempted"], 0)
        settings.assert_not_called()
        send.assert_not_called()


@patch(f"{SYNC_JOB}._update_job_record")
@patch(f"{SYNC_JOB}.frappe.enqueue")
class TestExecuteDigitaxSync(TestCase):
    def _execute(self, blocked, **kwargs):
        from titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job import execute_digitax_sync

        with patch(f"{COMPANY_CONFIG}.is_automatic_invoice_sending_blocked", return_value=blocked):
            return execute_digitax_sync(direction="To DigiTax", sync_type="Invoices", company="Blocked Co", **kwargs)

    def test_blocked_without_confirmation_queues_nothing(self, enqueue, _job):
        result = self._execute(blocked=True, from_date="2026-09-01", to_date="2026-09-10")

        self.assertEqual(result["status"], "requires_confirmation")
        enqueue.assert_not_called()

    def test_blocked_with_confirmation_queues_an_override_run(self, enqueue, _job):
        result = self._execute(blocked=True, from_date="2026-09-01", to_date="2026-09-10", send_anyway="1")

        self.assertEqual(result["status"], "queued")
        kwargs = enqueue.call_args.kwargs
        self.assertTrue(kwargs["send_anyway"])
        self.assertEqual((kwargs["from_date"], kwargs["to_date"]), ("2026-09-01", "2026-09-10"))

    def test_unblocked_never_overrides_even_if_asked(self, enqueue, _job):
        self._execute(blocked=False, send_anyway="1")

        self.assertFalse(enqueue.call_args.kwargs["send_anyway"])

    def test_reversed_date_range_is_rejected(self, enqueue, _job):
        with self.assertRaises(frappe.ValidationError):
            self._execute(blocked=False, from_date="2026-09-10", to_date="2026-09-01")
        enqueue.assert_not_called()
