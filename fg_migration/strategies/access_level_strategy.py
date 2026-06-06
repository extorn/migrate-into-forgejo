from copy import deepcopy
from dataclasses import dataclass
from typing import override

from pyforgejo import Team

from fg_migration.utils import fg_print
from fg_migration.strategies.access_mapping_strategy import AccessMappingStrategy
from fg_migration.core.canonical_types import CanonicalGroupMembership, CanonicalOrganization, CanonicalRepo, CanonicalRepoMembership, CanonicalRepoMemberships, CanonicalUser
from fg_migration.adapters.forgeo_types import ForgejoRepositoryRole, ForgejoRolePermissionDefinition, ForgejoTeamDefinition, IterativeFetchError
from fg_migration.adapters.forgjo import ForgejoDestination
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.utils.utils import name_clean


class AccessLevelAccessMappingStrategy(AccessMappingStrategy):
    """
    Access mapping strategy that derives Forgejo teams and repository permissions
    directly from source-system access levels.

    This strategy treats access levels as the canonical representation of
    authorization. Users with the same source access level are grouped into a
    corresponding Forgejo team, and repository access is granted through those
    teams whenever possible.

    Behaviour:
    - Creates or reuses Forgejo teams mapped from source access levels.
    - Imports organization membership by assigning users to the appropriate
      access-level team.
    - Grants repository access through teams for organization-owned repositories.
    - Falls back to direct repository collaborators only for users not covered
      by team-based access.
    - Supports fuzzy role/team mapping when enabled by migration configuration.
    - Preserves team-based authorization intent by avoiding automatic conversion
      of failed team assignments into individual collaborator assignments.
    """

    migration_dest:ForgejoDestination

    def __init__(self, migration_dest:ForgejoDestination):
        self.migration_dest = migration_dest
    
    @dataclass
    class TeamMatchResult:
        matched_team: Team | None = None
        renamed_team: Team | None = None
        remove_key: str | None = None



    @override
    def import_teams(self, migration_source: MigrationSource, organization: CanonicalOrganization):
        """Create Forgejo teams as a projection of canonical membership facts"""

        try:
            iter_all_teams = self.migration_dest.iter_forgejo_teams(
                org_name=organization.get_safe_username()
            )

            existing_forgejo_org_teams_map: dict[str, Team] = {
                team.name.lower(): team
                for team in iter_all_teams
                if team.name
            }

        except IterativeFetchError as e:
            fg_print.error(
                f"Failed to load existing teams for {organization.get_safe_username()}: {e}"
            )
            return # try the next org

        # ---------------------------------------------------
        # STEP 1: derive "team intent" from membership facts
        # ---------------------------------------------------
        # Option 4 rule: grouping is a *policy decision*, not a model assumption
        team_intent_map: dict[str, list[CanonicalGroupMembership]] = {}

        for m in organization.memberships:
            if m.username is None:
                continue

            # THIS is the ONLY grouping decision still allowed here:
            # (you can later move this into a policy layer if needed)
            key = str(m.access_level)

            team_intent_map.setdefault(key, []).append(m)

        # This is potentially a saving in API calls due to the fuzzy mapping to teams
        team_members_cache: dict[int, set[str]] = {}

        # ---------------------------------------------------
        # STEP 2: materialise Forgejo teams
        # ---------------------------------------------------
        for access_level, memberships in team_intent_map.items():

            forgejo_team_definition = self._get_forgejo_team_definition(
                migration_source=migration_source,
                source_access_level=access_level,
                fuzzy=self.migration_config.IS_FUZZY_TEAMS_ALLOWED,
            )

            if forgejo_team_definition is None:
                fg_print.warning(
                    f"No Forgejo team mapping for access level {access_level}"
                )
                continue

            usernames = {m.username for m in memberships if m.username}

            match_result = self._find_existing_org_team_for_new_users_matching(
                organization=organization,
                existing_forgejo_teams_map=existing_forgejo_org_teams_map,
                forgejo_team_definition=forgejo_team_definition,
                canonical_team_members=[
                    CanonicalUser(username=u) for u in usernames
                ],
            )

            if match_result is None:
                continue

            if match_result.remove_key:
                del existing_forgejo_org_teams_map[match_result.remove_key]
                existing_forgejo_org_teams_map[
                    match_result.renamed_team.name.lower()
                ] = match_result.renamed_team

            existing_team = match_result.matched_team
            is_new_team = False

            if existing_team is None:
                existing_team = self.migration_dest.forgejo_add_organization_team(
                    org_name=organization.get_safe_username(),
                    definition=forgejo_team_definition,
                )

                if existing_team is None:
                    fg_print.warning(
                        f"Failed to create team {forgejo_team_definition.name}"
                    )
                    continue

                is_new_team = True
                if not existing_team.name:
                    fg_print.error(
                        f"Created team returned without a name. Team Id = {existing_team.id}"
                        f"for organization {organization.get_safe_username()}. Skipping import of team users."
                    )
                    continue
                existing_forgejo_org_teams_map[existing_team.name.lower()] = existing_team

            # ---------------------------------------------------
            # STEP 3: attach users (idempotent)
            # ---------------------------------------------------
            self.migration_dest.import_team_users_from_usernames(
                organization=organization,
                usernames=usernames,
                dest_team=existing_team,
                team_members_cache=team_members_cache,
                is_new_team=is_new_team,
            )



    @override
    def import_repository_accessors(
        self,
        migration_source: MigrationSource,
        source_repo: CanonicalRepo,
    ):
        """Import all teams or individuals that should have access to a repository"""

        repo_accessors: CanonicalRepoMemberships = (
            migration_source.list_repository_accessors(source_repo)
        )

        if source_repo.is_individual:
            fg_print.info(
                f"\nImporting collaborators for personal {source_repo.source_type} {source_repo.name}..."
            )
        else:
            fg_print.info(
                f"\nImporting collaborators for shared {source_repo.source_type} {source_repo.name}..."
            )

        if len(repo_accessors.members) == 0:
            fg_print.info(
                f"No accessors found for {source_repo.name}, skipping collaborators"
            )
            return

        fg_print.debug(
            f"Repository {source_repo.name} accessors found: "
            f"{[a.username for a in repo_accessors.members]}"
        )

        # ---------------------------------------------------
        # Resolve repo owner
        # ---------------------------------------------------
        forgejo_repo_owner = self.migration_dest._resolve_forgejo_repo_owner(source_repo)
        if forgejo_repo_owner is None or forgejo_repo_owner.username is None:
            fg_print.error(
                f"Failed to resolve Forgejo owner for {source_repo.name}, skipping"
            )
            return

        # ---------------------------------------------------
        # Existing collaborators
        # ---------------------------------------------------
        try:
            iter_existing = self.migration_dest.iter_forgejo_collaborators(
                owner_username=forgejo_repo_owner.username,
                repo=source_repo.get_safe_username(),
            )

            existing_collaborator_ids: set[int] = {
                u.id for u in iter_existing if u.id is not None
            }

        except IterativeFetchError:
            fg_print.error(
                f"Failed to load existing collaborators for {source_repo.name}"
            )
            return

        # ---------------------------------------------------
        # STEP 1: extract all usernames from canonical facts
        # ---------------------------------------------------
        all_usernames: set[str] = {
            m.username for m in repo_accessors.members if m.username
        }

        all_team_usernames: set[str] = set()

        # ---------------------------------------------------
        # STEP 2: attach teams (only if repo is org-owned)
        # ---------------------------------------------------
        if not source_repo.is_individual:

            iter_existing_repo_teams = (
                self.migration_dest.iter_forgejo_teams_in_repository(
                    owner_username=forgejo_repo_owner.username,
                    repo_name=source_repo.get_safe_username(),
                )
            )

            try:
                existing_repo_team_ids = {
                    t.id
                    for t in iter_existing_repo_teams
                    if t.id is not None
                }
            except IterativeFetchError:
                fg_print.error(
                    f"Failed to load existing repository teams for "
                    f"{source_repo.get_safe_username()}"
                )
                return

            try:
                for org_team in self.migration_dest.iter_forgejo_teams(
                    org_name=forgejo_repo_owner.username
                ):
                    if org_team.id in existing_repo_team_ids:
                        continue

                    try:
                        team_members = list(
                            self.migration_dest.iter_forgejo_team_members(team=org_team)
                        )
                    except IterativeFetchError:
                        continue

                    member_usernames = {
                        m.login for m in team_members if m.login
                    }

                    # only attach team if all members are valid repo accessors
                    if not member_usernames.issubset(all_usernames):
                        continue

                    if not self.migration_dest.forgejo_add_team_to_repository(
                        owner_username=forgejo_repo_owner.username,
                        repo_name=source_repo.get_safe_username(),
                        team_name=org_team.name,
                    ):
                        # Only add usernames if successfully added team.
                        affected_usernames = member_usernames.difference(all_team_usernames)
                        fg_print.warning(f"Team addition with Users failed. These users will not be added as individual members instead. Affected users: {affected_usernames}")
                    
                    # Mark users as accounted for by team intent.
                    # We deliberately do not fall back to individual collaborators
                    # because that would alter the intended permission model.
                    all_team_usernames.update(member_usernames)
            except IterativeFetchError:
                fg_print.error(
                    f"Failed to load organization teams for "
                    f"{forgejo_repo_owner.username}"
                )
                return
        
        # ---------------------------------------------------
        # STEP 3: individual collaborators (true fallback path)
        # ---------------------------------------------------
        direct_users = [
            m for m in repo_accessors.members
            if m.username and m.username not in all_team_usernames
        ]

        # STEP 4: import remaining users directly

        for membership in direct_users:

            perm = self.resolve_forgejo_permission(
                migration_source,
                membership.access_level
            )

            if perm is None:
                fg_print.warning(
                    f"No Forgejo permission mapping for "
                    f"user {membership.username} "
                    f"access level {membership.access_level}"
                )
                continue

            self.migration_dest._import_individual_user_collaborator(
                existing_collaborator_ids=existing_collaborator_ids,
                accessor=membership,
                source_repo=source_repo,
                forgejo_permissions=perm,
            )



    @override
    def resolve_forgejo_permission(
        self,
        migration_source:MigrationSource,
        source_access_level: str,
    ) -> str | None:

        role_definition = self._get_forgejo_role_definition(
            migration_source=migration_source,
            source_access_level=source_access_level,
            fuzzy=self.migration_config.IS_FUZZY_USERS_ALLOWED,
        )

        if role_definition is None:
            return None

        permissions = role_definition.permission

        if "admin" in permissions:
            return "admin"

        if "write" in permissions:
            return "write"

        if "read" in permissions:
            return "read"

        return None
    

    def _get_forgejo_team_definition(self, migration_source:MigrationSource, source_access_level:str, fuzzy:bool) -> ForgejoTeamDefinition | None:
        """Retrieves a ForgejoTeamDefinition, creating a new one and adding neccessary data to the maps as required"""
        # get forgejo team definition matching gitlab permission level
        repository_role : ForgejoRepositoryRole = migration_source.get_repository_role(source_access_level=source_access_level)
        
        if repository_role.is_custom:
            nearest_repository_role = None
            if fuzzy:
                fg_print.warning(f"{migration_source.getSourceSystemName()} Role:Forgejo Team Mapping missing for {repository_role}. Using fuzzy matching")
                nearest_repository_role = migration_source.get_nearest_repository_role(source_access_level=source_access_level,
                                                                                allow_downgrade=self.migration_config.ALLOW_FUZZY_AUTH_DOWNGRADE,
                                                                                allow_upgrade=self.migration_config.ALLOW_FUZZY_AUTH_UPGRADE)
            # if it still isn't valid.
            if nearest_repository_role is None:
                return None
            # we now have a valid nearest_repository_role, lets create a mapping based on the team referenced by that for our invalid one.
            self.migration_dest.addTeamMapping(map_from_role=repository_role, to_role=nearest_repository_role)

        # now we definitely have a team mapped against this role, even if it is just a basic string
        return self.migration_dest.team_definitions[repository_role]
    


    def _find_existing_org_team_for_new_users_matching(self,
                                                   organization : CanonicalOrganization,
                                                   existing_forgejo_teams_map : dict[str,Team], 
                                                   forgejo_team_definition : ForgejoTeamDefinition, 
                                                   canonical_team_members : list[CanonicalUser]) -> TeamMatchResult | None:
        
        matched_team = self._find_matching_team(existing_forgejo_teams_map, forgejo_team_definition)
        if matched_team is None:
            return self.TeamMatchResult(matched_team=None)
        
        try:
            existing_usernames = {member.login
                                  for member in self.migration_dest.iter_forgejo_team_members(matched_team)
                                  if member.login is not None}
        except IterativeFetchError:
            # bubble the problem
            return None


        imported_usernames = { user.get_safe_username()
                               for user in canonical_team_members}

        if existing_usernames == imported_usernames:
            fg_print.info(f"Pre-existing team members exactly match team being imported, so reusing."
                          f"\nAffected Forgejo Organization {organization.get_safe_username()} Team {matched_team.name}")
            return self.TeamMatchResult(matched_team=matched_team)
        
        if self.migration_config.USE_EXISTING_TEAMS:
            fg_print.warning(f"Pre-existing team users will be granted access to new repositories that are created with access granted to this team."
                             f"\nAffected Forgejo Organization {organization.get_safe_username()} Team {matched_team.name}, usernames: {existing_usernames}")
            return self.TeamMatchResult(matched_team=matched_team)
        
        result = self._rename_team_out_of_the_way(team = matched_team)


        return result
    

    def _rename_team_out_of_the_way(self,
                                    team: Team,
                                    ) -> TeamMatchResult:
        
        current_definition = ForgejoTeamDefinition.fromTeam(
            team=team,
            role_builder=self.migration_dest.forgejo_team_to_role_mapper,
            require_exact=True,
        )

        new_definition = deepcopy(current_definition)
        new_definition.name += name_clean(
            f"_pre_migrate_{self.migration_config.MIGRATION_DATE_TIME}"
        )

        updated_team = self.migration_dest.forgejo_update_organization_team(
            team=team,
            current_definition=current_definition,
            new_definition=new_definition,
        )

        if updated_team is not None:
            fg_print.debug(f"Success: Moved team with name {team.name} out of the way to allow creation of new team with the same name for import. Renamed team to {updated_team.name}")
        else:
            fg_print.debug(f"Failed: Moved team with name {team.name} out of the way to allow creation of new team with the same name for import.")

        if updated_team is not None:
            return self.TeamMatchResult(renamed_team=updated_team, remove_key=team.name.lower())
        
        return self.TeamMatchResult(matched_team=team)

    

    def _get_forgejo_role_definition(self, migration_source:MigrationSource, source_access_level:str, fuzzy:bool) -> ForgejoRolePermissionDefinition | None:
        """Retrieves a ForgejoRoleDefinition, creating a new one and adding neccessary data to the maps as required"""
        # get forgejo team definition matching gitlab permission level
        repository_role : ForgejoRepositoryRole = migration_source.get_repository_role(source_access_level=source_access_level)
        
        if repository_role.is_custom:
            nearest_repository_role = None
            if fuzzy:
                fg_print.warning(f"{migration_source.getSourceSystemName()} Role:Forgejo Team Mapping missing for {repository_role}. Using fuzzy matching")
                nearest_repository_role = migration_source.get_nearest_repository_role(source_access_level=source_access_level,
                                                                                allow_downgrade=self.migration_config.ALLOW_FUZZY_AUTH_DOWNGRADE,
                                                                                allow_upgrade=self.migration_config.ALLOW_FUZZY_AUTH_UPGRADE)
            # if it still isn't valid.
            if nearest_repository_role is None:
                return None
            # we now have a valid nearest_repository_role, lets create a mapping based on the team referenced by that for our invalid one.
            self.migration_dest.addRoleMapping(map_from_role=repository_role, to_existing_role=nearest_repository_role)

        # now we definitely have a team mapped against this role, even if it is just a basic string
        return self.migration_dest.role_definitions[repository_role]
    


    def _find_matching_team(self,
                            existing_forgejo_teams_map: dict[str, Team],
                            forgejo_team_definition: ForgejoTeamDefinition,
    ) -> Team | None:
        
        team_key = forgejo_team_definition.name.lower()
        found = existing_forgejo_teams_map.get(
            team_key
        )
        if found is None:
            fg_print.debug(f"No existing team matching {team_key} found in set !{list(existing_forgejo_teams_map.keys())}")
        return found