# TODO: Group-Based Account Creation

## Overview
Support creating OpenStack accounts where users are added to a Keystone group
for project access (organization use case), instead of direct user-to-project
role assignment.

## Key Differences from Current Flow
- Separate Google Sheet (different format than individual registrations)
- Group name = project name (same convention as deletion)
- Multiple users per project (group membership)
- Role assigned to group, not individual user
- Need: create_group, add_user_to_group, assign_project_role_to_group

## OpenStack SDK Methods
- `identity.create_group(name, domain_id)`
- `identity.add_user_to_group(user, group)`
- `identity.assign_project_role_to_group(project, group, role)`

## Deferred
This feature will be implemented after group deletion is complete.
