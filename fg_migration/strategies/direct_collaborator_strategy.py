"""contains the DirectCollaboratorOnlyStrategy class"""
from typing import override

from pyforgejo import Team

from fg_migration.adapters.forgjo import ForgejoDestination
from fg_migration.core.config_types import MigrationConfig
from fg_migration.strategies.access_mapping_strategy import AccessMappingStrategy
from fg_migration.core.canonical_types import (
    CanonicalOrganization,
    CanonicalRepo,
    CanonicalRepoMemberships,
)
from fg_migration.adapters.forgeo_types import IterativeFetchError
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.utils import fg_print


class DirectCollaboratorOnlyStrategy(AccessMappingStrategy):
    """
    Strategy that bypasses all access-level team modelling and assigns
    repository access directly to users.

    Important constraint:
    - The Forgejo owner team is still assumed to exist (mandatory platform requirement)
    - This strategy does NOT create or manage any additional teams beyond that
    - All non-owner access is handled via direct repository collaborators
    """

    migration_dest:ForgejoDestination
    migration_config:MigrationConfig

    def __init__(self, migration_dest:ForgejoDestination, migration_config:MigrationConfig):
        self.migration_dest = migration_dest
        self.migration_config = migration_config

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

    @override
    def import_team_users_from_usernames(
        self,
        organization: CanonicalOrganization,
        usernames: set[str],
        dest_team: Team,
        team_members_cache: dict[int, set[str]],
        is_new_team: bool,
    ):
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

    # ---------------------------------------------------------
    # Permission mapping
    # ---------------------------------------------------------
    @override
    def resolve_forgejo_permission(
        self,
        migration_source: MigrationSource,
        source_access_level: str,
    ) -> str | None:

        role_definition = migration_source.get_repository_role(source_access_level)
        if role_definition is None:
            return None

        permissions = role_definition.permission

        if "admin" in permissions:
            return "admin"
        if "write" in permissions:
            return "write"
        if "read" in permissions:
            return "read"

        return None
