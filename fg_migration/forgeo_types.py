
from abc import abstractmethod
from dataclasses import dataclass, field
from pprint import pformat
import re
from typing import override
import typing

# Forgejo API imports:
from pyforgejo import CreateTeamOptionPermission, PyforgejoApi, Team
import pyforgejo

from fg_migration import fg_print
from fg_migration.config_types import ForgejoConfig
from fg_migration.utils import diff_dataclasses
from httpx import Client as HttpxClient

class ForgejoApiBuilder:
    config : ForgejoConfig

    def __init__(self, forgejo_config:ForgejoConfig):
            self.config = forgejo_config

    def build_forgejo_api_client(self, api_key: str | None) -> pyforgejo.PyforgejoApi:
        """Build a Forgejo API Client using either the API key provided, or the default API key"""
        api_token : str
        if api_key is None:
            api_token = self.config.FORGEJO_API_TOKEN
        else:
            api_token = api_key
        return PyforgejoApi(base_url=self.config.FORGEJO_API_URL, 
                            api_key=api_token, 
                            httpx_client = self._build_httpx_client(config=self.config))
    
    def _build_httpx_client(self, timeout: typing.Optional[float]=60, follow_redirects: typing.Optional[bool] = True) -> HttpxClient:
        client = None
        if(self.config.FORGEJO_CLIENT_AUTH_CERT != None and self.config.FORGEJO_CLIENT_AUTH_KEY != None):
            cert_path = self.config.FORGEJO_CLIENT_AUTH_CERT
            key_path = self.config.FORGEJO_CLIENT_AUTH_KEY
            cert = (cert_path, key_path)
            client = HttpxClient(cert=cert, timeout=timeout,follow_redirects=follow_redirects)
        return client

    def test_forgejo_connection(self, fg_api:PyforgejoApi) -> bool:
        try:
            response = fg_api.miscellaneous.get_version()
        except Exception as e:
            fg_print.error(f"Failed to connect to Forgejo! {e}")
            return False
        fg_ver = response.version
        
        fg_print.info(f"Connected to Forgejo, version: {fg_ver}")
        return True



@dataclass(frozen=True)
class ForgejoRepositoryRole():
    id: str
    is_custom: bool = False


@dataclass
class ForgejoRolePermissionDefinition:
    role : ForgejoRepositoryRole
    can_create_org_repo:bool = False
    includes_all_repositories:bool = False
    permission:CreateTeamOptionPermission = ""
    units_map: dict[str,str] = field(default_factory=dict) # use of field here ensures new instance for every instance of the class

    def diff(self, other:ForgejoRolePermissionDefinition) -> str :
        return diff_dataclasses(self,other)

@dataclass
class ForgejoTeamDefinition:
    """A definition for a Forgejo team, which may be used to create new teams or compare with existing ones.
       Note that a team name is its ID, but it is only unique in a given organization.
       Technically two identically named teams could exist in separate organizations with different permissions
       This is not currently handled in the code, but it is something to be aware of."""
    name: str
    description: str
    permissions: ForgejoRolePermissionDefinition
    allow_empty: bool

    @staticmethod
    def fromTeam(team:Team, role_builder:ForgejoTeamRoleBuilder, require_exact:bool=False) -> ForgejoTeamDefinition:
        """Will find a team definition closest matching the team provided according to the rules in role_builder.
           If require_exact is true, then if none are found, one will be created"""
        
        if role_builder:
            role,_ = role_builder.get_role_matching_permission(team=team, require_exact=require_exact)
            
            role_permissions = role_builder.get_role_permissions(role)
            # we don't cache these because teams are only unique in a given organization.
            return ForgejoTeamDefinition(name=team.name, description=team.description, permissions=role_permissions)

        raise Exception("Role builder not available")
    
    def diff(self, other:ForgejoTeamDefinition) -> str :
        return diff_dataclasses(self,other)

