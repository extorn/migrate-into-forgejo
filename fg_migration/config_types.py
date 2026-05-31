
import configparser
from dataclasses import dataclass


@dataclass(frozen=True)
class MigrationConfig:
    USE_EXISTING_TEAMS:bool
    ADD_EMPTY_TEAMS:bool
    ADD_EMPTY_TEAMS_TO_REPOS:bool
    IS_FUZZY_TEAMS_ALLOWED:bool
    IS_FUZZY_USERS_ALLOWED:bool
    ALLOW_FUZZY_AUTH_DOWNGRADE:bool
    ALLOW_FUZZY_AUTH_UPGRADE:bool

    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="migrate"):
        return cls(
            USE_EXISTING_TEAMS = config.getboolean(section, "use_existing_teams", fallback=False),
            ADD_EMPTY_TEAMS = config.getboolean(section, "add_empty_teams_to_organizations", fallback=False),
            ADD_EMPTY_TEAMS_TO_REPOS = config.getboolean(section, "add_empty_teams_to_repos", fallback=False),
            IS_FUZZY_TEAMS_ALLOWED = config.getboolean(section, "allow_fuzzy_teams", fallback=False),
            IS_FUZZY_USERS_ALLOWED = config.getboolean(section, "allow_fuzzy_users", fallback=False),
            ALLOW_FUZZY_AUTH_DOWNGRADE = config.getboolean(section, "allow_fuzzy_auth_downgrade", fallback=False),
            ALLOW_FUZZY_AUTH_UPGRADE = config.getboolean(section, "allow_fuzzy_auth_upgrade", fallback=False),
        )

@dataclass(frozen=True)
class ForgejoConfig:
    # This is the name that Forgejo assigns the initial Team for an organization with the role Owners
    FORGEJO_DEFAULT_OWNERS_TEAM_NAME="Owners"

    FORGEJO_CLIENT_AUTH_CERT : str | None
    FORGEJO_CLIENT_AUTH_KEY : str | None
    FORGEJO_URL : str
    FORGEJO_API_URL : str
    FORGEJO_API_TOKEN : str
    # Not used. The script uses a personal access token for authentication
    FORGEJO_USER : str | None
    FORGEJO_PASSWORD : str | None

    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="forgejo"):
        forgejo_website_url = config.get(section, "forgejo_url")
        return cls(
            FORGEJO_CLIENT_AUTH_CERT = config.get(section, "forgejo_client_auth_cert", fallback=None),
            FORGEJO_CLIENT_AUTH_KEY = config.get(section, "forgejo_client_auth_key", fallback=None),
            FORGEJO_URL = forgejo_website_url,
            FORGEJO_API_URL = f"{forgejo_website_url}/api/v1",
            FORGEJO_API_TOKEN = config.get(section, "forgejo_token"),
            # user and pass Not used. The script uses a personal access token for authentication
            FORGEJO_USER = config.get(section, "forgejo_admin_user", fallback=None), # TODO remove later
            FORGEJO_PASSWORD = config.get(section, "forgejo_admin_pass", fallback=None), # TODO remove later
        )

@dataclass(frozen=True)
class ForgejoMigrationConfig:
    
    ORG_TEAM_OWNERS_NAME : str
    ORG_TEAM_MAINTAINERS_NAME :str
    ORG_TEAM_DEVELOPERS_NAME : str
    ORG_TEAM_REPORTERS_NAME : str
    ORG_TEAM_GUESTS_NAME : str
    ORG_TEAM_OWNERS_DESCRIPTION : str
    ORG_TEAM_MAINTAINERS_DESCRIPTION : str
    ORG_TEAM_DEVELOPERS_DESCRIPTION : str
    ORG_TEAM_REPORTERS_DESCRIPTION : str
    ORG_TEAM_GUESTS_DESCRIPTION : str

    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="migrate.forgejo"):
        owners_team_name = ForgejoConfig.FORGEJO_DEFAULT_OWNERS_TEAM_NAME # MUST NOT change, hardcoded in Forgejo code. config.get("migrate", "org_team_owners_name", fallback="Owners")
        maintainers_team_name = config.get(section, "org_team_maintainers_name", fallback="Maintainers")
        developers_team_name = config.get(section, "org_team_developers_name", fallback="Developers")
        reporters_team_name = config.get(section, "org_team_reporters_name", fallback="Reporters")
        guests_team_name = config.get(section, "org_team_guests_name", fallback="Guests")
        return cls(
            ORG_TEAM_OWNERS_NAME = owners_team_name,
            ORG_TEAM_MAINTAINERS_NAME = maintainers_team_name,
            ORG_TEAM_DEVELOPERS_NAME = developers_team_name,
            ORG_TEAM_REPORTERS_NAME = reporters_team_name,
            ORG_TEAM_GUESTS_NAME = guests_team_name,
            # Default to the setting for the name if no description provided
            ORG_TEAM_OWNERS_DESCRIPTION = config.get(section, "org_team_owners_description", fallback=owners_team_name),
            ORG_TEAM_MAINTAINERS_DESCRIPTION = config.get(section, "org_team_maintainers_description", fallback=maintainers_team_name),
            ORG_TEAM_DEVELOPERS_DESCRIPTION = config.get(section, "org_team_developers_description", fallback=developers_team_name),
            ORG_TEAM_REPORTERS_DESCRIPTION = config.get(section, "org_team_reporters_description", fallback=reporters_team_name),
            ORG_TEAM_GUESTS_DESCRIPTION = config.get(section, "org_team_guests_description", fallback=guests_team_name),
        )

@dataclass(frozen=True)
class GitLabConfig:
    GITLAB_CLIENT_AUTH_CERT : str | None
    GITLAB_CLIENT_AUTH_KEY : str | None
    GITLAB_URL : str
    GITLAB_TOKEN : str | None
    GITLAB_ADMIN_USER : str | None
    GITLAB_ADMIN_PASS : str | None

    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="gitlab"):
        return cls(
            GITLAB_CLIENT_AUTH_CERT = config.get(section, "gitlab_client_auth_cert", fallback=None),
            GITLAB_CLIENT_AUTH_KEY = config.get(section, "gitlab_client_auth_key", fallback=None),
            GITLAB_URL = config.get(section, "gitlab_url"),
            GITLAB_TOKEN = config.get(section, "gitlab_token", fallback=None),
            GITLAB_ADMIN_USER = config.get(section, "gitlab_admin_user", fallback=None),
            GITLAB_ADMIN_PASS = config.get(section, "gitlab_admin_pass", fallback=None),
        )



@dataclass(frozen=True)
class GitLabMigrationConfig:
    IGNORE_GITLAB_SYSTEM_USERS : bool
    IGNORED_GITLAB_SYSTEM_USERS : set[str]


    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="migrate.gitlab"):
        default_ignored_users = GitLabMigrationConfig._build_default_ignored_users()
        raw_users = config.get(section,
                               "gitlab_system_users",
                               fallback=",".join(default_ignored_users))
        return cls(
            IGNORE_GITLAB_SYSTEM_USERS = config.getboolean(section, "ignore_gitlab_system_users", fallback=False),
            IGNORED_GITLAB_SYSTEM_USERS = cls._parse_user_list(raw_users)
        )
    
    @staticmethod
    def _parse_user_list(value: str) -> set[str]:
        return {
            user.strip()
            for user in value.split(",")
            if user.strip()
        }
        
    @staticmethod
    def _build_default_ignored_users() -> set[str]:
        usernames : set[str] = frozenset({
        "GitLab-Admin-Bot",
        "ghost",
        "support-bot",
        "alert-bot",
        "GitLabDuo",
        })
        return usernames