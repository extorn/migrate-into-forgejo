from abc import ABC, abstractmethod

from fg_migration.canonical_types import CanonicalOrganizations, CanonicalRepo, CanonicalRepoAccessors, CanonicalSystemUser
from fg_migration.forgjo import ForgejoRepositoryRole


class MigrationSource(ABC):
    @abstractmethod
    def getSourceSystemName(self) -> str:
        pass

    @abstractmethod
    def listRepos(self) -> list[CanonicalRepo]:
        pass

    @abstractmethod
    def list_organizations(self) -> CanonicalOrganizations:
        pass

    @abstractmethod
    def list_repository_accessors(self, repo:CanonicalRepo) -> CanonicalRepoAccessors:
        pass

    @abstractmethod
    def list_system_users(self) -> list[CanonicalSystemUser]:
        pass

    @abstractmethod
    def get_repository_role(self, source_access_level:str) -> ForgejoRepositoryRole:
        pass

    @abstractmethod
    def get_nearest_repository_role(self, source_access_level:str,
                                 allow_downgrade:bool,
                                 allow_upgrade:bool) -> ForgejoRepositoryRole | None:
        pass

    @abstractmethod
    def list_mapped_forgejo_repository_roles(self) -> set[ForgejoRepositoryRole]:
        pass