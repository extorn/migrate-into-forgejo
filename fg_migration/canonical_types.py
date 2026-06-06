
from dataclasses import dataclass, field



from fg_migration.utils import name_clean

# Note: I created these types to make it explict what fields are currently being handled by the migration
#       In theory we could either use the Forgejo types directly, or extend them e.g. class CanonicalUser(pyforgejo.User)
#       BUT, the issue is that you'll lose the ability to see what is and isn't happening as it won't be explicit.
#       Possibly I might change this to extend the core types, if we get most fields migrated, but, it helps to know 
#       where an object has come from and that is obvious when seeing the type is Canonical<ForgejoTypeName>


@dataclass
class CanonicalUser:
    username: str

    def get_safe_username(self) -> str:
        return name_clean(self.username)



@dataclass
class CanonicalGpgKey:
    name:str
    armored_public_key: str
    armored_signature:str|None



@dataclass
class CanonicalKey:
    name:str
    key: str



@dataclass
class CanonicalSystemUser:
    source_system:str # e.g. gitlab
    username: str
    full_name: str
    email: str
    avatar_url:str|None
    password:str|None # This will be set to a new temporary one when creating the user but should otherwise be None
    gpg_keys:list[CanonicalGpgKey] = field(default_factory=list)
    keys:list[CanonicalKey] = field(default_factory=list)

    def get_safe_username(self) -> str:
        return name_clean(self.username)



@dataclass
class CanonicalOrganizations:
    source_type:str # what is this type defined as at source e.g. for gitlab, Groups
    members:list[CanonicalOrganization] = field(default_factory=list)



@dataclass
class CanonicalGroupMembership:
    group_path: str
    username: str
    access_level: int



@dataclass
class CanonicalOrganization:
    source_type:str # what is this type defined as at source e.g. for gitlab, Group
    username: str
    full_name:str
    description:str
    members: list[CanonicalUser] = field(default_factory=list)
    memberships: list[CanonicalGroupMembership] = field(default_factory=list)

    def get_safe_username(self) -> str:
        return name_clean(self.username)



@dataclass
class CanonicalRepoOwner:
    id:int|None
    username:str|None

    def is_complete(self) -> bool:
        return self.id is not None and self.username is not None



@dataclass(frozen=True) # This allows use in sets, and I can think of no good reason to ever alter the contents.
class CanonicalRepoMembership:
    username:str
    repository:CanonicalRepo
    access_level:str

    def get_safe_username(self) -> str:
        return name_clean(self.username)



@dataclass
class CanonicalRepoMemberships:
    source_system:str # e.g. gitlab
    members:list[CanonicalRepoMembership] = field(default_factory=list)
    source_type:str = "Users" # what is this type defined as at source e.g. for gitlab, Users
    
    @staticmethod
    def get_grouped_by_access_level(members:list[CanonicalRepoMembership]) -> dict[str,set[CanonicalRepoMembership]]:
        grouped_by_access_level : dict[str,set[CanonicalRepoMembership]] = {}
        for member in members:
            grouped_by_access_level.get(member.access_level, set()).add(member)
        return grouped_by_access_level
    


@dataclass
class CanonicalRepo:

    is_individual:bool # Owned by a user? (else an organization)
    username:str
    name:str # Note, in some systems, a user friendly name may be possible, but in Forgejo, there is just the username at present.
    description:str
    owner_name: str
    clone_url:str
    is_private:bool
    auth_password:str
    auth_username:str
    auth_token:str
    source_system:str # e.g. gitlab
    source_id:str # the UUID for this object in the source system e.g. gitlab
    source_type: str = "Repository" # what is this type defined as at source e.g. for gitlab, Project

    def get_safe_username(self) -> str:
        return name_clean(self.name)
    
    def get_safe_owner_name(self) -> str:
        return name_clean(self.owner_name)
