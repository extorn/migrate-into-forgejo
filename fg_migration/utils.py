
from dataclasses import asdict
import os
import re
import typing

import gitlab
from pyforgejo import PyforgejoApi
import pyforgejo
import requests
from httpx import Client as HttpxClient

import gitlab  # pip install python-gitlab
import gitlab.v4.objects

from fg_migration import fg_print
from fg_migration.config_types import ForgejoConfig, GitLabConfig


def diff_dataclasses(before, after) -> dict:
    before_dict = asdict(before)
    after_dict = asdict(after)

    diff = {}

    for key in before_dict.keys() | after_dict.keys():
        if before_dict.get(key) != after_dict.get(key):
            diff[key] = {
                "before": before_dict.get(key),
                "after": after_dict.get(key),
            }

    return diff



def name_clean(name):
    """Cleans a name for usage in Forgejo - names can be used as identifiers,
       so this is necessary for usernames, organization names, repo names, team names etc"""
    new_name = name.replace(" ", "_")
    new_name = re.sub(r"[^a-zA-Z0-9_\.-]", "-", new_name)

    if new_name.lower() == "plugins":
        return f"{new_name}-user"

    return new_name


def _build_gitlab_api_client(config: GitLabConfig) -> gitlab.Gitlab:
    session = requests.Session()
    # add client authentication if cert and key are provided in the config
    if(config.GITLAB_CLIENT_AUTH_CERT != None and config.GITLAB_CLIENT_AUTH_KEY != None):
        cert_path = config.GITLAB_CLIENT_AUTH_CERT
        key_path = config.GITLAB_CLIENT_AUTH_KEY
        session.cert = (cert_path, key_path)
    # private token or personal token authentication
    gl = gitlab.Gitlab(url = config.GITLAB_URL, private_token=config.GITLAB_TOKEN, session=session)
    try:
        gl.auth()
    except gitlab.GitlabAuthenticationError:
        fg_print.error("Failed to authenticate with GitLab! Check access token and client authentication settings in .migrate.ini")
        os.sys.exit()
    except Exception as e:
        fg_print.error(f"Failed to connect to GitLab! {e}")
        os.sys.exit()
    assert isinstance(gl.user, gitlab.v4.objects.CurrentUser)
    fg_print.info(f"Connected to GitLab, version: {gl.version()[0]}")
    return gl

def _build_httpx_client(config: ForgejoConfig, timeout: typing.Optional[float]=60, follow_redirects: typing.Optional[bool] = True) -> HttpxClient:
    client = None
    if(config.FORGEJO_CLIENT_AUTH_CERT != None and config.FORGEJO_CLIENT_AUTH_KEY != None):
        cert_path = config.FORGEJO_CLIENT_AUTH_CERT
        key_path = config.FORGEJO_CLIENT_AUTH_KEY
        cert = (cert_path, key_path)
        client = HttpxClient(cert=cert, timeout=timeout,follow_redirects=follow_redirects)
    return client


def _build_forgejo_api_client(config: ForgejoConfig) -> pyforgejo.PyforgejoApi:
    return PyforgejoApi(base_url=config.FORGEJO_API_URL, api_key=config.FORGEJO_API_TOKEN, httpx_client = _build_httpx_client(config=config))

def _test_forgejo_connection(fg_api:PyforgejoApi):
    try:
        response = fg_api.miscellaneous.get_version()
    except Exception as e:
        fg_print.error(f"Failed to connect to Forgejo! {e}")
        os.sys.exit()
    fg_ver = response.version
    
    fg_print.info(f"Connected to Forgejo, version: {fg_ver}")

def _test_gitlab_connection(gl_api:gitlab.Gitlab):
    fg_print.info(
        f"Connected to GitLab, version: {gl_api.version()[0]}"
    )