"""
Definitions for cartesian coordinate systems




"""

#-----------------------------------------------------------------------------
# Copyright (c) 2013, yt Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

import numpy as np
from .coordinate_handler import \
    CoordinateHandler, \
    _get_coord_fields, \
    _get_vert_fields, \
    cartesian_to_cylindrical, \
    cylindrical_to_cartesian
from yt.funcs import mylog
from yt.utilities.lib.pixelization_routines import \
    pixelize_element_mesh, pixelize_off_axis_cartesian, \
    pixelize_cartesian, pixelize_sph_kernel_slice, \
    pixelize_sph_kernel_projection
from yt.data_objects.unstructured_mesh import SemiStructuredMesh

class CartesianCoordinateHandler(CoordinateHandler):
    name = "cartesian"

    def __init__(self, ds, ordering = ('x','y','z')):
        super(CartesianCoordinateHandler, self).__init__(ds, ordering)

    def setup_fields(self, registry):
        for axi, ax in enumerate(self.axis_order):
            f1, f2 = _get_coord_fields(axi)
            registry.add_field(("index", "d%s" % ax),
                               sampling_type="cell",
                               function = f1,
                               display_field = False,
                               units = "code_length")

            registry.add_field(("index", "path_element_%s" % ax),
                               sampling_type="cell",
                               function = f1,
                               display_field = False,
                               units = "code_length")

            registry.add_field(("index", "%s" % ax),
                               sampling_type="cell",
                               function = f2,
                               display_field = False,
                               units = "code_length")

            f3 = _get_vert_fields(axi)
            registry.add_field(("index", "vertex_%s" % ax),
                               sampling_type="cell",
                               function = f3,
                               display_field = False,
                               units = "code_length")
        def _cell_volume(field, data):
            rv  = data["index", "dx"].copy(order='K')
            rv *= data["index", "dy"]
            rv *= data["index", "dz"]
            return rv

        registry.add_field(("index", "cell_volume"),
                           sampling_type="cell",
                           function=_cell_volume,
                           display_field=False,
                           units = "code_length**3")

        registry.check_derived_fields(
            [("index", "dx"), ("index", "dy"), ("index", "dz"),
             ("index", "x"), ("index", "y"), ("index", "z"),
             ("index", "cell_volume")])

    def pixelize(self, dimension, data_source, field, bounds, size,
                 antialias = True, periodic = True):
        index = data_source.ds.index
        if (hasattr(index, 'meshes') and
           not isinstance(index.meshes[0], SemiStructuredMesh)):
            ftype, fname = field
            mesh_id = int(ftype[-1]) - 1
            mesh = index.meshes[mesh_id]
            coords = mesh.connectivity_coords
            indices = mesh.connectivity_indices
            offset = mesh._index_offset
            ad = data_source.ds.all_data()
            field_data = ad[field]
            buff_size = size[0:dimension] + (1,) + size[dimension:]

            ax = data_source.axis
            xax = self.x_axis[ax]
            yax = self.y_axis[ax]
            c = np.float64(data_source.center[dimension].d)

            extents = np.zeros((3, 2))
            extents[ax] = np.array([c, c])
            extents[xax] = bounds[0:2]
            extents[yax] = bounds[2:4]

            # if this is an element field, promote to 2D here
            if len(field_data.shape) == 1:
                field_data = np.expand_dims(field_data, 1)
            # if this is a higher-order element, we demote to 1st order
            # here, for now.
            elif field_data.shape[1] == 27:
                # hexahedral
                mylog.warning("High order elements not yet supported, " +
                              "dropping to 1st order.")
                field_data = field_data[:, 0:8]
                indices = indices[:, 0:8]
            elif field_data.shape[1] == 10:
                # tetrahedral
                mylog.warning("High order elements not yet supported, " +
                              "dropping to 1st order.")
                field_data = field_data[:,0:4]
                indices = indices[:, 0:4]

            img = pixelize_element_mesh(coords,
                                        indices,
                                        buff_size, field_data, extents,
                                        index_offset=offset)

            # re-order the array and squeeze out the dummy dim
            return np.squeeze(np.transpose(img, (yax, xax, ax)))

        elif self.axis_id.get(dimension, dimension) < 3:
            return self._ortho_pixelize(data_source, field, bounds, size,
                                        antialias, dimension, periodic)
        else:
            return self._oblique_pixelize(data_source, field, bounds, size,
                                          antialias)

    def _ortho_pixelize(self, data_source, field, bounds, size, antialias,
                        dim, periodic):
        from yt.frontends.sph.data_structures import ParticleDataset
        from yt.data_objects.selection_data_containers import \
            YTSlice
        from yt.data_objects.construction_data_containers import \
            YTQuadTreeProj
        # We should be using fcoords
        period = self.period[:2].copy() # dummy here
        period[0] = self.period[self.x_axis[dim]]
        period[1] = self.period[self.y_axis[dim]]
        if hasattr(period, 'in_units'):
            period = period.in_units("code_length").d
        buff = np.zeros((size[1], size[0]), dtype="f8")
        if isinstance(data_source.ds, ParticleDataset) and field[0] == 'gas':
            ptype = data_source.ds._sph_ptype
            ounits = data_source.ds.field_info[field].output_units
            px_name = 'particle_position_%s' % self.axis_name[self.x_axis[dim]]
            py_name = 'particle_position_%s' % self.axis_name[self.y_axis[dim]]
            if isinstance(data_source, YTQuadTreeProj):
                le = data_source.data_source.left_edge.in_units('code_length')
                re = data_source.data_source.right_edge.in_units('code_length')
                le[self.x_axis[dim]] = bounds[0]
                le[self.y_axis[dim]] = bounds[2]
                re[self.x_axis[dim]] = bounds[1]
                re[self.y_axis[dim]] = bounds[3]
                proj_reg = data_source.ds.region(
                    left_edge=le, right_edge=re, center=data_source.center,
                    data_source=data_source.data_source
                )
                buff = pixelize_sph_kernel_projection(
                    proj_reg[ptype, px_name].in_units('cm'),
                    proj_reg[ptype, py_name].in_units('cm'),
                    proj_reg[ptype, 'smoothing_length'].in_units('cm'),
                    proj_reg[ptype, 'particle_mass'].in_units('g'),
                    proj_reg[ptype, 'density'].in_units('g/cm**3'),
                    proj_reg[ptype, 'density'].in_units(ounits),
                    size[0], size[1],
                    data_source.ds.arr(bounds, 'code_length').in_units('cm').tolist()).transpose()
            elif isinstance(data_source, YTSlice):
                buff = pixelize_sph_kernel_slice(
                    data_source[ptype, px_name],
                    data_source[ptype, py_name],
                    data_source[ptype, 'smoothing_length'],
                    data_source[ptype, 'particle_mass'],
                    data_source[ptype, 'density'],
                    data_source[ptype, 'density'].in_units(ounits),
                    size[0], size[1], bounds).transpose()
            else:
                raise NotImplementedError(
                    "A pixelization routine has not been implemented for %s "
                    "data objects" % str(type(data_source)))
        else:
            pixelize_cartesian(buff, data_source['px'], data_source['py'],
                               data_source['pdx'], data_source['pdy'],
                               data_source[field], size[0], size[1],
                               bounds, int(antialias),
                               period, int(periodic)).transpose()
        return buff

    def _oblique_pixelize(self, data_source, field, bounds, size, antialias):
        indices = np.argsort(data_source['pdx'])[::-1]
        buff = np.zeros((size[1], size[0]), dtype="f8")
        pixelize_off_axis_cartesian(buff,
                              data_source['x'], data_source['y'],
                              data_source['z'], data_source['px'],
                              data_source['py'], data_source['pdx'],
                              data_source['pdy'], data_source['pdz'],
                              data_source.center, data_source._inv_mat, indices,
                              data_source[field], bounds)
        return buff

    def convert_from_cartesian(self, coord):
        return coord

    def convert_to_cartesian(self, coord):
        return coord

    def convert_to_cylindrical(self, coord):
        center = self.ds.domain_center
        return cartesian_to_cylindrical(coord, center)

    def convert_from_cylindrical(self, coord):
        center = self.ds.domain_center
        return cylindrical_to_cartesian(coord, center)

    def convert_to_spherical(self, coord):
        raise NotImplementedError

    def convert_from_spherical(self, coord):
        raise NotImplementedError

    _x_pairs = (('x', 'y'), ('y', 'z'), ('z', 'x'))
    _y_pairs = (('x', 'z'), ('y', 'x'), ('z', 'y'))

    @property
    def period(self):
        return self.ds.domain_width
