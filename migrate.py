#!/usr/bin/env python3
#
# imports projects, users, groups, issues, labels, milestones, keys
# and collaborators from GitLab to Forgejo
#
"""
Usage: migrate.py [--users] [--groups] [--projects] [--all] [--notify]
       migrate.py --help

Migration script to import projects, users, groups, from GitLab to Forgejo.

Options
  -h, --help  Show this screen
  --users     migrate users
  --groups    migrate groups
  --projects  migrate projects
  --all       migrate all
  --notify    send notification to users
"""
import os
import configparser
import typing

from docopt import docopt


from fg_migration.migration_source_type import MigrationSource
from fg_migration.config_types import ForgejoConfig, GitLabConfig, GitLabMigrationConfig, MigrationConfig
from fg_migration.forgjo import ForgejoMigrator
from fg_migration.gitlab import GitLabMigrationSource
from fg_migration.migrator import Migrator

from fg_migration import fg_print
from fg_migration.utils import _build_forgejo_api_client, _build_gitlab_api_client, _test_forgejo_connection

SCRIPT_VERSION = "1.0.0-alpha.1"



#######################
# CONFIG SECTION START
#######################
if not os.path.exists(".migrate.ini"):
    fg_print.info("Please create .migrate.ini as explained in the README!")
    os.sys.exit()

config = configparser.RawConfigParser()
config.read(".migrate.ini")
migration_config = MigrationConfig.from_config(config=config)
forgejo_config = ForgejoConfig.from_config(config=config)
gitlab_config = GitLabConfig.from_config(config=config)
migration_config_gitlab = GitLabMigrationConfig.from_config(config=config)

#######################
# CONFIG SECTION END
#######################


def main():
    """Main function"""
    _args = docopt(__doc__)
    args = {k.replace("--", ""): v for k, v in _args.items()}

    fg_print.print_color(
        fg_print.Bcolors.HEADER, "---=== GitLab to Forgejo migration ===---"
    )
    fg_print.info(f"Version: {SCRIPT_VERSION}\n")
    

    gl = _build_gitlab_api_client(gitlab_config)

    fg = _build_forgejo_api_client(forgejo_config)

    _test_forgejo_connection(fg_api=fg)
    

    migration_source : MigrationSource = GitLabMigrationSource(gitlab_api=gl, gitlab_config=gitlab_config, gitlab_migration_config=migration_config_gitlab)
    migration_dest : ForgejoMigrator = ForgejoMigrator(fg_api=fg, forgejo_config=forgejo_config)
    migrator = Migrator(migration_config=migration_config,
                        migration_source=migration_source, 
                        migration_dest=migration_dest)

    # IMPORT System users
    if args["users"] or args["all"]:
        migrator.import_users()
    # IMPORT Organizations
    if args["groups"] or args["all"]:
         # Note, import_groups uses the gitlab projects object because they're intrinsically linked really.
        migrator.import_organizations()
    # IMPORT Repositories
    if args["projects"] or args["all"]:
        migrator.import_repos()
    # IMPORT NOTHING ?
    if (
        not args["users"]
        and not args["groups"]
        and not args["projects"]
        and not args["all"]
    ):
        fg_print.info("")
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




if __name__ == "__main__":
    main()
