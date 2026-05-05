import frappe
from frappe.utils.password import get_decrypted_password

def get_digitax_credentials():
    try:
        digitax_base_url = frappe.get_single("Digitax Settings").base_url
        digitax_api_key = get_decrypted_password(
            "Digitax Settings", "Digitax Settings", "api_key"
        )
        return digitax_base_url, digitax_api_key
    except Exception as e:
        frappe.msgprint("Error fetching Digitax credentials. Check error log for details.")
        frappe.log_error(f"Error fetching Digitax credentials: {str(e)}", "Digitax Utils")
        return None, None

@frappe.whitelist(allow_guest=True, methods=["POST"])
def digitax_callback_sales_with_items():
    try:
        # Get the request data
        if frappe.request.content_type == "application/json":
            data = frappe.request.get_json()
        else:
            data = frappe.form_dict

        data = data.get("data", {})

        sale_id = data.get("id")
        trader_invoice_number = data.get("trader_invoice_number")
        status = data.get("status", "")
        etims_url = data.get("etims_url", "")

        if not trader_invoice_number or not sale_id:
            frappe.log_error(
                title="Digitax Callback Error",
                message="Missing trader_invoice_number or sale_id in callback data"
            )
            return {
                "status": "error",
                "message": "Missing trader_invoice_number or sale_id"
            }

        invoice_name = frappe.db.get_value(
            "Sales Invoice",
            {"custom_trader_invoice_number": trader_invoice_number},
            "name",
        ) or (trader_invoice_number if frappe.db.exists("Sales Invoice", trader_invoice_number) else None)

        if invoice_name:
            doc = frappe.get_doc("Sales Invoice", invoice_name)
            doc.custom_digitax_status = status
            doc.custom_etims_url = etims_url
            doc.custom_sale_id = sale_id
            doc.add_comment("Comment", f"Digitax Callback received. Status: {status}, ETIMS URL: {etims_url}")
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return {"status": "success"}

        try:
            amendment_row = frappe.db.get_value(
                "Digitax Amendment Row",
                {"trader_invoice_number": trader_invoice_number},
                ["name", "parent", "amendment_type"],
                as_dict=True,
            )
        except Exception:
            amendment_row = None

        if amendment_row:
            frappe.db.set_value(
                "Digitax Amendment Row",
                amendment_row.name,
                {
                    "digitax_status": status,
                    "etims_url": etims_url,
                    "digitax_sale_id": sale_id,
                },
                update_modified=False,
            )

            if amendment_row.amendment_type == "Virtual Sale":
                frappe.db.set_value(
                    "Sales Invoice",
                    amendment_row.parent,
                    {
                        "custom_digitax_status": status,
                        "custom_etims_url": etims_url,
                        "custom_sale_id": sale_id,
                    },
                    update_modified=False,
                )

            frappe.db.commit()
            return {"status": "success"}

        frappe.log_error(
            title="Digitax Callback Error",
            message=f"No Sales Invoice or Digitax Amendment Row found for trader_invoice_number {trader_invoice_number}"
        )
        return {
            "status": "error",
            "message": "Document not found for trader_invoice_number"
        }

    except Exception as e:
        frappe.log_error(
            title="Digitax Callback Error",
            message=frappe.get_traceback()
        )
        return {
            "status": "error",
            "message": str(e)
        }


def get_digitax_callback_url_for_sales_with_items():
    site_url = frappe.utils.get_url()
    callback_path = "/api/method/titan_digitax.titan_digitax.utils.utils.digitax_callback_sales_with_items"
    return f"{site_url}{callback_path}"
