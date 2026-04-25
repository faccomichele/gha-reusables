#!/usr/bin/env python3
"""
Terraform Plan JSON to Markdown Converter

Reads tf-logs.json (streaming) and/or tf-show.json (show output) from the current
directory and converts to a human-readable Markdown format (tf-report.md) that
highlights changes, additions, and deletions.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def get_action_emoji(actions: List[str]) -> str:
    """Return emoji and description for resource actions."""
    if "create" in actions:
        return "➕ **CREATE**"
    elif "delete" in actions:
        return "🗑️ **DELETE**"
    elif "update" in actions:
        return "🔄 **UPDATE**"
    elif "replace" in actions or ("delete" in actions and "create" in actions):
        return "🔁 **REPLACE**"
    elif "read" in actions:
        return "📖 **READ**"
    else:
        return "⚪ **NO-OP**"


def format_value(value: Any, indent: int = 0) -> str:
    """Format a value for display in markdown."""
    prefix = "  " * indent
    
    if value is None:
        return ""
    elif isinstance(value, bool):
        return f"{prefix}`{str(value).lower()}`"
    elif isinstance(value, (int, float)):
        return f"{prefix}`{value}`"
    elif isinstance(value, str):
        if not value:  # Skip empty strings
            return ""
        if len(value) > 100:
            return f"{prefix}`{value[:97]}...`"
        return f"{prefix}`{value}`"
    elif isinstance(value, list):
        if not value:
            return ""
        if len(value) > 5:
            return f"{prefix}List with {len(value)} items"
        return f"{prefix}" + ", ".join([format_value(v, 0).strip() for v in value])
    elif isinstance(value, dict):
        if not value:
            return ""
        return f"{prefix}Object with {len(value)} properties"
    else:
        return f"{prefix}`{str(value)}`"


def format_change(before: Any, after: Any, key: str, indent: int = 0) -> List[str]:
    """Format a change between before and after values."""
    lines = []
    prefix = "  " * indent
    
    # Skip if both are None or empty
    if (before is None or (isinstance(before, (list, dict)) and not before)) and \
       (after is None or (isinstance(after, (list, dict)) and not after)):
        return lines
    
    # Skip if after is None or empty (deletion)
    if after is None or (isinstance(after, (list, dict)) and not after):
        return lines
    
    # Skip if before is None or empty but after has value (addition)
    if before is None or (isinstance(before, (list, dict)) and not before):
        after_str = format_value(after, 0).strip()
        if after_str:  # Only add if format_value returned non-empty
            lines.append(f"{prefix}- **{key}**: {after_str}")
    elif before != after:
        before_str = format_value(before, 0).strip()
        after_str = format_value(after, 0).strip()
        if before_str or after_str:  # Only add if at least one value is non-empty
            if before_str and after_str:
                lines.append(f"{prefix}- **{key}**: ~~{before_str}~~ → {after_str}")
            elif after_str:
                lines.append(f"{prefix}- **{key}**: {after_str}")
    
    return lines


def process_change(change: Dict[str, Any]) -> List[str]:
    """Process a single resource change into markdown lines."""
    lines = []
    
    before = change.get("before", {})
    after = change.get("after", {})
    before_sensitive = change.get("before_sensitive", {})
    after_sensitive = change.get("after_sensitive", {})
    
    # Get all keys from both before and after
    all_keys = set()
    if isinstance(before, dict):
        all_keys.update(before.keys())
    if isinstance(after, dict):
        all_keys.update(after.keys())
    
    # Sort keys for consistent output
    for key in sorted(all_keys):
        before_val = before.get(key) if isinstance(before, dict) else None
        after_val = after.get(key) if isinstance(after, dict) else None
        
        # Skip if values are the same
        if before_val == after_val:
            continue
        
        # Check if sensitive
        is_sensitive = (before_sensitive.get(key) if isinstance(before_sensitive, dict) else False) or \
                      (after_sensitive.get(key) if isinstance(after_sensitive, dict) else False)
        
        if is_sensitive:
            lines.append(f"  - **{key}**: `(sensitive value)`")
        else:
            lines.extend(format_change(before_val, after_val, key, 1))
    
    return lines


def extract_all_tag_changes(resource_changes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Extract all tag changes from tag-only update resources with occurrence count."""
    tag_changes = {}
    tags_only_resources = []
    
    # Collect all tag-only resources
    for rc in resource_changes:
        if rc.get("tags_only", False):
            tags_only_resources.append(rc)
    
    if not tags_only_resources:
        return {}
    
    total_resources = len(tags_only_resources)
    
    # Collect all tag changes from all resources
    for rc in tags_only_resources:
        change = rc.get("change", {})
        before = change.get("before", {})
        after = change.get("after", {})
        
        # Check both tags and tags_all (prefer tags)
        for tag_field in ["tags", "tags_all"]:
            before_tags = before.get(tag_field, {})
            after_tags = after.get(tag_field, {})
            
            if isinstance(before_tags, dict) and isinstance(after_tags, dict):
                # Find changed tags
                all_tag_keys = set(before_tags.keys()) | set(after_tags.keys())
                
                for tag_key in all_tag_keys:
                    before_val = before_tags.get(tag_key)
                    after_val = after_tags.get(tag_key)
                    
                    if before_val != after_val:
                        # Track this change with a unique key for the before/after pair
                        change_key = f"{before_val}→{after_val}"
                        
                        if tag_key not in tag_changes:
                            tag_changes[tag_key] = {}
                        
                        if change_key not in tag_changes[tag_key]:
                            tag_changes[tag_key][change_key] = {
                                "before": before_val,
                                "after": after_val,
                                "count": 0
                            }
                        
                        tag_changes[tag_key][change_key]["count"] += 1
                
                # Only process tags field to avoid duplication
                if tag_field == "tags" and before_tags:
                    break
    
    # Flatten the structure for easier display
    result = {}
    for tag_key, changes in tag_changes.items():
        # If all resources have the same change, mark as common
        if len(changes) == 1:
            change_info = list(changes.values())[0]
            if change_info["count"] == total_resources:
                result[tag_key] = {
                    "before": change_info["before"],
                    "after": change_info["after"],
                    "count": total_resources,
                    "is_common": True
                }
            else:
                result[tag_key] = {
                    "before": change_info["before"],
                    "after": change_info["after"],
                    "count": change_info["count"],
                    "is_common": False
                }
        else:
            # Multiple different changes for this tag
            result[tag_key] = {
                "changes": list(changes.values()),
                "is_common": False
            }
    
    return result


