# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Build DigiTax sale/credit-note line items from a Sales Invoice (D9 / D14)."""

from __future__ import annotations

import frappe

from titan_digitax.titan_digitax.utils import digitax_item_sync
from titan_digitax.titan_digitax.utils.actionable import create_actionable_item


def _round_qty1_amount(amount):
	"""v1 aggregation: qty=1, unit_price=total=rounded amount."""
	total = round(abs(float(amount or 0)), 2)
	return 1, total, total


def build_digitax_items_payload(doc, digitax_settings, logger=None, dry_run=False):
	"""
	Collect SI lines, apply D14 gates, aggregate by DigiTax display name,
	redistribute discounts, return DigiTax items[] or a skip/error dict.

	dry_run=True skips every write side effect a gate failure would otherwise
	cause (writing custom_error_message, committing, raising an Actionable Item)
	while still returning the exact same {"ok": False, ...} shape - for callers
	that only need to know WHAT would be sent (e.g. building a print preview),
	not actually record a failed send. The real send path never sets this.

	Returns:
		dict with either:
		  {"ok": True, "items": [...]}
		  {"ok": False, "skipped": True, "reason": ..., "message": ...}
	"""
	log = logger or frappe.logger("digitax_integration", allow_site=True, file_count=10)

	require_name = bool(digitax_settings.get("require_digitax_item_name", 1))
	require_sync = bool(digitax_settings.get("require_manual_item_sync", 1))

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
			if not doc.is_return:
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
	log.info(f"Processing {len(discount_items)} discount items. Total discounts: {total_discounts}")

	all_items = []
	total_items_amount = 0
	for display_name, item_data in items_dict.items():
		all_items.append(
			{
				"display_name": display_name,
				"description": item_data["item_description"],
				"amount": item_data["total_amount"],
				"item_data": item_data,
			}
		)
		total_items_amount += item_data["total_amount"]

	all_items.sort(key=lambda x: x["amount"], reverse=True)

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

	remaining_discount = total_discounts
	for item in all_items:
		if remaining_discount <= 0:
			break
		item_data = item["item_data"]
		original_amount = item["amount"]
		if remaining_discount >= original_amount:
			new_amount = 0
			remaining_discount -= original_amount
		else:
			new_amount = original_amount - remaining_discount
			remaining_discount = 0
		qty, unit_pr, total_amt = _round_qty1_amount(new_amount)
		item_data["quantity"] = qty
		item_data["unit_price"] = unit_pr
		item_data["total_amount"] = total_amt

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
