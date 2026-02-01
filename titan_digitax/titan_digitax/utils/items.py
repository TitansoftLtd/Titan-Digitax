# -*- coding: utf-8 -*-
# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime
from titan_digitax.titan_digitax.utils.digitax_client import DigitaxClient


def create_and_sync_item_from_mis(item_name, price):
	"""
	Create Item in ERPNext with defaults from Digitax Settings, then sync to Digitax.
	Used during MIS invoice sync when items are missing.
	
	Args:
		item_name (str): Item name from MIS lineDescription
		price (float): Price from MIS grossAmount
	
	Returns:
		dict: Result with status (success/failed) and item_name if created
	"""
	try:
		# Get Digitax Settings for defaults
		settings = frappe.get_single("Digitax Settings")
		
		if not settings.enable:
			return {
				"status": "failed",
				"reason": "Digitax integration not enabled",
				"item_name": None
			}
		
		# Check if item already exists (race condition check)
		if frappe.db.exists("Item", item_name):
			frappe.logger().info(f"Item {item_name} already exists (created by concurrent process)")
			return {"status": "exists", "item_name": item_name}
		
		# Create Item in ERPNext with defaults
		try:
			new_item = frappe.get_doc({
				"doctype": "Item",
				"item_code": item_name,
				"item_name": item_name,
				"item_group": "Services",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"disabled": 0,
				# Digitax defaults from settings
				"custom_item_class_code": settings.default_item_class_code or "99020000",
				"custom_tax_type_code": settings.default_item_tax_type_code or "D",
				"custom_digitax_item_type_code": settings.default_item_type_code or "3",
				"custom_digitax_origin_nation_code": settings.default_origin_nation_code or "KE",
				"custom_digitax_package_unit_code": settings.default_package_unit_code or "NT",
				"custom_digitax_quantity_unit_code": settings.default_quantity_unit_code or "U",
			})
			new_item.insert(ignore_permissions=True)
			frappe.logger().info(f"Created Item in ERPNext: {new_item.name}")
			
		except frappe.DuplicateEntryError:
			# Race condition - item was created by another process
			frappe.logger().info(f"Item {item_name} already exists (created concurrently)")
			return {"status": "exists", "item_name": item_name}
		
		# Create Item Price for Standard Selling
		if price and price > 0:
			try:
				item_price = frappe.get_doc({
					"doctype": "Item Price",
					"item_code": item_name,
					"price_list": "Standard Selling",
					"price_list_rate": price,
					"currency": "KES"
				})
				item_price.insert(ignore_permissions=True)
				frappe.logger().info(f"Created Item Price for {item_name}: {price}")
			except Exception as e:
				frappe.logger().warning(f"Failed to create Item Price for {item_name}: {str(e)}")
				# Continue - item still created, price can be added later
		
		# Commit to ensure item is visible for retry lookup
		frappe.db.commit()
		
		# Sync to Digitax
		try:
			client = DigitaxClient()
			
			# Build payload
			payload = {
				"item_class_code": settings.default_item_class_code or "99020000",
				"item_type_code": settings.default_item_type_code or "3",
				"item_name": item_name,
				"origin_nation_code": settings.default_origin_nation_code or "KE",
				"package_unit_code": settings.default_package_unit_code or "NT",
				"quantity_unit_code": settings.default_quantity_unit_code or "U",
				"tax_type_code": settings.default_item_tax_type_code or "D",
				"default_unit_price": float(price) if price and price > 0 else 1.0
			}
			
			# Send to Digitax
			response = client.create_item(payload)
			
			# Check HTTP status code for successful creation (201)
			http_status = response.get("http_status_code")
			
			if http_status != 201:
				# Not a successful creation
				raise Exception(
					f"Digitax returned HTTP {http_status}. Expected 201 (Created)."
				)
			
			# HTTP 201 = Success - Update Item with Digitax response
			item_doc = frappe.get_doc("Item", item_name)
			item_doc.custom_digitax_id = response.get("id")
			item_doc.custom_digitax_etims_item_code = response.get("etims_item_code")
			item_doc.custom_digitax_synced = 1
			item_doc.custom_digitax_last_sync = now_datetime()
			item_doc.save(ignore_permissions=True)
			
			# Commit to ensure Digitax ID is visible immediately
			frappe.db.commit()
			
			frappe.logger().info(
				f"Successfully synced item {item_name} to Digitax (HTTP {http_status})"
			)
			
			return {
				"status": "success",
				"item_name": item_name,
				"digitax_id": response.get("id")
			}
			
		except Exception as e:
			# Digitax sync failed - item exists in ERP but not in Digitax
			frappe.logger().error(f"Failed to sync item {item_name} to Digitax: {str(e)}")
			
			# Return partial success - item exists in ERP, can be used for invoice
			return {
				"status": "partial",
				"reason": f"Item created in ERPNext but Digitax sync failed: {str(e)}",
				"item_name": item_name
			}
	
	except Exception as e:
		frappe.logger().error(f"Failed to create item {item_name}: {str(e)}")
		return {
			"status": "failed",
			"reason": str(e),
			"item_name": None
		}