def convert_plan_to_markdown(plan_data: Dict[str, Any]) -> str:
    """Convert Terraform plan JSON to Markdown format."""
    md_lines = []
    
    # Header
    md_lines.append("# Terraform Summary")
    md_lines.append("")

    # Check for errors first
    errors = plan_data.get("errors", [])
    
    # Check if there are any resource changes
    resource_changes = plan_data.get("resource_changes", [])
    output_changes = plan_data.get("output_changes", {})
    outputs = plan_data.get("outputs", {})
    
    # If no errors, no resource changes, no output changes, and no outputs, return short message
    if not errors and not resource_changes and not output_changes and not outputs:
        md_lines.append("✅ No changes detected. Your infrastructure is already up to date.")
        return "\n".join(md_lines)
    
    # Display errors if any
    if errors:
        md_lines.append("## 🚨 Terraform Errors")
        md_lines.append("")
        for error_event in errors:
            # The diagnostic info can be at the top level or nested
            diagnostic = error_event.get("diagnostic", error_event)
            
            summary = diagnostic.get("summary", "No summary available.")
            address = diagnostic.get("address")
            snippet = diagnostic.get("snippet", {})

            if address:
                md_lines.append(f"### ❌ Error in `{address}`")
            else:
                md_lines.append("### ❌ General Error")
            
            md_lines.append("")
            md_lines.append(f"**Summary**: {summary}")
            md_lines.append("")
            
            if snippet and "code" in snippet:
                md_lines.append("```terraform")
                range_info = diagnostic.get("range", {})
                filename = range_info.get('filename', 'unknown')
                start_line = snippet.get('start_line', 'unknown')
                md_lines.append(f"# File: {filename}:{start_line}")
                md_lines.append(snippet["code"])
                md_lines.append("```")
            
            md_lines.append("---")
        md_lines.append("")
    
    # Format version info
    if "terraform_version" in plan_data:
        md_lines.append(f"**Terraform Version**: `{plan_data['terraform_version']}`")
        md_lines.append("")
    
    if not resource_changes and not outputs:
        md_lines.append("No resource changes detected.")
        return "\n".join(md_lines)
    
    # Count changes by action (only if there are resource changes)
    if resource_changes:
        action_counts = {
            "create": 0,
            "delete": 0,
            "update": 0,
            "update_tags_only": 0,
            "replace": 0,
            "read": 0,
            "no-op": 0
        }
        
        for rc in resource_changes:
            actions = rc.get("change", {}).get("actions", [])
            tags_only = rc.get("tags_only", False)
            
            if "create" in actions and "delete" in actions:
                action_counts["replace"] += 1
            elif "create" in actions:
                action_counts["create"] += 1
            elif "delete" in actions:
                action_counts["delete"] += 1
            elif "update" in actions:
                if tags_only:
                    action_counts["update_tags_only"] += 1
                else:
                    action_counts["update"] += 1
            elif "read" in actions:
                action_counts["read"] += 1
            else:
                action_counts["no-op"] += 1
        
        # Summary section
        md_lines.append("## Summary")
        md_lines.append("")
        md_lines.append(f"- ➕ **Create**: {action_counts['create']} resource(s)")
        md_lines.append(f"- 🔄 **Update**: {action_counts['update']} resource(s)")
        if action_counts['update_tags_only'] > 0:
            md_lines.append(f"- 🏷️ **Update (tags only)**: {action_counts['update_tags_only']} resource(s)")
        md_lines.append(f"- 🔁 **Replace**: {action_counts['replace']} resource(s)")
        md_lines.append(f"- 🗑️ **Delete**: {action_counts['delete']} resource(s)")
        if action_counts['read'] > 0:
            md_lines.append(f"- 📖 **Read**: {action_counts['read']} resource(s)")
        md_lines.append("")
        
        # Extract and display all tag changes for tag-only resources
        if action_counts['update_tags_only'] > 0:
            all_tag_changes = extract_all_tag_changes(resource_changes)
            
            if all_tag_changes:
                md_lines.append("### Tag Changes Summary")
                md_lines.append("")
                md_lines.append(f"Tags being updated across {action_counts['update_tags_only']} resource(s):")
                md_lines.append("")
                
                # Separate common and varied tags
                common_tags = {}
                varied_tags = {}
                
                for tag_key, change_info in sorted(all_tag_changes.items()):
                    if change_info.get("is_common", False):
                        common_tags[tag_key] = change_info
                    else:
                        varied_tags[tag_key] = change_info
                
                # Display common tags first
                if common_tags:
                    md_lines.append("#### Common to all resources:")
                    md_lines.append("")
                    for tag_key, change_info in sorted(common_tags.items()):
                        before_val = change_info.get("before")
                        after_val = change_info.get("after")
                        
                        if before_val is None and after_val is not None:
                            md_lines.append(f"- **{tag_key}**: (new) → `{after_val}`")
                        elif before_val is not None and after_val is None:
                            md_lines.append(f"- **{tag_key}**: `{before_val}` → (removed)")
                        else:
                            md_lines.append(f"- **{tag_key}**: `{before_val}` → `{after_val}`")
                    
                    md_lines.append("")
                
                # Display varied tags
                if varied_tags:
                    md_lines.append("#### Varies by resource:")
                    md_lines.append("")
                    for tag_key, change_info in sorted(varied_tags.items()):
                        if "changes" in change_info:
                            # Multiple different changes
                            md_lines.append(f"- **{tag_key}**: Multiple values")
                            for change in change_info["changes"]:
                                before_val = change.get("before")
                                after_val = change.get("after")
                                count = change.get("count")
                                if before_val is None and after_val is not None:
                                    md_lines.append(f"  - (new) → `{after_val}` ({count} resource(s))")
                                elif before_val is not None and after_val is None:
                                    md_lines.append(f"  - `{before_val}` → (removed) ({count} resource(s))")
                                else:
                                    md_lines.append(f"  - `{before_val}` → `{after_val}` ({count} resource(s))")
                        else:
                            # Single change but not on all resources
                            before_val = change_info.get("before")
                            after_val = change_info.get("after")
                            count = change_info.get("count")
                            
                            if before_val is None and after_val is not None:
                                md_lines.append(f"- **{tag_key}**: (new) → `{after_val}` ({count}/{action_counts['update_tags_only']} resource(s))")
                            elif before_val is not None and after_val is None:
                                md_lines.append(f"- **{tag_key}**: `{before_val}` → (removed) ({count}/{action_counts['update_tags_only']} resource(s))")
                            else:
                                md_lines.append(f"- **{tag_key}**: `{before_val}` → `{after_val}` ({count}/{action_counts['update_tags_only']} resource(s))")
                    
                    md_lines.append("")
        
        # Detailed changes section
        md_lines.append("## Detailed Changes")
        md_lines.append("")
        
        # Group by action type (skip update_tags_only since we show common changes instead)
        for action_type in ["create", "update", "replace", "delete"]:
            resources_for_action = []
            
            for rc in resource_changes:
                actions = rc.get("change", {}).get("actions", [])
                tags_only = rc.get("tags_only", False)
                
                if action_type == "replace" and "create" in actions and "delete" in actions:
                    resources_for_action.append(rc)
                elif action_type == "create" and "create" in actions and "delete" not in actions:
                    resources_for_action.append(rc)
                elif action_type == "delete" and "delete" in actions and "create" not in actions:
                    resources_for_action.append(rc)
                elif action_type == "update" and "update" in actions and not tags_only:
                    resources_for_action.append(rc)
            
            if resources_for_action:
                action_emoji = {
                    "create": "➕ Resources to Create",
                    "update": "🔄 Resources to Update",
                    "replace": "🔁 Resources to Replace",
                    "delete": "🗑️ Resources to Delete"
                }
                
                md_lines.append(f"### {action_emoji.get(action_type, action_type.title())}")
                md_lines.append("")
                
                for rc in resources_for_action:
                    resource_type = rc.get("type", "unknown")
                    resource_name = rc.get("name", "unknown")
                    address = rc.get("address", f"{resource_type}.{resource_name}")
                    
                    md_lines.append(f"#### `{address}`")
                    md_lines.append("")
                    md_lines.append(f"**Type**: `{resource_type}`")
                    md_lines.append("")
                    
                    # Add change details
                    change = rc.get("change", {})
                    actions = change.get("actions", [])
                    
                    if action_type == "replace":
                        # For replace, identify which attributes force replacement
                        action_reason = rc.get("action_reason") or change.get("action_reason")
                        
                        # Get replace paths from change object
                        replace_paths = change.get("replace_paths", [])
                        force_replace_attrs = set()
                        
                        # Extract attribute names from replace_paths
                        for path in replace_paths:
                            if isinstance(path, list) and len(path) > 0:
                                # Path is like ["name_prefix"] or ["tags", "Name"]
                                force_replace_attrs.add(path[0])
                        
                        # List attributes that force replacement
                        if force_replace_attrs:
                            md_lines.append("**Attributes forcing replacement**:")
                            md_lines.append("")
                            for attr in sorted(force_replace_attrs):
                                md_lines.append(f"  - `{attr}`")
                            md_lines.append("")
                        
                        # Show all configuration changes
                        after = change.get("after", {})
                        if after and isinstance(after, dict):
                            config_lines = []
                            for key in sorted(after.keys()):
                                value = after[key]
                                if change.get("after_sensitive", {}).get(key):
                                    config_lines.append(f"  - **{key}**: `(sensitive value)`")
                                else:
                                    formatted = format_value(value, 0).strip()
                                    if formatted:  # Only add if non-empty
                                        config_lines.append(f"  - **{key}**: {formatted}")
                            if config_lines:
                                md_lines.append("**Configuration**:")
                                md_lines.append("")
                                md_lines.extend(config_lines)
                                md_lines.append("")
                    
                    elif action_type == "create":
                        # For create, show the new configuration
                        after = change.get("after", {})
                        if after and isinstance(after, dict):
                            config_lines = []
                            for key in sorted(after.keys()):
                                value = after[key]
                                if change.get("after_sensitive", {}).get(key):
                                    config_lines.append(f"  - **{key}**: `(sensitive value)`")
                                else:
                                    formatted = format_value(value, 0).strip()
                                    if formatted:  # Only add if non-empty
                                        config_lines.append(f"  - **{key}**: {formatted}")
                            if config_lines:
                                md_lines.append("**Configuration**:")
                                md_lines.append("")
                                md_lines.extend(config_lines)
                                md_lines.append("")
                    
                    elif action_type == "update":
                        # For updates, show what's changing
                        change_lines = process_change(change)
                        if change_lines:
                            md_lines.append("**Changes**:")
                            md_lines.append("")
                            md_lines.extend(change_lines)
                            md_lines.append("")
                    
                    elif action_type == "delete":
                        # Add action reasons if available for deletions (check root level)
                        action_reason = rc.get("action_reason") or change.get("action_reason")
                        if action_reason:
                            # Format the action reason for better readability
                            reason_text = action_reason.replace("_", " ").title()
                            md_lines.append(f"**Reason**: {reason_text}")
                            md_lines.append("")
                        
                        # For delete, show what's being removed
                        before = change.get("before", {})
                        if before and isinstance(before, dict):
                            delete_lines = []
                            for key in sorted(before.keys())[:10]:  # Limit to first 10 attributes
                                value = before[key]
                                formatted = format_value(value, 0).strip()
                                if formatted:  # Only add if non-empty
                                    delete_lines.append(f"  - **{key}**: {formatted}")
                            if delete_lines:
                                md_lines.append("**Resource being deleted**:")
                                md_lines.append("")
                                md_lines.extend(delete_lines)
                                md_lines.append("")
                    
                    # Add action reasons if available for other action types
                    else:
                        action_reason = rc.get("action_reason") or change.get("action_reason")
                        if action_reason:
                            reason_text = action_reason.replace("_", " ").title()
                            md_lines.append(f"**Reason**: {reason_text}")
                            md_lines.append("")
                    
                    md_lines.append("---")
                    md_lines.append("")
    
    # Output changes section
    output_changes = plan_data.get("output_changes", {})
    if output_changes:
        md_lines.append("## Output Changes")
        md_lines.append("")
        
        for output_name, output_change in output_changes.items():
            actions = output_change.get("actions", [])
            action_emoji = get_action_emoji(actions)
            
            md_lines.append(f"### {action_emoji} `{output_name}`")
            md_lines.append("")
            
            if output_change.get("sensitive"):
                md_lines.append("Value: `(sensitive)`")
            else:
                before = output_change.get("before")
                after = output_change.get("after")
                
                if before is None and after is not None:
                    md_lines.append(f"Value: {format_value(after, 0).strip()}")
                elif before != after:
                    md_lines.append(f"Before: {format_value(before, 0).strip()}")
                    md_lines.append(f"After: {format_value(after, 0).strip()}")
            
            md_lines.append("")
    
    # Outputs section (from terraform apply)
    outputs = plan_data.get("outputs", {})
    if outputs:
        md_lines.append("## Outputs")
        md_lines.append("")
        md_lines.append("*Values from terraform apply:*")
        md_lines.append("")
        
        for output_name in sorted(outputs.keys()):
            output_info = outputs[output_name]
            value = output_info.get("value")
            output_type = output_info.get("type", "unknown")
            
            md_lines.append(f"### `{output_name}`")
            md_lines.append("")
            md_lines.append(f"**Type**: `{output_type}`")
            md_lines.append("")
            
            # Display value regardless of sensitive flag
            formatted_value = format_value(value, 0).strip()
            if formatted_value:
                md_lines.append(f"**Value**: {formatted_value}")
            else:
                md_lines.append(f"**Value**: `{value}`")
            
            md_lines.append("")
    
    # Footer
    md_lines.append("*Generated from Terraform plan JSON output*")
    
    return "\n".join(md_lines)


