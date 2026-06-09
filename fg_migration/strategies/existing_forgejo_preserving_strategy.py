"""contains the ExistingForgejoPreservingStrategy class"""
from copy import deepcopy
from typing import override

from pyforgejo import Team

from fg_migration.adapters.forgeo_types import ForgejoTeamDefinition, IterativeFetchError
from fg_migration.core.canonical_types import CanonicalOrganization, CanonicalUser
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.strategies.access_level_mapping_strategy import AccessLevelAccessMappingStrategy
from fg_migration.utils import fg_print


class ExistingForgejoPreservingStrategy(AccessLevelAccessMappingStrategy):
    """
    Migration strategy that preserves all pre-existing Forgejo structures.

    This strategy creates new teams only when they do not already exist.
    Existing Forgejo teams are never modified, renamed, repurposed, or
    assumed to represent source-system groups.

    When a source group conflicts with an existing Forgejo team name, a
    new migration-specific team is created instead.

    Example
    -------
    Source-system groups:

        Maintainers
            ├── alice
            └── bob

        Developers
            └── charlie

        Guests
            └── dave

    Existing Forgejo organization:

        Maintainers
            └── legacy-user

    Because the existing team is preserved, the migration creates:

        Maintainers_migrated
            ├── alice
            └── bob

        Developers
            └── charlie

        Guests
            └── dave

    rather than modifying the existing Maintainers team.

    Repository access is then attached to the migration-created teams:

        project-a
            ├── Maintainers_migrated
            ├── Developers
            └── Guests

    Existing Forgejo teams remain untouched.

    Behaviour:
    - Never modifies existing Forgejo teams.
    - Never removes existing team members.
    - Never repurposes existing teams.
    - Creates migration-specific teams on naming conflict.
    - Preserves empty source groups as empty teams.
    - Grants repository access through migration-created teams.
    - Suitable for migrations into active Forgejo environments.
    """

    TEAM_SUFFIX = "_migrated"

     # old_name.lower()) to new definition
    org_remapped_teams_cache : dict[str,ForgejoTeamDefinition] = {}

    @override
    def import_teams(self, migration_source: MigrationSource, organization: CanonicalOrganization):
        assert len(self.org_remapped_teams_cache) == 0 # check not inadvertently changing this
        super().import_teams(migration_source=migration_source, organization=organization)
        self.org_remapped_teams_cache.clear() # ensure it is ready for the next organization


    @override
    def _find_existing_org_team_for_new_users_matching(
        self,
        organization: CanonicalOrganization,
        existing_forgejo_teams_map: dict[str, Team], # map[Team.name.lower() : Team]
        forgejo_team_definition:ForgejoTeamDefinition,
        canonical_team_members: list[CanonicalUser],
    ) -> AccessLevelAccessMappingStrategy.TeamMatchResult | None:
        fg_print.error("Running new code")

        # NOTE: this override is called ONLY while materializing teams, NOT adding collaborators

        remapped = self.org_remapped_teams_cache.get(forgejo_team_definition.name.lower())
        if remapped is not None:
            # skip the rest of the logic in this function (we know there is a match).
            matched_team = self._find_matching_team(
                existing_forgejo_teams_map,
                remapped,
            )
            fg_print.debug(f"Using remapped team {remapped.name} in lieu"
                           f" of {forgejo_team_definition.name}  in "
                           f"org {organization.get_safe_username()}")
            return remapped

        #
        # First look for an exact-name team.
        #
        matched_team = self._find_matching_team(
            existing_forgejo_teams_map,
            forgejo_team_definition,
        )

        if matched_team is None:
            # No team exists, can create a team as desired
            fg_print.debug(f"No existing team {forgejo_team_definition.name} in "
                           f"org {organization.get_safe_username()}, safe to use")
            return self.TeamMatchNoResult(team_definition=forgejo_team_definition)

        # get a list of all users in the existing team
        # (don't yet know if we'll be able to use it)
        try:
            existing_usernames = {
                member.login
                for member in self.migration_dest
                    .iter_forgejo_team_members(matched_team)
                if member.login is not None
            }
        except IterativeFetchError:
            return None # signals error

        # get a list of all users that are expected to be a member of this team
        # that was found (don't yet know if we'll be able to use it)
        imported_usernames = {
            user.get_safe_username()
            for user in canonical_team_members
        }

        #
        # Exact membership match -> reuse.
        #
        if existing_usernames == imported_usernames:
            fg_print.info(
                "Pre-existing team members exactly match team "
                "being imported, so reusing.\n"
                f"Affected Forgejo Organization "
                f"{organization.get_safe_username()} "
                f"Team {matched_team.name}"
            )

            return self.TeamMatchExactResult(matched_team=matched_team)

        #
        # Explicitly configured to use existing teams.
        # really this overrides the entire point of this strategy!
        # Might make more sense to instead, use this as a flag to
        # block or allow using teams with identical set of users to
        # those being imported. (see block above)
        if self.migration_config.USE_EXISTING_TEAMS is True:
            fg_print.warning(
                "Pre-existing team users will be granted "
                "access to new repositories that are created "
                "with access granted to this team.\n"
                f"Affected Forgejo Organization "
                f"{organization.get_safe_username()} "
                f"Team {matched_team.name}, "
                f"usernames: {existing_usernames}"
            )

            return self.TeamMatchExactResult(matched_team=matched_team)

        #
        # Conflict:
        # Keep existing team untouched.
        # Reuse a migrated team (from a previous migration) if one already exists
        # AND the team members exactly match those to add
        existing_team_names = set(existing_forgejo_teams_map.keys())
        migrated_name = self._generate_safe_team_name(forgejo_team_definition.name,
                                                      existing_names=existing_team_names)

        migrated_team = existing_forgejo_teams_map.get(migrated_name.lower())

        if migrated_team is not None:

            try:
                migrated_usernames = {
                    member.login
                    for member in self.migration_dest
                        .iter_forgejo_team_members(
                            migrated_team
                        )
                    if member.login is not None
                }
            except IterativeFetchError:
                return None

            if migrated_usernames == imported_usernames:
                fg_print.info(
                    f"Reusing migrated team "
                    f"{migrated_team.name}"
                )

                return self.TeamMatchExactResult(
                    matched_team=migrated_team
                )
            #TODO is it okay to & worth it to add this to the cache?
        else:
            #
            # Force creation of a new team.
            #
            updated_definition = deepcopy(forgejo_team_definition)
            updated_definition.name = migrated_name
            fg_print.info(f"Created migration team {migrated_name} "
                          f"to avoid clash with {forgejo_team_definition.name}")
            # update the cache so we get a headstart next time.
            self.org_remapped_teams_cache[forgejo_team_definition.name.lower()] = updated_definition
            return self.TeamMatchNoResult(team_definition=updated_definition)

    @override
    def _generate_safe_team_name(
        self,
        source_name: str,
        existing_names: set[str],
    ) -> str:

        migrated_name = (
            f"{source_name}{self.TEAM_SUFFIX}"
        )

        if migrated_name.lower() not in existing_names:
            return migrated_name

        #
        # Reuse the same migrated name whenever possible.
        # Only fall back to numbered names if there is an
        # actual collision.
        #
        counter = 1

        while True:

            candidate = (
                f"{source_name}"
                f"{self.TEAM_SUFFIX}_{counter}"
            )

            if candidate.lower() not in existing_names:
                return candidate

            counter += 1
