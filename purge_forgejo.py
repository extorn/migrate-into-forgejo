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
"""
import os
import configparser
import requests
from docopt import docopt
from click import confirm
from fg_migration import fg_print

#######################
# CONFIG SECTION START
#######################
if not os.path.exists(".migrate.ini"):
    fg_print.error("Please create .migrate.ini as explained in the README!")
    os.sys.exit()

config = configparser.RawConfigParser()
config.read(".migrate.ini")
GITLAB_URL = config.get("migrate", "gitlab_url")
GITLAB_TOKEN = config.get("migrate", "gitlab_token")
GITLAB_ADMIN_USER = config.get("migrate", "gitlab_admin_user")
GITLAB_ADMIN_PASS = config.get("migrate", "gitlab_admin_pass")
FORGEJO_URL = config.get("migrate", "forgejo_url")
FORGEJO_API_URL = f"{FORGEJO_URL}/api/v1"
FORGEJO_HOST = FORGEJO_URL.split("/")[-1]
FORGEJO_USER = config.get("migrate", "forgejo_admin_user")
FORGEJO_PASSWORD = config.get("migrate", "forgejo_admin_pass")
FORGEJO_TOKEN = config.get("migrate", "forgejo_token")
FORGEJO_PREFIX_URL = f"https://{FORGEJO_USER}:{FORGEJO_PASSWORD}@{FORGEJO_HOST}"
session = requests.Session()
session.auth = (FORGEJO_USER, FORGEJO_PASSWORD)
#######################
# CONFIG SECTION END
#######################


def ask_confirmation() -> None:
    """Ask for confirmation for the prefix to use"""
    fg_print.info(
        "This script should be used with a grain of salt as it will delete all your data!"
    )
    fg_print.warning(
        "You need to run it multiple times, because requests pagination is not implemented"
    )
    choice = confirm("Do you want continue?")
    if not choice:
        fg_print.info("OK. See you next time!")
        os.sys.exit()


def del_orgs_repos() -> None:
    """Delete all repositories in all organizations"""
    orgs = session.get(f"{FORGEJO_API_URL}/orgs").json()
    org_names = [org["name"] for org in orgs]
    for org in org_names:
        repos = session.get(f"{FORGEJO_API_URL}/orgs/{org}/repos").json()
        for repo in repos:
            repo_name = repo["name"]
            url = f"{FORGEJO_API_URL}/repos/{org}/{repo_name}"
            response: requests.Response = session.delete(url, timeout=20)
            if response.ok:
                fg_print.info(f"Repository {repo_name} deleted on Forgejo for {org}")
            else:
                fg_print.error(
                    f"Error deleting repository {repo_name} on Forgejo for {org}"
                )


def del_orgs() -> None:
    """Delete all organizations"""
    orgs = session.get(f"{FORGEJO_API_URL}/orgs").json()
    org_names = [org["name"] for org in orgs]
    for orgs in org_names:
        url = f"{FORGEJO_API_URL}/orgs/{orgs}"
        response: requests.Response = session.delete(url, timeout=20)
        if response.ok:
            fg_print.info(f"Organization {orgs} deleted on Forgejo")
        else:
            fg_print.error(f"Error deleting organization {orgs} on Forgejo")


def del_user_repos() -> None:
    """Delete all user repositories"""
    user_repos_dict = session.get(f"{FORGEJO_API_URL}/user/repos").json()
    user_repos = [user_repo["full_name"] for user_repo in user_repos_dict]
    for repo in user_repos:
        url = f"{FORGEJO_API_URL}/repos/{repo}"
        response: requests.Response = session.delete(url, timeout=20)
        if response.ok:
            fg_print.info(f"Repository {repo} deleted on Forgejo")
        else:
            fg_print.error(f"Error deleting repository {repo} on Forgejo")


def del_users() -> None:
    """Delete all users"""
    users_dict = session.get(f"{FORGEJO_API_URL}/admin/users").json()
    user_names = [user["username"] for user in users_dict]
    for user in user_names:
        url = f"{FORGEJO_API_URL}/admin/users/{user}"
        response: requests.Response = session.delete(url, timeout=20)
        if response.ok:
            fg_print.info(f"User {user} deleted on Forgejo")
        else:
            fg_print.error(f"Error deleting user {user} on Forgejo")


if __name__ == "__main__":

    args = docopt(__doc__)
    ask_confirmation()
    if args["--orgs-repos"]:
        del_orgs_repos()
    if args["--orgs"]:
        del_orgs()
    if args["--user-repos"]:
        del_user_repos()
    if args["--users"]:
        del_users()

    session.close()
    ERR_COUNT = fg_print.GLOBAL_ERROR_COUNT
    if ERR_COUNT == 0:
        fg_print.success("\nMigration finished with no errors!")
    else:
        fg_print.error(f"\nMigration finished with {ERR_COUNT} errors!")
