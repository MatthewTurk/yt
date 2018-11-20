"""
AMR index container class



"""
from __future__ import print_function

#-----------------------------------------------------------------------------
# Copyright (c) 2013, yt Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

from yt.utilities.on_demand_imports import _h5py as h5py
import numpy as np
import weakref

from collections import defaultdict

from yt.arraytypes import blankRecordArray
from yt.config import ytcfg
from yt.funcs import \
    ensure_list, ensure_numpy_array
from yt.geometry.geometry_handler import \
    Index, YTDataChunk, ChunkDataCache
from yt.utilities.definitions import MAXLEVEL
from yt.utilities.logger import ytLogger as mylog
from .grid_container import \
    GridTree, MatchPointsToGrids


class GridIndex(Index):
    """The index class for patch and block AMR datasets. """
    float_type = 'float64'
    _preload_implemented = False
    _grid_tree = None
    _index_properties = ("grid_left_edge", "grid_right_edge",
                         "grid_levels", "grid_particle_count",
                         "grid_dimensions")

    def _setup_geometry(self):
        mylog.debug("Counting grids.")
        self._count_grids()

        mylog.debug("Initializing grid arrays.")
        self._initialize_grid_arrays()

        mylog.debug("Parsing index.")
        self._parse_index()

        mylog.debug("Constructing grid objects.")
        self._populate_grid_objects()

        mylog.debug("Re-examining index")
        self._initialize_level_stats()

    def __del__(self):
        del self.grid_dimensions
        del self.grid_left_edge
        del self.grid_right_edge
        del self.grid_levels
        del self.grid_particle_count
        del self.grids

    @property
    def parameters(self):
        return self.dataset.parameters

    def _setup_filenames(self):
        # We will do everything here assuming that the grid objects have associated
        # filenames.
        grids_by_file = {}
        for g in self.grids:
            if g.filename not in grids_by_file:
                grids_by_file[g.filename] = []
            grids_by_file[g.filename].append(g.id - g._id_offset)
        for fn in grids_by_file:
            # Sort these, to preserve some kind of order
            grids_by_file[fn].sort()
        cls = self.dataset._file_class
        self.data_files = []
        # If _grid_chunk_size is not set, default to something enormous
        GRID_CHUNKSIZE = self.dataset._grid_chunk_size or (2 << 32)
        fi = 0
        for fn, grids in sorted(grids_by_file.items()):
            for i in range(0, len(grids), GRID_CHUNKSIZE):
                grids_ = grids[i:i+GRID_CHUNKSIZE]
                df = cls(self.dataset, self.io, fn, fi, grids_)
                self.data_files.append(df)
                fi += 1

    def _detect_output_fields_backup(self):
        # grab fields from backup file as well, if present
        return
        try:
            backup_filename = self.dataset.backup_filename
            f = h5py.File(backup_filename, 'r')
            g = f["data"]
            grid = self.grids[0] # simply check one of the grids
            grid_group = g["grid_%010i" % (grid.id - grid._id_offset)]
            for field_name in grid_group:
                if field_name != 'particles':
                    self.field_list.append(field_name)
        except KeyError:
            return
        except IOError:
            return

    def select_grids(self, level):
        """
        Returns an array of grids at *level*.
        """
        return self.grids[self.grid_levels.flat == level]

    def get_levels(self):
        for level in range(self.max_level+1):
            yield self.select_grids(level)

    def _initialize_grid_arrays(self):
        # At some point, we can or should get rid of this function in favor of
        # just having iterables of uncounted objects.
        mylog.debug("Allocating arrays for %s grids", self.num_grids)
        self.grid_dimensions = np.ones((self.num_grids,3), 'int32')
        self.grid_left_edge = self.ds.arr(np.zeros((self.num_grids,3),
                                    self.float_type), 'code_length')
        self.grid_right_edge = self.ds.arr(np.ones((self.num_grids,3),
                                    self.float_type), 'code_length')
        self.grid_levels = np.zeros((self.num_grids,1), 'int32')
        self.grid_particle_count = np.zeros((self.num_grids,1), 'int32')

    def clear_all_data(self):
        """
        This routine clears all the data currently being held onto by the grids
        and the data io handler.
        """
        for g in self.grids: g.clear_data()
        self.io.queue.clear()

    def get_smallest_dx(self):
        """
        Returns (in code units) the smallest cell size in the simulation.
        """
        return self.select_grids(self.grid_levels.max())[0].dds[:].min()

    def _get_particle_type_counts(self):
        return {self.ds.particle_types_raw[0]: self.grid_particle_count.sum()}

    def _initialize_level_stats(self):
        # Now some statistics:
        #   0 = number of grids
        #   1 = number of cells
        #   2 = blank
        desc = {'names': ['numgrids','numcells','level'],
                'formats':['int64']*3}
        self.level_stats = blankRecordArray(desc, MAXLEVEL)
        self.level_stats['level'] = [i for i in range(MAXLEVEL)]
        self.level_stats['numgrids'] = [0 for i in range(MAXLEVEL)]
        self.level_stats['numcells'] = [0 for i in range(MAXLEVEL)]
        for level in range(self.max_level+1):
            self.level_stats[level]['numgrids'] = np.sum(self.grid_levels == level)
            li = (self.grid_levels[:,0] == level)
            self.level_stats[level]['numcells'] = self.grid_dimensions[li,:].prod(axis=1).sum()

    @property
    def grid_corners(self):
        return np.array([
          [self.grid_left_edge[:,0], self.grid_left_edge[:,1], self.grid_left_edge[:,2]],
          [self.grid_right_edge[:,0], self.grid_left_edge[:,1], self.grid_left_edge[:,2]],
          [self.grid_right_edge[:,0], self.grid_right_edge[:,1], self.grid_left_edge[:,2]],
          [self.grid_left_edge[:,0], self.grid_right_edge[:,1], self.grid_left_edge[:,2]],
          [self.grid_left_edge[:,0], self.grid_left_edge[:,1], self.grid_right_edge[:,2]],
          [self.grid_right_edge[:,0], self.grid_left_edge[:,1], self.grid_right_edge[:,2]],
          [self.grid_right_edge[:,0], self.grid_right_edge[:,1], self.grid_right_edge[:,2]],
          [self.grid_left_edge[:,0], self.grid_right_edge[:,1], self.grid_right_edge[:,2]],
        ], dtype='float64')

    def lock_grids_to_parents(self):
        r"""This function locks grid edges to their parents.

        This is useful in cases where the grid structure may be somewhat
        irregular, or where setting the left and right edges is a lossy
        process.  It is designed to correct situations where left/right edges
        may be set slightly incorrectly, resulting in discontinuities in images
        and the like.
        """
        mylog.info("Locking grids to parents.")
        for i, g in enumerate(self.grids):
            si = g.get_global_startindex()
            g.LeftEdge = self.ds.domain_left_edge + g.dds * si
            g.RightEdge = g.LeftEdge + g.ActiveDimensions * g.dds
            self.grid_left_edge[i,:] = g.LeftEdge
            self.grid_right_edge[i,:] = g.RightEdge

    def print_stats(self):
        """
        Prints out (stdout) relevant information about the simulation
        """
        header = "%3s\t%6s\t%14s\t%14s" % ("level","# grids", "# cells",
                                           "# cells^3")
        print(header)
        print("%s" % (len(header.expandtabs())*"-"))
        for level in range(MAXLEVEL):
            if (self.level_stats['numgrids'][level]) == 0:
                break
            print("% 3i\t% 6i\t% 14i\t% 14i" % \
                  (level, self.level_stats['numgrids'][level],
                   self.level_stats['numcells'][level],
                   np.ceil(self.level_stats['numcells'][level]**(1./3))))
            dx = self.select_grids(level)[0].dds[0]
        print("-" * 46)
        print("   \t% 6i\t% 14i" % (self.level_stats['numgrids'].sum(), self.level_stats['numcells'].sum()))
        print("\n")
        try:
            print("z = %0.8f" % (self["CosmologyCurrentRedshift"]))
        except:
            pass
        print("t = %0.8e = %0.8e s = %0.8e years" % \
            (self.ds.current_time.in_units("code_time"),
             self.ds.current_time.in_units("s"),
             self.ds.current_time.in_units("yr")))
        print("\nSmallest Cell:")
        for item in ("Mpc", "pc", "AU", "cm"):
            print("\tWidth: %0.3e %s" % (dx.in_units(item), item))

    def _find_field_values_at_points(self, fields, coords):
        r"""Find the value of fields at a set of coordinates.

        Returns the values [field1, field2,...] of the fields at the given
        (x, y, z) points. Returns a numpy array of field values cross coords
        """
        coords = self.ds.arr(ensure_numpy_array(coords), 'code_length')
        grids = self._find_points(coords[:, 0], coords[:, 1], coords[:, 2])[0]
        fields = ensure_list(fields)
        mark = np.zeros(3, dtype=np.int)
        out = []

        # create point -> grid mapping
        grid_index = {}
        for coord_index, grid in enumerate(grids):
            if grid not in grid_index:
                grid_index[grid] = []
            grid_index[grid].append(coord_index)

        out = []
        for field in fields:
            funit = self.ds._get_field_info(field).units
            out.append(self.ds.arr(np.empty((len(coords))), funit))

        for grid in grid_index:
            cellwidth = (grid.RightEdge - grid.LeftEdge) / grid.ActiveDimensions
            for field_index, field in enumerate(fields):
                for coord_index in grid_index[grid]:
                    mark = ((coords[coord_index, :] - grid.LeftEdge) / cellwidth)
                    mark = np.array(mark, dtype='int64')
                    out[field_index][coord_index] = \
                        grid[field][mark[0], mark[1], mark[2]]
        if len(fields) == 1:
            return out[0]
        return out


    def _find_points(self, x, y, z) :
        """
        Returns the (objects, indices) of leaf grids containing a number of (x,y,z) points
        """
        x = ensure_numpy_array(x)
        y = ensure_numpy_array(y)
        z = ensure_numpy_array(z)
        if not len(x) == len(y) == len(z):
            raise AssertionError("Arrays of indices must be of the same size")

        grid_tree = self._get_grid_tree()
        pts = MatchPointsToGrids(grid_tree, len(x), x, y, z)
        ind = pts.find_points_in_tree()
        return self.grids[ind], ind

    @property
    def grid_tree(self):
        return self._get_grid_tree()

    def _get_grid_tree(self):
        if self._grid_tree is not None:
            return self._grid_tree

        left_edge = self.ds.arr(np.zeros((self.num_grids, 3)),
                               'code_length')
        right_edge = self.ds.arr(np.zeros((self.num_grids, 3)),
                                'code_length')
        level = np.zeros((self.num_grids), dtype='int64')
        parent_ind = np.zeros((self.num_grids), dtype='int64')
        num_children = np.zeros((self.num_grids), dtype='int64')
        dimensions = np.zeros((self.num_grids, 3), dtype="int32")

        for i, grid in enumerate(self.grids) :

            left_edge[i,:] = grid.LeftEdge
            right_edge[i,:] = grid.RightEdge
            level[i] = grid.Level
            if grid.Parent is None :
                parent_ind[i] = -1
            else :
                parent_ind[i] = grid.Parent.id - grid.Parent._id_offset
            num_children[i] = np.int64(len(grid.Children))
            dimensions[i,:] = grid.ActiveDimensions

        self._grid_tree = GridTree(self.num_grids, left_edge,
                                   right_edge, dimensions,
                                   parent_ind, level, num_children)
        return self._grid_tree

    def convert(self, unit):
        return self.dataset.conversion_factors[unit]

    def _identify_base_chunk(self, dobj):
        r"""What this does is set up a chunk object and modify in-place the
        passed-in dobj.  The modifications to the dobj:

          * set _chunk_info to be an object array of proxy-to-"grid" objects
          * set size to be the size; if this is None, it will not know in
            advance the size.
          * set _current_chunk to be a *list* (other places we may make this an
            iterable, but we do not here) of a single chunk of type "all" with
            the data object.

        One point of interest is that this setup means that we always have
        *two* levels of chunking taking place -- chunk_all will always be
        called, which assumes that the base chunk has been set up.

        TODO: Maybe this should be redone so that we don't modify dobj in
        place?
        """
        # We take our inspiration from the particle geometry handler
        if getattr(dobj, "_chunk_info", None) is None:
            #if isinstance(dobj, ) # This is checking if we just want data
            #from a grid or file
            pass
        # It's possible this needs to be indented?
        # Not sure about this part:
        #if dobj._type_name == "grid":
        #    dobj._chunk_info = np.empty(1, dtype='object')
        #    dobj._chunk_info[0] = weakref.proxy(dobj)
        #
        # This indexer is a selector we can use as input to subsequent selectors, too.
        indexer = self.grid_tree.selector()
        if getattr(dobj, "size", None) is None:
            dobj.size = self._count_selection(dobj, indexer = indexer)
        if getattr(dobj, "shape", None) is None:
            dobj.shape = (dobj.size,)
        # We collect the *files* that are part of our selection, as well as our grids.
        # Once the indexer has been initialized, which will happen during the counting,
        # we can find the files to which everything belongs.
        dobj._chunk_info = [f for f in self.data_files if 
                 any(indexer.cell_count_by_grid.get(_, 0) > 0
                 for _ in f.grid_id_values)]
        dobj._current_chunk = list(self._chunk_all(dobj, cache = False,
                                   indexer = indexer))[0]

    def _count_selection(self, dobj, grids = None, indexer = None):
        if indexer is not None:
            return indexer.count(dobj.selector)
        raise NotImplementedError
        if grids is None: grids = dobj._chunk_info
        count = sum((g.count(dobj.selector) for g in grids))
        return count

    def _chunk_all(self, dobj, cache = True, indexer = None):
        fobjs = getattr(dobj._current_chunk, "objs", dobj._chunk_info)
        indexer = indexer or getattr(dobj._current_chunk, "_indexer",
            None)
        yield YTDataChunk(dobj, "all", fobjs, dobj.size, 
                        cache, indexer = indexer)
        
    def _chunk_spatial(self, dobj, ngz, sort = None, preload_fields = None):
        gobjs = getattr(dobj._current_chunk, "objs", dobj._chunk_info)
        if sort in ("+level", "level"):
            giter = sorted(gobjs, key=lambda g: g.Level)
        elif sort == "-level":
            giter = sorted(gobjs, key=lambda g: -g.Level)
        elif sort is None:
            giter = gobjs
        if preload_fields is None: preload_fields = []
        preload_fields, _ = self._split_fields(preload_fields)
        if self._preload_implemented and len(preload_fields) > 0 and ngz == 0:
            giter = ChunkDataCache(list(giter), preload_fields, self)
        for i, og in enumerate(giter):
            if ngz > 0:
                g = og.retrieve_ghost_zones(ngz, [], smoothed=True)
            else:
                g = og
            size = self._count_selection(dobj, [og])
            if size == 0: continue
            # We don't want to cache any of the masks or icoords or fcoords for
            # individual grids.
            yield YTDataChunk(dobj, "spatial", [g], size, cache = False)

    def _chunk_io(self, dobj, cache=True, local_only=False,
                  preload_fields=None, chunk_sizing="auto"):
        # local_only is only useful for inline datasets and requires
        # implementation by subclasses.
        if preload_fields is None:
            preload_fields = []
        preload_fields, _ = self._split_fields(preload_fields)
        fobjs = getattr(dobj._current_chunk, "objs", dobj._chunk_info)
        indexer = dobj._current_chunk._indexer
        gobjs = sorted((self.grids[_] for _ in indexer.cell_count_by_grid),
                       key = lambda a: a.id)
        # We can apply a heuristic here to make sure we aren't loading too
        # many grids all at once.
        # 
        # We need to re-enable this, but likely at a higher level when setting up the
        # initial collections
        GRID_CHUNKSIZE = self.dataset._grid_chunk_size or (2 << 32)
        if chunk_sizing == "auto":
            chunk_ngrids = len(gobjs)
            if chunk_ngrids > 0:
                nproc = np.float(ytcfg.getint("yt", "__global_parallel_size"))
                chunking_factor = np.ceil(GRID_CHUNKSIZE*nproc/chunk_ngrids).astype("int")
                size = max(GRID_CHUNKSIZE//chunking_factor, 1)
            else:
                size = self._grid_chunksize
        elif chunk_sizing == "config_file":
            size = ytcfg.getint("yt", "chunk_size")
        elif chunk_sizing == "just_one":
            size = 1
        elif chunk_sizing == "old":
            size = GRID_CHUNKSIZE
        else:
            raise RuntimeError("%s is an invalid value for the 'chunk_sizing' argument." % chunk_sizing)
        for f in fobjs:
            # Now we have the data file...
            gs = f.grid_id_values
            for g_indices in (gs[pos:pos + size] for pos
                          in range(0, len(gs), size)):
                this_loop = np.zeros(self.grids.size, "uint8")
                for g_id in g_indices:
                    # Note that we do not need to offset by _id_offset here
                    this_loop[g_id] = 1
                indexer2 = self.grid_tree.selector(this_loop)
                # Now, the order of the grids array is probably not the same as
                # the order in the indexer, so we need to ask the indexer
                # to sort it.
                chunk_size = self._count_selection(dobj, indexer = indexer2)
                grids = self.grids[np.asarray(indexer2.grid_order)].tolist()
                dc = YTDataChunk(dobj, "io", [f], chunk_size,
                        cache = cache, indexer = indexer2)
                # We allow four full chunks to be included.
                with self.io.preload(dc, preload_fields, 
                            4.0 * size):
                    yield dc


def _grid_sort_id(g):
    return g.id

def _grid_sort_mixed(g):
    if g.filename is None:
        return str(g.id)
    return g.filename
