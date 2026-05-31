from abc import ABC, abstractmethod
from typing import List

from fg_migration.canonical_types import CanonicalOrganizations, CanonicalRepo, CanonicalRepoAccessors, CanonicalRepositoryRole, CanonicalSystemUser


class MigrationSource(ABC):
    @abstractmethod
    def getSourceSystemName(self) -> str:
        pass

    @abstractmethod
    def listRepos(self) -> List[CanonicalRepo]:
        pass

    @abstractmethod
    def list_organizations(self) -> CanonicalOrganizations:
        pass

    @abstractmethod
    def list_repository_accessors(self, repo:CanonicalRepo) -> CanonicalRepoAccessors:
        pass

    @abstractmethod
    def list_system_users(self) -> List[CanonicalSystemUser]:
        pass

    @abstractmethod
    def get_repository_role(self, source_access_level:str) -> CanonicalRepositoryRole | str:
        pass

    @abstractmethod
    def get_nearest_repository_role(self, source_access_level:str,
                                 allow_downgrade:bool,
                                 allow_upgrade:bool) -> CanonicalRepositoryRole | None:
        pass