def has_non_tag_changes(change_data: Dict[str, Any]) -> bool:
    """
    Check if a resource change includes non-tag attribute changes.
    Returns True if there are changes beyond tags/tags_all.
    """
    # For terraform plan -json output, we need to check if there are
    # attribute changes beyond tags and tags_all
    # Since the streaming format doesn't include before/after details,
    # we'll mark this for special handling in the presentation
    return True  # Will be refined when we have access to change details


def parse_terraform_json_lines(file_path: Path, errors_only: bool = False) -> Dict[str, Any]:
    """Parse Terraform JSON Lines output and extract the plan data.
    
    Args:
        file_path: Path to the JSON lines file
        errors_only: If True, only extract errors from the file
    """
    plan_data = {
        "terraform_version": None,
        "resource_changes": [],
        "output_changes": {},
        "outputs": {},
        "errors": []
    }
    
    # Track resources to filter out refresh-only operations
    planned_changes = {}
    # Track which attributes are changing per resource
    resource_changes_attrs = {}
    
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                event = json.loads(line)
                event_type = event.get("type")
                
                # Extract errors
                if event.get("@level") == "error" and event_type == "diagnostic":
                    plan_data["errors"].append(event)
                
                # If errors_only mode, skip processing other events
                if errors_only:
                    continue

                # Extract Terraform version from version event
                if event_type == "version":
                    plan_data["terraform_version"] = event.get("terraform")
                
                # Extract outputs from terraform apply
                elif event_type == "outputs":
                    outputs_data = event.get("outputs", {})
                    for output_name, output_info in outputs_data.items():
                        if isinstance(output_info, dict):
                            plan_data["outputs"][output_name] = {
                                "value": output_info.get("value"),
                                "type": output_info.get("type"),
                                "sensitive": output_info.get("sensitive", False)
                            }
                
                # Track resource drift to identify what's changing
                elif event_type == "resource_drift":
                    change_data = event.get("change", {})
                    resource = change_data.get("resource", {})
                    addr = resource.get("addr", "")
                    
                    # Try to determine what attributes are changing
                    # This is difficult with streaming JSON, so we'll use a heuristic
                    if addr and addr not in resource_changes_attrs:
                        resource_changes_attrs[addr] = set()
                
                # Extract resource changes from planned_change events
                elif event_type == "planned_change":
                    change_data = event.get("change", {})
                    resource = change_data.get("resource", {})
                    action = change_data.get("action")
                    
                    # Skip if it's just a read operation (data source refresh)
                    if action == "read":
                        continue
                    
                    addr = resource.get("addr", "")
                    
                    # Check if we can determine what's changing from the action
                    # For updates, we'll need to infer tag-only changes
                    tags_only = False
                    if action == "update":
                        # We'll mark all updates as potentially tag-only for now
                        # In a real scenario, we'd need the full plan output with -out
                        tags_only = False  # Conservative default
                    
                    resource_change = {
                        "address": addr,
                        "module": resource.get("module", ""),
                        "type": resource.get("resource_type", ""),
                        "name": resource.get("resource_name", ""),
                        "change": {
                            "actions": [action] if isinstance(action, str) else action,
                            "before": {},
                            "after": {},
                            "after_unknown": {},
                            "before_sensitive": {},
                            "after_sensitive": {},
                            "action_reason": change_data.get("reason")
                        },
                        "tags_only": tags_only
                    }
                    
                    planned_changes[addr] = resource_change
                
            except json.JSONDecodeError:
                continue  # Skip malformed lines
    
    # Convert dict to list (only if not errors_only mode)
    if not errors_only:
        plan_data["resource_changes"] = list(planned_changes.values())
    
    return plan_data


