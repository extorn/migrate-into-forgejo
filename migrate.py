#!/usr/bin/env python3
#
# imports projects, users, groups, issues, labels, milestones, keys
# and collaborators from Gitlab to Forgejo
#
"""
Usage: migrate.py [--users] [--groups] [--projects] [--all] [--notify]
       migrate.py --help

Migration script to import projects, users, groups, from Gitlab to Forgejo.

Options
  -h, --help  Show this screen
  --users     migrate users
  --groups    migrate groups
  --projects  migrate projects
  --all       migrate all
  --notify    send notification to users
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import os
import re
import random
import string
import configparser
from typing import Dict
from typing import List
import typing
from pyforgejo.core import RequestOptions
from typing_extensions import deprecated

from docopt import docopt
import requests
import dateutil.parser
from httpx import Client as HttpxClient

import gitlab  # pip install python-gitlab
import gitlab.v4.objects
import pyforgejo  # pip install pyforgejo (https://github.com/h44z/pyforgejo)

# Forgejo API imports:
from pyforgejo import ConflictError, CreateTeamOptionPermission, GpgKey, Issue, Label, Milestone, NotFoundError, Organization, PublicKey, PyforgejoApi, Repository, Team, TeamPermission, User
from pyforgejo.core.api_error import ApiError

from fg_migration import fg_print

SCRIPT_VERSION = "0.5"

# This is the name that Forgejo assigns the initial Team for an organization with the role Owners
FORGEJO_DEFAULT_OWNERS_TEAM_NAME="Owners"

#######################
# CONFIG SECTION START
#######################
if not os.path.exists(".migrate.ini"):
    fg_print.info("Please create .migrate.ini as explained in the README!")
    os.sys.exit()


GITLAB_ACCESS_LEVEL_OWNER = 50
GITLAB_ACCESS_LEVEL_MAINTAINER = 40
GITLAB_ACCESS_LEVEL_DEVELOPER = 30
GITLAB_ACCESS_LEVEL_REPORTER = 20
GITLAB_ACCESS_LEVEL_GUEST = 10

DEFAULT_IGNORED_USERS = {
    "GitLab-Admin-Bot",
    "ghost",
    "support-bot",
    "alert-bot",
    "GitLabDuo",
}

config = configparser.RawConfigParser()
config.read(".migrate.ini")
ADD_EMPTY_TEAMS = config.getboolean("migrate", "add_empty_teams_to_organizations", fallback=False)
ADD_EMPTY_TEAMS_AS_COLLABORATORS = config.getboolean("migrate", "add_empty_teams_to_projects", fallback=False)
ORG_TEAM_NAME_OWNERS = FORGEJO_DEFAULT_OWNERS_TEAM_NAME # MUST NOT change, hardcoded in Forgejo code. config.get("migrate", "org_team_name_owners", fallback="Owners")
ORG_TEAM_NAME_MAINTAINERS = config.get("migrate", "org_team_name_maintainers", fallback="Maintainers")
ORG_TEAM_NAME_DEVELOPERS = config.get("migrate", "org_team_name_developers", fallback="Developers")
ORG_TEAM_NAME_REPORTERS = config.get("migrate", "org_team_name_reporters", fallback="Reporters")
ORG_TEAM_NAME_GUESTS = config.get("migrate", "org_team_name_guests", fallback="Guests")
ORG_TEAM_NAME_OWNERS_DESCRIPTION = config.get("migrate", "org_team_name_owners_description", fallback=ORG_TEAM_NAME_OWNERS)
ORG_TEAM_NAME_MAINTAINERS_DESCRIPTION = config.get("migrate", "org_team_name_maintainers_description", fallback=ORG_TEAM_NAME_MAINTAINERS)
ORG_TEAM_NAME_DEVELOPERS_DESCRIPTION = config.get("migrate", "org_team_name_developers_description", fallback=ORG_TEAM_NAME_DEVELOPERS)
ORG_TEAM_NAME_REPORTERS_DESCRIPTION = config.get("migrate", "org_team_name_reporters_description", fallback=ORG_TEAM_NAME_REPORTERS)
ORG_TEAM_NAME_GUESTS_DESCRIPTION = config.get("migrate", "org_team_name_guests_description", fallback=ORG_TEAM_NAME_GUESTS)

IGNORE_GITLAB_SYSTEM_USERS = config.getboolean("migrate", "ignore_gitlab_system_users", fallback=False)
raw_users = config.get("migrate","gitlab_system_users",fallback=",".join(DEFAULT_IGNORED_USERS),
)
IGNORED_GITLAB_SYSTEM_USERS: set[str] = {user.strip()
                                         for user in raw_users.split(",")
                                         if user.strip()
                                        }
IS_FUZZY_TEAMS_ALLOWED = config.getboolean("migrate", "allow_fuzzy_teams", fallback=False)
IS_FUZZY_USERS_ALLOWED = config.getboolean("migrate", "allow_fuzzy_users", fallback=False)
ALLOW_FUZZY_AUTH_DOWNGRADE = config.getboolean("migrate", "allow_fuzzy_auth_downgrade", fallback=False)
ALLOW_FUZZY_AUTH_UPGRADE = config.getboolean("migrate", "allow_fuzzy_auth_upgrade", fallback=False)

GITLAB_CLIENT_AUTH_CERT = config.get("migrate", "gitlab_client_auth_cert", fallback=None)
GITLAB_CLIENT_AUTH_KEY = config.get("migrate", "gitlab_client_auth_key", fallback=None)
GITLAB_URL = config.get("migrate", "gitlab_url")
GITLAB_TOKEN = config.get("migrate", "gitlab_token", fallback=None)
GITLAB_ADMIN_USER = config.get("migrate", "gitlab_admin_user", fallback=None)
GITLAB_ADMIN_PASS = config.get("migrate", "gitlab_admin_pass", fallback=None)
FORGEJO_CLIENT_AUTH_CERT = config.get("migrate", "forgejo_client_auth_cert", fallback=None)
FORGEJO_CLIENT_AUTH_KEY = config.get("migrate", "forgejo_client_auth_key", fallback=None)
FORGEJO_URL = config.get("migrate", "forgejo_url")
FORGEJO_API_URL = f"{FORGEJO_URL}/api/v1"
FORGEJO_TOKEN = config.get("migrate", "forgejo_token")
# Not used. The script uses a personal access token for authentication
#FORGEJO_USER = config.get("migrate", "forgejo_admin_user")
#FORGEJO_PASSWORD = config.get("migrate", "forgejo_admin_pass")
#######################
# CONFIG SECTION END
#######################
@dataclass
class ForgejoTeamDefinition:
    name: str
    description: str
    permissions: ForgejoTeamPermissionDefinition

    @staticmethod
    def fromTeam(team:Team) -> ForgejoTeamDefinition:
        return ForgejoTeamDefinition(name=team.name,
                              description=team.description,
                              permissions=ForgejoTeamPermissionDefinition(
                                  can_create_org_repo=team.can_create_org_repo,
                                  includes_all_repositories=team.includes_all_repositories,
                                  permission=team.permission,
                                  units_map=team.units_map
                              ))
    
    def diff(self, other:ForgejoTeamDefinition) -> str :
        return diff_dataclasses(self,other)

@dataclass
class ForgejoTeamPermissionDefinition:
    can_create_org_repo:bool = False
    includes_all_repositories:bool = False
    permission:CreateTeamOptionPermission = ""
    units_map: dict[str,str] = field(default_factory=dict) # use of field here ensures new instance for every instance of the class

    def diff(self, other:ForgejoTeamPermissionDefinition) -> str :
        return diff_dataclasses(self,other)

########################################
# forgejo team permissions configuration
########################################
permissions_team_owners=ForgejoTeamPermissionDefinition(
    permission="admin", # Not supported
    units_map= { "repo.actions": "write", "repo.code": "write", "repo.ext_issues": "read", "repo.ext_wiki": "admin", "repo.issues": "write", "repo.packages": "write", "repo.projects": "write", "repo.pulls": "owner", "repo.releases": "write", "repo.wiki": "admin" }
)
permissions_team_maintainers=ForgejoTeamPermissionDefinition(
    permission="admin",
    units_map= { "repo.actions": "write", "repo.code": "write", "repo.ext_issues": "read", "repo.ext_wiki": "admin", "repo.issues": "write", "repo.packages": "write", "repo.projects": "write", "repo.pulls": "owner", "repo.releases": "write", "repo.wiki": "admin" }
)
permissions_team_developers=ForgejoTeamPermissionDefinition(
    permission="write",
    units_map= { "repo.actions": "read", "repo.code": "write", "repo.ext_issues": "read", "repo.ext_wiki": "read", "repo.issues": "write", "repo.packages": "write", "repo.projects": "read", "repo.pulls": "owner", "repo.releases": "write", "repo.wiki": "write" }
)
permissions_team_reporters=ForgejoTeamPermissionDefinition(
    permission="read",
    units_map= { "repo.actions": "none", "repo.code": "read", "repo.ext_issues": "read", "repo.ext_wiki": "read", "repo.issues": "write", "repo.packages": "none", "repo.projects": "none", "repo.pulls": "none", "repo.releases": "none", "repo.wiki": "none" }
)
permissions_team_guests=ForgejoTeamPermissionDefinition(
    permission="read",
    units_map= { "repo.actions": "none", "repo.code": "read", "repo.ext_issues": "none", "repo.ext_wiki": "none", "repo.issues": "read", "repo.packages": "read", "repo.projects": "read", "repo.pulls": "read", "repo.releases": "read", "repo.wiki": "read" }
)



def main():
    """Main function"""
    _args = docopt(__doc__)
    args = {k.replace("--", ""): v for k, v in _args.items()}

    fg_print.print_color(
        fg_print.Bcolors.HEADER, "---=== Gitlab to Forgejo migration ===---"
    )
    fg_print.info(f"Version: {SCRIPT_VERSION}\n")
    

    session = requests.Session()
    # add client authentication if cert and key are provided in the config
    if(GITLAB_CLIENT_AUTH_CERT != None and GITLAB_CLIENT_AUTH_KEY != None):
        cert_path = GITLAB_CLIENT_AUTH_CERT
        key_path = GITLAB_CLIENT_AUTH_KEY
        session.cert = (cert_path, key_path)
    # private token or personal token authentication
    gl = gitlab.Gitlab(url = GITLAB_URL, private_token=GITLAB_TOKEN, session=session)
    try:
        gl.auth()
    except gitlab.GitlabAuthenticationError:
        fg_print.error("Failed to authenticate with Gitlab! Check access token and client authentication settings in .migrate.ini")
        os.sys.exit()
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to connect to Gitlab! {detail}")
        os.sys.exit()
    assert isinstance(gl.user, gitlab.v4.objects.CurrentUser)
    fg_print.info(f"Connected to Gitlab, version: {gl.version()[0]}")

    fg = _build_forgejo_api_client(FORGEJO_TOKEN)
    try:
        response = fg.miscellaneous.get_version()
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to connect to Forgejo! {detail}")
        os.sys.exit()
    fg_ver = response.version
    
    fg_print.info(f"Connected to Forgejo, version: {fg_ver}")

    # IMPORT USERS
    if args["users"] or args["all"]:
        import_users(gl, fg)
    # IMPORT GROUPS
    if args["groups"] or args["all"]:
         # Note, import_groups uses the gitlab projects object because they're intrinsically linked really.
        import_groups(gl, fg)
    # IMPORT PROJECTS
    if args["projects"] or args["all"]:
        import_projects(gl, fg)
    # IMPORT NOTHING ?
    if (
        not args["users"]
        and not args["groups"]
        and not args["projects"]
        and not args["all"]
    ):
        fg_print.info()
        fg_print.warning("No migration option(s) selected, nothing to do!")
        os.sys.exit()

    fg_print.info("")
    if fg_print.GLOBAL_ERROR_COUNT == 0:
        fg_print.success("Migration finished with no errors!")
    else:
        fg_print.error(f"Migration finished with {fg_print.GLOBAL_ERROR_COUNT} errors!")
        fg_print.info("Failed elements:")
        print(*fg_print.GLOBAL_ERROR_LIST, sep="\n")



#
# Data loading helpers for Forgejo
#



def diff_dataclasses(before, after) -> dict:
    before_dict = asdict(before)
    after_dict = asdict(after)

    diff = {}

    for key in before_dict.keys() | after_dict.keys():
        if before_dict.get(key) != after_dict.get(key):
            diff[key] = {
                "before": before_dict.get(key),
                "after": after_dict.get(key),
            }

    return diff



def _get_gitlab_access_level_role_map() -> dict[int,str]:
    """Maps gitlab access level to gitlab roles"""
    gitlab_access_level_to_role: dict[int, str] = {
        GITLAB_ACCESS_LEVEL_OWNER: "Owner",
        GITLAB_ACCESS_LEVEL_MAINTAINER: "Maintainer",
        GITLAB_ACCESS_LEVEL_DEVELOPER: "Developer",
        GITLAB_ACCESS_LEVEL_REPORTER: "Reporter",
        GITLAB_ACCESS_LEVEL_GUEST: "Guest",
    }
    return gitlab_access_level_to_role



def _get_gitlab_role_to_forgejo_team_map() -> dict[str,ForgejoTeamDefinition]:
    """Maps gitlab roles to forgejo team names"""
    gitlab_role_to_forgejo_team: dict[str, str] = {
        "Owner": ForgejoTeamDefinition(name=ORG_TEAM_NAME_OWNERS,description=ORG_TEAM_NAME_OWNERS_DESCRIPTION,permissions=permissions_team_owners),
        "Maintainer": ForgejoTeamDefinition(name=ORG_TEAM_NAME_MAINTAINERS,description=ORG_TEAM_NAME_MAINTAINERS_DESCRIPTION,permissions=permissions_team_maintainers),
        "Developer": ForgejoTeamDefinition(name=ORG_TEAM_NAME_DEVELOPERS,description=ORG_TEAM_NAME_DEVELOPERS_DESCRIPTION,permissions=permissions_team_developers),
        "Reporter": ForgejoTeamDefinition(name=ORG_TEAM_NAME_REPORTERS,description=ORG_TEAM_NAME_REPORTERS_DESCRIPTION,permissions=permissions_team_reporters),
        "Guest": ForgejoTeamDefinition(name=ORG_TEAM_NAME_GUESTS,description=ORG_TEAM_NAME_GUESTS_DESCRIPTION,permissions=permissions_team_guests),
    }
    return gitlab_role_to_forgejo_team



def _get_exception_detail(e: Exception) -> str:
    if isinstance(e, ApiError):
        body = getattr(e, "body", None)
        detail = body.get("message") if isinstance(body, dict) else str(body)
        if("token does not have at least one of required scope" in detail):
            fg_print.error(f"Trapped Error {detail}")
            fg_print.error(f"ERROR: Access Token used MUST have read+write permission on everything (permission:all) and be admin. Please create a new one and update the .migrate.ini file.")
            os.sys.exit(1)
    else:
        detail = str(e)
    return detail



def name_clean(name):
    """Cleans a name for usage in Forgejo"""
    new_name = name.replace(" ", "_")
    new_name = re.sub(r"[^a-zA-Z0-9_\.-]", "-", new_name)

    if new_name.lower() == "plugins":
        return f"{new_name}-user"

    return new_name



def _build_httpx_client(timeout: typing.Optional[float]=60, follow_redirects: typing.Optional[bool] = True) -> HttpxClient:
    client = None
    if(FORGEJO_CLIENT_AUTH_CERT != None and FORGEJO_CLIENT_AUTH_KEY != None):
        cert_path = FORGEJO_CLIENT_AUTH_CERT
        key_path = FORGEJO_CLIENT_AUTH_KEY
        cert = (cert_path, key_path)
        client = HttpxClient(cert=cert, timeout=timeout,follow_redirects=follow_redirects)
    return client



def _build_forgejo_api_client(forgejo_api_key: str) -> pyforgejo.PyforgejoApi:
    return PyforgejoApi(base_url=FORGEJO_API_URL, api_key=forgejo_api_key, httpx_client = _build_httpx_client())



def _get_forgejo_labels(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str) -> List[Label]:
    """get labels for a repository"""
    
    try:
        existing_labels = fg_api.issue.list_labels(owner, repo)
        return existing_labels
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load existing labels for project {repo}! {detail}")
        return []



def _get_forgejo_milestones(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str) -> List[Milestone]:
    """get milestones for a repository"""

    try:
        existing_milestones : List[Milestone] = fg_api.issue.get_milestones_list(owner, repo)
        return existing_milestones
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load existing milestones for project {repo}! {detail}")
        return []



def _get_forgejo_issues(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str) -> List[Issue]:
    """get issues for a repository"""

    try:
        existing_issues = fg_api.issue.list_issues(owner, repo)
        return existing_issues
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load existing issues for project {repo}! {detail}")
        return []



def _get_forgejo_teams(fg_api: pyforgejo.PyforgejoApi, orgname: str) -> List[Team]:
    """get teams for an organization"""

    try:
        existing_teams = fg_api.organization.org_list_teams(orgname)
        return existing_teams
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load existing teams for organization {orgname}! {detail}")
        return []
    


def _get_forgejo_team_members(fg_api: pyforgejo.PyforgejoApi, team: Team) -> List[User]:
    """get members for a team"""

    try:
        members = fg_api.organization.org_list_team_members(id=team.id)
        return members
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load team members for team {team.name} {detail}")
        return []



def _get_forgejo_collaborators(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str) -> List[User]:
    """get collaborators for a repository"""

    try:
        collaborators = fg_api.repository.repo_list_collaborators(owner, repo)
        return collaborators
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load collaborators for repo {repo} {detail}")
        return []



def _get_forgejo_user_keys(fg_api: pyforgejo.PyforgejoApi, username : str) -> List[PublicKey] :
    """get public keys for a user"""

    try:
        keys = fg_api.user.list_keys(username)
        return keys
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load public keys for user {username}! {detail}")
    return []

def _get_forgejo_user_gpg_keys(fg_api: pyforgejo.PyforgejoApi, username : str) -> List[GpgKey] :
    """get gpg keys for a user"""

    try:
        keys = fg_api.user.user_list_gpg_keys(username)
        return keys
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load gpg keys for user {username}! {detail}")
        
    return []



#TODO references to gitlab in comments inside forgejo function.
def _get_forgejo_organization(fg_api: pyforgejo.PyforgejoApi, projectName: str, org_name: str) -> User:
    
    try:
        #fg_print.info(f"Trying to load forgejo organization {possible_org} for gitlab project {project.name}...")
        org = fg_api.organization.org_get(org_name)
        fg_print.info(f"loaded organization {org.full_name} for gitlab project {projectName}!")
        return org
    except Exception as e:
        if isinstance(e, NotFoundError):
            fg_print.error(f"Failed to load forgejo organization {org_name} for gitlab project {projectName}! {e.body['message']}")
        else:
            fg_print.error(f"Failed to load forgejo organization {org_name} for gitlab project {projectName}! {e}")    
            
    return None



#TODO references to gitlab in comments inside forgejo function.
def _get_forgejo_user(fg_api: pyforgejo.PyforgejoApi, projectName: str, username: str) -> User:
    """get user by name"""
    try:
        user = fg_api.user.get(username)
        fg_print.info(f"loaded user {user.username} for gitlab project {projectName}!")
        return user
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to load user {username} for gitlab project {projectName}! {detail}")
    return None



def _forgejo_user_exists(fg_api: pyforgejo.PyforgejoApi, username: str) -> bool:
    """check if a user exists"""
    try:
        user = fg_api.user.get(username)
        fg_print.warning(f"User {username} already exists in Forgejo, skipping!")
        return True
    except NotFoundError:
        return False
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.info(f"User {username} not found in Forgejo, importing! {detail}")
        return False



def _forgejo_organization_exists(fg_api: pyforgejo.PyforgejoApi, orgname: str) -> bool:
    """check if an organization exists"""
    try:
        org = fg_api.organization.org_get(orgname)
        fg_print.warning(f"Organization {orgname} already exists in Forgejo, skipping!")
        return True
    except NotFoundError:
        return False
    except Exception as e:
        fg_print.info(f"Organization {orgname} not found in Forgejo, importing!")
        return False



def _forgejo_team_member_exists(fg_api: pyforgejo.PyforgejoApi, username: str, team: Team) -> bool:
    """check if a member exists in a team"""
    existing_members = _get_forgejo_team_members(fg_api=fg_api, team=team)
    if existing_members:
        
        existing_member = next(
            (item for item in existing_members if item.username == username), None
        )

        if existing_member:
            fg_print.warning(
                f"Member {username} is already in team {team.name}, skipping!"
            )
            return True

        fg_print.info(f"Member {username} is not in team {team.name}, importing!")
        return False

    fg_print.info(f"No members in team {team.name}, importing!")
    return False



def _forgejo_collaborator_exists(fg_api: pyforgejo.PyforgejoApi, _owner: str, repo: str, username: str) -> bool:
    """check if a collaborator exists in a repository"""
    try:
        collaborators : List[User] = fg_api.repository.repo_list_collaborators(_owner, repo)
        existing = next(
            (c for c in collaborators if c.username == username),
            None,
        )
        if existing:
            fg_print.warning(
                f"Collaborator {username} already exists in Forgejo, skipping!"
            )
            return True
        else:
            fg_print.info(f"Collaborator {username} not found in Forgejo, importing!")
            return False
    except NotFoundError:
        return False
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(f"Failed to list collaborators for project {repo} for owner {_owner} {detail}!")
        return False



def _forgejo_repo_exists(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str) -> bool:
    """check if a repository exists"""
    try:
        fg_print.info(f"Checking if project {repo} exists in Forgejo for owner {owner}...")
        repository = fg_api.repository.repo_get(owner=owner, repo=repo)
        if repository is not None:
            fg_print.warning(f"Project {repo} already exists in Forgejo, skipping!")
            return True
    except Exception as e:
        if isinstance(e, NotFoundError):
            fg_print.info(f"Project {repo} not found in Forgejo, importing!")
            return False
        else:
            detail = _get_exception_detail(e)
            fg_print.error(f"Failed to check if project {repo} exists in Forgejo for owner {owner}! {detail}")

    
    fg_print.info(f"Project {repo} not found in Forgejo, importing!")
    return False



def _forgejo_label_exists(
    fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str, labelname: str
) -> bool:
    """check if a label exists in a repository"""
    #issues = fg_api.issue.list_issues(owner, repo)
    existing_labels = fg_api.issue.list_labels(owner, repo)
    if existing_labels:
        existing_label = next(
            (item for item in existing_labels if item.name == labelname), None
        )

        if existing_label is not None:
            fg_print.warning(
                f"Label {labelname} already exists in project {repo}, skipping!"
            )
            return True

        fg_print.info(f"Label {labelname} does not exist in project {repo}, importing!")
        return False

    fg_print.info(f"No labels in project {repo}, importing!")
    return False



def _forgejo_issue_exists(existing_issues : List[Issue], repo: str, issue_title: str) -> bool:
    """check if an issue exists in a repository"""
    
    if existing_issues:
        existing_issue = next(
            (item for item in existing_issues if item.title == issue_title), None
        )

        if existing_issue is not None:
            fg_print.warning(
                f"Issue {issue_title} already exists in project {repo}, skipping!"
            )
            return True

        fg_print.info(f"Issue {issue_title} does not exist in project {repo}, importing!")
        return False

    fg_print.info(f"No issues in project {repo}, importing!")
    return False



def _find_forgejo_milestone_id_by_title(forgejo_milestones: List[Milestone], title: str) -> int:
    """get milestone id by title"""
    # get the forgejo milestone with matching title
    # the issue, if it exists, otherwise return None
    
    forgejo_milestone : Milestone = next(
        (
            item
            for item in forgejo_milestones
            if item.title == title
        ),
        None,
    )
    if forgejo_milestone:
        return forgejo_milestone.id
    return None



def _find_forgejo_milestone_by_title(
    existing_milestones : List[Milestone], title: str
) -> bool:
    """check if a milestone exists in a repository"""
    
    if existing_milestones:
        existing_milestone = next(
            (item for item in existing_milestones if item.title == title), None
        )

        return existing_milestone
    
    return None



def _forgejo_delete_collaborator(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str, collaborator_id:int, collaborator_name: str) -> bool:
    """delete a collaborator from a repository"""
    try:
        fg_api.repository.repo_delete_collaborator(owner = owner, 
                                                   repo = repo, 
                                                   collaborator = collaborator_name)
        fg_print.info(f"Collaborator {collaborator_name} deleted!")
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
                f"Collaborator {collaborator_name} delete failed: {detail}",
                f"Collaborator {collaborator_name} delete from {repo}, skipping!: {detail}"
            )
        return False
    return True



def _forgejo_add_replace_collaborator(fg_api: pyforgejo.PyforgejoApi,
                                      existing_collaborator_ids:set[int], 
                                      collaborator_name:str,
                                      collaborator_id:int,
                                      owner:str, repo:str, permissions:str):
    """Add collaboration entry for repo. Will replace any existing one matching the name provided"""
    # If there is an existing collaboration record, delete it.
    if collaborator_id in existing_collaborator_ids:
        deleted = _forgejo_delete_collaborator(fg_api=fg_api, owner=owner, repo=repo, 
                                                collaborator_id=collaborator_id,
                                                collaborator_name=collaborator_name)
        if not deleted:
            return False
    # Add new collaboration record for user
    added = _forgejo_add_collaborator(fg_api=fg_api, owner=owner, repo=repo, 
                                      collaborator_id=collaborator_id,
                                      collaborator_name=collaborator_name,
                                      permission=permissions)
    if not added:
        pass
    return added



def _forgejo_add_collaborator(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str, collaborator_id:str, collaborator_name: str, permission: str) -> bool:
    """add a collaborator to a repository"""
    try:
        fg_api.repository.repo_add_collaborator(owner = owner, 
                                                repo = repo, 
                                                collaborator = collaborator_name, 
                                                permission = permission)
        fg_print.info(f"Collaborator {collaborator_name} imported!")
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
                f"Collaborator id={collaborator_id} name={collaborator_name} import failed: {detail}",
                f"Collaborator id={collaborator_id} name={collaborator_name} import failed {repo}, skipping!: {detail}"
            )
        return False
    # return true even if the collaborator already exists in the repository, because the existence of the collaborator in the repository is not a failure for the import of the project, we just skip it and continue with the import of the other collaborators
    return True



#TODO gitlab username references in forgejo function
def _forgejo_add_user(fg_api: pyforgejo.PyforgejoApi, gitlab_username: str, username: str, full_name: str, email: str, notify: bool) -> bool:
    """add a user to Forgejo, return True if user created or already exists"""

    if not _forgejo_user_exists(fg_api=fg_api, username=username): # need this because status 422 returned for conflict, not 409 
        rnd_str = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
        tmp_password = f"Tmp1!{rnd_str}"
        try:
            fg_api.admin.create_user(
                email=email,
                full_name=full_name,
                login_name=username,
                password=tmp_password,
                send_notify=notify,
                source_id=0,  # local user
                username=username,
            )
            fg_print.info(f"User {gitlab_username} imported as {username}, temporary password: {tmp_password}")
            return True
        except ConflictError:
            return True # already exists
        except Exception as e:
            detail = _get_exception_detail(e)
            fg_print.error(f"Adding User {gitlab_username} as {username} failed: {detail}",
                            f"failed to import user {gitlab_username} as {username} in Forgejo: {detail}",
            )
            return False
    return True


def _forgejo_list_team_in_repository(fg_api: pyforgejo.PyforgejoApi,
                                    owner:str,
                                    repo_name:str) -> List[Team]:
    """List all teams in a repository"""
    try:
        return fg_api.repository.repo_list_teams(owner=owner,repo=repo_name)
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"Listing teams in Repository {repo_name} Failed: {detail}"
        )
        return []


def _forgejo_add_team_to_repository(fg_api: pyforgejo.PyforgejoApi,
                                    owner_name:str,
                                    repo_name:str,
                                    team_name:str):
    """Add a team to a repository"""
    try:
        fg_api.repository.repo_add_team(owner=owner_name,repo=repo_name,team=team_name)
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"Adding team {team_name} to Repository {repo_name} Failed: {detail}",
            f"Adding team {team_name} to Repository {repo_name} Failed: {detail}",
        )
        return None



def _forgejo_add_user_key(fg_api: pyforgejo.PyforgejoApi, username : str, key_name : str, key_content : str) -> PublicKey :
    """Add a public key to the user"""
    try:
        # fg_print.info(f"Importing public key {key_name} for user {username}...")
        new_key = fg_api.admin.create_public_key(
            username=username,
            key=key_content,
            read_only=True,
            title=key_name,
        )
        fg_print.info(f"Public key {key_name} imported for user {username}!")
        return new_key
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"Public key {key_name} import failed: {detail}",
            f"failed to import Public key '{key_name}' for user {username}",
        )
        return None



def _build_forgejo_sudo_request_options(username:str) -> RequestOptions :
    headers : Dict = { "Sudo" : username }
    request_options : RequestOptions = RequestOptions(additional_headers=headers)
    return request_options



def _forgejo_add_gpg_key(fg_api: pyforgejo.PyforgejoApi, username : str, key_id : str, key_content : str) -> GpgKey :
    """Add a GPG key to the user"""
    
    try:
        new_key = fg_api.user.user_current_post_gpg_key (
            armored_public_key=key_content,
            request_options=_build_forgejo_sudo_request_options(username)
        )
        fg_print.info(f"GPG key {key_id} imported for user {username}!")
        return new_key
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"GPG key {key_id} import failed: {e}",
            f"failed to import GPG key '{key_id}' for user {username} {detail}",
        )
        return None



@deprecated("This cannot be used to create api tokens when the API was authorised using an access token")
def _forgejo_delete_temp_api_token_for_user(fg_api: pyforgejo.PyforgejoApi, username:str, token_name:str):
    """Delete an Access Token for the user (if using sudo)"""
    try:
        fg_api.user.delete_access_token(username=username, token=token_name)
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"Delete temporary user api token {token_name} of user {username} failed: {detail}",
        )



@deprecated("This cannot be used to create api tokens when the API was authorised using an access token")
def _forgejo_add_temp_api_token_for_user(fg_api: pyforgejo.PyforgejoApi, username:str, token_name:str, desired_scopes:Dict[str] = None) -> str:
    """Create an Access Token for the user (if using sudo)"""
    #Example desired_scopes=["read:user","write:user"]
    # A full list is here: https://forgejo.org/docs/latest/user/token-scope/
    try:
        fg_print.info(f"Creating access token for user {username} {token_name} with scope {desired_scopes}")
        user_api_token = fg_api.user.create_token(username=username, name=token_name, scopes=desired_scopes)
    except Exception as e:
        fg_print.warning(f"Creating access token for user {username} {token_name} with scope {desired_scopes} failed...")
        detail = _get_exception_detail(e)
        try:
            fg_api.user.delete_access_token(username=username, token=token_name)
            user_api_token = fg_api.user.create_token(username=username, name=token_name, scopes=desired_scopes)
        except Exception as e:
            detail = _get_exception_detail(e)
            fg_print.error(f"Error creating temporary API token {token_name} for user {username} {detail}")
            return None
    return user_api_token



def _forgejo_add_organization(fg_api: pyforgejo.PyforgejoApi, orgname: str, full_name: str, description: str) -> bool:
    """add a group as organization in Forgejo"""
    if not _forgejo_organization_exists(fg_api=fg_api, orgname=orgname): # need this because status 422 returned for conflict, not 409 
        try:
            fg_api.organization.org_create(
                description=description,
                full_name=full_name,
                location="",
                username=orgname,
                website="",
            )
            fg_print.info(f"Organization {orgname} imported!")
        except ConflictError:
            return True # already exists
        except Exception as e:
            detail = _get_exception_detail(e)
            fg_print.error(
                f"Adding organization {orgname} import failed: {e} {detail}",
                f"failed to import organization {orgname} in Forgejo: {detail}",
            )
            return False
    # return true even if the organization already exists, because the existence of the organization is not a failure for the import of the group, we just skip it and continue with the import of the group members and projects
    return True



def _forgejo_add_organization_team(fg_api: pyforgejo.PyforgejoApi, org_name: str, definition : ForgejoTeamDefinition) -> Team | None:
    """Add a team to an organization"""
    try:
        team = fg_api.organization.org_create_team(org=org_name,
                                            name=definition.name,
                                            can_create_org_repo=definition.permissions.can_create_org_repo, 
                                            description=definition.description,
                                            includes_all_repositories=definition.permissions.includes_all_repositories,
                                            permission=definition.permissions.permission,
                                            units=list(definition.permissions.units_map.keys()),
                                            units_map=definition.permissions.units_map
                                            )
        fg_print.info(f"Added team {definition.name} to organization {org_name}")
        return team
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"Adding team {definition.name} to organization {org_name} import failed: {detail}",
            f"Failed to add team {definition.name} to organization {org_name} in Forgejo: {detail}",
        )
        return None


def _forgejo_add_user_to_organization_team(fg_api: pyforgejo.PyforgejoApi, username: str, organization_name: str, team: Team) -> bool:
    """add a user to a team for a group"""
    if not _forgejo_team_member_exists(fg_api=fg_api, username=username, team=team):
        try:
            fg_api.organization.org_add_team_member(team.id, username)
            fg_print.info(f"User {username} added to team {team.name} of organization {organization_name}!")
        except Exception as e:
            detail = _get_exception_detail(e)
            fg_print.error(
                f"Adding user {username} to team {team.name} of organization {organization_name} import failed: {detail}",
                f"Failed to add member {username} to team {team.name} for organization {organization_name} in Forgejo: {detail}",
            )
            return False
    # return true even if the member already exists in the team, because the existence of the member in the team is not a failure for the import of the group, we just skip it and continue with the import of the other members
    return True



def _forgejo_add_milestone(fg_api: pyforgejo.PyforgejoApi, owner: str, repo: str, forgejo_milestones:List[Milestone], title: str, description: str, due_date: str, state: str) -> bool:
    """add a milestone to a repository"""
    forgejo_milestone : Milestone = _find_forgejo_milestone_by_title(forgejo_milestones, title)

    # if the milestone doesn't exist in the list
    if forgejo_milestone == None:
        if due_date:
            due_date = dateutil.parser.parse(due_date).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        try:
            forgejo_milestones.append(
                fg_api.issue.create_milestone(owner, repo, title=title, description=description, due_on=due_date, state=state)
            )
        except Exception as e:
            detail = _get_exception_detail(e)
            fg_print.error(
                f"Milestone {title} import failed: {detail}",
                f"Failed to import milestone {title} for project {repo} in Forgejo {detail}",
            )
            return False
    return True
        


def _forge_update_organization_team(fg_api: pyforgejo.PyforgejoApi, team:Team, definition:ForgejoTeamDefinition) -> Team | None :
    """Rename a Forgejo Team (e.g. Owners)"""
    try:
        updated = fg_api.organization.org_edit_team(id=team.id,
                                                    name=definition.name,
                                                    can_create_org_repo=definition.permissions.can_create_org_repo, 
                                                    description=definition.description,
                                                    includes_all_repositories=definition.permissions.includes_all_repositories,
                                                    permission=definition.permissions.permission,
                                                    units=list(definition.permissions.units_map.keys()),
                                                    units_map=definition.permissions.units_map
                                                    )
        changes = ForgejoTeamDefinition.fromTeam(team).diff(definition)
        fg_print.info(f"Updated Forgejo team {team.name} changes: {changes}")
        return updated
    except Exception as e:
        detail = _get_exception_detail(e)
        fg_print.error(
            f"Update Forgejo {team.name} to {definition} failed: {detail}",
            f"Failed to update team {team.name} in Forgejo {detail}",
        )
        return None



#
# Gitlab helper functions
#



def _get_forgejo_owner_for_gitlab_project(fg_api: pyforgejo.PyforgejoApi, project: gitlab.v4.objects.Project) -> User | Organization | None:
    
    user_or_org_name = name_clean(_get_gitlab_project_owner_slug(project))
    namespace_kind = project.namespace.get("kind")
    if namespace_kind == "user":
        if user := _get_forgejo_user(fg_api=fg_api, projectName=project.name, username=user_or_org_name):
            return user
        else:
            fg_print.error(f"Failed to load project owner for project {project.name}, skipping import!")
    elif namespace_kind == "group":
        if org := _get_forgejo_organization(fg_api=fg_api, projectName= project.name, org_name = user_or_org_name):
            return org
        else:
            fg_print.error(f"Failed to load project organization for project {project.name}, skipping import!")
    else:
        fg_print.error(f"Unsupported namespace kind {project.namespace['kind']} for project {project.name}, skipping import!")
    
    return None



def _get_forgejo_owner_username_for_gitlab_project(fg_api: pyforgejo.PyforgejoApi, project: gitlab.v4.objects.Project) -> str:
    username: str = None
    user_or_org_name = name_clean(_get_gitlab_project_owner_slug(project))
    if project.namespace["kind"] == "user":
        if user := _get_forgejo_user(fg_api=fg_api, projectName=project.name, username=user_or_org_name):
            username = user.username
        else:
            fg_print.error(f"Failed to load project owner for project {project.name}, skipping import!")
    elif project.namespace["kind"] == "group":
        if org := _get_forgejo_organization(fg_api=fg_api, projectName= project.name, org_name = user_or_org_name):
            username = org.username
        else:
            fg_print.error(f"Failed to load project organization for project {project.name}, skipping import!")
    else:
        fg_print.error(f"Unsupported namespace kind {project.namespace['kind']} for project {project.name}, skipping import!")
    
    return username



def _get_gitlab_project_owner_slug(project: gitlab.v4.objects.Project) -> str:
    if project.namespace["kind"] == "user":
        return project.namespace["path"]
    elif project.namespace["kind"] == "group":
        return project.namespace["name"]
    else:
        fg_print.error(f"Unsupported namespace kind {project.namespace['kind']} for project {project.name}, skipping import!")
        return None



def _build_or_extract_email(user: gitlab.v4.objects.User) -> str:
    """build an email address for a user, if the email is not available, we use a dummy email address based on the username"""
    
    # Some gitlab instances do not publish user emails, so we use a dummy email
    
    try:
        emails : list[gitlab.v4.objects.UserEmail] = user.emails.list(get_all=True)
    except AttributeError:
        emails = []
    
    if emails and len(emails) > 0:
        tmp_email = emails[0].email
    else:
        tmp_email = f"{user.username}@noemail-git.local"
    try:
        tmp_email = user.email
    except AttributeError:
        pass
    return tmp_email



#
# Import functions
#



def _import_project_labels(
    fg_api: pyforgejo.PyforgejoApi,
    labels: List[gitlab.v4.objects.ProjectLabel],
    project_owner: str,
    project_name: str,
):
    forgejo_safe_project_owner_name = name_clean(project_owner)
    forgejo_safe_project_name = name_clean(project_name)
    """import labels for a repository"""
    for label in labels:
        if not _forgejo_label_exists(fg_api=fg_api, owner=forgejo_safe_project_owner_name, repo=forgejo_safe_project_name, labelname=label.name):  # need this because status 422 returned for conflict, not 409 
            try:
                fg_api.issue.create_label(owner=forgejo_safe_project_owner_name, repo=forgejo_safe_project_name, name=label.name, color=label.color, description=label.description)
                fg_print.info(f"Label {label.name} imported!")
            except ConflictError:
                continue # already exists :-)
            except Exception as e:
                detail = _get_exception_detail(e)
                fg_print.error(
                    f"Label {label.name} import failed: {detail}",
                    f"Failed to import label {label.name} for project {forgejo_safe_project_name} in Forgejo: {detail}",
                )
                continue



def _import_project_milestones(
    fg_api: pyforgejo.PyforgejoApi,
    milestones: List[gitlab.v4.objects.ProjectMilestone],
    project_owner: str,
    project_name: str,
):
    """import milestones for a repository from a gitlab project"""
    forgejo_safe_project_name = name_clean(project_name)
    forgejo_safe_project_owner_name = name_clean(project_owner)
    forgejo_milestones = _get_forgejo_milestones(fg_api=fg_api, owner=forgejo_safe_project_owner_name, repo=forgejo_safe_project_name)
    for milestone in milestones:
        # Note: _forgejo_add_milestone appends to the cached list of forgejo_milestones too for efficiency.
        success = _forgejo_add_milestone(fg_api=fg_api, owner=forgejo_safe_project_owner_name, repo=forgejo_safe_project_name, 
                                         forgejo_milestones=forgejo_milestones, title=milestone.title, 
                                         description=milestone.description, due_date=milestone.due_date, 
                                         state=milestone.state)
        if not success:
            continue



def _import_project_issues(
    fg_api: pyforgejo.PyforgejoApi,
    issues: List[gitlab.v4.objects.ProjectIssue],
    project_owner: str,
    project_name: str,
):
    """Import issues for a repo from a gitlab project"""
    forgejo_safe_project_owner = name_clean(project_owner)
    forgejo_safe_project_name = name_clean(project_name)

    # reload all existing milestones and labels, needed for assignment in issues
    forgejo_milestones = _get_forgejo_milestones(fg_api=fg_api, owner=forgejo_safe_project_owner, repo=forgejo_safe_project_name)
    forgejo_labels = _get_forgejo_labels(fg_api=fg_api, owner=forgejo_safe_project_owner, repo=forgejo_safe_project_name)
    # get a list of all existing forgejo issues
    forgejo_issues = _get_forgejo_issues(fg_api=fg_api, owner=forgejo_safe_project_owner, repo=forgejo_safe_project_name)
    
    for issue in issues:
        if not _forgejo_issue_exists(forgejo_issues, repo=forgejo_safe_project_name, issue_title=issue.title):
            due_date = ""
            if issue.due_date is not None:
                due_date = dateutil.parser.parse(issue.due_date).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )

            # extract assignee, mapping to forgejo safe username
            assignee = None
            if issue.assignee is not None:
                assignee = name_clean(issue.assignee["username"])

            # extract list of assignees, mapping to forgejo safe username
            assignees : List[str] = []
            for tmp_assignee in issue.assignees:
                assignees.append(name_clean(tmp_assignee["username"]))

            # Get milestone id for the issue, if milestone is assigned to the issue in Gitlab.
            # # We need to get the milestone id for the milestone title from Forgejo, because the 
            # milestone id in Gitlab is not the same as the milestone id in Forgejo, and we need 
            # the milestone id for the assignment of the milestone to the issue in Forgejo. 
            # If there is no milestone with the same title in Forgejo, we do not assign a milestone 
            # to the issue in Forgejo, because there is no equivalent milestone in Forgejo.
            forgejo_milestoneId = None
            missing_milestone = False
            if issue.milestone is not None:
                forgejo_milestoneId = _find_forgejo_milestone_id_by_title(forgejo_milestones, issue.milestone["title"]) # N.b. gitlab issue so dict
                if forgejo_milestoneId is None:
                    # if this happens, something went wrong with the milestone import, because the milestone assigned 
                    # to the issue in Gitlab should have been imported to Forgejo in the milestone import step before 
                    # the issue import step, so we print an error and skip the milestone assignment for this issue, 
                    # but we continue with the import of the issue without the milestone assignment, because the 
                    # existence of the milestone is not a failure for the import of the issue, we just skip the 
                    # milestone assignment for this issue and continue with the import of the issue without the 
                    # milestone assignment.
                    fg_print.error(
                        f"Milestone {issue.milestone['title']} assigned to issue {issue.title} does not exist in Forgejo, skipping milestone assignment for this issue!",
                        f"Failed to import issue {issue.title} for project {forgejo_safe_project_name} in Forgejo",
                    )
                    missing_milestone = True
            if missing_milestone:
                continue # stop the import of this issue (to allow milestone import to be fixed and re-run not to create duplicate issues)


            missing_label = False
            forgejo_issue_label_ids : List[int] = []
            for label in issue.labels:
                existing_label : Label = None
                existing_label = next(
                    (item for item in forgejo_labels if item.name == label), None
                )
                if existing_label:
                    forgejo_issue_label_ids.append(existing_label.id)
                else:
                    fg_print.error(
                        f"Label {label} assigned to issue {issue.title} does not exist in Forgejo, skipping label assignment for this issue!",
                        f"Failed to import issue {issue.title} for project {repo} in Forgejo",
                    )
                    missing_label = True
                    break
            if missing_label:
                continue # stop the import of this issue (to allow milestone import to be fixed and re-run not to create duplicate issues)
                
            try:
                fg_api.issue.create_issue(owner=forgejo_safe_project_owner, repo=forgejo_safe_project_name,
                                        title=issue.title, body=issue.description,
                                        assignee=assignee, assignees=assignees,
                                        milestone=forgejo_milestoneId, labels=forgejo_issue_label_ids,
                                        due_on=due_date, closed=issue.state == "closed")
                fg_print.info(f"Issue {issue.title} imported!")
            except Exception as e:
                detail = _get_exception_detail(e)
                fg_print.error(
                    f"Issue {issue.title} import failed: {detail}"
                    f"Failed to import issue {issue.title} for project {forgejo_safe_project_name} in Forgejo: {detail}",
                )



def _run_inbuilt_repo_import(fg_api: pyforgejo.PyforgejoApi, project: gitlab.v4.objects.Project):
    """Run the inbuilt import on the project"""

    forgejo_safe_project_name = name_clean(project.name)
    
    # get either the Forgejo User or Organization name as appropriate for this gitlab project owner
    forgejo_owner = _get_forgejo_owner_for_gitlab_project(fg_api=fg_api, project=project)
    
    forgejo_owner_name : str
    forgejo_owner_id : int
    if forgejo_owner is None:
        fg_print.error(f"Failed to determine project owner for project {project.name}, skipping import!")
        return
    elif isinstance(forgejo_owner, Organization):
        forgejo_owner_name = forgejo_owner.username
        forgejo_owner_id = forgejo_owner.id
    elif isinstance(forgejo_owner, User):
        forgejo_owner_name = forgejo_owner.login
        forgejo_owner_id = forgejo_owner.id


    if not _forgejo_repo_exists(fg_api=fg_api, owner=forgejo_owner_name, repo=forgejo_safe_project_name):
        clone_url = project.web_url
        if GITLAB_ADMIN_PASS == "" and GITLAB_ADMIN_USER == "":
            clone_url = project.http_url_to_repo

        fg_print.info(f"Importing project {project.name} from {clone_url}...")
        private = project.visibility == "private" or project.visibility == "internal"
        
        if forgejo_owner.id is None:
            fg_print.error(
                f"Failed to load project owner for project {project.name}, skipping import!",
                f"project {project.name} failed to load owner, skipping import!",
            )
            return

        if forgejo_owner.id:
            try:
                repo : Repository = fg_api.repository.repo_migrate(
                                            auth_password=GITLAB_ADMIN_PASS,
                                            auth_username=GITLAB_ADMIN_USER,
                                            auth_token=GITLAB_TOKEN,
                                            clone_addr=clone_url,
                                            description=project.description,
                                            service="gitlab",
                                            issues=True,
                                            labels=True,
                                            milestones=True,
                                            mirror=False,
                                            pull_requests=True,
                                            releases=True,
                                            private=private,
                                            repo_name=forgejo_safe_project_name,
                                            uid=forgejo_owner_id,
                                            wiki=True,
                                    )
                fg_print.info(f"Project {forgejo_safe_project_name} imported {clone_url}!")
            except Exception as e:
                detail = _get_exception_detail(e)
                fg_print.error(f"project {forgejo_safe_project_name} import failed from url {clone_url} : {detail}")
        else:
            fg_print.error(
                f"Failed to load project owner for project {forgejo_safe_project_name}",
                f"project {forgejo_safe_project_name} failed to load owner",
            )



def _import_project_members(
    fg_api: pyforgejo.PyforgejoApi,
    project: gitlab.v4.objects.Project,
):
    """import collaborators for a repository"""
    is_group_owned = False
    if(project.namespace["kind"] == "group"):
        is_group_owned = True
        fg_print.info(f"\nImporting collaborators for group project {project.name}...")
    else:
        fg_print.info(f"\nImporting collaborators for personal project {project.name}...")
    
    project_members: List[gitlab.v4.objects.GroupMember] = project.members.list(get_all=True)
    
    if(len(project_members) == 0):
        fg_print.info(f"No collaborators found for project {project.name}, skipping!")
        return

    # Look up the actual stored username in the database - ensures the project exists but is a marginal overhead
    forgejo_owner = _get_forgejo_owner_for_gitlab_project(fg_api=fg_api, project=project)
    
    forgejo_owner_name : str
    if forgejo_owner is None:
        fg_print.error(f"Failed to determine project owner for project {project.name}, skipping import!")
        return
    elif isinstance(forgejo_owner, Organization):
        forgejo_owner_name = forgejo_owner.username
    elif isinstance(forgejo_owner, User):
        forgejo_owner_name = forgejo_owner.login
    
    forgejo_safe_project_name = name_clean(project.name)
    
    # get list of Users that are collaborators already.
    existing_collaborators = _get_forgejo_collaborators(fg_api=fg_api, owner=forgejo_owner_name, repo=forgejo_safe_project_name)
    existing_collaborator_ids :set[int] = {user.id for user in existing_collaborators}

    gitlab_access_level_to_role_map = _get_gitlab_access_level_role_map()
    gitlab_role_to_forgejo_team_defintions_map = _get_gitlab_role_to_forgejo_team_map()

    required_access_levels_user_map : dict[int,set[str]] = _get_gitlab_required_access_levels_to_username_map_for_group_members(members=project_members)

    is_might_need_to_add_some_users_direct = False
    if is_group_owned:
        # owner is an organization
        user_teams = _get_forgejo_teams(fg_api=fg_api, orgname=forgejo_owner_name)
        existing_repo_teams = _forgejo_list_team_in_repository(fg_api=fg_api, owner=forgejo_owner_name, repo_name=forgejo_safe_project_name)
        existing_repo_team_ids = {team.id for team in existing_repo_teams}
        all_forgejo_teams_members_usernames : set[str] = set()
        is_might_need_to_add_some_users_direct = not (IS_FUZZY_TEAMS_ALLOWED)
        for team in user_teams:
            add_team_to_repo = False
            if team.id in existing_repo_team_ids:
                fg_print.info(f"Skipping team {team.name}, already attached to repository {forgejo_safe_project_name}")
            else:
                if ADD_EMPTY_TEAMS_AS_COLLABORATORS:
                    # Always add team as collaborator
                    add_team_to_repo = True
                else:
                    # Only add non empty teams
                    team_members = _get_forgejo_team_members(fg_api=fg_api, team=team)
                    add_team_to_repo = len(team_members) > 0
            

            if add_team_to_repo:
                _forgejo_add_team_to_repository(fg_api=fg_api,
                                                owner_name=forgejo_owner_name,
                                                repo_name=forgejo_safe_project_name,
                                                team_name=team.name)
            if is_might_need_to_add_some_users_direct:
                # some users could not be added to teams, lets get a list of all those already accounted for in teams
                all_forgejo_teams_members_usernames.update(member.username for member in team_members)
        
    if (not is_group_owned) or is_might_need_to_add_some_users_direct:
        # For every user that is a project member
        for gitlab_access_level,gitlab_usernames in required_access_levels_user_map.items():

            # get a forgejo permissions object relevant for the gitlab_access_level
            forgejo_user_permissions = _get_safe_forgejo_team_definition(gitlab_access_level_to_role_map=gitlab_access_level_to_role_map, 
                                                                        gitlab_role_to_forgejo_team_defintions_map=gitlab_role_to_forgejo_team_defintions_map, 
                                                                        gitlab_access_level=gitlab_access_level,
                                                                        fuzzy=IS_FUZZY_USERS_ALLOWED).permissions.permission
            if forgejo_user_permissions == None:
                if IS_FUZZY_USERS_ALLOWED:
                    fg_print.error(f"Collaborator import failed for users {gitlab_usernames}. Unable to find a direct match for user with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini",
                                f"Collaborator import failed for users {gitlab_usernames}. Unable to find a direct match for user with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini")
                else:
                    fg_print.error(f"Collaborator import failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini",
                                f"Collaborator import failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini")
                # try next access level in use.
                continue

            # get the gitlab usernames requiring import as collaborator
            gitlab_usernames_for_import = gitlab_usernames

            if is_might_need_to_add_some_users_direct:
                # if this is a group owned project, we need to filter out all those already added to teams which have been made collaborators
                gitlab_forgejo_username_map = [(gitlab_username,name_clean(gitlab_username)) for gitlab_username in gitlab_usernames]
                gitlab_usernames_for_import = [gitlab_username
                                                for gitlab_username, cleaned_name in gitlab_forgejo_username_map
                                                if cleaned_name not in all_forgejo_teams_members_usernames]
            
            _import_users_as_collaborator(fg_api=fg_api,
                                    gitlab_access_level_to_role_map=gitlab_access_level_to_role_map,
                                    gitlab_role_to_forgejo_team_defintions_map=gitlab_role_to_forgejo_team_defintions_map,
                                    gitlab_access_level=gitlab_access_level,
                                    gitlab_usernames=gitlab_usernames_for_import,
                                    forgejo_repo=forgejo_safe_project_name,
                                    forgejo_owner=forgejo_owner_name,
                                    existing_collaborator_ids=existing_collaborator_ids)

            

def _import_users_as_collaborator(fg_api: pyforgejo.PyforgejoApi,
                                 gitlab_access_level_to_role_map:dict[int,str],
                                 gitlab_role_to_forgejo_team_defintions_map:dict[str,ForgejoTeamDefinition],
                                 gitlab_access_level:int,
                                 gitlab_usernames:set[str],
                                 forgejo_repo:str,
                                 forgejo_owner:str,
                                 existing_collaborator_ids:set[int]):
    # get a forgejo permissions object relevant for the gitlab_access_level
    forgejo_user_permissions = _get_safe_forgejo_team_definition(gitlab_access_level_to_role_map=gitlab_access_level_to_role_map, 
                                                                gitlab_role_to_forgejo_team_defintions_map=gitlab_role_to_forgejo_team_defintions_map, 
                                                                gitlab_access_level=gitlab_access_level,
                                                                fuzzy=IS_FUZZY_USERS_ALLOWED).permissions.permission
    if forgejo_user_permissions == None:
        if IS_FUZZY_USERS_ALLOWED:
            fg_print.error(f"Collaborator import failed for users {gitlab_usernames}. Unable to find a direct match for user with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini",
                        f"Collaborator import failed for users {gitlab_usernames}. Unable to find a direct match for user with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini")
        else:
            fg_print.error(f"Collaborator import failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini",
                        f"Collaborator import failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. Check fuzzy match upgrade/downgrade settings in .migrate.ini")
        # try next access level in use.
        return

    # For every user that is a project member.... (make them a collaborator)
    for gitlab_username in gitlab_usernames:
        _import_individual_user_collaborator(fg_api=fg_api, 
                                            existing_collaborator_ids=existing_collaborator_ids,
                                            gitlab_username=gitlab_username, forgejo_owner=forgejo_owner,
                                            forgejo_repo=forgejo_repo,
                                            forgejo_permissions=forgejo_user_permissions) 



def _import_individual_user_collaborator(fg_api: pyforgejo.PyforgejoApi,
                                         existing_collaborator_ids:set[int],
                                         gitlab_username:str, 
                                         forgejo_owner:str,
                                         forgejo_repo:str,
                                         forgejo_permissions:str):
    """identical to _import_individual_collaborator except first checks a user exists in Forgejo with that username"""
    forgejo_safe_username = name_clean(gitlab_username)
    user = _get_forgejo_user(fg_api=fg_api, projectName=forgejo_repo, username=forgejo_safe_username)
    if _forgejo_user_exists(fg_api=fg_api, username=forgejo_safe_username):
        _forgejo_add_replace_collaborator(fg_api=fg_api, 
                                    existing_collaborator_ids=existing_collaborator_ids, 
                                    collaborator_id=user.id,
                                    collaborator_name=user.login,
                                    owner=forgejo_owner,
                                    repo=forgejo_repo,
                                    permissions=forgejo_permissions) 
    else:
        fg_print.error(f"Unable to add non existent user {forgejo_safe_username} as collaborator of {forgejo_repo}",
                        f"Unable to add non existent user {forgejo_safe_username} as collaborator of {forgejo_repo}")

    

def is_ignore_gitlab_user(username : str) -> bool:
    BOT_REGEX = re.compile(r"^project_\d{2}_bot_[a-zA-Z0-9]{32}$")
    if (username in IGNORED_GITLAB_SYSTEM_USERS
            or BOT_REGEX.match(username)):
            if IGNORE_GITLAB_SYSTEM_USERS:
                return True
    return False



def _import_users(
    fg_api: pyforgejo.PyforgejoApi, users: List[gitlab.v4.objects.User], notify: bool = False):
    """import users and their public keys"""

    redirect_username = name_clean("redirect")
    isAdded = _forgejo_add_user(fg_api=fg_api, gitlab_username = redirect_username, username = redirect_username, full_name = redirect_username, email = f"{redirect_username}@noemail-git.local", notify = notify)
    
    user : gitlab.v4.objects.User
    for user in users:
        gpg_keys : List[gitlab.v4.objects.UserGPGKey] = user.gpgkeys.list(get_all=True)
        keys: List[gitlab.v4.objects.UserKey] = user.keys.list(get_all=True)

        fg_print.info(f"Importing user {user.username}...")
        
        if is_ignore_gitlab_user(user.username):
            if IGNORE_GITLAB_SYSTEM_USERS:
                fg_print.warning(f"Ignored a Gitlab specific system user {user.username}. If this is incorrect, rerun import permitting system user cloning")
                continue
            else:
                fg_print.warning(f"Likely a Gitlab specific system user {user.username}. Can possibly be deleted after import!")

        fg_print.info(f"Found {len(gpg_keys)} gpg keys for user {user.username}")
        fg_print.info(f"Found {len(keys)} public keys for user {user.username}")

        forgejo_safe_username = name_clean(user.username)
        if not _forgejo_user_exists(fg_api=fg_api, username=forgejo_safe_username):  # need this because status 422 returned for conflict, not 409 
            emailAddress : str = _build_or_extract_email(user)
            isAdded = _forgejo_add_user(fg_api=fg_api, gitlab_username = user.username, username = forgejo_safe_username, full_name = user.name, email = emailAddress, notify = notify)
            if not isAdded:
                # something went wrong with the user import. can't do any more for this user.
                continue

        # import public keys if possible
        _import_user_keys(fg_api=fg_api, keys=keys, gpg_keys=gpg_keys, username=user.username)



def _import_user_keys(
    fg_api: pyforgejo.PyforgejoApi,
    keys: List[gitlab.v4.objects.UserKey],
    gpg_keys : List[gitlab.v4.objects.UserGPGKey],
    username: str,
):
    """import public keys for a user"""
    forgejo_safe_username = name_clean(username)
    forgejo_keys = _get_forgejo_user_keys(fg_api=fg_api, username=forgejo_safe_username)
    forgejo_gpg_keys = _get_forgejo_user_gpg_keys(fg_api=fg_api, username=forgejo_safe_username)

    #
    # SSH keys
    #
    for key in keys:
        key_name = key.title
        key_content = key.key
        existing_key = next(
            (item for item in forgejo_keys if item.title == key_name), None
        )
        if existing_key is None:
            # Import key
            new_key = _forgejo_add_user_key(fg_api=fg_api, username=forgejo_safe_username, key_name=key_name, key_content=key_content)
            if new_key is not None:
                forgejo_keys.append(new_key)

    #
    # GPG keys
    #
    for gpg_key in gpg_keys:
        key_id = getattr(gpg_key, "key_id", None)
        
        key_content = (getattr(gpg_key, "public_key", None) 
                       or getattr(gpg_key, "key", None))
        existing_key = next(
            (item for item in forgejo_gpg_keys if item.key_id == key_id), None
        )
        if existing_key is None:
            # Import key
            new_key = _forgejo_add_gpg_key(fg_api=fg_api, key_id=key_id, key_content=key_content)
            if new_key is not None:
                forgejo_gpg_keys.append(new_key)



def _get_gitlab_required_access_levels_to_username_map_for_project_members(members: List[gitlab.v4.objects.ProjectMember]) -> dict[int,set[str]]:
    """Get a list of all gitlab permissions levels utilised by the group members"""
    
    required_access_levels_user_map : dict[int,set[str]] = dict()
    # If so desired, ensure we create ALL teams regardless of if they presently contain a user or not
    if ADD_EMPTY_TEAMS:
        for permission in _get_gitlab_access_level_role_map().keys():
            required_access_levels_user_map[permission]=set()

    # Now fill the map with the users.
    for member in members:
        users_set = required_access_levels_user_map.get(member.access_level)
        if users_set == None:
            users_set = set()
            required_access_levels_user_map[member.access_level] = users_set
            
        if (not is_ignore_gitlab_user(member.username) 
            and not member.username in users_set
            ):
            #fg_print.info(f"Added member {member.username} to access group {member.access_level}")
            users_set.add(member.username)
    return required_access_levels_user_map



def _get_gitlab_required_access_levels_to_username_map_for_group_members(members: List[gitlab.v4.objects.GroupMember]) -> dict[int,set[str]]:
    """Get a list of all gitlab permissions levels utilised by the group members"""
    
    required_access_levels_user_map : dict[int,set[str]] = dict()
    # If so desired, ensure we create ALL teams regardless of if they presently contain a user or not
    if ADD_EMPTY_TEAMS:
        for permission in _get_gitlab_access_level_role_map().keys():
            required_access_levels_user_map[permission]=set()

    # Now fill the map with the users.
    for member in members:
        users_set = required_access_levels_user_map.get(member.access_level)
        if users_set == None:
            users_set = set()
            required_access_levels_user_map[member.access_level] = users_set
        if (not is_ignore_gitlab_user(member.username)
           and not member.username in users_set):
            #fg_print.info(f"Added member {member.username} to access group {member.access_level}")
            users_set.add(member.username)
    return required_access_levels_user_map



def _import_groups(fg_api: pyforgejo.PyforgejoApi, groups: List[gitlab.v4.objects.Group]):
    """import all groups and their members"""
    fg_print.info(f"Found {len(groups)} gitlab groups")

    group_names = [obj.name for obj in groups]
    fg_print.info(f"Importing groups... {group_names}")
    
    # A group is an organization in Forgejo
    for group in groups:
        # create the Forgejo organization (gitlab group)
        forgejo_safe_group_name = name_clean(group.name)
        fg_print.info(f"Importing group {forgejo_safe_group_name} as Forgejo organization...")

        members: List[gitlab.v4.objects.GroupMember] = group.members.list(get_all=True)
        required_access_levels_user_map : dict[int,set[str]] = _get_gitlab_required_access_levels_to_username_map_for_group_members(members=members)

        # Add the forgejo organization
        added_org = _forgejo_add_organization(fg_api=fg_api, orgname=forgejo_safe_group_name, full_name=group.full_name, description=group.description)
        if not added_org:
            fg_print.warning(f"Group members may fail to import due to organization not being created!")
            #continue # don't skip attempting to add group members

        # report the user mappings identified        
        gitlab_perm_role_mapping = _get_gitlab_access_level_role_map()
        forgejo_role_members = [ (gitlab_perm_role_mapping[item[0]],item[1]) for item in required_access_levels_user_map.items() if len(item[1]) > 0 ]
        fg_print.info(f"Identified roles for members for group {forgejo_safe_group_name} : {forgejo_role_members}")
        
        # Finally, import those group members
        _import_group_members(fg_api=fg_api, group=group, required_access_levels_user_map=required_access_levels_user_map)



def _get_gitlab_role_for_unknown_access_level(gitlab_access_level_to_role_map:dict[int,str], gitlab_access_level:int) -> str | None:
    """Retrieve the most similar role available permissions wise matching user requirements"""
    gitlab_role : str = None
    if ALLOW_FUZZY_AUTH_DOWNGRADE:
        # get role with highest access level below this one
        gitlab_lower_access_levels = [item
                for item in gitlab_access_level_to_role_map.keys()
                if item < gitlab_access_level].sort(reverse=True)
        for gitlab_access_level in gitlab_lower_access_levels:
            gitlab_role = gitlab_access_level_to_role_map.get(gitlab_access_level)
            if gitlab_role != None:
                break
    if ALLOW_FUZZY_AUTH_UPGRADE and gitlab_role == None:
        # No role found with lower access level, Now get next highest
        gitlab_higher_access_levels = [item
            for item in gitlab_access_level_to_role_map.keys()
            if item > gitlab_access_level].sort()
        for gitlab_access_level in gitlab_higher_access_levels:
            gitlab_role = gitlab_access_level_to_role_map.get(gitlab_access_level)
            if gitlab_role != None:
                break
    if gitlab_role == None:
        fg_print.error(f"Error: No Matching Gitlab role could be found for Gitlab access_level {gitlab_access_level}!")
        fg_print.info(f"Either permit gitlab_[upgrade/downgrade]_access_level, or you need to add a gitlab role and forgejo team definition and mapping in this script.")
    return gitlab_role



def _update_forgejo_team_definitions_map_for_custom_role(gitlab_role_to_forgejo_team_defintions_map:dict[int,ForgejoTeamDefinition], closest_role:str, custom_role:str) -> ForgejoTeamDefinition: 
    """Update the mapping, creating and adding a new team definition to match the role"""
    closest_forgejo_team = gitlab_role_to_forgejo_team_defintions_map[closest_role]
    # create a new Forgejo team definition for this role
    new_forgejo_team = deepcopy(closest_forgejo_team)
    new_forgejo_team.name = f"{closest_forgejo_team.name}_GitLab_{custom_role}"
    # Now cache a forgejo team mapping for this gitlab_role
    gitlab_role_to_forgejo_team_defintions_map[custom_role] = new_forgejo_team
    fg_print.info(f"Added custom Forgejo team definition {new_forgejo_team.name} for role {custom_role} matching team {closest_forgejo_team.name} (gitlab role {closest_role})")
    return new_forgejo_team



def _update_gitlab_roles_map_with_custom_role(gitlab_access_level_to_role_map:dict[int,str], gitlab_access_level:int) -> str:
    """Update the mapping, creating and adding a new gitlab role"""
    # Cache the gitlab access_level -> role mapping
    custom_role = f"GitLabRole_{gitlab_access_level}"
    gitlab_access_level_to_role_map[gitlab_access_level] = custom_role
    fg_print.info(f"Added custom GitLab Role {custom_role} for access_level {gitlab_access_level}")
    return custom_role



def _get_safe_forgejo_team_definition(gitlab_access_level_to_role_map:dict[int,str], 
                                      gitlab_role_to_forgejo_team_defintions_map:dict[str,ForgejoTeamDefinition], 
                                      gitlab_access_level:int,
                                      fuzzy:bool) -> ForgejoTeamDefinition | None:
    """Retrieves a ForgejoTeamDefinition, creating a new one and adding neccessary data to the maps as required"""
    # get forgejo team definition matching gitlab permission level
    gitlab_role = gitlab_access_level_to_role_map.get(gitlab_access_level)
    if gitlab_role == None:
        fg_print.error(f"Gitlab Access_Level:Role Mapping missing for {gitlab_access_level}")
        gitlab_role = f"GitLab_{gitlab_access_level}"
        fg_print.info(f"Created new GitLab role : {gitlab_role}")
    
    # Forgejo team needed for this permission level
    forgejo_team_definition = gitlab_role_to_forgejo_team_defintions_map[gitlab_role]

    # If one couldn't be found, then, create one according to user requirements from those available
    if forgejo_team_definition == None and fuzzy:
        fg_print.error(f"Gitlab Role:Forgejo Team Mapping missing for {gitlab_role}")
        closest_gitlab_role = _get_gitlab_role_for_unknown_access_level(gitlab_access_level_to_role_map, gitlab_access_level)
        if closest_gitlab_role != None:
            custom_role = _update_gitlab_roles_map_with_custom_role(gitlab_access_level_to_role_map, gitlab_access_level=gitlab_access_level)
            forgejo_team_definition = _update_forgejo_team_definitions_map_for_custom_role(gitlab_role_to_forgejo_team_defintions_map, 
                                                                                    closest_role=closest_gitlab_role,
                                                                                    custom_role=custom_role)
        else:
            return None
    return forgejo_team_definition



def _import_group_members(
    fg_api: pyforgejo.PyforgejoApi,
    group: gitlab.v4.objects.Group,
    required_access_levels_user_map: dict[int,set[str]]
):
    """import group members (users) as members to an Forgejo organization team"""
    forgejo_safe_group_name = name_clean(group.name)
    existing_teams = _get_forgejo_teams(fg_api=fg_api, orgname=forgejo_safe_group_name)
    existing_teams_names = [team.name for team in existing_teams]
    fg_print.info(f"Existing forgejo teams for {forgejo_safe_group_name} : {existing_teams_names}")

    gitlab_access_level_to_role_map = _get_gitlab_access_level_role_map()
    gitlab_role_to_forgejo_team_defintions_map = _get_gitlab_role_to_forgejo_team_map()
    
    # For each used gitlab access level role
    for gitlab_access_level,gitlab_usernames in required_access_levels_user_map.items():
        forgejo_team_definition = _get_safe_forgejo_team_definition(gitlab_access_level_to_role_map=gitlab_access_level_to_role_map, 
                                                                    gitlab_role_to_forgejo_team_defintions_map=gitlab_role_to_forgejo_team_defintions_map,
                                                                    gitlab_access_level=gitlab_access_level,
                                                                    fuzzy=IS_FUZZY_TEAMS_ALLOWED)
        
        if forgejo_team_definition == None:
            if not IS_FUZZY_TEAMS_ALLOWED and not IS_FUZZY_USERS_ALLOWED:
                fg_print.error(f"Import to Team {forgejo_team_definition.name} failed for users {gitlab_usernames}. Unable to find a direct match for team with gitlab access level {gitlab_access_level}. Import will need either Fuzzy teams or Fuzzy users to succeed.",
                            f"Import to Team {forgejo_team_definition.name} failed for users {gitlab_usernames}. Unable to find a direct match for team with gitlab access level {gitlab_access_level}. Import will need either Fuzzy teams or Fuzzy users to succeed.")
            elif not IS_FUZZY_USERS_ALLOWED:
                fg_print.error(f"Import to Team {forgejo_team_definition.name} failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. Check fuzzy match < > settings in .migrate.ini",
                            f"Import to Team {forgejo_team_definition.name} failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. Check fuzzy match < > settings in .migrate.ini")
            else: # IS_FUZZY_USERS
                fg_print.warning(f"Import to Team {forgejo_team_definition.name} failed for users {gitlab_usernames}. Unable to find neither a direct nor fuzzy match for team with gitlab access level {gitlab_access_level}. User will be added as an individual Collaborator with fuzzy matching if possible")
            # try next access level in use.
            continue
                
            
        possible_team_names = {forgejo_team_definition.name}

        # Handle the owners specially since that team is always pre-created in Forgejo and 
        # a new one cannot be added with those permissions. We might need to update it's name to match user requirement
        if(gitlab_access_level == GITLAB_ACCESS_LEVEL_OWNER):
            # create a set (unique items) including the default owners team name Forgejo uses during initial organization creation
            possible_team_names.add(FORGEJO_DEFAULT_OWNERS_TEAM_NAME)
        
        # find the first matching team
        matching_teams = [team for team in existing_teams 
                        if team.name in possible_team_names]
        
        if len(matching_teams) == 0:
            # No matching team found, lets create one
            team = _forgejo_add_organization_team(fg_api=fg_api, org_name=forgejo_safe_group_name, definition=forgejo_team_definition)
            # add to the empty list of matches
            matching_teams.append(team)
            if team is None:
                fg_print.warning(f"Team not available {forgejo_team_definition.name}, skipping!")
                # Unable to add users to this team, continue with next iteration of for loop.
                continue
        elif len(matching_teams) > 1:
            fg_print.warning(f"Multiple teams were found with name in set {possible_team_names}, this shouldn't be possible in Forgejo, using first")
        
        # get matching Forgejo team
        team = matching_teams[0]
        
        # check team name matches that desired (update if not)
        if team.name != forgejo_team_definition.name:
            # update the team to be named as script user has configured.
            # NOTE: you MUST NOT change the name of the Owners team. It must be hardcoded in Forgejo somewhere.
            team = _forge_update_organization_team(fg_api=fg_api, team=team, definition=forgejo_team_definition)
        
        # Add all matching users to this team
        for gitlab_username in gitlab_usernames:
            forgejo_safe_username = name_clean(gitlab_username)
            added = _forgejo_add_user_to_organization_team(fg_api=fg_api, organization_name=forgejo_safe_group_name, username=forgejo_safe_username, team=team)
            if not added:
                fg_print.error(f"Failed to import user {gitlab_username} permissions in group {group.name}",
                                f"Failed to import user {gitlab_username} permissions in group {group.name}")



def import_users(gitlab_api: gitlab.Gitlab, fg_api: pyforgejo.PyforgejoApi, notify=False):
    """import all users and groups"""
    # read all users
    users: List[gitlab.v4.objects.User] = gitlab_api.users.list(get_all=True)

    fg_print.info(f"Found {len(users)} gitlab users as user {gitlab_api.user.username}")

    # import all non existing users
    _import_users(fg_api=fg_api, users=users, notify=notify)



def import_groups(gitlab_api: gitlab.Gitlab, fg_api: pyforgejo.PyforgejoApi):
    """import all users and groups"""
    # read all users
    groups: List[gitlab.v4.objects.Group] = gitlab_api.groups.list(get_all=True)
    
    # import all non existing groups
    _import_groups(fg_api=fg_api, groups=groups)



def import_projects(gitlab_api: gitlab.Gitlab, fg_api: pyforgejo.PyforgejoApi):
    """read all projects and their issues"""
    projects: List[gitlab.v4.objects.Project] = gitlab_api.projects.list(get_all=True)

    fg_print.info(f"Found {len(projects)} gitlab projects as user {gitlab_api.user.username}")

    project : gitlab.v4.objects.Project
    for project in projects:
        project_owner_name = _get_gitlab_project_owner_slug(project)
        
        if(project.namespace["kind"] == "group"):
            fg_print.info(f"Importing project {project.name} from owner {project_owner_name}")
            fg_print.info(f"Project {project.name} is in group namespace, this will be imported as a repository of organization {project_owner_name}")
        else:
            fg_print.info(f"Importing project {project.name} from owner {project_owner_name}")
        
        # import project repo
        _run_inbuilt_repo_import(fg_api=fg_api, project=project)

        _import_project_members(fg_api=fg_api, project=project)

        # Handled by inbuilt repo migration
        # import labels
        #labels: List[gitlab.v4.objects.ProjectLabel] = project.labels.list(get_all=True)
        #fg_print.info(f"Found {len(labels)} labels for project {project.name}")
        #_import_project_labels(fg_api=fg_api, labels, project_owner_name, project.name)

        # Handled by inbuilt repo migration
        # import milestones
        #milestones: List[gitlab.v4.objects.ProjectMilestone] = project.milestones.list(all=True)
        #fg_print.info(f"Found {len(milestones)} milestones for project {project.name}")
        #_import_project_milestones(fg_api=fg_api, milestones, project_owner_name, project.name)

        # Handled by inbuilt repo migration
        # import issues
        #issues: List[gitlab.v4.objects.ProjectIssue] = project.issues.list(get_all=True)
        #fg_print.info(f"Found {len(issues)} issues for project {project.name}")
        #_import_project_issues(fg_api=fg_api, issues, project_owner_name, project.name)




if __name__ == "__main__":
    main()
