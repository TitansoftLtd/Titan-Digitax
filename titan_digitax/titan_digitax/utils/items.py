# -*- coding: utf-8 -*-
# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

from titan_digitax.titan_digitax.utils.actionable import create_actionable_item
from titan_digitax.titan_digitax.utils.digitax_client import DigitaxClient
from titan_digitax.titan_digitax.utils.sales_items import get_digitax_display_name


def create_and_sync_item_from_mis(item_name, price):
	"""
	Create Item in ERPNext with defaults from Digitax Settings, then sync to Digitax.

	Used only when Digitax Settings auto_sync_items_to_digitax is enabled (D14).
	Engage/school sync should not call this by default (D10).
	"""
	try:
		settings = frappe.get_single("Digitax Settings")

		if not settings.enable:
			return {
				"status": "failed",
				"reason": "Digitax integration not enabled",
				"item_name": None,
			}

		if frappe.db.exists("Item", item_name):
			frappe.logger().info(f"Item {item_name} already exists (created by concurrent process)")
			return {"status": "exists", "item_name": item_name}

		default_digitax_name = settings.get("default_item_name") or "School Fees"

		try:
			new_item = frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": item_name,
					"item_name": item_name,
					"item_group": "Services",
					"stock_uom": "Nos",
					"is_stock_item": 0,
					"is_sales_item": 1,
					"disabled": 0,
					"custom_item_class_code": settings.default_item_class_code or "99020000",
					"custom_tax_type_code": settings.default_item_tax_type_code or "D",
					"custom_digitax_item_type_code": settings.get("default_item_type_code") or "3",
					"custom_digitax_origin_nation_code": settings.get("default_origin_nation_code") or "KE",
					"custom_digitax_package_unit_code": settings.get("default_package_unit_code") or "NT",
					"custom_digitax_quantity_unit_code": settings.get("default_quantity_unit_code") or "U",
					"custom_digitax_item_name": default_digitax_name,
				}
			)
			new_item.insert(ignore_permissions=True)
			frappe.logger().info(f"Created Item in ERPNext: {new_item.name}")

		except frappe.DuplicateEntryError:
			frappe.logger().info(f"Item {item_name} already exists (created concurrently)")
			return {"status": "exists", "item_name": item_name}

		if price and price > 0:
			try:
				item_price = frappe.get_doc(
					{
						"doctype": "Item Price",
						"item_code": item_name,
						"price_list": "Standard Selling",
						"price_list_rate": price,
						"currency": "KES",
					}
				)
				item_price.insert(ignore_permissions=True)
			except Exception as e:
				frappe.logger().warning(f"Failed to create Item Price for {item_name}: {str(e)}")

		frappe.db.commit()

		try:
			result = sync_item_to_digitax(item_name)
			if result.get("status") == "success":
				return {
					"status": "success",
					"item_name": item_name,
					"digitax_id": result.get("digitax_id"),
				}
			return {
				"status": "partial",
				"reason": result.get("message") or "Digitax sync failed",
				"item_name": item_name,
			}
		except Exception as e:
			frappe.logger().error(f"Failed to sync item {item_name} to Digitax: {str(e)}")
			return {
				"status": "partial",
				"reason": f"Item created in ERPNext but Digitax sync failed: {str(e)}",
				"item_name": item_name,
			}

	except Exception as e:
		frappe.logger().error(f"Failed to create item {item_name}: {str(e)}")
		return {
			"status": "failed",
			"reason": str(e),
			"item_name": None,
		}


def _reuse_digitax_id_by_display_name(item, display_name):
	"""If another Item already has this DigiTax display name synced, copy its ID."""
	if not display_name:
		return None

	rows = frappe.db.sql(
		"""
		select name, custom_digitax_id, custom_digitax_etims_item_code
		from `tabItem`
		where name != %s
		  and ifnull(custom_digitax_id, '') != ''
		  and (
			nullif(trim(custom_digitax_item_name), '') = %s
			or (ifnull(trim(custom_digitax_item_name), '') = '' and item_name = %s)
		  )
		limit 1
		""",
		(item.name, display_name, display_name),
		as_dict=True,
	)
	if not rows:
		return None

	src = rows[0]
	item.custom_digitax_id = src.custom_digitax_id
	if src.custom_digitax_etims_item_code:
		item.custom_digitax_etims_item_code = src.custom_digitax_etims_item_code
	item.custom_digitax_synced = 1
	item.custom_digitax_last_sync = now_datetime()
	item.save(ignore_permissions=True)
	frappe.db.commit()
	return src.custom_digitax_id


