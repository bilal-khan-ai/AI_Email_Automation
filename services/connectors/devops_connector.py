"""
Azure DevOps REST API Connector
Enables work item creation, linking, and status tracking directly from Support Dashboard.
"""

import os
import logging
import base64
import requests
import json
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)

# Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Connector Module - start

class AzureDevOpsConnector:
    """
    Client for Azure DevOps REST API (v7.1).
    Supports PAT authentication and Azure AD App OAuth token credentials.
    """
    
    DEVOPS_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798/.default"

    def __init__(self, org: str = None, project: str = None, pat: str = None):
        raw_org = org or Config.AZURE_DEVOPS_ORG or ''
        # Sanitize org name in case user inputs visualstudio.com or dev.azure.com URL
        self.org = raw_org.replace('https://', '').replace('http://', '').replace('dev.azure.com/', '').replace('.visualstudio.com', '').strip('/')
        self.project = (project or Config.AZURE_DEVOPS_PROJECT or '').strip('/')
        self.pat = (pat or Config.AZURE_DEVOPS_PAT or '').strip()
        
        self.token = None
        self.token_expires_at = None
        self._credential = None
        # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Organization Graph Users Caching - start
        self._graph_users_cache = []
        self._graph_users_cache_time = None
        # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Organization Graph Users Caching - end

    @property
    def is_configured(self) -> bool:
        """Check if organization and project are configured."""
        return bool(self.org and self.project)

    def _get_auth_headers(self) -> Dict[str, str]:
        """Obtain authorization headers via PAT or Azure AD App token."""
        if self.pat:
            encoded_pat = base64.b64encode(f":{self.pat}".encode('utf-8')).decode('utf-8')
            return {"Authorization": f"Basic {encoded_pat}"}
        
        # Azure AD OAuth Token Flow
        # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Guard token_expires_at against datetime.min OverflowError - start
        if not self.token or not self.token_expires_at or datetime.now() >= self.token_expires_at - timedelta(minutes=5):
            self._refresh_aad_token()
        # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Guard token_expires_at against datetime.min OverflowError - end
            
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
            
        return {}

    def _refresh_aad_token(self):
        """Fetch fresh Azure AD access token for Azure DevOps."""
        try:
            if not self._credential and Config.AZURE_CLIENT_ID and Config.AZURE_CLIENT_SECRET and Config.AZURE_TENANT_ID:
                from azure.identity import ClientSecretCredential
                self._credential = ClientSecretCredential(
                    tenant_id=Config.AZURE_TENANT_ID,
                    client_id=Config.AZURE_CLIENT_ID,
                    client_secret=Config.AZURE_CLIENT_SECRET
                )
                
            if self._credential:
                token_obj = self._credential.get_token(self.DEVOPS_RESOURCE_ID)
                self.token = token_obj.token
                self.token_expires_at = datetime.fromtimestamp(token_obj.expires_on)
                logger.info("✅ Refreshed Azure DevOps OAuth token")
        except Exception as e:
            logger.warning(f"⚠️ Could not obtain Azure DevOps OAuth token: {e}")

    def _base_url(self) -> str:
        return f"https://dev.azure.com/{self.org}/{self.project}/_apis"

    def format_work_item(self, item: Dict) -> Dict[str, Any]:
        """Format raw Azure DevOps work item JSON into simplified dashboard model."""
        fields = item.get('fields', {})
        assigned = fields.get('System.AssignedTo', {})
        assigned_name = assigned.get('displayName') if isinstance(assigned, dict) else str(assigned or 'Unassigned')
        assigned_email = assigned.get('uniqueName', '') if isinstance(assigned, dict) else ''
        
        raw_tags = fields.get('System.Tags', '')
        tags = [t.strip() for t in raw_tags.split(';') if t.strip()] if raw_tags else []
        
        work_item_id = item.get('id')
        item_project = fields.get('System.TeamProject') or self.project
        web_url = f"https://dev.azure.com/{self.org}/{item_project}/_workitems/edit/{work_item_id}"
        
        return {
            'id': work_item_id,
            'title': fields.get('System.Title', 'Untitled'),
            'type': fields.get('System.WorkItemType', 'Task'),
            'state': fields.get('System.State', 'New'),
            'assigned_to': assigned_name or 'Unassigned',
            'assigned_email': assigned_email,
            'tags': tags,
            'priority': fields.get('Microsoft.VSTS.Common.Priority', 2),
            'severity': fields.get('Microsoft.VSTS.Common.Severity', ''),
            'area_path': fields.get('System.AreaPath', ''),
            'iteration_path': fields.get('System.IterationPath', ''),
            'created_date': fields.get('System.CreatedDate'),
            'changed_date': fields.get('System.ChangedDate'),
            'url': web_url
        }

    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Org-Wide Work Item Fetch across all Projects - start
    def get_work_items_batch(self, work_item_ids: List[int]) -> List[Dict[str, Any]]:
        """Fetch multiple work items in chunked batch requests across any project in the organization."""
        if not self.is_configured or not work_item_ids:
            return []
            
        try:
            unique_ids = list(dict.fromkeys(work_item_ids))
            all_formatted = []
            chunk_size = 100
            headers = self._get_auth_headers()
            headers["Accept"] = "application/json"
            
            for i in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[i:i + chunk_size]
                ids_str = ",".join(str(x) for x in chunk)
                url = f"https://dev.azure.com/{self.org}/_apis/wit/workitems?ids={ids_str}&api-version=7.1"
                
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get('value', [])
                    all_formatted.extend([self.format_work_item(item) for item in items])
                else:
                    logger.error(f"❌ DevOps batch fetch failed: HTTP {resp.status_code} - {resp.text}")
                    
            return all_formatted
        except Exception as e:
            logger.error(f"❌ Exception fetching DevOps work items batch: {e}")
            return []

    def get_work_item(self, work_item_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single work item by ID across any project in the organization."""
        if not self.is_configured:
            return None
            
        try:
            url = f"https://dev.azure.com/{self.org}/_apis/wit/workitems/{work_item_id}?api-version=7.1"
            headers = self._get_auth_headers()
            headers["Accept"] = "application/json"
            
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return self.format_work_item(resp.json())
            elif resp.status_code == 404:
                return None
            else:
                logger.error(f"❌ DevOps work item {work_item_id} fetch failed: HTTP {resp.status_code} - {resp.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Exception fetching DevOps work item #{work_item_id}: {e}")
            return None
    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Org-Wide Work Item Fetch across all Projects - end

    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Azure DevOps Attachment Upload and Linking - start
    def upload_attachment(self, project: str, file_data: bytes, filename: str) -> Optional[Dict[str, Any]]:
        """
        Upload binary media/file to Azure DevOps attachments API.
        
        Args:
            project: DevOps Project name.
            file_data: Raw bytes of the file/media.
            filename: File name with extension (e.g., 'repro.mp4', 'error.png').
            
        Returns:
            Dict containing 'url', 'id', and 'filename' or None on failure.
        """
        if not self.is_configured or not file_data:
            return None
            
        target_project = project or self.project
        try:
            import urllib.parse
            clean_filename = urllib.parse.quote(filename)
            url = f"https://dev.azure.com/{self.org}/{target_project}/_apis/wit/attachments?fileName={clean_filename}&api-version=7.1"
            
            headers = self._get_auth_headers()
            headers["Content-Type"] = "application/octet-stream"
            headers["Accept"] = "application/json"
            
            resp = requests.post(url, headers=headers, data=file_data, timeout=60)
            if resp.status_code in [200, 201]:
                data = resp.json()
                logger.info(f"✅ Successfully uploaded attachment '{filename}' to Azure DevOps: {data.get('id')}")
                return {
                    'url': data.get('url'),
                    'id': data.get('id'),
                    'filename': filename
                }
            else:
                logger.error(f"❌ Failed to upload DevOps attachment '{filename}': HTTP {resp.status_code} - {resp.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Exception uploading DevOps attachment '{filename}': {e}")
            return None

    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Stateless project resolution & schema sanitization - start
    def create_work_item(
        self,
        work_item_type: str,
        fields: Dict[str, Any],
        project: Optional[str] = None,
        attachment_urls: List[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new Work Item in Azure DevOps using JSON Patch payload.
        
        Args:
            work_item_type: 'Bug', 'Task', 'User Story', 'Issue', etc.
            fields: Dictionary mapping field reference names to values.
            project: Optional target project name (defaults to configured project).
            attachment_urls: Optional list of Azure DevOps attachment URLs to link.
        """
        if not self.is_configured:
            logger.warning("Azure DevOps not configured (AZURE_DEVOPS_ORG or AZURE_DEVOPS_PROJECT missing)")
            return None
            
        target_project = (project or self.project).strip('/')
        try:
            clean_type = work_item_type.strip()
            url = f"https://dev.azure.com/{self.org}/{target_project}/_apis/wit/workitems/${clean_type}?api-version=7.1"
            
            # Sanitize fields by work item type: Bug-specific fields should not be sent for Task/Story/Issue
            is_bug = clean_type.lower() == 'bug'
            bug_only_fields = {
                'Microsoft.VSTS.TCM.ReproSteps',
                'Microsoft.VSTS.TCM.SystemInfo',
                'Microsoft.VSTS.Common.Severity'
            }

            patch_ops = []
            for field_name, val in fields.items():
                if val is not None and val != "":
                    if not is_bug and field_name in bug_only_fields:
                        continue
                    patch_ops.append({
                        "op": "add",
                        "path": f"/fields/{field_name}",
                        "value": val
                    })

            if attachment_urls:
                for att_url in attachment_urls:
                    if att_url:
                        patch_ops.append({
                            "op": "add",
                            "path": "/relations/-",
                            "value": {
                                "rel": "AttachedFile",
                                "url": att_url,
                                "attributes": {
                                    "comment": "Uploaded from Support Dashboard"
                                }
                            }
                        })
                    
            headers = self._get_auth_headers()
            headers["Content-Type"] = "application/json-patch+json"
            headers["Accept"] = "application/json"
            
            resp = requests.post(url, headers=headers, json=patch_ops, timeout=15)
            if resp.status_code in [200, 201]:
                created_item = resp.json()
                logger.info(f"✅ Successfully created Azure DevOps work item #{created_item.get('id')} in project '{target_project}'")
                return self.format_work_item(created_item)
            else:
                logger.error(f"❌ Failed to create DevOps work item in project '{target_project}': HTTP {resp.status_code} - {resp.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Exception creating DevOps work item in project '{target_project}': {e}")
            return None
    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Stateless project resolution & schema sanitization - end

    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Stateless project metadata and iteration retrieval - start
    def get_metadata(self, project: Optional[str] = None) -> Dict[str, Any]:
        """Fetch work item types, area paths, iteration paths, and project users for dropdown forms."""
        target_project = (project or self.project).strip('/')
        result = {
            'work_item_types': ['Bug', 'Task', 'User Story', 'Issue'],
            'area_paths': [],
            'iteration_paths': [],
            'users': [],
            'project': target_project
        }
        
        if not self.is_configured:
            return result
            
        headers = self._get_auth_headers()
        headers["Accept"] = "application/json"
        
        # 1. Work Item Types
        try:
            url = f"https://dev.azure.com/{self.org}/{target_project}/_apis/wit/workitemtypes?api-version=7.1"
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                types = resp.json().get('value', [])
                valid_types = [t.get('name') for t in types if not t.get('isDisabled')]
                if valid_types:
                    result['work_item_types'] = valid_types
        except Exception as e:
            logger.debug(f"Failed to fetch DevOps work item types for {target_project}: {e}")
            
        # 2. Area Paths
        try:
            url = f"https://dev.azure.com/{self.org}/{target_project}/_apis/wit/classificationnodes/areas?$depth=4&api-version=7.1"
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                areas = self._flatten_nodes(resp.json())
                if areas:
                    result['area_paths'] = areas
        except Exception as e:
            logger.debug(f"Failed to fetch DevOps area paths for {target_project}: {e}")
            
        # 3. Iteration Paths
        try:
            url = f"https://dev.azure.com/{self.org}/{target_project}/_apis/wit/classificationnodes/iterations?$depth=4&api-version=7.1"
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                iterations = self._flatten_nodes(resp.json())
                if iterations:
                    result['iteration_paths'] = iterations
        except Exception as e:
            logger.debug(f"Failed to fetch DevOps iteration paths for {target_project}: {e}")
            
        # 4. Organization Users (from Azure DevOps Graph API)
        result['users'] = self.get_organization_users()[:30]
            
        # 5. Active Sprint Detection (Suggestive Default)
        result['active_iteration'] = self.get_active_iteration(project=target_project)

        return result
    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Stateless project metadata and iteration retrieval - end

    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Dynamic Graph User Search & Caching - start
    def get_organization_users(self) -> List[Dict[str, Any]]:
        """Fetch and cache all organization users from Azure DevOps Graph API (10min TTL)."""
        if not self.is_configured:
            return []
            
        # Return from cache if fresh (10 minutes)
        if self._graph_users_cache and self._graph_users_cache_time and (datetime.now() - self._graph_users_cache_time < timedelta(minutes=10)):
            return self._graph_users_cache
            
        try:
            headers = self._get_auth_headers()
            headers["Accept"] = "application/json"
            url = f"https://vssps.dev.azure.com/{self.org}/_apis/graph/users?api-version=7.1-preview.1"
            
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                raw_users = resp.json().get('value', [])
                valid_users = []
                
                for u in raw_users:
                    display_name = (u.get('displayName') or '').strip()
                    email = (u.get('mailAddress') or u.get('principalName') or '').strip()
                    # Filter out system and build services
                    if not display_name or 'Build Service' in display_name or 'Project Collection' in display_name or 'Release Management' in display_name:
                        continue
                    if display_name.startswith('D201') or display_name.startswith('Test Build'):
                        continue
                    
                    valid_users.append({
                        'displayName': display_name,
                        'uniqueName': email,
                        'mailAddress': email,
                        'descriptor': u.get('descriptor', '')
                    })
                
                # Sort alphabetically by display name
                valid_users.sort(key=lambda x: x['displayName'].lower())
                self._graph_users_cache = valid_users
                self._graph_users_cache_time = datetime.now()
                logger.info(f"✅ Cached {len(valid_users)} Azure DevOps organization users")
                return valid_users
            else:
                logger.warning(f"⚠️ Failed to fetch Graph users: HTTP {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"❌ Exception fetching DevOps Graph users: {e}")
            
        return self._graph_users_cache or []

    def search_users(self, query: str = '', limit: int = 30) -> List[Dict[str, Any]]:
        """Filter cached organization users by search query (sub-millisecond typeahead)."""
        users = self.get_organization_users()
        if not query:
            return users[:limit]
            
        q = query.strip().lower()
        matched = []
        for u in users:
            d_name = u.get('displayName', '').lower()
            email = u.get('mailAddress', '').lower()
            if q in d_name or q in email:
                matched.append(u)
                if len(matched) >= limit:
                    break
                    
        return matched
    # Bilal Khan (31/08/2026) Issue No  Sheet_Name  - Dynamic Graph User Search & Caching - end

    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Multi-Project & Active Sprint Methods stateless fix - start
    def get_active_iteration(self, project: Optional[str] = None, team: Optional[str] = None) -> Optional[str]:
        """
        Return the full iteration path of the team's currently active sprint.
        Uses the Azure DevOps 'current' timeframe filter.
        """
        if not self.is_configured:
            return None
        
        target_project = (project or self.project).strip('/')
        team_name = team or target_project
        headers = self._get_auth_headers()
        headers["Accept"] = "application/json"
        
        endpoints = [
            f"https://dev.azure.com/{self.org}/{target_project}/{team_name}/_apis/work/teamsettings/iterations?$timeframe=current&api-version=7.1",
            f"https://dev.azure.com/{self.org}/{target_project}/_apis/work/teamsettings/iterations?$timeframe=current&api-version=7.1"
        ]
        
        for url in endpoints:
            try:
                resp = requests.get(url, headers=headers, timeout=6)
                if resp.status_code == 200:
                    val = resp.json().get('value', [])
                    if val and len(val) > 0:
                        return val[0].get('path') or val[0].get('name')
            except Exception as e:
                logger.debug(f"Could not fetch active iteration from {url}: {e}")
        return None

    def list_projects(self) -> List[Dict[str, str]]:
        """
        List all accessible projects in the Azure DevOps organization.
        Returns: [{'id': '...', 'name': '...'}]
        """
        if not self.org:
            return []
        try:
            url = f"https://dev.azure.com/{self.org}/_apis/projects?api-version=7.1&$top=100"
            headers = self._get_auth_headers()
            headers["Accept"] = "application/json"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return [
                    {'id': p['id'], 'name': p['name']}
                    for p in resp.json().get('value', [])
                    if p.get('state') == 'wellFormed'
                ]
            return []
        except Exception as e:
            logger.debug(f"Failed to list DevOps projects: {e}")
            return []

    def get_metadata_for_project(self, project: str) -> Dict[str, Any]:
        """
        Fetch metadata for a dynamic project statelessly without mutating self.project.
        """
        return self.get_metadata(project=project)
    # Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Multi-Project & Active Sprint Methods stateless fix - end
    # Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Multi-Project & Active Sprint Methods - end

    def _flatten_nodes(self, node: Dict, current_path: str = "") -> List[str]:
        """Recursively flatten classification node tree into list of full paths."""
        paths = []
        name = node.get('name', '')
        path = f"{current_path}\\{name}" if current_path else name
        if path:
            paths.append(path)
        for child in node.get('children', []):
            paths.extend(self._flatten_nodes(child, path))
        return paths

# Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Azure DevOps Connector Module - end
