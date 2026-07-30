# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Build DigiTax sale/credit-note line items from a Sales Invoice (D9 / D14)."""

from __future__ import annotations

import frappe

from titan_digitax.titan_digitax.utils.actionable import create_actionable_item


def get_digitax_display_name(item_code, digitax_settings=None):
	"""
	Resolve DigiTax display name for an ERP Item (plan §2.1).

	When require_digitax_item_name is ON: custom_digitax_item_name only.
	When OFF: custom_digitax_item_name or item_name or item_code.
	"""
	if not item_code:
		return None

	settings = digitax_settings or frappe.get_single("Digitax Settings")
	require_name = bool(settings.get("require_digitax_item_name", 1))

	row = frappe.db.get_value(
		"Item",
		item_code,
		["custom_digitax_item_name", "item_name", "item_code"],
		as_dict=True,
	)
	if not row:
		return None

	digitax_name = (row.get("custom_digitax_item_name") or "").strip()
	if require_name:
		return digitax_name or None

	return digitax_name or (row.get("item_name") or "").strip() or row.get("item_code")


def _round_qty1_amount(amount):
	"""v1 aggregation: qty=1, unit_price=total=rounded amount."""
	total = round(abs(float(amount or 0)), 2)
	return 1, total, total


def build_digitax_items_payload(doc, digitax_settings, logger=None):
	"""
	Collect SI lines, apply D14 gates, aggregate by DigiTax display name,
	redistribute discounts, return DigiTax items[] or a skip/error dict.

	Returns:
		dict with either:
		  {"ok": True, "items": [...]}
		  {"ok": False, "skipped": True, "reason": ..., "message": ...}
	"""
	log = logger or frappe.logger("digitax_integration", allow_site=True, file_count=10)

	require_name = bool(digitax_settings.get("require_digitax_item_name", 1))
	require_sync = bool(digitax_settings.get("require_manual_item_sync", 1))
	auto_sync = bool(digitax_settings.get("auto_sync_items_to_digitax", 0))

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

		display_name = get_digitax_display_name(item_code, digitax_settings)
		digitax_id = frappe.db.get_value("Item", item_code, "custom_digitax_id")

		if require_name and not display_name:
			gate_failures.append(
				{
					"item_code": item_code,
					"item_name": item_name,
					"amount": amount,
					"reason": "missing_digitax_item_name",
				}
			)
			continue

		if not display_name:
			display_name = (item_name or item_code or "").strip()

		if require_sync and not digitax_id:
			if auto_sync:
				digitax_id = _try_auto_sync_item(item_code, display_name, log)
			if not digitax_id:
				gate_failures.append(
					{
						"item_code": item_code,
						"item_name": item_name,
						"amount": amount,
						"reason": "missing_digitax_id",
					}
				)
				continue
		elif not digitax_id and auto_sync:
			digitax_id = _try_auto_sync_item(item_code, display_name, log)

		# Aggregate by display name (plan §2.1 v1: qty=1, sum amounts)
		line_amount = abs(float(amount or 0))
		if display_name in items_dict:
			existing = items_dict[display_name]
			if digitax_id and existing.get("id") and existing["id"] != digitax_id:
				msg = (
					f"Conflicting DigiTax IDs for display name '{display_name}': "
					f"{existing['id']} vs {digitax_id}"
				)
				return _block_send(doc, msg, "conflicting_digitax_ids", log)
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
			if not doc.is_return:
				entry["item_name"] = display_name
				entry["item_class_code"] = (
					frappe.db.get_value("Item", item_code, "custom_item_class_code")
					or digitax_settings.get("default_item_class_code")
					or "99020000"
				)
				entry["item_tax_type_code"] = (
					frappe.db.get_value("Item", item_code, "custom_tax_type_code")
					or digitax_settings.get("default_item_tax_type_code")
					or "D"
				)
				entry["is_stockable"] = bool(digitax_settings.get("default_is_stockable"))
				entry["item_bar_code"] = digitax_settings.get("default_item_bar_code") or "SCHOOL_FEES"
			items_dict[display_name] = entry

	if gate_failures:
		return _block_gate_failures(doc, gate_failures, require_name, require_sync, log)

	if discount_items:
		result = _apply_discounts(doc, items_dict, discount_items, total_discounts, log)
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
		return _block_send(doc, msg, "no_items_after_filtering", log)

	log.info(f"Built {len(payload_items)} DigiTax line(s) after aggregation by display name")
	return {"ok": True, "items": payload_items}


