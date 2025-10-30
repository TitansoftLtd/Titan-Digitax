frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 1 && !frm.doc.custom_sent_to_digitax) {
			add_send_to_digitax_button(frm);
		}
	},
});

const add_send_to_digitax_button = (frm) => {
	frm.add_custom_button(__("Send to Digitax"), function () {
		frappe.call({
			method: "titan_digitax.titan_digitax.utils.sales.send_sales_invoice_to_digitax",
			args: {
				docname: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Sending Sales Invoice to Digitax..."),
			callback: function (r) {
				// Python exception occurred
				if (r.exc) {
					frappe.msgprint({
						title: __("Error sending to Digitax"),
						message: __("Error sending to Digitax. Check error log."),
						indicator: "red",
					});
				}

				// Check if message exists and has the expected structure
				if (r.message) {
					// Check for API error response (has 'code' property indicating error)
					if (r.message.code) {
						frappe.msgprint({
							title: __("Digitax API Error"),
							message: __("Digitax Error: {0}", [
								r.message.message || "Unknown error",
							]),
							indicator: "orange",
						});
					}

					// Success - check for expected success properties
					if (r.message.id && r.message.status) {
						frappe.msgprint({
							title: __("Success"),
							message: __("Sales Invoice sent to Digitax successfully"),
							indicator: "green",
						});
					}
				}
			},
			error: function (r) {
				// This catches network errors or server errors
				frappe.msgprint({
					title: __("Connection Error"),
					message: __("Failed to connect to Digitax"),
					indicator: "red",
				});
			},
		});
	});
};
