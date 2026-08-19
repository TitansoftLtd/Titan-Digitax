# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Build DigiTax sale/credit-note line items from a Sales Invoice (D9 / D14)."""

from __future__ import annotations

import frappe

from titan_digitax.titan_digitax.utils import digitax_item_sync
from titan_digitax.titan_digitax.utils.actionable import create_actionable_item
from titan_digitax.titan_digitax.utils.discount_handler import resolve_discount_handler


def _round_qty1_amount(amount):
	"""v1 aggregation: qty=1, unit_price=total=rounded amount."""
	total = round(abs(float(amount or 0)), 2)
	return 1, total, total


def build_digitax_items_payload(doc, digitax_settings, logger=None, dry_run=False, include_sale_fields=None):
	"""
	Collect SI lines, apply D14 gates, aggregate by DigiTax display name,
	redistribute discounts, return DigiTax items[] or a skip/error dict.

	dry_run=True skips every write side effect a gate failure would otherwise
	cause (writing custom_error_message, committing, raising an Actionable Item)
	while still returning the exact same {"ok": False, ...} shape - for callers
	that only need to know WHAT would be sent (e.g. building a print preview),
	not actually record a failed send. The real send path never sets this.

	include_sale_fields controls whether each item gets the sale-only fields
	(item_name, item_class_code, item_tax_type_code, is_stockable) needed for
	sales-with-items, or omits them for credit-notes-with-barcode. Defaults to
	None, which derives it from doc.is_return - correct for a normal send,
	where the endpoint is determined by the invoice's own nature. A virtual
	amendment must pass this explicitly instead: which endpoint it's building
	for depends on the amendment action (reversal vs. corrected sale), not on
	whether the *original* invoice happened to be a return.

	Returns:
		dict with either:
		  {"ok": True, "items": [...]}
		  {"ok": False, "skipped": True, "reason": ..., "message": ...}
	"""
	log = logger or frappe.logger("digitax_integration", allow_site=True, file_count=10)

	require_name = bool(digitax_settings.get("require_digitax_item_name", 1))
	require_sync = bool(digitax_settings.get("require_manual_item_sync", 1))
	use_sale_fields = (not doc.is_return) if include_sale_fields is None else include_sale_fields

	gate_failures = []
	items_dict = {}  # display_name -> aggregated line
	discount_items = []
	total_discounts = 0

	log.info(f"Processing {len(doc.items)} line items from Sales Invoice (D9 aggregation)")

	for item_row in doc.items:
		item_code = item_row.item_code
		item_name = item_row.item_name
		quantity = item_row.qty or 1
		rate = item_row.rate
		amount = item_row.amount

		is_discount = False
		discount_value = 0

		if doc.is_return:
			calculated_amount = quantity * rate
			if calculated_amount > 0:
				is_discount = True
				discount_value = abs(calculated_amount)
				log.info(f"Discount detected in credit note: {item_name} = {discount_value}")
			else:
				quantity = abs(quantity)
				rate = abs(rate)
				amount = abs(amount)
		else:
			if amount is not None and amount < 0:
				is_discount = True
				discount_value = abs(amount)
				log.info(f"Discount detected in invoice: {item_name} = {discount_value}")

		if is_discount:
			discount_items.append(
				{
					"item_code": item_code,
					"item_name": item_name,
					"discount_amount": discount_value,
				}
			)
			total_discounts += discount_value
			continue

		if quantity < 0 or (rate is not None and rate < 0) or (amount is not None and amount < 0):
			quantity = abs(quantity)
			rate = abs(rate or 0)
			amount = abs(amount or 0)

		digitax_item_name = digitax_item_sync.get_digitax_item_for_invoice_item(item_code, doc.company)
		digitax_item = frappe.get_cached_doc("Digitax Item", digitax_item_name) if digitax_item_name else None

		if require_name and not digitax_item:
			gate_failures.append(
				{
					"item_code": item_code,
					"item_name": item_name,
					"amount": amount,
					"reason": "missing_digitax_item_link",
				}
			)
			continue

		display_name = digitax_item.item_name if digitax_item else (item_name or item_code or "").strip()
		digitax_id = digitax_item.digitax_id if digitax_item else None

		if require_sync and not digitax_id:
			gate_failures.append(
				{
					"item_code": item_code,
					"item_name": item_name,
					"amount": amount,
					"reason": "missing_digitax_id",
				}
			)
			continue

		# Aggregate by display name (plan §2.1 v1: qty=1, sum amounts)
		line_amount = abs(float(amount or 0))
		if display_name in items_dict:
			existing = items_dict[display_name]
			if digitax_id and existing.get("id") and existing["id"] != digitax_id:
				msg = (
					f"Conflicting DigiTax IDs for display name '{display_name}': "
					f"{existing['id']} vs {digitax_id}"
				)
				return _block_send(doc, msg, "conflicting_digitax_ids", log, dry_run)
			if digitax_id and not existing.get("id"):
				existing["id"] = digitax_id
			existing["total_amount"] = round(existing["total_amount"] + line_amount, 2)
			existing["unit_price"] = existing["total_amount"]
			existing["item_codes"].add(item_code)
		else:
			qty, unit_pr, total_amt = _round_qty1_amount(line_amount)
			entry = {
				"quantity": qty,
				"unit_price": unit_pr,
				"total_amount": total_amt,
				"package_unit_quantity": 1,
				"discount_rate": 0,
				"discount_amount": 0,
				"item_description": display_name,
				"item_codes": {item_code},
			}
			if digitax_id:
				entry["id"] = digitax_id
			# item_bar_code is required by both DigiTax endpoints (sales-with-items and
			# credit-notes-with-barcode — the latter's name says as much), so it's set
			# unconditionally. The other fields below identify/register a brand new item
			# and are only meaningful on the sales side; credit notes reference an
			# already-registered item by id/barcode instead.
			entry["item_bar_code"] = digitax_settings.get("default_item_bar_code") or "SCHOOL_FEES"
			if use_sale_fields:
				entry["item_name"] = display_name
				entry["item_class_code"] = (
					(digitax_item.item_class_code if digitax_item else None)
					or digitax_settings.get("default_item_class_code")
					or "99020000"
				)
				entry["item_tax_type_code"] = (
					(digitax_item.tax_type_code if digitax_item else None)
					or digitax_settings.get("default_item_tax_type_code")
					or "D"
				)
				entry["is_stockable"] = bool(digitax_settings.get("default_is_stockable"))
			items_dict[display_name] = entry

	if gate_failures:
		return _block_gate_failures(doc, gate_failures, require_name, require_sync, log, dry_run)

	if discount_items:
		result = _apply_discounts(doc, items_dict, discount_items, total_discounts, log, dry_run)
		if result is not None:
			return result

	payload_items = []
	for display_name, item_data in items_dict.items():
		if item_data["total_amount"] <= 0:
			log.info(f"Skipping '{display_name}' - fully discounted")
			continue
		if item_data["unit_price"] < 0.01:
			log.warning(f"Skipping '{display_name}' - unit_price < 0.01")
			continue
		clean = {k: v for k, v in item_data.items() if k != "item_codes"}
		# Ensure qty * unit_price == total_amount after discount edits
		qty, unit_pr, total_amt = _round_qty1_amount(clean["total_amount"])
		clean["quantity"] = qty
		clean["unit_price"] = unit_pr
		clean["total_amount"] = total_amt
		payload_items.append(clean)

	if not payload_items:
		msg = "No items to send to Digitax. All items were filtered out (zero amount or unit_price < 0.01)."
		return _block_send(doc, msg, "no_items_after_filtering", log, dry_run)

	log.info(f"Built {len(payload_items)} DigiTax line(s) after aggregation by display name")
	return {"ok": True, "items": payload_items}


