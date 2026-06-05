#!/usr/bin/env python3
#
"""
Usage: purge_forgejo.py [--orgs-repos] [--orgs] [--user-repos] [--users]
       purge_forgejo.py --help

Purge repositories and/or users from Forgejo.

Options
  -h, --help    Show this screen
  --orgs-repos  delete organizations repositories
  --orgs        delete organizations
  --user-repos  delete user repositories
  --users       delete users
  --purge       delete all data for the user
"""
import os
import configparser
from pyforgejo import PyforgejoApi
from docopt import docopt
from click import confirm
from fg_migration import fg_print
from fg_migration.config_types import ForgejoConfig
from fg_migration.fg_purger import ForgejoPurger
from fg_migration.forgeo_types import ForgejoApiBuilder

SCRIPT_VERSION = "1.0.0-alpha.1"

#######################
# CONFIG SECTION START
#######################
if not os.path.exists(".migrate.ini"):
    fg_print.error("Please create .migrate.ini as explained in the README!")
    os.sys.exit()

config = configparser.RawConfigParser()
config.read(".migrate.ini")
forgejo_config = ForgejoConfig.from_config(config=config)
#######################
# CONFIG SECTION END
#######################


def ask_confirmation() -> None:
        """Ask for confirmation before proceeding"""
        fg_print.info("This script deletes your data. Use it with a grain of salt!")
        choice = confirm("Do you want continue?")
        if not choice:
            fg_print.info("OK. See you next time!")
            os.sys.exit()



if __name__ == "__main__":

    args = docopt(__doc__)
    # control debug logging
    if args["--debug"]:
        fg_print.IS_DEBUG=True
    
    if not any([args["--orgs-repos"], args["--orgs"], 
                args["--user-repos"], args["--current-user-repos"], 
                args["--users"]]):
        fg_print.error("Please specify what to delete! You can use --help for more information.")
        os.sys.exit()


    ask_confirmation()

    fg_api_builder = ForgejoApiBuilder(forgejo_config=forgejo_config)
    fg_api = fg_api_builder.build_forgejo_api_client()
    fg_conn_success= fg_api_builder.test_forgejo_connection(fg_api=fg_api)
    if not fg_conn_success:
        os.sys.exit()
    purger = ForgejoPurger(fg_api=fg_api, forgejo_config=forgejo_config)


    if args["--orgs-repos"]:
        purger.del_orgs_repos()
    if args["--user-repos"]:
        purger.del_all_user_repos()
    if args["--current-user-repos"]:
        purger.del_current_user_repos()
    if args["--orgs"]:
        purger.del_orgs()
    if args["--users"]:
        PURGE_OPT = "true" if args["--purge"] else "false"
        purger.del_users(PURGE_OPT)
    
    purger.close()

    ERR_COUNT = fg_print.GLOBAL_ERROR_COUNT
    if ERR_COUNT == 0:
        fg_print.success("\nMigration finished with no errors!")
    else:
        fg_print.error(f"\nMigration finished with {ERR_COUNT} errors!")
        print("Failed elements:")
        print(*fg_print.GLOBAL_ERROR_LIST, sep="\n")
