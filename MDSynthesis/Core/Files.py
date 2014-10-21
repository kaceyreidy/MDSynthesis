"""
Classes for datafile syncronization. 

"""

import Aggregators
import Workers
from uuid import uuid4
import tables
import fcntl
import os
from functools import wraps

class File(object):
    """File object base class. Implements file locking and reloading methods.
    
    """
    def __init__(self, filename, logger=None, **kwargs):
        """Create File instance for interacting with file on disk.

        All files in MDSynthesis should be accessible by high-level
        methods without having to worry about simultaneous reading and writing by
        other processes. The File object includes methods and infrastructure
        for ensuring shared and exclusive locks are consistently applied before
        reads and writes, respectively. It handles any other low-level tasks
        for maintaining file integrity.

        :Arguments:
           *filename*
              name of file on disk object corresponds to 
           *logger*
              logger to send warnings and errors to

        """
        self.filename = os.path.abspath(filename)
        self.handle = None
        self.logger = logger

    def _shlock(self):
        """Get shared lock on file.

        Using fcntl.lockf, a shared lock on the file is obtained. If an
        exclusive lock is already held on the file by another process,
        then the method waits until it can obtain the lock.

        :Returns:
           *success*
              True if shared lock successfully obtained
        """
        fcntl.lockf(self.handle, fcntl.LOCK_SH)

        return True

    def _exlock(self):
        """Get exclusive lock on file.
    
        Using fcntl.lockf, an exclusive lock on the file is obtained. If a
        shared or exclusive lock is already held on the file by another
        process, then the method waits until it can obtain the lock.

        :Returns:
           *success*
              True if exclusive lock successfully obtained
        """
        # first obtain shared lock; may help to avoid race conditions between
        # exclusive locks (REQUIRES THOROUGH TESTING)
        if self._shlock():
            fcntl.lockf(self.handle, fcntl.LOCK_EX)
    
        return True

    def _unlock(self):
        """Remove exclusive or shared lock on file.

        WARNING: It is very rare that this is necessary, since a file must be unlocked
        before it is closed. Furthermore, locks disappear when a file is closed anyway.
        This method will remain here for now, but may be removed in the future if
        not needed (likely).

        :Returns:
           *success*
              True if lock removed
        """
        fcntl.lockf(self.handle, fcntl.LOCK_UN)

        return True

    def _check_existence(self):
        """Check for existence of file.
    
        """
        return os.path.exists(self.filename)