@frappe.whitelist()
def sync_item_to_digitax(item_name):
	"""
	Sync a single Item to Digitax.
	Called from "Sync to Digitax" button on Item form.
	
	Args:
		item_name (str): Name of the Item to sync
	
	Returns:
		dict: Result with status and message
	"""
	try:
		# Load item
		item = frappe.get_doc("Item", item_name)
		
		# Check if already synced
		if item.custom_digitax_id:
			return {
				"status": "error",
				"message": "This item is already synced to Digitax"
			}
		
		# Validate Digitax integration is enabled
		settings = frappe.get_single("Digitax Settings")
		if not settings.enable:
			frappe.throw("Digitax integration is not enabled. Please enable it in Digitax Settings.")
		
		# Validate required fields
		missing_fields = []
		if not item.item_name:
			missing_fields.append("Item Name")
		if not item.custom_item_class_code:
			missing_fields.append("Item Class Code")
		if not item.custom_digitax_item_type_code:
			missing_fields.append("Item Type Code")
		if not item.custom_tax_type_code:
			missing_fields.append("Tax Type Code")
		if not item.custom_digitax_origin_nation_code:
			missing_fields.append("Origin Nation Code")
		if not item.custom_digitax_package_unit_code:
			missing_fields.append("Package Unit Code")
		if not item.custom_digitax_quantity_unit_code:
			missing_fields.append("Quantity Unit Code")
		
		# Get price from Item Price (Standard Selling)
		price_list_rate = frappe.db.get_value(
			"Item Price",
			{
				"item_code": item.name,
				"price_list": "Standard Selling"
			},
			"price_list_rate"
		)
		
		if not price_list_rate or price_list_rate <= 0:
			missing_fields.append("Standard Selling Price")
		
		if missing_fields:
			# Create actionable item for missing fields
			from rusinga_school.rusinga_school.doctype.actionable_items.actionable_items import create_actionable_item
			
			create_actionable_item(
				title=f"Missing Required Fields: {item_name}",
				item_type="Validation Error",
				description=f"<p>Item <strong>{item_name}</strong> cannot be synced to Digitax because required fields are missing.</p>"
						   f"<p><strong>Missing Fields:</strong></p>"
						   f"<ul>{''.join(f'<li>{field}</li>' for field in missing_fields)}</ul>",
				action_required=f"Fill in the missing fields: {', '.join(missing_fields)}",
				reference_doctype="Item",
				reference_name=item_name,
				related_data=frappe.as_json({"missing_fields": missing_fields}),
				priority="Medium"
			)
			
			frappe.throw(
				f"Missing required fields for Digitax sync: {', '.join(missing_fields)}. "
				f"An actionable item has been created for follow-up."
			)
		
		# Build payload
		payload = {
			"item_class_code": item.custom_item_class_code,
			"item_type_code": item.custom_digitax_item_type_code,
			"item_name": item.item_name,
			"origin_nation_code": item.custom_digitax_origin_nation_code or "KE",
			"package_unit_code": item.custom_digitax_package_unit_code,
			"quantity_unit_code": item.custom_digitax_quantity_unit_code,
			"tax_type_code": item.custom_tax_type_code,
			"default_unit_price": float(price_list_rate)
		}
		
		# Send to Digitax
		client = DigitaxClient()
		response = client.create_item(payload)
		
		# Check HTTP status code for successful creation (201)
		http_status = response.get("http_status_code")
		
		if http_status != 201:
			frappe.throw(
				f"Failed to create item in Digitax. HTTP {http_status} returned. "
				f"Expected 201 (Created)."
			)
		
		# HTTP 201 = Success - Update item with Digitax response
		item.custom_digitax_id = response.get("id")
		item.custom_digitax_etims_item_code = response.get("etims_item_code")
		item.custom_digitax_synced = 1
		item.custom_digitax_last_sync = now_datetime()
		
		item.save(ignore_permissions=True)
		
		frappe.db.commit()
		
		return {
			"status": "success",
			"message": f"Item synced to Digitax successfully (HTTP {http_status}). "
					  f"Digitax ID: {response.get('id')}, ETIMS Code: {response.get('etims_item_code')}",
			"digitax_id": response.get("id"),
			"etims_item_code": response.get("etims_item_code")
		}
	
	except frappe.ValidationError as e:
		# Already handled - just re-raise
		raise
	
	except Exception as e:
		# Create actionable item for unexpected errors
		from rusinga_school.rusinga_school.doctype.actionable_items.actionable_items import create_actionable_item
		
		create_actionable_item(
			title=f"Digitax Sync Failed: {item_name}",
			item_type="Data Sync Issue",
			description=f"<p>Failed to sync item <strong>{item_name}</strong> to Digitax.</p>"
					   f"<p><strong>Error:</strong> {str(e)}</p>",
			action_required=f"Review error and retry sync: {str(e)}",
			reference_doctype="Item",
			reference_name=item_name,
			related_data=frappe.as_json({"error": str(e)}),
			priority="High"
		)
		
		frappe.log_error(
			message=f"Error syncing item {item_name} to Digitax: {str(e)}\n{frappe.get_traceback()}",
			title=f"Digitax Item Sync Failed - {item_name}"
		)
		
		frappe.throw(f"Failed to sync item to Digitax: {str(e)}")
