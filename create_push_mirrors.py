#!/usr/bin/env python3
#
# pylint: disable=line-too-long
"""
Usage: create_push_mirrors.py [--to-forgejo] [--to-gitlab] [--all] [--limit LIMIT] (--create | --delete)
       create_push_mirrors.py --help

Create push mirrors from GitLab to Forgejo and vice versa.

Options
  -h, --help     Show this screen
  --to-forgejo   create mirrors from GitLab to Forgejo
  --to-gitlab    create mirrors from Forgejo to GitLab
  --all          create mirrors in both directions
  --limit LIMIT  limit number of projects [default: 100000]
  --create       create mirrors
  --delete       delete mirrors
"""
# pylint: enable=line-too-long

import os
import configparser

from docopt import docopt

from fg_migration.utils import fg_print
from fg_migration.core.config_types import ForgejoConfig, GitLabConfig
from fg_migration.adapters.forgeo_types import ForgejoApiBuilder
from fg_migration.adapters.source_gitlab import GitLabApiBuilder
from fg_migration.services.push_mirror_creator import PushMirrorCreator

SCRIPT_VERSION = "0.2"

#######################
# CONFIG SECTION START
#######################
if not os.path.exists(".migrate.ini"):
    fg_print.error("Please create .migrate.ini as explained in the README!")
    os.sys.exit()


config = configparser.RawConfigParser()
config.read(".migrate.ini")
forgejo_config = ForgejoConfig.from_config(config=config)
gitlab_config = GitLabConfig.from_config(config=config)

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
        fg_print.Bcolors.HEADER,
        "---=== GitLab <-> Forgejo Push Mirror Management ===---",
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


    #
    # Load projects
    #

    limit = int(args["limit"])

    if limit != 100000:
        projects = gl_api.projects.list(
            all=False,
            per_page=limit,
            page=1,
        )
    else:
        projects = gl_api.projects.list(get_all=True)

    fg_print.info(f"Found {len(projects)} projects")

    #
    # Execute actions
    #

    pmc = PushMirrorCreator(fg_api=fg_api, forgejo_config=forgejo_config, gitlab_config=gitlab_config)

    if args["create"]:

        fg_print.info("Creating mirrors")

        if args["to-forgejo"] or args["all"]:
            pmc.to_forgejo(projects)

        if args["to-gitlab"] or args["all"]:
            pmc.to_gitlab(projects)

    elif args["delete"]:

        fg_print.info("Deleting mirrors")

        if args["to-forgejo"] or args["all"]:
            pmc.delete_to_forgejo(projects)

        if args["to-gitlab"] or args["all"]:
            pmc.delete_to_gitlab(projects)

    else:
        fg_print.error(
            "Please specify --create or --delete"
        )
        os.sys.exit()

    #
    # Summary
    #

    err_count = fg_print.GLOBAL_ERROR_COUNT

    if err_count == 0:
        fg_print.success(
            "\nMirror management finished with no errors!"
        )
    else:
        fg_print.error(
            f"\nMirror management finished with "
            f"{err_count} errors!"
        )

        fg_print.info("Failed elements:")

        print(*fg_print.GLOBAL_ERROR_LIST, sep="\n")


if __name__ == "__main__":
    main()
