"""Contains an implementation of AccessMappingStrategy"""
from typing import override

from pyforgejo import User

from fg_migration.core.canonical_types import (CanonicalRepo,
                                               CanonicalRepoMemberships)
from fg_migration.core.migration_source_type import MigrationSource
from fg_migration.strategies.strict_access_level_mapping_strategy import StrictMirrorAccessMappingStrategy
from fg_migration.utils import fg_print

class HybridMirrorFallbackStrategy(StrictMirrorAccessMappingStrategy):
    """
    Group-preserving migration strategy with collaborator fallback.

    This strategy attempts to preserve source-system group structure by
    creating Forgejo teams and granting repository access through those
    teams whenever possible.

    Unlike StrictMirrorAccessMappingStrategy, repository access is never
    allowed to fail because of a team mismatch. Users that cannot be
    represented through a mapped team are imported as direct collaborators.

    Example
    -------
    Given source-system groups:

        Maintainers
            ├── alice
            └── bob

        Developers
            └── charlie

        Guests
            └── dave

        Auditors
            └── <no members>

    the following Forgejo organization structure is created:

        Maintainers
            ├── alice
            └── bob

        Developers
            └── charlie

        Guests
            └── dave

        Auditors
            └── <no members>

    For repository "project-a", team access is preferred:

        project-a
            ├── Maintainers
            ├── Developers
            ├── Guests
            └── Auditors

    If a repository accessor cannot be represented by a team:

        project-a accessors:
            alice
            bob
            charlie
            contractor123

    then the missing user is added directly:

        project-a
            ├── Maintainers
            ├── Developers
            ├── Guests
            └── contractor123 (direct collaborator)

    Behaviour:
    - Mirrors source groups to Forgejo teams.
    - Preserves empty groups as empty teams.
    - Grants repository access through teams whenever possible.
    - Falls back to direct collaborators for uncovered users.
    - Reports structural mismatches but does not fail migration.
    - Prioritises migration completeness over exact structural fidelity.
    """


    @override
    def handle_missing_accessors(self,
                                 migration_source: MigrationSource,
                                 source_repo:CanonicalRepo,
                                 repo_accessors:CanonicalRepoMemberships,
                                 all_allowed_users:set[User]):
        all_allowed_usernames = {m.login for m in all_allowed_users}
        all_allowed_user_ids = {m.id for m in all_allowed_users}
        direct_users = [
            m
            for m in repo_accessors.members
            if m.username and m.username not in all_allowed_usernames
        ]

        for membership in direct_users:

            perm = self.resolve_forgejo_permission(
                migration_source,
                membership.access_level,
            )

            if perm is None:
                fg_print.warning(
                    f"No permission mapping for {membership.username}"
                )
                continue

            self.migration_dest.import_individual_user_collaborator(
                existing_collaborator_ids=all_allowed_user_ids,
                accessor=membership,
                source_repo=source_repo,
                forgejo_permissions=perm,
            )
