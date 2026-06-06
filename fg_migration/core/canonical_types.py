""" Note: I created these types to make it explict what fields are currently being handled by the
          migration.
          In theory we could either use the Forgejo types directly, or extend them e.g.
          class CanonicalUser(pyforgejo.User) BUT, the issue is that you'll lose the ability to
          see what is and isn't happening as it won't be explicit. Possibly I might change this
          to extend the core types, if we get most fields migrated, but, it helps to know
          where an object has come from and that is obvious when seeing the type is
          Canonical<ForgejoTypeName>
          These types have fields such as username, they are the Source System username.
          the get_safe_username function is to convert that to Forgejo equivalent"""
from dataclasses import dataclass, field



from fg_migration.utils.utils import name_clean




@dataclass
class CanonicalUser:
    """Typesafe definition of a User in its most basic form"""
    username: str

    def get_safe_username(self) -> str:
        """Get Forgejo safe username"""
        return name_clean(self.username)



@dataclass
class CanonicalGpgKey:
    """A GPG Key"""
    name:str
    armored_public_key: str
    armored_signature:str|None



@dataclass
class CanonicalKey:
    """An SSH Key"""
    name:str
    key: str



@dataclass
class CanonicalSystemUser:
    """A System User (i.e. that can login)"""
    source_system:str # e.g. gitlab
    username: str
    full_name: str
    email: str
    avatar_url:str|None
    # The password will be set to a new temporary one when creating the
    # user but should otherwise be None
    password:str|None
    gpg_keys:list[CanonicalGpgKey] = field(default_factory=list)
    keys:list[CanonicalKey] = field(default_factory=list)

    def get_safe_username(self) -> str:
        """Get Forgejo safe username"""
        return name_clean(self.username)



@dataclass
class CanonicalOrganizations:
    """Group of Owners of repositories and teams. Really this just allows definition
       of the source type which is typically a plural of the CanonicalOrganization
       source type (used in logging)"""
    source_type:str # what is this type defined as at source e.g. for gitlab, Groups
    members:list[CanonicalOrganization] = field(default_factory=list)



@dataclass
class CanonicalGroupMembership:
    """A mapping from Organization to user that is a member"""
    group_path: str
    username: str
    access_level: int



@dataclass
class CanonicalOrganization:
    """Owner of repositories and teams"""
    source_type:str # what is this type defined as at source e.g. for gitlab, Group
    username: str
    full_name:str
    description:str
    members: list[CanonicalUser] = field(default_factory=list)
    memberships: list[CanonicalGroupMembership] = field(default_factory=list)

    def get_safe_username(self) -> str:
        """Get Forgejo safe username"""
        return name_clean(self.username)



@dataclass
class CanonicalRepoOwner:
    """Owner of a repository (either user identifier or organization identifier)"""
    id:int|None
    username:str|None

    def is_complete(self) -> bool:
        """Is the repo owner fully defined (theoretically this should always be true)"""
        return self.id is not None and self.username is not None


# frozen permits its use in sets, and I can think of no good reason to ever alter the contents.
@dataclass(frozen=True)
class CanonicalRepoMembership:
    """Mapping between repository and user with access including the permission they have there"""
    username:str
    repository:CanonicalRepo
    access_level:str

    def get_safe_username(self) -> str:
        """Get Forgejo safe username"""
        return name_clean(self.username)



@dataclass
class CanonicalRepoMemberships:
    """This is essentially a wrapper around the list of CanonicalRepoMembership
       allowing the plural to be recorded"""
    source_system:str # e.g. gitlab
    members:list[CanonicalRepoMembership] = field(default_factory=list)
    source_type:str = "Users" # what is this type defined as at source e.g. for gitlab, Users

    @staticmethod
    def get_grouped_by_access_level(members:list[CanonicalRepoMembership]
                                         ) -> dict[str,set[CanonicalRepoMembership]]:
        """Builds a list of memberships that have been grouped by their access level"""
        grouped_by_access_level : dict[str,set[CanonicalRepoMembership]] = {}
        for member in members:
            grouped_by_access_level.get(member.access_level, set()).add(member)
        return grouped_by_access_level



@dataclass
class CanonicalRepo:
    """A repository for code"""
    is_individual:bool # Owned by a user? (else an organization)
    username:str
    # Note, in some systems, a user friendly name may be possible,
    # but in Forgejo, there is just the username at present.
    name:str
    description:str
    owner_name: str
    clone_url:str
    is_private:bool
    auth_password:str
    auth_username:str
    auth_token:str
    source_system:str # e.g. gitlab
    # source_id - the UUID for this object in the source system e.g. gitlab
    source_id:str
    # source_type - what is this type defined as at source e.g. for gitlab, Project
    source_type: str = "Repository"

    def get_safe_username(self) -> str:
        """Get Forgejo safe username"""
        return name_clean(self.name)

    def get_safe_owner_name(self) -> str:
        """Get Forgejo safe owner name"""
        return name_clean(self.owner_name)
