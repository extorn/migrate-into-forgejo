"""A factory for the source system"""

import configparser
from enum import StrEnum


from fg_migration.adapters.gitlab_types import GitLabApiBuilder
from fg_migration.core.config_types import GitLabConfig, GitLabMigrationConfig, MigrationConfig
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.adapters.source_gitlab import GitLabMigrationSource



class SourceFactory:
    """Build source system"""

    class SourceType(StrEnum):
        """List of valid source types and config value"""
        GITLAB = "gitlab"

    @staticmethod
    def build_gitlab_source(config: configparser.RawConfigParser,) -> MigrationSource:
        """Builder for a GitLab source"""
        gitlab_config = GitLabConfig.from_config(config=config)
        migration_config_gitlab = GitLabMigrationConfig.from_config(config=config)

        gl_api_builder = GitLabApiBuilder(gitlab_config)
        gl_api = gl_api_builder.build_gitlab_api_client()
        gl_conn_success = gl_api_builder.test_gitlab_connection(gl_api)
        if not gl_conn_success:
            raise ConnectionError("Unable to connect to GitLab")

        return GitLabMigrationSource(
                                    gitlab_api=gl_api,
                                    gitlab_config=gitlab_config,
                                    gitlab_migration_config=migration_config_gitlab)

    SOURCE_BUILDERS = {
        SourceType.GITLAB: build_gitlab_source,
    }

    @staticmethod
    def build_migration_source(
        config: configparser.RawConfigParser,
        migration_config: MigrationConfig
    ) -> MigrationSource:
        """
        Build an instance of MigrationSource

        Raises
        ------
            ConnectionError
                If a quick diagnostic test of the source system API fails
            ValueError
                If the source type is not recognised
        """

        try:
            source_type = SourceFactory.SourceType(migration_config.MIGRATION_SOURCE)
        except ValueError as exc:
            valid_types = ", ".join(t.value for t in SourceFactory.SourceType)
            dev_msg = ""
            err_msg = ""
            if "" != migration_config.MIGRATION_SOURCE:
                err_msg = f"Unsupported migration source '{migration_config.MIGRATION_SOURCE}'. "
                dev_msg = "\nPlease look at the README-Developers.md and " \
                          "consider implementing the desired source system"
            raise ValueError(
                f"{err_msg}Supported migration sources: {valid_types}{dev_msg}") from exc
        builder = SourceFactory.SOURCE_BUILDERS[source_type]
        return builder(config=config)