def _apply_discounts(doc, items_dict, discount_items, total_discounts, log, dry_run=False):
	"""Redistribute the total discount value across items_dict's real item lines.

	A negative-amount line ("this is actually a discount") isn't something
	standard ERPNext discount entry produces on its own - it's specific to
	whatever MIS integration maps a discount that way (e.g. Engage, via
	st_austins). Redistributing it correctly needs that integration's own
	knowledge (e.g. which items the discount is even meant to apply against,
	how to split it across different tax types), so the actual algorithm is
	delegated to whatever app declares digitax_discount_redistribution_handler
	(see discount_handler.py) - titan_digitax itself only does the basic sanity
	check (discount not bigger than the invoice) and, if no handler is
	installed, refuses to guess and blocks instead of applying its own
	naive largest-first split.
	"""
	log.info(f"Processing {len(discount_items)} discount items. Total discounts: {total_discounts}")

	total_items_amount = sum(item_data["total_amount"] for item_data in items_dict.values())

	if total_discounts > total_items_amount:
		discount_names = [f"{d['item_name']} ({d['discount_amount']})" for d in discount_items]
		error_msg = (
			f"Total discounts ({total_discounts}) exceed total invoice items amount ({total_items_amount}). "
			f"Discounts: {', '.join(discount_names)}"
		)
		if not dry_run:
			create_actionable_item(
				title=f"Discounts Exceed Invoice Total: {doc.name}",
				item_type="Discount Validation Error",
				description=(
					f"<p><strong>Sales Invoice:</strong> {doc.name} cannot be sent to Digitax.</p>"
					f"<p><strong>Issue:</strong> Total discounts <strong>({total_discounts})</strong> "
					f"exceed total invoice amount <strong>({total_items_amount})</strong>.</p>"
				),
				action_required=f"Reduce total discounts to max {total_items_amount} or adjust invoice amounts",
				reference_doctype="Sales Invoice",
				reference_name=doc.name,
				related_data=frappe.as_json(
					{
						"total_discounts": total_discounts,
						"total_items_amount": total_items_amount,
						"discount_items": discount_items,
					}
				),
				priority="High",
			)
		return _block_send(doc, error_msg, "discounts_exceed_total", log, dry_run)

	handler = resolve_discount_handler()
	if handler:
		try:
			handler(items_dict, discount_items, total_discounts, doc, log)
			return None
		except Exception as e:
			error_msg = str(e)
			log.error(f"Discount redistribution handler failed: {error_msg}")
			if not dry_run:
				create_actionable_item(
					title=f"Digitax Discount Redistribution Failed: {doc.name}",
					item_type="Discount Validation Error",
					description=(
						f"<p><strong>Sales Invoice:</strong> {doc.name} cannot be sent to Digitax.</p>"
						f"<p><strong>Issue:</strong> {error_msg}</p>"
					),
					action_required="Resolve the discount allocation on this invoice, or adjust amounts",
					reference_doctype="Sales Invoice",
					reference_name=doc.name,
					related_data=frappe.as_json({"discount_items": discount_items}),
					priority="High",
				)
			return _block_send(doc, error_msg, "discount_redistribution_failed", log, dry_run)

	# No handler resolved - refuse to guess how to redistribute an unexpected
	# negative-amount line rather than silently applying a naive split.
	discount_names = [f"{d['item_name']} ({d['discount_amount']})" for d in discount_items]
	error_msg = (
		f"This invoice has {len(discount_items)} negative-amount line(s) that look like "
		f"discounts ({', '.join(discount_names)}), but no installed app declares "
		f"digitax_discount_redistribution_handler to redistribute them. This isn't "
		f"standard ERPNext discount entry - refusing to guess how to apply it."
	)
	if not dry_run:
		create_actionable_item(
			title=f"Digitax Discount Handling Not Configured: {doc.name}",
			item_type="Discount Validation Error",
			description=(
				f"<p><strong>Sales Invoice:</strong> {doc.name} cannot be sent to Digitax.</p>"
				f"<p>{error_msg}</p>"
			),
			action_required=(
				"Install/configure an app that declares digitax_discount_redistribution_handler, "
				"or resolve this invoice's discount line(s) manually"
			),
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
			related_data=frappe.as_json({"discount_items": discount_items}),
			priority="High",
		)
	return _block_send(doc, error_msg, "discount_handler_not_configured", log, dry_run)

	return None