class ContainerFile(File):
    """Container file object; syncronized access to Container data.

    """
    class _Meta(tables.IsDescription):
        """Table definition for metadata.

        All strings limited to hardcoded size for now.

        """
        # unique identifier for container
        uuid = tables.StringCol(36)

        # user-given name of container
        name = tables.StringCol(128)

        # container type; Sim or Group
        container = tables.StringCol(36)

        # eventually we would like this to be generated dynamically
        # meaning, size of location string is size needed, and meta table
        # is regenerated if any of its strings need to be (or smaller)
        # When Coordinator generates its database, it uses largest string size
        # needed
        location = tables.StringCol(256)

    class _Coordinator(tables.IsDescription):
        """Table definition for coordinator info.

        This information is kept separate from other metadata to allow the
        Coordinator to simply stack tables to populate its database. It doesn't
        need entries that store its own path.

        Path length fixed size for now.
        """
        # absolute path of coordinator
        abspath = tables.StringCol(256)
        
    class _Tags(tables.IsDescription):
        """Table definition for tags.

        """
        tag = tables.StringCol(36)

    class _Categories(tables.IsDescription):
        """Table definition for categories.

        """
        category = tables.StringCol(36)
        value = tables.StringCol(36)

    def __init__(self, filename, logger, classname, **kwargs): 
        """Initialize Container state file.

        This is the base class for all Container state files. It generates 
        data structure elements common to all Containers. It also implements
        low-level I/O functionality.

        :Arguments:
           *filename*
              path to file
           *logger*
              Container's logger instance
           *classname*
              Container's class name

        :Keywords:
           *name*
              user-given name of Container object
           *coordinator*
              directory in which coordinator state file can be found [None]
           *categories*
              user-given dictionary with custom keys and values; used to
              give distinguishing characteristics to object for search
           *tags*
              user-given list with custom elements; used to give distinguishing
              characteristics to object for search
           *details*
              user-given string for object notes

        .. Note:: kwargs passed to :meth:`create`

        """
        super(ContainerFile, self).__init__(filename, logger=logger)
        
        # if file does not exist, it is created
        if not self._check_existence():
            self.create(classname, **kwargs)

    def create(self, classname, **kwargs):
        """Build state file and common data structure elements.

        :Arguments:
           *classname*
              Container's class name

        :Keywords:
           *name*
              user-given name of Container object
           *coordinator*
              directory in which coordinator state file can be found [None]
           *categories*
              user-given dictionary with custom keys and values; used to
              give distinguishing characteristics to object for search
           *tags*
              user-given list with custom elements; used to give distinguishing
              characteristics to object for search
        """
        self.handle = tables.open_file(self.filename, 'w')
        self._exlock()

        # metadata table
        meta_table = self.handle.create_table('/', 'meta', self._Meta, 'metadata')
        container = meta_table.row

        container['uuid'] = str(uuid4())
        container['name'] = kwargs.pop('name', classname)
        container['container'] = classname
        container['location'] = os.path.dirname(self.filename)
        container.append()

        # coordinator table
        coordinator_table = self.handle.create_table('/', 'coordinator', self._Coordinator, 'coordinator information')
        container = coordinator_table.row
        
        container['abspath'] = kwargs.pop('coordinator', None)
        container.append()

        # tags table
        #TODO: make use of add_tags methods, but must be wary of multiple opens to file
        # previous opens are only closed when the interpreter exits
        tags_table = self.handle.create_table('/', 'tags', self._Tags, 'tags')
        container = tags_table.row
        
        tags = kwargs.pop('tags', list())
        for tag in tags:
            container['tag'] = str(tag)
            container.append()

        # categories table
        categories_table = self.handle.create_table('/', 'categories', self._Categories, 'categories')
        container = categories_table.row
        
        categories = kwargs.pop('categories', dict())
        for key in categories.keys():
            container['category'] = str(key)
            container['value'] = str(categories[key])
            container.append()

        # remove lock and close
        self.handle.close()
    
    def _read(func):
        """Decorator for opening file for reading and applying shared lock.
        
        Applying this decorator to a method will ensure that the file is opened
        for reading and that a shared lock is obtained before that method is
        executed. It also ensures that the lock is removed and the file closed
        after the method returns.

        """
        @wraps(func)
        def inner(self, *args, **kwargs):
            self.handle = tables.open_file(self.filename, 'r')
            self._shlock()
            out = func(self, *args, **kwargs)
            self.handle.close()
            return out

        return inner
    
    def _write(func):
        """Decorator for opening file for writing and applying exclusive lock.
        
        Applying this decorator to a method will ensure that the file is opened
        for appending and that an exclusive lock is obtained before that method is
        executed. It also ensures that the lock is removed and the file closed
        after the method returns.

        """
        @wraps(func)
        def inner(self, *args, **kwargs):
            self.handle = tables.open_file(self.filename, 'a')
            self._exlock()
            out = func(self, *args, **kwargs)
            self.handle.close()
            return out

        return inner

    @_read
    def get_tags(self):
        """Get all tags as a list.

        :Returns:
            *tags*
                list of all tags
        """
        table = self.handle.get_node('/', 'tags')
        return [ x['tag'] for x in table.iterrows() ]
        
    @_write
    def add_tags(self, *tags):
        """Add any number of tags to the Container.

        Tags are individual strings that serve to differentiate Containers from
        one another. Sometimes preferable to categories.

        :Arguments:
           *tags*
              Tags to add. Must be convertable to strings using the str() builtin.

        """
        table = self.handle.get_node('/', 'tags')

        # ensure tags are unique (we don't care about order)
        tags = set([ str(tag) for tag in tags ])

        # remove tags already present in metadata from list
        #TODO: more efficient way to do this?
        tags_present = list()
        for row in table:
            for tag in tags:
                if (row['tag'] == tag):
                    tags_present.append(tag)

        tags = list(tags - set(tags_present))

        # add new tags
        for tag in tags:
            table.row['tag'] = tag
            table.row.append()

    @_write
    def del_tags(self, *tags, **kwargs):
        """Delete tags from Container.

        Any number of tags can be given as arguments, and these will be
        deleted.

        :Arguments:
            *tags*
                Tags to delete.

        :Keywords:
            *all*
                When True, delete all tags [``False``]

        """
        table = self.handle.get_node('/', 'tags')
        purge = kwargs.pop('all', False)

        if purge:
            table.remove()
            table = self.handle.create_table('/', 'tags', self._Tags, 'tags')
            
        else:
            # remove redundant tags from given list if present
            tags = set([ str(tag) for tag in tags ])

            # get matching rows
            rowlist = list()
            for row in table:
                for tag in tags:
                    if (row['tag'] == tag):
                        rowlist.append(row.nrow)

            # must include a separate condition in case all rows will be removed
            # due to a limitation of PyTables
            if len(rowlist) == table.nrows:
                table.remove()
                table = self.handle.create_table('/', 'tags', self._Tags, 'tags')
            else:
                rowlist.sort()
                j = 0
                # delete matching rows; have to use j to shift the register as we
                # delete rows
                for i in rowlist:
                    table.remove_row(i-j)
                    j=j+1

    @_read
    def get_categories(self):
        """Get all categories as a dictionary.

        :Returns:
            *categories*
                dictionary of all categories 
        """
        table = self.handle.get_node('/', 'categories')
        return { x['category']: x['value'] for x in table.iterrows() }

    @_write
    def add_categories(self, **categories):
        """Add any number of categories to the Container.

        Categories are key-value pairs of strings that serve to differentiate
        Containers from one another. Sometimes preferable to tags.

        If a given category already exists (same key), the value given will replace
        the value for that category.

        :Keywords:
            *categories*
                Categories to add. Keyword used as key, value used as value. Both
                must be convertible to strings using the str() builtin.

        """
        table = self.handle.get_node('/', 'categories')

        # remove categories already present in metadata from dictionary 
        #TODO: more efficient way to do this?
        for row in table:
            for key in categories.keys():
                if (row['category'] == key):
                    row['value'] = str(categories[key])
                    row.update()
                    # dangerous? or not since we are iterating through
                    # categories.keys() and not categories?
                    categories.pop(key)
        
        # add new categories
        for key in categories.keys():
            table.row['category'] = key
            table.row['value'] = str(categories[key])
            table.row.append()

    @_write
    def del_categories(self, *categories, **kwargs):
        """Delete categories from Container.
    
        Any number of categories (keys) can be given as arguments, and these
        keys (with their values) will be deleted.
         
        :Arguments:
            *categories*
                Categories to delete.

        :Keywords:
            *all*
                When True, delete all categories [``False``]
    
        """
        table = self.handle.get_node('/', 'categories')
        purge = kwargs.pop('all', False)

        if purge:
            table.remove()
            table = self.handle.create_table('/', 'categories', self._Categories, 'categories')
        else:
            # remove redundant categories from given list if present
            categories = set([ str(category) for category in categories ])

            # get matching rows
            rowlist = list()
            for row in table:
                for category in categories:
                    if (row['category'] == category):
                        rowlist.append(row.nrow)

            # must include a separate condition in case all rows will be removed
            # due to a limitation of PyTables
            if len(rowlist) == table.nrows:
                table.remove()
                table = self.handle.create_table('/', 'categories', self._Categories, 'categories')
            else:
                rowlist.sort()
                j = 0
                # delete matching rows; have to use j to shift the register as we
                # delete rows
                for i in rowlist:
                    table.remove_row(i-j)
                    j=j+1
    
    def _open_r(self):
        """Open file with intention to write.

        Not to be used except for debugging files.

        """
        self.handle = tables.open_file(self.filename, 'r')
        self.shlock()

    def _open_w(self):
        """Open file with intention to write.
    
        Not to be used except for debugging files.
         
        """
        self.handle = tables.open_file(self.filename, 'a')
        self.exlock()
    
    def _close(self):
        """Close file.
    
        Not to be used except for debugging files.
    
        """
        self.handle.close()

