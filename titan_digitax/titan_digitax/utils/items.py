# -*- coding: utf-8 -*-
# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe


def create_and_sync_item_from_mis(item_name, price, company):
	"""
	Create Item in ERPNext. Digitax Items are never auto-created — this only
	auto-links the new Item to an existing Digitax Item when this company's
	Digitax Company Settings has auto_match_items_by_name enabled; otherwise
	the Item is left unlinked for a person to link and sync manually.

	Engage/school sync should not call this by default (D10).
	"""
	from titan_digitax.titan_digitax.utils.company_config import (
		get_digitax_settings,
		is_digitax_enabled_for_company,
	)

	try:
		if not is_digitax_enabled_for_company(company):
			return {
				"status": "failed",
				"reason": "Digitax integration not enabled",
				"item_name": None,
			}

		if frappe.db.exists("Item", item_name):
			frappe.logger().info(f"Item {item_name} already exists (created by concurrent process)")
			return {"status": "exists", "item_name": item_name}

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

		settings = get_digitax_settings(company)
		digitax_item_name = None
		if settings.get("auto_match_items_by_name"):
			default_dx_name = settings.get("default_item_name") or "School Fees"
			abbr = frappe.get_cached_value("Company", company, "abbr")
			candidate = f"{default_dx_name}({abbr})"
			if frappe.db.exists("Digitax Item", candidate):
				digitax_item_name = candidate
				frappe.db.set_value("Item", item_name, "custom_digitax_item", digitax_item_name, update_modified=False)

		frappe.db.commit()

		return {
			"status": "success" if digitax_item_name else "partial",
			"item_name": item_name,
			"digitax_item": digitax_item_name,
		}

	except Exception as e:
		frappe.logger().error(f"Failed to create item {item_name}: {str(e)}")
		return {
			"status": "failed",
			"reason": str(e),
			"item_name": None,
		}
