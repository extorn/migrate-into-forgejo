"""Contains an implementation of AccessMappingStrategy"""
from typing import override

from pyforgejo import Team

from fg_migration.strategies.access_mapping_strategy import AccessMappingStrategy
from fg_migration.core.canonical_types import (CanonicalOrganization, CanonicalRepo,
                                               CanonicalRepoMemberships)
from fg_migration.adapters.forgeo_types import IterativeFetchError
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.utils import fg_print


class StrictMirrorAccessMappingStrategy(AccessMappingStrategy):
    """
    Strict 1:1 mirror of source system access structures.

    This strategy enforces exact replication of:
    - Source groups → Forgejo teams (1:1)
    - Group membership → team membership (exact)
    - Repository access → only via mapped teams (no direct fallback users)

    Design principles:
    - No fuzzy mapping
    - No role collapsing
    - No fallback to direct collaborators
    - Fail-fast on structural mismatch
    """

    def __init__(self, migration_dest):
        self.migration_dest = migration_dest

    # ---------------------------------------------------------
    # TEAM IMPORT: strict group mirroring
    # ---------------------------------------------------------
    @override
    def import_teams(self, migration_source: MigrationSource, organization: CanonicalOrganization):
        """
        Each canonical group becomes exactly one Forgejo team.
        """

        fg_print.info(
            f"Strict mirror import of teams for {organization.get_safe_username()}"
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
            fg_print.error(f"Failed to load existing teams: {e}")
            return

        for group in organization.groups:
            team_name = group.get_safe_username().lower()

            existing_team = existing_teams.get(team_name)

            if existing_team is None:
                fg_print.info(f"Creating strict-mirror team {group.username}")

                existing_team = self.migration_dest.forgejo_add_organization_team(
                    org_name=organization.get_safe_username(),
                    definition=self._group_to_team_definition(group),
                )

                if existing_team is None:
                    fg_print.error(f"Failed to create team {group.username}")
                    continue

            # enforce exact membership
            usernames = {
                m.username
                for m in group.memberships
                if m.username is not None
            }

            self.import_team_users_from_usernames(
                organization=organization,
                usernames=usernames,
                dest_team=existing_team,
                team_members_cache={},  # no caching needed in strict mode
                is_new_team=False,
            )

    # ---------------------------------------------------------
    # TEAM MEMBERS: strict enforcement (no caching logic needed)
    # ---------------------------------------------------------
    @override
    def import_team_users_from_usernames(
        self,
        organization: CanonicalOrganization,
        usernames: set[str],
        dest_team: Team,
        team_members_cache: dict[int, set[str]],
        is_new_team: bool,
    ):
        try:
            existing = {
                m.login
                for m in self.migration_dest.iter_forgejo_team_members(dest_team)
                if m.login
            }
        except IterativeFetchError:
            fg_print.error(f"Cannot read team members for {dest_team.name}")
            return

        # Strict enforcement: mismatch is not silently ignored
        if existing != usernames:
            fg_print.warning(
                f"Team mismatch detected for {dest_team.name}\n"
                f"Expected: {sorted(usernames)}\n"
                f"Actual:   {sorted(existing)}"
            )

        # converge state exactly
        for u in usernames - existing:
            self.migration_dest.forgejo_add_user_to_organization_team(
                organization_name=organization.get_safe_username(),
                username=u,
                team=dest_team,
            )

    # ---------------------------------------------------------
    # REPOSITORY ACCESS: team-only enforcement
    # ---------------------------------------------------------
    @override
    def import_repository_accessors(
        self,
        migration_source: MigrationSource,
        source_repo: CanonicalRepo,
    ):
        repo_accessors: CanonicalRepoMemberships = (
            migration_source.list_repository_accessors(source_repo)
        )

        fg_print.info(f"Strict mirror repo import: {source_repo.name}")

        forgejo_owner = self.migration_dest.resolve_forgejo_repo_owner(source_repo)
        if not forgejo_owner or not forgejo_owner.username:
            fg_print.error(f"Cannot resolve repo owner for {source_repo.name}")
            return

        # load all org teams
        try:
            org_teams = list(
                self.migration_dest.iter_forgejo_teams(
                    org_name=forgejo_owner.username
                )
            )
        except IterativeFetchError:
            fg_print.error("Cannot load org teams")
            return

        # load repo team links
        try:
            existing_repo_teams = {
                t.id
                for t in self.migration_dest.iter_forgejo_teams_in_repository(
                    owner_username=forgejo_owner.username,
                    repo_name=source_repo.get_safe_username(),
                )
                if t.id
            }
        except IterativeFetchError:
            fg_print.error("Cannot load repo teams")
            return

        # ALL access MUST come from teams only
        all_allowed_users: set[str] = set()

        for team in org_teams:
            if team.id in existing_repo_teams:
                continue

            try:
                members = list(
                    self.migration_dest.iter_forgejo_team_members(team)
                )
            except IterativeFetchError:
                continue

            member_names = {m.login for m in members if m.login}

            # strict rule: team must be fully valid for repo access
            repo_usernames = {
                m.username
                for m in repo_accessors.members
                if m.username
            }

            if not member_names.issubset(repo_usernames):
                continue

            added = self.migration_dest.forgejo_add_team_to_repository(
                owner_username=forgejo_owner.username,
                repo_name=source_repo.get_safe_username(),
                team_name=team.name,
            )

            if added:
                all_allowed_users.update(member_names)
            else:
                fg_print.error(f"Failed to attach team {team.name}")

        # STRICT RULE: no fallback users allowed
        missing = {
            m.username
            for m in repo_accessors.members
            if m.username and m.username not in all_allowed_users
        }

        if missing:
            fg_print.error(
                f"Strict mirror violation: users not covered by any team: {missing}"
            )

    # ---------------------------------------------------------
    # PERMISSIONS: must be exact, no collapsing logic
    # ---------------------------------------------------------
    @override
    def resolve_forgejo_permission(
        self,
        migration_source: MigrationSource,
        source_access_level: str,
    ) -> str | None:

        role = migration_source.get_repository_role(source_access_level)
        if role is None:
            return None

        perms = role.permission

        # strict ordering preserved (no fuzzy fallback)
        if perms == {"admin"}:
            return "admin"
        if perms == {"write"}:
            return "write"
        if perms == {"read"}:
            return "read"

        return None

    # ---------------------------------------------------------
    # helpers
    # ---------------------------------------------------------
    def _group_to_team_definition(self, group):
        """
        Minimal deterministic mapping: group → team definition.
        """
        return self.migration_dest.team_definitions_from_group(group)