class SimFile(ContainerFile):
    """Main Sim state file.

    This file contains all the information needed to store the state of a
    Sim object. It includes accessors, setters, and modifiers for all
    elements of the data structure, as well the data structure definition.
    It also defines the format of the file, i.e. the writer and reader
    used to manage it.
    
    """

    class UniverseTopology(tables.IsDescription):
        """Table definition for storing universe topology paths.

        Three versions of the path to a topology are stored: the absolute path
        (abspath), the relative path from user's home directory (relhome), and the
        relative path from the Sim object's directory (relSim). This allows the
        Sim object to use some heuristically good starting points trying to find
        missing files using Finder.
        
        """
        abspath = tables.StringCol(255)
        relhome = tables.StringCol(255)
        relSim = tables.StringCol(255)

    class UniverseTrajectory(tables.IsDescription):
        """Table definition for storing universe trajectory paths.

        The paths to trajectories used for generating the Universe
        are stored in this table.

        See UniverseTopology for path storage descriptions.

        """
        abspath = tables.StringCol(255)
        relhome = tables.StringCol(255)
        relSim = tables.StringCol(255)

    class Selection(tables.IsDescription):
        """Table definition for storing selections.

        A single table corresponds to a single selection. Each row in the
        column contains a selection string. This allows one to store a list
        of selections so as to preserve selection order, which is often
        required for structural alignments.

        """
        selection = tables.StringCol(255)

    def __init__(self, filename, logger, **kwargs):
        """Initialize Sim state file.

        :Arguments:
           *filename*
              path to file
           *logger*
              logger to send warnings and errors to

        """
        super(SimFile, self).__init__(location, logger=logger, classname='Sim', **kwargs)
    
    def create(self, classname, **kwargs):
        """Build Sim data structure.

        :Arguments:
           *classname*
              Container's class name

        :Keywords:
           *name*
              user-given name of Sim object
           *coordinator*
              directory in which Coordinator state file can be found [``None``]
           *categories*
              user-given dictionary with custom keys and values; used to
              give distinguishing characteristics to object for search
           *tags*
              user-given list with custom elements; used to give distinguishing
              characteristics to object for search

        .. Note:: kwargs passed to :meth:`create`

        """
        super(SimFile, self).create(classname, **kwargs)

        self.handle = tables.open_file(self.filename, 'a')
        self.exlock()

        # universes group
        universes_group = self.handle.create_group('/', 'universes', 'universes')

        # remove lock and close
        self.handle.close()

class DatabaseFile(File):
    """Database file object; syncronized access to Database data.

    """

class DataFile(object):
    """Universal datafile interface.

    Allows for safe reading and writing of datafiles, which can be of a wide
    array of formats. Handles the details of conversion from pythonic data
    structure to persistent file form.

    """