def _block_gate_failures(doc, gate_failures, require_name, require_sync, log, dry_run=False):
	missing_links = [g for g in gate_failures if g["reason"] == "missing_digitax_item_link"]
	missing_ids = [g for g in gate_failures if g["reason"] == "missing_digitax_id"]

	parts = []
	if missing_links:
		parts.append(
			f"{len(missing_links)} item(s) with no linked Digitax Item: "
			+ ", ".join(f"{g['item_code']}" for g in missing_links[:5])
		)
	if missing_ids:
		parts.append(
			f"{len(missing_ids)} item(s) linked to a Digitax Item that isn't synced yet: "
			+ ", ".join(f"{g['item_code']}" for g in missing_ids[:5])
		)
	error_msg = "; ".join(parts)
	if require_name or require_sync:
		error_msg += ". DigiTax send blocked by Digitax Company Settings gates."

	if not dry_run:
		create_actionable_item(
			title=f"Digitax Item Gates Failed: {doc.name}",
			item_type="Missing Digitax IDs",
			description=(
				f"<p><strong>Sales Invoice:</strong> {doc.name} cannot be sent to Digitax.</p>"
				f"<p>{error_msg}</p>"
				f"<ul>{''.join(f'<li>{g['item_code']} - {g['item_name']} ({g['reason']})</li>' for g in gate_failures)}</ul>"
			),
			action_required="Link a Digitax Item and/or Sync it to Digitax for the listed Items",
			reference_doctype="Sales Invoice",
			reference_name=doc.name,
			related_data=frappe.as_json({"gate_failures": gate_failures}),
			priority="High",
		)
	return _block_send(doc, error_msg, "digitax_item_gates_failed", log, dry_run)


def _block_send(doc, error_msg, reason, log, dry_run=False):
	log.error(f"SKIP INVOICE: {error_msg}")
	if dry_run:
		return {
			"ok": False,
			"skipped": True,
			"reason": reason,
			"message": error_msg,
		}
	frappe.db.set_value(
		"Sales Invoice",
		doc.name,
		"custom_error_message",
		error_msg,
		update_modified=False,
	)
	frappe.db.commit()
	return {
		"ok": False,
		"skipped": True,
		"reason": reason,
		"message": error_msg,
	}