def _try_auto_sync_item(item_code, display_name, log):
	"""Reuse DigiTax ID by display name, or create via DigitaxClient when auto_sync is on."""
	try:
		existing_id = frappe.db.get_value(
			"Item",
			{"custom_digitax_item_name": display_name, "custom_digitax_id": ["!=", ""]},
			"custom_digitax_id",
		)
		if not existing_id:
			# Also match items that used ERP name as DigiTax name
			existing_id = frappe.db.sql(
				"""
				select custom_digitax_id from `tabItem`
				where ifnull(custom_digitax_id, '') != ''
				  and (
					nullif(trim(custom_digitax_item_name), '') = %s
					or (ifnull(trim(custom_digitax_item_name), '') = '' and item_name = %s)
				  )
				limit 1
				""",
				(display_name, display_name),
			)
			existing_id = existing_id[0][0] if existing_id else None

		if existing_id:
			frappe.db.set_value(
				"Item",
				item_code,
				{
					"custom_digitax_id": existing_id,
					"custom_digitax_synced": 1,
				},
				update_modified=False,
			)
			log.info(f"Reused DigiTax ID {existing_id} for {item_code} via display name '{display_name}'")
			return existing_id

		from titan_digitax.titan_digitax.utils.items import sync_item_to_digitax

		result = sync_item_to_digitax(item_code)
		if result and result.get("status") == "success":
			return result.get("digitax_id") or frappe.db.get_value("Item", item_code, "custom_digitax_id")
	except Exception as e:
		log.warning(f"Auto-sync failed for {item_code}: {e}")
	return None


def _apply_discounts(doc, items_dict, discount_items, total_discounts, log):
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
		return _block_send(doc, error_msg, "discounts_exceed_total", log)

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


def _block_gate_failures(doc, gate_failures, require_name, require_sync, log):
	missing_names = [g for g in gate_failures if g["reason"] == "missing_digitax_item_name"]
	missing_ids = [g for g in gate_failures if g["reason"] == "missing_digitax_id"]

	parts = []
	if missing_names:
		parts.append(
			f"{len(missing_names)} item(s) missing Digitax Item Name: "
			+ ", ".join(f"{g['item_code']}" for g in missing_names[:5])
		)
	if missing_ids:
		parts.append(
			f"{len(missing_ids)} item(s) not synced to Digitax: "
			+ ", ".join(f"{g['item_code']}" for g in missing_ids[:5])
		)
	error_msg = "; ".join(parts)
	if require_name or require_sync:
		error_msg += ". DigiTax send blocked by Digitax Settings gates."

	create_actionable_item(
		title=f"Digitax Item Gates Failed: {doc.name}",
		item_type="Missing Digitax IDs",
		description=(
			f"<p><strong>Sales Invoice:</strong> {doc.name} cannot be sent to Digitax.</p>"
			f"<p>{error_msg}</p>"
			f"<ul>{''.join(f'<li>{g['item_code']} - {g['item_name']} ({g['reason']})</li>' for g in gate_failures)}</ul>"
		),
		action_required="Set Digitax Item Name and/or Sync to Digitax on the listed Items",
		reference_doctype="Sales Invoice",
		reference_name=doc.name,
		related_data=frappe.as_json({"gate_failures": gate_failures}),
		priority="High",
	)
	return _block_send(doc, error_msg, "digitax_item_gates_failed", log)


def _block_send(doc, error_msg, reason, log):
	log.error(f"SKIP INVOICE: {error_msg}")
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
