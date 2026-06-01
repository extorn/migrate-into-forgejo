
import re
from typing import List, override

import gitlab  # pip install python-gitlab
import gitlab.v4.objects
import yaml

from fg_migration import fg_print
from fg_migration.forgjo import ForgejoRepositoryRole
from fg_migration.migration_source_type import MigrationSource
from fg_migration.canonical_types import CanonicalGpgKey, CanonicalKey, CanonicalOrganization, CanonicalOrganizations, CanonicalRepo, CanonicalRepoAccessor, CanonicalRepoAccessors, CanonicalSystemUser, CanonicalTeam, CanonicalUser
from fg_migration.config_types import GitLabMigrationConfig, GitLabConfig

class GitLabMigrationSource(MigrationSource):

    gitlab_api: gitlab.Gitlab
    gitlab_config: GitLabConfig
    gitlab_migration_config: GitLabMigrationConfig
    source_system: str = "GitLab"
    access_level_role_map : dict[int,ForgejoRepositoryRole]

    def __init__(self, 
                 gitlab_api:gitlab.Gitlab,
                 gitlab_config:GitLabConfig,
                 gitlab_migration_config:GitLabMigrationConfig):
        self.gitlab_api = gitlab_api
        self.gitlab_config = gitlab_config
        self.gitlab_migration_config = gitlab_migration_config
        self.access_level_role_map = self._load_access_levels_roles_map(path=self.gitlab_migration_config.ACCESS_LEVELS_TO_FORGEJO_ROLES_MAP_FILE_PATH)

    def _load_access_levels_roles_map(self, path: str) -> dict[int,ForgejoRepositoryRole]:
        with open(path) as f:
            cfg = yaml.safe_load(f)

        access_level_role_map : dict[int,ForgejoRepositoryRole] = {}

        for gitlab_access_level, role_cfg in cfg["gitlab_access_levels"].items():
            role_id = role_cfg.get("forgejo_role").strip() # remove whitespace just in case
            role = ForgejoRepositoryRole(role_id)
            gitlab_access_level_int = int(gitlab_access_level)
            access_level_role_map[gitlab_access_level_int] = role

        return access_level_role_map
    


    def _get_is_individual(self, project : gitlab.v4.objects.Project) -> bool:
        namespace_kind = project.namespace.get("kind")
        if namespace_kind == "user":
            return True
        elif namespace_kind == "group":
            return False
        else:
            fg_print.error(f"Unsupported namespace kind {project.namespace['kind']} for project {project.name}, skipping import!")
            raise ValueError(f"namespace unsupported {project.namespace['kind']}")



    def _get_gitlab_project_owner_slug(self, project: gitlab.v4.objects.Project) -> str:
        if project.namespace["kind"] == "user":
            return project.namespace["path"]
        elif project.namespace["kind"] == "group":
            # TODO should this also be: return project.namespace["path"] (maybe not because in Forgjo, the name is used as the identifier)
            return project.namespace["name"]
        else:
            fg_print.error(f"Unsupported namespace kind {project.namespace['kind']} for project {project.name}, skipping import!")
            return None
        


    def _is_ignore_gitlab_user(self, username : str) -> bool:
        BOT_REGEX = re.compile(r"^project_\d{2}_bot_[a-zA-Z0-9]{32}$")
        if (username in self.gitlab_migration_config.IGNORED_GITLAB_SYSTEM_USERS
                or BOT_REGEX.match(username)):
                if self.gitlab_migration_config.IGNORE_GITLAB_SYSTEM_USERS:
                    return True
        fg_print.debug(f"username {username} not in ignored users list {self.gitlab_migration_config.IGNORED_GITLAB_SYSTEM_USERS} and does not match bot regex, will not ignore")
        return False
        


    def _build_or_extract_email(self, user: gitlab.v4.objects.User) -> str:
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



    @override
    def getSourceSystemName(self) -> str:
        return self.source_system
    


    @override
    def list_mapped_forgejo_repository_roles(self) -> set[ForgejoRepositoryRole]:
        return set(self.access_level_role_map.values())



    @override
    def listRepos(self) -> List[CanonicalRepo]:
        projects: List[gitlab.v4.objects.Project] = self.gitlab_api.projects.list(get_all=True)
        fg_print.info(f"Found {len(projects)} gitlab projects as user {self.gitlab_api.user.username}")

        repos : List[CanonicalRepo] = []
        project : gitlab.v4.objects.Project
        seen_repos_map: dict[int,str] = {}
        for project in projects:
            if project.get_id() in seen_repos_map:
                fg_print.error(f"Already have reference to Project {seen_repos_map[project.get_id()]} with ID {project.get_id()}")
                continue

            owner_name = self._get_gitlab_project_owner_slug(project)
            is_individual = self._get_is_individual(project)
            clone_url = project.web_url
            #TODO check clone url set correctly if SSH and or HTTPS clone mode
            if self.gitlab_config.GITLAB_ADMIN_PASS == "" and self.gitlab_config.GITLAB_ADMIN_USER == "":
                clone_url = project.http_url_to_repo
            
            is_private = (project.visibility == "private" or project.visibility == "internal")
            auth_password=self.gitlab_config.GITLAB_ADMIN_PASS
            auth_username=self.gitlab_config.GITLAB_ADMIN_USER
            auth_token=self.gitlab_config.GITLAB_TOKEN

            #TODO is it worth pulling in all the project members now so avoid retrieving them again?
            repo = CanonicalRepo(source_system=self.source_system, source_id=project.get_id(),is_individual=is_individual, name=project.name,owner_name=owner_name, clone_url=clone_url, 
                                       is_private=is_private,description=project.description,
                                       auth_username=auth_username,auth_password=auth_password,auth_token=auth_token,
                                       source_type="Project")
            repos.append(repo)
            seen_repos_map[repo.source_id] = repo.name
            
        return repos
    


    @override
    def list_repository_accessors(self, repo:CanonicalRepo) -> CanonicalRepoAccessors:
        # gitlab project = forgejo repo
        project: gitlab.v4.objects.Project = self.gitlab_api.projects.get(id=repo.source_id)
        project_members: List[gitlab.v4.objects.ProjectMember] = project.members.list(get_all=True)
        project_member : gitlab.v4.objects.ProjectMember
        repo_accessors_members : List[CanonicalRepoAccessor] = []
        repo_accessors = CanonicalRepoAccessors(source_system=self.source_system, source_type="User", members=repo_accessors_members)
        for project_member in project_members:
            if self._is_ignore_gitlab_user(project_member.username):
                if self.gitlab_migration_config.IGNORE_GITLAB_SYSTEM_USERS:
                    fg_print.warning(f"Ignored a GitLab specific system user {project_member.username}. If this is incorrect, rerun import permitting system user cloning")
                    continue
                else:
                    fg_print.warning(f"Likely a GitLab specific system user {project_member.username}. Can possibly be deleted after import!")

            repo_accessors_members.append(CanonicalRepoAccessor(username = project_member.username, access_level= project_member.access_level))
        return repo_accessors



    @override
    def list_organizations(self) -> CanonicalOrganizations:
        # read all users
        groups: List[gitlab.v4.objects.Group] = self.gitlab_api.groups.list(get_all=True)
        
        organizations = CanonicalOrganizations(source_type="Groups", members=[])

        group : gitlab.v4.objects.Group

        # Each group is essentially mapping as repository (or series of) owner (Forgejo organization)
        for group in groups:
            # create a map only to avoid searching for the right team for each user
            #TODO currently though we have a list of Teams for each org, we simply
            #     add users to the first matching team in the access level. If actually there are multiple
            #     users with same access level in a group, but not all sharing access to same repository,
            #     we'll need to create multiple teams, depending on repository access (which might get complicated quickly)
            access_role_teams_map: dict[int,List[CanonicalTeam]] = {}
            
            # Group members are users. They share a finite set of access_level
            # we can map that access level to a team.
            groupMembers: List[gitlab.v4.objects.GroupMember] = group.members.list(get_all=True)
            
            # For every user that has access to this group
            for member in groupMembers:
                if self._is_ignore_gitlab_user(member.username):
                    if self.gitlab_migration_config.IGNORE_GITLAB_SYSTEM_USERS:
                        fg_print.warning(f"Ignored a GitLab specific system user {member.username}. If this is incorrect, rerun import permitting system user cloning")
                        continue
                    else:
                        fg_print.warning(f"Likely a GitLab specific system user {member.username}. Can possibly be deleted after import!")

                # get the correct team
                team = self._find_or_create_team(access_level_teams_map=access_role_teams_map, access_level=member.access_level)
                
                # add the user to the team
                team.users.append(CanonicalUser(username=member.username))

            # create an organization
            #TODO is gitlab api Group username stored in path or full_path or username?
            this_org = CanonicalOrganization(source_type="Group", username=group.path, full_name=group.full_name, 
                                             description=group.description, teams=[
                                                                                    team
                                                                                    for team_list in access_role_teams_map.values()
                                                                                    for team in team_list
                                                                                ],) # Note have to flatten the list of teams per access_level here.
            # add the org to the list
            organizations.members.append(this_org)
            
        return organizations



    def _find_or_create_team(self, access_level_teams_map:dict[int,List[CanonicalTeam]], access_level:int) -> CanonicalTeam:
        # create a new team for each access level that doesn't already have one.
        if access_level not in access_level_teams_map:
            # create a new team for this access level, we will fill the users later
            access_level_teams_map[access_level] = [CanonicalTeam(
                                                        username=None,
                                                        source_access_level=str(access_level),
                                                        users=[]
                                                    )]
        teams = access_level_teams_map[access_level]
        return teams[0] #TODO we're not defining teams per repo yet.



    @override
    def list_system_users(self) -> List[CanonicalSystemUser]:
        users: List[gitlab.v4.objects.User] = self.gitlab_api.users.list(get_all=True)
        canonical_users : List[CanonicalSystemUser] = []
        for user in users:
            if self._is_ignore_gitlab_user(user.username):
                if self.gitlab_migration_config.IGNORE_GITLAB_SYSTEM_USERS:
                    fg_print.warning(f"Ignored a GitLab specific system user {user.username}. If this is incorrect, rerun import permitting system user cloning")
                    continue
                else:
                    fg_print.warning(f"Likely a GitLab specific system user {user.username}. Can possibly be deleted after import!")


            gpg_keys : List[gitlab.v4.objects.UserGPGKey] = user.gpgkeys.list(get_all=True)
            keys: List[gitlab.v4.objects.UserKey] = user.keys.list(get_all=True)
            
            emailAddress : str = self._build_or_extract_email(user)
            canonical_keys = [CanonicalKey(name=key.title, key=key.key) for key in keys]
            canonical_gpg_keys : List[CanonicalGpgKey] = []
            for gpg_key in gpg_keys:
                key_id = getattr(gpg_key, "key_id", None)
                
                key_content = (getattr(gpg_key, "public_key", None) 
                            or getattr(gpg_key, "key", None))
                canonical_gpg_keys.append(CanonicalGpgKey(name=key_id, armored_public_key=key_content, armored_signature=None))
                
            canonical_users.append(CanonicalSystemUser(gpg_keys=canonical_gpg_keys,keys=canonical_keys,
                                                       email=emailAddress, full_name=user.name,
                                                       username=user.username, source_system=self.source_system))
        return canonical_users

    

    @override
    def get_repository_role(self, source_access_level:str) -> ForgejoRepositoryRole:
        """Get a predefined ForgejoRepositoryRole or give a unique string to identify a role type that should be used for this access level"""
        gitlab_access_level : int = int(source_access_level)
        role = self.access_level_role_map.get(gitlab_access_level)
        if role is None:
            fg_print.error(f"{self.source_system} Access_Level:Role Mapping missing for {source_access_level}")
            role_id = f"{self.source_system}_Role_{source_access_level}"
            fg_print.info(f"Created new {self.source_system} role type : {role_id}")
            self.access_level_role_map[gitlab_access_level] = ForgejoRepositoryRole(id=role_id, is_custom=True)
        return role


    @override
    def get_nearest_repository_role(self, source_access_level:str,
                                 allow_downgrade:bool,
                                 allow_upgrade:bool) -> ForgejoRepositoryRole | None:
        """Used when a new role type has had to be created.
           Get the closest defined role for this access level following the rules.
           The result willl be assigned to be used whenever the custom role type is used"""
        access_level_int = int(source_access_level)
        known_access_levels = sorted(self.access_level_role_map.keys())
        closest_access_level: int | None = None
        #TODO this isn't very good, we could check the Forgejo roles to see if there are any custom ones
        #     that we could map to instead of just looking at the predefined ones in the gitlab role mapping, but this is a start.
        if(allow_upgrade):
            larger = min((x for x in known_access_levels if x > access_level_int), default=None)
            if not larger is None:
                closest_access_level = larger
        if(allow_downgrade):
            smaller = max((x for x in known_access_levels if x < access_level_int), default=None)
            if not smaller is None:
                closest_access_level = smaller
        
        return self.get_repository_role(str(closest_access_level))