@frappe.whitelist()
def sync_item_to_digitax(item_name):
	"""
	Sync a single Item to Digitax.
	Called from "Sync to Digitax" button on Item form.
	"""
	try:
		item = frappe.get_doc("Item", item_name)

		if item.custom_digitax_id:
			return {
				"status": "error",
				"message": "This item is already synced to Digitax",
			}

		settings = frappe.get_single("Digitax Settings")
		if not settings.enable:
			frappe.throw("Digitax integration is not enabled. Please enable it in Digitax Settings.")

		display_name = get_digitax_display_name(item.name, settings)
		require_name = bool(settings.get("require_digitax_item_name", 1))

		missing_fields = []
		if require_name and not (item.get("custom_digitax_item_name") or "").strip():
			missing_fields.append("Digitax Item Name")
		if not display_name:
			missing_fields.append("Item Name / Digitax Item Name")
		if not item.get("custom_item_class_code"):
			missing_fields.append("Item Class Code")
		if not item.get("custom_digitax_item_type_code"):
			missing_fields.append("Item Type Code")
		if not item.get("custom_tax_type_code"):
			missing_fields.append("Tax Type Code")
		if not item.get("custom_digitax_origin_nation_code"):
			missing_fields.append("Origin Nation Code")
		if not item.get("custom_digitax_package_unit_code"):
			missing_fields.append("Package Unit Code")
		if not item.get("custom_digitax_quantity_unit_code"):
			missing_fields.append("Quantity Unit Code")

		price_list_rate = frappe.db.get_value(
			"Item Price",
			{"item_code": item.name, "price_list": "Standard Selling"},
			"price_list_rate",
		)
		if not price_list_rate or price_list_rate <= 0:
			missing_fields.append("Standard Selling Price")

		if missing_fields:
			create_actionable_item(
				title=f"Missing Required Fields: {item_name}",
				item_type="Validation Error",
				description=(
					f"<p>Item <strong>{item_name}</strong> cannot be synced to Digitax because required fields are missing.</p>"
					f"<p><strong>Missing Fields:</strong></p>"
					f"<ul>{''.join(f'<li>{field}</li>' for field in missing_fields)}</ul>"
				),
				action_required=f"Fill in the missing fields: {', '.join(missing_fields)}",
				reference_doctype="Item",
				reference_name=item_name,
				related_data=frappe.as_json({"missing_fields": missing_fields}),
				priority="Medium",
			)
			frappe.throw(
				f"Missing required fields for Digitax sync: {', '.join(missing_fields)}. "
				f"An actionable item has been created for follow-up."
			)

		reused = _reuse_digitax_id_by_display_name(item, display_name)
		if reused:
			return {
				"status": "success",
				"message": (
					f"Item linked to existing DigiTax catalog entry for '{display_name}'. "
					f"Digitax ID: {reused}"
				),
				"digitax_id": reused,
				"etims_item_code": item.get("custom_digitax_etims_item_code"),
			}

		payload = {
			"item_class_code": item.custom_item_class_code,
			"item_type_code": item.custom_digitax_item_type_code,
			"item_name": display_name,
			"origin_nation_code": item.custom_digitax_origin_nation_code or "KE",
			"package_unit_code": item.custom_digitax_package_unit_code,
			"quantity_unit_code": item.custom_digitax_quantity_unit_code,
			"tax_type_code": item.custom_tax_type_code,
			"default_unit_price": float(price_list_rate),
		}

		client = DigitaxClient()
		response = client.create_item(payload)

		http_status = response.get("http_status_code")
		if http_status != 201:
			frappe.throw(
				f"Failed to create item in Digitax. HTTP {http_status} returned. "
				f"Expected 201 (Created)."
			)

		item.custom_digitax_id = response.get("id")
		item.custom_digitax_etims_item_code = response.get("etims_item_code")
		item.custom_digitax_synced = 1
		item.custom_digitax_last_sync = now_datetime()
		if require_name and not (item.get("custom_digitax_item_name") or "").strip():
			item.custom_digitax_item_name = display_name
		item.save(ignore_permissions=True)
		frappe.db.commit()

		return {
			"status": "success",
			"message": (
				f"Item synced to Digitax successfully (HTTP {http_status}). "
				f"Digitax ID: {response.get('id')}, ETIMS Code: {response.get('etims_item_code')}"
			),
			"digitax_id": response.get("id"),
			"etims_item_code": response.get("etims_item_code"),
		}

	except frappe.ValidationError:
		raise

	except Exception as e:
		create_actionable_item(
			title=f"Digitax Sync Failed: {item_name}",
			item_type="Data Sync Issue",
			description=(
				f"<p>Failed to sync item <strong>{item_name}</strong> to Digitax.</p>"
				f"<p><strong>Error:</strong> {str(e)}</p>"
			),
			action_required=f"Review error and retry sync: {str(e)}",
			reference_doctype="Item",
			reference_name=item_name,
			related_data=frappe.as_json({"error": str(e)}),
			priority="High",
		)
		frappe.log_error(
			message=f"Error syncing item {item_name} to Digitax: {str(e)}\n{frappe.get_traceback()}",
			title=f"Digitax Item Sync Failed - {item_name}",
		)
		frappe.throw(f"Failed to sync item to Digitax: {str(e)}")
