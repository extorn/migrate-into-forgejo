#!/usr/bin/env python3
#
# imports projects, users, groups, issues, labels, milestones, keys
# and collaborators from GitLab to Forgejo
#
"""
Usage: migrate.py [--debug] [--users] [--groups] [--projects] [--membership] [--all] [--notify]
       migrate.py --help

Migration script to import users, groups, projects, and group/user membership of projects from GitLab to Forgejo.

Options
  -h, --help    Show this screen
  --debug       show extra debug output
  --users       migrate users
  --groups      migrate groups
  --projects    migrate projects
  --membership  migrate project membership
  --all         migrate all
  --notify      send notification to users
"""
import os
import configparser

from docopt import docopt


from fg_migration.forgeo_types import ForgejoApiBuilder
from fg_migration.migration_source_type import MigrationSource
from fg_migration.config_types import ForgejoConfig, GitLabConfig, GitLabMigrationConfig, MigrationConfig
from fg_migration.forgjo import ForgejoDestination
from fg_migration.gitlab import GitLabApiBuilder, GitLabMigrationSource
from fg_migration.migrator import Migrator

from fg_migration import fg_print

SCRIPT_VERSION = "1.0.0-alpha.2"



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
    # control debug logging
    if args["debug"]:
        fg_print.IS_DEBUG=True

    fg_print.print_color(
        fg_print.Bcolors.HEADER, "---=== GitLab to Forgejo migration ===---"
    )
    fg_print.info(f"Version: {SCRIPT_VERSION}\n")
    

    gl_api_builder = GitLabApiBuilder(gitlab_config)
    gl_api = gl_api_builder.build_gitlab_api_client()
    gl_conn_success = gl_api_builder.test_gitlab_connection(gl_api)

    fg_api_builder = ForgejoApiBuilder(forgejo_config=forgejo_config)
    fg_api = fg_api_builder.build_forgejo_api_client()
    fg_conn_success= fg_api_builder.test_forgejo_connection(fg_api=fg_api)

    if not (gl_conn_success and fg_conn_success):
        os.sys.exit()
    

    migration_source : MigrationSource = GitLabMigrationSource(gitlab_api=gl_api, gitlab_config=gitlab_config, gitlab_migration_config=migration_config_gitlab)
    migration_dest : ForgejoDestination = ForgejoDestination(fg_api=fg_api, forgejo_config=forgejo_config)
    migrator = Migrator(migration_config=migration_config,
                        migration_source=migration_source, 
                        migration_dest=migration_dest,
                        fg_api_builder=fg_api_builder)

    # IMPORT System Users
    if args["users"] or args["all"]:
        migrator.import_users()
    # IMPORT Organizations and Teams (Groups and their member Users)
    if args["groups"] or args["all"]:
         # Note, import_groups uses the gitlab projects object because they're intrinsically linked really.
        migrator.import_organizations()
    # IMPORT Repositories (Projects) AND OR Collaborators (Memberships of Projects)
    if args["projects"] or args["membership"] or args["all"]:
        migrator.import_repos(import_repo_content=args["projects"])
    # IMPORT NOTHING ?
    if (
        not args["users"]
        and not args["groups"]
        and not args["projects"]
        and not args["membership"]
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
