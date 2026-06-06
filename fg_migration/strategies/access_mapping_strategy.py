"""Contains the interface AccessMappingStrategy"""
from abc import ABC, abstractmethod

from pyforgejo import Team

from fg_migration.core.canonical_types import CanonicalOrganization, CanonicalRepo
from fg_migration.core.migration_source_type import MigrationSource


class AccessMappingStrategy(ABC):
    """An interface for managing the access mapping strategy for
       users to repositories, teams, organizations etc"""
    @abstractmethod
    def import_teams(self, migration_source:MigrationSource, organization: CanonicalOrganization):
        """Entry point from the migrator to import teams (no requirement to import teams)"""


    @abstractmethod
    def import_team_users_from_usernames(
        self,
        organization: CanonicalOrganization,
        usernames: set[str],
        dest_team: Team,
        team_members_cache: dict[int, set[str]],
        is_new_team: bool,
    ):
        """A common enough function of this strategy that an implementation is required for adding
           any users matching usernames into the team provided.
           Don't forget to update the team_members_cache"""

    @abstractmethod
    def import_repository_accessors(
        self,
        migration_source: MigrationSource,
        source_repo: CanonicalRepo,
    ):
        """Entry point from the migrator to import repository accessors"""

    @abstractmethod
    def resolve_forgejo_permission(
        self,
        migration_source:MigrationSource,
        source_access_level: str,
    ) -> str | None:
        """return the most appropriate Forgejo permission string
           for the given access level {read,write,admin}"""
