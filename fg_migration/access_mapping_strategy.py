from abc import ABC, abstractmethod

from pyforgejo import Team

from fg_migration.canonical_types import CanonicalOrganization, CanonicalRepo
from fg_migration.migration_source_type import MigrationSource


class AccessMappingStrategy(ABC):
    @abstractmethod
    def import_teams(self, migration_source:MigrationSource, organization: CanonicalOrganization):
        pass

    @abstractmethod
    def import_team_users_from_usernames(
        self,
        organization: CanonicalOrganization,
        usernames: set[str],
        dest_team: Team,
        team_members_cache: dict[int, set[str]],
        is_new_team: bool,
    ):
        pass

    @abstractmethod
    def import_repository_accessors(
        self,
        source_repo: CanonicalRepo,
        migration_source: MigrationSource,
    ):
        pass

    @abstractmethod
    def resolve_forgejo_permission(
        self,
        migration_source:MigrationSource,
        source_access_level: str,
    ) -> str | None:
        pass