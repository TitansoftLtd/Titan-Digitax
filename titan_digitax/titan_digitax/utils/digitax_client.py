# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""
Digitax API Client for pulling data FROM Digitax into ERPNext.
Reuses authentication and base URL from this company's Digitax Company Settings.
"""

import frappe
import requests
from frappe import _

# DigiTax's GET /items has no name/code filter (confirmed against their published
# OpenAPI spec - only before/after/page_size, pure pagination) and no dedicated
# search endpoint exists anywhere in their API, so a name lookup has no cheaper
# option than walking every page. Caching the full catalogue as a name->item map
# means a burst of separate lookups against the same company (e.g. Engage
# creating several new items in a row, each triggering its own auto-sync) only
# pays for the walk once instead of per item, at the cost of every lookup -
# including a lone, isolated one - now always walking the full catalogue to
# populate the cache rather than potentially exiting early once a match is
# found. Short TTL bounds how stale a "not found" answer can get if the real
# catalogue changes elsewhere; explicitly invalidated on this client's own
# create_item calls too.
ITEM_CATALOGUE_CACHE_TTL_SECONDS = 300


class DigitaxClient:
	"""Client for interacting with Digitax API to pull data."""

	def _item_catalogue_cache_key(self):
		return f"titan_digitax:item_catalogue:{self.company}"

	def _get_cached_item_catalogue(self):
		return frappe.cache().get_value(self._item_catalogue_cache_key())

	def _set_cached_item_catalogue(self, catalogue):
		frappe.cache().set_value(
			self._item_catalogue_cache_key(), catalogue, expires_in_sec=ITEM_CATALOGUE_CACHE_TTL_SECONDS
		)

	def invalidate_item_catalogue_cache(self):
		"""Call after any write that changes this company's DigiTax catalogue (e.g.
		create_item succeeding) - a cached "not found" answer must never outlive
		an item that was just created.
		"""
		frappe.cache().delete_value(self._item_catalogue_cache_key())


	def __init__(self, company):
		"""Initialize client with this company's own Digitax Company Settings."""
		from titan_digitax.titan_digitax.utils.company_config import (
			get_digitax_company_config,
			get_digitax_settings,
		)

		self.company = company
		self.settings = get_digitax_settings(company)

		cfg = get_digitax_company_config(company)

		if not cfg.base_url:
			frappe.throw(_("Digitax Base URL is not configured for {0}").format(company))
		if not cfg.api_key:
			frappe.throw(_("Digitax API Key is not configured for {0}").format(company))

		self.base_url = cfg.base_url
		self.api_key = cfg.api_key

		self.timeout = int(self.settings.get("api_request_timeout") or 30)
	
	def _get_headers(self):
		"""Get common headers for Digitax API requests (same as sales.py)."""
		return {
			"accept": "application/json",
			"X-API-Key": self.api_key,
			"content-type": "application/json"
		}
	
	def _make_request(self, method, endpoint, data=None, params=None):
		"""
		Make HTTP request to Digitax API.
		
		Args:
			method: HTTP method (GET, POST, etc.)
			endpoint: API endpoint (e.g. "/customers")
			data: Request payload for POST/PUT
			params: Query parameters
		
		Returns:
			Response JSON
		"""
		url = f"{self.base_url}{endpoint}"
		headers = self._get_headers()

		try:
			response = requests.request(
				method=method,
				url=url,
				headers=headers,
				json=data,
				params=params,
				timeout=self.timeout
			)
			
			# Log request details
			frappe.logger().info(f"Digitax API {method} {endpoint}: Status {response.status_code}")
			
			# Handle errors
			if response.status_code >= 400:
				error_msg = f"Digitax API error: {response.status_code} - {response.text}"
				frappe.logger().error(error_msg)
				frappe.throw(error_msg)
			
			return response.json()
			
		except requests.exceptions.Timeout:
			frappe.throw(_("Digitax API request timed out after {0} seconds").format(self.timeout))
		except requests.exceptions.RequestException as e:
			frappe.throw(_("Digitax API request failed: {0}").format(str(e)))
		except Exception as e:
			frappe.throw(_("Unexpected error communicating with Digitax: {0}").format(str(e)))
	
	def fetch_customers(self, start_time=None, end_time=None):
		"""
		Fetch customers from Digitax.
		
		TODO: Implement when Digitax customer endpoint is provided.
		
		Args:
			start_time: Optional filter for customers modified after this time
			end_time: Optional filter for customers modified before this time
		
		Returns:
			List of customer records
		"""
		frappe.logger().info("fetch_customers called - endpoint not yet configured")
		
		# Placeholder return
		# When endpoint is provided, implement like:
		# params = {}
		# if start_time:
		#     params['start_time'] = start_time
		# if end_time:
		#     params['end_time'] = end_time
		# return self._make_request("GET", "/customers", params=params)
		
		return []
	
	def _paginate_items(self, params=None):
		"""
		Yield every item across GET /items pages for this company's account.

		API Endpoint: GET /items
		Response Format: {"pagination": {"next": "item_id", "page_size": 20}, "data": [...items...]}

		Pagination:
		- API returns max 20 items per page
		- Use 'after' parameter to get next page: /items?after=item_id
		- Continue until page_size < 20 (indicates last page)

		This is a generator so a caller looking for one specific item (e.g.
		find_item_by_name) can stop as soon as it finds a match, instead of always
		paying for the full catalogue fetch — DigiTax's API has no server-side
		search/filter by name, so any name lookup has to walk pages until found.
		"""
		params = dict(params or {})
		page_count = 0
		after_cursor = None

		while True:
			page_count += 1

			if after_cursor:
				params['after'] = after_cursor

			frappe.logger().info(f"Fetching page {page_count} (after={after_cursor})...")

			response = self._make_request("GET", "/items", params=params)

			if not isinstance(response, dict):
				frappe.logger().warning(f"Unexpected response format on page {page_count}")
				break

			items = response.get('data', [])
			pagination = response.get('pagination', {})

			if not isinstance(items, list):
				frappe.logger().warning(f"No items in response on page {page_count}")
				break

			for item in items:
				yield item

			page_size = pagination.get('page_size', 0)
			next_cursor = pagination.get('next')

			# Stop if page_size < 20 (last page) or no next cursor
			if page_size < 20 or not next_cursor:
				break

			after_cursor = next_cursor

			# Safety check: prevent infinite loops (max 1000 pages = 20,000 items)
			if page_count >= 1000:
				frappe.logger().warning("Reached max page limit (1000) paginating /items")
				break

	def fetch_items(self, start_time=None, end_time=None):
		"""
		Fetch ALL items from Digitax (with pagination support).

		Args:
			start_time: Optional filter for items modified after this time
			end_time: Optional filter for items modified before this time

		Returns:
			List of all item records from Digitax (across all pages)
		"""
		frappe.logger().info("Fetching items from Digitax (with pagination)...")

		params = {}
		if start_time:
			params['start_time'] = start_time
		if end_time:
			params['end_time'] = end_time

		all_items = list(self._paginate_items(params))
		frappe.logger().info(f"Completed fetching all items: {len(all_items)} items")
		return all_items

	def find_item_by_name(self, item_name):
		"""
		Look for an existing DigiTax catalogue item with this exact item_name.

		DigiTax's GET /items has no name filter and no search endpoint exists
		anywhere in their API, so a name lookup has to walk pages regardless.
		Backed by a short-TTL cached name->item map (see ITEM_CATALOGUE_CACHE_TTL_SECONDS
		above) so a burst of separate lookups against this company only pays for
		the walk once.

		Returns the raw DigiTax item dict, or None if no match exists.
		"""
		target = (item_name or "").strip()
		if not target:
			return None

		cached = self._get_cached_item_catalogue()
		if cached is not None:
			return cached.get(target)

		catalogue = {}
		for item in self._paginate_items():
			name = (item.get("item_name") or "").strip()
			if name:
				catalogue[name] = item

		self._set_cached_item_catalogue(catalogue)
		return catalogue.get(target)
	
	def create_item(self, payload):
		"""
		Create a new item in Digitax.
		
		Args:
			payload (dict): Item data to send to Digitax
		
		Returns:
			dict: Digitax response with item ID, etims_item_code, status, etc.
				  Includes 'http_status_code' key with the HTTP status code
		"""
		frappe.logger().info(f"Creating item in Digitax: {payload.get('item_name')}")
		
		try:
			# Make direct request to get status code
			url = f"{self.base_url}/items"
			headers = self._get_headers()
			
			response = requests.post(
				url=url,
				headers=headers,
				json=payload,
				timeout=self.timeout
			)
			
			frappe.logger().info(f"Digitax item creation response: HTTP {response.status_code}")
			
			# Handle errors
			if response.status_code >= 400:
				error_msg = f"Digitax API error: {response.status_code} - {response.text}"
				frappe.logger().error(error_msg)
				frappe.throw(error_msg)
			
			# Parse response
			data = response.json()
			
			# Add HTTP status code to response for caller
			data['http_status_code'] = response.status_code
			
			frappe.logger().info(
				f"Item creation response: HTTP {response.status_code}, "
				f"ID: {data.get('id')}, ETIMS: {data.get('etims_item_code')}"
			)

			# The cached catalogue (if any) is now stale - it wouldn't contain
			# this item. A cached "not found" must never outlive an item that
			# was just created, or the next lookup in the same burst would
			# wrongly think it still needs creating.
			self.invalidate_item_catalogue_cache()

			return data
			
		except requests.exceptions.Timeout:
			error_msg = f"Digitax API request timed out after {self.timeout} seconds"
			frappe.logger().error(error_msg)
			frappe.throw(error_msg)
		except requests.exceptions.RequestException as e:
			error_msg = f"Digitax API request failed: {str(e)}"
			frappe.logger().error(error_msg)
			frappe.throw(error_msg)
		except Exception as e:
			error_msg = f"Failed to create item in Digitax: {str(e)}"
			frappe.logger().error(error_msg)
			raise
