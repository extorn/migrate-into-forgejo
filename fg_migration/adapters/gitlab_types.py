"""A series of classes involved in the interaction with the GitLab API"""
import os
import time
from typing import Callable, Iterator, TypeVar

import gitlab  # pip install python-gitlab
import gitlab.v4 # pylint: disable=no-name-in-module
import gitlab.v4.objects
import requests

from fg_migration.utils import fg_print
from fg_migration.core.config_types import GitLabConfig


class IterativeFetchError(Exception):
    """Raised when the ApiPaginator fails to retrieve the next page of data for some reason"""

T = TypeVar("T")


class GitLabApiPaginator:
    """Wraps the GitLab API with pagination support where errors are logged"""
    gl_api:gitlab.Gitlab
    max_page_size:int
    items_type:str
    retrieval_detail:str

    def __init__(self, gl_api:gitlab.Gitlab, page_size:int=50,
                 items_type:str="Items", retrieval_detail:str=""):
        self.gl_api = gl_api
        self.max_page_size = page_size
        self.items_type = items_type
        self.retrieval_detail = retrieval_detail

    def iterate(self, fetch_page_from_api: Callable[[gitlab.Gitlab, int, int], list[T]],
        ) -> Iterator[T]:
        """Create the API pagination wrapped in an Iterator"""

        page_idx = 1
        try:
            while True:
                page_of_data : list
                for attempt in range(3):
                    try:
                        page_of_data = fetch_page_from_api(self.gl_api,page_idx, self.max_page_size)
                        break
                    except TimeoutError:
                        if attempt == 2:
                            raise
                        time.sleep(2 ** attempt)
                yield from page_of_data
                page_idx += 1
                if len(page_of_data) < self.max_page_size:
                    # no more to load
                    break
        except Exception as e:
            msg = f"Failed to retrieve existing {self.items_type} page[{page_idx}]" \
                  f"{self.retrieval_detail} {e}"
            fg_print.error(msg)
            raise IterativeFetchError(msg) from e


class GitLabApiBuilder:
    """A builder for the GitLab, configuring authentication etc in a central way"""
    config : GitLabConfig

    def __init__(self, gitlab_config:GitLabConfig):
        self.config = gitlab_config

    def build_gitlab_api_client(self) -> gitlab.Gitlab:
        """Build a GitLab API Client using either the API key provided, or the default API key"""
        session = requests.Session()
        # add client authentication if cert and key are provided in the config
        if(self.config.GITLAB_CLIENT_AUTH_CERT is not None
           and self.config.GITLAB_CLIENT_AUTH_KEY is not None):
            cert_path = self.config.GITLAB_CLIENT_AUTH_CERT
            key_path = self.config.GITLAB_CLIENT_AUTH_KEY
            session.cert = (cert_path, key_path)
        # private token or personal token authentication
        gl = gitlab.Gitlab(url = self.config.GITLAB_URL,
                           private_token=self.config.GITLAB_TOKEN,
                           session=session)
        try:
            gl.auth()
        except gitlab.GitlabAuthenticationError:
            fg_print.error("Failed to authenticate with GitLab! Check "
                           "access token and client authentication settings in .migrate.ini")
            os.sys.exit()
        except (gitlab.GitlabError, requests.exceptions.RequestException) as e:
            fg_print.error(f"Failed to connect to GitLab! {e}")
            os.sys.exit()
        assert isinstance(gl.user, gitlab.v4.objects.CurrentUser)
        return gl


    def test_gitlab_connection(self, gl_api:gitlab.Gitlab):
        """Run an API call to ensure the connection was successful"""
        version_tuple=gl_api.version()
        fg_print.info(
            f"Connected to GitLab, version: {version_tuple[0]}"
        )
        if version_tuple[0] == "unknown" and version_tuple[0] == "unknown":
            return False
        return True