class ForgejoTeamRoleBuilder:
    @abstractmethod
    def get_role_matching_permission(self, team:Team, require_exact:bool=False) -> tuple[ForgejoRepositoryRole,bool]:
        """Retrieve the Forgejo role permissions applicable for this role
           Note; this is used only when retrieving existing teams from the Forgejo server at present
           @return tuple[role:ForgejoRepositoryRole,is_exact_match:bool]
        """
        pass
    @abstractmethod
    def get_role_permissions(self, role:ForgejoRepositoryRole) -> ForgejoRolePermissionDefinition:
        """Retrieve the Forgejo role permissions applicable for this role
           Note; this is used only when retrieving existing teams from the Forgejo server at present
        """
        pass

class ForgejoTeamRoleMapper(ForgejoTeamRoleBuilder):

    role_definitions : dict[ForgejoRepositoryRole,ForgejoRolePermissionDefinition]
    custom_role_count: dict[ForgejoRepositoryRole, int] = {}
    CUSTOM_ROLE_PATTERN = re.compile(r"^Custom_(.+?)_(\d+)$")

    def __init__(self, role_definitions:dict[ForgejoRepositoryRole,ForgejoRolePermissionDefinition]):
        self.role_definitions = role_definitions
    


    @override
    def get_role_permissions(self, role:ForgejoRepositoryRole) -> ForgejoRolePermissionDefinition:
        return self.role_definitions[role]
    
    
    
    @override
    def get_role_matching_permission(self, team:Team, require_exact:bool=False) -> tuple[ForgejoRepositoryRole,bool]:
        
        best_role = None
        best_score = float("-inf")
        debug_log = []

        for role, perm_def in self.role_definitions.items():

            # Exact match shortcut
            if (
                str(perm_def.permission) == str(team.permission)
                and perm_def.units_map == team.units_map
            ):
                fg_print.debug(f"SUCCESS: Exact match for team {team.name}: {role}")
                return role,True

            score = 0

            # Permission match is important
            if str(perm_def.permission) == str(team.permission):
                score += 10

            # Compare unit permissions
            all_units = set(perm_def.units_map) | set(team.units_map)

            for unit in all_units:
                expected = perm_def.units_map.get(unit)
                actual = team.units_map.get(unit)

                if expected == actual:
                    score += 1
                else:
                    score -= 1

            debug_log.append(f"Role {role} scored {score} for team {team.name}")

            if score > best_score:
                best_score = score
                best_role = role

        #best_definition = self.role_definitions[best_role]
        fg_print.debug("\n".join(debug_log))
        fg_print.warning(
            f"No exact role match found for existing Forejo team {team.name}. "
            f"Closest role is {best_role}, "
            f"but sought permission: {team.permission}, "
            f"sought unit_map:\n{pformat(team.units_map)}"
        )

        role = best_role
        if require_exact:
            role = self.add_custom_forgejo_role(team=team, closest_existing_role=best_role)
            return role,True
        return role,False
    
    

    def _update_get_custom_role_count(self, role: ForgejoRepositoryRole | None) -> int:

        key = role if role is not None else ForgejoRepositoryRole(id = "UNKNOWN")

        self.custom_role_count[key] = (
            self.custom_role_count.get(key, 0) + 1
        )

        return self.custom_role_count[key]

    

    def _is_custom_role(self, role_id: str) -> bool:
        return bool(self.CUSTOM_ROLE_PATTERN.match(role_id))



    def _get_custom_role_base(self, role_id: str) -> str:
        match = self.CUSTOM_ROLE_PATTERN.match(role_id)
        return match.group(1) if match else role_id



    def _build_custom_role_id(self, base_name: str, num: int) -> str:
        return f"Custom_{base_name}_{num}"



    @override
    def add_custom_forgejo_role(self, team: Team, closest_existing_role: ForgejoRepositoryRole | None,) -> ForgejoRepositoryRole:

        num = self._update_get_custom_role_count(role=closest_existing_role)
        base_name = ("Role"
                        if closest_existing_role is None
                        else self._get_custom_role_base(closest_existing_role.id)
                    )

        new_role_id = self._build_custom_role_id(base_name=base_name, num=num,)

        role = ForgejoRepositoryRole(new_role_id)

        self.role_definitions[role] = ForgejoRolePermissionDefinition(
            role=role,
            can_create_org_repo=team.can_create_org_repo,
            includes_all_repositories=team.includes_all_repositories,
            permission=team.permission,
            units_map=dict(team.units_map),
        )

        return role