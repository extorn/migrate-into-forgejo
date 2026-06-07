"""Only contains the BaseAccessMappingStrategy, an abstract
   incomplete implementation of AccessMappingStrategy"""
from typing import override

from pyforgejo import Team

from fg_migration.adapters.destination_forgjo import ForgejoDestination
from fg_migration.adapters.forgeo_types import (ForgejoPermission, ForgejoRepositoryRole,
                                                ForgejoRolePermissionDefinition)
from fg_migration.core.canonical_types import CanonicalOrganization
from fg_migration.core.config_types import MigrationConfig
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.strategies.access_mapping_strategy import AccessMappingStrategy
from fg_migration.utils import fg_print


class BaseAccessMappingStrategy(AccessMappingStrategy):
    """A class to contain all generally useful functions"""
    migration_dest:ForgejoDestination
    migration_config:MigrationConfig

    def __init__(self, migration_dest:ForgejoDestination, migration_config:MigrationConfig):
        self.migration_config = migration_config
        self.migration_dest = migration_dest



    @override
    def resolve_forgejo_permission(
        self,
        migration_source:MigrationSource,
        source_access_level: str,
    ) -> str | None:

        role_definition = self._get_forgejo_role_definition(
            migration_source=migration_source,
            source_access_level=source_access_level,
            fuzzy=self.migration_config.IS_FUZZY_USERS_ALLOWED,
        )

        if role_definition is None:
            return None

        permissions = role_definition.permission

        for role in ForgejoPermission:
            if role in permissions:
                return role

        return None



    def _get_forgejo_role_definition(self, migration_source:MigrationSource,
                                     source_access_level:str,
                                     fuzzy:bool) -> ForgejoRolePermissionDefinition | None:
        """Retrieves a ForgejoRoleDefinition, creating a new one and adding neccessary data to the
           maps as required"""
        # get forgejo team definition matching gitlab permission level
        repository_role : ForgejoRepositoryRole = migration_source.get_repository_role(
                                                        source_access_level=source_access_level)
        #fg_print.debug(f"Role for access_level {source_access_level} : {repository_role.id},"
        #               f" custom={repository_role.is_custom}")
        if repository_role.is_custom:
            nearest_repository_role = None
            if fuzzy:
                fg_print.warning(f"{migration_source.get_source_system_name()} Role:Forgejo Team"
                                 f" Mapping missing for {repository_role}. Using fuzzy matching")

                nearest_repository_role = migration_source.get_nearest_repository_role(
                                source_access_level=source_access_level,
                                allow_downgrade=self.migration_config.ALLOW_FUZZY_AUTH_DOWNGRADE,
                                allow_upgrade=self.migration_config.ALLOW_FUZZY_AUTH_UPGRADE)

            # if it still isn't valid.
            if nearest_repository_role is None:
                return None
            # we now have a valid nearest_repository_role, lets create a mapping based on
            # the team referenced by that for our invalid one.
            self.migration_dest.add_role_mapping(map_from_role=repository_role,
                                                 to_existing_role=nearest_repository_role)

        # now we definitely have a team mapped against this role, even if it is just a basic string
        return self.migration_dest.role_definitions[repository_role]



    @override
    def import_team_users_from_usernames(
            self,
            organization: CanonicalOrganization,
            usernames: set[str],
            dest_team: Team,
            team_members_cache: dict[int, set[str]], # map[Team.id -> {member.username}]
            is_new_team: bool,
        ):
        self.migration_dest.import_team_users_from_usernames(
                organization=organization,
                usernames=usernames,
                dest_team=dest_team,
                team_members_cache=team_members_cache,
                is_new_team=is_new_team,
            )
