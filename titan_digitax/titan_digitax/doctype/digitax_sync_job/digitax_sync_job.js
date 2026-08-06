// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

const SYNC_TYPES_BY_DIRECTION = {
	"To DigiTax": ["Invoices"],
	"From DigiTax": ["Customers", "Items"],
};

const NOT_YET_AVAILABLE_SYNC_TYPES = ["Customers"];

frappe.ui.form.on("Digitax Sync Job", {
	refresh: function(frm) {
		update_sync_type_options(frm);

		// Only show Execute button if direction, sync type and company are selected
		if (frm.doc.direction && frm.doc.sync_type && frm.doc.company) {
			// Always show execute button (allows re-running)
			frm.add_custom_button(__("Execute Sync"), function() {
				execute_sync(frm);
			}).addClass("btn-primary");
		}

		// Add indicator based on status
		if (frm.doc.status) {
			const status_colors = {
				"Pending": "gray",
				"Running": "blue",
				"Completed": "green",
				"Failed": "red"
			};

			frm.page.set_indicator(
				frm.doc.status,
				status_colors[frm.doc.status] || "gray"
			);
		}
	},

	direction: function(frm) {
		// Changing direction invalidates whatever Sync Type was picked for the old one.
		frm.set_value("sync_type", "");
		update_sync_type_options(frm);
		frm.trigger("refresh");
	},

	sync_type: function(frm) {
		// Trigger refresh when sync type changes to show/hide button
		frm.trigger("refresh");
	},

	company: function(frm) {
		// Trigger refresh when company changes to show/hide button
		frm.trigger("refresh");
	}
});

function update_sync_type_options(frm) {
	const options = frm.doc.direction ? SYNC_TYPES_BY_DIRECTION[frm.doc.direction] || [] : [];
	frm.set_df_property("sync_type", "options", ["", ...options].join("\n"));
	frm.refresh_field("sync_type");
}

function execute_sync(frm) {
	if (!frm.doc.direction) {
		frappe.msgprint({
			title: __("Direction Required"),
			message: __("Please select a Direction before executing."),
			indicator: "red"
		});
		return;
	}

	if (!frm.doc.sync_type) {
		frappe.msgprint({
			title: __("Sync Type Required"),
			message: __("Please select a Sync Type before executing."),
			indicator: "red"
		});
		return;
	}

	if (!frm.doc.company) {
		frappe.msgprint({
			title: __("Company Required"),
			message: __("Please select a Company before executing."),
			indicator: "red"
		});
		return;
	}

	// Placeholder sync types short-circuit client-side — no server round-trip needed.
	if (NOT_YET_AVAILABLE_SYNC_TYPES.includes(frm.doc.sync_type)) {
		frappe.msgprint({
			title: __("Coming Soon"),
			message: __("{0} sync is not available yet. Coming soon.", [frm.doc.sync_type]),
			indicator: "blue"
		});
		return;
	}

	// Store the selected values before clearing
	const selected_direction = frm.doc.direction;
	const selected_sync_type = frm.doc.sync_type;
	const selected_company = frm.doc.company;
	const preposition = selected_direction === "To DigiTax" ? __("to") : __("from");

	// Confirm execution
	frappe.confirm(
		__("Are you sure you want to sync {0} {1} Digitax for {2}?", [selected_sync_type, preposition, selected_company]),
		function() {
			// User confirmed
			frappe.call({
				method: "titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.execute_digitax_sync",
				args: {
					direction: selected_direction,
					sync_type: selected_sync_type,
					company: selected_company
				},
				freeze: true,
				freeze_message: __("Starting sync job..."),
				callback: function(r) {
					if (r.message && r.message.status === "success") {
						frappe.show_alert({
							message: __(r.message.message),
							indicator: "green"
						}, 5);

						// Clear the sync type selector
						frm.set_value("sync_type", "");

						// Reload form to show updated status
						frm.reload_doc();
					} else if (r.message && r.message.status === "unavailable") {
						frappe.msgprint({
							title: __("Coming Soon"),
							message: r.message.message,
							indicator: "blue"
						});
					} else {
						frappe.msgprint({
							title: __("Sync Error"),
							message: __("Failed to start sync job. Please check error log."),
							indicator: "red"
						});
					}
				},
				error: function(r) {
					frappe.msgprint({
						title: __("Sync Error"),
						message: __("An error occurred while starting the sync job.<br><br>{0}", [r.message || "Unknown error"]),
						indicator: "red"
					});
				}
			});
		}
	);
}
