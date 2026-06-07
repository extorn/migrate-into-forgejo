from typing import override

from fg_migration.adapters.forgeo_types import IterativeFetchError
from fg_migration.core.canonical_types import CanonicalOrganization
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.strategies.strict_access_level_mapping_strategy import StrictMirrorAccessMappingStrategy
from fg_migration.utils import fg_print


class ExistingForgejoPreservingStrategy(StrictMirrorAccessMappingStrategy):
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

    @override
    def import_teams(
        self,
        migration_source: MigrationSource,
        organization: CanonicalOrganization,
    ):

        fg_print.info(
            f"Preserving existing Forgejo teams for "
            f"{organization.get_safe_username()}"
        )

        try:
            existing_teams = {
                t.name.lower(): t
                for t in self.migration_dest.iter_forgejo_teams(
                    org_name=organization.get_safe_username()
                )
                if t.name
            }

        except IterativeFetchError as e:
            fg_print.error(
                f"Failed to load existing teams: {e}"
            )
            return

        for group in organization.groups:

            base_name = group.get_safe_username()

            team_name = self._generate_safe_team_name(
                base_name,
                existing_names=set(existing_teams.keys()),
            )

            if team_name.lower() in existing_teams:
                existing_team = existing_teams[
                    team_name.lower()
                ]

            else:

                definition = self._group_to_team_definition(
                    group
                )

                definition.name = team_name

                fg_print.info(
                    f"Creating migration team {team_name}"
                )

                existing_team = (
                    self.migration_dest
                    .forgejo_add_organization_team(
                        org_name=organization.get_safe_username(),
                        definition=definition,
                    )
                )

                if existing_team is None:
                    fg_print.error(
                        f"Failed to create team "
                        f"{team_name}"
                    )
                    continue

                existing_teams[
                    team_name.lower()
                ] = existing_team

            usernames = {
                m.username
                for m in group.memberships
                if m.username
            }

            self.import_team_users_from_usernames(
                organization=organization,
                usernames=usernames,
                dest_team=existing_team,
                team_members_cache={},
                is_new_team=False,
            )

    def _generate_safe_team_name(
        self,
        source_name: str,
        existing_names: set[str],
    ) -> str:

        if source_name.lower() not in existing_names:
            return source_name

        candidate = (
            f"{source_name}{self.TEAM_SUFFIX}"
        )

        if candidate.lower() not in existing_names:
            return candidate

        counter = 1

        while True:

            candidate = (
                f"{source_name}"
                f"{self.TEAM_SUFFIX}_{counter}"
            )

            if candidate.lower() not in existing_names:
                return candidate

            counter += 1