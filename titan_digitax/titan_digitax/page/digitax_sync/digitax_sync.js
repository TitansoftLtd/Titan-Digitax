frappe.provide("titan_digitax");

const SYNC_TYPES_BY_DIRECTION = {
	"To DigiTax": ["Invoices"],
	"From DigiTax": ["Customers", "Items"],
};

const NOT_YET_AVAILABLE_SYNC_TYPES = ["Customers"];

frappe.pages["digitax-sync"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Digitax Sync"),
		single_column: true,
	});

	// Layout: full-width progress bar at top, then two-column content, then job list.
	// Page gutter / max-width live in digitax_sync.css (see the note there — v16 gives
	// custom Desk pages no padding of their own).
	const $layout = $(`
        <div class="digitax-sync-page">
            <div data-fieldname="progress" style="margin-bottom: 16px;">
                <div class="progress" style="height: 28px;">
                    <div class="progress-bar progress-bar-striped bg-secondary" role="progressbar"
                         data-fieldname="progress-bar" style="width: 0%">0%</div>
                </div>
                <div class="mt-2 d-flex justify-content-between" style="font-size: 13px; color: #555;">
                    <div data-fieldname="progress-message">${__("Ready to sync")}</div>
                    <div data-fieldname="progress-details" style="font-size: 12px; color: #888;"></div>
                </div>
            </div>
            <div class="row g-3 align-items-start">
                <div class="col-md-6">
                    <div class="card shadow-none border-0">
                        <div class="card-body p-0 digitax-sync-form">
                            <div class="form-group" data-fieldname="direction-group">
                                <label class="control-label">${__("Direction")}</label>
                                <select class="form-control" data-fieldname="direction">
                                    <option value="">${__("Select Direction")}</option>
                                    <option value="To DigiTax">${__("To DigiTax")}</option>
                                    <option value="From DigiTax">${__("From DigiTax")}</option>
                                </select>
                            </div>
                            <div class="form-group mt-2" data-fieldname="sync-type-group">
                                <label class="control-label">${__("Sync Type")}</label>
                                <select class="form-control" data-fieldname="sync_type">
                                    <option value="">${__("Select Direction first")}</option>
                                </select>
                            </div>
                            <div class="form-group mt-2" data-fieldname="company-group">
                                <label class="control-label">${__("Company")}</label>
                                <div data-fieldname="company-control"></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="mb-3 digitax-sync-actions">
                        <button class="btn btn-primary" data-action="execute">${__("Execute Sync")}</button>
                        <button class="btn btn-secondary ml-2" data-action="refresh" style="display:none;">${__("Refresh Status")}</button>
                    </div>
                    <div class="mb-2 text-muted" data-fieldname="job_id_display" style="font-size: 11px;"></div>
                    <div data-fieldname="status"></div>
                </div>
            </div>

            <!-- Job List Section -->
            <hr style="margin-top: 20px;">
            <div class="mt-4" data-fieldname="job-list-section">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="mb-0">${__("Sync Jobs Queue")}</h5>
                    <button class="btn btn-xs btn-default" data-action="refresh-jobs">
                        <i class="fa fa-refresh"></i> ${__("Refresh")}
                    </button>
                </div>
                <div data-fieldname="running-jobs-warning" class="alert alert-warning" style="display: none;">
                    <i class="fa fa-exclamation-triangle"></i>
                    <span data-fieldname="running-jobs-count"></span>
                </div>
                <div class="digitax-sync-jobs-scroll">
                    <div data-fieldname="job-list-table" class="frappe-list">
                        <div class="text-muted text-center py-3">${__("Loading jobs...")}</div>
                    </div>
                </div>
            </div>
        </div>
    `);

	page.body.empty().append($layout);

	const directionSelect = $layout.find('[data-fieldname="direction"]');
	const syncTypeSelect = $layout.find('[data-fieldname="sync_type"]');
	const companyControlWrapper = $layout.find('[data-fieldname="company-control"]');
	const statusBox = $layout.find('[data-fieldname="status"]');
	const progressBar = $layout.find('[data-fieldname="progress-bar"]');
	const progressMessage = $layout.find('[data-fieldname="progress-message"]');
	const progressDetails = $layout.find('[data-fieldname="progress-details"]');
	const executeBtn = $layout.find('[data-action="execute"]');
	const refreshBtn = $layout.find('[data-action="refresh"]');
	const jobIdDisplay = $layout.find('[data-fieldname="job_id_display"]');
	const jobListTable = $layout.find('[data-fieldname="job-list-table"]');
	const runningJobsWarning = $layout.find('[data-fieldname="running-jobs-warning"]');
	const runningJobsCount = $layout.find('[data-fieldname="running-jobs-count"]');
	const refreshJobsBtn = $layout.find('[data-action="refresh-jobs"]');

	let pollInterval = null;
	let currentJobId = null;

	// Company selection — mirrors st_austins' Engage Sync page: an unrestricted Link so
	// any Company can be picked, not just ones with existing Digitax Company Settings.
	const COMPANY_STORAGE_KEY = "titan_digitax_sync_company";

	function selectedCompany() {
		return companyField.get_value() || null;
	}

	const companyField = frappe.ui.form.make_control({
		df: {
			fieldtype: "Link",
			options: "Company",
			fieldname: "company",
			placeholder: __("Select a Company"),
			get_query: () => ({ filters: { is_group: 0 } }),
			onchange: () => {
				localStorage.setItem(COMPANY_STORAGE_KEY, selectedCompany() || "");
				loadJobList();
			},
		},
		parent: companyControlWrapper,
		render_input: true,
	});
	companyField.refresh();

	const remembered = localStorage.getItem(COMPANY_STORAGE_KEY);
	if (remembered) {
		companyField.set_value(remembered);
	}

	function updateSyncTypeOptions() {
		const direction = directionSelect.val();
		const options = direction ? SYNC_TYPES_BY_DIRECTION[direction] || [] : [];

		syncTypeSelect.empty();
		if (!direction) {
			syncTypeSelect.append(`<option value="">${__("Select Direction first")}</option>`);
			return;
		}

		syncTypeSelect.append(`<option value="">${__("Select Sync Type")}</option>`);
		options.forEach((opt) => {
			const label = NOT_YET_AVAILABLE_SYNC_TYPES.includes(opt) ? `${opt} (${__("Coming Soon")})` : opt;
			syncTypeSelect.append(`<option value="${opt}">${label}</option>`);
		});
	}

	directionSelect.on("change", updateSyncTypeOptions);
	updateSyncTypeOptions();

	// Job list functions
	function getStatusBadge(status) {
		const badges = {
			"Queued": '<span class="badge badge-secondary">Queued</span>',
			"Running": '<span class="badge badge-primary"><i class="fa fa-spinner fa-spin"></i> Running</span>',
			"Success": '<span class="badge badge-success"><i class="fa fa-check"></i> Success</span>',
			"Failed": '<span class="badge badge-danger"><i class="fa fa-times"></i> Failed</span>',
		};
		return badges[status] || `<span class="badge badge-secondary">${status || "Unknown"}</span>`;
	}

	function formatDateTime(dt) {
		if (!dt) return "-";
		return frappe.datetime.str_to_user(dt);
	}

	function loadJobList() {
		frappe.call({
			method: "titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.get_recent_sync_jobs",
			args: { limit: 10, company: selectedCompany() },
			callback: function (r) {
				const jobs = r.message || [];
				renderJobList(jobs);

				const runningJobs = jobs.filter((j) => j.status === "Running");
				if (runningJobs.length > 0) {
					const myRunning = runningJobs.filter((j) => j.is_current_user).length;
					const othersRunning = runningJobs.length - myRunning;

					let msg = "";
					if (myRunning > 0 && othersRunning > 0) {
						msg = __("{0} running job(s) by you, {1} by others.", [myRunning, othersRunning]);
					} else if (myRunning > 0) {
						msg = __("{0} running job(s) by you. Wait for completion before starting new ones.", [myRunning]);
					} else {
						msg = __("{0} running job(s) by other users.", [othersRunning]);
					}

					runningJobsCount.text(msg);
					runningJobsWarning.show();
				} else {
					runningJobsWarning.hide();
				}
			},
		});
	}

	function renderJobList(jobs) {
		if (!jobs.length) {
			jobListTable.html(`<div class="text-muted text-center py-3">${__("No sync jobs found")}</div>`);
			return;
		}

		let html = `
            <table class="table table-bordered table-sm" style="font-size: 12px;">
                <thead class="thead-light">
                    <tr>
                        <th>${__("Direction")}</th>
                        <th>${__("Sync Type")}</th>
                        <th>${__("Company")}</th>
                        <th>${__("Status")}</th>
                        <th>${__("Progress")}</th>
                        <th>${__("Records")}</th>
                        <th>${__("Errors")}</th>
                        <th>${__("Started By")}</th>
                        <th>${__("Started At")}</th>
                        <th>${__("Message")}</th>
                    </tr>
                </thead>
                <tbody>
        `;

		jobs.forEach((job) => {
			const rowClass = job.is_current_user ? "table-info" : "";
			const userBadge = job.is_current_user ? ' <span class="badge badge-info">You</span>' : "";
			const progress = job.percentage_complete || 0;

			html += `
                <tr class="${rowClass}">
                    <td>${frappe.utils.escape_html(job.direction || "-")}</td>
                    <td>${frappe.utils.escape_html(job.sync_type || "-")}</td>
                    <td>${frappe.utils.escape_html(job.company || "-")}</td>
                    <td>${getStatusBadge(job.status)}</td>
                    <td>
                        <div class="progress" style="height: 18px; min-width: 80px;">
                            <div class="progress-bar ${job.status === "Running" ? "progress-bar-striped progress-bar-animated" : ""}
                                ${job.status === "Success" ? "bg-success" : job.status === "Failed" ? "bg-danger" : "bg-primary"}"
                                style="width: ${progress}%">${progress}%</div>
                        </div>
                    </td>
                    <td>${job.processed_records || 0} / ${job.total_records || 0}</td>
                    <td>${job.errors || 0}</td>
                    <td>${frappe.utils.escape_html(job.owner_name || job.owner)}${userBadge}</td>
                    <td>${formatDateTime(job.started_at)}</td>
                    <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                        title="${frappe.utils.escape_html(job.last_message || "")}">
                        ${frappe.utils.escape_html(job.last_message || "-")}
                    </td>
                </tr>
            `;
		});

		html += `</tbody></table>`;
		jobListTable.html(html);
	}

	refreshJobsBtn.on("click", function () {
		$(this).find("i").addClass("fa-spin");
		loadJobList();
		setTimeout(() => $(this).find("i").removeClass("fa-spin"), 500);
	});

	loadJobList();
	checkForRunningJob();
	setInterval(loadJobList, 5000); // Refresh every 5 seconds

	function checkForRunningJob() {
		frappe.call({
			method: "titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.get_last_running_job",
			args: { company: selectedCompany() },
			callback: function (r) {
				if (r.message && r.message.job_id) {
					const job = r.message;
					currentJobId = job.job_id;
					jobIdDisplay.text(`Job ID: ${job.job_id}`);
					refreshBtn.show();
					executeBtn.prop("disabled", true);

					progressBar.removeClass("bg-success bg-danger bg-secondary").addClass("progress-bar-animated bg-primary");

					updateJobDisplay(job);
					pollJobProgress(job.job_id);

					frappe.show_alert({
						message: __("Resumed tracking running sync job"),
						indicator: "blue",
					});
				}
			},
		});
	}

	function updateJobDisplay(job) {
		const percentage = job.percentage_complete || 0;

		progressBar.css("width", percentage + "%").text(percentage + "%");
		progressMessage.text(job.last_message || "Processing...");
		progressDetails.html(
			`<b>Processed:</b> ${job.processed_records || 0} / ${job.total_records || 0} | ` +
				`<b>Skipped:</b> ${job.skipped_records || 0} | <b>Errors:</b> ${job.errors || 0}`
		);

		if (job.status !== "Running" && job.status !== "Queued") {
			if (pollInterval) clearInterval(pollInterval);
			executeBtn.prop("disabled", false);

			if (job.status === "Success") {
				progressBar.removeClass("progress-bar-animated bg-primary bg-secondary").addClass("bg-success");
				statusBox.html(
					`<div class="alert alert-success">
                        <div><b>${__("Sync Completed Successfully!")}</b></div>
                        <div>${job.last_message || ""}</div>
                    </div>`
				);
			} else if (job.status === "Failed") {
				progressBar.removeClass("progress-bar-animated bg-primary bg-secondary").addClass("bg-danger");
				statusBox.html(
					`<div class="alert alert-danger">
                        <div><b>${__("Sync Failed")}</b></div>
                        <div>${job.last_message || ""}</div>
                    </div>`
				);
			}
			loadJobList();
		}
	}

	function fetchJobStatus(job_id) {
		frappe.call({
			method: "frappe.client.get",
			args: {
				doctype: "Digitax Sync Job",
				name: job_id,
			},
			callback: function (r) {
				if (r.message) {
					updateJobDisplay(r.message);
				}
			},
			error: function (err) {
				console.error("Failed to fetch job:", err);
				statusBox.html(
					`<div class="alert alert-warning">
                        ${__("Could not fetch job status. Job ID")}: ${job_id}
                    </div>`
				);
			},
		});
	}

	function pollJobProgress(job_id) {
		if (pollInterval) clearInterval(pollInterval);

		fetchJobStatus(job_id);
		pollInterval = setInterval(() => {
			fetchJobStatus(job_id);
		}, 2000);
	}

	refreshBtn.on("click", () => {
		if (currentJobId) {
			refreshBtn.prop("disabled", true).text(__("Refreshing..."));
			frappe.call({
				method: "frappe.client.get",
				args: {
					doctype: "Digitax Sync Job",
					name: currentJobId,
				},
				callback: function (r) {
					refreshBtn.prop("disabled", false).text(__("Refresh Status"));
					if (r.message) {
						updateJobDisplay(r.message);
						frappe.show_alert({ message: __("Status refreshed"), indicator: "green" });
					}
				},
				error: function () {
					refreshBtn.prop("disabled", false).text(__("Refresh Status"));
					frappe.show_alert({ message: __("Failed to refresh status"), indicator: "red" });
				},
			});
		}
	});

	executeBtn.on("click", () => {
		const direction = directionSelect.val();
		const sync_type = syncTypeSelect.val();
		const company = selectedCompany();

		if (!direction) {
			frappe.msgprint({
				title: __("Direction Required"),
				message: __("Please select a Direction before executing."),
				indicator: "orange",
			});
			return;
		}

		if (!sync_type) {
			frappe.msgprint({
				title: __("Sync Type Required"),
				message: __("Please select a Sync Type before executing."),
				indicator: "orange",
			});
			return;
		}

		if (!company) {
			frappe.msgprint({
				title: __("Company Required"),
				message: __("Please select a Company before executing."),
				indicator: "orange",
			});
			return;
		}

		if (NOT_YET_AVAILABLE_SYNC_TYPES.includes(sync_type)) {
			frappe.msgprint({
				title: __("Coming Soon"),
				message: __("{0} sync is not available yet. Coming soon.", [sync_type]),
				indicator: "blue",
			});
			return;
		}

		const preposition = direction === "To DigiTax" ? __("to") : __("from");

		frappe.confirm(__("Are you sure you want to sync {0} {1} Digitax for {2}?", [sync_type, preposition, company]), function () {
			statusBox.html("");
			progressBar
				.css("width", "0%")
				.text("0%")
				.removeClass("bg-success bg-danger bg-secondary")
				.addClass("progress-bar-animated bg-primary");
			progressMessage.text(__("Starting sync..."));
			progressDetails.text("");
			executeBtn.prop("disabled", true);

			frappe.call({
				method: "titan_digitax.titan_digitax.doctype.digitax_sync_job.digitax_sync_job.execute_digitax_sync",
				args: { direction, sync_type, company },
			})
				.then((r) => {
					const msg = r && r.message ? r.message : {};
					if (msg.status === "unavailable") {
						executeBtn.prop("disabled", false);
						progressMessage.text(__("Ready to sync"));
						frappe.msgprint({
							title: __("Coming Soon"),
							message: msg.message,
							indicator: "blue",
						});
						return;
					}

					if (msg.job_id) {
						currentJobId = msg.job_id;
						jobIdDisplay.text(`Job ID: ${msg.job_id}`);
						refreshBtn.show();
						pollJobProgress(msg.job_id);
						loadJobList();
					}
				})
				.catch((err) => {
					executeBtn.prop("disabled", false);
					progressBar.removeClass("progress-bar-animated bg-primary").addClass("bg-danger");
					progressBar.css("width", "100%").text(__("Error"));
					progressMessage.text(__("Failed to start sync"));
					statusBox.html(
						`<div class="alert alert-danger">
                            ${__("Error starting sync")}: ${err.message || err}
                        </div>`
					);
				});
		});
	});
};
