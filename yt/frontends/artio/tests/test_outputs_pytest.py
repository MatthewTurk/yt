"""
Title: test_artio.py
Purpose: Contains ARTIO frontend tests
Notes:
    Copyright (c) 2013, yt Development Team.
    Distributed under the terms of the Modified BSD License.
    The full license is in the file COPYING.txt, distributed with this
    software.
"""
import pytest

from yt.frontends.artio.api import ARTIODataset
from yt.testing import (
    assert_allclose_units,
    assert_equal,
    units_override_check,
)
from yt.utilities.answer_testing.answer_tests import (
    field_values,
    pixelized_projection_values,
)
from yt.utilities.answer_testing.utils import create_obj


# Data file
sizmbhloz = "sizmbhloz-clref04SNth-rs9_a0.9011/"
sizmbhloz += "sizmbhloz-clref04SNth-rs9_a0.9011.art"


@pytest.mark.answer_test
@pytest.mark.usefixtures("answer_file")
class TestArtIo:
    @pytest.mark.usefixtures("hashing")
    @pytest.mark.parametrize("ds", [sizmbhloz], indirect=True)
    def test_sizmbhloz(self, d, a, w, f, ds):
        ds.max_range = 1024 * 1024
        # Run tests
        fv = field_values(ds, f, d)
        self.hashes.update({"field_values": fv})
        ppv = pixelized_projection_values(ds, a, f, w, d)
        self.hashes.update({"pixelized_projection_values": ppv})
        dobj = create_obj(ds, d)
        s1 = dobj["ones"].sum()
        s2 = sum(mask.sum() for block, mask in dobj.blocks)
        assert_equal(s1, s2)
        assert_equal(ds.particle_type_counts, {"N-BODY": 100000, "STAR": 110650})

    @pytest.mark.parametrize("ds", [sizmbhloz], indirect=True)
    def test_ARTIODataset(self, ds):
        assert isinstance(ds, ARTIODataset)

    @pytest.mark.parametrize("ds", [sizmbhloz], indirect=True)
    def test_units_override(self, ds):
        units_override_check(ds, sizmbhloz)

    @pytest.mark.parametrize("ds", [sizmbhloz], indirect=True)
    def test_particle_derived_field(self, ds):
        def star_age_alias(field, data):
            # test to make sure we get back data in the correct units
            # during field detection
            return data["STAR", "age"].in_units("Myr")

        ds.add_field(
            ("STAR", "new_field"),
            function=star_age_alias,
            units="Myr",
            sampling_type="particle",
        )
        ad = ds.all_data()
        assert_allclose_units(
            ad["STAR", "age"].in_units("Myr"), ad["STAR", "new_field"]
        )