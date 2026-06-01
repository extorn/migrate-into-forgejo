
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
            USE_EXISTING_TEAMS = config.getboolean(section, option="use_existing_teams", fallback=False),
            ADD_EMPTY_TEAMS = config.getboolean(section, option="add_empty_teams_to_organizations", fallback=False),
            ADD_EMPTY_TEAMS_TO_REPOS = config.getboolean(section, option="add_empty_teams_to_repos", fallback=False),
            IS_FUZZY_TEAMS_ALLOWED = config.getboolean(section, option="allow_fuzzy_teams", fallback=False),
            IS_FUZZY_USERS_ALLOWED = config.getboolean(section, option="allow_fuzzy_users", fallback=False),
            ALLOW_FUZZY_AUTH_DOWNGRADE = config.getboolean(section, option="allow_fuzzy_auth_downgrade", fallback=False),
            ALLOW_FUZZY_AUTH_UPGRADE = config.getboolean(section, option="allow_fuzzy_auth_upgrade", fallback=False),
        )

@dataclass(frozen=True)
class ForgejoConfig:
    USER_ROLES_FILE_PATH : str
    # This is the name that Forgejo assigns the initial Team for an organization with the role Owners
    FORGEJO_DEFAULT_OWNERS_TEAM_NAME="Owners"

    FORGEJO_CLIENT_AUTH_CERT : str | None
    FORGEJO_CLIENT_AUTH_KEY : str | None
    FORGEJO_URL : str
    FORGEJO_API_URL : str
    FORGEJO_API_TOKEN : str
    

    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="forgejo"):
        forgejo_website_url = config.get(section, option="forgejo_url")
        return cls(
            USER_ROLES_FILE_PATH = config.get(section, option="forgejo_user_roles_file_path", fallback="forgejo_user_roles.yaml"),
            FORGEJO_CLIENT_AUTH_CERT = config.get(section, option="forgejo_client_auth_cert", fallback=None),
            FORGEJO_CLIENT_AUTH_KEY = config.get(section, option="forgejo_client_auth_key", fallback=None),
            FORGEJO_URL = forgejo_website_url,
            FORGEJO_API_URL = f"{forgejo_website_url}/api/v1",
            FORGEJO_API_TOKEN = config.get(section, option="forgejo_token"),
        )



@dataclass(frozen=True)
class GitLabConfig:
    GITLAB_CLIENT_AUTH_CERT : str | None
    GITLAB_CLIENT_AUTH_KEY : str | None
    GITLAB_URL : str
    GITLAB_TOKEN : str | None
    GITLAB_ADMIN_USER : str | None
    GITLAB_ADMIN_PASS : str | None
    GITLAB_SYNC_CONNECTION_TYPE : str | None

    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="gitlab"):
        return cls(
            GITLAB_CLIENT_AUTH_CERT = config.get(section, option="gitlab_client_auth_cert", fallback=None),
            GITLAB_CLIENT_AUTH_KEY = config.get(section, option="gitlab_client_auth_key", fallback=None),
            GITLAB_URL = config.get(section, option="gitlab_url"),
            GITLAB_TOKEN = config.get(section, option="gitlab_token", fallback=None),
            GITLAB_ADMIN_USER = config.get(section, option="gitlab_admin_user", fallback=None),
            GITLAB_ADMIN_PASS = config.get(section, option="gitlab_admin_pass", fallback=None),
            GITLAB_SYNC_CONNECTION_TYPE = config.get(section, option="gitlab_sync_connection_type", fallback="https"),
            
        )



@dataclass(frozen=True)
class GitLabMigrationConfig:
    IGNORE_GITLAB_SYSTEM_USERS : bool
    IGNORED_GITLAB_SYSTEM_USERS : set[str]
    ACCESS_LEVELS_TO_FORGEJO_ROLES_MAP_FILE_PATH : str


    @classmethod
    def from_config(cls, config:configparser.RawConfigParser, section:str="migrate.gitlab"):
        default_ignored_users = GitLabMigrationConfig._build_default_ignored_users()
        users = cls._parse_user_list(config.get(section, "gitlab_system_users", fallback=None)
                                    ) or default_ignored_users
        return cls(
            IGNORE_GITLAB_SYSTEM_USERS = config.getboolean(section, "ignore_gitlab_system_users", fallback=False),
            IGNORED_GITLAB_SYSTEM_USERS = users,
            ACCESS_LEVELS_TO_FORGEJO_ROLES_MAP_FILE_PATH = config.get(section, option="access_levels_to_forgejo_role_map_file_path", fallback="gitlab_forgejo_roles_map.yaml"),
        )
    
    @staticmethod
    def _parse_user_list(value: str|None) -> set[str]|None:
        if value is None:
            return None
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