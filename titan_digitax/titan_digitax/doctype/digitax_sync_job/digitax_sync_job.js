// Copyright (c) 2026, Titan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Digitax Sync Job", {
	refresh: function(frm) {
		// Only show Execute button if sync type is selected
		if (frm.doc.sync_type) {
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
	
	sync_type: function(frm) {
		// Trigger refresh when sync type changes to show/hide button
		frm.trigger("refresh");
	}
});

function execute_sync(frm) {
	if (!frm.doc.sync_type) {
		frappe.msgprint({
			title: __("Sync Type Required"),
			message: __("Please select a Sync Type before executing."),
			indicator: "red"
		});
		return;
	}
	
	// Store the selected sync type before clearing
	const selected_sync_type = frm.doc.sync_type;
	
	// Confirm execution
	frappe.confirm(
		__("Are you sure you want to sync {0} from Digitax?", [selected_sync_type]),
		function() {
			// User confirmed
			frappe.call({
				method: "titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.execute_digitax_sync",
				args: {
					sync_type: selected_sync_type
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
