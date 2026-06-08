"""A group of utility functions with no home"""
from dataclasses import asdict
import re
from typing import Literal, get_args, get_origin



def diff_dataclasses(before, after) -> dict:
    """get a diff output for two dataclasses"""
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



def get_union_values_as_str(src) -> set[str]:
    """Retrieve the values of a Union"""
    return set(str(item) for item in _get_union_values_generator(src=src))



def get_union_values(src) -> list:
    """Retrieve the values of a Union"""
    return list(_get_union_values_generator(src=src))


def _get_union_values_generator(src):
    for arg in get_args(src):
        if get_origin(arg) is Literal:
            yield from get_args(arg)
