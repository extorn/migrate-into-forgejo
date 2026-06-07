"""Defines the AccessLevelAccessMappingStrategy class"""
from abc import ABC
from copy import deepcopy
from dataclasses import dataclass
from typing import cast, override

from pyforgejo import Team

from fg_migration.core.config_types import MigrationConfig
from fg_migration.utils import fg_print
from fg_migration.strategies.access_mapping_strategy import AccessMappingStrategy
from fg_migration.core.canonical_types import (CanonicalOrganizationMembership,
                                               CanonicalOrganization,
                                               CanonicalRepo, CanonicalRepoMemberships,
                                               CanonicalUser)
from fg_migration.adapters.forgeo_types import (ForgejoRepositoryRole,
                                                ForgejoRolePermissionDefinition,
                                                ForgejoTeamDefinition, IterativeFetchError)
from fg_migration.adapters.destination_forgjo import ForgejoDestination
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

    Example
    -------
    Source system organization memberships:

        alice   -> Maintainer
        bob     -> Maintainer
        charlie -> Developer
        dave    -> Guest

    Team mappings:

        Maintainer -> forgejo-maintainers
        Developer  -> forgejo-developers
        Guest      -> forgejo-guests
        Auditor    -> forgejo-auditors

    Resulting Forgejo organization structure:

        forgejo-maintainers
            ├── alice
            └── bob

        forgejo-developers
            └── charlie

        forgejo-guests
            └── dave

        forgejo-auditors
            └── <no members>

    The empty "forgejo-auditors" team may still be created when
    ADD_EMPTY_TEAMS_TO_ORGANIZATIONS is enabled and the team definition
    allows empty teams.

    Repository access example
    -------------------------
    If repository "project-a" is accessible to all four users in the source
    system, the strategy will preferentially grant repository access through
    teams:

        project-a
            ├── forgejo-maintainers
            ├── forgejo-developers
            └── forgejo-guests

    rather than creating individual repository collaborators.

    Empty teams can also be attached to repositories when
    ADD_EMPTY_TEAMS_TO_REPOSITORIES is enabled:

        project-a
            ├── forgejo-maintainers
            ├── forgejo-developers
            ├── forgejo-guests
            └── forgejo-auditors (0 members)

    This allows predefined permission structures to exist in Forgejo even when
    no users currently occupy a given role.

    Direct collaborators are only used as a fallback when a user cannot be
    represented through a team-based access mapping.

    Behaviour:
    - Creates or reuses Forgejo teams mapped from source access levels.
    - Optionally creates configured empty teams even when no source users map
      to them.
    - Imports organization membership by assigning users to the appropriate
      access-level team.
    - Grants repository access through teams for organization-owned repositories.
    - Optionally grants repository access to empty teams.
    - Falls back to direct repository collaborators only for users not covered
      by team-based access.
    - Supports fuzzy role/team mapping when enabled by migration configuration.
    - Preserves team-based authorization intent by avoiding automatic conversion
      of failed team assignments into individual collaborator assignments.
    """

    migration_dest:ForgejoDestination
    migration_config:MigrationConfig

    def __init__(self, migration_dest:ForgejoDestination, migration_config:MigrationConfig):
        self.migration_dest = migration_dest
        self.migration_config = migration_config

    @dataclass
    class TeamMatchResult(ABC):
        """Complex type for use when searching for a team to add users to. If a team is
           found but it needs moving out of the way because it has pre-existing different
           set of users to those being imported, renamed team is set and old_name is set
           otherwise matched team is set (i.e. either or)"""



    @dataclass
    class TeamMatchNoResult(TeamMatchResult):
        """See super type definition"""
        team_definition: ForgejoTeamDefinition

    @dataclass
    class TeamMatchExactResult(TeamMatchResult):
        """See super type definition"""
        matched_team: Team



    @dataclass
    class TeamMatchRenamedResult(TeamMatchResult):
        """See super type definition"""
        renamed_team: Team
        old_name: str



    @dataclass
    class TeamSearchResult:
        """Result of finding team to add members to"""
        team:Team
        is_new_team:bool
        usernames: set[str]



    @override
    def import_teams(self, migration_source: MigrationSource, organization: CanonicalOrganization):
        """Create Forgejo teams as a projection of canonical membership facts"""

        existing_forgejo_org_teams_map: dict[str, Team] # map[Team.name.lower() : Team]
        try:
            iter_all_teams = self.migration_dest.iter_forgejo_teams(
                org_name=organization.get_safe_username()
            )

            existing_forgejo_org_teams_map = {team.name.lower(): team
                                              for team in iter_all_teams
                                              if team.name}

        except IterativeFetchError as e:
            fg_print.error( "Failed to load existing teams for "
                           f"{organization.get_safe_username()}: {e}")
            return # try the next org

        # ---------------------------------------------------
        # STEP 1: derive "team intent" from membership facts
        # ---------------------------------------------------
        # rule: grouping is a *policy decision*, not a model assumption
        team_intent_map: dict[str, list[CanonicalOrganizationMembership]] = {}

        for m in organization.memberships:
            if m.username is None:
                continue

            key = m.access_level

            team_intent_map.setdefault(key, []).append(m)

        # This is potentially a saving in API calls due to the fuzzy mapping to teams
        team_members_cache: dict[int, set[str]] = {} # map[Team.id -> {member.username}]

        # ---------------------------------------------------
        # STEP 2: materialise Forgejo teams
        # ---------------------------------------------------

        for access_level, memberships in team_intent_map.items():

            team_result = self._get_forgejo_team_for_members_add(
                                migration_source=migration_source,
                                organization=organization,
                                access_level=access_level,
                                memberships=memberships,
                                existing_forgejo_org_teams_map=existing_forgejo_org_teams_map)
            if team_result is None:
                continue

            # ---------------------------------------------------
            # STEP 3: attach users (idempotent)
            # ---------------------------------------------------
            self.import_team_users_from_usernames(
                organization=organization,
                usernames=team_result.usernames,
                dest_team=team_result.team,
                team_members_cache=team_members_cache,
                is_new_team=team_result.is_new_team,
            )

        # ----------------------------------------------------------------------------
        # STEP 4: add any teams with no members defined in the teams config yaml file.
        # ----------------------------------------------------------------------------
        if self.migration_config.ADD_EMPTY_TEAMS_TO_ORGANIZATIONS:
            for possible_team in self.migration_dest.get_default_team_definitions():
                if possible_team.name.lower() in existing_forgejo_org_teams_map:
                    # already existed before this operation started
                    continue

                if possible_team.allow_empty:
                    self.migration_dest.forgejo_add_organization_team(
                        org_name=organization.get_safe_username(),
                        definition=possible_team,
                    )
                else:
                    fg_print.info(f"Skipped adding empty team {possible_team.name} because"
                                  " defined as empty not allowed in team definitions yaml file")



    def _get_forgejo_team_for_members_add(self,
                    migration_source:MigrationSource,
                    organization:CanonicalOrganization,
                    access_level:str,
                    memberships:list[CanonicalOrganizationMembership],
                    existing_forgejo_org_teams_map: dict[str, Team] # map[Team.name.lower() : Team]
                    ) -> TeamSearchResult | None:

        forgejo_team_definition = self._get_forgejo_team_definition(
            migration_source=migration_source,
            source_access_level=access_level,
            fuzzy=self.migration_config.IS_FUZZY_TEAMS_ALLOWED,
        )

        if forgejo_team_definition is None:
            fg_print.warning(
                f"No Forgejo team mapping for access level {access_level}"
            )
            return None

        usernames = {m.get_safe_username() for m in memberships if m.username}

        match_result = self._find_existing_org_team_for_new_users_matching(
            organization=organization,
            existing_forgejo_teams_map=existing_forgejo_org_teams_map,
            forgejo_team_definition=forgejo_team_definition,
            canonical_team_members=[
                CanonicalUser(username=u) for u in usernames
            ],
        )
        is_new_team = False
        if match_result is None:
            # Something went wrong trying to find existing org team
            fg_print.error("skipping import of access level due to match failure...")
            return None

        if isinstance(match_result, self.TeamMatchRenamedResult):
            # Team was moved, remove old mapping and add a new one
            del existing_forgejo_org_teams_map[match_result.old_name.lower()]
            existing_forgejo_org_teams_map[match_result.renamed_team.name.lower()] = \
                                                                match_result.renamed_team
            # Now no team matches the name we renamed from
            existing_team = None
        elif isinstance(match_result, self.TeamMatchExactResult):
            existing_team = match_result.matched_team
        elif isinstance(match_result, self.TeamMatchNoResult):
            is_new_team = True
            existing_team = None
            # Create that required and currently non existent team
            existing_team = self._safely_add_new_team(organization=organization,
                                                      team_definition=match_result.team_definition)
            if existing_team is None:
                return None
            existing_forgejo_org_teams_map[existing_team.name.lower()] = existing_team
        else:
            raise RuntimeError("Unhandled result type for function result TeamMatchResult")

        return self.TeamSearchResult(team=existing_team,
                                     is_new_team=is_new_team,
                                     usernames=usernames)



    def _safely_add_new_team(self,
                             organization:CanonicalOrganization,
                             team_definition:ForgejoTeamDefinition) -> Team | None:
        existing_team = self.migration_dest.forgejo_add_organization_team(
            org_name=organization.get_safe_username(),
            definition=team_definition,
        )

        if existing_team is None:
            fg_print.warning(f"Failed to create team {team_definition.name}")
            return None

        if not existing_team.name:
            fg_print.error(
                f"Created team returned without a name. Team Id = {existing_team.id}"
                f"for organization {organization.get_safe_username()}."
                " Skipping import of team users."
            )
            return None
        return existing_team


    @override
    def import_team_users_from_usernames(
            self,
            organization: CanonicalOrganization,
            usernames: set[str],
            dest_team: Team,
            team_members_cache: dict[int, set[str]], # map[Team.id -> {member.username}]
            is_new_team: bool,
        ):
        self.migration_dest.import_team_users_from_usernames(
                organization=organization,
                usernames=usernames,
                dest_team=dest_team,
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
                f"\nImporting collaborators for personal {source_repo.source_type}"
                f" {source_repo.name}..."
            )
        else:
            fg_print.info(
                f"\nImporting collaborators for shared {source_repo.source_type}"
                f" {source_repo.name}..."
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
        forgejo_repo_owner = self.migration_dest.resolve_forgejo_repo_owner(source_repo)
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

            # Get list of teams in the repository essentially.
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
                # get all the teams in the organization owning this repository
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

                    if len(member_usernames) == 0:
                        #if the team is empty....
                        if not self.migration_config.ADD_EMPTY_TEAMS_TO_REPOSITORIES:
                            # Don't add this one to the the repository unless user wanted
                            continue

                    if not self.migration_dest.forgejo_add_team_to_repository(
                        owner_username=forgejo_repo_owner.username,
                        repo_name=source_repo.get_safe_username(),
                        team_name=org_team.name,
                    ):
                        # Only add usernames if successfully added team.
                        affected_usernames = member_usernames.difference(all_team_usernames)
                        fg_print.warning("Team attachment failed. Users represented by this team"
                                         "will not be imported as individual collaborators because"
                                         "doing so would alter the intended authorization model"
                                         f"Affected users: {affected_usernames}")

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

        self.handle_missing_accessors(
                    migration_source=migration_source,
                    source_repo=source_repo,
                    repo_accessors=repo_accessors,
                    all_team_usernames=all_team_usernames,
                    existing_collaborator_ids=existing_collaborator_ids,
                )



    def handle_missing_accessors(
        self,
        migration_source: MigrationSource,
        source_repo: CanonicalRepo,
        repo_accessors: CanonicalRepoMemberships,
        all_team_usernames: set[str],
        existing_collaborator_ids: set[int],
    ):
        """Handle all those accessors not already added by way of membership to a Forgejo Team"""
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

            self.migration_dest.import_individual_user_collaborator(
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


    def _get_forgejo_team_definition(self,
                                     migration_source:MigrationSource,
                                     source_access_level:str,
                                     fuzzy:bool) -> ForgejoTeamDefinition | None:
        """Retrieves a ForgejoTeamDefinition, creating a new one and adding neccessary data to
           the maps as required"""
        # get forgejo team definition matching gitlab permission level
        repository_role : ForgejoRepositoryRole = migration_source.get_repository_role(
                                                    source_access_level=source_access_level)

        if repository_role.is_custom:
            nearest_repository_role = None
            if fuzzy:
                fg_print.warning(f"{migration_source.get_source_system_name()} Role:"
                                 f"Forgejo Team Mapping missing for {repository_role}."
                                  " Using fuzzy matching")
                nearest_repository_role = migration_source.get_nearest_repository_role(
                                source_access_level=source_access_level,
                                allow_downgrade=self.migration_config.ALLOW_FUZZY_AUTH_DOWNGRADE,
                                allow_upgrade=self.migration_config.ALLOW_FUZZY_AUTH_UPGRADE)
            # if it still isn't valid.
            if nearest_repository_role is None:
                return None
            # we now have a valid nearest_repository_role, lets create a mapping based
            # on the team referenced by that for our invalid one.
            self.migration_dest.add_team_mapping(map_from_role=repository_role,
                                                 to_role=nearest_repository_role)

        # now we definitely have a team mapped against this role, even if it is just a basic string
        return self.migration_dest.team_definitions[repository_role]



    def _find_existing_org_team_for_new_users_matching(self,
                        organization : CanonicalOrganization,
                        existing_forgejo_teams_map : dict[str,Team], # map[Team.name.lower() : Team]
                        forgejo_team_definition : ForgejoTeamDefinition,
                        canonical_team_members : list[CanonicalUser]) -> TeamMatchResult | None:

        matched_team = self._find_matching_team(existing_forgejo_teams_map, forgejo_team_definition)
        if matched_team is None:
            return self.TeamMatchNoResult(team_definition=forgejo_team_definition)

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
            fg_print.info(f"Pre-existing team members exactly match team being imported,"
                          " so reusing.\n"
                          f"Affected Forgejo Organization {organization.get_safe_username()}"
                          f" Team {matched_team.name}")
            return self.TeamMatchExactResult(matched_team=matched_team)

        if self.migration_config.USE_EXISTING_TEAMS:
            fg_print.warning("Pre-existing team users will be granted access to new repositories"
                             " that are created with access granted to this team.\n"
                             f"Affected Forgejo Organization {organization.get_safe_username()}"
                             f" Team {matched_team.name}, usernames: {existing_usernames}")
            return self.TeamMatchExactResult(matched_team=matched_team)

        result = self._rename_team_out_of_the_way(team = matched_team)

        return result


    def _rename_team_out_of_the_way(self,
                                    team: Team,
                                    ) -> TeamMatchRenamedResult | None:

        current_definition = ForgejoTeamDefinition.from_team(
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
            fg_print.debug(f"Success: Moved team with name {team.name} out of the way to allow "
                           "creation of new team with the same name for import. Renamed team "
                           f"to {updated_team.name}")
        else:
            fg_print.debug(f"Failed: Moved team with name {team.name} out of the way to allow "
                           "creation of new team with the same name for import.")

        if updated_team is not None:
            return self.TeamMatchRenamedResult(renamed_team=updated_team, old_name=team.name)

        # return self.TeamMatchExactResult(matched_team=team)
        return None # Team does exist, but we clearly didn't want to use it



    def _get_forgejo_role_definition(self, migration_source:MigrationSource,
                                     source_access_level:str,
                                     fuzzy:bool) -> ForgejoRolePermissionDefinition | None:
        """Retrieves a ForgejoRoleDefinition, creating a new one and adding neccessary data to the
           maps as required"""
        # get forgejo team definition matching gitlab permission level
        repository_role : ForgejoRepositoryRole = migration_source.get_repository_role(
                                                        source_access_level=source_access_level)

        if repository_role.is_custom:
            nearest_repository_role = None
            if fuzzy:
                fg_print.warning(f"{migration_source.get_source_system_name()} Role:Forgejo Team"
                                 f" Mapping missing for {repository_role}. Using fuzzy matching")

                nearest_repository_role = migration_source.get_nearest_repository_role(
                                source_access_level=source_access_level,
                                allow_downgrade=self.migration_config.ALLOW_FUZZY_AUTH_DOWNGRADE,
                                allow_upgrade=self.migration_config.ALLOW_FUZZY_AUTH_UPGRADE)

            # if it still isn't valid.
            if nearest_repository_role is None:
                return None
            # we now have a valid nearest_repository_role, lets create a mapping based on
            # the team referenced by that for our invalid one.
            self.migration_dest.add_role_mapping(map_from_role=repository_role,
                                                 to_existing_role=nearest_repository_role)

        # now we definitely have a team mapped against this role, even if it is just a basic string
        return self.migration_dest.role_definitions[repository_role]



    def _find_matching_team(self,
                        existing_forgejo_teams_map: dict[str, Team], # map[Team.name.lower() : Team]
                        forgejo_team_definition: ForgejoTeamDefinition,
    ) -> Team | None:

        team_key = forgejo_team_definition.name.lower()
        found = existing_forgejo_teams_map.get(
            team_key
        )
        if found is None:
            fg_print.debug(f"No existing team matching {team_key}"
                           f" found in set !{list(existing_forgejo_teams_map.keys())}")
        return found
