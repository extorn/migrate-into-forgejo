# A developers summary

## Code Layout

### Core Files

| **File** | **Purpose** | **Notes** |
| --- | --- | --- |
| **ADAPTERS** |   |   |
| forgejo\_types.py | A few useful utility classes used by the ForgejoDestination class | Extracted primarily to try and reduce the volume of code in destination\_forgejo.py |
| destination\_forgejo.py | A class that wraps the pyforge API, Canonical Types are passed in to many of the functions | The key idea for this was to extract a lot of print statements from the migration logic to make that more readable, but it's become more useful over time. Now it contains a class ForgejoDestination, that the migrator uses. |
| gitlab\_types.py | A few useful utility classes used by the GitlabMigrationSource class | Extracted primarily to try and reduce the volume of code in source\_gitlab.py |
| source\_gitlab.py | An implementation (the only so far) of the class MigrationSource | This class extracts data from gitlab into Canonical classes which are then loaded into Forgejo by the migration code. This class is driven by the migrator |
| **CORE** |   |   |
| canonical\_types.py | A set of classes which act as a bridge between the source systems and Forgejo types. | The key idea is that it is a lot easier mentally to see what is going on if you know a type is canonical it has come from the source system, if it is e.g. a Team, or User, it has come from Forgejo. These classes could equally have been called Import\[ed\]\<XYZ> or similar. |
| config\_types.py | Immutable Data classes that configuration files are loaded into, these types are passed around the rest of the code | These are loaded with data from the .migrate.ini file sections |
| migration\_source\_type.py | An abstract class that defines the interface for ANY source system to be imported into Forgejo | There is no support for paging API calls at present, that's the next consideration now the migration code essentially works, though of less urgency as most users are presumed to be either own small repositories for personal use or have funds to write such a script for themselves for their custom commercial use. |
| migration\_source.py | Builder functions for the MigrationSource classes | One builder per MigrationSource implementation |
| **SERVICES** |   |   |
| fg\_purger.py | A class that contains all code used to purge Forgejo | This code was extracted from the orginal forked code in purge\_forgejo.py. It has been updated to use the pyforge API, but not been tested |
| migrator.py | The actual migration engine itself | Extracts canonical types from the migration source provided and loads them into the destination provided - currently only Forgejo is supported as a destination with no effort made to make this configurable, though it wouldn't be too complicated now, the interface would be much broader than the MigrationSource ones |
| push\_mirror\_creator.py | A class that contains all code used manage push mirrors to/from Forgejo | This code was extracted from the orginal forked code in purge\_forgejo.py. It has been updated to use the pyforge API, but not been tested |
| **STRATEGIES** |   |   |
| base\_access\_mapping\_strategy.py | abstract implementation | This adds a few helpful implementations of functions that are commonly needed |
| access\_level\_mapping\_strategy.py |   | see class comment |
| strict\_access\_level\_mapping\_strategy.py |   | see class comment |
| access\_mapping\_strategy.py | Interface class | implemented to define how users andd collaborations are mapped into the repositories, organizations, etc |
| direct\_collaborator\_strategy.py |   | see class comment |
| existing\_forgejo\_preserving\_strategy.py |   | see class comment |
| **UTILS** |   |   |
| fg\_print.py | A wrapper around logging inherited from the original forked code | I'm not clear on the benefits of this, but not being a native python developer, I've left it as is for now |
| utils.py | A place for a series of functions used in various places | A temporary dumping ground, but the aim is to keep this as small as possible |

### Scripts

| **File** | **Purpose** | **Notes** |
| --- | --- | --- |
| LICENCE | Describes the terms of use | GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007. |
| .migrate.ini | a transient configuration file | This is created by the user based on the README.md and updated to match what the user desires to happen. It is git excluded so won't end up in the repository. |
| create\_push\_mirrors.py | script to control push mirror create/delete | Loads any config files into classes and then passes them into the requisite classes that do the work |
| forgejo\_user\_roles.yaml | defines Forgejo user roles | This is used to create any users, but also Teams within Forgejo during import. All names, descriptions, and permissions can be set as you wish, but be warned that I cannot get the Forgejo web interface to work properly without at least one team being named as "Owners" a present. |
| gitlab\_forgejo\_roles\_map.yaml | maps gitlab access\_level to Forgejo role | Any role mapping defined in this file must refer to a role defined in forgejo\_user\_roles.yaml. The migration script checks for this before anything starts |
| migrate.py | script to control migration into Forgejo | Loads any config files into classes and then passes them into the requisite classes that do the work |
| purge\_forgejo.py | script to facilitate purging Forgejo of various items of data | This code was extracted from the orginal forked code in purge\_forgejo.py. It has been updated to use the pyforge API, but not been tested |
| requirements.txt | defines python library requirements | Versions of libraries used can be defined here to ensure the code still compiles when external libraries are updated. Currently I've left it free to use any version available, pyforge excepting |

## Adding support for a new Source System

### Steps

1.  Create a new file `./fg_migration/<my_source_system_name>.py` by copying and pasting `migration_source_type.py`
2.  Rename the class header inside to `class <my_source_system_name>MigrationSource(MigrationSource):`
3.  Implement each function as required. _Look to the_ `_gitlab.py_` _as an example implementation_
4.  In `migration_source.py`
    1.  Add a builder function for your new Source System
    2.  Update the enum class to include a unique config value for your SourceSystem `class SourceType`
    3.  Update the `SOURCE_BUILDERS` map, adding an entry from your enum entry to the new builder function you wrote