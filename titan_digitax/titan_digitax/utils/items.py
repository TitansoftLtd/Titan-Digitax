# -*- coding: utf-8 -*-
# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime
from titan_digitax.titan_digitax.utils.digitax_client import DigitaxClient


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
				item_type="Digitax Item Sync Failed",
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
		
		# Update item with Digitax response
		item.custom_digitax_id = response.get("id")
		item.custom_digitax_etims_item_code = response.get("etims_item_code")
		item.custom_digitax_status = response.get("status")
		item.custom_digitax_active = 1 if response.get("active") else 0
		item.custom_digitax_synced = 1
		item.custom_digitax_last_sync = now_datetime()
		
		item.save(ignore_permissions=True)
		
		frappe.db.commit()
		
		return {
			"status": "success",
			"message": f"Item synced to Digitax successfully. Digitax ID: {response.get('id')}, ETIMS Code: {response.get('etims_item_code')}",
			"digitax_id": response.get("id"),
			"etims_item_code": response.get("etims_item_code"),
			"digitax_status": response.get("status")
		}
	
	except frappe.ValidationError as e:
		# Already handled - just re-raise
		raise
	
	except Exception as e:
		# Create actionable item for unexpected errors
		from rusinga_school.rusinga_school.doctype.actionable_items.actionable_items import create_actionable_item
		
		create_actionable_item(
			title=f"Digitax Sync Failed: {item_name}",
			item_type="Digitax Item Sync Failed",
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
