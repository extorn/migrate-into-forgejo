"""contains the DirectCollaboratorOnlyStrategy class"""
from typing import override
from warnings import deprecated


from fg_migration.core.canonical_types import (
    CanonicalOrganization,
    CanonicalRepo,
    CanonicalRepoMemberships,
)
from fg_migration.adapters.forgeo_types import IterativeFetchError
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.strategies.base_access_mapping_strategy import BaseAccessMappingStrategy
from fg_migration.utils import fg_print


@deprecated("Not Implemented")
class FlattenedHierarchyStrategy(BaseAccessMappingStrategy):
    """
Access mapping strategy that derives Forgejo teams from source-system
group hierarchies rather than access levels.

This strategy treats repository memberships and their originating group
paths as the canonical representation of authorization. Nested groups are
flattened into Forgejo-compatible team names, allowing GitLab subgroup and
descendant-group structures to be represented without requiring Forgejo to
support nested teams.

Each unique hierarchy path is projected into a Forgejo team. Users are
assigned to the team corresponding to the group through which they gained
access, and repository permissions are granted through those teams whenever
possible.

Example
-------

Given the following GitLab group structure:

    engineering
    ├── backend
    │   └── api
    └── frontend

and repository memberships:

    alice   -> engineering/backend
    bob     -> engineering/backend
    charlie -> engineering/frontend
    dave    -> engineering/backend/api

the hierarchy is flattened into Forgejo teams:

    engineering-backend
    engineering-frontend
    engineering-backend-api

Resulting Forgejo organization structure:

    engineering-backend
    ├── alice
    └── bob

    engineering-frontend
    └── charlie

    engineering-backend-api
    └── dave

Repository access example
-------------------------

For repository "project-a":

    project-a
        ├── engineering-backend
        ├── engineering-frontend
        └── engineering-backend-api

Repository permissions are granted through the flattened hierarchy teams
rather than through individual collaborators whenever possible.

Hierarchy markers may optionally be preserved when generating team names.
For example:

    engineering/:s:/backend
        -> engineering-sub-backend

    engineering/:d:/platform/:d:/api
        -> engineering-desc-platform-desc-api

Behaviour:
- Creates Forgejo teams from source-system group hierarchies.
- Flattens nested group structures into Forgejo-compatible team names.
- Preserves hierarchy information in team naming.
- Imports organization membership through hierarchy-derived teams.
- Grants repository access through hierarchy-derived teams.
- Avoids creating direct collaborators when team-based access can represent
  the authorization model.
- Supports configurable naming conventions for flattened hierarchies.
- Suitable for GitLab migrations where subgroup and descendant-group
  structures should be preserved despite Forgejo lacking nested teams.
"""


    # ---------------------------------------------------------
    # Teams: explicitly disabled except for required owner team
    # ---------------------------------------------------------
    @override
    def import_teams(self, migration_source: MigrationSource, organization: CanonicalOrganization):
        fg_print.info(
            f"Skipping access-level team import for {organization.get_safe_username()} "
            f"(owner team assumed pre-existing / handled elsewhere)"
        )
        return



    # ---------------------------------------------------------
    # Repository access: direct users only
    # ---------------------------------------------------------
    @override
    def import_repository_accessors(
        self,
        migration_source: MigrationSource,
        source_repo: CanonicalRepo,
    ):
        repo_accessors: CanonicalRepoMemberships = (
            migration_source.list_repository_accessors(source_repo)
        )

        if not repo_accessors.members:
            fg_print.info(f"No accessors for {source_repo.name}")
            return

        fg_print.info(f"Importing direct collaborators for {source_repo.name}")

        forgejo_repo_owner = self.migration_dest.resolve_forgejo_repo_owner(source_repo)
        if not forgejo_repo_owner or not forgejo_repo_owner.username:
            fg_print.error(f"Cannot resolve owner for {source_repo.name}")
            return

        try:
            existing_iter = self.migration_dest.iter_forgejo_collaborators(
                owner_username=forgejo_repo_owner.username,
                repo=source_repo.get_safe_username(),
            )

            existing_collaborator_ids = {
                u.id for u in existing_iter if u.id is not None
            }

        except IterativeFetchError:
            fg_print.error(f"Failed to load collaborators for {source_repo.name}")
            return

        for membership in repo_accessors.members:
            if not membership.username:
                continue

            perm = self.resolve_forgejo_permission(
                migration_source,
                membership.access_level,
            )

            if perm is None:
                fg_print.warning(
                    f"No permission mapping for {membership.username} "
                    f"(level={membership.access_level})"
                )
                continue

            self.migration_dest.import_individual_user_collaborator(
                existing_collaborator_ids=existing_collaborator_ids,
                accessor=membership,
                source_repo=source_repo,
                forgejo_permissions=perm,
            )