def parse_terraform_show_json(file_path: Path) -> Dict[str, Any]:
    """Parse Terraform show -json output (full plan file)."""
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    # Ensure outputs key exists (tf-show.json doesn't have apply outputs)
    if "outputs" not in data:
        data["outputs"] = {}
    
    # Extract resource changes and mark tag-only updates
    resource_changes = data.get("resource_changes", [])
    
    for rc in resource_changes:
        change = rc.get("change", {})
        actions = change.get("actions", [])
        
        # Check if this is an update action
        if "update" in actions and "create" not in actions and "delete" not in actions:
            # Determine if only tags are changing
            before = change.get("before", {})
            after = change.get("after", {})
            after_unknown = change.get("after_unknown", {})
            
            if isinstance(before, dict) and isinstance(after, dict):
                changed_attrs = set()
                
                # Find all changed attributes
                all_keys = set(before.keys()) | set(after.keys())
                for key in all_keys:
                    # Skip computed/unknown values for this comparison
                    if after_unknown and after_unknown.get(key):
                        continue
                    
                    before_val = before.get(key)
                    after_val = after.get(key)
                    
                    if before_val != after_val:
                        changed_attrs.add(key)
                
                # Check if only tags or tags_all changed
                if changed_attrs and changed_attrs.issubset({"tags", "tags_all"}):
                    rc["tags_only"] = True
                else:
                    rc["tags_only"] = False
            else:
                rc["tags_only"] = False
        else:
            rc["tags_only"] = False
    
    return data


def main():
    """Main function to read JSON and write Markdown."""
    # Define static file names in the current directory
    tf_logs_file = Path("tf-logs.json")
    tf_show_file = Path("tf-show.json")
    output_file = Path("tf-report.md")
    
    try:
        plan_data = None
        
        # Check if tf-show.json exists
        if tf_show_file.exists():
            print(f"Reading {tf_show_file}...")
            print("Detected: terraform show -json format")
            plan_data = parse_terraform_show_json(tf_show_file)
            
            # Also parse errors from tf-logs.json if it exists
            if tf_logs_file.exists():
                print(f"Reading errors from {tf_logs_file}...")
                logs_data = parse_terraform_json_lines(tf_logs_file, errors_only=True)
                # Merge errors from logs into the plan_data
                if logs_data.get("errors"):
                    plan_data.setdefault("errors", []).extend(logs_data["errors"])
        
        elif tf_logs_file.exists():
            # Fall back to tf-logs.json if tf-show.json doesn't exist
            print(f"Reading {tf_logs_file}...")
            print("Detected: terraform plan -json format (streaming)")
            print("⚠️  Note: Streaming format cannot distinguish tag-only updates.")
            print("   For better analysis, use: terraform plan -out=tfplan && terraform show -json tfplan > tf-show.json")
            plan_data = parse_terraform_json_lines(tf_logs_file, errors_only=False)
        
        else:
            print(f"Error: Neither {tf_show_file} nor {tf_logs_file} found in current directory.")
            sys.exit(1)
        
        # Convert to Markdown
        print("Converting to Markdown...")
        markdown_content = convert_plan_to_markdown(plan_data)
        
        # Write output
        print(f"Writing {output_file}...")
        with open(output_file, 'w') as f:
            f.write(markdown_content)
        
        print(f"✅ Successfully created {output_file}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
